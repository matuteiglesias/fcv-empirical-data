from pathlib import Path

import pandas as pd
import pytest
from empirical_contracts import AuthorityLevel, DataLayer, DatasetRef, GrainSpec
from spatial_foundation import DataRoot, sha256_file

from fcv_empirical.surveys.catalog import SurveyCatalogEntry
from fcv_empirical.surveys.dhs_commissioning import (
    DhsCommissioningSpec,
    materialize_dhs_commissioning_suite,
    run_dhs_commissioning_suite,
)
from fcv_empirical.surveys.dhs_commissioning_benchmarks import (
    nigeria_2018_commissioning_specs,
    uganda_2016_commissioning_specs,
    zambia_2018_commissioning_specs,
)


def _survey(survey_id: str = "dhs-ZZ2020DHS") -> SurveyCatalogEntry:
    return SurveyCatalogEntry(
        survey_id=survey_id,
        source_family="dhs",
        country_iso3="ZZZ",
        survey_year=2020,
        survey_phase="DHS-VII",
        release="synthetic-release-v1",
    )


def _dataset(
    dataset_id: str,
    grain: tuple[str, ...],
    content_sha256: str = "0" * 64,
) -> DatasetRef:
    return DatasetRef(
        dataset_id=dataset_id,
        version="fixture-v1",
        schema_version="fixture-v1",
        layer=DataLayer.SILVER if dataset_id.endswith("hr_households") else DataLayer.GOLD,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=grain),
        content_sha256=content_sha256,
    )


def _refs(
    *,
    hr_hash: str = "0" * 64,
    measurement_hash: str = "1" * 64,
) -> tuple[DatasetRef, DatasetRef]:
    return (
        _dataset("surveys.dhs.hr_households", ("source_row_id",), hr_hash),
        _dataset(
            "surveys.dhs.hr_household_measurements",
            ("source_row_id", "measurement_id"),
            measurement_hash,
        ),
    )


def _hr() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "survey_id": ["dhs-ZZ2020DHS"] * 4,
            "source_row_id": ["row-1", "row-2", "row-3", "row-4"],
            "source_weight_variable": ["hv005"] * 4,
            "source_household_weight": [2, 3, 5, 1],
            "hv012": [1, 1, 1, 2],
            "hv025": [1, 1, 2, 1],
        }
    )


def _electricity() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "survey_id": ["dhs-ZZ2020DHS"] * 4,
            "source_row_id": ["row-1", "row-2", "row-3", "row-4"],
            "measurement_id": ["dhs.household.electricity_access"] * 4,
            "measurement_status": [
                "observed",
                "observed",
                "source_missing_code",
                "observed",
            ],
            "normalized_value": ["false", "true", pd.NA, "false"],
        }
    )


def _electricity_spec(**updates) -> DhsCommissioningSpec:
    payload = dict(
        benchmark_id="dhs.synthetic.electricity",
        survey_id="dhs-ZZ2020DHS",
        measurement_id="dhs.household.electricity_access",
        reference_authority="Synthetic authority",
        reference_publication="Synthetic report",
        reference_publication_id="SYN1",
        reference_url="https://example.invalid/report",
        reference_table="2.4",
        reference_page=1,
        expected_percentages={"no": 27.2727272727, "yes": 27.2727272727},
        category_map={"false": "no", "true": "yes"},
        published_decimals=9,
    )
    payload.update(updates)
    return DhsCommissioningSpec(**payload)


def _qa_state(result, suffix: str) -> str:
    item = next(qa for qa in result.qa if qa.check_id.endswith(suffix))
    return item.state


def test_household_distribution_keeps_missing_weight_in_denominator():
    hr_dataset, measurement_dataset = _refs()
    result = run_dhs_commissioning_suite(
        _hr(),
        _electricity(),
        survey=_survey(),
        hr_dataset=hr_dataset,
        measurement_dataset=measurement_dataset,
        specs=(_electricity_spec(),),
    )

    observed = result.frame.set_index("cell_id")["observed_percent"].to_dict()
    assert observed["no"] == pytest.approx(100 * 3 / 11)
    assert observed["yes"] == pytest.approx(100 * 3 / 11)
    diagnostics = result.diagnostics["dhs.synthetic.electricity"]
    assert diagnostics["weighted_denominator"] == 11
    assert diagnostics["missing_measurement_weight"] == 5
    assert diagnostics["unmapped_measurement_or_category_weight"] == 0
    assert _qa_state(result, "quantitative_recovery") == "GREEN"


def test_unmapped_positive_weight_is_visible_and_blocks_quantitative_recovery():
    hr_dataset, measurement_dataset = _refs()
    measurements = _electricity().copy()
    measurements.loc[3, "measurement_status"] = "unmapped_source_code"
    measurements.loc[3, "normalized_value"] = pd.NA

    result = run_dhs_commissioning_suite(
        _hr(),
        measurements,
        survey=_survey(),
        hr_dataset=hr_dataset,
        measurement_dataset=measurement_dataset,
        specs=(
            _electricity_spec(
                expected_percentages={"no": 100 * 2 / 11, "yes": 100 * 3 / 11}
            ),
        ),
    )

    diagnostics = result.diagnostics["dhs.synthetic.electricity"]
    assert diagnostics["unmapped_measurement_or_category_weight"] == 1
    assert diagnostics["quantitative_recovery"] == "FAIL"
    assert _qa_state(result, "measurement_accounting") == "RED"
    assert _qa_state(result, "quantitative_recovery") == "RED"


def test_population_multiplier_and_domain_are_explicit_source_facts():
    hr = _hr().copy()
    hr["source_household_weight"] = 1
    hr["hv012"] = [1, 1, 9, 2]
    hr["hv025"] = [1, 1, 2, 1]
    measurements = pd.DataFrame(
        {
            "survey_id": ["dhs-ZZ2020DHS"] * 4,
            "source_row_id": ["row-1", "row-2", "row-3", "row-4"],
            "measurement_id": ["dhs.household.wealth_quintile"] * 4,
            "measurement_status": ["observed"] * 4,
            "normalized_value": ["1", "2", "3", "5"],
        }
    )
    spec = DhsCommissioningSpec(
        benchmark_id="dhs.synthetic.urban_wealth",
        survey_id="dhs-ZZ2020DHS",
        measurement_id="dhs.household.wealth_quintile",
        reference_authority="Synthetic authority",
        reference_publication="Synthetic report",
        reference_publication_id="SYN2",
        reference_url="https://example.invalid/report",
        reference_table="2.6",
        reference_page=2,
        expected_percentages={"lowest": 25, "second": 25, "highest": 50},
        category_map={"1": "lowest", "2": "second", "5": "highest"},
        population_multiplier_variable="HV012",
        domain_variable="HV025",
        domain_allowed_values=("1",),
    )
    hr_dataset, measurement_dataset = _refs()

    result = run_dhs_commissioning_suite(
        hr,
        measurements,
        survey=_survey(),
        hr_dataset=hr_dataset,
        measurement_dataset=measurement_dataset,
        specs=(spec,),
    )

    observed = result.frame.set_index("cell_id")["observed_percent"].to_dict()
    assert observed == {"lowest": 25.0, "second": 25.0, "highest": 50.0}
    diagnostics = result.diagnostics["dhs.synthetic.urban_wealth"]
    assert diagnostics["domain_rows"] == 3
    assert diagnostics["domain_excluded_rows"] == 1
    assert diagnostics["weighted_denominator"] == 4
    assert _qa_state(result, "quantitative_recovery") == "GREEN"


def test_release_local_water_map_is_required_at_execution_not_registry_level():
    hr_dataset, measurement_dataset = _refs()
    measurements = _electricity().assign(
        measurement_id="dhs.household.drinking_water_source_code",
        measurement_status="observed",
        normalized_value=["11", "21", "31", "41"],
    )
    spec = DhsCommissioningSpec(
        benchmark_id="dhs.synthetic.water",
        survey_id="dhs-ZZ2020DHS",
        measurement_id="dhs.household.drinking_water_source_code",
        reference_authority="Synthetic authority",
        reference_publication="Synthetic report",
        reference_publication_id="SYN3",
        reference_url="https://example.invalid/report",
        reference_table="2.1",
        reference_page=3,
        expected_percentages={"piped": 100.0},
        require_release_category_map=True,
    )

    with pytest.raises(ValueError, match="release-local category_map"):
        run_dhs_commissioning_suite(
            _hr(),
            measurements,
            survey=_survey(),
            hr_dataset=hr_dataset,
            measurement_dataset=measurement_dataset,
            specs=(spec,),
        )


def test_broken_row_linkage_and_invalid_weights_fail_closed():
    hr_dataset, measurement_dataset = _refs()
    broken = _electricity().iloc[:-1].copy()
    with pytest.raises(ValueError, match="row count"):
        run_dhs_commissioning_suite(
            _hr(),
            broken,
            survey=_survey(),
            hr_dataset=hr_dataset,
            measurement_dataset=measurement_dataset,
            specs=(_electricity_spec(),),
        )

    bad_hr = _hr().copy()
    bad_hr.loc[0, "source_household_weight"] = 0
    with pytest.raises(ValueError, match="strictly positive"):
        run_dhs_commissioning_suite(
            bad_hr,
            _electricity(),
            survey=_survey(),
            hr_dataset=hr_dataset,
            measurement_dataset=measurement_dataset,
            specs=(_electricity_spec(),),
        )


def test_materialization_hash_binds_inputs_and_persists_only_aggregate_evidence(tmp_path: Path):
    hr_path = tmp_path / "hr.parquet"
    measurement_path = tmp_path / "measurements.parquet"
    _hr().to_parquet(hr_path, index=False)
    _electricity().to_parquet(measurement_path, index=False)
    hr_dataset, measurement_dataset = _refs(
        hr_hash=sha256_file(hr_path),
        measurement_hash=sha256_file(measurement_path),
    )
    data_root = DataRoot.from_path(tmp_path / "data")

    result, manifest, dataset, output = materialize_dhs_commissioning_suite(
        hr_path=hr_path,
        hr_dataset=hr_dataset,
        measurement_path=measurement_path,
        measurement_dataset=measurement_dataset,
        survey=_survey(),
        specs=(_electricity_spec(),),
        data_root=data_root,
        run_id="dhs-commissioning-fixture",
        code_commit="deadbeef",
    )

    assert output.exists()
    persisted = pd.read_parquet(output)
    assert set(persisted) == {
        "benchmark_id",
        "survey_id",
        "measurement_id",
        "cell_id",
        "reference_percent",
        "observed_percent",
        "difference_pp",
        "published_decimals",
        "rounding_tolerance_pp",
        "rounding_compatible",
        "category_weight",
        "weighted_denominator",
    }
    assert "source_row_id" not in persisted.columns
    assert manifest.inputs == (hr_dataset, measurement_dataset)
    assert dataset.authority == AuthorityLevel.L2_DERIVED
    assert dataset.grain.keys == ("benchmark_id", "cell_id")
    assert dataset.content_sha256 is not None
    assert manifest.parameters["joined_microdata_persisted"] is False
    assert len(result.frame) == 2

    run = data_root.run("fcv-empirical-data", "dhs-commissioning-fixture")
    assert (run / "artifacts/commissioning/specs.json").exists()
    assert (run / "artifacts/commissioning/diagnostics.json").exists()

    measurement_path.write_bytes(measurement_path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="measurement bytes"):
        materialize_dhs_commissioning_suite(
            hr_path=hr_path,
            hr_dataset=hr_dataset,
            measurement_path=measurement_path,
            measurement_dataset=measurement_dataset,
            survey=_survey(),
            specs=(_electricity_spec(),),
            data_root=DataRoot.from_path(tmp_path / "other-data"),
            run_id="dhs-commissioning-tamper",
        )


def test_predeclared_reference_catalog_has_five_checks_and_no_guessed_water_mapping():
    nigeria = nigeria_2018_commissioning_specs()
    uganda = uganda_2016_commissioning_specs()
    zambia = zambia_2018_commissioning_specs()

    assert len(nigeria) + len(uganda) + len(zambia) == 5
    assert nigeria[0].expected_percentages["yes"] == 59.4
    assert nigeria[1].expected_percentages["tube_well_borehole"] == 37.2
    assert nigeria[1].require_release_category_map is True
    assert nigeria[1].category_map == {}
    assert nigeria[2].population_multiplier_variable == "HV012"
    assert nigeria[2].domain_variable == "HV025"
    assert uganda[0].expected_percentages["yes"] == 28.6
    assert zambia[0].expected_percentages["yes"] == 34.2
