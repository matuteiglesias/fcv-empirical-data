"""WB geocoded approval-count Gold preserves source subset/precision semantics."""

import json
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

from fcv_empirical.investments.worldbank_geocoded_gold import (
    build_wb_geocoded_gold,
    materialize_wb_geocoded_gold,
)


def _data():
    geography = pd.DataFrame({"geo_uid": ["g1", "g2", "g3"], "country_iso3": ["TST", "TST", "OTH"]})
    rel = pd.DataFrame(
        {
            "project_id": ["P1", "P1", "P2", "P4", "P5", "P6"],
            "project_location_id": ["L1", "L2", "L3", "L4", "L5", "L6"],
            "geo_uid": ["g1", "g1", "g1", pd.NA, "g2", "g2"],
            "recipients_iso3": ["TST"] * 6,
            "broad_source_point_assigned": [True, True, True, False, True, True],
            "high_confidence_assigned": [True, False, False, False, False, True],
            "relation_status": [
                "exact_named_place_point",
                "near_point_proxy",
                "adm2_centroid_name_unverified",
                "unresolved_coarse_or_capital_proxy",
                "near_point_proxy",
                "exact_named_place_point",
            ],
            "geographic_confidence": [
                "exact_named_place",
                "proxy",
                "proxy",
                "unresolved",
                "proxy",
                "exact_named_place",
            ],
        }
    )
    period = pd.DataFrame(
        {
            "project_id": ["P1", "P2", "P3", "P4", "P5", "P6"],
            "recipient_iso3": ["TST"] * 6,
            "period_id": [
                "2003-2004",
                "2003-2004",
                "2003-2004",
                "2003-2004",
                "2005-2006",
                "2015-2016",
            ],
            "time_assignment_status": ["assigned"] * 6,
        }
    )
    return rel, period, geography


def _scheme():
    return PeriodScheme(width_years=2, anchor_year=2001)


def _ref(name, keys, *, geo=None, period=None):
    return DatasetRef(
        dataset_id=name,
        version="fixture",
        schema_version="fixture-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=keys),
        geography=geo,
        period_scheme=period,
    )


def test_gold_deduplicates_locations_and_separates_precision_and_unknowns():
    geo, periods, units = _data()
    out = build_wb_geocoded_gold(
        geo,
        periods,
        units,
        period_scheme=_scheme(),
        source_universe_start_year=2001,
        source_universe_end_year=2006,
    )
    assert len(out.gold) == 9  # three GADM geos and three biennia
    a = out.gold.set_index(["geo_uid", "period_id"])
    x = a.loc[("g1", "2003-2004")]
    assert x.project_count == 2 and x.high_confidence_project_count == 1
    assert x.proxy_only_project_count == 1 and x.record_present
    y = a.loc[("g2", "2005-2006")]
    assert y.project_count == 1 and y.high_confidence_project_count == 0
    zero = a.loc[("g3", "2003-2004")]
    assert zero.project_count == 0 and not zero.record_present
    assert zero.measurement_status == "zero_among_resolved_eligible_source_geocodes"
    assert len(out.project_roster) == 4  # P1 twice, P2 once, P5 once
    support = out.country_period_support.set_index(["country_iso3", "period_id"])
    assert support.loc[("TST", "2003-2004"), "source_approved_project_count"] == 4
    assert support.loc[("TST", "2003-2004"), "broad_resolved_project_count"] == 2
    assert support.loc[("TST", "2003-2004"), "source_projects_without_broad_adm2_assignment"] == 2
    assert out.qa[0].state == "GREEN" and out.qa[-1].state == "YELLOW"


def test_gold_rejects_duplicate_source_location_or_cross_country_assignments():
    rel, period, units = _data()
    with pytest.raises(ValueError, match="unique WB source project locations"):
        build_wb_geocoded_gold(
            pd.concat([rel, rel.iloc[:1]], ignore_index=True),
            period,
            units,
            period_scheme=_scheme(),
        )
    bad = rel.copy()
    bad.loc[0, "geo_uid"] = "g3"
    with pytest.raises(ValueError, match="recipient ISO3 differs"):
        build_wb_geocoded_gold(bad, period, units, period_scheme=_scheme())


def test_gold_rejects_high_precision_not_broad_and_unresolved_geo_leakage():
    rel, period, units = _data()
    bad = rel.copy()
    bad.loc[3, "high_confidence_assigned"] = True
    with pytest.raises(ValueError, match="high confidence"):
        build_wb_geocoded_gold(bad, period, units, period_scheme=_scheme())
    bad = rel.copy()
    bad.loc[3, "geo_uid"] = "g1"
    with pytest.raises(ValueError, match="unassigned"):
        build_wb_geocoded_gold(bad, period, units, period_scheme=_scheme())


def test_gold_accepts_validated_contracts_and_no_money(tmp_path: Path):
    rel, period, units = _data()
    geospec = GeographySpec(provider="gadm", version="4.1-test", level="adm2", scheme="native")
    scheme = _scheme()
    output = materialize_wb_geocoded_gold(
        wb_geography_ref=_ref("wb.georelation", ("project_location_id",), geo=geospec),
        wb_period_ref=_ref("wb.period", ("project_id",), period=scheme),
        gadm_ref=_ref("gadm", ("geo_uid",), geo=geospec),
        source_wb_silver_ref=_ref("wb.source", ("project_id",)),
        wb_source_snapshot_id="wb-fixture",
        geography_relation=rel,
        period_relation=period,
        geography_units=units,
        geography=geospec,
        period_scheme=scheme,
        data_root=DataRoot.from_path(tmp_path / "dataroot"),
        run_id="wb-gold-fixture",
        source_start_year=2001,
        source_end_year=2006,
        code_commit="fixture-sha",
    )
    assert len(output.manifest.outputs) == 3
    assert len(output.manifest.inputs) == 4
    assert output.coverage_contract.absent_row_semantics == "not_observed"
    assert output.measurement_contract.parameters["amount_allocation"] is None
    assert all(v.content_sha256 for v in output.datasets.values())
    gold = pd.read_parquet(output.paths["gold"])
    assert len(gold) == 9
    assert "amount_USD" not in gold and "total_commitments" not in gold
    artifacts = (
        tmp_path / "dataroot" / "runs" / "fcv-empirical-data" / "wb-gold-fixture" / "artifacts"
    )
    assert json.loads((artifacts / "contracts" / "measurement_contract.json").read_text())[
        "measure_id"
    ]
    with pytest.raises(FileExistsError):
        materialize_wb_geocoded_gold(
            wb_geography_ref=_ref("wb.georelation", ("project_location_id",), geo=geospec),
            wb_period_ref=_ref("wb.period", ("project_id",), period=scheme),
            gadm_ref=_ref("gadm", ("geo_uid",), geo=geospec),
            source_wb_silver_ref=_ref("wb.source", ("project_id",)),
            wb_source_snapshot_id="wb-fixture",
            geography_relation=rel,
            period_relation=period,
            geography_units=units,
            geography=geospec,
            period_scheme=scheme,
            data_root=DataRoot.from_path(tmp_path / "dataroot"),
            run_id="wb-gold-fixture",
            source_start_year=2001,
            source_end_year=2006,
        )


def test_measurement_refuses_geography_and_period_contract_mismatch(tmp_path: Path):
    rel, period, units = _data()
    geo = GeographySpec(provider="gadm", version="4.1-test", level="adm2", scheme="native")
    wrong = GeographySpec(provider="gadm", version="vOLD", level="adm2", scheme="native")
    scheme = _scheme()
    with pytest.raises(ValueError, match="must agree"):
        materialize_wb_geocoded_gold(
            wb_geography_ref=_ref("wb.georelation", ("project_location_id",), geo=wrong),
            wb_period_ref=_ref("wb.period", ("project_id",), period=scheme),
            gadm_ref=_ref("gadm", ("geo_uid",), geo=geo),
            source_wb_silver_ref=_ref("wb.source", ("project_id",)),
            wb_source_snapshot_id="wb-fixture",
            geography_relation=rel,
            period_relation=period,
            geography_units=units,
            geography=geo,
            period_scheme=scheme,
            data_root=DataRoot.from_path(tmp_path / "dataroot"),
            run_id="wb-gold-mismatch",
        )
