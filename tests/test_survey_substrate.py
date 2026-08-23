import math
from dataclasses import fields
from datetime import date

import pytest
from empirical_contracts import GeographySpec, SourceFileRef, SourceSnapshotRef
from spatial_foundation.geography import MembershipStatus

from fcv_empirical.surveys import (
    SurveyCatalogEntry,
    SurveyDesignRecord,
    SurveyFileLink,
    SurveyGeographyLink,
    SurveyVariableMetadata,
    TemporalSemantics,
    validate_survey_file_link,
)


def _gadm_adm2() -> GeographySpec:
    return GeographySpec(
        provider="gadm",
        version="4.1",
        scheme="admin",
        level="2",
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
    assert household.weight_normalization_method is None


def test_afrobarometer_respondent_can_preserve_round_ea_and_weight() -> None:
    survey = SurveyCatalogEntry(
        survey_id="afb-nga-r9",
        source_family="afrobarometer",
        country_iso3="NGA",
        survey_year=2022,
        survey_phase="round-9",
        release="round-9-release",
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
    geography = _gadm_adm2()
    candidates = (
        SurveyGeographyLink(
            survey_id="dhs-ken-2022",
            source_object_id="cluster-17",
            source_object_type="survey_cluster",
            geography=geography,
            geo_uid="gadm:4.1:adm2:KEN.1.1_1",
            assignment_status=MembershipStatus.AMBIGUOUS_MULTIPLE,
            assignment_method="spatial_foundation.assign_points",
            uncertainty_metadata={"displacement_radius_km": 5.0, "candidate_count": 2},
        ),
        SurveyGeographyLink(
            survey_id="dhs-ken-2022",
            source_object_id="cluster-17",
            source_object_type="survey_cluster",
            geography=geography,
            geo_uid="gadm:4.1:adm2:KEN.1.2_1",
            assignment_status=MembershipStatus.AMBIGUOUS_MULTIPLE,
            assignment_method="spatial_foundation.assign_points",
            uncertainty_metadata={"displacement_radius_km": 5.0, "candidate_count": 2},
        ),
    )
    unmatched = SurveyGeographyLink(
        survey_id="dhs-ken-2022",
        source_object_id="cluster-99",
        source_object_type="survey_cluster",
        geography=geography,
        geo_uid=None,
        assignment_status=MembershipStatus.UNMATCHED_OUTSIDE,
        assignment_method="spatial_foundation.assign_points",
    )

    assert {row.geo_uid for row in candidates} == {
        "gadm:4.1:adm2:KEN.1.1_1",
        "gadm:4.1:adm2:KEN.1.2_1",
    }
    assert all(
        row.assignment_status is MembershipStatus.AMBIGUOUS_MULTIPLE for row in candidates
    )
    assert all(row.geography == geography for row in candidates)
    assert unmatched.geo_uid is None


def test_geography_link_rejects_contradictory_or_private_status_semantics() -> None:
    geography = _gadm_adm2()

    with pytest.raises(ValueError, match="require geo_uid"):
        SurveyGeographyLink(
            survey_id="dhs-ken-2022",
            source_object_id="cluster-17",
            source_object_type="survey_cluster",
            geography=geography,
            geo_uid=None,
            assignment_status=MembershipStatus.MATCHED_UNIQUE,
            assignment_method="spatial_foundation.assign_points",
        )

    with pytest.raises(ValueError, match="must not carry geo_uid"):
        SurveyGeographyLink(
            survey_id="dhs-ken-2022",
            source_object_id="cluster-17",
            source_object_type="survey_cluster",
            geography=geography,
            geo_uid="gadm:4.1:adm2:KEN.1.1_1",
            assignment_status=MembershipStatus.UNMATCHED_OUTSIDE,
            assignment_method="spatial_foundation.assign_points",
        )

    with pytest.raises(ValueError, match="MembershipStatus"):
        SurveyGeographyLink(
            survey_id="dhs-ken-2022",
            source_object_id="cluster-17",
            source_object_type="survey_cluster",
            geography=geography,
            geo_uid=None,
            assignment_status="survey_specific_guess",
            assignment_method="local_guess",
        )


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


def test_one_survey_can_link_files_from_distinct_source_snapshots() -> None:
    survey = SurveyCatalogEntry(
        survey_id="dhs-ken-2022",
        source_family="dhs",
        country_iso3="KEN",
        survey_year=2022,
        survey_phase="phase-8",
        release="2022-survey",
    )
    household_file = SourceFileRef(
        path="source/household_records.dat",
        sha256="a" * 64,
        size_bytes=1200,
    )
    gps_file = SourceFileRef(
        path="source/gps_clusters.shp",
        sha256="b" * 64,
        size_bytes=2400,
    )
    hr_snapshot = SourceSnapshotRef(
        source="dhs_hr",
        release="hr-release",
        snapshot_id="snapshot-dhs-ken-2022-hr",
        files=(household_file,),
    )
    gps_snapshot = SourceSnapshotRef(
        source="dhs_gps",
        release="gps-release",
        snapshot_id="snapshot-dhs-ken-2022-gps",
        files=(gps_file,),
    )
    hr_link = SurveyFileLink(
        survey_id=survey.survey_id,
        source_snapshot_id=hr_snapshot.snapshot_id,
        source_file=household_file,
        instrument="household-records",
    )
    gps_link = SurveyFileLink(
        survey_id=survey.survey_id,
        source_snapshot_id=gps_snapshot.snapshot_id,
        source_file=gps_file,
        instrument="gps-clusters",
    )

    validate_survey_file_link(survey, hr_link, hr_snapshot)
    validate_survey_file_link(survey, gps_link, gps_snapshot)

    assert hr_link.survey_id == gps_link.survey_id == survey.survey_id
    assert hr_link.source_snapshot_id != gps_link.source_snapshot_id
    assert not hasattr(survey, "source_snapshot_id")


def test_survey_file_link_fails_if_file_is_not_in_declared_snapshot() -> None:
    survey = SurveyCatalogEntry(
        survey_id="dhs-ken-2022",
        source_family="dhs",
        country_iso3="KEN",
        survey_year=2022,
        release="2022-survey",
    )
    registered = SourceFileRef(
        path="source/hr.dat",
        sha256="c" * 64,
        size_bytes=1200,
    )
    different = SourceFileRef(
        path="source/ge.dat",
        sha256="d" * 64,
        size_bytes=800,
    )
    snapshot = SourceSnapshotRef(
        source="dhs_hr",
        release="hr-release",
        snapshot_id="snapshot-hr",
        files=(registered,),
    )
    link = SurveyFileLink(
        survey_id=survey.survey_id,
        source_snapshot_id=snapshot.snapshot_id,
        source_file=different,
        instrument="gps",
    )

    with pytest.raises(ValueError, match="not a member"):
        validate_survey_file_link(survey, link, snapshot)


def test_normalized_weight_requires_explicit_transformation_provenance() -> None:
    with pytest.raises(ValueError, match="normalization_method"):
        SurveyDesignRecord(
            survey_id="dhs-ken-2022",
            observation_id="household-1",
            natural_grain="household",
            source_weight_variable="hv005",
            source_weight_value=153284,
            normalized_weight_value=0.153284,
        )

    record = SurveyDesignRecord(
        survey_id="dhs-ken-2022",
        observation_id="household-1",
        natural_grain="household",
        source_weight_variable="hv005",
        source_weight_value=153284,
        normalized_weight_value=0.153284,
        weight_normalization_method="divide source weight by 1_000_000",
    )
    assert math.isclose(record.normalized_weight_value or 0.0, 0.153284)


def test_catalog_rejects_noncanonical_country_identity() -> None:
    with pytest.raises(ValueError, match="uppercase ISO"):
        SurveyCatalogEntry(
            survey_id="dhs-ken-2022",
            source_family="dhs",
            country_iso3="ken",
            survey_year=2022,
            release="2022-survey",
        )


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
    forbidden = {
        "outcome",
        "treatment",
        "control",
        "covariate",
        "regression_weight",
        "estimator",
    }

    assert field_names.isdisjoint(forbidden)
