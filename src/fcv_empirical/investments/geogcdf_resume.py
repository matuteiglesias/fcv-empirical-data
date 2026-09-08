from __future__ import annotations

from pathlib import Path

import pandas as pd
from empirical_contracts import DatasetRef, GeographySpec, MeasurementContract, PeriodScheme, RunManifest
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


def materialize_geogcdf_gold_from_governed_relations(
    *,
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
    run must reuse them rather than republish identical stable paths or recompute an expensive
    spatial relation. The existing relation products remain first-class lineage inputs.
    """
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

    gold_ref = _gold_ref(
        silver_dataset.model_copy(update={"version": silver_dataset.version}).model_dump()
        if False
        else None,
        geography,
        period_scheme,
    )
