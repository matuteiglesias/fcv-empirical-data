from pathlib import Path

import pandas as pd

from fcv_empirical.violence.acled_events import normalize_acled_events, register_acled_snapshot


def _raw() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "EVENT_ID_CNTY": ["A1", "A2", "A2", "A4"],
            "EVENT_DATE": ["2001-01-15", "2001-02-01", "2001-02-02", "bad-date"],
            "COUNTRY": ["X", "X", "X", "X"],
            "LATITUDE": [0.5, 0.5, 0.7, 0.8],
            "LONGITUDE": [0.5, 0.7, 0.8, 0.9],
            "EVENT_TYPE": [
                "Protests",
                "Violence against civilians",
                "Violence against civilians",
                "Riots",
            ],
            "SUB_EVENT_TYPE": ["Peaceful protest", "Attack", "Attack", "Mob violence"],
            "FATALITIES": [0, 2, "not-a-number", 0],
            "GEO_PRECISION": [1, 2, 3, 1],
            "TIME_PRECISION": [1, 1, 1, 1],
        }
    )


def test_silver_keeps_zero_fatality_and_lower_precision_events(tmp_path: Path):
    source = tmp_path / "acled.csv"
    _raw().to_csv(source, index=False)
    snapshot = register_acled_snapshot(source, release="fixture")

    result = normalize_acled_events(_raw(), snapshot=snapshot)

    assert len(result.frame) == 4
    assert result.frame.loc[0, "fatalities"] == 0
    assert result.frame.loc[1, "geo_precision"] == 2
    assert result.frame.loc[2, "geo_precision"] == 3
    assert "source__GEO_PRECISION" in result.frame.columns
    assert result.frame["source_event_id"].tolist() == ["A1", "A2", "A2", "A4"]


def test_silver_detects_duplicate_ids_bad_dates_and_malformed_fatalities(tmp_path: Path):
    source = tmp_path / "acled.csv"
    raw = _raw()
    raw.to_csv(source, index=False)
    snapshot = register_acled_snapshot(source, release="fixture")

    result = normalize_acled_events(raw, snapshot=snapshot)
    qa = {item.check_id: item for item in result.qa}

    assert qa["acled.silver.source_event_id"].state == "YELLOW"
    assert qa["acled.silver.source_event_id"].metrics["duplicate_source_event_id_rows"] == 2
    assert qa["acled.silver.event_dates"].metrics["invalid_event_dates"] == 1
    assert qa["acled.silver.fatalities"].state == "RED"
    assert qa["acled.silver.fatalities"].metrics["invalid_fatalities"] == 1
    assert pd.isna(result.frame.loc[2, "fatalities"])
