import pandas as pd
import pytest
from empirical_contracts import SourceFileRef, SourceSnapshotRef

from fcv_empirical.surveys.catalog import SurveyCatalogEntry
from fcv_empirical.surveys.dhs_gc import (
    DHS_GC_SOURCE,
    build_dhs_gc_measurements,
    normalize_dhs_gc_clusters,
)


def _survey(source_family: str = "DHS") -> SurveyCatalogEntry:
    return SurveyCatalogEntry(
        survey_id="KE2015DHS",
        source_family=source_family,
        country_iso3="KEN",
        survey_year=2015,
        release="DHS-VII",
    )


def _snapshot() -> SourceSnapshotRef:
    return SourceSnapshotRef(
        source=DHS_GC_SOURCE,
        release="GC-test-v1",
        snapshot_id="gc-snapshot",
        files=(
            SourceFileRef(
                path="/external/KEGC.csv",
                sha256="a" * 64,
                size_bytes=123,
            ),
        ),
    )


def test_dhsid_and_dhsclust_survive_as_identity_not_covariates() -> None:
    raw = pd.DataFrame(
        {
            "DHSID": ["KE201500000001", "KE201500000002"],
            "DHSCLUST": [1, 2],
            "elevation": [120.0, 340.0],
        }
    )
    silver = normalize_dhs_gc_clusters(raw, survey=_survey(), snapshot=_snapshot())
    measurements = build_dhs_gc_measurements(silver, survey=_survey())

    assert silver.frame["dhsid"].tolist() == raw["DHSID"].tolist()
    assert silver.frame["dhsclust"].tolist() == ["1", "2"]
    assert "source__DHSID" in silver.frame.columns
    assert "source__DHSCLUST" in silver.frame.columns
    assert set(measurements.frame["source_variable"]) == {"elevation"}
    assert measurements.frame["dhsid"].tolist() == raw["DHSID"].tolist()
    assert measurements.frame["dhsclust"].tolist() == ["1", "2"]


def test_identity_columns_cannot_be_explicitly_selected_as_covariates() -> None:
    raw = pd.DataFrame(
        {
            "DHSID": ["KE201500000001"],
            "DHSCLUST": [1],
            "elevation": [120.0],
        }
    )
    silver = normalize_dhs_gc_clusters(raw, survey=_survey(), snapshot=_snapshot())

    with pytest.raises(ValueError, match="identity columns"):
        build_dhs_gc_measurements(
            silver,
            survey=_survey(),
            variable_columns=("DHSCLUST",),
        )


def test_gc_adapter_rejects_non_dhs_survey_identity() -> None:
    raw = pd.DataFrame({"DHSID": ["KE201500000001"], "elevation": [120.0]})

    with pytest.raises(ValueError, match="DHS SurveyCatalogEntry"):
        normalize_dhs_gc_clusters(
            raw,
            survey=_survey("afrobarometer"),
            snapshot=_snapshot(),
        )


def test_cluster_coverage_makes_absence_and_missingness_semantics_explicit() -> None:
    raw = pd.DataFrame(
        {
            "DHSID": ["KE201500000001", "KE201500000002"],
            "DHSCLUST": [1, 2],
            "rainfall": [10.0, None],
        }
    )
    silver = normalize_dhs_gc_clusters(raw, survey=_survey(), snapshot=_snapshot())
    coverage = build_dhs_gc_measurements(silver, survey=_survey()).coverage.iloc[0]

    assert coverage["coverage_scope"] == "dhs_cluster_measurement_availability"
    assert coverage["absent_row_semantics"] == "not_observed"
    assert coverage["missing_value_semantics"] == "missing_source_measurement"
    assert coverage["missing_cluster_count"] == 1
