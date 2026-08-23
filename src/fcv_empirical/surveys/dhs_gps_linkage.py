from __future__ import annotations

from typing import Any

import pandas as pd
from empirical_contracts import QAResult

from .catalog import SurveyCatalogEntry
from .dhs_gps_models import DhsGpsLinkageResult
from .dhs_gps_silver import normalize_cluster_identity


def validate_dhs_gps_linkage(
    survey_clusters: pd.DataFrame,
    gps_silver: pd.DataFrame,
    *,
    survey: SurveyCatalogEntry,
    expected_source_survey_id: str | None = None,
) -> DhsGpsLinkageResult:
    """Audit catalog/survey/GPS identity without hiding mismatches in an inner join."""
    if survey.source_family.casefold() != "dhs":
        raise ValueError("survey source_family must be DHS")
    survey_id = survey.survey_id
    for name, frame in (("survey clusters", survey_clusters), ("GPS Silver", gps_silver)):
        required = {"survey_id", "cluster_id"}
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"{name} missing required columns: {', '.join(missing)}")

    survey_ids = survey_clusters["survey_id"].astype("string")
    gps_ids = gps_silver["survey_id"].astype("string")
    survey_conflicts = survey_ids.notna() & survey_ids.ne(survey_id)
    gps_conflicts = gps_ids.notna() & gps_ids.ne(survey_id)

    survey_cluster_ids = (
        survey_clusters["cluster_id"].map(normalize_cluster_identity).astype("string")
    )
    gps_cluster_ids = gps_silver["cluster_id"].map(normalize_cluster_identity).astype("string")
    survey_present = survey_cluster_ids.dropna()
    gps_present = gps_cluster_ids.dropna()
    survey_duplicates = survey_present[survey_present.duplicated(keep=False)]
    gps_duplicates = gps_present[gps_present.duplicated(keep=False)]

    survey_unique = set(survey_present.unique())
    gps_unique = set(gps_present.unique())
    survey_only = sorted(survey_unique - gps_unique)
    gps_only = sorted(gps_unique - survey_unique)

    source_id_conflicts = pd.Series(False, index=gps_silver.index)
    multiple_source_survey_ids = 0
    if "source_survey_id" in gps_silver.columns:
        source_ids = gps_silver["source_survey_id"].astype("string").dropna().str.strip()
        multiple_source_survey_ids = int(source_ids.nunique() > 1)
        if expected_source_survey_id is not None:
            source_id_conflicts = (
                gps_silver["source_survey_id"].astype("string").notna()
                & gps_silver["source_survey_id"].astype("string").ne(expected_source_survey_id)
            )

    rows: list[dict[str, Any]] = []
    rows.extend(
        {"issue": "survey_cluster_absent_gps", "survey_id": survey_id, "cluster_id": value}
        for value in survey_only
    )
    rows.extend(
        {"issue": "gps_cluster_absent_survey", "survey_id": survey_id, "cluster_id": value}
        for value in gps_only
    )
    rows.extend(
        {"issue": "duplicate_survey_cluster", "survey_id": survey_id, "cluster_id": value}
        for value in sorted(set(survey_duplicates))
    )
    rows.extend(
        {"issue": "duplicate_gps_cluster", "survey_id": survey_id, "cluster_id": value}
        for value in sorted(set(gps_duplicates))
    )
    for frame, mask, issue in (
        (survey_clusters, survey_conflicts, "conflicting_survey_id"),
        (gps_silver, gps_conflicts, "conflicting_gps_survey_id"),
        (gps_silver, source_id_conflicts, "conflicting_source_survey_id"),
    ):
        for record in frame.loc[mask, ["cluster_id"]].itertuples(index=False):
            rows.append(
                {"issue": issue, "survey_id": survey_id, "cluster_id": record.cluster_id}
            )
    if multiple_source_survey_ids:
        rows.append(
            {
                "issue": "multiple_source_survey_ids",
                "survey_id": survey_id,
                "cluster_id": pd.NA,
            }
        )

    discrepancies = pd.DataFrame(rows, columns=["issue", "survey_id", "cluster_id"])
    mismatch_count = len(survey_only) + len(gps_only)
    duplicate_count = len(set(survey_duplicates)) + len(set(gps_duplicates))
    conflict_count = int(survey_conflicts.sum() + gps_conflicts.sum() + source_id_conflicts.sum())
    conflict_count += multiple_source_survey_ids

    qa = (
        QAResult(
            check_id="dhs.gps.linkage.cluster_coverage",
            state="GREEN" if mismatch_count == 0 else "YELLOW",
            message="survey/GPS cluster-set discrepancies remain explicit",
            metrics={
                "survey_clusters_absent_gps": len(survey_only),
                "gps_clusters_absent_survey": len(gps_only),
            },
        ),
        QAResult(
            check_id="dhs.gps.linkage.identity",
            state="GREEN" if duplicate_count == 0 and conflict_count == 0 else "RED",
            message=(
                "duplicate cluster and conflicting survey identities are rejected as clean linkage"
            ),
            metrics={
                "duplicate_cluster_ids": duplicate_count,
                "conflicting_survey_identity_rows": conflict_count,
            },
        ),
    )
    return DhsGpsLinkageResult(discrepancies=discrepancies, qa=qa)
