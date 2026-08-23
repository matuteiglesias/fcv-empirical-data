"""Minimal source-native substrate for complex respondent and household surveys."""

from .catalog import SurveyCatalogEntry, SurveyFileLink, validate_survey_file_link
from .design import ObservationGrain, SurveyDesignRecord, WeightValue, validate_observation_grain
from .dhs_gps import (
    DHS_GPS_SOURCE,
    POSSIBLE_GEOGRAPHY_UNDER_DISPLACEMENT,
    REPORTED_COORDINATE_MEMBERSHIP,
    DhsDisplacementPolicy,
    DhsGpsLinkageResult,
    DhsGpsSilverResult,
    DhsReportedMembershipResult,
    assign_dhs_reported_coordinate_membership,
    normalize_dhs_gps_clusters,
    register_dhs_gps_snapshot,
    validate_dhs_gps_linkage,
)
from .dhs_gps_pipeline import (
    materialize_dhs_gps_silver,
    materialize_dhs_reported_coordinate_membership,
)
from .geography import SurveyGeographyLink
from .variables import SurveyVariableMetadata, TemporalSemantics

__all__ = [
    "DHS_GPS_SOURCE",
    "POSSIBLE_GEOGRAPHY_UNDER_DISPLACEMENT",
    "REPORTED_COORDINATE_MEMBERSHIP",
    "DhsDisplacementPolicy",
    "DhsGpsLinkageResult",
    "DhsGpsSilverResult",
    "DhsReportedMembershipResult",
    "ObservationGrain",
    "SurveyCatalogEntry",
    "SurveyDesignRecord",
    "SurveyFileLink",
    "SurveyGeographyLink",
    "SurveyVariableMetadata",
    "TemporalSemantics",
    "WeightValue",
    "assign_dhs_reported_coordinate_membership",
    "materialize_dhs_gps_silver",
    "materialize_dhs_reported_coordinate_membership",
    "normalize_dhs_gps_clusters",
    "register_dhs_gps_snapshot",
    "validate_dhs_gps_linkage",
    "validate_observation_grain",
    "validate_survey_file_link",
]
