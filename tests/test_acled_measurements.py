import pandas as pd
from empirical_contracts import GeographySpec

from fcv_empirical.violence.acled_measurements import build_acled_coverage, build_acled_gold


def test_sparse_gold_counts_zero_fatality_events_without_duplication():
    silver = pd.DataFrame(
        {
            "event_row_id": ["e1", "e2", "e3"],
            "event_date": pd.to_datetime(["2001-01-01", "2001-02-01", "2001-03-01"]),
            "native_event_type": ["Protests", "Violence against civilians", "Protests"],
            "fatalities": [0.0, 2.0, 3.0],
        }
    )
    geography = pd.DataFrame(
        {
            "event_row_id": ["e1", "e2", "e3", "e3"],
            "geo_uid": ["g1", "g1", "g1", "g2"],
            "assignment_status": [
                "matched_unique",
                "matched_unique",
                "ambiguous_multiple",
                "ambiguous_multiple",
            ],
        }
    )
    periods = pd.DataFrame(
        {
            "event_row_id": ["e1", "e2", "e3"],
            "period_id": ["2001-2002", "2001-2002", "2001-2002"],
            "period_assignment_status": ["assigned", "assigned", "assigned"],
        }
    )

    result = build_acled_gold(silver, geography, periods)

    protests = result.frame.loc[result.frame.native_event_type == "Protests"].iloc[0]
    assert protests.event_count == 1
    assert protests.fatal_event_count == 0
    assert protests.fatalities == 0
    assert bool(protests.record_present) is True
    assert "g2" not in set(result.frame.geo_uid)
    assert len(result.frame) == 2


def test_sparse_absence_does_not_materialize_zero_and_coverage_defaults_unknown():
    silver = pd.DataFrame(
        {
            "event_row_id": ["e1"],
            "event_date": pd.to_datetime(["2001-01-01"]),
            "native_event_type": ["Protests"],
            "fatalities": [0.0],
        }
    )
    geography = pd.DataFrame(
        {"event_row_id": ["e1"], "geo_uid": ["g1"], "assignment_status": ["matched_unique"]}
    )
    periods = pd.DataFrame(
        {
            "event_row_id": ["e1"],
            "period_id": ["2001-2002"],
            "period_assignment_status": ["assigned"],
        }
    )
    gold = build_acled_gold(silver, geography, periods).frame
    coverage = build_acled_coverage(
        silver,
        snapshot_id="acled-fixture",
        geography=GeographySpec(provider="gadm", version="4.1", scheme="admin", level="2"),
    )

    assert len(gold) == 1
    assert not ((gold.geo_uid == "g2") & (gold.period_id == "2001-2002")).any()
    assert coverage.absent_row_semantics == "unknown"
