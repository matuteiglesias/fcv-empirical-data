from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TypeAlias

from empirical_contracts import GeographySpec
from spatial_foundation.geography import MembershipStatus

MetadataValue: TypeAlias = str | int | float | bool | None


def _require_text(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be non-empty")


@dataclass(frozen=True)
class SurveyGeographyLink:
    """One candidate geography relation for a survey source object.

    Several rows may share the same source object when membership is ambiguous. Every row carries
    the exact ``GeographySpec`` being referenced so candidates from different geography versions,
    schemes, or levels cannot be mixed accidentally. Assignment status reuses the public
    ``spatial-data-foundation`` vocabulary rather than creating a survey-specific status ontology.
    """

    survey_id: str
    source_object_id: str
    source_object_type: str
    geography: GeographySpec
    geo_uid: str | None
    assignment_status: MembershipStatus | str
    assignment_method: str
    uncertainty_metadata: Mapping[str, MetadataValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "survey_id",
            "source_object_id",
            "source_object_type",
            "assignment_method",
        ):
            _require_text(name, getattr(self, name))
        if not isinstance(self.geography, GeographySpec):
            raise TypeError("geography must be an empirical_contracts.GeographySpec")

        try:
            status = MembershipStatus(self.assignment_status)
        except ValueError as error:
            raise ValueError(
                "assignment_status must be a spatial-data-foundation MembershipStatus: "
                f"{self.assignment_status!r}"
            ) from error
        object.__setattr__(self, "assignment_status", status)

        resolved_statuses = {
            MembershipStatus.MATCHED_UNIQUE,
            MembershipStatus.AMBIGUOUS_MULTIPLE,
        }
        unresolved_statuses = {
            MembershipStatus.UNMATCHED_OUTSIDE,
            MembershipStatus.INVALID_POINT,
        }
        if status in resolved_statuses:
            if self.geo_uid is None:
                raise ValueError(f"{status.value} geography rows require geo_uid")
            _require_text("geo_uid", self.geo_uid)
        elif status in unresolved_statuses and self.geo_uid is not None:
            raise ValueError(f"{status.value} geography rows must not carry geo_uid")
