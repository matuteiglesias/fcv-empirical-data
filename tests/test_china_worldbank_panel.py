"""Common AidData geocoded-source panel: exact source/roster invariants."""

from pathlib import Path

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
from spatial_foundation import DataRoot

from fcv_empirical.investments.china_worldbank_panel import (
    build_china_worldbank_panel,
    materialize_china_worldbank_panel,
)


def _fixture():
    areas = pd.DataFrame({"geo_uid": ["g1", "g2", "g3"], "country_iso3": ["AAA", "AAA", "BBB"]})
    periods = ["2001-2002", "2003-2004"]
    rows = [
        {"geo_uid": geo, "country_iso3": cc, "period_id": p}
        for geo, cc in zip(areas.geo_uid, areas.country_iso3)
        for p in periods
    ]
    cn = pd.DataFrame(rows)
    cn["project_count"] = [1, 0, 0, 0, 0, 0]
    cn["positive_reported_amount_project_count"] = cn.project_count
    cn["reported_amount_known_project_count"] = cn.project_count
    cn["record_present"] = cn.project_count.gt(0)
    cn["measurement_status"] = cn.record_present.map(
        {True: "aggregated_from_observed", False: "structural_zero"}
    )
    wb = pd.DataFrame(rows)
    wb["project_count"] = [0, 1, 1, 0, 0, 0]
    wb["high_confidence_project_count"] = [0, 1, 0, 0, 0, 0]
    wb["proxy_only_project_count"] = wb.project_count - wb.high_confidence_project_count
    wb["record_present"] = wb.project_count.gt(0)
    wb["measurement_status"] = wb.record_present.map(
        {
            True: "aggregated_from_resolved_source_geocodes",
            False: "zero_among_resolved_eligible_source_geocodes",
        }
    )
    wb_roster = pd.DataFrame(
        {
            "project_id": ["P1", "P1", "P2"],
            "project_location_id": ["L1", "L2", "L3"],
            "geo_uid": ["g1", "g1", "g2"],
            "period_id": ["2003-2004", "2003-2004", "2001-2002"],
            "relation_status": [
                "exact_named_place_point",
                "near_point_proxy",
                "adm2_centroid_name_unverified",
            ],
            "geographic_confidence": ["exact_named_place", "proxy", "proxy"],
        }
    )
    wb_support = pd.DataFrame(
        [
            {
                "country_iso3": country,
                "period_id": p,
                "source_approved_project_count": x,
                "source_projects_without_broad_adm2_assignment": u,
            }
            for country in ["AAA", "BBB"]
            for p, x, u in [("2001-2002", 2, 0), ("2003-2004", 2, 1)]
        ]
    )
    cn_silver = pd.DataFrame(
        {
            "project_geometry_row_id": ["geom-1", "geom-foreign"],
            "source_project_id": ["CN1", "CN99"],
            "recipient_iso3": ["AAA", "ZZZ"],  # foreign footprint on same GADM area
        }
    )
    cn_geo = pd.DataFrame(
        {
            "project_geometry_row_id": ["geom-1", "geom-foreign"],
            "source_project_id": ["CN1", "CN99"],
            "geo_uid": ["g1", "g1"],
            "relation_method": ["positive_area_overlap", "positive_area_overlap"],
            "relation_status": ["matched_single", "matched_single"],
        }
    )
    cn_period = pd.DataFrame(
        {
            "project_geometry_row_id": ["geom-1", "geom-foreign"],
            "source_project_id": ["CN1", "CN99"],
            "project_date_type": ["commitment", "commitment"],
            "period_id": ["2001-2002", "2001-2002"],
            "period_assignment_status": ["assigned", "assigned"],
        }
    )
    return cn, wb, wb_roster, wb_support, cn_silver, cn_geo, cn_period, areas


def _scheme():
    return PeriodScheme(width_years=2, anchor_year=2001)


def _ref(name, keys, geo=None, scheme=None):
    return DatasetRef(
        dataset_id=name,
        version="fixture",
        schema_version="fixture-schema",
        layer=DataLayer.GOLD,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=keys),
        geography=geo,
        period_scheme=scheme,
    )


def test_common_panel_has_exact_support_and_project_id_roster_parity():
    z = build_china_worldbank_panel(
        *_fixture(), period_scheme=_scheme(), start_year=2001, end_year=2004
    )
    assert len(z.panel) == 6
    assert z.countries == ("AAA", "BBB")
    assert z.period_ids == ("2001-2002", "2003-2004")
    assert z.panel.cn_project_count.sum() == 1
    assert z.panel.wb_project_count.sum() == 2
    assert set(z.project_roster.source_project_id) == {"CN1", "P1", "P2"}
    assert len(z.project_roster) == 4  # WB P1 appears via two locations
    assert z.panel.wb_high_confidence_project_count.sum() == 1
    assert z.panel.cn_measurement_status.notna().all()
    assert z.panel.wb_measurement_status.notna().all()
    assert "exposure_class" not in z.panel.columns
    assert "CN99" not in z.project_roster.source_project_id.to_list()
    assert all(q.state in ("GREEN", "YELLOW") for q in z.qa)


def test_common_panel_fails_on_incomplete_gold_or_treatment_reconstruction():
    a = list(_fixture())
    a[0] = a[0].iloc[1:].copy()
    with pytest.raises(ValueError, match="incomplete source coverage"):
        build_china_worldbank_panel(*a, period_scheme=_scheme(), start_year=2001, end_year=2004)
    a = list(_fixture())
    a[0].loc[0, "project_count"] = 2
    a[0].loc[0, "record_present"] = True
    with pytest.raises(ValueError, match="parity violation"):
        build_china_worldbank_panel(*a, period_scheme=_scheme(), start_year=2001, end_year=2004)


def test_common_panel_rejects_corrupt_geography_or_duplicate_area():
    a = list(_fixture())
    a[0].loc[0, "country_iso3"] = "BBB"
    with pytest.raises(ValueError, match="Gold country disagrees"):
        build_china_worldbank_panel(*a, period_scheme=_scheme(), start_year=2001, end_year=2004)
    a = list(_fixture())
    a[1] = pd.concat([a[1], a[1].iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        build_china_worldbank_panel(*a, period_scheme=_scheme(), start_year=2001, end_year=2004)


def test_source_panel_contract_and_atomic_outputs(tmp_path: Path):
    z = _fixture()
    geo = GeographySpec(provider="gadm", version="4.1-test", level="adm2", scheme="native")
    scheme = _scheme()
    kwargs = {
        "cn_gold_ref": _ref("cn.gold", ("geo_uid", "period_id"), geo, scheme),
        "wb_gold_ref": _ref("wb.gold", ("geo_uid", "period_id"), geo, scheme),
        "wb_roster_ref": _ref(
            "wb.roster", ("project_location_id", "geo_uid", "period_id"), geo, scheme
        ),
        "wb_country_support_ref": _ref("wb.support", ("country_iso3", "period_id"), None, scheme),
        "cn_silver_ref": _ref("cn.silver", ("project_geometry_row_id",)),
        "cn_geo_ref": _ref("cn.geo", ("project_geometry_row_id", "geo_uid"), geo),
        "cn_period_ref": _ref(
            "cn.period", ("project_geometry_row_id", "project_date_type"), None, scheme
        ),
        "gadm_ref": _ref("gadm", ("geo_uid",), geo),
        "cn_gold": z[0],
        "wb_gold": z[1],
        "wb_project_roster": z[2],
        "wb_country_period_support": z[3],
        "cn_silver": z[4],
        "cn_geography_relation": z[5],
        "cn_period_relation": z[6],
        "geography_units": z[7],
        "geography": geo,
        "period_scheme": scheme,
        "data_root": DataRoot.from_path(tmp_path / "root"),
        "run_id": "test-source-only",
        "source_start_year": 2001,
        "source_end_year": 2004,
        "code_commit": "test-sha",
    }
    output = materialize_china_worldbank_panel(**kwargs)
    assert len(output.manifest.inputs) == 8
    assert len(output.manifest.outputs) == 3
    assert all(x.content_sha256 for x in output.manifest.outputs)
    assert len(pd.read_parquet(output.paths["panel"])) == 6
    assert "amount_sum" not in pd.read_parquet(output.paths["panel"]).columns
    with pytest.raises(FileExistsError):
        materialize_china_worldbank_panel(**kwargs)
    corrupt = dict(kwargs)
    corrupt["gadm_ref"] = _ref(
        "gadm",
        ("geo_uid",),
        GeographySpec(provider="gadm", version="vOLD", level="adm2", scheme="native"),
    )
    with pytest.raises(ValueError, match="geography version is incompatible"):
        materialize_china_worldbank_panel(**corrupt)
