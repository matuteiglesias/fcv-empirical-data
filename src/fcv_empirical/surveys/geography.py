from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, TypeAlias

MetadataValue: TypeAlias = str | int | float | bool | None


def _require_text(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be non-empty")


@dataclass(frozen=True)
class SurveyGeographyLink:
    """One candidate geography relation for a survey source object.

    Multiple rows may share the same source object when membership is ambiguous.
    ``geo_uid`` may be absent when the object is unmatched or cannot be assigned.
    """

    survey_id: str
    source_object_id: str
    source_object_type: str
    geo_uid: str | None
    assignment_status: str
    assignment_method: str
    uncertainty_metadata: Mapping[str, MetadataValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "survey_id",
            "source_object_id",
            "source_object_type",
            "assignment_status",
            "assignment_method",
        ):
            _require_text(name, getattr(self, name))
        if self.geo_uid is not None:
            _require_text("geo_uid", self.geo_uid)
