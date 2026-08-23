from __future__ import annotations

import hashlib
import json
import numbers
from collections.abc import Collection, Iterable, Sequence
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from empirical_contracts import QAResult, SourceSnapshotRef
from spatial_foundation import register_external_snapshot

from .catalog import SurveyCatalogEntry
from .dhs_gps_models import (
    DHS_GPS_SOURCE,
    DHS_ORIGIN,
    RAW_PREFIX,
    DhsDisplacementPolicy,
    DhsGpsSilverResult,
)

FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "dhsid": ("DHSID", "dhsid"),
    "cluster_id": ("DHSCLUST", "dhsclust", "CLUSTER", "cluster"),
    "reported_latitude": ("LATNUM", "latnum", "LATITUDE", "latitude"),
    "reported_longitude": ("LONGNUM", "longnum", "LONGITUDE", "longitude"),
    "urban_rural": ("URBAN_RURA", "urban_rura", "URBAN_RURAL", "urban_rural"),
    "source_survey_id": ("SURVEYID", "surveyid", "SURVEY_ID", "survey_id"),
}


def register_dhs_gps_snapshot(
    source_paths: Sequence[str | Path],
    *,
    release: str,
    origin: str = DHS_ORIGIN,
) -> SourceSnapshotRef:
    """Register externally stored DHS GE/GPS files without copying restricted source data."""
    if not source_paths:
        raise ValueError("at least one DHS GE/GPS source path is required")
    snapshot = register_external_snapshot(DHS_GPS_SOURCE, release, list(source_paths))
    return snapshot.model_copy(update={"origin": origin})


def _resolve_source_columns(columns: Iterable[str]) -> dict[str, str | None]:
    names = [str(column) for column in columns]
    if len(set(names)) != len(names):
        raise ValueError("DHS GPS input has duplicate column labels")

    exact = set(names)
    folded: dict[str, str] = {}
    for column in names:
        key = column.casefold()
        if key in folded and folded[key] != column:
            raise ValueError("DHS GPS input has case-insensitive duplicate column labels")
        folded[key] = column

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

    missing_coordinates = [
        field
        for field in ("reported_latitude", "reported_longitude")
        if resolved[field] is None
    ]
    if missing_coordinates:
        raise ValueError(
            "DHS GPS input is missing coordinate fields: " + ", ".join(missing_coordinates)
        )
    if resolved["cluster_id"] is None and resolved["dhsid"] is None:
        raise ValueError("DHS GPS input requires DHSCLUST/cluster or DHSID identity")
    return resolved


def _string_series(raw: pd.DataFrame, source: str | None) -> pd.Series:
    if source is None:
        return pd.Series(pd.NA, index=raw.index, dtype="string")
    return raw[source].astype("string").str.strip().replace("", pd.NA)


def normalize_cluster_identity(value: Any) -> Any:
    if pd.isna(value):
        return pd.NA
    if isinstance(value, numbers.Integral) and not isinstance(value, bool):
        return str(int(value))
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        numeric = float(value)
        if numeric.is_integer():
            return str(int(numeric))
    text = str(value).strip()
    return text if text else pd.NA


def _identity_series(raw: pd.DataFrame, source: str | None) -> pd.Series:
    if source is None:
        return pd.Series(pd.NA, index=raw.index, dtype="string")
    return raw[source].map(normalize_cluster_identity).astype("string")


def _raw_source_columns(raw: pd.DataFrame) -> pd.DataFrame:
    source = raw.copy()
    if isinstance(source, gpd.GeoDataFrame):
        geometry_name = source.geometry.name
        geometry_wkt = source.geometry.to_wkt()
        source = pd.DataFrame(source.drop(columns=[geometry_name]))
        source[geometry_name] = geometry_wkt
    source.columns = [f"{RAW_PREFIX}{column}" for column in source.columns]
    return source.reset_index(drop=True)


def normalize_dhs_gps_clusters(
    raw: pd.DataFrame,
    *,
    survey: SurveyCatalogEntry,
    snapshot: SourceSnapshotRef,
    displacement_policy: DhsDisplacementPolicy,
    placeholder_coordinates: Collection[tuple[float, float]] = (),
) -> DhsGpsSilverResult:
    """Build one-row-per-source-cluster Silver while keeping reported-location uncertainty."""
    if survey.source_family.casefold() != "dhs":
        raise ValueError("survey source_family must be DHS")
    if snapshot.source != DHS_GPS_SOURCE:
        raise ValueError(
            f"snapshot source {snapshot.source!r} does not match {DHS_GPS_SOURCE!r}"
        )

    source_columns = _resolve_source_columns(raw.columns)
    frame = pd.DataFrame(index=raw.index)
    frame["cluster_row_id"] = [
        f"{snapshot.snapshot_id}:{position:09d}" for position in range(len(raw))
    ]
    frame["survey_id"] = survey.survey_id
    frame["source_family"] = "dhs"
    frame["source_release"] = snapshot.release
    frame["source_snapshot_id"] = snapshot.snapshot_id

    dhsid = _string_series(raw, source_columns["dhsid"])
    source_cluster = _identity_series(raw, source_columns["cluster_id"])
    frame["dhsid"] = dhsid
    frame["cluster_id"] = source_cluster.where(source_cluster.notna(), dhsid)
    frame["source_survey_id"] = _string_series(raw, source_columns["source_survey_id"])
    frame["urban_rural"] = _string_series(raw, source_columns["urban_rural"])

    latitude_source = raw[source_columns["reported_latitude"]]
    longitude_source = raw[source_columns["reported_longitude"]]
    latitude = pd.to_numeric(latitude_source, errors="coerce")
    longitude = pd.to_numeric(longitude_source, errors="coerce")
    frame["reported_latitude"] = latitude
    frame["reported_longitude"] = longitude

    missing_mask = latitude.isna() | longitude.isna()
    invalid_mask = (
        ~missing_mask
        & (
            ~latitude.between(-90, 90, inclusive="both")
            | ~longitude.between(-180, 180, inclusive="both")
        )
    )
    placeholder_set = {(float(lat), float(lon)) for lat, lon in placeholder_coordinates}
    placeholder_mask = pd.Series(False, index=raw.index)
    if placeholder_set:
        pairs = pd.Series(list(zip(latitude, longitude, strict=False)), index=raw.index)
        placeholder_mask = ~missing_mask & pairs.isin(placeholder_set)
    validity = pd.Series("valid", index=raw.index, dtype="string")
    validity.loc[missing_mask] = "missing"
    validity.loc[invalid_mask] = "invalid"
    validity.loc[placeholder_mask & ~invalid_mask] = "source_placeholder"
    frame["coordinate_validity"] = validity

    policy_metadata = displacement_policy.as_metadata()
    for key, value in policy_metadata.items():
        frame[key] = value

    frame = pd.concat([frame.reset_index(drop=True), _raw_source_columns(raw)], axis=1)

    present_ids = frame.loc[frame["cluster_id"].notna(), "cluster_id"]
    missing_cluster_ids = int(frame["cluster_id"].isna().sum())
    duplicate_cluster_rows = int(present_ids.duplicated(keep=False).sum())
    zero_coordinate_rows = int(((latitude == 0) & (longitude == 0)).fillna(False).sum())
    missing_coordinate_rows = int(missing_mask.sum())
    invalid_coordinate_rows = int(invalid_mask.sum())
    placeholder_coordinate_rows = int(placeholder_mask.sum())

    schema_payload = json.dumps([str(column) for column in raw.columns], ensure_ascii=False)
    schema_sha256 = hashlib.sha256(schema_payload.encode("utf-8")).hexdigest()

    qa = (
        QAResult(
            check_id="dhs.gps.silver.row_retention",
            state="GREEN" if len(frame) == len(raw) else "RED",
            message="Silver retains every supplied DHS GPS cluster row",
            metrics={"cluster_rows": len(frame), "input_rows": len(raw)},
        ),
        QAResult(
            check_id="dhs.gps.silver.cluster_identity",
            state=(
                "GREEN"
                if missing_cluster_ids == 0 and duplicate_cluster_rows == 0
                else "YELLOW"
            ),
            message="cluster identity anomalies remain visible and are never deduplicated",
            metrics={
                "cluster_rows": len(frame),
                "unique_cluster_ids": int(present_ids.nunique()),
                "missing_cluster_ids": missing_cluster_ids,
                "duplicate_cluster_rows": duplicate_cluster_rows,
            },
        ),
        QAResult(
            check_id="dhs.gps.silver.coordinates",
            state=(
                "GREEN"
                if missing_coordinate_rows == 0
                and invalid_coordinate_rows == 0
                and placeholder_coordinate_rows == 0
                else "YELLOW"
            ),
            message=(
                "reported coordinates are profiled without treating zero as a placeholder unless "
                "the caller supplies that source convention"
            ),
            metrics={
                "missing_coordinate_rows": missing_coordinate_rows,
                "invalid_coordinate_rows": invalid_coordinate_rows,
                "zero_coordinate_rows": zero_coordinate_rows,
                "source_placeholder_coordinate_rows": placeholder_coordinate_rows,
            },
        ),
        QAResult(
            check_id="dhs.gps.silver.displacement_semantics",
            state="GREEN",
            message="reported-coordinate displacement metadata is explicit and release supplied",
            metrics={**policy_metadata},
        ),
        QAResult(
            check_id="dhs.gps.silver.source_schema",
            state="GREEN",
            message="source schema fingerprint and column mapping were recorded",
            metrics={"source_columns": len(raw.columns), "schema_sha256": schema_sha256},
        ),
    )
    return DhsGpsSilverResult(
        frame=frame,
        qa=qa,
        source_columns=source_columns,
        schema_sha256=schema_sha256,
    )
