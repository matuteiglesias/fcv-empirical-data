from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta

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
from spatial_foundation import PeriodIndex


@dataclass(frozen=True)
class AcledCoverageCertification:
    """Explicit external certification of complete ACLED reporting support.

    This object is deliberately not inferred from observed event rows, filenames,
    or min/max dates. A caller must provide the exact geography and temporal scope
    plus the evidence basis that justifies treating absent source events as zero.
    """

    certification_id: str
    country_iso3: tuple[str, ...]
    temporal_start: date
    temporal_end: date
    basis: str

    def __post_init__(self) -> None:
        if not self.certification_id.strip():
            raise ValueError("ACLED coverage certification_id must be non-empty")
        if not self.country_iso3:
            raise ValueError("ACLED coverage certification must name at least one country")
        countries = tuple(str(value).strip().upper() for value in self.country_iso3)
        if any(not value for value in countries):
            raise ValueError("ACLED coverage certification contains an empty country token")
        if len(set(countries)) != len(countries):
            raise ValueError("ACLED coverage certification country list contains duplicates")
        if self.temporal_end < self.temporal_start:
            raise ValueError("ACLED coverage certification end date precedes start date")
        if not self.basis.strip():
            raise ValueError("ACLED coverage certification basis must be non-empty")
        object.__setattr__(self, "country_iso3", tuple(sorted(countries)))

    def to_dict(self) -> dict[str, object]:
        return {
            "certification_id": self.certification_id,
            "country_iso3": list(self.country_iso3),
            "temporal_start": self.temporal_start.isoformat(),
            "temporal_end": self.temporal_end.isoformat(),
            "basis": self.basis,
        }

    @property
    def sha256(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AcledCertifiedGoldResult:
    frame: pd.DataFrame
    qa: tuple[QAResult, ...]
    certified_country_iso3: tuple[str, ...]
    full_coverage_period_ids: tuple[str, ...]
    native_event_types: tuple[str, ...]
    certification_sha256: str


def _full_periods_within_certification(
    scheme: PeriodScheme,
    *,
    temporal_start: date,
    temporal_end: date,
):
    index = PeriodIndex(scheme)
    periods = index.range(temporal_start.year, temporal_end.year)
    return tuple(
        period
        for period in periods
        if period.start_date >= temporal_start
        and period.end_date_exclusive - timedelta(days=1) <= temporal_end
    )


def build_acled_certified_gold(
    sparse_gold: pd.DataFrame,
    geography_units: pd.DataFrame,
    *,
    period_scheme: PeriodScheme,
    certification: AcledCoverageCertification,
) -> AcledCertifiedGoldResult:
    """Densify sparse ACLED aggregates only inside explicitly certified support."""

    required_sparse = {
        "geo_uid",
        "period_id",
        "native_event_type",
        "event_count",
        "fatal_event_count",
        "fatalities",
        "fatalities_known_event_count",
        "fatalities_missing_event_count",
        "record_present",
    }
    missing_sparse = sorted(required_sparse - set(sparse_gold.columns))
    if missing_sparse:
        raise ValueError(
            "ACLED sparse Gold is missing required columns: " + ", ".join(missing_sparse)
        )
    if sparse_gold.duplicated(["geo_uid", "period_id", "native_event_type"]).any():
        raise ValueError("ACLED sparse Gold is not unique at geo_uid × period_id × event type")

    required_geo = {"geo_uid", "country_iso3"}
    missing_geo = sorted(required_geo - set(geography_units.columns))
    if missing_geo:
        raise ValueError(
            "ACLED certification geography is missing required columns: " + ", ".join(missing_geo)
        )
    if geography_units["geo_uid"].duplicated().any():
        raise ValueError("ACLED certification geography must be unique by geo_uid")

    target_countries = set(geography_units["country_iso3"].dropna().astype(str).str.upper())
    missing_countries = sorted(set(certification.country_iso3) - target_countries)
    if missing_countries:
        raise ValueError(
            "ACLED certification contains countries absent from target geography: "
            + ", ".join(missing_countries)
        )

    covered_geographies = geography_units.loc[
        geography_units["country_iso3"].astype("string").str.upper().isin(certification.country_iso3),
        ["geo_uid", "country_iso3"],
    ].drop_duplicates()
    if covered_geographies.empty:
        raise ValueError("ACLED certification has no target geographies")

    full_periods = _full_periods_within_certification(
        period_scheme,
        temporal_start=certification.temporal_start,
        temporal_end=certification.temporal_end,
    )
    if not full_periods:
        raise ValueError("ACLED certification contains no full periods for the declared scheme")
    full_period_ids = tuple(period.period_id for period in full_periods)

    covered_geo_ids = set(covered_geographies["geo_uid"].astype(str))
    native_event_types = tuple(
        sorted(
            sparse_gold.loc[
                sparse_gold["geo_uid"].astype(str).isin(covered_geo_ids),
                "native_event_type",
            ]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )
    )
    if not native_event_types:
        raise ValueError("ACLED certification scope contains no native event types")

    sparse_scope = sparse_gold.loc[
        sparse_gold["geo_uid"].astype(str).isin(covered_geo_ids)
        & sparse_gold["period_id"].astype(str).isin(full_period_ids)
        & sparse_gold["native_event_type"].astype(str).isin(native_event_types)
    ].copy()

    universe = (
        covered_geographies.assign(_key=1)
        .merge(pd.DataFrame({"period_id": full_period_ids, "_key": 1}), on="_key")
        .merge(pd.DataFrame({"native_event_type": native_event_types, "_key": 1}), on="_key")
        .drop(columns="_key")
    )
    gold = universe.merge(
        sparse_scope,
        on=["geo_uid", "period_id", "native_event_type"],
        how="left",
        validate="one_to_one",
    )
    present = gold["record_present"].fillna(False).astype(bool)
    count_columns = [
        "event_count",
        "fatal_event_count",
        "fatalities_known_event_count",
        "fatalities_missing_event_count",
    ]
    for column in count_columns:
        gold.loc[~present, column] = 0
        gold[column] = gold[column].astype("int64")
    gold.loc[~present, "fatalities"] = 0.0
    gold["record_present"] = present
    gold["measurement_status"] = present.map(
        {True: "aggregated_from_observed", False: "structural_zero"}
    )
    gold = gold[
        [
            "geo_uid",
            "period_id",
            "country_iso3",
            "native_event_type",
            "event_count",
            "fatal_event_count",
            "fatalities",
            "fatalities_known_event_count",
            "fatalities_missing_event_count",
            "record_present",
            "measurement_status",
        ]
    ].sort_values(["geo_uid", "period_id", "native_event_type"]).reset_index(drop=True)

    qa = (
        QAResult(
            check_id="acled.certified.scope",
            state="GREEN",
            message=(
                "coverage certification is caller-supplied and explicit; no completeness claim "
                "is inferred from observed ACLED rows"
            ),
            metrics={
                "certification_id": certification.certification_id,
                "certification_sha256": certification.sha256,
                "certified_countries": len(certification.country_iso3),
                "covered_geographies": len(covered_geographies),
                "full_coverage_periods": len(full_period_ids),
                "native_event_types": len(native_event_types),
            },
        ),
        QAResult(
            check_id="acled.certified.dense_support",
            state="GREEN",
            message=(
                "certified Gold materializes structural zeros only inside the explicitly "
                "certified geography × full-period × native-event-type universe"
            ),
            metrics={
                "gold_rows": len(gold),
                "observed_rows": int(gold["record_present"].sum()),
                "structural_zero_rows": int(gold["measurement_status"].eq("structural_zero").sum()),
            },
        ),
    )
    return AcledCertifiedGoldResult(
        frame=gold,
        qa=qa,
        certified_country_iso3=certification.country_iso3,
        full_coverage_period_ids=full_period_ids,
        native_event_types=native_event_types,
        certification_sha256=certification.sha256,
    )


def build_acled_certified_coverage(
    result: AcledCertifiedGoldResult,
    *,
    certification: AcledCoverageCertification,
    geography: GeographySpec,
) -> CoverageContract:
    return CoverageContract(
        geography_scope=(
            f"{geography.id}; explicitly certified countries: "
            + ",".join(result.certified_country_iso3)
        ),
        temporal_start=certification.temporal_start,
        temporal_end=certification.temporal_end,
        observation_semantics=(
            "dense native-event-type ACLED aggregates derived from sparse supplied event records; "
            "zero rows are licensed only inside explicit external coverage certification"
        ),
        absent_row_semantics="zero_within_verified_coverage",
        authority=AuthorityLevel.L2_DERIVED,
        basis=(
            f"Explicit ACLED coverage certification {certification.certification_id} "
            f"({certification.sha256}). Basis supplied by operator/source evidence: "
            f"{certification.basis} Completeness is not inferred from filenames, row counts, "
            "or observed temporal bounds."
        ),
    )


def build_acled_certified_measurement_contract(
    *,
    sparse_gold_dataset: DatasetRef,
    geography: GeographySpec,
    period_scheme: PeriodScheme,
    coverage: CoverageContract,
    certification: AcledCoverageCertification,
    result: AcledCertifiedGoldResult,
) -> MeasurementContract:
    return MeasurementContract(
        measure_id="acled.native_event.area_period.coverage_certified",
        description=(
            "Coverage-certified ACLED event counts, fatal-event counts, and reported fatalities "
            "by analytical geography, shared period, and native event type"
        ),
        source_dataset=sparse_gold_dataset,
        output_grain=GrainSpec(keys=("geo_uid", "period_id", "native_event_type")),
        unit="events and reported fatalities",
        aggregation=(
            "retain sparse observed aggregates and materialize zero event/fatality counts only "
            "inside explicitly certified source coverage"
        ),
        coverage=coverage,
        geography=geography,
        period_scheme=period_scheme,
        parameters={
            "coverage_certification_id": certification.certification_id,
            "coverage_certification_sha256": certification.sha256,
            "certified_country_iso3": list(result.certified_country_iso3),
            "full_coverage_period_ids": list(result.full_coverage_period_ids),
            "native_event_types": list(result.native_event_types),
            "structural_zeros_materialized": True,
            "native_taxonomy_preserved": True,
            "zero_fatality_events_retained": True,
            "source_sparse_measurement_unchanged": True,
        },
    )
