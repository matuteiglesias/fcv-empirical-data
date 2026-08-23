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
from .worldbank import (
    WorldBankExtraction,
    flatten_worldbank_record,
    load_worldbank_pages,
    materialize_worldbank_silver,
    register_worldbank_snapshot,
)

__all__ = [
    "AidDataExtraction",
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
    "materialize_worldbank_silver",
    "register_aiddata_snapshot",
    "register_worldbank_snapshot",
]
