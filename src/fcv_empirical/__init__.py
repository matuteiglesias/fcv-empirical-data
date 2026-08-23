"""FCV empirical-data domain kernel."""

from .common.materialization import (
    FileMaterialization,
    materialize_file,
    materialize_files,
    output_path,
    persist_run_manifest,
    run_path,
)
from .common.parity import build_parity_report, serialize_parity_report
from .common.qa import accumulate_qa, presence_counts, serialize_qa

__all__ = [
    "FileMaterialization",
    "accumulate_qa",
    "build_parity_report",
    "materialize_file",
    "materialize_files",
    "output_path",
    "persist_run_manifest",
    "presence_counts",
    "run_path",
    "serialize_parity_report",
    "serialize_qa",
]
