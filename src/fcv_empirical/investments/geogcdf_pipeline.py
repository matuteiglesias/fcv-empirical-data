from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
from empirical_contracts import (
    AuthorityLevel,
    DataLayer,
    DatasetRef,
    GeographySpec,
    GrainSpec,
    MeasurementContract,
    PeriodScheme,
    RunManifest,
    SourceSnapshotRef,
)
from spatial_foundation import DataRoot

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact

from .geogcdf import GeoGCDFSilverResult, materialize_geogcdf_silver
from .geogcdf_measurements import (
    GeoGCDFGeographyResult,
    GeoGCDFGoldResult,
    GeoGCDFPeriodResult,
    assign_geogcdf_periods,
    build_geogcdf_commitment_coverage,
    build_geogcdf_commitment_gold,
    build_geogcdf_commitment_measurement_contract,
    relate_geogcdf_geography,
)


@dataclass(frozen=True)
class GeoGCDFVerticalResult:
    snapshot: SourceSnapshotRef
    silver_manifest: RunManifest
    measurement_manifest: RunManifest
    silver_dataset: DatasetRef
    geography_relation_dataset: DatasetRef
    period_relation_dataset: DatasetRef
    gold_dataset: DatasetRef
    measurement_contract: MeasurementContract
    paths: dict[str, Path]


def _json_text(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _geography_token(geography: GeographySpec) -> str:
    return hashlib.sha256(geography.id.encode("utf-8")).hexdigest()[:12]


def _relation_version(snapshot: SourceSnapshotRef, geography: GeographySpec) -> str:
    return f"{snapshot.snapshot_id}--geo-{_geography_token(geography)}"


def _measurement_version(
    snapshot: SourceSnapshotRef,
    geography: GeographySpec,
    period_scheme: PeriodScheme,
) -> str:
    return f"{snapshot.snapshot_id}--geo-{_geography_token(geography)}--{period_scheme.id}"


def _geography_relation_ref(
    snapshot: SourceSnapshotRef,
    geography: GeographySpec,
) -> DatasetRef:
    return DatasetRef(
        dataset_id="investments.aiddata_geogcdf.project_geography",
        version=_relation_version(snapshot, geography),
        schema_version="geogcdf-project-geography-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("project_geometry_row_id", "geo_uid")),
        geography=geography,
    )


def _period_relation_ref(
    snapshot: SourceSnapshotRef,
    period_scheme: PeriodScheme,
) -> DatasetRef:
    return DatasetRef(
        dataset_id="investments.aiddata_geogcdf.project_period",
        version=f"{snapshot.snapshot_id}--{period_scheme.id}",
        schema_version="geogcdf-project-period-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("project_geometry_row_id", "project_date_type")),
        period_scheme=period_scheme,
    )


def _gold_ref(
    snapshot: SourceSnapshotRef,
    geography: GeographySpec,
    period_scheme: PeriodScheme,
) -> DatasetRef:
    return DatasetRef(
        dataset_id="investments.aiddata_geogcdf.commitment_area_period",
        version=_measurement_version(snapshot, geography, period_scheme),
        schema_version="geogcdf-commitment-area-period-gold-v1",
        layer=DataLayer.GOLD,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("geo_uid", "period_id")),
        geography=geography,
        period_scheme=period_scheme,
    )


def _hashed_output(manifest: RunManifest, dataset_id: str) -> DatasetRef:
    matches = [dataset for dataset in manifest.outputs if dataset.dataset_id == dataset_id]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one materialized dataset {dataset_id!r}")
    return matches[0]


def materialize_geogcdf_measurement(
    *,
    snapshot: SourceSnapshotRef,
    silver: gpd.GeoDataFrame,
    silver_dataset: DatasetRef,
    geography_units: gpd.GeoDataFrame,
    geography: GeographySpec,
    geography_dataset: DatasetRef,
    period_scheme: PeriodScheme,
    data_root: DataRoot,
    run_id: str,
    source_universe_start_year: int = 2000,
    source_universe_end_year: int = 2021,
    require_complete_resolution: bool = True,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> tuple[
    GeoGCDFGeographyResult,
    GeoGCDFPeriodResult,
    GeoGCDFGoldResult,
    RunManifest,
    MeasurementContract,
    dict[str, DatasetRef],
    dict[str, Path],
]:
    """Materialize project-geography/time relations and commitment-area-period Gold."""
    geography_result = relate_geogcdf_geography(silver, geography_units)
    period_result = assign_geogcdf_periods(silver, scheme=period_scheme)
    gold_result = build_geogcdf_commitment_gold(
        silver,
        geography_result.frame,
        period_result.frame,
        geography_units,
        period_scheme=period_scheme,
        source_universe_start_year=source_universe_start_year,
        source_universe_end_year=source_universe_end_year,
        require_complete_resolution=require_complete_resolution,
    )

    geography_ref = _geography_relation_ref(snapshot, geography)
    period_ref = _period_relation_ref(snapshot, period_scheme)
    gold_ref = _gold_ref(snapshot, geography, period_scheme)
    geography_base = data_root.silver(
        "investments", "aiddata_geogcdf_project_geography", geography_ref.version
    )
    period_base = data_root.silver(
        "investments", "aiddata_geogcdf_project_period", period_ref.version
    )
    gold_base = data_root.gold(
        "investments", "aiddata_geogcdf_commitment_area_period", gold_ref.version
    )

    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        inputs=(silver_dataset, geography_dataset),
        outputs=(
            FileMaterialization(
                dataset=geography_ref,
                relative_path="project_geography.parquet",
                destination_base=geography_base,
                writer=lambda path: geography_result.frame.to_parquet(path, index=False),
            ),
            FileMaterialization(
                dataset=period_ref,
                relative_path="project_period.parquet",
                destination_base=period_base,
                writer=lambda path: period_result.frame.to_parquet(path, index=False),
            ),
            FileMaterialization(
                dataset=gold_ref,
                relative_path="commitment_area_period.parquet",
                destination_base=gold_base,
                writer=lambda path: gold_result.frame.to_parquet(path, index=False),
            ),
        ),
        parameters={
            "source_snapshot_id": snapshot.snapshot_id,
            "geography": geography.model_dump(mode="json"),
            "period_scheme": period_scheme.model_dump(mode="json"),
            "source_universe_start_year": source_universe_start_year,
            "source_universe_end_year": source_universe_end_year,
            "require_complete_resolution": require_complete_resolution,
            "point_geography_policy": "matched_unique_only",
            "areal_geography_policy": "all_positive_area_overlaps",
            "project_date_type": "commitment",
            "amount_allocation": None,
            "amount_sum_materialized": False,
        },
        code_commit=code_commit,
        qa=(*geography_result.qa, *period_result.qa, *gold_result.qa),
        overwrite=overwrite,
    )

    hashed_geography = _hashed_output(manifest, geography_ref.dataset_id)
    hashed_period = _hashed_output(manifest, period_ref.dataset_id)
    hashed_gold = _hashed_output(manifest, gold_ref.dataset_id)
    coverage = build_geogcdf_commitment_coverage(
        gold_result,
        geography=geography,
        period_scheme=period_scheme,
    )
    measurement = build_geogcdf_commitment_measurement_contract(
        silver_dataset=silver_dataset,
        geography=geography,
        period_scheme=period_scheme,
        coverage=coverage,
        covered_country_iso3=gold_result.covered_country_iso3,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "contracts/coverage.json",
        _json_text(coverage.model_dump(mode="json")),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "contracts/measurement_contract.json",
        _json_text(measurement.model_dump(mode="json")),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "coverage/covered_country_iso3.json",
        _json_text(list(gold_result.covered_country_iso3)),
        overwrite=overwrite,
    )

    datasets = {
        "geography_relation": hashed_geography,
        "period_relation": hashed_period,
        "gold": hashed_gold,
    }
    paths = {
        "geography_relation": geography_base / "project_geography.parquet",
        "period_relation": period_base / "project_period.parquet",
        "gold": gold_base / "commitment_area_period.parquet",
    }
    return (
        geography_result,
        period_result,
        gold_result,
        manifest,
        measurement,
        datasets,
        paths,
    )


def materialize_geogcdf_vertical(
    *,
    source_path: str | Path,
    data_root: DataRoot,
    run_id: str,
    geography_units: gpd.GeoDataFrame,
    geography: GeographySpec,
    geography_dataset: DatasetRef,
    period_scheme: PeriodScheme,
    release: str = "v3.0.1",
    layer: str | None = None,
    source_universe_start_year: int = 2000,
    source_universe_end_year: int = 2021,
    require_complete_resolution: bool = True,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> GeoGCDFVerticalResult:
    """Execute official GeoGCDF snapshot→Silver→geo/time→contracted commitment Gold."""
    snapshot, silver, silver_manifest, silver_ref, silver_path = materialize_geogcdf_silver(
        source_path=source_path,
        data_root=data_root,
        run_id=f"{run_id}-silver",
        release=release,
        layer=layer,
        code_commit=code_commit,
        overwrite=overwrite,
    )
    (
        _geography_result,
        _period_result,
        _gold_result,
        measurement_manifest,
        measurement,
        datasets,
        measurement_paths,
    ) = materialize_geogcdf_measurement(
        snapshot=snapshot,
        silver=silver.frame,
        silver_dataset=silver_ref,
        geography_units=geography_units,
        geography=geography,
        geography_dataset=geography_dataset,
        period_scheme=period_scheme,
        data_root=data_root,
        run_id=f"{run_id}-measurement",
        source_universe_start_year=source_universe_start_year,
        source_universe_end_year=source_universe_end_year,
        require_complete_resolution=require_complete_resolution,
        code_commit=code_commit,
        overwrite=overwrite,
    )
    return GeoGCDFVerticalResult(
        snapshot=snapshot,
        silver_manifest=silver_manifest,
        measurement_manifest=measurement_manifest,
        silver_dataset=silver_ref,
        geography_relation_dataset=datasets["geography_relation"],
        period_relation_dataset=datasets["period_relation"],
        gold_dataset=datasets["gold"],
        measurement_contract=measurement,
        paths={"silver": silver_path, **measurement_paths},
    )
