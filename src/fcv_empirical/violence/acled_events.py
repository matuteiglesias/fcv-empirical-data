from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from empirical_contracts import QAResult, SourceSnapshotRef
from spatial_foundation import register_external_snapshot

ACLED_SOURCE = "acled"
ACLED_ORIGIN = "https://acleddata.com/"
RAW_PREFIX = "source__"

FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "source_event_id": ("EVENT_ID_CNTY", "event_id_cnty", "EVENT_ID", "event_id"),
    "event_date": ("EVENT_DATE", "event_date"),
    "country": ("COUNTRY", "country"),
    "country_iso": ("ISO", "iso", "ISO3", "iso3"),
    "latitude": ("LATITUDE", "latitude"),
    "longitude": ("LONGITUDE", "longitude"),
    "fatalities": ("FATALITIES", "fatalities"),
    "native_event_type": ("EVENT_TYPE", "event_type"),
    "native_sub_event_type": ("SUB_EVENT_TYPE", "sub_event_type"),
    "geo_precision": ("GEO_PRECISION", "geo_precision"),
    "time_precision": ("TIME_PRECISION", "time_precision"),
}

REQUIRED_FIELDS = (
    "source_event_id",
    "event_date",
    "latitude",
    "longitude",
    "fatalities",
    "native_event_type",
)


@dataclass(frozen=True)
class AcledSilverResult:
    frame: pd.DataFrame
    qa: tuple[QAResult, ...]
    source_columns: dict[str, str | None]
    event_type_profile: pd.DataFrame
    geo_precision_profile: pd.DataFrame
    schema_sha256: str


def register_acled_snapshot(
    source_path: str | Path,
    *,
    release: str,
    origin: str = ACLED_ORIGIN,
) -> SourceSnapshotRef:
    """Register one existing ACLED export as an external immutable source snapshot."""
    snapshot = register_external_snapshot(ACLED_SOURCE, release, [source_path])
    return snapshot.model_copy(update={"origin": origin})


def _resolve_source_columns(columns: list[str]) -> dict[str, str | None]:
    if len(set(columns)) != len(columns):
        raise ValueError("ACLED input has duplicate column labels; lossless mapping is ambiguous")

    exact = set(columns)
    folded: dict[str, str] = {}
    for column in columns:
        key = column.casefold()
        if key in folded and folded[key] != column:
            raise ValueError(
                "ACLED input has case-insensitive duplicate column labels; "
                "lossless mapping is ambiguous"
            )
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

    missing = [field for field in REQUIRED_FIELDS if resolved[field] is None]
    if missing:
        raise ValueError(f"ACLED input is missing required source fields: {', '.join(missing)}")
    return resolved


def _raw_present(series: pd.Series) -> pd.Series:
    return series.notna() & series.astype("string").str.strip().fillna("").ne("")


def _profile(series: pd.Series, name: str) -> pd.DataFrame:
    values = series.astype("string").fillna("<missing>")
    counts = values.value_counts(dropna=False).rename_axis(name).reset_index(name="row_count")
    counts["row_share"] = counts["row_count"] / max(len(values), 1)
    return counts


def normalize_acled_events(
    raw: pd.DataFrame,
    *,
    snapshot: SourceSnapshotRef,
) -> AcledSilverResult:
    """Create a lossless ACLED Silver event table without filtering source events."""
    source_columns = _resolve_source_columns([str(column) for column in raw.columns])
    frame = pd.DataFrame(index=raw.index)
    frame["event_row_id"] = [
        f"{snapshot.snapshot_id}:{position:09d}" for position in range(len(raw))
    ]
    frame["source_family"] = ACLED_SOURCE
    frame["source_release"] = snapshot.release
    frame["source_snapshot_id"] = snapshot.snapshot_id

    event_id_source = raw[source_columns["source_event_id"]]
    frame["source_event_id"] = event_id_source.astype("string").str.strip().replace("", pd.NA)

    date_source = raw[source_columns["event_date"]]
    frame["event_date"] = pd.to_datetime(date_source, errors="coerce")

    for canonical in ("country", "country_iso", "native_event_type", "native_sub_event_type"):
        source = source_columns[canonical]
        if source is None:
            frame[canonical] = pd.Series(pd.NA, index=raw.index, dtype="string")
        else:
            frame[canonical] = raw[source].astype("string").str.strip().replace("", pd.NA)

    latitude_source = raw[source_columns["latitude"]]
    longitude_source = raw[source_columns["longitude"]]
    fatalities_source = raw[source_columns["fatalities"]]
    frame["latitude"] = pd.to_numeric(latitude_source, errors="coerce")
    frame["longitude"] = pd.to_numeric(longitude_source, errors="coerce")
    frame["fatalities"] = pd.to_numeric(fatalities_source, errors="coerce")

    for canonical in ("geo_precision", "time_precision"):
        source = source_columns[canonical]
        if source is None:
            frame[canonical] = pd.Series(pd.NA, index=raw.index, dtype="Float64")
        else:
            frame[canonical] = pd.to_numeric(raw[source], errors="coerce").astype("Float64")

    raw_columns = raw.copy()
    raw_columns.columns = [f"{RAW_PREFIX}{column}" for column in raw.columns]
    frame = pd.concat([frame.reset_index(drop=True), raw_columns.reset_index(drop=True)], axis=1)

    missing_ids = int(frame["source_event_id"].isna().sum())
    present_ids = frame.loc[frame["source_event_id"].notna(), "source_event_id"]
    duplicate_id_rows = int(present_ids.duplicated(keep=False).sum())

    missing_coordinates = int(
        frame["latitude"].isna().sum() + frame["longitude"].isna().sum()
    )
    invalid_coordinate_mask = (
        frame["latitude"].notna()
        & frame["longitude"].notna()
        & (
            ~frame["latitude"].between(-90, 90, inclusive="both")
            | ~frame["longitude"].between(-180, 180, inclusive="both")
        )
    )
    invalid_coordinates = int(invalid_coordinate_mask.sum())

    date_present = _raw_present(date_source)
    invalid_dates = int((date_present & frame["event_date"].isna()).sum())
    missing_dates = int((~date_present).sum())

    fatalities_present = _raw_present(fatalities_source)
    fatalities_invalid = int((fatalities_present & frame["fatalities"].isna()).sum())
    fatalities_missing = int((~fatalities_present).sum())
    negative_fatalities = int((frame["fatalities"] < 0).fillna(False).sum())
    raw_numeric_fatalities = pd.to_numeric(fatalities_source, errors="coerce")
    raw_fatality_sum = (
        float(raw_numeric_fatalities.sum(min_count=1))
        if raw_numeric_fatalities.notna().any()
        else 0.0
    )
    normalized_fatality_sum = (
        float(frame["fatalities"].sum(min_count=1))
        if frame["fatalities"].notna().any()
        else 0.0
    )

    schema_payload = json.dumps([str(column) for column in raw.columns], ensure_ascii=False)
    schema_sha256 = hashlib.sha256(schema_payload.encode("utf-8")).hexdigest()

    qa = (
        QAResult(
            check_id="acled.silver.row_retention",
            state="GREEN" if len(frame) == len(raw) else "RED",
            message="Silver retains every supplied ACLED source row",
            metrics={"input_rows": len(raw), "output_rows": len(frame)},
        ),
        QAResult(
            check_id="acled.silver.source_event_id",
            state="GREEN" if missing_ids == 0 and duplicate_id_rows == 0 else "YELLOW",
            message="source event identifiers are preserved; anomalies remain visible",
            metrics={
                "missing_source_event_id": missing_ids,
                "duplicate_source_event_id_rows": duplicate_id_rows,
            },
        ),
        QAResult(
            check_id="acled.silver.coordinates",
            state="GREEN" if missing_coordinates == 0 and invalid_coordinates == 0 else "YELLOW",
            message="coordinate availability/validity is profiled without dropping events",
            metrics={
                "missing_coordinate_values": missing_coordinates,
                "invalid_coordinate_rows": invalid_coordinates,
            },
        ),
        QAResult(
            check_id="acled.silver.event_dates",
            state="GREEN" if missing_dates == 0 and invalid_dates == 0 else "YELLOW",
            message="event-date parse failures remain visible",
            metrics={"missing_event_dates": missing_dates, "invalid_event_dates": invalid_dates},
        ),
        QAResult(
            check_id="acled.silver.fatalities",
            state=(
                "GREEN"
                if fatalities_missing == 0
                and fatalities_invalid == 0
                and negative_fatalities == 0
                else "RED"
            ),
            message="fatalities are normalized without coercing malformed or missing values to zero",
            metrics={
                "missing_fatalities": fatalities_missing,
                "invalid_fatalities": fatalities_invalid,
                "negative_fatalities": negative_fatalities,
                "raw_numeric_fatalities_sum": raw_fatality_sum,
                "normalized_fatalities_sum": normalized_fatality_sum,
            },
        ),
        QAResult(
            check_id="acled.silver.source_schema",
            state="GREEN",
            message="source column schema fingerprint and mapping were recorded",
            metrics={"source_columns": len(raw.columns), "schema_sha256": schema_sha256},
        ),
    )

    return AcledSilverResult(
        frame=frame,
        qa=qa,
        source_columns=source_columns,
        event_type_profile=_profile(frame["native_event_type"], "native_event_type"),
        geo_precision_profile=_profile(frame["geo_precision"], "geo_precision"),
        schema_sha256=schema_sha256,
    )
