from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from empirical_contracts import DatasetRef

ParityStatus = Literal["EQUAL", "EXPLAINED_DIVERGENCE", "UNEXPLAINED_DIVERGENCE"]
_ALLOWED_STATUSES = {"EQUAL", "EXPLAINED_DIVERGENCE", "UNEXPLAINED_DIVERGENCE"}


def _nonnegative(name: str, value: int) -> int:
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def build_parity_report(
    *,
    legacy_dataset: DatasetRef,
    new_dataset: DatasetRef,
    key_overlap: int,
    legacy_rows: int,
    new_rows: int,
    total_comparisons: int,
    legacy_only: int,
    new_only: int,
    numerical_differences: int,
    discrepancy_categories: Mapping[str, int] | None = None,
    status: ParityStatus,
    notes: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build an explicit parity report; divergence is not automatically failure."""
    if status not in _ALLOWED_STATUSES:
        raise ValueError(f"unsupported parity status: {status}")
    categories = dict(discrepancy_categories or {})
    for category, count in categories.items():
        _nonnegative(f"discrepancy category {category!r}", count)

    return {
        "legacy_dataset": legacy_dataset.model_dump(mode="json"),
        "new_dataset": new_dataset.model_dump(mode="json"),
        "key_overlap": _nonnegative("key_overlap", key_overlap),
        "row_counts": {
            "legacy": _nonnegative("legacy_rows", legacy_rows),
            "new": _nonnegative("new_rows", new_rows),
        },
        "total_comparisons": _nonnegative("total_comparisons", total_comparisons),
        "legacy_only": _nonnegative("legacy_only", legacy_only),
        "new_only": _nonnegative("new_only", new_only),
        "numerical_differences": _nonnegative(
            "numerical_differences", numerical_differences
        ),
        "discrepancy_categories": categories,
        "status": status,
        "notes": list(notes),
    }


def serialize_parity_report(report: Mapping[str, Any]) -> str:
    """Serialize a parity report deterministically for durable comparison artifacts."""
    return json.dumps(dict(report), sort_keys=True, indent=2) + "\n"
