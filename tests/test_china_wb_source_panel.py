"""Acceptance and adversarial source-fusion tests: no econometric treatment."""

from __future__ import annotations

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

from fcv_empirical.investments.china_wb_source_panel import (
    build_aiddata_china_wb_panel,
    materialize_aiddata_china_wb_panel,
)


def fixture():
    countries = pd.DataFrame(
        {
            "geo_uid": ["g1", "g2"],
            "country_iso3": ["AAA", "AAA"],
        }
    )
    periods = ("2001-2002", "2003-2004")
    grid = (
        countries.assign(_key=1)
        .merge(pd.DataFrame({"period_id": periods, "_key": 1}), on="_key")
        .drop(columns="_key")
    )
    wb = grid.copy()
    wb["project_count"] = [1, 0, 0, 0]
    wb["high_confidence_project_count"] = [1, 0, 0, 0]
    wb["proxy_only_project_count"] = 0
    wb["record_present"] = wb.project_count.gt(0)
    wb["measurement_status"] = wb.record_present.map(
        {
            True: "aggregated_from_resolved_source_geocodes",
            False: "zero_among_resolved_eligible_source_geocodes",
        }
    )
    cn = grid.copy()
    cn["project_count"] = [1, 0, 0, 1]
    cn["reported_amount_known_project_count"] = [1, 0, 0, 1]
    cn["positive_reported_amount_project_count"] = [1, 0, 0, 1]
    cn["record_present"] = cn.project_count.gt(0)
    cn["measurement_status"] = cn.record_present.map(
        {
            True: "aggregated_from_observed",
            False: "structural_zero",
        }
    )
    wb_roster = pd.DataFrame(
        {
            "project_id": ["P1", "P1"],
            "project_location_id": ["P1A", "P1B"],
            "geo_uid": ["g1", "g1"],
            "period_id": [periods[0], periods[0]],
            "relation_status": ["exact_named_place_point"] * 2,
            "geographic_confidence": ["exact_named_place"] * 2,
            "high_confidence_assigned": [True, True],
        }
    )
    cn_geo = pd.DataFrame(
        {
            "project_geometry_row_id": ["C1", "C2", "C3"],
            "source_project_id": [101, 102, 103],
            "recipient_iso3": ["AAA", "AAA", "BBB"],
            "geo_uid": ["g1", "g2", "g1"],
            "relation_status": ["matched_single"] * 3,
            "relation_method": ["positive_area_overlap"] * 3,
        }
    )
    cn_t = pd.DataFrame(
        {
            "project_geometry_row_id": ["C1", "C2", "C3", "C1"],
            "project_date_type": ["commitment", "commitment", "commitment", "completion"],
            "period_id": [periods[0], periods[1], periods[0], periods[1]],
            "period_assignment_status": ["assigned"] * 4,
        }
    )
    return {
        "wb_gold": wb,
        "cn_gold": cn,
        "wb_project_roster": wb_roster,
        "cn_project_geography": cn_geo,
        "cn_project_period": cn_t,
        "geography_units": countries,
        "target_period_ids": periods,
    }


def test_common_panel_and_roster_have_exact_source_parity():
    result = build_aiddata_china_wb_panel(**fixture())
    assert len(result.panel) == 4
    assert len(result.roster) == 4  # 2 WB rows, 2 eligible CN rows; outside source excluded
    assert result.support_counts == {
        "recorded_both": 1,
        "recorded_wb_only": 0,
        "recorded_china_only": 1,
        "recorded_neither": 2,
    }
    assert set(result.roster.source_family) == {"world_bank", "china"}
    assert "exposure_class" not in result.panel.columns
    assert "amount_usd" not in result.panel.columns
    assert {x.state for x in result.qa} == {"GREEN", "YELLOW"}


def test_duplicate_gold_grain_is_a_hard_failure():
    inputs = fixture()
    inputs["wb_gold"] = pd.concat(
        [inputs["wb_gold"], inputs["wb_gold"].iloc[:1]], ignore_index=True
    )
    with pytest.raises(ValueError, match="duplicated area-period"):
        build_aiddata_china_wb_panel(**inputs)


def test_missing_country_period_cannot_be_coerced_to_zero():
    inputs = fixture()
    inputs["wb_gold"] = inputs["wb_gold"].iloc[:-1]
    with pytest.raises(ValueError, match="incomplete source coverage"):
        build_aiddata_china_wb_panel(**inputs)


def test_source_roster_count_mismatch_is_a_hard_failure():
    inputs = fixture()
    inputs["cn_project_geography"] = inputs["cn_project_geography"].iloc[1:]
    with pytest.raises(ValueError, match="china: 1 source Gold cells disagree"):
        build_aiddata_china_wb_panel(**inputs)


def test_source_status_cannot_disagree_with_count():
    inputs = fixture()
    inputs["cn_gold"].loc[0, "measurement_status"] = "structural_zero"
    with pytest.raises(ValueError, match="status contradicts actual source count"):
        build_aiddata_china_wb_panel(**inputs)


def _ref(name, *, with_geo=True, with_period=True):
    return DatasetRef(
        dataset_id=name,
        version="fixture",
        schema_version="test-v1",
        layer=DataLayer.GOLD,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("geo_uid", "period_id")),
        geography=GeographySpec(
            provider="gadm", version="4.1-africa-adm2", scheme="native", level="adm2"
        )
        if with_geo
        else None,
        period_scheme=PeriodScheme(width_years=2, anchor_year=2001) if with_period else None,
        content_sha256="a" * 64,
    )


def test_source_compatibility_contract_catches_geography_mismatch(tmp_path):
    x = fixture()
    refs = {
        "wb_gold_ref": _ref("wb_gold"),
        "wb_roster_ref": _ref("wb_roster", with_period=False),
        "cn_gold_ref": _ref("cn_gold"),
        "cn_geo_ref": _ref("cn_geo", with_period=False),
        "cn_period_ref": _ref("cn_period", with_geo=False),
        "gadm_ref": _ref("gadm", with_period=False),
    }
    refs["cn_gold_ref"] = refs["cn_gold_ref"].model_copy(
        update={
            "geography": GeographySpec(
                provider="gadm", version="3.6", scheme="native", level="adm2"
            )
        }
    )
    with pytest.raises(ValueError, match="identical GeographySpec"):
        materialize_aiddata_china_wb_panel(
            **refs,
            **x,
            data_root=DataRoot.from_path(tmp_path / "d"),
            run_id="test",
        )


def test_manifest_and_artifacts_retained_for_source_qualified_comparison(tmp_path):
    x = fixture()
    refs = {
        "wb_gold_ref": _ref("wb_gold"),
        "wb_roster_ref": _ref("wb_roster", with_period=False),
        "cn_gold_ref": _ref("cn_gold"),
        "cn_geo_ref": _ref("cn_geo", with_period=False),
        "cn_period_ref": _ref("cn_period", with_geo=False),
        "gadm_ref": _ref("gadm", with_period=False),
    }
    result = materialize_aiddata_china_wb_panel(
        **refs,
        **x,
        data_root=DataRoot.from_path(tmp_path / "d"),
        run_id="test-source-panel",
        code_commit="test-commit",
    )
    assert all(path.exists() for path in result.paths.values())
    assert len(result.run_manifest.inputs) == 6
    assert len(result.run_manifest.outputs) == 2
    assert all(x.content_sha256 for x in result.run_manifest.outputs)
    assert len(pd.read_parquet(result.paths["panel"])) == 4
    assert (
        tmp_path
        / "d"
        / "runs"
        / "fcv-empirical-data"
        / "test-source-panel"
        / "artifacts"
        / "qa"
        / "source_support.json"
    ).exists()
