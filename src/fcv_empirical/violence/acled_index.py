from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
from empirical_contracts import GeographySpec, PeriodScheme, QAResult
from spatial_foundation import PeriodIndex
from spatial_foundation.geography import assign_points


@dataclass(frozen=True)
class AcledGeographyResult:
    frame: pd.DataFrame
    qa: tuple[QAResult, ...]


@dataclass(frozen=True)
class AcledPeriodResult:
    frame: pd.DataFrame
    qa: tuple[QAResult, ...]


def assign_acled_geography(
    silver: pd.DataFrame,
    polygons: gpd.GeoDataFrame,
    *,
    geography: GeographySpec,
    polygon_id_col: str = "geo_uid",
) -> AcledGeographyResult:
    """Attach candidate geography memberships while preserving ambiguity and bad coordinates."""
    required = {"event_row_id", "source_event_id", "latitude", "longitude"}
    missing = sorted(required - set(silver.columns))
    if missing:
        raise ValueError(f"ACLED Silver is missing required columns: {', '.join(missing)}")

    latitude = pd.to_numeric(silver["latitude"], errors="coerce")
    longitude = pd.to_numeric(silver["longitude"], errors="coerce")
    missing_mask = latitude.isna() | longitude.isna()
    invalid_mask = (
        ~missing_mask
        & (~latitude.between(-90, 90, inclusive="both") | ~longitude.between(-180, 180, inclusive="both"))
    )
    valid_mask = ~(missing_mask | invalid_mask)

    valid_rows = silver.loc[valid_mask, ["event_row_id", "source_event_id"]].copy()
    points = gpd.GeoDataFrame(
        valid_rows,
        geometry=gpd.points_from_xy(longitude.loc[valid_mask], latitude.loc[valid_mask]),
        crs="EPSG:4326",
    )

    pieces: list[pd.DataFrame] = []
    if len(points):
        candidates, _audit = assign_points(
            points,
            polygons,
            point_id_col="event_row_id",
            polygon_id_col=polygon_id_col,
        )
        candidates = candidates.rename(columns={polygon_id_col: "geo_uid"})
        candidates = candidates.merge(
            silver[["event_row_id", "source_event_id"]],
            on="event_row_id",
            how="left",
            validate="many_to_one",
        )
        pieces.append(candidates)

    for mask, status in ((missing_mask, "missing_coordinates"), (invalid_mask, "invalid_coordinates")):
        if mask.any():
            rows = silver.loc[mask, ["event_row_id", "source_event_id"]].copy()
            rows["geo_uid"] = pd.NA
            rows["candidate_count"] = 0
            rows["assignment_status"] = status
            pieces.append(rows)

    columns = [
        "event_row_id",
        "source_event_id",
        "geo_uid",
        "candidate_count",
        "assignment_status",
    ]
    if pieces:
        result = pd.concat(pieces, ignore_index=True)[columns]
    else:
        result = pd.DataFrame(columns=columns)
    result["geography_id"] = geography.id

    status_by_event = result.drop_duplicates("event_row_id")[["event_row_id", "assignment_status"]]
    counts = status_by_event["assignment_status"].value_counts()
    uniquely_assigned = int(counts.get("matched_unique", 0))
    ambiguous = int(counts.get("ambiguous_multiple", 0))
    outside = int(counts.get("unmatched_outside", 0))
    missing_coordinates = int(counts.get("missing_coordinates", 0))
    invalid_coordinates = int(counts.get("invalid_coordinates", 0))

    qa = (
        QAResult(
            check_id="acled.geography.assignment",
            state="GREEN" if len(status_by_event) == len(silver) else "RED",
            message="every Silver event receives an explicit geography-assignment status",
            metrics={
                "input_events": len(silver),
                "status_events": len(status_by_event),
                "matched_unique": uniquely_assigned,
                "ambiguous_multiple": ambiguous,
                "unmatched_outside": outside,
                "missing_coordinates": missing_coordinates,
                "invalid_coordinates": invalid_coordinates,
            },
        ),
        QAResult(
            check_id="acled.geography.ambiguity",
            state="GREEN" if ambiguous == 0 else "YELLOW",
            message="ambiguous memberships remain candidate rows and are not tie-broken",
            metrics={"ambiguous_events": ambiguous},
        ),
    )
    return AcledGeographyResult(frame=result, qa=qa)


def assign_acled_periods(
    silver: pd.DataFrame,
    *,
    scheme: PeriodScheme,
) -> AcledPeriodResult:
    """Attach shared PeriodIndex membership without reimplementing legacy T/y0 formulas."""
    required = {"event_row_id", "source_event_id", "event_date"}
    missing = sorted(required - set(silver.columns))
    if missing:
        raise ValueError(f"ACLED Silver is missing required columns: {', '.join(missing)}")

    index = PeriodIndex(scheme)
    rows: list[dict[str, object]] = []
    for record in silver[["event_row_id", "source_event_id", "event_date"]].itertuples(index=False):
        event_date = record.event_date
        if pd.isna(event_date):
            rows.append(
                {
                    "event_row_id": record.event_row_id,
                    "source_event_id": record.source_event_id,
                    "period_id": pd.NA,
                    "period_start_year": pd.NA,
                    "period_end_year": pd.NA,
                    "period_ordinal": pd.NA,
                    "period_assignment_status": "missing_or_invalid_date",
                }
            )
            continue
        timestamp = pd.Timestamp(event_date)
        period = index.period_for(timestamp.to_pydatetime())
        rows.append(
            {
                "event_row_id": record.event_row_id,
                "source_event_id": record.source_event_id,
                "period_id": period.period_id,
                "period_start_year": period.start_year,
                "period_end_year": period.end_year,
                "period_ordinal": period.ordinal,
                "period_assignment_status": "assigned",
            }
        )

    result = pd.DataFrame(rows)
    assigned = int((result["period_assignment_status"] == "assigned").sum()) if len(result) else 0
    missing_dates = len(result) - assigned
    qa = (
        QAResult(
            check_id="acled.period.assignment",
            state="GREEN" if missing_dates == 0 else "YELLOW",
            message="period membership is delegated to spatial-data-foundation PeriodIndex",
            metrics={
                "input_events": len(silver),
                "assigned_events": assigned,
                "unassigned_events": missing_dates,
                "period_scheme": scheme.id,
            },
        ),
    )
    return AcledPeriodResult(frame=result, qa=qa)
