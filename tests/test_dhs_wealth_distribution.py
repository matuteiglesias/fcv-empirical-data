import pandas as pd
import pytest

from fcv_empirical.surveys.dhs_wealth_distribution import (
    DhsWealthRegionSpec,
    build_dhs_wealth_region_shares,
)


def _frame(weight_scale: float = 1.0) -> pd.DataFrame:
    # Region A has the only poorest household, while the richer quintiles are
    # split between A and B. Household size must matter independently of HV005.
    return pd.DataFrame(
        {
            "survey_id": ["dhs-fixture"] * 7,
            "hv024": [1, 1, 1, 2, 2, 2, 2],
            "hv270": [1, 2, 3, 2, 3, 4, 5],
            "hv005": [2, 1, 1, 1, 3, 5, 7],
            "hv012": [3, 2, 1, 4, 1, 1, 1],
        }
    ).assign(hv005=lambda frame: frame["hv005"] * weight_scale)


def _spec() -> DhsWealthRegionSpec:
    return DhsWealthRegionSpec(survey_id="dhs-fixture", region_variable="HV024")


def test_briggs_weighting_uses_sample_weight_times_dejure_household_size() -> None:
    result = build_dhs_wealth_region_shares(_frame(), _spec())
    wide = result.wide.set_index("region_code")

    # Quintile 2 weighted mass is A: 1*2=2, B: 1*4=4.
    assert wide.loc["1", "second_poorest_share"] == pytest.approx(1 / 3)
    assert wide.loc["2", "second_poorest_share"] == pytest.approx(2 / 3)

    # Each quintile is a national denominator, not a within-region composition.
    sums = result.frame.groupby("wealth_quintile")["wealth_quintile_region_share"].sum()
    assert sums.to_dict() == pytest.approx({1: 1, 2: 1, 3: 1, 4: 1, 5: 1})


def test_source_weight_scale_cancels_from_shares() -> None:
    base = build_dhs_wealth_region_shares(_frame(), _spec()).wide
    scaled = build_dhs_wealth_region_shares(_frame(1_000_000), _spec()).wide
    columns = [column for column in base.columns if column.endswith("_share")]
    pd.testing.assert_frame_equal(base[columns], scaled[columns])


def test_region_quintile_support_is_dense_only_inside_observed_region_universe() -> None:
    result = build_dhs_wealth_region_shares(_frame(), _spec())
    assert len(result.frame) == 2 * 5
    q1 = result.frame[result.frame["wealth_quintile"].eq(1)].set_index("region_code")
    assert q1.loc["1", "wealth_quintile_region_share"] == pytest.approx(1.0)
    assert q1.loc["2", "wealth_quintile_region_share"] == pytest.approx(0.0)
    assert result.diagnostics["zero_region_quintile_cells"] > 0


def test_expected_population_share_is_one_fifth_sum_of_quintile_shares() -> None:
    result = build_dhs_wealth_region_shares(_frame(), _spec())
    share_columns = [
        "poorest_share",
        "second_poorest_share",
        "middle_share",
        "second_richest_share",
        "richest_share",
    ]
    expected = result.wide[share_columns].sum(axis=1) / 5.0
    assert result.wide["expected_population_share"].tolist() == pytest.approx(expected.tolist())


def test_missing_region_fails_closed() -> None:
    frame = _frame()
    frame.loc[0, "hv024"] = pd.NA
    with pytest.raises(ValueError, match="missing region codes"):
        build_dhs_wealth_region_shares(frame, _spec())


def test_invalid_quintile_fails_closed() -> None:
    frame = _frame()
    frame.loc[0, "hv270"] = 9
    with pytest.raises(ValueError, match="quintiles 1..5"):
        build_dhs_wealth_region_shares(frame, _spec())
