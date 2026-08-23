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
from .worldbank import (
    WorldBankExtraction,
    flatten_worldbank_record,
    load_worldbank_pages,
    materialize_worldbank_silver,
    register_worldbank_snapshot,
)

__all__ = [
    "AidDataExtraction",
    "GeoGCDFSilverResult",
    "InvestmentMaterializationResult",
    "SilverTableInput",
    "WorldBankExtraction",
    "build_aiddata_annotation_candidates",
    "build_annotation_candidates",
    "build_worldbank_annotation_candidates",
    "extract_aiddata_workbook",
    "flatten_worldbank_record",
    "load_worldbank_pages",
    "materialize_aiddata_silver",
    "materialize_annotation_candidates",
    "materialize_geogcdf_silver",
    "materialize_worldbank_silver",
    "normalize_geogcdf_projects",
    "register_aiddata_snapshot",
    "register_geogcdf_snapshot",
    "register_worldbank_snapshot",
]
