from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

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

from .catalog import SurveyCatalogEntry, SurveyFileLink, validate_survey_file_link
from .design import SurveyDesignRecord

DHS_SOURCE = "dhs"
DHS_ORIGIN = "https://dhsprogram.com/data/"
DHS_HR_RECODE = "HR"
HOUSEHOLD_GRAIN = "household"
_SURVEY_TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class DhsHrMetadata:
    """Verified DHS metadata needed to identify one HR source file without filename inference."""

    dhs_survey_id: str
    country_iso3: str
    country: str
    survey_year: int
    survey_phase: str
    release: str
    source_file_name: str
    recode_family: str = DHS_HR_RECODE

    def __post_init__(self) -> None:
        for name in (
            "dhs_survey_id",
            "country_iso3",
            "country",
            "survey_phase",
            "release",
            "source_file_name",
            "recode_family",
        ):
            value = getattr(self, name)
            if not value or not value.strip():
                raise ValueError(f"{name} must be non-empty")
            if value != value.strip():
                raise ValueError(f"{name} must not contain surrounding whitespace")
        if not _SURVEY_TOKEN.fullmatch(self.dhs_survey_id):
            raise ValueError("dhs_survey_id must be a stable source metadata token")
        if (
            len(self.country_iso3) != 3
            or not self.country_iso3.isalpha()
            or self.country_iso3 != self.country_iso3.upper()
        ):
            raise ValueError("country_iso3 must be a three-letter uppercase ISO code")
        if self.survey_year < 1000 or self.survey_year > 9999:
            raise ValueError("survey_year must be a four-digit year")
        if self.recode_family.upper() != DHS_HR_RECODE:
            raise ValueError("DHS HR materialization requires recode_family='HR'")
        if Path(self.source_file_name).name != self.source_file_name:
            raise ValueError("source_file_name must be a file name, not a path")


@dataclass(frozen=True)
class DhsHrColumnMap:
    """Release-verified source-variable mapping for the HR normalized envelope.

    The map is intentionally explicit at materialization time. DHS standard recodes commonly use
    HHID/HV001/HV005/HV021/HV022, but the caller must verify the release-specific documentation
    rather than relying on an experiment-side design convention.
    """

    household_id: str
    cluster_id: str
    source_weight: str
    psu_id: str | None = None
    stratum_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("household_id", "cluster_id", "source_weight"):
            value = getattr(self, name)
            if not value or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        for name in ("psu_id", "stratum_id"):
            value = getattr(self, name)
            if value is not None and (not value or not value.strip()):
                raise ValueError(f"{name} must be non-empty when supplied")


STANDARD_DHS_HR_COLUMNS = DhsHrColumnMap(
    household_id="hhid",
    cluster_id="hv001",
    source_weight="hv005",
    psu_id="hv021",
    stratum_id="hv022",
)


@dataclass(frozen=True)
class DhsHrSilverResult:
    frame: pd.DataFrame
    qa: tuple[QAResult, ...]
    catalog: SurveyCatalogEntry
    file_link: SurveyFileLink
    source_columns: dict[str, str | None]
    schema_sha256: str


def build_dhs_survey_id(metadata: DhsHrMetadata) -> str:
    """Build internal survey identity from a verified DHS survey identifier, never a filename."""
    return f"dhs-{metadata.dhs_survey_id}"


def build_dhs_survey_catalog(metadata: DhsHrMetadata) -> SurveyCatalogEntry:
    return SurveyCatalogEntry(
        survey_id=build_dhs_survey_id(metadata),
        source_family=DHS_SOURCE,
        country_iso3=metadata.country_iso3,
        survey_year=metadata.survey_year,
        release=metadata.release,
        survey_phase=metadata.survey_phase,
    )


def register_dhs_hr_snapshot(
    source_path: str | Path,
    *,
    release: str,
    origin: str = DHS_ORIGIN,
) -> SourceSnapshotRef:
    """Register an externally stored DHS HR file without copying protected microdata."""
    snapshot = register_external_snapshot(DHS_SOURCE, release, [source_path])
    return snapshot.model_copy(update={"origin": origin})


def _validate_snapshot_source(
    snapshot: SourceSnapshotRef,
    source_path: str | Path,
    *,
    release: str,
) -> None:
    if snapshot.source != DHS_SOURCE:
        raise ValueError(
            f"snapshot source {snapshot.source!r} does not match expected source {DHS_SOURCE!r}"
        )
    if snapshot.release != release:
        raise ValueError("supplied DHS snapshot release does not match verified DHS metadata")
    resolved = Path(source_path).expanduser().resolve()
    matches = [ref for ref in snapshot.files if Path(ref.path).expanduser().resolve() == resolved]
    if len(matches) != 1:
        raise ValueError("DHS source_path must be represented exactly once in SourceSnapshotRef")
    current = sha256_file(resolved)
    if current != matches[0].sha256:
        raise ValueError("DHS source file changed after snapshot registration")


def _source_file_ref(snapshot: SourceSnapshotRef, source_path: str | Path):
    resolved = Path(source_path).expanduser().resolve()
    matches = [ref for ref in snapshot.files if Path(ref.path).expanduser().resolve() == resolved]
    if len(matches) != 1:
        raise ValueError("DHS source_path must be represented exactly once in SourceSnapshotRef")
    return matches[0]


def build_dhs_hr_file_link(
    *,
    catalog: SurveyCatalogEntry,
    snapshot: SourceSnapshotRef,
    source_path: str | Path,
) -> SurveyFileLink:
    link = SurveyFileLink(
        survey_id=catalog.survey_id,
        source_snapshot_id=snapshot.snapshot_id,
        source_file=_source_file_ref(snapshot, source_path),
        instrument=DHS_HR_RECODE,
    )
    validate_survey_file_link(catalog, link, snapshot)
    return link


def _resolve_column(columns: list[str], requested: str | None) -> str | None:
    if requested is None:
        return None
    exact = [column for column in columns if column == requested]
    if len(exact) == 1:
        return exact[0]
    folded = [column for column in columns if column.casefold() == requested.casefold()]
    if len(folded) == 1:
        return folded[0]
    if len(folded) > 1:
        raise ValueError(f"DHS source has ambiguous case variants for variable {requested!r}")
    return None


def _resolve_source_columns(raw: pd.DataFrame, column_map: DhsHrColumnMap) -> dict[str, str | None]:
    columns = [str(column) for column in raw.columns]
    if len(set(columns)) != len(columns):
        raise ValueError("DHS HR input has duplicate source column labels")
    resolved = {
        "household_id": _resolve_column(columns, column_map.household_id),
        "cluster_id": _resolve_column(columns, column_map.cluster_id),
        "source_weight": _resolve_column(columns, column_map.source_weight),
        "psu_id": _resolve_column(columns, column_map.psu_id),
        "stratum_id": _resolve_column(columns, column_map.stratum_id),
    }
    missing_required = [
        field
        for field in ("household_id", "cluster_id", "source_weight")
        if resolved[field] is None
    ]
    if missing_required:
        raise ValueError(
            "DHS HR input is missing release-verified source fields: " + ", ".join(missing_required)
        )
    for field in ("psu_id", "stratum_id"):
        requested = getattr(column_map, field)
        if requested is not None and resolved[field] is None:
            raise ValueError(
                f"DHS HR input is missing release-verified {field} source field {requested!r}"
            )
    return resolved


def _id_series(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    return text.mask(series.isna() | text.eq(""), pd.NA)


def _raw_present(series: pd.Series) -> pd.Series:
    return series.notna() & series.astype("string").str.strip().fillna("").ne("")


def normalize_dhs_hr(
    raw: pd.DataFrame,
    *,
    metadata: DhsHrMetadata,
    snapshot: SourceSnapshotRef,
    source_path: str | Path,
    column_map: DhsHrColumnMap,
) -> DhsHrSilverResult:
    """Build row-preserving household Silver while retaining every source-native column."""
    if snapshot.source != DHS_SOURCE:
        raise ValueError("DHS HR normalization requires a DHS SourceSnapshotRef")
    if snapshot.release != metadata.release:
        raise ValueError("DHS snapshot release does not match verified metadata release")
    if Path(source_path).name != metadata.source_file_name:
        raise ValueError("source_path filename does not match verified DHS source_file_name")

    catalog = build_dhs_survey_catalog(metadata)
    file_link = build_dhs_hr_file_link(catalog=catalog, snapshot=snapshot, source_path=source_path)
    source_columns = _resolve_source_columns(raw, column_map)

    envelope_columns = {
        "survey_id",
        "source_family",
        "source_release",
        "source_snapshot_id",
        "source_file_name",
        "source_recode",
        "source_row_id",
        "household_observation_id",
        "household_id",
        "cluster_id",
        "psu_id",
        "stratum_id",
        "source_weight_variable",
        "source_household_weight",
        "country",
        "country_iso3",
    }
    collisions = sorted(envelope_columns.intersection(str(column) for column in raw.columns))
    if collisions:
        raise ValueError(
            "DHS source columns collide with normalized envelope names: " + ", ".join(collisions)
        )

    source = raw.reset_index(drop=True).copy()
    envelope = pd.DataFrame(index=source.index)
    envelope["survey_id"] = catalog.survey_id
    envelope["source_family"] = DHS_SOURCE
    envelope["source_release"] = snapshot.release
    envelope["source_snapshot_id"] = snapshot.snapshot_id
    envelope["source_file_name"] = metadata.source_file_name
    envelope["source_recode"] = metadata.recode_family.upper()
    envelope["source_row_id"] = [
        f"{snapshot.snapshot_id}:{position:09d}" for position in range(len(source))
    ]

    household_ids = _id_series(source[source_columns["household_id"]])
    cluster_ids = _id_series(source[source_columns["cluster_id"]])
    envelope["household_id"] = household_ids
    envelope["cluster_id"] = cluster_ids

    psu_column = source_columns["psu_id"]
    stratum_column = source_columns["stratum_id"]
    envelope["psu_id"] = (
        _id_series(source[psu_column])
        if psu_column is not None
        else pd.Series(pd.NA, index=source.index, dtype="string")
    )
    envelope["stratum_id"] = (
        _id_series(source[stratum_column])
        if stratum_column is not None
        else pd.Series(pd.NA, index=source.index, dtype="string")
    )

    envelope["source_weight_variable"] = source_columns["source_weight"]
    envelope["source_household_weight"] = source[source_columns["source_weight"]].copy()
    envelope["country"] = metadata.country
    envelope["country_iso3"] = metadata.country_iso3
    envelope["household_observation_id"] = [
        f"{catalog.survey_id}:{household_id}" if pd.notna(household_id) else source_row_id
        for household_id, source_row_id in zip(
            envelope["household_id"], envelope["source_row_id"], strict=True
        )
    ]

    frame = pd.concat([envelope, source], axis=1)

    missing_household_ids = int(frame["household_id"].isna().sum())
    present_household_ids = frame.loc[frame["household_id"].notna(), "household_id"]
    duplicate_household_id_rows = int(present_household_ids.duplicated(keep=False).sum())
    missing_cluster_ids = int(frame["cluster_id"].isna().sum())
    cluster_count = int(frame["cluster_id"].dropna().nunique())
    missing_psu_ids = int(frame["psu_id"].isna().sum())
    missing_strata = int(frame["stratum_id"].isna().sum())

    raw_weight = frame["source_household_weight"]
    weight_present = _raw_present(raw_weight)
    numeric_weight = pd.to_numeric(raw_weight, errors="coerce")
    missing_weights = int((~weight_present).sum())
    invalid_weights = int((weight_present & numeric_weight.isna()).sum())
    nonpositive_weights = int((numeric_weight.notna() & numeric_weight.le(0)).sum())

    source_preserved = frame[list(source.columns)].equals(source)
    schema_entries = [
        (str(column), str(dtype))
        for column, dtype in zip(source.columns, source.dtypes, strict=True)
    ]
    schema_payload = json.dumps(schema_entries, ensure_ascii=False)
    schema_sha256 = hashlib.sha256(schema_payload.encode("utf-8")).hexdigest()

    qa = (
        QAResult(
            check_id="dhs.hr.row_retention",
            state="GREEN" if len(frame) == len(source) else "RED",
            message="HR Silver preserves one output row per supplied source household row",
            metrics={"input_rows": len(source), "output_rows": len(frame)},
        ),
        QAResult(
            check_id="dhs.hr.household_identity",
            state=(
                "GREEN"
                if missing_household_ids == 0 and duplicate_household_id_rows == 0
                else "RED"
            ),
            message="household source identifiers are preserved and duplicate keys remain visible",
            metrics={
                "missing_household_ids": missing_household_ids,
                "duplicate_household_id_rows": duplicate_household_id_rows,
            },
        ),
        QAResult(
            check_id="dhs.hr.cluster_identity",
            state="GREEN" if missing_cluster_ids == 0 else "YELLOW",
            message="source cluster identity is profiled without dropping households",
            metrics={
                "missing_cluster_ids": missing_cluster_ids,
                "cluster_count": cluster_count,
                "missing_psu_ids": missing_psu_ids,
            },
        ),
        QAResult(
            check_id="dhs.hr.source_weight",
            state=(
                "GREEN"
                if missing_weights == 0 and invalid_weights == 0 and nonpositive_weights == 0
                else "YELLOW"
            ),
            message=(
                "source household weight is preserved unchanged; "
                "QA parses only for diagnostics"
            ),
            metrics={
                "missing_weights": missing_weights,
                "invalid_weights": invalid_weights,
                "nonpositive_weights": nonpositive_weights,
                "source_weight_variable": source_columns["source_weight"],
            },
        ),
        QAResult(
            check_id="dhs.hr.stratum",
            state="GREEN" if missing_strata == 0 else "YELLOW",
            message="source stratum identifier is preserved without choosing an estimation design",
            metrics={"missing_stratum_ids": missing_strata},
        ),
        QAResult(
            check_id="dhs.hr.source_variable_preservation",
            state="GREEN" if source_preserved else "RED",
            message="every source-native column and value survives alongside normalized aliases",
            metrics={
                "source_column_count": len(source.columns),
                "preserved_source_column_count": sum(
                    1 for column in source.columns if column in frame.columns
                ),
                "schema_sha256": schema_sha256,
            },
        ),
    )

    return DhsHrSilverResult(
        frame=frame,
        qa=qa,
        catalog=catalog,
        file_link=file_link,
        source_columns=source_columns,
        schema_sha256=schema_sha256,
    )


def iter_dhs_hr_design_records(frame: pd.DataFrame) -> Iterator[SurveyDesignRecord]:
    """Expose S0 design records without normalizing weights or selecting an estimation design."""
    required = {
        "survey_id",
        "household_observation_id",
        "cluster_id",
        "psu_id",
        "stratum_id",
        "source_weight_variable",
        "source_household_weight",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("DHS HR Silver is missing design envelope columns: " + ", ".join(missing))

    for row in frame[list(required)].to_dict(orient="records"):
        weight = row["source_household_weight"]
        if pd.isna(weight):
            weight = None
        elif hasattr(weight, "item"):
            weight = weight.item()
        yield SurveyDesignRecord(
            survey_id=str(row["survey_id"]),
            observation_id=str(row["household_observation_id"]),
            natural_grain=HOUSEHOLD_GRAIN,
            cluster_id=None if pd.isna(row["cluster_id"]) else str(row["cluster_id"]),
            psu_id=None if pd.isna(row["psu_id"]) else str(row["psu_id"]),
            stratum_id=None if pd.isna(row["stratum_id"]) else str(row["stratum_id"]),
            source_weight_variable=str(row["source_weight_variable"]),
            source_weight_value=weight,
        )


def _read_hr_source(source_path: str | Path) -> pd.DataFrame:
    path = Path(source_path)
    suffix = path.suffix.casefold()
    if suffix == ".dta":
        return pd.read_stata(path, convert_categoricals=False)
    if suffix == ".csv":
        return pd.read_csv(path, low_memory=False)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    raise ValueError("unsupported DHS HR source format; expected .dta, .csv, or .parquet")


def _dataset_ref(snapshot: SourceSnapshotRef) -> DatasetRef:
    return DatasetRef(
        dataset_id="surveys.dhs.hr_households",
        version=snapshot.snapshot_id,
        schema_version="dhs-hr-household-silver-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("survey_id", "household_id")),
    )


def _hashed_output(manifest: RunManifest, dataset_id: str) -> DatasetRef:
    matches = [dataset for dataset in manifest.outputs if dataset.dataset_id == dataset_id]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one materialized dataset {dataset_id!r}")
    return matches[0]


def _json_text(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def materialize_dhs_hr_silver(
    *,
    source_path: str | Path,
    metadata: DhsHrMetadata,
    column_map: DhsHrColumnMap,
    data_root: DataRoot,
    run_id: str,
    source_snapshot: SourceSnapshotRef | None = None,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> tuple[SourceSnapshotRef, DhsHrSilverResult, RunManifest, DatasetRef, Path]:
    """Register/validate one external HR file and publish household-grain source-native Silver."""
    path = Path(source_path)
    if path.name != metadata.source_file_name:
        raise ValueError("source_path filename does not match verified DHS source_file_name")
    snapshot = source_snapshot or register_dhs_hr_snapshot(path, release=metadata.release)
    _validate_snapshot_source(snapshot, path, release=metadata.release)
    raw = _read_hr_source(path)
    silver = normalize_dhs_hr(
        raw,
        metadata=metadata,
        snapshot=snapshot,
        source_path=path,
        column_map=column_map,
    )

    dataset = _dataset_ref(snapshot)
    destination = data_root.silver(
        "surveys", "dhs", silver.catalog.survey_id, snapshot.snapshot_id
    )
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        source_snapshot=snapshot,
        outputs=(
            FileMaterialization(
                dataset=dataset,
                relative_path="hr_households.parquet",
                destination_base=destination,
                writer=lambda output: silver.frame.to_parquet(output, index=False),
            ),
        ),
        parameters={
            "survey_id": silver.catalog.survey_id,
            "dhs_survey_id": metadata.dhs_survey_id,
            "country_iso3": metadata.country_iso3,
            "source_release": snapshot.release,
            "source_recode": DHS_HR_RECODE,
            "source_file_name": metadata.source_file_name,
            "source_schema_sha256": silver.schema_sha256,
            "source_column_map": silver.source_columns,
            "source_weight_transformation": None,
            "aggregation": None,
        },
        code_commit=code_commit,
        qa=silver.qa,
        overwrite=overwrite,
    )
    hashed = _hashed_output(manifest, dataset.dataset_id)

    persist_run_artifact(
        data_root,
        run_id,
        "catalog/dhs_survey.json",
        _json_text(
            {
                "survey_id": silver.catalog.survey_id,
                "source_family": silver.catalog.source_family,
                "country_iso3": silver.catalog.country_iso3,
                "survey_year": silver.catalog.survey_year,
                "survey_phase": silver.catalog.survey_phase,
                "release": silver.catalog.release,
            }
        ),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "catalog/dhs_hr_file_link.json",
        _json_text(
            {
                "survey_id": silver.file_link.survey_id,
                "source_snapshot_id": silver.file_link.source_snapshot_id,
                "source_file": silver.file_link.source_file.model_dump(mode="json"),
                "instrument": silver.file_link.instrument,
            }
        ),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "mappings/dhs_hr_source_columns.json",
        _json_text(
            {
                "resolved_columns": silver.source_columns,
                "schema_sha256": silver.schema_sha256,
            }
        ),
        overwrite=overwrite,
    )
    return snapshot, silver, manifest, hashed, destination / "hr_households.parquet"


__all__ = [
    "DHS_HR_RECODE",
    "DHS_SOURCE",
    "DhsHrColumnMap",
    "DhsHrMetadata",
    "DhsHrSilverResult",
    "HOUSEHOLD_GRAIN",
    "STANDARD_DHS_HR_COLUMNS",
    "build_dhs_hr_file_link",
    "build_dhs_survey_catalog",
    "build_dhs_survey_id",
    "iter_dhs_hr_design_records",
    "materialize_dhs_hr_silver",
    "normalize_dhs_hr",
    "register_dhs_hr_snapshot",
]
