from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
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
    RunManifest,
    SourceSnapshotRef,
)
from spatial_foundation import DataRoot, sha256_file

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact

from .catalog import SurveyCatalogEntry
from .dhs_gps import (
    DhsDisplacementPolicy,
    DhsGpsSilverResult,
    DhsReportedMembershipResult,
    assign_dhs_reported_coordinate_membership,
    normalize_dhs_gps_clusters,
    register_dhs_gps_snapshot,
)


def _json_text(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _validate_snapshot_files(
    snapshot: SourceSnapshotRef,
    source_paths: Sequence[str | Path],
) -> None:
    expected = {
        str(Path(ref.path).expanduser().resolve()): ref.sha256 for ref in snapshot.files
    }
    supplied = [str(Path(path).expanduser().resolve()) for path in source_paths]
    if len(set(supplied)) != len(supplied):
        raise ValueError("DHS GPS source_paths contain duplicates")
    if set(supplied) != set(expected):
        raise ValueError("DHS GPS source_paths must match SourceSnapshotRef files exactly")
    for path in supplied:
        if sha256_file(Path(path)) != expected[path]:
            raise ValueError(f"DHS GPS source file changed after snapshot registration: {path}")


def _silver_dataset_ref(snapshot: SourceSnapshotRef) -> DatasetRef:
    return DatasetRef(
        dataset_id="surveys.dhs.gps_clusters",
        version=snapshot.snapshot_id,
        schema_version="dhs-gps-cluster-silver-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("cluster_row_id",)),
    )


def _geography_token(geography: GeographySpec) -> str:
    digest = hashlib.sha256(geography.id.encode("utf-8")).hexdigest()[:12]
    return f"geo-{digest}"


def _membership_dataset_ref(
    snapshot: SourceSnapshotRef,
    geography: GeographySpec,
) -> DatasetRef:
    return DatasetRef(
        dataset_id="surveys.dhs.reported_coordinate_geography",
        version=f"{snapshot.snapshot_id}--{_geography_token(geography)}",
        schema_version="dhs-reported-coordinate-geography-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("membership_row_id",)),
        geography=geography,
    )


def _hashed_output(manifest: RunManifest, dataset_id: str) -> DatasetRef:
    matches = [dataset for dataset in manifest.outputs if dataset.dataset_id == dataset_id]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one materialized dataset {dataset_id!r}")
    return matches[0]


def materialize_dhs_gps_silver(
    *,
    source_path: str | Path,
    source_paths: Sequence[str | Path],
    survey: SurveyCatalogEntry,
    release: str,
    displacement_policy: DhsDisplacementPolicy,
    data_root: DataRoot,
    run_id: str,
    source_snapshot: SourceSnapshotRef | None = None,
    placeholder_coordinates: Sequence[tuple[float, float]] = (),
    code_commit: str | None = None,
    overwrite: bool = False,
) -> tuple[SourceSnapshotRef, DhsGpsSilverResult, RunManifest, DatasetRef, Path]:
    """Read one authoritative DHS GE/GPS source and publish cluster-grain Silver."""
    snapshot = source_snapshot or register_dhs_gps_snapshot(source_paths, release=release)
    if snapshot.release != release:
        raise ValueError("supplied DHS GPS snapshot release does not match requested release")
    _validate_snapshot_files(snapshot, source_paths)

    resolved_source_path = Path(source_path).expanduser().resolve()
    registered = {Path(ref.path).expanduser().resolve() for ref in snapshot.files}
    if resolved_source_path not in registered:
        raise ValueError("DHS GPS source_path must be one of the registered snapshot files")

    raw = gpd.read_file(resolved_source_path)
    silver = normalize_dhs_gps_clusters(
        raw,
        survey=survey,
        snapshot=snapshot,
        displacement_policy=displacement_policy,
        placeholder_coordinates=placeholder_coordinates,
    )
    dataset = _silver_dataset_ref(snapshot)
    destination = data_root.silver("surveys", "dhs_gps_clusters", dataset.version)
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
            "source_release": snapshot.release,
            "source_schema_sha256": silver.schema_sha256,
            "coordinate_semantics": "reported_dhs_coordinate",
            "displacement_policy": displacement_policy.as_metadata(),
            "de_displacement": False,
        },
        code_commit=code_commit,
        qa=silver.qa,
        overwrite=overwrite,
    )
    hashed = _hashed_output(manifest, dataset.dataset_id)
    persist_run_artifact(
        data_root,
        run_id,
        "mappings/dhs_gps_source_columns.json",
        _json_text(silver.source_columns),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "metadata/dhs_displacement_policy.json",
        _json_text(displacement_policy.as_metadata()),
        overwrite=overwrite,
    )
    return snapshot, silver, manifest, hashed, destination / "clusters.parquet"


def materialize_dhs_reported_coordinate_membership(
    *,
    snapshot: SourceSnapshotRef,
    silver: pd.DataFrame,
    silver_dataset: DatasetRef,
    polygons: gpd.GeoDataFrame,
    geography: GeographySpec,
    geography_dataset: DatasetRef,
    data_root: DataRoot,
    run_id: str,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> tuple[DhsReportedMembershipResult, RunManifest, DatasetRef, Path]:
    """Publish reported-coordinate membership with Silver + analytical-geography lineage."""
    if silver_dataset.dataset_id != "surveys.dhs.gps_clusters":
        raise ValueError("silver_dataset must identify DHS GPS cluster Silver")
    if silver_dataset.version != snapshot.snapshot_id:
        raise ValueError("silver_dataset version must match the DHS GPS source snapshot")
    if geography_dataset.geography != geography:
        raise ValueError("geography_dataset must carry the requested GeographySpec")

    result = assign_dhs_reported_coordinate_membership(
        silver,
        polygons,
        geography=geography,
    )
    dataset = _membership_dataset_ref(snapshot, geography)
    destination = data_root.silver(
        "surveys",
        "dhs_reported_coordinate_geography",
        dataset.version,
    )
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        inputs=(silver_dataset, geography_dataset),
        outputs=(
            FileMaterialization(
                dataset=dataset,
                relative_path="membership.parquet",
                destination_base=destination,
                writer=lambda path: result.frame.to_parquet(path, index=False),
            ),
        ),
        parameters={
            "survey_source_snapshot_id": snapshot.snapshot_id,
            "geography_id": geography.id,
            "membership_semantics": "reported_coordinate_membership",
            "true_cluster_location_claim": False,
            "de_displacement": False,
            "candidate_displacement_enumeration": False,
        },
        code_commit=code_commit,
        qa=result.qa,
        overwrite=overwrite,
    )
    hashed = _hashed_output(manifest, dataset.dataset_id)
    return result, manifest, hashed, destination / "membership.parquet"
