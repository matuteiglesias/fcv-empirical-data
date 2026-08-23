from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

ObservationGrain: TypeAlias = str
WeightValue: TypeAlias = int | float | str


def validate_observation_grain(value: str) -> str:
    """Validate a durable grain label without constraining it to a closed ontology."""
    if not value or not value.strip():
        raise ValueError("natural_grain must be non-empty")
    return value


def _optional_text(name: str, value: str | None) -> None:
    if value is not None and (not value or not value.strip()):
        raise ValueError(f"{name} must be non-empty when supplied")


@dataclass(frozen=True)
class SurveyDesignRecord:
    """Source-facing sampling/design facts for one survey observation."""

    survey_id: str
    observation_id: str
    natural_grain: ObservationGrain
    cluster_id: str | None = None
    psu_id: str | None = None
    stratum_id: str | None = None
    source_weight_variable: str | None = None
    source_weight_value: WeightValue | None = None
    normalized_weight_value: float | None = None

    def __post_init__(self) -> None:
        if not self.survey_id or not self.survey_id.strip():
            raise ValueError("survey_id must be non-empty")
        if not self.observation_id or not self.observation_id.strip():
            raise ValueError("observation_id must be non-empty")
        validate_observation_grain(self.natural_grain)
        for name in ("cluster_id", "psu_id", "stratum_id", "source_weight_variable"):
            _optional_text(name, getattr(self, name))
