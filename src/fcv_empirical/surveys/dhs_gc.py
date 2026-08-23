from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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
from .variables import SurveyVariableMetadata, TemporalSemantics

DHS_GC_SOURCE = "dhs_gc"
DHS_GC_ORIGIN = "DHS Program Geospatial Covariates"
RAW_PREFIX = "source__"
CLUSTER_GRAIN = "cluster"


@dataclass(frozen=True)
class GCTemporalRule:
    """Documentation-backed temporal rule for one source-variable name pattern.

    No rule means ``UNKNOWN``. A year is parsed only when ``year_group`` explicitly
    names a regex capture group or ``documented_year`` is supplied by the registry.
    Merely lacking a year suffix never acquires survey-year meaning.
    """

    source_variable_pattern: str
    temporal_semantics: TemporalSemantics
    year_group: str | int | None = None
    documented_year: int | None = None
    documented_time_token: str | None = None
    ignore_case: bool = False
    codebook_provenance: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_variable_pattern or not self.source_variable_pattern.strip():
            raise ValueError("source_variable_pattern must be non-empty")
        try:
            re.compile(self.source_variable_pattern)
        except re.error as error:
            raise ValueError(f"invalid source_variable_pattern: {error}") from error
        if self.year_group is not None and self.documented_year is not None:
            raise ValueError("year_group and documented_year are mutually exclusive")
        if self.documented_year is not None and not 1 <= self.documented_year <= 9999:
            raise ValueError("documented_year must be a valid calendar year")
        if self.documented_time_token is not None and not self.documented_time_token.strip():
            raise ValueError("documented_time_token must be non-empty when supplied")

    def match(self, source_variable: str) -> re.Match[str] | None:
        flags = re.IGNORECASE if self.ignore_case else 0
        return re.fullmatch(self.source_variable_pattern, source_variable, flags=flags)


@dataclass(frozen=True)
class GCVariableTemporalMetadata:
    source_variable: str
    temporal_semantics: TemporalSemantics
    source_time_token: str | None
    measurement_year: int | None
    codebook_provenance: Mapping[str, str]


@dataclass(frozen=True)
class GCTimeParsingReport:
    explicit_year_variables: tuple[str, ...]
    static_variables: tuple[str, ...]
    annual_variables: tuple[str, ...]
    epoch_variables: tuple[str, ...]
    climatology_variables: tuple[str, ...]
    survey_time_variables: tuple[str, ...]
    retrospective_variables: tuple[str, ...]
    unknown_variables: tuple[str, ...]
    impossible_year_variables: tuple[str, ...]
    parse_conflict_variables: tuple[str, ...]


@dataclass(frozen=True)
class DHSGCClusterSilverResult:
    frame: pd.DataFrame
    qa: tuple[QAResult, ...]
    raw_column_map: Mapping[str, str]
    cluster_column: str
    schema_sha256: str


@dataclass(frozen=True)
class DHSGCMeasurementResult:
    frame: pd.DataFrame
    variable_metadata: tuple[SurveyVariableMetadata, ...]
    temporal_metadata: tuple[GCVariableTemporalMetadata, ...]
    temporal_report: GCTimeParsingReport
    coverage: pd.DataFrame
    qa: tuple[QAResult, ...]
    temporal_registry_sha256: str
    measurement_spec_sha256: str


def register_dhs_gc_snapshot(
    source_path: str | Path,
    *,
    release: str,
    origin: str = DHS_GC_ORIGIN,
) -> SourceSnapshotRef:
    """Register one externally stored DHS GC release file without copying it."""
    snapshot = register_external_snapshot(DHS_GC_SOURCE, release, [source_path])
    return snapshot.model_copy(update={"origin": origin})


def resolve_dhs_gc_file_link(
    survey: SurveyCatalogEntry,
    snapshot: SourceSnapshotRef,
    source_path: str | Path,
) -> SurveyFileLink:
    """Resolve one GC file explicitly to SurveyCatalog instead of inferring survey identity."""
    resolved = Path(source_path).expanduser().resolve()
    matches = [
        source_file
        for source_file in snapshot.files
        if Path(source_file.path).expanduser().resolve() == resolved
    ]
    if len(matches) != 1:
        raise ValueError("DHS GC source_path must be represented exactly once in SourceSnapshotRef")
    link = SurveyFileLink(
        survey_id=survey.survey_id,
        source_snapshot_id=snapshot.snapshot_id,
        source_file=matches[0],
        instrument="GC",
    )
    validate_survey_file_link(survey, link, snapshot)
    return link


def _validate_snapshot_source(snapshot: SourceSnapshotRef, source_path: str | Path) -> None:
    resolved = Path(source_path).expanduser().resolve()
    matches = [
        source_file
        for source_file in snapshot.files
        if Path(source_file.path).expanduser().resolve() == resolved
    ]
    if len(matches) != 1:
        raise ValueError("DHS GC source_path must be represented exactly once in SourceSnapshotRef")
    if sha256_file(resolved) != matches[0].sha256:
        raise ValueError("DHS GC source file changed after snapshot registration")


def _read_dhs_gc(source_path: str | Path) -> pd.DataFrame:
    path = Path(source_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, keep_default_na=False, na_values=[""])
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".xlsx":
        return pd.read_excel(path, keep_default_na=False, na_values=[""])
    raise ValueError("DHS GC reader supports .csv, .parquet, and .xlsx source files")


def _schema_sha256(raw: pd.DataFrame) -> str:
    payload = {
        "columns": [str(column) for column in raw.columns],
        "dtypes": [str(dtype) for dtype in raw.dtypes],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def normalize_dhs_gc_clusters(
    raw: pd.DataFrame,
    *,
    survey: SurveyCatalogEntry,
    snapshot: SourceSnapshotRef,
    cluster_column: str = "DHSID",
) -> DHSGCClusterSilverResult:
    """Build source-native cluster Silver without aggregation or value imputation."""
    source_columns = [str(column) for column in raw.columns]
    if len(set(source_columns)) != len(source_columns):
        raise ValueError("DHS GC input has duplicate column labels")
    if cluster_column not in raw.columns:
        raise ValueError(f"DHS GC input is missing cluster identity column {cluster_column!r}")

    cluster_id = raw[cluster_column].astype("string").str.strip().replace("", pd.NA)
    missing_cluster_ids = int(cluster_id.isna().sum())
    duplicate_cluster_rows = int(cluster_id[cluster_id.notna()].duplicated(keep=False).sum())
    if missing_cluster_ids:
        raise ValueError("DHS GC source contains rows without cluster identity")
    if duplicate_cluster_rows:
        raise ValueError("DHS GC source contains duplicate cluster identities")

    raw_column_map = {column: f"{RAW_PREFIX}{column}" for column in source_columns}
    source_native = raw.copy()
    source_native.columns = [raw_column_map[column] for column in source_columns]

    frame = pd.DataFrame(index=raw.index)
    frame["survey_id"] = survey.survey_id
    frame["cluster_id"] = cluster_id
    frame["source_release"] = snapshot.release
    frame["source_snapshot_id"] = snapshot.snapshot_id
    frame = pd.concat([frame.reset_index(drop=True), source_native.reset_index(drop=True)], axis=1)

    qa = (
        QAResult(
            check_id="dhs_gc.clusters.row_preservation",
            state="GREEN" if len(frame) == len(raw) else "RED",
            message="cluster Silver preserves every supplied DHS GC row",
            metrics={"input_rows": len(raw), "silver_rows": len(frame)},
        ),
        QAResult(
            check_id="dhs_gc.clusters.identity",
            state="GREEN",
            message="survey and source cluster identities define the durable cluster grain",
            metrics={
                "cluster_count": len(frame),
                "missing_cluster_ids": 0,
                "duplicate_cluster_rows": 0,
            },
        ),
        QAResult(
            check_id="dhs_gc.clusters.no_geography_aggregation",
            state="GREEN",
            message="GC remains cluster-conditioned; no polygon/GID aggregation is performed",
            metrics={"gid_aggregation_count": 0},
        ),
    )
    return DHSGCClusterSilverResult(
        frame=frame,
        qa=qa,
        raw_column_map=raw_column_map,
        cluster_column=cluster_column,
        schema_sha256=_schema_sha256(raw),
    )


def _parse_rule_year(
    rule: GCTemporalRule,
    match: re.Match[str],
) -> tuple[str | None, int | None, bool]:
    if rule.documented_year is not None:
        token = rule.documented_time_token or str(rule.documented_year)
        return token, rule.documented_year, False
    if rule.year_group is None:
        return rule.documented_time_token, None, False
    try:
        token = match.group(rule.year_group)
    except (IndexError, KeyError) as error:
        raise ValueError(
            f"year_group {rule.year_group!r} is absent from pattern "
            f"{rule.source_variable_pattern!r}"
        ) from error
    token = str(token)
    try:
        year = int(token)
    except ValueError:
        return token, None, True
    if not 1 <= year <= 9999:
        return token, None, True
    return token, year, False


def resolve_gc_temporal_metadata(
    source_variables: Sequence[str],
    *,
    source_family: str,
    source_value_types: Mapping[str, str] | None = None,
    rules: Iterable[GCTemporalRule] = (),
) -> tuple[
    tuple[SurveyVariableMetadata, ...],
    tuple[GCVariableTemporalMetadata, ...],
    GCTimeParsingReport,
    tuple[QAResult, ...],
]:
    """Resolve documented temporal semantics while preserving unknowns and conflicts."""
    rule_set = tuple(rules)
    variable_metadata: list[SurveyVariableMetadata] = []
    temporal_metadata: list[GCVariableTemporalMetadata] = []
    explicit_year: list[str] = []
    impossible_years: list[str] = []
    conflicts: list[str] = []

    for source_variable in source_variables:
        matches = [(rule, rule.match(source_variable)) for rule in rule_set]
        matched = [(rule, match) for rule, match in matches if match is not None]
        if len(matched) == 1:
            rule, match = matched[0]
            assert match is not None
            token, measurement_year, impossible = _parse_rule_year(rule, match)
            semantics = rule.temporal_semantics
            provenance = dict(rule.codebook_provenance)
            if measurement_year is not None:
                explicit_year.append(source_variable)
            if impossible:
                impossible_years.append(source_variable)
        elif len(matched) > 1:
            semantics = TemporalSemantics.UNKNOWN
            token = None
            measurement_year = None
            provenance = {}
            conflicts.append(source_variable)
        else:
            semantics = TemporalSemantics.UNKNOWN
            token = None
            measurement_year = None
            provenance = {}

        variable_metadata.append(
            SurveyVariableMetadata(
                source_family=source_family,
                source_variable=source_variable,
                source_label=None,
                natural_grain=CLUSTER_GRAIN,
                source_value_type=(source_value_types or {}).get(
                    source_variable, "source-native-wide"
                ),
                temporal_semantics=semantics,
                instrument="GC",
                codebook_provenance=provenance,
            )
        )
        temporal_metadata.append(
            GCVariableTemporalMetadata(
                source_variable=source_variable,
                temporal_semantics=semantics,
                source_time_token=token,
                measurement_year=measurement_year,
                codebook_provenance=provenance,
            )
        )

    by_semantics: dict[TemporalSemantics, tuple[str, ...]] = {}
    for semantics in TemporalSemantics:
        by_semantics[semantics] = tuple(
            item.source_variable
            for item in temporal_metadata
            if item.temporal_semantics is semantics
        )

    report = GCTimeParsingReport(
        explicit_year_variables=tuple(explicit_year),
        static_variables=by_semantics[TemporalSemantics.STATIC],
        annual_variables=by_semantics[TemporalSemantics.ANNUAL],
        epoch_variables=by_semantics[TemporalSemantics.EPOCH],
        climatology_variables=by_semantics[TemporalSemantics.CLIMATOLOGY],
        survey_time_variables=by_semantics[TemporalSemantics.SURVEY_TIME],
        retrospective_variables=by_semantics[TemporalSemantics.RETROSPECTIVE],
        unknown_variables=by_semantics[TemporalSemantics.UNKNOWN],
        impossible_year_variables=tuple(impossible_years),
        parse_conflict_variables=tuple(conflicts),
    )
    qa = (
        QAResult(
            check_id="dhs_gc.temporal.semantics",
            state="YELLOW" if report.unknown_variables else "GREEN",
            message=(
                "temporal semantics come only from explicit documentation-backed "
                "registry rules"
            ),
            metrics={
                "explicit_year_variables": len(report.explicit_year_variables),
                "static_variables": len(report.static_variables),
                "annual_variables": len(report.annual_variables),
                "epoch_variables": len(report.epoch_variables),
                "climatology_variables": len(report.climatology_variables),
                "survey_time_variables": len(report.survey_time_variables),
                "retrospective_variables": len(report.retrospective_variables),
                "unknown_variables": len(report.unknown_variables),
            },
        ),
        QAResult(
            check_id="dhs_gc.temporal.year_parse",
            state=(
                "RED"
                if report.parse_conflict_variables
                else "YELLOW"
                if report.impossible_year_variables
                else "GREEN"
            ),
            message=(
                "explicit year parsing retains impossible years and rule conflicts "
                "as QA evidence"
            ),
            metrics={
                "impossible_year_variables": len(report.impossible_year_variables),
                "parse_conflict_variables": len(report.parse_conflict_variables),
            },
        ),
        QAResult(
            check_id="dhs_gc.temporal.no_panel_imputation",
            state="GREEN",
            message=(
                "no survey-year assignment, fill, interpolation, or period replication "
                "is performed"
            ),
            metrics={
                "ffill_count": 0,
                "bfill_count": 0,
                "interpolation_count": 0,
                "static_period_replication_count": 0,
                "implicit_survey_year_assignment_count": 0,
            },
        ),
    )
    return tuple(variable_metadata), tuple(temporal_metadata), report, qa


def _registry_sha256(rules: Iterable[GCTemporalRule]) -> str:
    payload = []
    for rule in rules:
        item = asdict(rule)
        item["temporal_semantics"] = rule.temporal_semantics.value
        item["year_group"] = str(rule.year_group) if rule.year_group is not None else None
        item["codebook_provenance"] = dict(sorted(rule.codebook_provenance.items()))
        payload.append(item)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _coverage_from_long(frame: pd.DataFrame) -> pd.DataFrame:
    observed = frame.assign(_observed=frame["source_value"].notna())
    cluster_observed = (
        observed.groupby(
            ["survey_id", "cluster_id", "source_variable"],
            dropna=False,
            sort=False,
        )["_observed"]
        .any()
        .reset_index()
    )
    summary = (
        cluster_observed.groupby(["survey_id", "source_variable"], sort=False)["_observed"]
        .agg(cluster_count="size", observed_cluster_count="sum")
        .reset_index()
    )
    summary["observed_cluster_count"] = summary["observed_cluster_count"].astype("int64")
    summary["missing_cluster_count"] = (
        summary["cluster_count"] - summary["observed_cluster_count"]
    )
    summary["coverage_scope"] = "dhs_cluster_measurement_availability"
    return summary


def build_dhs_gc_measurements(
    silver: DHSGCClusterSilverResult,
    *,
    survey: SurveyCatalogEntry,
    temporal_rules: Iterable[GCTemporalRule] = (),
    variable_columns: Iterable[str] | None = None,
) -> DHSGCMeasurementResult:
    """Build a derived long cluster-measurement view without inventing panel semantics."""
    rules = tuple(temporal_rules)
    if variable_columns is None:
        selected = [
            source_variable
            for source_variable in silver.raw_column_map
            if source_variable != silver.cluster_column
        ]
    else:
        selected = list(variable_columns)
        unknown = [name for name in selected if name not in silver.raw_column_map]
        if unknown:
            raise ValueError("unknown DHS GC source variable(s): " + ", ".join(unknown))
        if silver.cluster_column in selected:
            raise ValueError("cluster identity cannot be emitted as a GC covariate measurement")
        if len(set(selected)) != len(selected):
            raise ValueError("variable_columns contains duplicates")

    source_value_types = {
        source_variable: str(silver.frame[silver.raw_column_map[source_variable]].dtype)
        for source_variable in selected
    }
    variable_metadata, temporal_metadata, report, temporal_qa = resolve_gc_temporal_metadata(
        selected,
        source_family=survey.source_family,
        source_value_types=source_value_types,
        rules=rules,
    )
    temporal_by_variable = {item.source_variable: item for item in temporal_metadata}

    pieces: list[pd.DataFrame] = []
    for source_variable in selected:
        source_column = silver.raw_column_map[source_variable]
        values = silver.frame[source_column]
        item = temporal_by_variable[source_variable]
        piece = silver.frame[
            ["survey_id", "cluster_id", "source_release", "source_snapshot_id"]
        ].copy()
        piece["source_variable"] = source_variable
        piece["source_value"] = values.astype("string")
        piece["source_value_type"] = str(values.dtype)
        piece["source_time_token"] = item.source_time_token
        piece["temporal_semantics"] = item.temporal_semantics.value
        piece["measurement_year"] = item.measurement_year
        pieces.append(piece)

    columns = [
        "survey_id",
        "cluster_id",
        "source_variable",
        "source_value",
        "source_value_type",
        "source_time_token",
        "temporal_semantics",
        "measurement_year",
        "source_release",
        "source_snapshot_id",
    ]
    if pieces:
        frame = pd.concat(pieces, ignore_index=True)[columns]
        frame["measurement_year"] = pd.array(frame["measurement_year"], dtype="Int64")
    else:
        frame = pd.DataFrame({column: pd.Series(dtype="object") for column in columns})
        frame["measurement_year"] = pd.Series(dtype="Int64")

    coverage = _coverage_from_long(frame) if not frame.empty else pd.DataFrame(
        columns=[
            "survey_id",
            "source_variable",
            "cluster_count",
            "observed_cluster_count",
            "missing_cluster_count",
            "coverage_scope",
        ]
    )
    missing_measurements = int(frame["source_value"].isna().sum()) if not frame.empty else 0
    qa = (
        *temporal_qa,
        QAResult(
            check_id="dhs_gc.measurements.missingness",
            state="GREEN",
            message="missing GC source values remain missing; zero is never inferred from absence",
            metrics={
                "measurement_rows": len(frame),
                "missing_measurement_rows": missing_measurements,
                "imputed_measurement_rows": 0,
            },
        ),
        QAResult(
            check_id="dhs_gc.measurements.cluster_conditioning",
            state="GREEN",
            message="long measurements retain survey and cluster identity without GID aggregation",
            metrics={"gid_aggregation_count": 0},
        ),
    )
    temporal_registry_sha256 = _registry_sha256(rules)
    measurement_spec_payload = {
        "source_variables": selected,
        "temporal_registry_sha256": temporal_registry_sha256,
    }
    measurement_spec_sha256 = hashlib.sha256(
        json.dumps(measurement_spec_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return DHSGCMeasurementResult(
        frame=frame,
        variable_metadata=variable_metadata,
        temporal_metadata=temporal_metadata,
        temporal_report=report,
        coverage=coverage,
        qa=qa,
        temporal_registry_sha256=temporal_registry_sha256,
        measurement_spec_sha256=measurement_spec_sha256,
    )


def _cluster_dataset_ref(snapshot: SourceSnapshotRef) -> DatasetRef:
    return DatasetRef(
        dataset_id="surveys.dhs_gc.clusters",
        version=snapshot.snapshot_id,
        schema_version="dhs-gc-cluster-silver-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("survey_id", "cluster_id")),
    )


def _measurement_dataset_ref(
    silver_dataset: DatasetRef,
    measurement_spec_sha256: str,
) -> DatasetRef:
    return DatasetRef(
        dataset_id="surveys.dhs_gc.cluster_measurements",
        version=f"{silver_dataset.version}--spec-{measurement_spec_sha256[:12]}",
        schema_version="dhs-gc-cluster-measurement-silver-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("survey_id", "cluster_id", "source_variable")),
    )


def _hashed_output(manifest: RunManifest, dataset_id: str) -> DatasetRef:
    matches = [item for item in manifest.outputs if item.dataset_id == dataset_id]
    if len(matches) != 1:
        raise RuntimeError(f"expected one materialized dataset {dataset_id!r}")
    return matches[0]


def materialize_dhs_gc_silver(
    *,
    source_path: str | Path,
    survey: SurveyCatalogEntry,
    data_root: DataRoot,
    run_id: str,
    release: str,
    cluster_column: str = "DHSID",
    source_snapshot: SourceSnapshotRef | None = None,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> tuple[
    SourceSnapshotRef,
    SurveyFileLink,
    DHSGCClusterSilverResult,
    RunManifest,
    DatasetRef,
    Path,
]:
    """Register/validate one external GC file and materialize cluster-native Silver."""
    snapshot = source_snapshot or register_dhs_gc_snapshot(source_path, release=release)
    if snapshot.source != DHS_GC_SOURCE:
        raise ValueError("supplied source snapshot is not registered as DHS GC")
    if snapshot.release != release:
        raise ValueError("supplied DHS GC snapshot release does not match requested release")
    _validate_snapshot_source(snapshot, source_path)
    link = resolve_dhs_gc_file_link(survey, snapshot, source_path)
    raw = _read_dhs_gc(source_path)
    silver = normalize_dhs_gc_clusters(
        raw,
        survey=survey,
        snapshot=snapshot,
        cluster_column=cluster_column,
    )
    dataset = _cluster_dataset_ref(snapshot)
    destination = data_root.silver("surveys", "dhs_gc_clusters", dataset.version)
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        source_snapshot=snapshot,
        outputs=(
            FileMaterialization(
                dataset=dataset,
                relative_path="clusters.parquet",
                destination_base=destination,
                writer=lambda path: silver.frame.to_parquet(path, index=False),
            ),
        ),
        parameters={
            "survey_id": survey.survey_id,
            "survey_release": survey.release,
            "source_release": snapshot.release,
            "source_snapshot_id": snapshot.snapshot_id,
            "cluster_column": cluster_column,
            "source_schema_sha256": silver.schema_sha256,
            "gid_aggregation": None,
            "imputation": None,
        },
        code_commit=code_commit,
        qa=silver.qa,
        overwrite=overwrite,
    )
    hashed = _hashed_output(manifest, dataset.dataset_id)
    persist_run_artifact(
        data_root,
        run_id,
        "mappings/dhs_gc_source_columns.json",
        json.dumps(dict(silver.raw_column_map), sort_keys=True, indent=2) + "\n",
        overwrite=overwrite,
    )
    return snapshot, link, silver, manifest, hashed, destination / "clusters.parquet"


def _temporal_metadata_json(result: DHSGCMeasurementResult) -> str:
    payload: list[dict[str, Any]] = []
    for item in result.temporal_metadata:
        payload.append(
            {
                "source_variable": item.source_variable,
                "temporal_semantics": item.temporal_semantics.value,
                "source_time_token": item.source_time_token,
                "measurement_year": item.measurement_year,
                "codebook_provenance": dict(item.codebook_provenance),
            }
        )
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


def materialize_dhs_gc_measurements(
    *,
    silver: DHSGCClusterSilverResult,
    silver_dataset: DatasetRef,
    survey: SurveyCatalogEntry,
    data_root: DataRoot,
    run_id: str,
    temporal_rules: Iterable[GCTemporalRule] = (),
    variable_columns: Iterable[str] | None = None,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> tuple[DHSGCMeasurementResult, RunManifest, DatasetRef, Path]:
    """Materialize the derived long GC measurement view with explicit temporal semantics."""
    rules = tuple(temporal_rules)
    selected_variables = tuple(variable_columns) if variable_columns is not None else None
    result = build_dhs_gc_measurements(
        silver,
        survey=survey,
        temporal_rules=rules,
        variable_columns=selected_variables,
    )
    dataset = _measurement_dataset_ref(silver_dataset, result.measurement_spec_sha256)
    destination = data_root.silver("surveys", "dhs_gc_cluster_measurements", dataset.version)
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        inputs=(silver_dataset,),
        outputs=(
            FileMaterialization(
                dataset=dataset,
                relative_path="cluster_measurements.parquet",
                destination_base=destination,
                writer=lambda path: result.frame.to_parquet(path, index=False),
            ),
        ),
        parameters={
            "survey_id": survey.survey_id,
            "temporal_registry_sha256": result.temporal_registry_sha256,
            "variable_columns": (
                list(selected_variables) if selected_variables is not None else None
            ),
            "measurement_spec_sha256": result.measurement_spec_sha256,
            "implicit_survey_year_assignment": False,
            "ffill": False,
            "bfill": False,
            "interpolation": False,
            "static_period_replication": False,
            "gid_aggregation": False,
        },
        code_commit=code_commit,
        qa=result.qa,
        overwrite=overwrite,
    )
    hashed = _hashed_output(manifest, dataset.dataset_id)
    persist_run_artifact(
        data_root,
        run_id,
        "temporal/dhs_gc_variable_metadata.json",
        _temporal_metadata_json(result),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "temporal/dhs_gc_time_parsing_report.json",
        json.dumps(asdict(result.temporal_report), sort_keys=True, indent=2) + "\n",
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "coverage/dhs_gc_cluster_availability.json",
        result.coverage.to_json(orient="records", indent=2) + "\n",
        overwrite=overwrite,
    )
    return result, manifest, hashed, destination / "cluster_measurements.parquet"
