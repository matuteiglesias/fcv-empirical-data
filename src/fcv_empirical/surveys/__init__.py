"""Minimal source-native substrate for complex respondent and household surveys."""

from .catalog import SurveyCatalogEntry, SurveyFileLink, validate_survey_file_link
from .design import ObservationGrain, SurveyDesignRecord, WeightValue, validate_observation_grain
from .dhs_hr import (
    DHS_HR_RECODE,
    DHS_SOURCE,
    DhsHrColumnMap,
    DhsHrMetadata,
    DhsHrSilverResult,
    HOUSEHOLD_GRAIN,
    STANDARD_DHS_HR_COLUMNS,
    build_dhs_hr_file_link,
    build_dhs_survey_catalog,
    build_dhs_survey_id,
    iter_dhs_hr_design_records,
    materialize_dhs_hr_silver,
    normalize_dhs_hr,
    register_dhs_hr_snapshot,
)
from .geography import SurveyGeographyLink
from .variables import SurveyVariableMetadata, TemporalSemantics

__all__ = [
    "DHS_HR_RECODE",
    "DHS_SOURCE",
    "DhsHrColumnMap",
    "DhsHrMetadata",
    "DhsHrSilverResult",
    "HOUSEHOLD_GRAIN",
    "ObservationGrain",
    "STANDARD_DHS_HR_COLUMNS",
    "SurveyCatalogEntry",
    "SurveyDesignRecord",
    "SurveyFileLink",
    "SurveyGeographyLink",
    "SurveyVariableMetadata",
    "TemporalSemantics",
    "WeightValue",
    "build_dhs_hr_file_link",
    "build_dhs_survey_catalog",
    "build_dhs_survey_id",
    "iter_dhs_hr_design_records",
    "materialize_dhs_hr_silver",
    "normalize_dhs_hr",
    "register_dhs_hr_snapshot",
    "validate_observation_grain",
    "validate_survey_file_link",
]
