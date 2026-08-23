from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
from empirical_contracts import (
    AuthorityLevel,
    CoverageContract,
    DatasetRef,
    GeographySpec,
    GrainSpec,
    MeasurementContract,
    PeriodScheme,
    QAResult,
)


@dataclass(frozen=True)
class AcledGoldResult:
    frame: pd.DataFrame
    qa: tuple[QAResult, ...]


def build_acled_gold(
    silver: pd.DataFrame,
    geography_membership: pd.DataFrame,
    period_membership: pd.DataFrame,
) -> AcledGoldResult:
    """Build a sparse native-event-type area-period measurement from uniquely resolved events."""
    silver_required = {"event_row_id", "native_event_type", "fatalities"}
    geography_required = {"event_row_id", "geo_uid", "assignment_status"}
    period_required = {"event_row_id", "period_id", "period_assignment_status"}
    for name, frame, required in (
        ("silver", silver, silver_required),
        ("geography membership", geography_membership, geography_required),
        ("period membership", period_membership, period_required),
    ):
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"ACLED {name} is missing required columns: {', '.join(missing)}")

    resolved_geo = geography_membership.loc[
        geography_membership["assignment_status"].eq("matched_unique")
        & geography_membership["geo_uid"].notna(),
        ["event_row_id", "geo_uid"],
    ].copy()
    if resolved_geo["event_row_id"].duplicated().any():
        raise ValueError("matched_unique geography relation must contain at most one row per event")

    resolved_period = period_membership.loc[
        period_membership["period_assignment_status"].eq("assigned")
        & period_membership["period_id"].notna(),
        ["event_row_id", "period_id"],
    ].copy()
    if resolved_period["event_row_id"].duplicated().any():
        raise ValueError("period membership must contain at most one assigned row per event")

    eligible = (
        silver[["event_row_id", "native_event_type", "fatalities"]]
        .merge(resolved_geo, on="event_row_id", how="inner", validate="one_to_one")
        .merge(resolved_period, on="event_row_id", how="inner", validate="one_to_one")
    )

    rows: list[dict[str, object]] = []
    for keys, group in eligible.groupby(
        ["geo_uid", "period_id", "native_event_type"], dropna=False, sort=True
    ):
        geo_uid, period_id, native_event_type = keys
        fatalities = pd.to_numeric(group["fatalities"], errors="coerce")
        rows.append(
            {
                "geo_uid": geo_uid,
                "period_id": period_id,
                "native_event_type": native_event_type,
                "event_count": len(group),
                "fatal_event_count": int((fatalities > 0).fillna(False).sum()),
                "fatalities": fatalities.sum(min_count=1),
                "fatalities_known_event_count": int(fatalities.notna().sum()),
                "fatalities_missing_event_count": int(fatalities.isna().sum()),
                "record_present": True,
            }
        )

    columns = [
        "geo_uid",
        "period_id",
        "native_event_type",
        "event_count",
        "fatal_event_count",
        "fatalities",
        "fatalities_known_event_count",
        "fatalities_missing_event_count",
        "record_present",
    ]
    gold = pd.DataFrame(rows, columns=columns)

    contributing_events = int(eligible["event_row_id"].nunique())
    total_events = len(silver)
    known_silver_fatalities = pd.to_numeric(silver["fatalities"], errors="coerce")
    known_eligible_fatalities = pd.to_numeric(eligible["fatalities"], errors="coerce")
    total_known_fatalities = (
        float(known_silver_fatalities.sum(min_count=1))
        if known_silver_fatalities.notna().any()
        else 0.0
    )
    included_known_fatalities = (
        float(known_eligible_fatalities.sum(min_count=1))
        if known_eligible_fatalities.notna().any()
        else 0.0
    )

    ambiguous_events = int(
        geography_membership.loc[
            geography_membership["assignment_status"].eq("ambiguous_multiple"), "event_row_id"
        ].nunique()
    )
    qa = (
        QAResult(
            check_id="acled.gold.membership_policy",
            state="GREEN" if contributing_events <= total_events else "RED",
            message="Gold includes only uniquely assigned geography and valid period memberships",
            metrics={
                "silver_events": total_events,
                "contributing_events": contributing_events,
                "excluded_events": total_events - contributing_events,
                "ambiguous_events_excluded": ambiguous_events,
            },
        ),
        QAResult(
            check_id="acled.gold.fatality_accounting",
            state="GREEN",
            message="known fatalities excluded by resolution remain explicit in QA",
            metrics={
                "silver_known_fatalities": total_known_fatalities,
                "included_known_fatalities": included_known_fatalities,
                "excluded_known_fatalities": total_known_fatalities - included_known_fatalities,
            },
        ),
        QAResult(
            check_id="acled.gold.sparse_semantics",
            state="GREEN",
            message=(
                "Gold contains observed aggregate records only; absent rows are not materialized "
                "as zero"
            ),
            metrics={"gold_rows": len(gold)},
        ),
    )
    return AcledGoldResult(frame=gold, qa=qa)


def build_acled_coverage(
    silver: pd.DataFrame,
    *,
    snapshot_id: str,
    geography: GeographySpec,
) -> CoverageContract:
    """Describe observed support without licensing missing sparse rows as zero."""
    dates = pd.to_datetime(silver["event_date"], errors="coerce").dropna()
    temporal_start: date | None = dates.min().date() if len(dates) else None
    temporal_end: date | None = dates.max().date() if len(dates) else None
    return CoverageContract(
        geography_scope=(
            f"{geography.id}; source event locations after explicit spatial membership; "
            "not a claim of complete geographic reporting"
        ),
        temporal_start=temporal_start,
        temporal_end=temporal_end,
        observation_semantics=(
            "sparse aggregates of supplied ACLED event records with unique geography membership "
            "and valid period assignment"
        ),
        absent_row_semantics="unknown",
        authority=AuthorityLevel.L3_REBUILT,
        basis=(
            f"ACLED snapshot {snapshot_id}; temporal bounds are observed event-date bounds only. "
            "No source-completeness claim has been inferred from the filename or observed bounds."
        ),
    )


def build_acled_measurement_contract(
    *,
    silver_dataset: DatasetRef,
    geography: GeographySpec,
    period_scheme: PeriodScheme,
    coverage: CoverageContract,
) -> MeasurementContract:
    """Declare the native-event-type Gold measurement without treatment semantics."""
    return MeasurementContract(
        measure_id="acled.native_event.area_period",
        description=(
            "ACLED event counts, fatal-event counts, and reported fatalities by analytical "
            "geography, shared period, and native ACLED event type"
        ),
        source_dataset=silver_dataset,
        output_grain=GrainSpec(keys=("geo_uid", "period_id", "native_event_type")),
        unit="events and reported fatalities",
        aggregation="count events; count fatal events; sum known reported fatalities",
        coverage=coverage,
        geography=geography,
        period_scheme=period_scheme,
        parameters={
            "geography_membership_policy": "matched_unique_only",
            "ambiguous_membership_policy": "retain_in_membership_product_exclude_from_gold",
            "zero_fatality_events_retained": True,
            "geo_precision_filter": None,
            "native_taxonomy_preserved": True,
        },
    )
