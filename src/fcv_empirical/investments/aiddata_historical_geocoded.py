from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from empirical_contracts import (
    AuthorityLevel,
    DataLayer,
    DatasetRef,
    GrainSpec,
    QAResult,
    SourceSnapshotRef,
)
from spatial_foundation import DataRoot, register_external_snapshot

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact
from fcv_empirical.investments.common import (
    InvestmentMaterializationResult,
    json_text,
    persist_contract_artifacts,
    validate_source_snapshot,
)


@dataclass(frozen=True)
class HistoricalAidDataRelease:
    release_id: str
    donor: str
    source_id: str
    origin: str
    archive_filename: str
    published_scope: str

    def __post_init__(self) -> None:
        for name in (
            "release_id",
            "donor",
            "source_id",
            "origin",
            "archive_filename",
            "published_scope",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty")


BRIGGS_WB_2011 = HistoricalAidDataRelease(
    release_id="all-world-bank-ibrd-ida-2011",
    donor="World Bank",
    source_id="aiddata_historical_worldbank_geocoded",
    origin=(
        "https://github.com/AidData-WM/public_datasets/raw/master/geocoded/"
        "AllWorldBank_IBRDIDA.csv.zip"
    ),
    archive_filename="AllWorldBank_IBRDIDA.csv.zip",
    published_scope=(
        "geocoded World Bank IBRD/IDA projects approved 1997-2011; 2011 historical release"
    ),
)

BRIGGS_AFDB_2009_2010 = HistoricalAidDataRelease(
    release_id="afdb-2009-2010-all-approved-projects",
    donor="African Development Bank",
    source_id="aiddata_historical_afdb_geocoded",
    origin=(
        "https://github.com/AidData-WM/public_datasets/raw/master/geocoded/"
        "AfDB_2009_2010_AllApprovedProjects.xlsx.zip"
    ),
    archive_filename="AfDB_2009_2010_AllApprovedProjects.xlsx.zip",
    published_scope="all geocoded African Development Bank activities approved in 2009-2010",
)

SUPPORTED_TABLE_SUFFIXES = (".csv", ".xlsx")


@dataclass(frozen=True)
class HistoricalAidDataExtraction:
    rows: pd.DataFrame
    source_member: str
    source_columns: tuple[str, ...]
    qa: tuple[QAResult, ...]
    audit: dict[str, Any]


def _source_members(archive: zipfile.ZipFile) -> list[str]:
    return sorted(
        member
        for member in archive.namelist()
        if not member.endswith("/") and Path(member).suffix.lower() in SUPPORTED_TABLE_SUFFIXES
    )


def _read_member(archive: zipfile.ZipFile, member: str) -> pd.DataFrame:
    suffix = Path(member).suffix.lower()
    with archive.open(member) as handle:
        payload = handle.read()
    if suffix == ".csv":
        return pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False, low_memory=False)
    if suffix == ".xlsx":
        return pd.read_excel(
            io.BytesIO(payload), dtype=str, keep_default_na=False, engine="openpyxl"
        )
    raise ValueError(f"unsupported historical AidData member type: {member}")


def _candidate_columns(columns: tuple[str, ...]) -> dict[str, list[str]]:
    """Surface possible semantic fields for local review without choosing among them."""
    patterns = {
        "project_identity": ("project", "projectid", "project_id", "id"),
        "location_identity": ("location", "locationid", "location_id", "place"),
        "approval_time": ("approval", "approved", "year"),
        "precision": ("precision", "geocode", "accuracy"),
        "country": ("country", "recipient"),
        "region": ("adm1", "admin1", "region", "province", "state"),
        "amount": ("amount", "cost", "commitment", "usd", "value"),
        "coordinates": ("latitude", "longitude", "lat", "lon", "lng"),
    }
    lower = {column: column.casefold().replace(" ", "_") for column in columns}
    return {
        semantic: [
            column
            for column, normalized in lower.items()
            if any(token in normalized for token in tokens)
        ]
        for semantic, tokens in patterns.items()
    }


def read_historical_aiddata_archive(
    archive_path: str | Path,
    *,
    release: HistoricalAidDataRelease,
) -> HistoricalAidDataExtraction:
    """Read one historical AidData ZIP without assigning modern semantics to its fields.

    The archive itself remains source authority. Exactly one CSV/XLSX table must be present;
    README/documentation members are ignored. Every source column is retained. Candidate
    semantic columns are reported only as audit hints and are never selected automatically.
    """

    path = Path(archive_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.name != release.archive_filename:
        raise ValueError(
            f"archive filename must be {release.archive_filename!r} for release "
            f"{release.release_id!r}; got {path.name!r}"
        )
    if not zipfile.is_zipfile(path):
        raise ValueError(f"historical AidData source must be a ZIP archive: {path}")

    with zipfile.ZipFile(path) as archive:
        members = _source_members(archive)
        if len(members) != 1:
            raise ValueError(
                "historical AidData archive must contain exactly one CSV/XLSX source table; "
                f"found {members}"
            )
        member = members[0]
        source = _read_member(archive, member)

    if source.empty:
        raise ValueError("historical AidData source table must be non-empty")
    source_columns = tuple(str(column) for column in source.columns)
    if len(set(source_columns)) != len(source_columns):
        raise ValueError("historical AidData source table has duplicate column names after decoding")
    if any(not column.strip() for column in source_columns):
        raise ValueError("historical AidData source table has blank column names")

    rows = source.copy()
    rows.insert(0, "fcv_source_row_number", range(1, len(rows) + 1))
    rows.insert(1, "fcv_source_archive_member", member)
    rows.insert(2, "fcv_source_release_id", release.release_id)
    rows.insert(3, "fcv_source_donor", release.donor)

    candidates = _candidate_columns(source_columns)
    audit = {
        "release_id": release.release_id,
        "donor": release.donor,
        "published_scope": release.published_scope,
        "archive_filename": path.name,
        "source_member": member,
        "row_count": len(source),
        "column_count": len(source_columns),
        "source_columns": list(source_columns),
        "candidate_columns_for_review_only": candidates,
        "semantic_mapping_selected": False,
    }
    qa = (
        QAResult(
            check_id="aiddata.historical.archive.table",
            state="GREEN",
            message="historical AidData archive exposes exactly one supported source table",
            metrics={"source_member": member, "row_count": len(source)},
        ),
        QAResult(
            check_id="aiddata.historical.source_columns",
            state="GREEN",
            message="source columns are retained without automatic semantic harmonization",
            metrics={"source_column_count": len(source_columns)},
        ),
        QAResult(
            check_id="aiddata.historical.row_identity",
            state="GREEN",
            message=(
                "physical source-row numbering is unique; substantive project/location identity "
                "remains unresolved until an explicit release field map is commissioned"
            ),
            metrics={
                "source_rows": len(rows),
                "unique_source_row_numbers": rows["fcv_source_row_number"].nunique(),
            },
        ),
    )
    return HistoricalAidDataExtraction(
        rows=rows,
        source_member=member,
        source_columns=source_columns,
        qa=qa,
        audit=audit,
    )


def register_historical_aiddata_snapshot(
    archive_path: str | Path,
    *,
    release: HistoricalAidDataRelease,
) -> SourceSnapshotRef:
    path = Path(archive_path)
    if path.name != release.archive_filename:
        raise ValueError(f"unexpected archive filename for {release.release_id}: {path.name}")
    snapshot = register_external_snapshot(release.source_id, release.release_id, [path])
    return snapshot.model_copy(update={"origin": release.origin})


def _dataset_ref(version: str) -> DatasetRef:
    return DatasetRef(
        dataset_id="investments.aiddata_historical_geocoded.rows",
        version=version,
        schema_version="source-native-historical-geocoded-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("fcv_source_row_number",)),
    )


def materialize_historical_aiddata_silver(
    *,
    archive_path: str | Path,
    release: HistoricalAidDataRelease,
    data_root: DataRoot,
    run_id: str,
    source_snapshot: SourceSnapshotRef | None = None,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> InvestmentMaterializationResult:
    """Materialize a historical geocoded AidData archive at source-row grain.

    This is intentionally upstream of the Briggs scientific measurement. It does not
    choose approval-year, precision, project/subproject identity, region mapping, or
    amount-allocation fields. Those choices require an explicit release-specific field
    map after local source inspection.
    """

    path = Path(archive_path)
    snapshot = source_snapshot or register_historical_aiddata_snapshot(path, release=release)
    validate_source_snapshot(
        snapshot,
        expected_source=release.source_id,
        expected_release=release.release_id,
        exact_paths=[path],
    )
    extraction = read_historical_aiddata_archive(path, release=release)
    version = snapshot.snapshot_id
    dataset = _dataset_ref(version)
    silver_base = data_root.silver("investments", "aiddata_historical_geocoded", version)

    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        source_snapshot=snapshot,
        outputs=(
            FileMaterialization(
                dataset=dataset,
                relative_path="rows.parquet",
                destination_base=silver_base,
                writer=lambda output: extraction.rows.to_parquet(
                    output, index=False, engine="pyarrow"
                ),
            ),
        ),
        parameters={
            "source": release.source_id,
            "release_id": release.release_id,
            "donor": release.donor,
            "transformation": "source-native-historical-geocoded-v1",
            "semantic_mapping_selected": False,
            "briggs_filter_applied": False,
            "amount_allocation_applied": False,
        },
        code_commit=code_commit,
        qa=extraction.qa,
        overwrite=overwrite,
    )
    hashed_ref = manifest.outputs[0]
    parity = {
        "status": "NOT_RUN",
        "reason": (
            "no independent source-native legacy table was supplied; Briggs final analysis data "
            "is an oracle, not source parity input"
        ),
    }
    persist_contract_artifacts(
        data_root=data_root,
        run_id=run_id,
        manifest=manifest,
        qa=manifest.qa,
        parity=parity,
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "source/historical_aiddata_schema_audit.json",
        json_text(extraction.audit),
        overwrite=overwrite,
    )
    return InvestmentMaterializationResult(
        manifest=manifest,
        datasets={"rows": hashed_ref},
        paths={"rows": silver_base / "rows.parquet"},
        qa=manifest.qa,
        parity=parity,
    )


def write_historical_aiddata_schema_audit(
    archive_path: str | Path,
    *,
    release: HistoricalAidDataRelease,
    output_path: str | Path,
) -> Path:
    """Write a sanitized header/shape audit suitable for a GitHub handoff."""
    extraction = read_historical_aiddata_archive(archive_path, release=release)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(extraction.audit, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return path
