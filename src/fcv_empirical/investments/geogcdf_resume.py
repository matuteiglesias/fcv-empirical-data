from __future__ import annotations

from pathlib import Path

import pandas as pd
from empirical_contracts import (
    DatasetRef,
    GeographySpec,
    MeasurementContract,
    PeriodScheme,
    RunManifest,
    SourceSnapshotRef,
)
from spatial_foundation import DataRoot

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact

from .geogcdf_measurements import (
    GeoGCDFGoldResult,
    build_geogcdf_commitment_coverage,
    build_geogcdf_commitment_gold,
    build_geogcdf_commitment_measurement_contract,
)
from .geogcdf_pipeline import _gold_ref, _json_text


GEOGRAPHY_RELATION_DATASET_ID = "investments.aiddata_geogcdf.project_geography"
PERIOD_RELATION_DATASET_ID = "investments.aiddata_geogcdf.project_period"


def _require_governed_relation(
    dataset: DatasetRef,
    *,
    dataset_id: str,
    geography: GeographySpec | None = None,
    period_scheme: PeriodScheme | None = None,
) -> None:
    if dataset.dataset_id != dataset_id:
        raise ValueError(f"expected governed relation {dataset_id!r}, got {dataset.dataset_id!r}")
    if dataset.content_sha256 is None:
        raise ValueError(f"governed relation {dataset_id!r} must carry a content SHA-256")
    if geography is not None and dataset.geography != geography:
        raise ValueError("governed GeoGCDF geography relation contradicts target geography")
    if period_scheme is not None and dataset.period_scheme != period_scheme:
        raise ValueError("governed GeoGCDF period relation contradicts target PeriodScheme")


def _hashed_output(manifest: RunManifest, dataset_id: str) -> DatasetRef:
    matches = [output for output in manifest.outputs if output.dataset_id == dataset_id]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one materialized dataset {dataset_id!r}")
    return matches[0]


def materialize_geogcdf_gold_from_governed_relations(
    *,
    snapshot: SourceSnapshotRef,
    silver: pd.DataFrame,
    silver_dataset: DatasetRef,
    geography_relation: pd.DataFrame,
    geography_relation_dataset: DatasetRef,
    period_relation: pd.DataFrame,
    period_relation_dataset: DatasetRef,
    geography_units: pd.DataFrame,
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
) -> tuple[GeoGCDFGoldResult, RunManifest, MeasurementContract, DatasetRef, Path]:
    """Publish only GeoGCDF commitment Gold from already governed relation products.

    Geography and period relations are deterministic measurement intermediates. When those
    exact relation artifacts already exist under hash-bearing DatasetRefs, a later Gold policy
    run reuses them as explicit lineage inputs instead of republishing identical stable paths
    or recomputing the expensive spatial relation.
    """
    if silver_dataset.content_sha256 is None:
        raise ValueError("governed GeoGCDF Silver must carry a content SHA-256")
    if geography_dataset.content_sha256 is None:
        raise ValueError("governed target geography must carry a content SHA-256")
    _require_governed_relation(
        geography_relation_dataset,
        dataset_id=GEOGRAPHY_RELATION_DATASET_ID,
        geography=geography,
    )
    _require_governed_relation(
        period_relation_dataset,
        dataset_id=PERIOD_RELATION_DATASET_ID,
        period_scheme=period_scheme,
    )
    if geography_dataset.geography != geography:
        raise ValueError("governed geography DatasetRef contradicts target geography")

    gold_result = build_geogcdf_commitment_gold(
        silver,
        geography_relation,
        period_relation,
        geography_units,
        period_scheme=period_scheme,
        source_universe_start_year=source_universe_start_year,
        source_universe_end_year=source_universe_end_year,
        require_complete_resolution=require_complete_resolution,
    )

    gold_ref = _gold_ref(snapshot, geography, period_scheme)
    gold_base = data_root.gold(
        "investments", "aiddata_geogcdf_commitment_area_period", gold_ref.version
    )
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        inputs=(
            silver_dataset,
            geography_dataset,
            geography_relation_dataset,
            period_relation_dataset,
        ),
        outputs=(
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
            "resolution_policy": gold_result.resolution_policy,
            "excluded_unresolved_project_count": gold_result.excluded_unresolved_project_count,
            "reused_governed_relations": True,
            "geography_relation_dataset": geography_relation_dataset.model_dump(mode="json"),
            "period_relation_dataset": period_relation_dataset.model_dump(mode="json"),
            "point_geography_policy": "matched_unique_only",
            "areal_geography_policy": "all_positive_area_overlaps",
            "project_date_type": "commitment",
            "amount_allocation": None,
            "amount_sum_materialized": False,
        },
        code_commit=code_commit,
        qa=gold_result.qa,
        overwrite=overwrite,
    )

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
        resolution_policy=gold_result.resolution_policy,
        unresolved_geography_project_count=gold_result.unresolved_geography_project_count,
        unresolved_commitment_time_project_count=(
            gold_result.unresolved_commitment_time_project_count
        ),
        excluded_unresolved_project_count=gold_result.excluded_unresolved_project_count,
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
    persist_run_artifact(
        data_root,
        run_id,
        "coverage/resolution_exclusions.json",
        _json_text(
            {
                "resolution_policy": gold_result.resolution_policy,
                "unresolved_geography_project_count": (
                    gold_result.unresolved_geography_project_count
                ),
                "unresolved_commitment_time_project_count": (
                    gold_result.unresolved_commitment_time_project_count
                ),
                "excluded_unresolved_project_count": (
                    gold_result.excluded_unresolved_project_count
                ),
                "reused_governed_relations": True,
                "geography_relation_content_sha256": geography_relation_dataset.content_sha256,
                "period_relation_content_sha256": period_relation_dataset.content_sha256,
            }
        ),
        overwrite=overwrite,
    )

    return (
        gold_result,
        manifest,
        measurement,
        hashed_gold,
        gold_base / "commitment_area_period.parquet",
    )


__all__ = ["materialize_geogcdf_gold_from_governed_relations"]
