from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
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

GEOGCDF_SOURCE = "aiddata_geogcdf"
GEOGCDF_ORIGIN = "https://github.com/aiddata/gcdf-geospatial-data"
RAW_PREFIX = "source__"

FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "source_project_id": ("id", "ID", "project_id", "Project.ID"),
    "recipient": ("Recipient", "recipient"),
    "recipient_iso3": ("Recipient.ISO-3", "Recipient_ISO_3", "recipient_iso3"),
    "title": ("Title", "title"),
    "reported_amount_constant_usd_2021": (
        "Amount.(Constant.USD.2021)",
        "Amount_Constant_USD_2021",
        "amount_constant_usd_2021",
    ),
    "native_status": ("Status", "status"),
    "native_sector": ("Sector.Name", "Sector_Name", "sector_name"),
    "infrastructure": ("Infrastructure", "infrastructure"),
    "commitment_year": ("Commitment.Year", "Commitment_Year", "commitment_year"),
    "implementation_start_year": (
        "Implementation.Start.Year",
        "Implementation_Start_Year",
        "implementation_start_year",
    ),
    "completion_year": ("Completion.Year", "Completion_Year", "completion_year"),
    "commitment_date": (
        "Commitment.Date.(MM/DD/YYYY)",
        "Commitment_Date_MM_DD_YYYY",
        "commitment_date",
    ),
    "implementation_start_date": (
        "Actual.Implementation.Start.Date.(MM/DD/YYYY)",
        "Actual_Implementation_Start_Date_MM_DD_YYYY",
        "implementation_start_date",
    ),
    "completion_date": (
        "Actual.Completion.Date.(MM/DD/YYYY)",
        "Actual_Completion_Date_MM_DD_YYYY",
        "completion_date",
    ),
    "feature_count": ("feature_count", "Feature.Count", "feature_count_1"),
    "osm_links": ("osm_links", "OSM.Links", "osm_links_1"),
    "osm_precision_list": ("osm_precision_list", "OSM.Precision.List"),
}

REQUIRED_FIELDS = (
    "source_project_id",
    "recipient_iso3",
    "title",
    "commitment_year",
)


@dataclass(frozen=True)
class GeoGCDFSilverResult:
    frame: gpd.GeoDataFrame
    qa: tuple[QAResult, ...]
    source_columns: dict[str, str | None]
    schema_sha256: str


def register_geogcdf_snapshot(
    source_path: str | Path,
    *,
    release: str = "v3.0.1",
    origin: str = GEOGCDF_ORIGIN,
) -> SourceSnapshotRef:
    """Register one official bulk GeoGCDF geospatial artifact without copying it."""
    snapshot = register_external_snapshot(GEOGCDF_SOURCE, release, [source_path])
    return snapshot.model_copy(update={"origin": origin})


def _resolve_source_columns(columns: list[str]) -> dict[str, str | None]:
    if len(set(columns)) != len(columns):
        raise ValueError("GeoGCDF input has duplicate column labels")
    folded: dict[str, str] = {}
    for column in columns:
        key = column.casefold()
        if key in folded and folded[key] != column:
            raise ValueError("GeoGCDF input has case-insensitive duplicate column labels")
        folded[key] = column

    exact = set(columns)
    resolved: dict[str, str | None] = {}
    for canonical, candidates in FIELD_CANDIDATES.items():
        match = next((candidate for candidate in candidates if candidate in exact), None)
        if match is None:
            match = next(
                (
                    folded[candidate.casefold()]
                    for candidate in candidates
                    if candidate.casefold() in folded
                ),
                None,
            )
        resolved[canonical] = match

    missing = [field for field in REQUIRED_FIELDS if resolved[field] is None]
    if missing:
        raise ValueError(
            "GeoGCDF input is missing required official project fields: " + ", ".join(missing)
        )
    return resolved


def _read_geogcdf(source_path: str | Path, *, layer: str | None = None) -> gpd.GeoDataFrame:
    path = Path(source_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    selected_layer = layer
    if path.suffix.lower() == ".gpkg" and selected_layer is None:
        layers = gpd.list_layers(path)
        names = layers["name"].astype(str).tolist()
        if len(names) != 1:
            raise ValueError(
                "GeoGCDF GeoPackage has multiple layers; select one explicitly: "
                + ", ".join(names)
            )
        selected_layer = names[0]
    frame = gpd.read_file(path, layer=selected_layer)
    if frame.crs is None:
        raise ValueError("GeoGCDF source geometry must have a declared CRS")
    return frame


def _string_column(raw: gpd.GeoDataFrame, column: str | None) -> pd.Series:
    if column is None:
        return pd.Series(pd.NA, index=raw.index, dtype="string")
    return raw[column].astype("string").str.strip().replace("", pd.NA)


def _number_column(raw: gpd.GeoDataFrame, column: str | None) -> pd.Series:
    if column is None:
        return pd.Series(pd.NA, index=raw.index, dtype="Float64")
    return pd.to_numeric(raw[column], errors="coerce").astype("Float64")


def _date_column(raw: gpd.GeoDataFrame, column: str | None) -> pd.Series:
    if column is None:
        return pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")
    return pd.to_datetime(raw[column], errors="coerce")


def _schema_sha256(raw: gpd.GeoDataFrame) -> str:
    payload = {
        "columns": [str(column) for column in raw.columns],
        "dtypes": [str(dtype) for dtype in raw.dtypes],
        "crs": str(raw.crs),
        "geometry_name": raw.geometry.name,
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_geogcdf_projects(
    raw: gpd.GeoDataFrame,
    *,
    snapshot: SourceSnapshotRef,
) -> GeoGCDFSilverResult:
    """Preserve GeoGCDF project geometries and source attributes at source-native grain."""
    if raw.crs is None:
        raise ValueError("GeoGCDF source geometry must have a declared CRS")
    source_columns = _resolve_source_columns(
        [str(column) for column in raw.columns if column != raw.geometry.name]
    )

    frame = gpd.GeoDataFrame(index=raw.index, geometry=raw.geometry.to_crs("EPSG:4326"), crs="EPSG:4326")
    frame["project_geometry_row_id"] = [
        f"{snapshot.snapshot_id}:{position:07d}" for position in range(len(raw))
    ]
    frame["source_family"] = GEOGCDF_SOURCE
    frame["source_release"] = snapshot.release
    frame["source_snapshot_id"] = snapshot.snapshot_id
    frame["source_project_id"] = _string_column(raw, source_columns["source_project_id"])
    frame["recipient"] = _string_column(raw, source_columns["recipient"])
    frame["recipient_iso3"] = _string_column(raw, source_columns["recipient_iso3"])
    frame["title"] = _string_column(raw, source_columns["title"])
    frame["native_status"] = _string_column(raw, source_columns["native_status"])
    frame["native_sector"] = _string_column(raw, source_columns["native_sector"])
    frame["infrastructure"] = _string_column(raw, source_columns["infrastructure"])
    frame["reported_amount_constant_usd_2021"] = _number_column(
        raw, source_columns["reported_amount_constant_usd_2021"]
    )
    frame["commitment_year"] = _number_column(raw, source_columns["commitment_year"])
    frame["implementation_start_year"] = _number_column(
        raw, source_columns["implementation_start_year"]
    )
    frame["completion_year"] = _number_column(raw, source_columns["completion_year"])
    frame["commitment_date"] = _date_column(raw, source_columns["commitment_date"])
    frame["implementation_start_date"] = _date_column(
        raw, source_columns["implementation_start_date"]
    )
    frame["completion_date"] = _date_column(raw, source_columns["completion_date"])
    frame["feature_count"] = _number_column(raw, source_columns["feature_count"])
    frame["osm_links"] = _string_column(raw, source_columns["osm_links"])
    frame["osm_precision_list"] = _string_column(raw, source_columns["osm_precision_list"])

    raw_attributes = raw.drop(columns=[raw.geometry.name]).copy()
    raw_attributes.columns = [f"{RAW_PREFIX}{column}" for column in raw_attributes.columns]
    frame = gpd.GeoDataFrame(
        pd.concat([frame.reset_index(drop=True), raw_attributes.reset_index(drop=True)], axis=1),
        geometry="geometry",
        crs="EPSG:4326",
    )

    missing_id = int(frame["source_project_id"].isna().sum())
    present_ids = frame.loc[frame["source_project_id"].notna(), "source_project_id"]
    duplicate_id_rows = int(present_ids.duplicated(keep=False).sum())
    null_geometry = frame.geometry.isna()
    empty_geometry = ~null_geometry & frame.geometry.is_empty
    invalid_geometry = ~null_geometry & ~empty_geometry & ~frame.geometry.is_valid
    geometry_types = frame.geometry.geom_type.astype("string").fillna("<missing>")
    unsupported_geometry = ~geometry_types.isin(
        ["Point", "Polygon", "MultiPolygon", "<missing>"]
    )

    source_amount = source_columns["reported_amount_constant_usd_2021"]
    if source_amount is None:
        amount_parse_failures = 0
    else:
        source_present = raw[source_amount].notna() & raw[source_amount].astype("string").str.strip().ne("")
        amount_parse_failures = int(
            (source_present & frame["reported_amount_constant_usd_2021"].isna()).sum()
        )

    qa = (
        QAResult(
            check_id="geogcdf.projects.row_preservation",
            state="GREEN" if len(frame) == len(raw) else "RED",
            message="Silver preserves every supplied GeoGCDF feature row",
            metrics={"input_rows": len(raw), "silver_rows": len(frame)},
        ),
        QAResult(
            check_id="geogcdf.projects.source_id",
            state="GREEN" if missing_id == 0 and duplicate_id_rows == 0 else "RED",
            message="source project IDs are retained and audited without deduplication",
            metrics={
                "missing_source_id": missing_id,
                "duplicate_source_id_rows": duplicate_id_rows,
            },
        ),
        QAResult(
            check_id="geogcdf.projects.geometry",
            state=(
                "GREEN"
                if int(null_geometry.sum()) == 0
                and int(empty_geometry.sum()) == 0
                and int(invalid_geometry.sum()) == 0
                and int(unsupported_geometry.sum()) == 0
                else "YELLOW"
            ),
            message="project geometry quality remains explicit; no centroid coercion occurs",
            metrics={
                "missing_geometry": int(null_geometry.sum()),
                "empty_geometry": int(empty_geometry.sum()),
                "invalid_geometry": int(invalid_geometry.sum()),
                "unsupported_geometry": int(unsupported_geometry.sum()),
            },
        ),
        QAResult(
            check_id="geogcdf.projects.amount_parse",
            state="GREEN" if amount_parse_failures == 0 else "YELLOW",
            message="reported source amount is parsed numerically without zero coercion",
            metrics={"amount_parse_failures": amount_parse_failures},
        ),
    )
    return GeoGCDFSilverResult(
        frame=frame,
        qa=qa,
        source_columns=source_columns,
        schema_sha256=_schema_sha256(raw),
    )


def _validate_snapshot_source(snapshot: SourceSnapshotRef, source_path: str | Path) -> None:
    resolved = Path(source_path).expanduser().resolve()
    matches = [ref for ref in snapshot.files if Path(ref.path).expanduser().resolve() == resolved]
    if len(matches) != 1:
        raise ValueError("GeoGCDF source_path must be represented exactly once in SourceSnapshotRef")
    if sha256_file(resolved) != matches[0].sha256:
        raise ValueError("GeoGCDF source file changed after snapshot registration")


def _dataset_ref(snapshot: SourceSnapshotRef) -> DatasetRef:
    return DatasetRef(
        dataset_id="investments.aiddata_geogcdf.projects",
        version=snapshot.snapshot_id,
        schema_version="geogcdf-project-geometry-silver-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("project_geometry_row_id",)),
    )


def _hashed_output(manifest: RunManifest, dataset_id: str) -> DatasetRef:
    matches = [item for item in manifest.outputs if item.dataset_id == dataset_id]
    if len(matches) != 1:
        raise RuntimeError(f"expected one materialized dataset {dataset_id!r}")
    return matches[0]


def materialize_geogcdf_silver(
    *,
    source_path: str | Path,
    data_root: DataRoot,
    run_id: str,
    release: str = "v3.0.1",
    layer: str | None = None,
    source_snapshot: SourceSnapshotRef | None = None,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> tuple[SourceSnapshotRef, GeoGCDFSilverResult, RunManifest, DatasetRef, Path]:
    """Register an official GeoGCDF artifact and materialize project-native GeoParquet Silver."""
    snapshot = source_snapshot or register_geogcdf_snapshot(source_path, release=release)
    if snapshot.release != release:
        raise ValueError("supplied GeoGCDF snapshot release does not match requested release")
    _validate_snapshot_source(snapshot, source_path)
    raw = _read_geogcdf(source_path, layer=layer)
    silver = normalize_geogcdf_projects(raw, snapshot=snapshot)
    dataset = _dataset_ref(snapshot)
    destination = data_root.silver("investments", "aiddata_geogcdf_projects", dataset.version)
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        source_snapshot=snapshot,
        outputs=(
            FileMaterialization(
                dataset=dataset,
                relative_path="projects.parquet",
                destination_base=destination,
                writer=lambda path: silver.frame.to_parquet(path, index=False),
            ),
        ),
        parameters={
            "source_release": snapshot.release,
            "source_layer": layer,
            "source_schema_sha256": silver.schema_sha256,
            "project_filter": None,
            "geometry_coercion": None,
            "amount_allocation": None,
        },
        code_commit=code_commit,
        qa=silver.qa,
        overwrite=overwrite,
    )
    hashed = _hashed_output(manifest, dataset.dataset_id)
    persist_run_artifact(
        data_root,
        run_id,
        "mappings/geogcdf_source_columns.json",
        json.dumps(silver.source_columns, sort_keys=True, indent=2) + "\n",
        overwrite=overwrite,
    )
    return snapshot, silver, manifest, hashed, destination / "projects.parquet"
