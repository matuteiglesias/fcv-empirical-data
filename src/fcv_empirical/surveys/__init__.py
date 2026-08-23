"""Minimal source-native substrate for complex respondent and household surveys."""

from .catalog import SurveyCatalogEntry, SurveyFileLink, validate_survey_file_link
from .design import ObservationGrain, SurveyDesignRecord, WeightValue, validate_observation_grain
from .geography import SurveyGeographyLink
from .variables import SurveyVariableMetadata, TemporalSemantics

__all__ = [
    "ObservationGrain",
    "SurveyCatalogEntry",
    "SurveyDesignRecord",
    "SurveyFileLink",
    "SurveyGeographyLink",
    "SurveyVariableMetadata",
    "TemporalSemantics",
    "WeightValue",
    "validate_observation_grain",
    "validate_survey_file_link",
]
