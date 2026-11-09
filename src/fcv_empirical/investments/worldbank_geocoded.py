"""AidData World Bank Geocoded Research Release: immutable, source-native Silver.

This is NOT the World Bank Projects API vertical in worldbank.py.
The legacy level_1a.csv is a convenience join, not a distinct source fact set.
No geography projection, period assignment, local financing, or treatment is made here.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from empirical_contracts import (
    AuthorityLevel,
    DataLayer,
    DatasetRef,
    GrainSpec,
    QAResult,
    RunManifest,
    SourceSnapshotRef,
)
from spatial_foundation import DataRoot, register_external_snapshot, sha256_file

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact

WB_GEOCODED_SOURCE = "aiddata_worldbank_geocoded"
WB_GEOCODED_RELEASE = "v1.4.2"
WB_GEOCODED_ORIGIN = "https://github.com/AidData-WM/public_datasets/tree/master/geocoded"
SOURCE_PREFIX = "WorldBank_GeocodedResearchRelease_Level1_v1.4.2/data/"
SOURCE_FILES = {
    "projects": ("projects.csv", "project_id"),
    "locations": ("locations.csv", "project_location_id"),
    "transactions": ("transactions.csv", "transaction_id"),
    "ancillary": ("projects_ancillary.csv", "PROJECT ID"),
}


@dataclass(frozen=True)
class WBGeocodedExtraction:
    tables: dict[str, pd.DataFrame]
    qa: tuple[QAResult, ...]
    source_member_sha256: dict[str, str]
    source_counts: dict[str, int]


@dataclass(frozen=True)
class WBGeocodedSilverResult:
    snapshot: SourceSnapshotRef
    manifest: RunManifest
    datasets: dict[str, DatasetRef]
    paths: dict[str, Path]
    extraction: WBGeocodedExtraction


def register_wb_geocoded_snapshot(
    source_zip: str | Path,
    *,
    release: str = WB_GEOCODED_RELEASE,
) -> SourceSnapshotRef:
    if release != WB_GEOCODED_RELEASE:
        raise ValueError("this reader supports exactly the pinned v1.4.2 source layout")
    snapshot = register_external_snapshot(WB_GEOCODED_SOURCE, release, [source_zip])
    return snapshot.model_copy(update={"origin": WB_GEOCODED_ORIGIN})


def _read_source_table(archive: zipfile.ZipFile, member: str, id_column: str) -> pd.DataFrame:
    with archive.open(SOURCE_PREFIX + member) as handle:
        frame = pd.read_csv(
            handle,
            low_memory=False,
            dtype={id_column: "string", "project_id": "string", "PROJECT ID": "string"},
        )
    frame.insert(0, "fcv_source_row_number", range(1, len(frame) + 1))
    return frame


def extract_wb_geocoded_release(source_zip: str | Path) -> WBGeocodedExtraction:
    """Read only fixed, known source members; never expand untrusted ZIP paths."""
    path = Path(source_zip).expanduser().resolve()
    tables: dict[str, pd.DataFrame] = {}
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(path) as archive:
        for name, (member, id_col) in SOURCE_FILES.items():
            raw = archive.read(SOURCE_PREFIX + member)
            hashes[name] = hashlib.sha256(raw).hexdigest()
            tables[name] = _read_source_table(archive, member, id_col)

    projects, locations, transactions, ancillary = (
        tables["projects"],
        tables["locations"],
        tables["transactions"],
        tables["ancillary"],
    )

    for name, (_, key) in SOURCE_FILES.items():
        ids = tables[name][key].astype("string")
        if ids.isna().any() or ids.str.strip().eq("").any():
            raise ValueError(f"{name}: missing source identifier {key}")
        if name != "ancillary" and ids.duplicated().any():
            raise ValueError(f"{name}: duplicated source identifier {key}")
    project_ids = set(projects["project_id"])
    locids = set(locations["project_id"])
    txnids = set(transactions["project_id"])
    ancillaryids = set(ancillary["PROJECT ID"])
    for name, keys in (
        ("locations", locids),
        ("transactions", txnids),
        ("ancillary", ancillaryids),
    ):
        unknown = keys - project_ids
        if unknown:
            raise ValueError(f"{name}: {len(unknown)} orphan foreign project IDs")
    flag = pd.to_numeric(projects["is_geocoded"], errors="coerce")
    if flag.isna().any() or not flag.isin((0, 1)).all():
        raise ValueError("projects.is_geocoded must be a valid 0/1 source flag")
    flags = set(projects.loc[flag.eq(1), "project_id"])
    if flags != locids:
        raise ValueError(
            "AidData is_geocoded source flag disagrees with native location membership "
            f"(flag_only={len(flags - locids)}, location_only={len(locids - flags)})"
        )

    start = pd.to_datetime(projects["start_actual_isodate"], errors="coerce")
    if start.isna().any():
        raise ValueError("projects contain missing or invalid source start dates")

    # Ancillary is many-to-one, not another unique project table. Its approval
    # date can independently validate the source project-start interpretation.
    dates = pd.to_datetime(ancillary["APPROVAL DATE"], format="%d-%b-%Y", errors="coerce")
    approval = ancillary[["PROJECT ID"]].assign(_approval_date=dates).dropna()
    grouped = approval.groupby("PROJECT ID")["_approval_date"].agg(["nunique", "first"])
    if grouped["nunique"].gt(1).any():
        raise ValueError("ancillary has conflicting approval dates for the same project")
    original_start = pd.Series(start.array, index=projects["project_id"])
    common = original_start.index.intersection(grouped.index)
    mismatched = (original_start.loc[common] != grouped.loc[common, "first"]).sum()
    if mismatched:
        raise ValueError(f"World Bank approval date parity failed for {mismatched} projects")

    source_counts = {
        "projects": len(projects),
        "geocoded_projects": len(locids),
        "not_geocoded_projects": len(project_ids - locids),
        "locations": len(locations),
        "transactions": len(transactions),
        "ancillary_rows": len(ancillary),
        "approval_date_parity_projects": len(common),
    }
    missing_disbursement = int(projects["total_disbursements"].isna().sum())
    qa = (
        QAResult(
            check_id="aiddata_wb_geocoded.native_identity",
            state="GREEN",
            message=(
                "source-native project, location, transaction IDs are unique; "
                "child IDs have valid parent"
            ),
            metrics=source_counts,
        ),
        QAResult(
            check_id="aiddata_wb_geocoded.approval_date_parity",
            state="GREEN",
            message=(
                "source start date agrees with independently recorded ancillary approval date"
            ),
            metrics={"matched_projects": len(common), "mismatches": 0},
        ),
        QAResult(
            check_id="aiddata_wb_geocoded.geocoding_missingness",
            state="YELLOW" if source_counts["not_geocoded_projects"] else "GREEN",
            message="projects without a source location are retained, not treated as zero investment",
            metrics={
                "projects_without_locations": source_counts["not_geocoded_projects"],
                "fraction": source_counts["not_geocoded_projects"] / len(projects),
            },
        ),
        QAResult(
            check_id="aiddata_wb_geocoded.disbursement_missingness",
            state="YELLOW" if missing_disbursement else "GREEN",
            message="missing native disbursements retained as NA, never imputed as zero",
            metrics={"projects_missing_disbursement": missing_disbursement},
        ),
    )
    return WBGeocodedExtraction(
        tables=tables, qa=qa, source_member_sha256=hashes, source_counts=source_counts
    )


def _ref(snapshot: SourceSnapshotRef, table: str, key: str) -> DatasetRef:
    return DatasetRef(
        dataset_id=f"investments.aiddata_worldbank_geocoded.{table}",
        version=snapshot.snapshot_id,
        schema_version="aiddata-wb-geocoded-native-silver-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=(key,)),
    )


def materialize_wb_geocoded_silver(
    *,
    source_zip: str | Path,
    data_root: DataRoot,
    run_id: str,
    source_snapshot: SourceSnapshotRef | None = None,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> WBGeocodedSilverResult:
    """Atomically materialize four source-native tables and one provenance manifest."""
    source_zip = Path(source_zip).expanduser().resolve()
    snapshot = source_snapshot or register_wb_geocoded_snapshot(source_zip)
    if snapshot.source != WB_GEOCODED_SOURCE or snapshot.release != WB_GEOCODED_RELEASE:
        raise ValueError("invalid World Bank geocoded source snapshot identity")
    if len(snapshot.files) != 1 or Path(snapshot.files[0].path).resolve() != source_zip:
        raise ValueError("snapshot must pin exactly the requested source ZIP")
    if sha256_file(source_zip) != snapshot.files[0].sha256:
        raise ValueError("source ZIP has changed since registration")
    extraction = extract_wb_geocoded_release(source_zip)
    destination = data_root.silver(
        "investments", "aiddata_worldbank_geocoded", snapshot.snapshot_id
    )
    datasets = {
        name: _ref(snapshot, name, "fcv_source_row_number" if name == "ancillary" else key)
        for name, (_, key) in SOURCE_FILES.items()
    }
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        source_snapshot=snapshot,
        outputs=tuple(
            FileMaterialization(
                dataset=datasets[name],
                relative_path=f"{name}.parquet",
                destination_base=destination,
                writer=lambda path, df=extraction.tables[name]: df.to_parquet(path, index=False),
            )
            for name in SOURCE_FILES
        ),
        parameters={
            "source": WB_GEOCODED_SOURCE,
            "source_layout": "v1.4.2 official CSV tables inside immutable ZIP",
            "source_member_sha256": extraction.source_member_sha256,
            "temporal_reinterpretation": None,
            "geography_projection": None,
            "finance_allocation": None,
            "missing_to_zero": False,
        },
        qa=extraction.qa,
        code_commit=code_commit,
        overwrite=overwrite,
    )
    hashed = {item.dataset_id.rsplit(".", 1)[-1]: item for item in manifest.outputs}
    persist_run_artifact(
        data_root,
        run_id,
        "source/wb_geocoded_source_profile.json",
        json.dumps(
            {
                "release": snapshot.release,
                "snapshot_id": snapshot.snapshot_id,
                "source_counts": extraction.source_counts,
                "member_sha256": extraction.source_member_sha256,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        overwrite=overwrite,
    )
    return WBGeocodedSilverResult(
        snapshot=snapshot,
        manifest=manifest,
        datasets=hashed,
        paths={name: destination / f"{name}.parquet" for name in SOURCE_FILES},
        extraction=extraction,
    )
