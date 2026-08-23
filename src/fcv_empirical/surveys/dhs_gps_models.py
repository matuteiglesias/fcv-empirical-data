from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from empirical_contracts import QAResult

from .geography import SurveyGeographyLink

DHS_GPS_SOURCE = "dhs_gps"
DHS_ORIGIN = "https://dhsprogram.com/"
RAW_PREFIX = "source__"
REPORTED_COORDINATE_MEMBERSHIP = "reported_coordinate_membership"
POSSIBLE_GEOGRAPHY_UNDER_DISPLACEMENT = "possible_geography_under_displacement"


@dataclass(frozen=True)
class DhsDisplacementPolicy:
    """Authoritative release-specific metadata about public-coordinate displacement.

    No displacement distance is assumed by default. Callers should populate only values supported
    by the documentation for the registered GPS/GE release.
    """

    coordinate_is_displaced: bool
    policy_class: str | None = None
    displacement_max_km: float | None = None
    urban_max_km: float | None = None
    rural_max_km: float | None = None
    exceptional_rural_displacement_possible: bool | None = None
    exceptional_rural_max_km: float | None = None
    policy_source: str | None = None
    urban_rural_rule: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.coordinate_is_displaced, bool):
            raise TypeError("coordinate_is_displaced must be bool")
        for name in ("policy_class", "policy_source", "urban_rural_rule"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must be non-empty when supplied")
        for name in (
            "displacement_max_km",
            "urban_max_km",
            "rural_max_km",
            "exceptional_rural_max_km",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative when supplied")
        if (
            self.exceptional_rural_displacement_possible is False
            and self.exceptional_rural_max_km is not None
        ):
            raise ValueError(
                "exceptional_rural_max_km contradicts "
                "exceptional_rural_displacement_possible=False"
            )
        if not self.coordinate_is_displaced and (
            any(
                value is not None
                for value in (
                    self.displacement_max_km,
                    self.urban_max_km,
                    self.rural_max_km,
                    self.exceptional_rural_max_km,
                )
            )
            or self.exceptional_rural_displacement_possible is True
        ):
            raise ValueError("non-displaced coordinates cannot carry displacement-distance rules")

    def as_metadata(self) -> dict[str, str | int | float | bool | None]:
        return {
            "coordinate_is_displaced": self.coordinate_is_displaced,
            "displacement_policy_class": self.policy_class,
            "displacement_max_km": self.displacement_max_km,
            "displacement_urban_max_km": self.urban_max_km,
            "displacement_rural_max_km": self.rural_max_km,
            "exceptional_rural_displacement_possible": (
                self.exceptional_rural_displacement_possible
            ),
            "exceptional_rural_displacement_max_km": self.exceptional_rural_max_km,
            "displacement_policy_source": self.policy_source,
            "displacement_urban_rural_rule": self.urban_rural_rule,
        }


@dataclass(frozen=True)
class DhsGpsSilverResult:
    frame: pd.DataFrame
    qa: tuple[QAResult, ...]
    source_columns: dict[str, str | None]
    schema_sha256: str


@dataclass(frozen=True)
class DhsGpsLinkageResult:
    discrepancies: pd.DataFrame
    qa: tuple[QAResult, ...]


@dataclass(frozen=True)
class DhsReportedMembershipResult:
    frame: pd.DataFrame
    links: tuple[SurveyGeographyLink, ...]
    qa: tuple[QAResult, ...]
