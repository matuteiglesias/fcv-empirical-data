from dataclasses import fields
from datetime import date

from empirical_contracts import SourceFileRef

from fcv_empirical.surveys import (
    SurveyCatalogEntry,
    SurveyDesignRecord,
    SurveyFileLink,
    SurveyGeographyLink,
    SurveyVariableMetadata,
    TemporalSemantics,
)


def test_dhs_household_can_preserve_cluster_and_source_weight() -> None:
    survey = SurveyCatalogEntry(
        survey_id="dhs-ken-2022",
        source_family="dhs",
        country_iso3="KEN",
        survey_year=2022,
        fieldwork_start=date(2022, 2, 1),
        fieldwork_end=date(2022, 7, 31),
        survey_phase="phase-8",
        release="2022-release",
        source_snapshot_id="snapshot-dhs-ken-2022",
    )
    household = SurveyDesignRecord(
        survey_id=survey.survey_id,
        observation_id="household-17-0042",
        natural_grain="household",
        cluster_id="cluster-17",
        psu_id="cluster-17",
        stratum_id="urban-nairobi",
        source_weight_variable="household_weight",
        source_weight_value=153284,
    )

    assert household.natural_grain == "household"
    assert household.cluster_id == "cluster-17"
    assert household.source_weight_variable == "household_weight"
    assert household.normalized_weight_value is None


def test_afrobarometer_respondent_can_preserve_round_ea_and_weight() -> None:
    survey = SurveyCatalogEntry(
        survey_id="afb-nga-r9",
        source_family="afrobarometer",
        country_iso3="NGA",
        survey_year=2022,
        survey_phase="round-9",
        release="round-9-release",
        source_snapshot_id="snapshot-afb-r9",
    )
    respondent = SurveyDesignRecord(
        survey_id=survey.survey_id,
        observation_id="respondent-00017",
        natural_grain="respondent",
        psu_id="ea-044",
        stratum_id="ng-stratum-3",
        source_weight_variable="within_country_weight",
        source_weight_value=0.871,
    )

    assert survey.survey_phase == "round-9"
    assert respondent.natural_grain == "respondent"
    assert respondent.psu_id == "ea-044"


def test_enumeration_area_observation_is_not_respondent_grain() -> None:
    respondent = SurveyDesignRecord(
        survey_id="afb-nga-r9",
        observation_id="respondent-00017",
        natural_grain="respondent",
        psu_id="ea-044",
    )
    ea_observation = SurveyDesignRecord(
        survey_id="afb-nga-r9",
        observation_id="ea-044",
        natural_grain="enumeration_area",
        psu_id="ea-044",
    )

    assert ea_observation.observation_id == respondent.psu_id
    assert ea_observation.natural_grain == "enumeration_area"
    assert ea_observation.natural_grain != respondent.natural_grain


def test_displaced_point_can_keep_multiple_candidate_geographies_and_unmatched_state() -> None:
    candidates = (
        SurveyGeographyLink(
            survey_id="dhs-ken-2022",
            source_object_id="cluster-17",
            source_object_type="survey_cluster",
            geo_uid="gadm:KEN:adm2:001",
            assignment_status="ambiguous_multiple",
            assignment_method="spatial_foundation.assign_points",
            uncertainty_metadata={"displacement_radius_km": 5.0, "candidate_count": 2},
        ),
        SurveyGeographyLink(
            survey_id="dhs-ken-2022",
            source_object_id="cluster-17",
            source_object_type="survey_cluster",
            geo_uid="gadm:KEN:adm2:002",
            assignment_status="ambiguous_multiple",
            assignment_method="spatial_foundation.assign_points",
            uncertainty_metadata={"displacement_radius_km": 5.0, "candidate_count": 2},
        ),
    )
    unmatched = SurveyGeographyLink(
        survey_id="dhs-ken-2022",
        source_object_id="cluster-99",
        source_object_type="survey_cluster",
        geo_uid=None,
        assignment_status="unmatched_outside",
        assignment_method="spatial_foundation.assign_points",
    )

    assert {row.geo_uid for row in candidates} == {
        "gadm:KEN:adm2:001",
        "gadm:KEN:adm2:002",
    }
    assert all(row.assignment_status == "ambiguous_multiple" for row in candidates)
    assert unmatched.geo_uid is None


def test_variable_metadata_keeps_temporal_semantics_explicit_and_unknown_by_default() -> None:
    static = SurveyVariableMetadata(
        source_family="dhs",
        recode="household",
        source_variable="roof_material",
        source_label="Main roof material",
        natural_grain="household",
        source_value_type="categorical",
        temporal_semantics=TemporalSemantics.STATIC,
    )
    survey_time = SurveyVariableMetadata(
        source_family="afrobarometer",
        round_id="round-9",
        source_variable="employment_status",
        source_label="Current employment status",
        natural_grain="respondent",
        source_value_type="categorical",
        temporal_semantics=TemporalSemantics.SURVEY_TIME,
    )
    annual = SurveyVariableMetadata(
        source_family="derived_context",
        instrument="annual-context",
        source_variable="rainfall_total",
        source_label="Annual rainfall total",
        natural_grain="survey_cluster",
        source_value_type="float",
        temporal_semantics=TemporalSemantics.ANNUAL,
    )
    unknown = SurveyVariableMetadata(
        source_family="legacy_survey",
        source_variable="v17",
        source_label=None,
        natural_grain="person",
        source_value_type="integer",
    )

    assert static.temporal_semantics is TemporalSemantics.STATIC
    assert survey_time.temporal_semantics is TemporalSemantics.SURVEY_TIME
    assert annual.temporal_semantics is TemporalSemantics.ANNUAL
    assert unknown.temporal_semantics is TemporalSemantics.UNKNOWN


def test_two_source_files_can_belong_to_one_survey() -> None:
    first = SurveyFileLink(
        survey_id="dhs-ken-2022",
        source_snapshot_id="snapshot-dhs-ken-2022",
        source_file=SourceFileRef(
            path="source/household_records.dat",
            sha256="a" * 64,
            size_bytes=1200,
        ),
        instrument="household-records",
    )
    second = SurveyFileLink(
        survey_id="dhs-ken-2022",
        source_snapshot_id="snapshot-dhs-ken-2022",
        source_file=SourceFileRef(
            path="source/person_records.dat",
            sha256="b" * 64,
            size_bytes=2400,
        ),
        instrument="person-records",
    )

    assert first.survey_id == second.survey_id
    assert first.source_file.path != second.source_file.path
    assert first.instrument != second.instrument


def test_substrate_has_no_experiment_role_fields() -> None:
    field_names = {
        field.name
        for model in (
            SurveyCatalogEntry,
            SurveyDesignRecord,
            SurveyFileLink,
            SurveyGeographyLink,
            SurveyVariableMetadata,
        )
        for field in fields(model)
    }
    forbidden = {"outcome", "treatment", "control", "covariate", "regression_weight", "estimator"}

    assert field_names.isdisjoint(forbidden)
