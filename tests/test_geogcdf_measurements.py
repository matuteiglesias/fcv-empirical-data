import geopandas as gpd
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
from shapely.geometry import Point, Polygon

from fcv_empirical.investments.geogcdf_measurements import (
    assign_geogcdf_periods,
    build_geogcdf_commitment_coverage,
    build_geogcdf_commitment_gold,
    build_geogcdf_commitment_measurement_contract,
    relate_geogcdf_geography,
)


def _geography():
    return gpd.GeoDataFrame(
        {
            "geo_uid": ["gadm:4.1:adm2:EXA.1", "gadm:4.1:adm2:EXA.2", "gadm:4.1:adm2:EXB.1"],
            "country_iso3": ["EXA", "EXA", "EXB"],
            "geometry_role": ["analytical", "analytical", "analytical"],
        },
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
            Polygon([(3, 0), (4, 0), (4, 1), (3, 1)]),
        ],
        crs="EPSG:4326",
    )


def _silver():
    return gpd.GeoDataFrame(
        {
            "project_geometry_row_id": ["row-1", "row-2"],
            "source_project_id": ["101", "102"],
            "recipient_iso3": ["EXA", "EXA"],
            "reported_amount_constant_usd_2021": [100.0, 0.0],
            "commitment_date": [pd.Timestamp("2003-03-01"), pd.NaT],
            "commitment_year": [2003.0, 2004.0],
            "implementation_start_date": [pd.NaT, pd.NaT],
            "implementation_start_year": [2004.0, 2005.0],
            "completion_date": [pd.NaT, pd.NaT],
            "completion_year": [2005.0, pd.NA],
        },
        geometry=[
            Polygon([(0.5, 0.2), (1.5, 0.2), (1.5, 0.8), (0.5, 0.8)]),
            Point(0.25, 0.25),
        ],
        crs="EPSG:4326",
    )


def test_geogcdf_geography_preserves_legitimate_multi_area_project_footprint():
    result = relate_geogcdf_geography(_silver(), _geography())

    project_101 = result.frame[result.frame["source_project_id"].eq("101")]
    assert set(project_101["geo_uid"].dropna()) == {
        "gadm:4.1:adm2:EXA.1",
        "gadm:4.1:adm2:EXA.2",
    }
    assert set(project_101["relation_status"]) == {"matched_multiple"}
    assert set(project_101["relation_method"]) == {"positive_area_overlap"}

    project_102 = result.frame[result.frame["source_project_id"].eq("102")]
    assert project_102.iloc[0]["geo_uid"] == "gadm:4.1:adm2:EXA.1"
    assert project_102.iloc[0]["relation_status"] == "matched_unique"


def test_geogcdf_periods_keep_date_type_and_use_shared_period_scheme():
    scheme = PeriodScheme(width_years=2, anchor_year=2001)
    result = assign_geogcdf_periods(_silver(), scheme=scheme)

    p1_commitment = result.frame[
        result.frame["source_project_id"].eq("101")
        & result.frame["project_date_type"].eq("commitment")
    ].iloc[0]
    p2_commitment = result.frame[
        result.frame["source_project_id"].eq("102")
        & result.frame["project_date_type"].eq("commitment")
    ].iloc[0]
    assert p1_commitment["period_id"] == "2003-2004"
    assert p1_commitment["temporal_basis"] == "source_exact_date"
    assert p2_commitment["period_id"] == "2003-2004"
    assert p2_commitment["temporal_basis"] == "source_year"


def test_commitment_gold_materializes_structural_zeros_without_allocating_amounts():
    silver = _silver()
    geography = _geography()
    scheme = PeriodScheme(width_years=2, anchor_year=2001)
    geography_relation = relate_geogcdf_geography(silver, geography).frame
    periods = assign_geogcdf_periods(silver, scheme=scheme).frame

    gold = build_geogcdf_commitment_gold(
        silver,
        geography_relation,
        periods,
        geography,
        period_scheme=scheme,
    )

    # Only EXA is source-covered in this fixture. Fully covered T2 periods are
    # 2001-02 through 2019-20: 10 periods x 2 EXA geographies.
    assert len(gold.frame) == 20
    assert set(gold.frame["country_iso3"]) == {"EXA"}
    a_2003 = gold.frame[
        gold.frame["geo_uid"].eq("gadm:4.1:adm2:EXA.1")
        & gold.frame["period_id"].eq("2003-2004")
    ].iloc[0]
    b_2003 = gold.frame[
        gold.frame["geo_uid"].eq("gadm:4.1:adm2:EXA.2")
        & gold.frame["period_id"].eq("2003-2004")
    ].iloc[0]
    assert a_2003["project_count"] == 2
    assert a_2003["positive_reported_amount_project_count"] == 1
    assert b_2003["project_count"] == 1
    assert b_2003["positive_reported_amount_project_count"] == 1
    assert "reported_amount_sum" not in gold.frame.columns

    zero = gold.frame[
        gold.frame["geo_uid"].eq("gadm:4.1:adm2:EXA.2")
        & gold.frame["period_id"].eq("2001-2002")
    ].iloc[0]
    assert zero["project_count"] == 0
    assert zero["record_present"] == False  # noqa: E712
    assert zero["measurement_status"] == "structural_zero"


def test_structural_zero_gold_fails_when_target_country_project_geography_is_unresolved():
    silver = _silver().copy()
    silver.loc[1, "geometry"] = Point(1, 0.5)  # exact boundary between EXA.1 and EXA.2
    geography = _geography()
    scheme = PeriodScheme(width_years=2, anchor_year=2001)
    geography_relation = relate_geogcdf_geography(silver, geography).frame
    periods = assign_geogcdf_periods(silver, scheme=scheme).frame

    with pytest.raises(ValueError, match="cannot license structural-zero commitment support"):
        build_geogcdf_commitment_gold(
            silver,
            geography_relation,
            periods,
            geography,
            period_scheme=scheme,
            require_complete_resolution=True,
        )


def test_commitment_measurement_contract_describes_source_defined_zero_not_global_no_investment():
    silver = _silver()
    geography_units = _geography()
    geography = GeographySpec(provider="gadm", version="4.1", scheme="native", level="adm2")
    scheme = PeriodScheme(width_years=2, anchor_year=2001)
    geo_relation = relate_geogcdf_geography(silver, geography_units).frame
    period_relation = assign_geogcdf_periods(silver, scheme=scheme).frame
    gold = build_geogcdf_commitment_gold(
        silver,
        geo_relation,
        period_relation,
        geography_units,
        period_scheme=scheme,
    )
    coverage = build_geogcdf_commitment_coverage(gold, geography=geography, period_scheme=scheme)
    source_dataset = DatasetRef(
        dataset_id="investments.aiddata_geogcdf.projects",
        version="snapshot",
        schema_version="v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("project_geometry_row_id",)),
    )
    contract = build_geogcdf_commitment_measurement_contract(
        silver_dataset=source_dataset,
        geography=geography,
        period_scheme=scheme,
        coverage=coverage,
        covered_country_iso3=gold.covered_country_iso3,
    )

    assert coverage.absent_row_semantics == "not_observed"
    assert contract.parameters["structural_zeros_materialized"] is True
    assert contract.parameters["amount_allocation"] is None
    assert contract.parameters["amount_sum_materialized"] is False
    assert contract.output_grain.keys == ("geo_uid", "period_id")
