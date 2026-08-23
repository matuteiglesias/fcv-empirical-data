import geopandas as gpd
import pandas as pd
from empirical_contracts import GeographySpec, PeriodScheme
from shapely.geometry import Polygon

from fcv_empirical.violence.acled_index import assign_acled_geography, assign_acled_periods


def _silver() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_row_id": ["e1", "e2", "e3", "e4"],
            "source_event_id": ["A1", "A2", "A3", "A4"],
            "latitude": [0.5, 0.5, 0.5, pd.NA],
            "longitude": [0.5, 1.0, 3.0, 0.5],
            "event_date": pd.to_datetime(["2001-01-01", "2002-06-01", "2004-01-01", None]),
        }
    )


def _polygons() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "geo_uid": ["g1", "g2"],
            "geometry_role": ["analytical", "analytical"],
        },
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
        ],
        crs="EPSG:4326",
    )


def test_geography_preserves_boundary_ambiguity_outside_and_missing_coordinates():
    geography = GeographySpec(provider="gadm", version="4.1", scheme="admin", level="2")
    result = assign_acled_geography(_silver(), _polygons(), geography=geography)

    status = result.frame.groupby("event_row_id")["assignment_status"].first().to_dict()
    assert status["e1"] == "matched_unique"
    assert status["e2"] == "ambiguous_multiple"
    assert status["e3"] == "unmatched_outside"
    assert status["e4"] == "missing_coordinates"
    assert len(result.frame.loc[result.frame.event_row_id == "e2"]) == 2
    assert set(result.frame.loc[result.frame.event_row_id == "e2", "geo_uid"]) == {"g1", "g2"}


def test_period_assignment_uses_shared_period_scheme():
    t2 = assign_acled_periods(_silver(), scheme=PeriodScheme(width_years=2, anchor_year=2001))
    t3 = assign_acled_periods(_silver(), scheme=PeriodScheme(width_years=3, anchor_year=2000))

    assert t2.frame.loc[t2.frame.event_row_id == "e1", "period_id"].item() == "2001-2002"
    assert t2.frame.loc[t2.frame.event_row_id == "e3", "period_id"].item() == "2003-2004"
    assert t3.frame.loc[t3.frame.event_row_id == "e1", "period_id"].item() == "2000-2002"
    assert (
        t2.frame.loc[t2.frame.event_row_id == "e4", "period_assignment_status"].item()
        == "missing_or_invalid_date"
    )
