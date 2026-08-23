"""Investment source verticals with source-native Silver semantics."""

from .aiddata_clg import (
    AidDataExtraction,
    extract_aiddata_workbook,
    materialize_aiddata_silver,
    register_aiddata_snapshot,
)
from .annotation_candidates import (
    SilverTableInput,
    build_aiddata_annotation_candidates,
    build_annotation_candidates,
    build_worldbank_annotation_candidates,
    materialize_annotation_candidates,
)
from .common import InvestmentMaterializationResult
from .geogcdf import (
    GeoGCDFSilverResult,
    materialize_geogcdf_silver,
    normalize_geogcdf_projects,
    register_geogcdf_snapshot,
)
from .geogcdf_measurements import (
    GeoGCDFGeographyResult,
    GeoGCDFGoldResult,
    GeoGCDFPeriodResult,
    assign_geogcdf_periods,
    build_geogcdf_commitment_coverage,
    build_geogcdf_commitment_gold,
    build_geogcdf_commitment_measurement_contract,
    relate_geogcdf_geography,
)
from .geogcdf_pipeline import (
    GeoGCDFVerticalResult,
    materialize_geogcdf_measurement,
    materialize_geogcdf_vertical,
)
from .worldbank import (
    WorldBankExtraction,
    flatten_worldbank_record,
    load_worldbank_pages,
    materialize_worldbank_silver,
    register_worldbank_snapshot,
)

__all__ = [
    "AidDataExtraction",
    "GeoGCDFGeographyResult",
    "GeoGCDFGoldResult",
    "GeoGCDFPeriodResult",
    "GeoGCDFSilverResult",
    "GeoGCDFVerticalResult",
    "InvestmentMaterializationResult",
    "SilverTableInput",
    "WorldBankExtraction",
    "assign_geogcdf_periods",
    "build_aiddata_annotation_candidates",
    "build_annotation_candidates",
    "build_geogcdf_commitment_coverage",
    "build_geogcdf_commitment_gold",
    "build_geogcdf_commitment_measurement_contract",
    "build_worldbank_annotation_candidates",
    "extract_aiddata_workbook",
    "flatten_worldbank_record",
    "load_worldbank_pages",
    "materialize_aiddata_silver",
    "materialize_annotation_candidates",
    "materialize_geogcdf_measurement",
    "materialize_geogcdf_silver",
    "materialize_geogcdf_vertical",
    "materialize_worldbank_silver",
    "normalize_geogcdf_projects",
    "register_aiddata_snapshot",
    "register_geogcdf_snapshot",
    "register_worldbank_snapshot",
    "relate_geogcdf_geography",
]
