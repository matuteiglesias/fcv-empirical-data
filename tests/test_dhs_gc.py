from pathlib import Path

import pandas as pd
import pytest
from empirical_contracts import DataLayer, DatasetRef, SourceSnapshotRef
from spatial_foundation import DataRoot

from fcv_empirical.surveys.catalog import SurveyCatalogEntry
from fcv_empirical.surveys.dhs_gc import (
    GCTemporalRule,
    build_dhs_gc_measurements,
    materialize_dhs_gc_measurements,
    materialize_dhs_gc_silver,
    normalize_dhs_gc_clusters,
    register_dhs_gc_snapshot,
    resolve_gc_temporal_metadata,
)
from fcv_empirical.surveys.variables import TemporalSemantics


def _survey(survey_id: str = "KE2015DHS", year: int = 2015) -> SurveyCatalogEntry:
    return SurveyCatalogEntry(
        survey_id=survey_id,
        source_family="DHS",
        country_iso3="KEN",
        survey_year=year,
        release="DHS-VII",
    )


def _source(tmp_path: Path, name: str, frame: pd.DataFrame, release: str = "GC-test-v1"):
    path = tmp_path / name
    frame.to_csv(path, index=False)
    snapshot = register_dhs_gc_snapshot(path, release=release)
    return path, snapshot


def _rules() -> tuple[GCTemporalRule, ...]:
    provenance = {"registry": "synthetic-test-codebook"}
    return (
        GCTemporalRule(
            r"elevation",
            TemporalSemantics.STATIC,
            codebook_provenance=provenance,
        ),
        GCTemporalRule(
            r"rain_(?P<year>\d{4})",
            TemporalSemantics.ANNUAL,
            year_group="year",
            codebook_provenance=provenance,
        ),
        GCTemporalRule(
            r"climatology_mean",
            TemporalSemantics.CLIMATOLOGY,
            codebook_provenance=provenance,
        ),
        GCTemporalRule(
            r"survey_signal",
            TemporalSemantics.SURVEY_TIME,
            codebook_provenance=provenance,
        ),
    )


def test_cluster_silver_preserves_source_truth_identity_and_missingness(tmp_path: Path) -> None:
    raw = pd.DataFrame(
        {
            "DHSID": ["KE001", "KE002"],
            "elevation": [120.0, 340.0],
            "rain_2015": [1.5, None],
            "mystery": ["x", None],
        }
    )
    _path, snapshot = _source(tmp_path, "gc.csv", raw)
    result = normalize_dhs_gc_clusters(raw, survey=_survey(), snapshot=snapshot)

    assert result.frame["survey_id"].tolist() == ["KE2015DHS", "KE2015DHS"]
    assert result.frame["cluster_id"].tolist() == ["KE001", "KE002"]
    assert result.frame["source_release"].tolist() == ["GC-test-v1", "GC-test-v1"]
    assert result.frame["source_snapshot_id"].nunique() == 1
    assert set(result.raw_column_map) == set(raw.columns)
    assert result.frame["source__DHSID"].tolist() == ["KE001", "KE002"]
    assert pd.isna(result.frame.loc[1, "source__rain_2015"])
    assert len(result.frame) == len(raw)
    assert "GID" not in result.frame.columns
    assert "geo_uid" not in result.frame.columns


def test_explicit_temporal_registry_never_assigns_survey_year_by_default(tmp_path: Path) -> None:
    raw = pd.DataFrame(
        {
            "DHSID": ["KE001"],
            "elevation": [120.0],
            "rain_2015": [1.5],
            "climatology_mean": [22.0],
            "survey_signal": [4.0],
            "mystery": [9.0],
        }
    )
    _path, snapshot = _source(tmp_path, "gc.csv", raw)
    silver = normalize_dhs_gc_clusters(raw, survey=_survey(), snapshot=snapshot)
    result = build_dhs_gc_measurements(silver, survey=_survey(), temporal_rules=_rules())
    frame = result.frame.set_index("source_variable")

    assert frame.loc["elevation", "temporal_semantics"] == "static"
    assert pd.isna(frame.loc["elevation", "measurement_year"])
    assert frame.loc["rain_2015", "temporal_semantics"] == "annual"
    assert frame.loc["rain_2015", "measurement_year"] == 2015
    assert frame.loc["rain_2015", "source_time_token"] == "2015"
    assert frame.loc["climatology_mean", "temporal_semantics"] == "climatology"
    assert pd.isna(frame.loc["climatology_mean", "measurement_year"])
    assert frame.loc["survey_signal", "temporal_semantics"] == "survey_time"
    assert pd.isna(frame.loc["survey_signal", "measurement_year"])
    assert frame.loc["mystery", "temporal_semantics"] == "unknown"
    assert pd.isna(frame.loc["mystery", "measurement_year"])
    assert result.temporal_report.unknown_variables == ("mystery",)


def test_static_value_across_surveys_does_not_become_fake_annual_series(tmp_path: Path) -> None:
    raw = pd.DataFrame({"DHSID": ["KE001"], "elevation": [120.0]})
    _path_a, snapshot_a = _source(tmp_path, "gc-a.csv", raw, release="GC-a")
    _path_b, snapshot_b = _source(tmp_path, "gc-b.csv", raw, release="GC-b")
    survey_a = _survey("KE2010DHS", 2010)
    survey_b = _survey("KE2015DHS", 2015)

    result_a = build_dhs_gc_measurements(
        normalize_dhs_gc_clusters(raw, survey=survey_a, snapshot=snapshot_a),
        survey=survey_a,
        temporal_rules=_rules(),
    )
    result_b = build_dhs_gc_measurements(
        normalize_dhs_gc_clusters(raw, survey=survey_b, snapshot=snapshot_b),
        survey=survey_b,
        temporal_rules=_rules(),
    )
    combined = pd.concat([result_a.frame, result_b.frame], ignore_index=True)

    assert combined["temporal_semantics"].tolist() == ["static", "static"]
    assert combined["measurement_year"].isna().all()
    assert combined["source_time_token"].isna().all()
    assert combined["source_value"].tolist() == ["120.0", "120.0"]


def test_missing_gc_stays_missing_and_coverage_is_cluster_availability(tmp_path: Path) -> None:
    raw = pd.DataFrame(
        {
            "DHSID": ["KE001", "KE002", "KE003"],
            "rain_2015": [1.0, None, 3.0],
        }
    )
    _path, snapshot = _source(tmp_path, "gc.csv", raw)
    silver = normalize_dhs_gc_clusters(raw, survey=_survey(), snapshot=snapshot)
    result = build_dhs_gc_measurements(silver, survey=_survey(), temporal_rules=_rules())

    missing = result.frame.loc[result.frame["cluster_id"] == "KE002", "source_value"]
    assert missing.isna().all()
    coverage = result.coverage.iloc[0]
    assert coverage["cluster_count"] == 3
    assert coverage["observed_cluster_count"] == 2
    assert coverage["missing_cluster_count"] == 1
    assert coverage["coverage_scope"] == "dhs_cluster_measurement_availability"
    assert not any(column.lower().startswith("gid") for column in result.coverage.columns)


def test_time_parsing_reports_impossible_years_and_rule_conflicts() -> None:
    rules = (
        GCTemporalRule(
            r"rain_(?P<year>\d{4})",
            TemporalSemantics.ANNUAL,
            year_group="year",
        ),
        GCTemporalRule(r"elevation", TemporalSemantics.STATIC),
        GCTemporalRule(r"elevation", TemporalSemantics.CLIMATOLOGY),
    )
    _variables, temporal, report, qa = resolve_gc_temporal_metadata(
        ["rain_0000", "elevation", "unregistered"],
        source_family="DHS",
        rules=rules,
    )
    by_name = {item.source_variable: item for item in temporal}

    assert report.impossible_year_variables == ("rain_0000",)
    assert report.parse_conflict_variables == ("elevation",)
    assert by_name["rain_0000"].measurement_year is None
    assert by_name["elevation"].temporal_semantics is TemporalSemantics.UNKNOWN
    assert "unregistered" in report.unknown_variables
    parse_qa = next(item for item in qa if item.check_id == "dhs_gc.temporal.year_parse")
    assert parse_qa.state == "RED"


def test_duplicate_or_missing_cluster_identity_is_not_silently_repaired(tmp_path: Path) -> None:
    _path, snapshot = _source(
        tmp_path,
        "gc.csv",
        pd.DataFrame({"DHSID": ["KE001"], "elevation": [1.0]}),
    )
    with pytest.raises(ValueError, match="duplicate cluster"):
        normalize_dhs_gc_clusters(
            pd.DataFrame({"DHSID": ["KE001", "KE001"], "elevation": [1.0, 2.0]}),
            survey=_survey(),
            snapshot=snapshot,
        )
    with pytest.raises(ValueError, match="without cluster identity"):
        normalize_dhs_gc_clusters(
            pd.DataFrame({"DHSID": ["KE001", None], "elevation": [1.0, 2.0]}),
            survey=_survey(),
            snapshot=snapshot,
        )


def test_materialization_keeps_wide_silver_and_long_view_as_distinct_lineage(
    tmp_path: Path,
) -> None:
    raw = pd.DataFrame(
        {
            "DHSID": ["KE001", "KE002"],
            "elevation": [120.0, 340.0],
            "rain_2015": [1.5, None],
        }
    )
    source_path = tmp_path / "gc.csv"
    raw.to_csv(source_path, index=False)
    root = DataRoot.from_path(tmp_path / "data")

    snapshot, link, silver, silver_manifest, silver_dataset, silver_path = (
        materialize_dhs_gc_silver(
            source_path=source_path,
            survey=_survey(),
            data_root=root,
            run_id="dhs-gc-silver",
            release="GC-test-v1",
        )
    )
    measurements, measurement_manifest, measurement_dataset, measurement_path = (
        materialize_dhs_gc_measurements(
            silver=silver,
            silver_dataset=silver_dataset,
            survey=_survey(),
            data_root=root,
            run_id="dhs-gc-measurements",
            temporal_rules=_rules(),
        )
    )

    assert isinstance(snapshot, SourceSnapshotRef)
    assert link.survey_id == "KE2015DHS"
    assert link.instrument == "GC"
    assert isinstance(silver_manifest.inputs[0], SourceSnapshotRef)
    assert isinstance(measurement_manifest.inputs[0], DatasetRef)
    assert measurement_manifest.inputs[0].dataset_id == "surveys.dhs_gc.clusters"
    assert silver_dataset.layer == DataLayer.SILVER
    assert measurement_dataset.layer == DataLayer.SILVER
    assert silver_dataset.content_sha256
    assert measurement_dataset.content_sha256
    assert silver_path.exists()
    assert measurement_path.exists()
    assert pd.read_parquet(silver_path)["cluster_id"].tolist() == ["KE001", "KE002"]
    persisted_long = pd.read_parquet(measurement_path)
    assert set(persisted_long["source_variable"]) == {"elevation", "rain_2015"}
    assert pd.isna(
        persisted_long.loc[
            (persisted_long["cluster_id"] == "KE002")
            & (persisted_long["source_variable"] == "rain_2015"),
            "source_value",
        ].iloc[0]
    )
    assert measurements.temporal_report.static_variables == ("elevation",)


def test_registered_source_is_immutable_at_materialization_boundary(tmp_path: Path) -> None:
    raw = pd.DataFrame({"DHSID": ["KE001"], "elevation": [120.0]})
    source_path, snapshot = _source(tmp_path, "gc.csv", raw)
    source_path.write_text("DHSID,elevation\nKE001,999\n", encoding="utf-8")

    with pytest.raises(ValueError, match="changed after snapshot registration"):
        materialize_dhs_gc_silver(
            source_path=source_path,
            survey=_survey(),
            data_root=DataRoot.from_path(tmp_path / "data"),
            run_id="dhs-gc-mutated",
            release="GC-test-v1",
            source_snapshot=snapshot,
        )
