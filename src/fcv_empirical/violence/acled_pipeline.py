from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
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
from spatial_foundation import DataRoot, sha256_file

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact

from .acled_events import AcledSilverResult, normalize_acled_events, register_acled_snapshot
from .acled_index import (
    AcledGeographyResult,
    AcledPeriodResult,
    assign_acled_geography,
    assign_acled_periods,
)
from .acled_measurements import (
    AcledGoldResult,
    build_acled_coverage,
    build_acled_gold,
    build_acled_measurement_contract,
)
from .acled_parity import compare_acled_legacy


@dataclass(frozen=True)
class AcledVerticalResult:
    snapshot: SourceSnapshotRef
    silver_manifest: RunManifest
    measurement_manifest: RunManifest
    silver_dataset: DatasetRef
    geography_membership_dataset: DatasetRef
    period_membership_dataset: DatasetRef
    gold_dataset: DatasetRef
    measurement_contract: MeasurementContract
    parity: dict[str, Any]
    paths: dict[str, Path]


def _validate_snapshot_source(snapshot: SourceSnapshotRef, source_path: str | Path) -> None:
    resolved = Path(source_path).expanduser().resolve()
    matches = [ref for ref in snapshot.files if Path(ref.path).expanduser().resolve() == resolved]
    if len(matches) != 1:
        raise ValueError("ACLED source_path must be represented exactly once in SourceSnapshotRef")
    current = sha256_file(resolved)
    if current != matches[0].sha256:
        raise ValueError("ACLED source file changed after snapshot registration")


def _json_text(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _silver_dataset_ref(snapshot: SourceSnapshotRef) -> DatasetRef:
    return DatasetRef(
        dataset_id="violence.acled.events",
        version=snapshot.snapshot_id,
        schema_version="acled-event-silver-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("event_row_id",)),
    )


def _geography_version_token(geography: GeographySpec) -> str:
    digest = hashlib.sha256(geography.id.encode("utf-8")).hexdigest()[:12]
    return f"geo-{digest}"


def _membership_version(snapshot: SourceSnapshotRef, geography: GeographySpec) -> str:
    return f"{snapshot.snapshot_id}--{_geography_version_token(geography)}"


def _measurement_version(
    snapshot: SourceSnapshotRef,
    geography: GeographySpec,
    period_scheme: PeriodScheme,
) -> str:
    return f"{snapshot.snapshot_id}--{_geography_version_token(geography)}--{period_scheme.id}"


def _geography_membership_ref(
    snapshot: SourceSnapshotRef,
    geography: GeographySpec,
) -> DatasetRef:
    return DatasetRef(
        dataset_id="violence.acled.event_geography",
        version=_membership_version(snapshot, geography),
        schema_version="acled-event-geography-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("event_row_id", "geo_uid")),
        geography=geography,
    )


def _period_membership_ref(
    snapshot: SourceSnapshotRef,
    period_scheme: PeriodScheme,
) -> DatasetRef:
    return DatasetRef(
        dataset_id="violence.acled.event_period",
        version=f"{snapshot.snapshot_id}--{period_scheme.id}",
        schema_version="acled-event-period-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("event_row_id",)),
        period_scheme=period_scheme,
    )


def _gold_ref(
    snapshot: SourceSnapshotRef,
    geography: GeographySpec,
    period_scheme: PeriodScheme,
) -> DatasetRef:
    return DatasetRef(
        dataset_id="violence.acled.area_period_native_event",
        version=_measurement_version(snapshot, geography, period_scheme),
        schema_version="acled-native-event-gold-v1",
        layer=DataLayer.GOLD,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("geo_uid", "period_id", "native_event_type")),
        geography=geography,
        period_scheme=period_scheme,
    )


def _hashed_output(manifest: RunManifest, dataset_id: str) -> DatasetRef:
    matches = [dataset for dataset in manifest.outputs if dataset.dataset_id == dataset_id]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one materialized dataset {dataset_id!r}")
    return matches[0]


def materialize_acled_silver(
    *,
    source_path: str | Path,
    release: str,
    data_root: DataRoot,
    run_id: str,
    source_snapshot: SourceSnapshotRef | None = None,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> tuple[SourceSnapshotRef, AcledSilverResult, RunManifest, DatasetRef, Path]:
    """Rebuild lossless source-native Silver events from one immutable ACLED export."""
    snapshot = source_snapshot or register_acled_snapshot(source_path, release=release)
    if snapshot.release != release:
        raise ValueError("supplied ACLED snapshot release does not match requested release")
    _validate_snapshot_source(snapshot, source_path)
    raw = pd.read_csv(source_path, dtype=str, low_memory=False)
    silver = normalize_acled_events(raw, snapshot=snapshot)
    dataset = _silver_dataset_ref(snapshot)
    destination = data_root.silver("violence", "acled_events", dataset.version)
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        source_snapshot=snapshot,
        outputs=(
            FileMaterialization(
                dataset=dataset,
                relative_path="events.parquet",
                destination_base=destination,
                writer=lambda path: silver.frame.to_parquet(path, index=False),
            ),
        ),
        parameters={
            "source_release": snapshot.release,
            "source_schema_sha256": silver.schema_sha256,
            "event_filter": None,
            "geo_precision_filter": None,
            "zero_fatality_filter": None,
        },
        code_commit=code_commit,
        qa=silver.qa,
        overwrite=overwrite,
    )
    hashed = _hashed_output(manifest, dataset.dataset_id)
    persist_run_artifact(
        data_root,
        run_id,
        "mappings/acled_source_columns.json",
        _json_text(silver.source_columns),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "profiles/event_type_profile.csv",
        silver.event_type_profile.to_csv(index=False),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "profiles/geo_precision_profile.csv",
        silver.geo_precision_profile.to_csv(index=False),
        overwrite=overwrite,
    )
    return snapshot, silver, manifest, hashed, destination / "events.parquet"


def materialize_acled_measurement(
    *,
    snapshot: SourceSnapshotRef,
    silver: pd.DataFrame,
    silver_dataset: DatasetRef,
    polygons: gpd.GeoDataFrame,
    geography: GeographySpec,
    geography_dataset: DatasetRef,
    period_scheme: PeriodScheme,
    data_root: DataRoot,
    run_id: str,
    legacy: pd.DataFrame | None = None,
    geo_uid_to_legacy_gid: dict[str, str] | None = None,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> tuple[
    AcledGeographyResult,
    AcledPeriodResult,
    AcledGoldResult,
    RunManifest,
    MeasurementContract,
    dict[str, Any],
    dict[str, DatasetRef],
    dict[str, Path],
]:
    """Attach shared geography/time and materialize sparse source-specific Gold."""
    geography_result = assign_acled_geography(silver, polygons, geography=geography)
    period_result = assign_acled_periods(silver, scheme=period_scheme)
    gold_result = build_acled_gold(silver, geography_result.frame, period_result.frame)

    geography_ref = _geography_membership_ref(snapshot, geography)
    period_ref = _period_membership_ref(snapshot, period_scheme)
    gold_ref = _gold_ref(snapshot, geography, period_scheme)
    geography_base = data_root.silver(
        "violence", "acled_event_geography", geography_ref.version
    )
    period_base = data_root.silver("violence", "acled_event_period", period_ref.version)
    gold_base = data_root.gold("violence", "acled_area_period", gold_ref.version)

    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        inputs=(silver_dataset, geography_dataset),
        outputs=(
            FileMaterialization(
                dataset=geography_ref,
                relative_path="event_geography.parquet",
                destination_base=geography_base,
                writer=lambda path: geography_result.frame.to_parquet(path, index=False),
            ),
            FileMaterialization(
                dataset=period_ref,
                relative_path="event_period.parquet",
                destination_base=period_base,
                writer=lambda path: period_result.frame.to_parquet(path, index=False),
            ),
            FileMaterialization(
                dataset=gold_ref,
                relative_path="area_period_native_event.parquet",
                destination_base=gold_base,
                writer=lambda path: gold_result.frame.to_parquet(path, index=False),
            ),
        ),
        parameters={
            "source_snapshot_id": snapshot.snapshot_id,
            "geography": geography.model_dump(mode="json"),
            "period_scheme": period_scheme.model_dump(mode="json"),
            "geography_membership_policy": "matched_unique_only",
            "ambiguous_membership_policy": "retain_candidates_exclude_from_gold",
            "geo_precision_filter": None,
            "zero_fatality_filter": None,
        },
        code_commit=code_commit,
        qa=(*geography_result.qa, *period_result.qa, *gold_result.qa),
        overwrite=overwrite,
    )

    hashed_geography = _hashed_output(manifest, geography_ref.dataset_id)
    hashed_period = _hashed_output(manifest, period_ref.dataset_id)
    hashed_gold = _hashed_output(manifest, gold_ref.dataset_id)
    coverage = build_acled_coverage(
        silver,
        snapshot_id=snapshot.snapshot_id,
        geography=geography,
    )
    measurement = build_acled_measurement_contract(
        silver_dataset=silver_dataset,
        geography=geography,
        period_scheme=period_scheme,
        coverage=coverage,
    )
    parity = compare_acled_legacy(
        silver=silver,
        gold=gold_result.frame,
        legacy=legacy,
        geo_uid_to_legacy_gid=geo_uid_to_legacy_gid,
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
        "parity/acled_legacy_parity.json",
        _json_text(parity),
        overwrite=overwrite,
    )

    datasets = {
        "geography_membership": hashed_geography,
        "period_membership": hashed_period,
        "gold": hashed_gold,
    }
    paths = {
        "geography_membership": geography_base / "event_geography.parquet",
        "period_membership": period_base / "event_period.parquet",
        "gold": gold_base / "area_period_native_event.parquet",
    }
    return (
        geography_result,
        period_result,
        gold_result,
        manifest,
        measurement,
        parity,
        datasets,
        paths,
    )


def materialize_acled_vertical(
    *,
    source_path: str | Path,
    release: str,
    data_root: DataRoot,
    run_id: str,
    polygons: gpd.GeoDataFrame,
    geography: GeographySpec,
    geography_dataset: DatasetRef,
    period_scheme: PeriodScheme,
    legacy_path: str | Path | None = None,
    geo_uid_to_legacy_gid: dict[str, str] | None = None,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> AcledVerticalResult:
    """Execute snapshot→Silver→membership/time→sparse Gold with two explicit lineage runs."""
    snapshot, silver, silver_manifest, silver_ref, silver_path = materialize_acled_silver(
        source_path=source_path,
        release=release,
        data_root=data_root,
        run_id=f"{run_id}-silver",
        code_commit=code_commit,
        overwrite=overwrite,
    )
    legacy = None
    if legacy_path is not None:
        legacy_file = Path(legacy_path)
        if legacy_file.exists():
            legacy = pd.read_csv(legacy_file, low_memory=False)

    (
        _geography_result,
        _period_result,
        _gold_result,
        measurement_manifest,
        measurement,
        parity,
        datasets,
        measurement_paths,
    ) = materialize_acled_measurement(
        snapshot=snapshot,
        silver=silver.frame,
        silver_dataset=silver_ref,
        polygons=polygons,
        geography=geography,
        geography_dataset=geography_dataset,
        period_scheme=period_scheme,
        data_root=data_root,
        run_id=f"{run_id}-measurement",
        legacy=legacy,
        geo_uid_to_legacy_gid=geo_uid_to_legacy_gid,
        code_commit=code_commit,
        overwrite=overwrite,
    )
    return AcledVerticalResult(
        snapshot=snapshot,
        silver_manifest=silver_manifest,
        measurement_manifest=measurement_manifest,
        silver_dataset=silver_ref,
        geography_membership_dataset=datasets["geography_membership"],
        period_membership_dataset=datasets["period_membership"],
        gold_dataset=datasets["gold"],
        measurement_contract=measurement,
        parity=parity,
        paths={"silver": silver_path, **measurement_paths},
    )
