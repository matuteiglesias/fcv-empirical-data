import pandas as pd
import pytest
from empirical_contracts import AuthorityLevel, DataLayer, DatasetRef, GrainSpec

from fcv_empirical.surveys.catalog import SurveyCatalogEntry
from fcv_empirical.surveys.dhs_integration import build_dhs_survey_integration_report


def _survey() -> SurveyCatalogEntry:
    return SurveyCatalogEntry(
        survey_id="dhs-ZZ2020DHS",
        source_family="dhs",
        country_iso3="ZZZ",
        survey_year=2020,
        survey_phase="DHS-VII",
        release="synthetic-release-v1",
    )


def _frames():
    survey_id = _survey().survey_id
    hr = pd.DataFrame(
        {
            "source_row_id": ["hr-row-1", "hr-row-2", "hr-row-3"],
            "survey_id": [survey_id, survey_id, survey_id],
            "household_id": ["001001", "001002", "002001"],
            "cluster_id": ["1", "1", "2"],
            "source_snapshot_id": ["hr-snapshot"] * 3,
        }
    )
    gps = pd.DataFrame(
        {
            "cluster_row_id": ["gps-row-1", "gps-row-2"],
            "survey_id": [survey_id, survey_id],
            "cluster_id": ["1", "2"],
            "dhsid": ["ZZ2020DHS00000001", "ZZ2020DHS00000002"],
            "source_snapshot_id": ["gps-snapshot"] * 2,
        }
    )
    gc = pd.DataFrame(
        {
            "survey_id": [survey_id, survey_id],
            "cluster_id": ["ZZ2020DHS00000001", "ZZ2020DHS00000002"],
            "dhsid": ["ZZ2020DHS00000001", "ZZ2020DHS00000002"],
            "dhsclust": ["1", "2"],
            "source_snapshot_id": ["gc-snapshot"] * 2,
        }
    )
    return hr, gps, gc


def _dataset(dataset_id: str, *keys: str) -> DatasetRef:
    return DatasetRef(
        dataset_id=dataset_id,
        version="fixture",
        schema_version="fixture-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=keys),
    )


def _qa(report, check_id: str):
    return next(item for item in report.qa if item.check_id == check_id)


def test_one_catalog_connects_households_gps_and_gc_without_joining_grains():
    hr, gps, gc = _frames()
    report = build_dhs_survey_integration_report(
        survey=_survey(),
        hr=hr,
        gps=gps,
        gc=gc,
        datasets={
            "hr": _dataset("surveys.dhs.hr_households", "source_row_id"),
            "gps": _dataset("surveys.dhs.gps_clusters", "cluster_row_id"),
            "gc": _dataset("surveys.dhs_gc.clusters", "survey_id", "cluster_id"),
        },
    )

    assert report.summary["gc_link_key_basis"] == "dhsclust"
    assert report.summary["hr_gps_overlap"] == 2
    assert report.summary["hr_gc_overlap"] == 2
    assert report.summary["gps_gc_overlap"] == 2
    assert report.summary["hr_household_rows"] == 3
    assert report.summary["dataset_refs_checked"] == ("gc", "gps", "hr")
    assert len(report.cluster_support) == 2
    assert report.cluster_support[["in_hr", "in_gps", "in_gc"]].all(axis=None)
    assert _qa(report, "dhs.integration.survey_identity").state == "GREEN"
    assert _qa(report, "dhs.integration.dataset_grain").state == "GREEN"
    assert _qa(report, "dhs.integration.cluster_support").state == "GREEN"


def test_gc_dhsid_is_not_mistaken_for_household_cluster_number():
    hr, gps, gc = _frames()
    report = build_dhs_survey_integration_report(survey=_survey(), hr=hr, gps=gps, gc=gc)

    assert report.summary["gc_link_key_basis"] == "dhsclust"
    assert set(report.cluster_support["cluster_link_id"]) == {"1", "2"}
    assert not set(gc["cluster_id"]).intersection(set(hr["cluster_id"]))


def test_source_only_clusters_remain_visible_instead_of_inner_join_disappearing():
    hr, gps, gc = _frames()
    gps = pd.concat(
        [
            gps,
            pd.DataFrame(
                {
                    "cluster_row_id": ["gps-row-3"],
                    "survey_id": [_survey().survey_id],
                    "cluster_id": ["3"],
                    "dhsid": ["ZZ2020DHS00000003"],
                    "source_snapshot_id": ["gps-snapshot"],
                }
            ),
        ],
        ignore_index=True,
    )

    report = build_dhs_survey_integration_report(survey=_survey(), hr=hr, gps=gps, gc=gc)

    cluster_three = report.cluster_support.loc[report.cluster_support["cluster_link_id"] == "3"].iloc[0]
    assert not cluster_three.in_hr
    assert cluster_three.in_gps
    assert not cluster_three.in_gc
    assert report.summary["gps_only_vs_hr"] == 1
    assert _qa(report, "dhs.integration.cluster_support").state == "YELLOW"


def test_numeric_equivalence_is_reported_but_never_used_as_a_silent_join_rule():
    hr, gps, gc = _frames()
    hr["cluster_id"] = ["001", "001", "002"]

    report = build_dhs_survey_integration_report(survey=_survey(), hr=hr, gps=gps, gc=gc)

    assert report.summary["hr_gps_overlap"] == 0
    assert report.summary["hr_gc_overlap"] == 0
    assert set(report.cluster_support["cluster_link_id"]) == {"001", "002", "1", "2"}
    normalization = _qa(report, "dhs.integration.cluster_identity_normalization")
    assert normalization.state == "YELLOW"
    assert normalization.metrics["numeric_alias_pairs"] == 2


def test_cross_survey_product_is_rejected_before_support_comparison():
    hr, gps, gc = _frames()
    gps.loc[0, "survey_id"] = "dhs-OTHER"

    with pytest.raises(ValueError, match="does not resolve uniquely"):
        build_dhs_survey_integration_report(survey=_survey(), hr=hr, gps=gps, gc=gc)


def test_dataset_role_cannot_masquerade_as_another_product():
    hr, gps, gc = _frames()

    with pytest.raises(ValueError, match="requires dataset_id"):
        build_dhs_survey_integration_report(
            survey=_survey(),
            hr=hr,
            gps=gps,
            gc=gc,
            datasets={"gps": _dataset("surveys.dhs.hr_households", "cluster_row_id")},
        )


def test_declared_dataset_grain_must_be_unique_in_actual_product():
    hr, gps, gc = _frames()
    hr.loc[1, "household_id"] = hr.loc[0, "household_id"]

    with pytest.raises(ValueError, match="grain does not uniquely identify"):
        build_dhs_survey_integration_report(
            survey=_survey(),
            hr=hr,
            gps=gps,
            gc=gc,
            datasets={
                "hr": _dataset(
                    "surveys.dhs.hr_households", "survey_id", "household_id"
                )
            },
        )


def test_missing_cluster_identity_is_visible_not_dropped():
    hr, gps, gc = _frames()
    hr.loc[2, "cluster_id"] = pd.NA

    report = build_dhs_survey_integration_report(survey=_survey(), hr=hr, gps=gps, gc=gc)

    assert report.summary["hr_missing_cluster_rows"] == 1
    support = _qa(report, "dhs.integration.cluster_support")
    assert support.state == "YELLOW"
    assert support.metrics["missing_cluster_identity_rows"] == 1
