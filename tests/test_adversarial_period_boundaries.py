import pandas as pd
from empirical_contracts import PeriodScheme

from fcv_empirical.violence.acled_index import assign_acled_periods


def _silver() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_row_id": ["early", "anchor"],
            "source_event_id": ["A-early", "A-anchor"],
            "event_date": pd.to_datetime(["1999-06-01", "2001-06-01"]),
        }
    )


def test_period_assignment_delegates_negative_ordinal_and_alternate_width() -> None:
    two_year = assign_acled_periods(
        _silver(), scheme=PeriodScheme(width_years=2, anchor_year=2001)
    ).frame.set_index("event_row_id")
    four_year = assign_acled_periods(
        _silver(), scheme=PeriodScheme(width_years=4, anchor_year=2001)
    ).frame.set_index("event_row_id")

    assert two_year.loc["early", "period_id"] == "1999-2000"
    assert two_year.loc["early", "period_ordinal"] == -1
    assert two_year.loc["anchor", "period_id"] == "2001-2002"
    assert four_year.loc["early", "period_id"] == "1997-2000"
    assert four_year.loc["early", "period_ordinal"] == -1
    assert four_year.loc["anchor", "period_id"] == "2001-2004"
