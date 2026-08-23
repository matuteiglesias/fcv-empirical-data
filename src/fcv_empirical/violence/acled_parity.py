from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

VAC_EVENT_TYPE = "Violence against civilians"
LEGACY_VAC_COLUMN = "deaths_Violence against civilians"


def _known_sum(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    return float(numeric.sum(min_count=1)) if numeric.notna().any() else 0.0


def legacy_filter_diagnostics(silver: pd.DataFrame) -> dict[str, Any]:
    """Quantify two destructive legacy choices without applying them to authoritative Silver."""
    precision = pd.to_numeric(silver["geo_precision"], errors="coerce")
    fatalities = pd.to_numeric(silver["fatalities"], errors="coerce")
    precision_one = precision.eq(1).fillna(False)
    zero_fatality = fatalities.eq(0).fillna(False)
    total_fatalities = _known_sum(fatalities)
    precision_one_fatalities = _known_sum(fatalities.loc[precision_one])
    return {
        "silver_event_rows": len(silver),
        "silver_known_fatalities": total_fatalities,
        "geo_precision_1_rows": int(precision_one.sum()),
        "geo_precision_1_known_fatalities": precision_one_fatalities,
        "geo_precision_filter_removed_rows": int((~precision_one).sum()),
        "geo_precision_filter_removed_known_fatalities": total_fatalities - precision_one_fatalities,
        "zero_fatality_events_retained": int(zero_fatality.sum()),
        "note": "Diagnostics describe historical filters only. Authoritative Silver is unchanged.",
    }


def compare_acled_legacy(
    *,
    silver: pd.DataFrame,
    gold: pd.DataFrame,
    legacy: pd.DataFrame | None = None,
    geo_uid_to_legacy_gid: Mapping[str, str] | None = None,
    legacy_gid_col: str = "GID",
    legacy_period_col: str = "TimePeriod",
    legacy_vac_column: str = LEGACY_VAC_COLUMN,
) -> dict[str, Any]:
    """Explain modern/legacy differences without forcing the rebuilt surface to match legacy."""
    diagnostics = legacy_filter_diagnostics(silver)
    report: dict[str, Any] = {
        "legacy_filter_diagnostics": diagnostics,
        "expected_difference_categories": [
            "historical_geo_precision_filter",
            "zero_fatality_event_retention",
            "geography_version_or_membership_difference",
            "terminal_period_coverage_difference",
            "source_snapshot_difference",
        ],
    }
    if legacy is None:
        report.update(
            {
                "status": "NOT_RUN",
                "reason": "legacy aggregate was not supplied",
                "key_comparison": {"status": "NOT_RUN"},
            }
        )
        return report

    required = {legacy_gid_col, legacy_period_col}
    missing = sorted(required - set(legacy.columns))
    if missing:
        report.update(
            {
                "status": "BLOCKED",
                "reason": f"legacy aggregate is missing required columns: {', '.join(missing)}",
                "key_comparison": {"status": "NOT_RUN"},
            }
        )
        return report

    legacy_keys = legacy[[legacy_gid_col, legacy_period_col]].astype("string")
    duplicate_legacy_keys = int(legacy_keys.duplicated(keep=False).sum())
    report["legacy_duplicate_key_rows"] = duplicate_legacy_keys

    if geo_uid_to_legacy_gid is None:
        report.update(
            {
                "status": "PARTIAL",
                "reason": (
                    "legacy aggregate supplied but no explicit geo_uid→legacy GID crosswalk "
                    "was supplied"
                ),
                "key_comparison": {"status": "NOT_RUN"},
            }
        )
        return report

    modern_vac = gold.loc[
        gold["native_event_type"].astype("string").eq(VAC_EVENT_TYPE).fillna(False),
        ["geo_uid", "period_id", "fatalities"],
    ].copy()
    modern_vac[legacy_gid_col] = modern_vac["geo_uid"].map(geo_uid_to_legacy_gid)
    unmapped_modern_rows = int(modern_vac[legacy_gid_col].isna().sum())
    modern_mapped = modern_vac.loc[modern_vac[legacy_gid_col].notna()].copy()
    modern_mapped[legacy_period_col] = modern_mapped["period_id"].astype("string")

    legacy_key_set = set(map(tuple, legacy_keys.dropna().itertuples(index=False, name=None)))
    modern_key_frame = modern_mapped[[legacy_gid_col, legacy_period_col]].astype("string")
    modern_duplicate_key_rows = int(modern_key_frame.duplicated(keep=False).sum())
    report["modern_duplicate_legacy_key_rows"] = modern_duplicate_key_rows
    modern_key_set = set(
        map(tuple, modern_key_frame.dropna().itertuples(index=False, name=None))
    )
    intersection = legacy_key_set & modern_key_set
    union = legacy_key_set | modern_key_set
    report["key_comparison"] = {
        "status": "COMPARED",
        "legacy_keys": len(legacy_key_set),
        "modern_mapped_keys": len(modern_key_set),
        "overlap_keys": len(intersection),
        "legacy_only_keys": len(legacy_key_set - modern_key_set),
        "modern_only_keys": len(modern_key_set - legacy_key_set),
        "jaccard": len(intersection) / len(union) if union else 1.0,
        "modern_rows_without_legacy_gid_mapping": unmapped_modern_rows,
    }

    if duplicate_legacy_keys or modern_duplicate_key_rows:
        reasons = []
        if duplicate_legacy_keys:
            reasons.append("legacy keys are duplicated")
        if modern_duplicate_key_rows:
            reasons.append("explicit geo crosswalk creates duplicate legacy keys")
        report.update(
            {
                "status": "PARTIAL",
                "reason": "; ".join(reasons) + "; value comparison was not coerced by aggregation",
                "vac_value_comparison": {"status": "NOT_RUN"},
            }
        )
        return report

    if legacy_vac_column not in legacy.columns:
        report.update(
            {
                "status": "PARTIAL",
                "reason": f"legacy VAC field {legacy_vac_column!r} is absent",
                "vac_value_comparison": {"status": "NOT_RUN"},
            }
        )
        return report

    modern_values = modern_mapped.rename(columns={"fatalities": "modern_vac_fatalities"})[
        [legacy_gid_col, legacy_period_col, "modern_vac_fatalities"]
    ]
    legacy_values = legacy[
        [legacy_gid_col, legacy_period_col, legacy_vac_column]
    ].rename(columns={legacy_vac_column: "legacy_vac_fatalities"})
    compared = legacy_values.merge(
        modern_values,
        on=[legacy_gid_col, legacy_period_col],
        how="inner",
        validate="one_to_one",
    )
    compared["legacy_vac_fatalities"] = pd.to_numeric(
        compared["legacy_vac_fatalities"], errors="coerce"
    )
    compared["modern_vac_fatalities"] = pd.to_numeric(
        compared["modern_vac_fatalities"], errors="coerce"
    )
    valid = compared[["legacy_vac_fatalities", "modern_vac_fatalities"]].notna().all(axis=1)
    absolute_difference = (
        compared.loc[valid, "modern_vac_fatalities"] - compared.loc[valid, "legacy_vac_fatalities"]
    ).abs()
    report["vac_value_comparison"] = {
        "status": "COMPARED",
        "overlap_rows": len(compared),
        "numeric_overlap_rows": int(valid.sum()),
        "exact_numeric_matches": int((absolute_difference == 0).sum()),
        "max_absolute_difference": (
            float(absolute_difference.max()) if len(absolute_difference) else None
        ),
        "mean_absolute_difference": (
            float(absolute_difference.mean()) if len(absolute_difference) else None
        ),
    }
    report["status"] = "COMPARED"
    report["reason"] = (
        "differences are evidence to classify, not a requirement to modify the rebuilt measurement"
    )
    return report
