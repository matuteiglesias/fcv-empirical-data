from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from empirical_contracts import SourceFileRef


def _require_text(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be non-empty")


@dataclass(frozen=True)
class SurveyCatalogEntry:
    """Repository-owned identity for one survey, independent of its source files."""

    survey_id: str
    source_family: str
    country_iso3: str
    survey_year: int
    release: str
    source_snapshot_id: str
    fieldwork_start: date | None = None
    fieldwork_end: date | None = None
    survey_phase: str | None = None

    def __post_init__(self) -> None:
        for name in ("survey_id", "source_family", "country_iso3", "release", "source_snapshot_id"):
            _require_text(name, getattr(self, name))
        if len(self.country_iso3) != 3 or not self.country_iso3.isalpha():
            raise ValueError("country_iso3 must be a three-letter ISO code")
        if self.survey_year < 1000 or self.survey_year > 9999:
            raise ValueError("survey_year must be a four-digit year")
        if self.survey_phase is not None:
            _require_text("survey_phase", self.survey_phase)
        if (
            self.fieldwork_start
            and self.fieldwork_end
            and self.fieldwork_end < self.fieldwork_start
        ):
            raise ValueError("fieldwork_end must be on or after fieldwork_start")


@dataclass(frozen=True)
class SurveyFileLink:
    """Link one source file to a survey without equating file identity with survey identity."""

    survey_id: str
    source_snapshot_id: str
    source_file: SourceFileRef
    instrument: str | None = None

    def __post_init__(self) -> None:
        _require_text("survey_id", self.survey_id)
        _require_text("source_snapshot_id", self.source_snapshot_id)
        if self.instrument is not None:
            _require_text("instrument", self.instrument)
