from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from empirical_contracts import QAResult


def accumulate_qa(*groups: Iterable[QAResult]) -> tuple[QAResult, ...]:
    """Flatten QA results without altering their contract-backed meaning."""
    return tuple(result for group in groups for result in group)


def presence_counts(values: Iterable[Any]) -> dict[str, int]:
    """Count observed and missing values while keeping zero distinct from missing."""
    total = 0
    missing = 0
    for value in values:
        total += 1
        if value is None:
            missing += 1
    return {"rows": total, "observed": total - missing, "missing": missing}


def serialize_qa(results: Iterable[QAResult]) -> str:
    """Serialize QAResult objects using their public compatibility contract."""
    payload = [result.model_dump(mode="json") for result in results]
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"
