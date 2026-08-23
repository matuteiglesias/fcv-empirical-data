from pathlib import Path

import pandas as pd
import pytest
from empirical_contracts import AuthorityLevel
from spatial_foundation import DataRoot

from fcv_empirical.surveys.dhs_hr import (
    DhsHrMetadata,
    STANDARD_DHS_HR_COLUMNS,
    build_dhs_survey_id,
    iter_dhs_hr_design_records,
    materialize_dhs_hr_silver,
    normalize_dhs_hr,
    register_dhs_hr_snapshot,
)


def _metadata(source_file_name: str = "ZZHR71FL.csv") -> DhsHrMetadata:
    return DhsHrMetadata(
        dhs_survey_id="ZZ2020DHS",
        country_iso3="ZZZ",
        country="Synthetic Republic",
        survey_year=2020,
        survey_phase="DHS-VII",
        release="synthetic-release-v1",
        source_file_name=source_file_name,
    )


def _raw() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "hhid": ["001001", "001002", "002001"],
            "hv001": [1, 1, 2],
            "hv002": [1, 2, 1],
            "hv005": [1_000_000, 750_000, 1_250_000],
            "hv021": [101, 101, 202],
            "hv022": [11, 11, 22],
            "hv201": [11, 21, 31],
            "country_specific_note": ["a", "b", "c"],
        }
    )


def _normalized(tmp_path: Path, raw: pd.DataFrame | None = None):
    source = tmp_path / "ZZHR71FL.csv"
    frame = _raw() if raw is None else raw
    frame.to_csv(source, index=False)
    snapshot = register_dhs_hr_snapshot(source, release="synthetic-release-v1")
    return normalize_dhs_hr(
        frame,
        metadata=_metadata(),
        snapshot=snapshot,
        source_path=source,
        column_map=STANDARD_DHS_HR_COLUMNS,
    )


def _metric(result, check_id: str, metric: str):
    qa = next(item for item in result.qa if item.check_id == check_id)
    return qa.metrics[metric]


def test_survey_identity_comes_from_verified_metadata_not_filename():
    first = _metadata("ZZHR71FL.csv")
    second = _metadata("renamed-local-copy.csv")

    assert build_dhs_survey_id(first) == "dhs-ZZ2020DHS"
    assert build_dhs_survey_id(second) == build_dhs_survey_id(first)

    with pytest.raises(ValueError, match="stable source metadata token"):
        DhsHrMetadata(
            dhs_survey_id="unresolved survey id",
            country_iso3="ZZZ",
            country="Synthetic Republic",
            survey_year=2020,
            survey_phase="DHS-VII",
            release="synthetic-release-v1",
            source_file_name="ZZHR71FL.csv",
        )


def test_household_grain_design_facts_and_source_variables_survive(tmp_path: Path):
    raw = _raw()
    result = _normalized(tmp_path, raw)

    assert len(result.frame) == len(raw)
    assert result.frame["household_id"].tolist() == raw["hhid"].tolist()
    assert result.frame["cluster_id"].tolist() == ["1", "1", "2"]
    assert result.frame["source_household_weight"].tolist() == raw["hv005"].tolist()
    assert result.frame["psu_id"].tolist() == ["101", "101", "202"]
    assert result.frame["stratum_id"].tolist() == ["11", "11", "22"]
    assert result.frame["hv201"].tolist() == raw["hv201"].tolist()
    assert result.frame["country_specific_note"].tolist() == ["a", "b", "c"]

    cluster_one = result.frame.loc[result.frame["cluster_id"] == "1"]
    assert len(cluster_one) == 2
    assert cluster_one["household_id"].nunique() == 2

    design = list(iter_dhs_hr_design_records(result.frame))
    assert len(design) == len(raw)
    assert design[0].natural_grain == "household"
    assert design[0].source_weight_variable == "hv005"
    assert design[0].source_weight_value == 1_000_000
    assert design[0].normalized_weight_value is None
    assert design[0].psu_id == "101"
    assert design[0].stratum_id == "11"


def test_identity_design_and_weight_anomalies_remain_visible(tmp_path: Path):
    raw = _raw().copy()
    raw.loc[1, "hhid"] = raw.loc[0, "hhid"]
    raw.loc[2, "hv005"] = None
    raw.loc[0, "hv005"] = 0
    raw.loc[2, "hv001"] = None
    raw.loc[1, "hv022"] = None

    result = _normalized(tmp_path, raw)

    assert len(result.frame) == 3
    assert result.frame["household_id"].tolist()[:2] == ["001001", "001001"]
    assert _metric(result, "dhs.hr.household_identity", "duplicate_household_id_rows") == 2
    assert _metric(result, "dhs.hr.cluster_identity", "missing_cluster_ids") == 1
    assert _metric(result, "dhs.hr.cluster_identity", "cluster_count") == 1
    assert _metric(result, "dhs.hr.source_weight", "missing_weights") == 1
    assert _metric(result, "dhs.hr.source_weight", "nonpositive_weights") == 1
    assert _metric(result, "dhs.hr.stratum", "missing_stratum_ids") == 1


def test_missing_household_id_is_visible_and_row_survives(tmp_path: Path):
    raw = _raw().copy()
    raw.loc[1, "hhid"] = None

    result = _normalized(tmp_path, raw)

    assert len(result.frame) == 3
    assert pd.isna(result.frame.loc[1, "household_id"])
    assert _metric(result, "dhs.hr.household_identity", "missing_household_ids") == 1
    assert result.frame.loc[1, "household_observation_id"].startswith(
        result.file_link.source_snapshot_id
    )


def test_no_household_values_are_aggregated(tmp_path: Path):
    raw = _raw()
    result = _normalized(tmp_path, raw)

    source_columns = list(raw.columns)
    assert result.frame[source_columns].reset_index(drop=True).equals(raw.reset_index(drop=True))
    assert _metric(result, "dhs.hr.row_retention", "input_rows") == 3
    assert _metric(result, "dhs.hr.row_retention", "output_rows") == 3


def test_materialization_records_l3_lineage_hash_and_zero_padded_ids(tmp_path: Path):
    source = tmp_path / "ZZHR71FL.csv"
    _raw().to_csv(source, index=False)
    data_root = DataRoot.from_path(tmp_path / "data")

    snapshot, silver, manifest, dataset, output = materialize_dhs_hr_silver(
        source_path=source,
        metadata=_metadata(),
        column_map=STANDARD_DHS_HR_COLUMNS,
        data_root=data_root,
        run_id="dhs-hr-fixture",
        code_commit="deadbeef",
    )

    assert output.exists()
    assert output.parts[-5:-1] == (
        "surveys",
        "dhs",
        "dhs-ZZ2020DHS",
        snapshot.snapshot_id,
    )
    materialized = pd.read_parquet(output)
    assert len(materialized) == len(_raw())
    assert materialized["hhid"].tolist() == ["001001", "001002", "002001"]
    assert materialized["household_id"].tolist() == ["001001", "001002", "002001"]
    assert dataset.authority == AuthorityLevel.L3_REBUILT
    assert dataset.content_sha256 is not None
    assert dataset.grain.keys == ("survey_id", "household_id")
    assert manifest.inputs == (snapshot,)
    assert manifest.outputs == (dataset,)
    assert manifest.parameters["source_weight_transformation"] is None
    assert manifest.parameters["aggregation"] is None
    assert silver.file_link.source_snapshot_id == snapshot.snapshot_id

    run = data_root.run("fcv-empirical-data", "dhs-hr-fixture")
    assert (run / "artifacts/catalog/dhs_survey.json").exists()
    assert (run / "artifacts/catalog/dhs_hr_file_link.json").exists()
    assert (run / "artifacts/mappings/dhs_hr_source_columns.json").exists()


def test_registered_snapshot_mutation_fails_closed(tmp_path: Path):
    source = tmp_path / "ZZHR71FL.csv"
    _raw().to_csv(source, index=False)
    snapshot = register_dhs_hr_snapshot(source, release="synthetic-release-v1")
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="changed after snapshot registration"):
        materialize_dhs_hr_silver(
            source_path=source,
            metadata=_metadata(),
            column_map=STANDARD_DHS_HR_COLUMNS,
            data_root=DataRoot.from_path(tmp_path / "data"),
            run_id="dhs-hr-drift",
            source_snapshot=snapshot,
        )


def test_verified_source_filename_mismatch_fails_instead_of_guessing(tmp_path: Path):
    source = tmp_path / "unexpected.csv"
    _raw().to_csv(source, index=False)

    with pytest.raises(ValueError, match="verified DHS source_file_name"):
        materialize_dhs_hr_silver(
            source_path=source,
            metadata=_metadata(),
            column_map=STANDARD_DHS_HR_COLUMNS,
            data_root=DataRoot.from_path(tmp_path / "data"),
            run_id="dhs-hr-name-mismatch",
        )
