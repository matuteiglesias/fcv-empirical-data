from __future__ import annotations

import math
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
    """Sampling/design facts for one survey observation.

    Source weights remain source facts. A normalized weight may be carried only when the
    transformation that produced it is explicitly named; this record never chooses which weight
    an experiment should use.
    """

    survey_id: str
    observation_id: str
    natural_grain: ObservationGrain
    cluster_id: str | None = None
    psu_id: str | None = None
    stratum_id: str | None = None
    source_weight_variable: str | None = None
    source_weight_value: WeightValue | None = None
    normalized_weight_value: float | None = None
    weight_normalization_method: str | None = None

    def __post_init__(self) -> None:
        if not self.survey_id or not self.survey_id.strip():
            raise ValueError("survey_id must be non-empty")
        if not self.observation_id or not self.observation_id.strip():
            raise ValueError("observation_id must be non-empty")
        validate_observation_grain(self.natural_grain)
        for name in (
            "cluster_id",
            "psu_id",
            "stratum_id",
            "source_weight_variable",
            "weight_normalization_method",
        ):
            _optional_text(name, getattr(self, name))

        if self.normalized_weight_value is None:
            if self.weight_normalization_method is not None:
                raise ValueError(
                    "weight_normalization_method requires normalized_weight_value"
                )
        else:
            if not math.isfinite(self.normalized_weight_value):
                raise ValueError("normalized_weight_value must be finite when supplied")
            if self.weight_normalization_method is None:
                raise ValueError(
                    "normalized_weight_value requires an explicit weight_normalization_method"
                )
