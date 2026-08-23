from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, TypeAlias

from .design import ObservationGrain, validate_observation_grain

MetadataValue: TypeAlias = str | int | float | bool | None


class TemporalSemantics(str, Enum):
    """Small source-facing vocabulary for the temporal meaning of a variable."""

    STATIC = "static"
    SURVEY_TIME = "survey_time"
    ANNUAL = "annual"
    EPOCH = "epoch"
    CLIMATOLOGY = "climatology"
    RETROSPECTIVE = "retrospective"
    UNKNOWN = "unknown"


def _require_text(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _optional_text(name: str, value: str | None) -> None:
    if value is not None:
        _require_text(name, value)


@dataclass(frozen=True)
class SurveyVariableMetadata:
    """Source-native metadata for one survey variable, before downstream scientific use."""

    source_family: str
    source_variable: str
    source_label: str | None
    natural_grain: ObservationGrain
    source_value_type: str
    temporal_semantics: TemporalSemantics = TemporalSemantics.UNKNOWN
    recode: str | None = None
    round_id: str | None = None
    instrument: str | None = None
    missing_value_metadata: Mapping[str, MetadataValue] = field(default_factory=dict)
    codebook_provenance: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("source_family", "source_variable", "source_value_type"):
            _require_text(name, getattr(self, name))
        _optional_text("source_label", self.source_label)
        _optional_text("recode", self.recode)
        _optional_text("round_id", self.round_id)
        _optional_text("instrument", self.instrument)
        validate_observation_grain(self.natural_grain)
