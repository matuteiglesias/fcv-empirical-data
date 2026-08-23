from pathlib import Path

import pandas as pd
import pytest
from empirical_contracts import AuthorityLevel, DataLayer, DatasetRef, GrainSpec
from spatial_foundation import DataRoot, sha256_file

from fcv_empirical.surveys.catalog import SurveyCatalogEntry
from fcv_empirical.surveys.dhs_variables import (
    DHS_VII_STANDARD_HR_REGISTRY,
    DhsVariableDefinition,
    build_dhs_household_measurements,
    materialize_dhs_household_measurements,
)


def _survey(phase: str = "DHS-VII") -> SurveyCatalogEntry:
    return SurveyCatalogEntry(
        survey_id="dhs-ZZ2020DHS",
        source_family="dhs",
        country_iso3="ZZZ",
        survey_year=2020,
        survey_phase=phase,
        release="synthetic-release-v1",
    )


def _hr_dataset(content_sha256: str | None = None) -> DatasetRef:
    return DatasetRef(
        dataset_id="surveys.dhs.hr_households",
        version="hr-fixture",
        schema_version="dhs-hr-household-silver-v2",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("source_row_id",)),
        content_sha256=content_sha256,
    )


def _hr() -> pd.DataFrame:
    survey_id = _survey().survey_id
    return pd.DataFrame(
        {
            "survey_id": [survey_id] * 4,
            "source_row_id": ["row-1", "row-2", "row-3", "row-4"],
            "household_id": ["001001", "001002", "002001", "002002"],
            "hv206": [0, 1, 9, 2],
            "hv270": [1, 5, 3, 2],
            "hv201": [11, 99, 43, 72],
        }
    )


def _measurement(result, measurement_id: str) -> pd.DataFrame:
    return result.frame.loc[result.frame["measurement_id"] == measurement_id].reset_index(drop=True)


def test_registry_uses_verified_standard_variables_not_historical_mislabels():
    by_measure = {item.measurement_id: item for item in DHS_VII_STANDARD_HR_REGISTRY}

    assert by_measure["dhs.household.electricity_access"].source_variable == "HV206"
    assert by_measure["dhs.household.wealth_quintile"].source_variable == "HV270"
    assert by_measure["dhs.household.drinking_water_source_code"].source_variable == "HV201"
    assert "HV215" not in {item.source_variable for item in DHS_VII_STANDARD_HR_REGISTRY}
    assert "HV040" not in {item.source_variable for item in DHS_VII_STANDARD_HR_REGISTRY}
    assert all(item.codebook_provenance for item in DHS_VII_STANDARD_HR_REGISTRY)


def test_electricity_missing_and_unknown_codes_never_become_zero():
    result = build_dhs_household_measurements(
        _hr(), survey=_survey(), hr_dataset=_hr_dataset()
    )
    electricity = _measurement(result, "dhs.household.electricity_access")

    assert electricity["measurement_status"].tolist() == [
        "observed",
        "observed",
        "source_missing_code",
        "unmapped_source_code",
    ]
    assert electricity["normalized_value"].tolist()[:2] == ["false", "true"]
    assert electricity.loc[2:, "normalized_numeric_value"].isna().all()
    assert electricity.loc[2:, "normalized_value"].isna().all()


def test_water_source_remains_country_specific_source_code_not_improved_water_claim():
    result = build_dhs_household_measurements(
        _hr(), survey=_survey(), hr_dataset=_hr_dataset()
    )
    water = _measurement(result, "dhs.household.drinking_water_source_code")

    assert water["comparability_status"].eq("country_specific_categories").all()
    assert water["normalized_value"].tolist()[0] == "11"
    assert pd.isna(water["normalized_value"].tolist()[1])
    assert water["measurement_status"].tolist()[1] == "source_missing_code"
    assert water["normalized_value"].tolist()[3] == "72"
    assert water["measurement_status"].tolist()[3] == "observed"
    assert "improved" not in " ".join(result.frame.columns).lower()


def test_wealth_quintile_is_ordered_but_explicitly_survey_relative():
    result = build_dhs_household_measurements(
        _hr(), survey=_survey(), hr_dataset=_hr_dataset()
    )
    wealth = _measurement(result, "dhs.household.wealth_quintile")

    assert wealth["normalized_numeric_value"].tolist() == [1.0, 5.0, 3.0, 2.0]
    assert wealth["value_label"].tolist() == ["Poorest", "Richest", "Middle", "Poorer"]
    assert wealth["comparability_status"].eq("survey_relative_scale").all()


def test_registry_phase_mismatch_fails_instead_of_assuming_cross_phase_comparability():
    with pytest.raises(ValueError, match="not validated for survey phase"):
        build_dhs_household_measurements(
            _hr(), survey=_survey("DHS-VI"), hr_dataset=_hr_dataset()
        )


def test_measurement_contracts_contain_no_experiment_role_vocabulary():
    result = build_dhs_household_measurements(
        _hr(), survey=_survey(), hr_dataset=_hr_dataset()
    )

    assert {contract.measure_id for contract in result.contracts} == {
        "dhs.household.electricity_access",
        "dhs.household.wealth_quintile",
        "dhs.household.drinking_water_source_code",
    }
    assert all("experiment_role" not in contract.parameters for contract in result.contracts)
    assert all(contract.coverage.absent_row_semantics == "not_observed" for contract in result.contracts)
    assert all(contract.output_grain.keys == ("source_row_id",) for contract in result.contracts)


def test_duplicate_household_id_does_not_destroy_physical_measurement_identity():
    hr = _hr()
    hr.loc[1, "household_id"] = hr.loc[0, "household_id"]

    result = build_dhs_household_measurements(hr, survey=_survey(), hr_dataset=_hr_dataset())

    assert not result.frame[["source_row_id", "measurement_id"]].duplicated().any()
    assert result.frame["household_id"].value_counts().max() > 1


def test_non_default_dataframe_index_cannot_misalign_measurement_values():
    hr = _hr().copy()
    hr.index = [10, 20, 30, 40]

    result = build_dhs_household_measurements(hr, survey=_survey(), hr_dataset=_hr_dataset())
    electricity = _measurement(result, "dhs.household.electricity_access")

    assert electricity["source_row_id"].tolist() == ["row-1", "row-2", "row-3", "row-4"]
    assert electricity["measurement_status"].tolist() == [
        "observed",
        "observed",
        "source_missing_code",
        "unmapped_source_code",
    ]


def test_custom_definition_without_codebook_provenance_is_rejected():
    definition = DhsVariableDefinition(
        survey_phase="DHS-VII",
        recode_family="HR",
        source_variable="HV215",
        measurement_id="dhs.household.electricity_access",
        source_label="unsupported historical interpretation",
        unit="boolean",
        transform="binary_standard",
        comparability_status="unknown",
        allowed_codes=("0", "1"),
    )

    with pytest.raises(ValueError, match="codebook provenance"):
        build_dhs_household_measurements(
            _hr().assign(hv215=[0, 1, 0, 1]),
            survey=_survey(),
            hr_dataset=_hr_dataset(),
            definitions=(definition,),
        )


def test_materialization_requires_exact_hashed_hr_silver(tmp_path: Path):
    hr_path = tmp_path / "hr.parquet"
    _hr().to_parquet(hr_path, index=False)
    dataset = _hr_dataset(sha256_file(hr_path))

    result, manifest, output_dataset, output = materialize_dhs_household_measurements(
        hr_path=hr_path,
        hr_dataset=dataset,
        survey=_survey(),
        data_root=DataRoot.from_path(tmp_path / "data"),
        run_id="dhs-variable-fixture",
    )

    assert output.exists()
    assert len(pd.read_parquet(output)) == len(_hr()) * len(DHS_VII_STANDARD_HR_REGISTRY)
    assert manifest.inputs == (dataset,)
    assert "experiment_roles" not in manifest.parameters
    assert output_dataset.content_sha256 is not None
    assert output_dataset.grain.keys == ("source_row_id", "measurement_id")
    assert result.registry_sha256[:12] in output_dataset.version

    hr_path.write_bytes(hr_path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="do not match"):
        materialize_dhs_household_measurements(
            hr_path=hr_path,
            hr_dataset=dataset,
            survey=_survey(),
            data_root=DataRoot.from_path(tmp_path / "other-data"),
            run_id="dhs-variable-tamper",
        )
