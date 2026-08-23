from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
from empirical_contracts import DatasetRef, QAResult

from .catalog import SurveyCatalogEntry
from .dhs_gps_silver import normalize_cluster_identity

_EXPECTED_DATASET_IDS = {
    "hr": "surveys.dhs.hr_households",
    "gps": "surveys.dhs.gps_clusters",
    "gc": "surveys.dhs_gc.clusters",
}


@dataclass(frozen=True)
class DhsSurveyIntegrationReport:
    """QA evidence that separate DHS products refer to one survey and cluster universe.

    The report is intentionally not a joined analysis table. It compares identities and support
    while leaving household, GPS-cluster, and GC-cluster products at their native grains.
    """

    survey: SurveyCatalogEntry
    cluster_support: pd.DataFrame
    summary: dict[str, Any]
    qa: tuple[QAResult, ...]
    datasets: dict[str, DatasetRef]

    def to_payload(self) -> dict[str, Any]:
        return {
            "survey": {
                "survey_id": self.survey.survey_id,
                "source_family": self.survey.source_family,
                "country_iso3": self.survey.country_iso3,
                "survey_year": self.survey.survey_year,
                "survey_phase": self.survey.survey_phase,
                "release": self.survey.release,
            },
            "datasets": {
                name: dataset.model_dump(mode="json") for name, dataset in self.datasets.items()
            },
            "summary": self.summary,
            "cluster_support": self.cluster_support.to_dict(orient="records"),
            "qa": [item.model_dump(mode="json") for item in self.qa],
        }


def _require_columns(frame: pd.DataFrame, *, name: str, columns: set[str]) -> None:
    missing = sorted(columns - set(frame.columns))
    if missing:
        raise ValueError(f"DHS {name} product is missing required columns: {', '.join(missing)}")


def _validate_survey_identity(
    frame: pd.DataFrame,
    *,
    name: str,
    survey: SurveyCatalogEntry,
) -> tuple[int, tuple[str, ...]]:
    _require_columns(frame, name=name, columns={"survey_id"})
    values = frame["survey_id"].astype("string").str.strip()
    missing = int(values.isna().sum() + values.eq("").fillna(False).sum())
    observed = tuple(sorted(set(values.dropna()) - {""}))
    if missing or observed != (survey.survey_id,):
        raise ValueError(
            f"DHS {name} product does not resolve uniquely to survey_id {survey.survey_id!r}; "
            f"observed={observed!r}, missing_rows={missing}"
        )
    return missing, observed


def _cluster_values(frame: pd.DataFrame, column: str) -> set[str]:
    values: set[str] = set()
    for value in frame[column]:
        normalized = normalize_cluster_identity(value)
        if pd.notna(normalized):
            values.add(str(normalized))
    return values


def _gc_link_column(frame: pd.DataFrame) -> str:
    if "dhsclust" in frame.columns and frame["dhsclust"].notna().any():
        return "dhsclust"
    return "cluster_id"


def _numeric_alias_count(left: set[str], right: set[str]) -> int:
    def index(values: set[str]) -> dict[int, set[str]]:
        result: dict[int, set[str]] = {}
        for value in values:
            if value.isdigit():
                result.setdefault(int(value), set()).add(value)
        return result

    left_index = index(left)
    right_index = index(right)
    aliases = 0
    for key in left_index.keys() & right_index.keys():
        if left_index[key].isdisjoint(right_index[key]):
            aliases += 1
    return aliases


def _snapshot_ids(frame: pd.DataFrame) -> tuple[str, ...]:
    if "source_snapshot_id" not in frame.columns:
        return ()
    values = frame["source_snapshot_id"].astype("string").dropna().str.strip()
    return tuple(sorted(value for value in set(values) if value))


def _validate_dataset_refs(datasets: dict[str, DatasetRef] | None) -> dict[str, DatasetRef]:
    if datasets is None:
        return {}
    unknown = sorted(set(datasets) - set(_EXPECTED_DATASET_IDS))
    if unknown:
        raise ValueError("unknown DHS integration dataset role(s): " + ", ".join(unknown))
    for role, dataset in datasets.items():
        expected = _EXPECTED_DATASET_IDS[role]
        if dataset.dataset_id != expected:
            raise ValueError(
                f"DHS integration role {role!r} requires dataset_id {expected!r}, "
                f"got {dataset.dataset_id!r}"
            )
    return dict(datasets)


def build_dhs_survey_integration_report(
    *,
    survey: SurveyCatalogEntry,
    hr: pd.DataFrame,
    gps: pd.DataFrame,
    gc: pd.DataFrame,
    datasets: dict[str, DatasetRef] | None = None,
) -> DhsSurveyIntegrationReport:
    """Audit HR ↔ GPS ↔ GC identity without constructing an analysis table."""
    if survey.source_family.casefold() != "dhs":
        raise ValueError("DHS survey integration requires a DHS SurveyCatalogEntry")

    for name, frame in (("HR", hr), ("GPS", gps), ("GC", gc)):
        _validate_survey_identity(frame, name=name, survey=survey)

    _require_columns(hr, name="HR", columns={"cluster_id"})
    _require_columns(gps, name="GPS", columns={"cluster_id"})
    _require_columns(gc, name="GC", columns={"cluster_id"})

    gc_link_column = _gc_link_column(gc)
    hr_clusters = _cluster_values(hr, "cluster_id")
    gps_clusters = _cluster_values(gps, "cluster_id")
    gc_clusters = _cluster_values(gc, gc_link_column)

    union = sorted(hr_clusters | gps_clusters | gc_clusters)
    support = pd.DataFrame(
        {
            "cluster_link_id": union,
            "in_hr": [value in hr_clusters for value in union],
            "in_gps": [value in gps_clusters for value in union],
            "in_gc": [value in gc_clusters for value in union],
        }
    )

    pair_counts = {
        "hr_gps_overlap": len(hr_clusters & gps_clusters),
        "hr_only_vs_gps": len(hr_clusters - gps_clusters),
        "gps_only_vs_hr": len(gps_clusters - hr_clusters),
        "hr_gc_overlap": len(hr_clusters & gc_clusters),
        "hr_only_vs_gc": len(hr_clusters - gc_clusters),
        "gc_only_vs_hr": len(gc_clusters - hr_clusters),
        "gps_gc_overlap": len(gps_clusters & gc_clusters),
        "gps_only_vs_gc": len(gps_clusters - gc_clusters),
        "gc_only_vs_gps": len(gc_clusters - gps_clusters),
    }
    numeric_aliases = {
        "hr_gps": _numeric_alias_count(hr_clusters, gps_clusters),
        "hr_gc": _numeric_alias_count(hr_clusters, gc_clusters),
        "gps_gc": _numeric_alias_count(gps_clusters, gc_clusters),
    }
    total_aliases = sum(numeric_aliases.values())
    incomplete_support = sum(
        int(not (row.in_hr and row.in_gps and row.in_gc))
        for row in support.itertuples(index=False)
    )

    summary: dict[str, Any] = {
        "survey_id": survey.survey_id,
        "country_iso3": survey.country_iso3,
        "survey_year": survey.survey_year,
        "hr_household_rows": len(hr),
        "hr_cluster_count": len(hr_clusters),
        "gps_cluster_rows": len(gps),
        "gps_cluster_count": len(gps_clusters),
        "gc_cluster_rows": len(gc),
        "gc_cluster_count": len(gc_clusters),
        "gc_link_key_basis": gc_link_column,
        "hr_snapshot_ids": _snapshot_ids(hr),
        "gps_snapshot_ids": _snapshot_ids(gps),
        "gc_snapshot_ids": _snapshot_ids(gc),
        "numeric_equivalent_but_text_distinct_pairs": numeric_aliases,
        **pair_counts,
    }

    support_state = "GREEN" if incomplete_support == 0 else "YELLOW"
    normalization_state = "GREEN" if total_aliases == 0 else "YELLOW"
    qa = (
        QAResult(
            check_id="dhs.integration.survey_identity",
            state="GREEN",
            message="HR, GPS, and GC products resolve to the same explicit SurveyCatalogEntry",
            metrics={
                "survey_id": survey.survey_id,
                "hr_rows": len(hr),
                "gps_rows": len(gps),
                "gc_rows": len(gc),
            },
        ),
        QAResult(
            check_id="dhs.integration.cluster_support",
            state=support_state,
            message=(
                "cluster support is compared across native products without dropping source-only "
                "clusters or constructing a joined analysis table"
            ),
            metrics={
                "union_cluster_count": len(union),
                "incomplete_support_clusters": incomplete_support,
                **pair_counts,
            },
        ),
        QAResult(
            check_id="dhs.integration.cluster_identity_normalization",
            state=normalization_state,
            message=(
                "numeric-equivalent but text-distinct cluster IDs remain unresolved evidence; "
                "the integration audit never silently strips leading zeros or rewrites source IDs"
            ),
            metrics={
                "numeric_alias_pairs": total_aliases,
                "hr_gps_alias_pairs": numeric_aliases["hr_gps"],
                "hr_gc_alias_pairs": numeric_aliases["hr_gc"],
                "gps_gc_alias_pairs": numeric_aliases["gps_gc"],
            },
        ),
    )

    return DhsSurveyIntegrationReport(
        survey=survey,
        cluster_support=support,
        summary=summary,
        qa=qa,
        datasets=_validate_dataset_refs(datasets),
    )


__all__ = ["DhsSurveyIntegrationReport", "build_dhs_survey_integration_report"]
