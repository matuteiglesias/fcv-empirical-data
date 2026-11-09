import geopandas as gpd
import pandas as pd
import pytest
from empirical_contracts import PeriodScheme
from shapely.geometry import Polygon

from fcv_empirical.investments.worldbank_geocoded_measurement_relations import (
    build_wb_geocoded_relations,
)


def _geography():
    geo = gpd.GeoDataFrame(
        {
            "geo_uid": ["g1", "g2", "g3"],
            "source_geo_id": ["EXA.1.1_1", "EXA.1.2_1", "EXB.1.1_1"],
            "country_iso3": ["EXA", "EXA", "EXB"],
            "geometry_role": ["analytical"] * 3,
        },
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
            Polygon([(3, 0), (4, 0), (4, 1), (3, 1)]),
        ],
        crs="EPSG:4326",
    )
    names = pd.DataFrame(
        {
            "GID_2": geo.source_geo_id,
            "NAME_2": ["Alpha", "Beta", "Gamma"],
        }
    )
    return geo, names


def _wb():
    project_ids = [f"P{x}" for x in range(1, 10)]
    p = pd.DataFrame(
        {
            "project_id": project_ids,
            "recipients_iso3": ["EXA", "EXA", "EXA", "EXA", "EXB", "EXA", "EXA", "EXA", "OTHER"],
            "start_actual_isodate": [
                "2003-01-01",
                "2003-06-10",
                "2014-12-31",
                "2011-05-20",
                "2005-05-20",
                "2008-03-01",
                "1999-09-20",
                "2010-01-01",
                "2004-03-02",
            ],
        }
    )
    locations = pd.DataFrame(
        {
            "project_id": ["P1", "P1", "P2", "P3", "P4", "P5", "P6", "P7"],
            "project_location_id": ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"],
            "precision_code": [1, 1, 3, 3, 8, 1, 1, 2],
            "place_name": [
                "Alpha",
                "Alpha",
                "Beta District",
                "Old Beta",
                "Capital",
                "Alpha",
                "Boundary",
                "Near Alpha",
            ],
            "latitude": [0.2, 0.4, 0.4, 0.55, 0.3, 0.2, 0.5, 0.6],
            "longitude": [0.2, 0.3, 1.4, 1.6, 0.3, 0.3, 1.0, 0.5],
            "location_type_code": ["PPL"] * 8,
            "geographic_exactness": [1] * 8,
        }
    )
    return p, locations


def test_wb_geography_preserves_exact_verified_proxy_and_unresolved() -> None:
    geo, names = _geography()
    p, loc = _wb()
    result = build_wb_geocoded_relations(
        p, loc, geo, names, period_scheme=PeriodScheme(width_years=2, anchor_year=2001)
    )
    r = result.geography.set_index("project_location_id")
    assert len(r) == 8
    assert r.loc["L1", "relation_status"] == "exact_named_place_point"
    assert r.loc["L2", "geo_uid"] == "g1"
    assert r.loc["L3", "relation_status"] == "adm2_centroid_name_verified"
    assert r.loc["L3", "geo_uid"] == "g2"
    assert r.loc["L4", "relation_status"] == "adm2_centroid_name_unverified"
    assert r.loc["L4", "broad_source_point_assigned"]
    assert not r.loc["L4", "high_confidence_assigned"]
    assert r.loc["L5", "relation_status"] == "unresolved_coarse_or_capital_proxy"
    assert pd.isna(r.loc["L5", "geo_uid"])
    assert r.loc["L6", "relation_status"] == "unresolved_cross_country_point"
    assert pd.isna(r.loc["L6", "geo_uid"])
    assert r.loc["L7", "relation_status"] == "unresolved_point_outside_or_ambiguous"
    assert pd.isna(r.loc["L7", "geo_uid"])
    assert r.loc["L8", "relation_status"] == "near_point_proxy"
    assert not r.loc["L8", "high_confidence_assigned"]
    assert r.project_id.eq("P1").sum() == 2  # locations preserved; Gold later counts one P1
    assert r.high_confidence_assigned.sum() == 3


def test_wb_approval_dates_use_shared_period_index_not_implementation() -> None:
    geo, names = _geography()
    p, loc = _wb()
    result = build_wb_geocoded_relations(
        p, loc, geo, names, period_scheme=PeriodScheme(width_years=2, anchor_year=2001)
    )
    periods = result.periods.set_index("project_id")
    assert len(periods) == 8  # external OTHER project is outside declared GADM
    assert periods.loc["P3", "period_id"] == "2013-2014"
    assert periods.loc["P7", "period_id"] == "1999-2000"
    assert periods.loc["P8", "period_id"] == "2009-2010"  # no locations, still retained
    assert periods.event_type.eq("board_approval").all()


def test_administrative_name_inventory_mismatch_fails_closed() -> None:
    geo, names = _geography()
    p, loc = _wb()
    with pytest.raises(ValueError, match="not the exact analytical geography"):
        build_wb_geocoded_relations(
            p,
            loc,
            geo,
            names.iloc[:-1].copy(),
            period_scheme=PeriodScheme(width_years=2, anchor_year=2001),
        )


def test_duplicate_source_location_id_refused() -> None:
    geo, names = _geography()
    p, loc = _wb()
    with pytest.raises(ValueError, match="location IDs must be unique"):
        build_wb_geocoded_relations(
            p,
            pd.concat([loc, loc.iloc[:1]], ignore_index=True),
            geo,
            names,
            period_scheme=PeriodScheme(width_years=2, anchor_year=2001),
        )


def test_wb_relation_materialization_has_traceable_inputs(tmp_path):
    from empirical_contracts import AuthorityLevel, DataLayer, DatasetRef, GeographySpec, GrainSpec
    from spatial_foundation import DataRoot
    from fcv_empirical.investments.worldbank_geocoded_measurement_relations import (
        materialize_wb_geocoded_relations,
    )

    geo, names = _geography()
    projects, loc = _wb()
    names_path = tmp_path / "gadm_names.csv"
    names.to_csv(names_path, index=False)

    def ref(ident, keys):
        return DatasetRef(
            dataset_id=ident,
            version="fixture-v1",
            schema_version="fixture-schema-v1",
            layer=DataLayer.SILVER,
            authority=AuthorityLevel.L3_REBUILT,
            grain=GrainSpec(keys=keys),
        )

    root = DataRoot.from_path(tmp_path / "root")
    result = materialize_wb_geocoded_relations(
        wb_silver_dataset_refs=(
            ref("wb.fixture.projects", ("project_id",)),
            ref("wb.fixture.locations", ("project_location_id",)),
        ),
        gadm_dataset_ref=ref("gadm.fixture", ("geo_uid",)),
        wb_source_snapshot_id="wb-fixture",
        projects=projects,
        locations=loc,
        geography=geo,
        adm2_names_file=names_path,
        geography_spec=GeographySpec(
            provider="gadm", version="4.1-africa-adm2", level="adm2", scheme="native"
        ),
        period_scheme=PeriodScheme(width_years=2, anchor_year=2001),
        data_root=root,
        run_id="wb-w2-fixture",
        code_commit="synthetic-test",
    )
    assert len(result.manifest.inputs) == 4
    assert len(result.manifest.outputs) == 2
    assert all(x.content_sha256 for x in result.manifest.outputs)
    assert pd.read_parquet(result.paths["geography"]).shape[0] == 8
    assert pd.read_parquet(result.paths["periods"]).shape[0] == 8
    assert (root.run("fcv-empirical-data", "wb-w2-fixture") / "run_manifest.json").exists()
