from datetime import date

import pandas as pd
import pytest
from empirical_contracts import (
    AuthorityLevel,
    DataLayer,
    DatasetRef,
    GeographySpec,
    GrainSpec,
    PeriodScheme,
)

from fcv_empirical.violence.acled_certified import (
    AcledCoverageCertification,
    build_acled_certified_coverage,
    build_acled_certified_gold,
    build_acled_certified_measurement_contract,
)


def _sparse_gold():
    return pd.DataFrame(
        {
            "geo_uid": ["g1", "g1", "g2"],
            "period_id": ["2001-2002", "2001-2002", "2003-2004"],
            "native_event_type": ["Violence against civilians", "Protests", "Protests"],
            "event_count": [1, 2, 1],
            "fatal_event_count": [1, 0, 0],
            "fatalities": [2.0, 0.0, 0.0],
            "fatalities_known_event_count": [1, 2, 1],
            "fatalities_missing_event_count": [0, 0, 0],
            "record_present": [True, True, True],
        }
    )


def _geography_units():
    return pd.DataFrame(
        {
            "geo_uid": ["g1", "g2", "g3"],
            "country_iso3": ["EXA", "EXA", "EXB"],
        }
    )


def _certification(end: date = date(2004, 12, 31)):
    return AcledCoverageCertification(
        certification_id="fixture-complete-export",
        country_iso3=("EXA",),
        temporal_start=date(2001, 1, 1),
        temporal_end=end,
        basis="Synthetic fixture explicitly declares complete EXA event reporting.",
    )


def test_certified_gold_materializes_zero_only_inside_explicit_scope():
    scheme = PeriodScheme(width_years=2, anchor_year=2001)
    result = build_acled_certified_gold(
        _sparse_gold(),
        _geography_units(),
        period_scheme=scheme,
        certification=_certification(),
    )

    assert result.certified_country_iso3 == ("EXA",)
    assert result.full_coverage_period_ids == ("2001-2002", "2003-2004")
    assert set(result.native_event_types) == {"Protests", "Violence against civilians"}
    assert len(result.frame) == 8  # 2 EXA geographies × 2 periods × 2 event types
    assert "g3" not in set(result.frame.geo_uid)

    observed = result.frame.loc[
        result.frame.geo_uid.eq("g1")
        & result.frame.period_id.eq("2001-2002")
        & result.frame.native_event_type.eq("Violence against civilians")
    ].iloc[0]
    assert observed.event_count == 1
    assert observed.fatalities == 2
    assert bool(observed.record_present) is True
    assert observed.measurement_status == "aggregated_from_observed"

    zero = result.frame.loc[
        result.frame.geo_uid.eq("g2")
        & result.frame.period_id.eq("2001-2002")
        & result.frame.native_event_type.eq("Violence against civilians")
    ].iloc[0]
    assert zero.event_count == 0
    assert zero.fatalities == 0
    assert bool(zero.record_present) is False
    assert zero.measurement_status == "structural_zero"


def test_certification_does_not_license_partial_period():
    scheme = PeriodScheme(width_years=2, anchor_year=2001)
    result = build_acled_certified_gold(
        _sparse_gold(),
        _geography_units(),
        period_scheme=scheme,
        certification=_certification(end=date(2003, 12, 31)),
    )

    assert result.full_coverage_period_ids == ("2001-2002",)
    assert set(result.frame.period_id) == {"2001-2002"}


def test_certification_refuses_country_missing_from_target_geography():
    certification = AcledCoverageCertification(
        certification_id="missing-country",
        country_iso3=("EXC",),
        temporal_start=date(2001, 1, 1),
        temporal_end=date(2004, 12, 31),
        basis="Synthetic explicit completeness statement.",
    )
    with pytest.raises(ValueError, match="countries absent from target geography"):
        build_acled_certified_gold(
            _sparse_gold(),
            _geography_units(),
            period_scheme=PeriodScheme(width_years=2, anchor_year=2001),
            certification=certification,
        )


def test_certified_contract_licenses_zero_without_mutating_sparse_measurement():
    geography = GeographySpec(provider="gadm", version="4.1", scheme="native", level="adm2")
    scheme = PeriodScheme(width_years=2, anchor_year=2001)
    certification = _certification()
    result = build_acled_certified_gold(
        _sparse_gold(),
        _geography_units(),
        period_scheme=scheme,
        certification=certification,
    )
    coverage = build_acled_certified_coverage(
        result,
        certification=certification,
        geography=geography,
    )
    sparse_dataset = DatasetRef(
        dataset_id="violence.acled.area_period_native_event",
        version="sparse-v1",
        schema_version="acled-native-event-gold-v1",
        layer=DataLayer.GOLD,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("geo_uid", "period_id", "native_event_type")),
        geography=geography,
        period_scheme=scheme,
    )
    contract = build_acled_certified_measurement_contract(
        sparse_gold_dataset=sparse_dataset,
        geography=geography,
        period_scheme=scheme,
        coverage=coverage,
        certification=certification,
        result=result,
    )

    assert coverage.absent_row_semantics == "zero_within_verified_coverage"
    assert "not inferred" in coverage.basis
    assert contract.measure_id == "acled.native_event.area_period.coverage_certified"
    assert contract.parameters["source_sparse_measurement_unchanged"] is True
    assert contract.parameters["coverage_certification_sha256"] == certification.sha256
