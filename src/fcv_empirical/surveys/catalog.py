from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from empirical_contracts import SourceFileRef, SourceSnapshotRef


def _require_text(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be non-empty")


@dataclass(frozen=True)
class SurveyCatalogEntry:
    """Repository-owned identity for one survey, independent of acquisition artifacts.

    Source snapshots belong to ``SurveyFileLink`` rather than the survey identity itself:
    one survey may legitimately be represented by HR, PR, GE, GC, or other files acquired
    and versioned through different snapshots.
    """

    survey_id: str
    source_family: str
    country_iso3: str
    survey_year: int
    release: str
    fieldwork_start: date | None = None
    fieldwork_end: date | None = None
    survey_phase: str | None = None

    def __post_init__(self) -> None:
        for name in ("survey_id", "source_family", "country_iso3", "release"):
            _require_text(name, getattr(self, name))
        if (
            len(self.country_iso3) != 3
            or not self.country_iso3.isalpha()
            or self.country_iso3 != self.country_iso3.upper()
        ):
            raise ValueError("country_iso3 must be a three-letter uppercase ISO code")
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
    """Link one contract-backed source file to a survey.

    ``source_snapshot_id`` identifies the acquisition snapshot containing the file. The
    catalog entry deliberately does not carry a single snapshot identity because different
    instruments belonging to one survey may be acquired/versioned separately.
    """

    survey_id: str
    source_snapshot_id: str
    source_file: SourceFileRef
    instrument: str | None = None

    def __post_init__(self) -> None:
        _require_text("survey_id", self.survey_id)
        _require_text("source_snapshot_id", self.source_snapshot_id)
        if not isinstance(self.source_file, SourceFileRef):
            raise TypeError("source_file must be an empirical_contracts.SourceFileRef")
        if self.instrument is not None:
            _require_text("instrument", self.instrument)


def validate_survey_file_link(
    survey: SurveyCatalogEntry,
    link: SurveyFileLink,
    snapshot: SourceSnapshotRef,
) -> None:
    """Validate the survey → snapshot → file relation without duplicating source contracts."""
    if link.survey_id != survey.survey_id:
        raise ValueError(
            f"SurveyFileLink survey_id {link.survey_id!r} does not match "
            f"SurveyCatalogEntry {survey.survey_id!r}"
        )
    if link.source_snapshot_id != snapshot.snapshot_id:
        raise ValueError(
            f"SurveyFileLink snapshot {link.source_snapshot_id!r} does not match "
            f"SourceSnapshotRef {snapshot.snapshot_id!r}"
        )
    if link.source_file not in snapshot.files:
        raise ValueError("SurveyFileLink source_file is not a member of the declared SourceSnapshotRef")
