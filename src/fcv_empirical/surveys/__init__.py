"""Minimal source-native substrate for complex respondent and household surveys."""

from .catalog import SurveyCatalogEntry, SurveyFileLink, validate_survey_file_link
from .design import ObservationGrain, SurveyDesignRecord, WeightValue, validate_observation_grain
from .dhs_commissioning import (
    DhsCommissioningResult,
    DhsCommissioningSpec,
    materialize_dhs_commissioning_suite,
    run_dhs_commissioning_suite,
)
from .dhs_commissioning_benchmarks import (
    nigeria_2018_commissioning_specs,
    uganda_2016_commissioning_specs,
    zambia_2018_commissioning_specs,
)
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
from .dhs_hr import (
    DHS_HR_RECODE,
    DHS_SOURCE,
    HOUSEHOLD_GRAIN,
    STANDARD_DHS_HR_COLUMNS,
    DhsHrColumnMap,
    DhsHrMetadata,
    DhsHrSilverResult,
    build_dhs_hr_file_link,
    build_dhs_survey_catalog,
    build_dhs_survey_id,
    iter_dhs_hr_design_records,
    normalize_dhs_hr,
    register_dhs_hr_snapshot,
)
from .dhs_hr import (
    materialize_dhs_hr_silver as materialize_dhs_hr_legacy_tabular_silver,
)
from .dhs_hr_low_memory_writer import install_low_memory_dhs_hr_writer
from .dhs_hr_release import (
    DhsFixedWidthDictionary,
    DhsFixedWidthField,
    materialize_dhs_hr_release_silver,
    parse_dhs_stata_dictionary,
    read_dhs_fixed_width_dat,
    register_dhs_hr_release_snapshot,
)
from .dhs_integration import DhsSurveyIntegrationReport, build_dhs_survey_integration_report
from .dhs_variables import (
    DHS_VII_STANDARD_HR_REGISTRY,
    DhsHouseholdMeasurementResult,
    DhsVariableDefinition,
    build_dhs_household_measurements,
    materialize_dhs_household_measurements,
    registry_sha256,
)
from .dhs_wealth_distribution import (
    QUINTILE_LABELS,
    DhsWealthRegionResult,
    DhsWealthRegionSpec,
    build_dhs_wealth_region_shares,
)
from .geography import SurveyGeographyLink
from .variables import SurveyVariableMetadata, TemporalSemantics

# Canonical package-level HR materialization now means authoritative fixed-width release decoding.
# Install the ultra-wide low-memory Parquet profile before exposing the canonical materializer.
install_low_memory_dhs_hr_writer()
# The former tabular materializer remains explicit for legacy/parity investigation only.
materialize_dhs_hr_silver = materialize_dhs_hr_release_silver

__all__ = [
    "DHS_GPS_SOURCE",
    "DHS_HR_RECODE",
    "DHS_SOURCE",
    "DHS_VII_STANDARD_HR_REGISTRY",
    "HOUSEHOLD_GRAIN",
    "POSSIBLE_GEOGRAPHY_UNDER_DISPLACEMENT",
    "QUINTILE_LABELS",
    "REPORTED_COORDINATE_MEMBERSHIP",
    "STANDARD_DHS_HR_COLUMNS",
    "DhsCommissioningResult",
    "DhsCommissioningSpec",
    "DhsDisplacementPolicy",
    "DhsFixedWidthDictionary",
    "DhsFixedWidthField",
    "DhsGpsLinkageResult",
    "DhsGpsSilverResult",
    "DhsHouseholdMeasurementResult",
    "DhsHrColumnMap",
    "DhsHrMetadata",
    "DhsHrSilverResult",
    "DhsReportedMembershipResult",
    "DhsSurveyIntegrationReport",
    "DhsVariableDefinition",
    "DhsWealthRegionResult",
    "DhsWealthRegionSpec",
    "ObservationGrain",
    "SurveyCatalogEntry",
    "SurveyDesignRecord",
    "SurveyFileLink",
    "SurveyGeographyLink",
    "SurveyVariableMetadata",
    "TemporalSemantics",
    "WeightValue",
    "assign_dhs_reported_coordinate_membership",
    "build_dhs_household_measurements",
    "build_dhs_hr_file_link",
    "build_dhs_survey_catalog",
    "build_dhs_survey_id",
    "build_dhs_survey_integration_report",
    "build_dhs_wealth_region_shares",
    "iter_dhs_hr_design_records",
    "materialize_dhs_commissioning_suite",
    "materialize_dhs_gps_silver",
    "materialize_dhs_household_measurements",
    "materialize_dhs_hr_legacy_tabular_silver",
    "materialize_dhs_hr_release_silver",
    "materialize_dhs_hr_silver",
    "materialize_dhs_reported_coordinate_membership",
    "nigeria_2018_commissioning_specs",
    "normalize_dhs_gps_clusters",
    "normalize_dhs_hr",
    "parse_dhs_stata_dictionary",
    "read_dhs_fixed_width_dat",
    "register_dhs_gps_snapshot",
    "register_dhs_hr_release_snapshot",
    "register_dhs_hr_snapshot",
    "registry_sha256",
    "run_dhs_commissioning_suite",
    "uganda_2016_commissioning_specs",
    "validate_dhs_gps_linkage",
    "validate_observation_grain",
    "validate_survey_file_link",
    "zambia_2018_commissioning_specs",
]
