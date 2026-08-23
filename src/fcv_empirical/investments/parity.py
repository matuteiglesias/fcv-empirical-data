from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd
from empirical_contracts import AuthorityLevel, DataLayer, DatasetRef, GrainSpec

from fcv_empirical.common import build_parity_report


def legacy_dataset_ref(dataset_id: str, version: str, grain: tuple[str, ...]) -> DatasetRef:
    return DatasetRef(
        dataset_id=dataset_id,
        version=version,
        schema_version="legacy-csv",
        layer=DataLayer.LEGACY_COMPAT,
        authority=AuthorityLevel.L0_LEGACY_INHERITED,
        grain=GrainSpec(keys=grain),
    )


def _key_values(df: pd.DataFrame, key: str) -> pd.Series:
    if key not in df.columns:
        return pd.Series(dtype="string")
    values = df[key].astype("string").str.strip()
    return values[values.notna() & (values != "")]


def _normalized_value(value: Any) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    return None if text in {"", "<NA>", "nan", "None"} else text


def compare_unique_key_tables(
    *,
    legacy: pd.DataFrame,
    new: pd.DataFrame,
    key: str,
    legacy_ref: DatasetRef,
    new_ref: DatasetRef,
    mapped_fields: Iterable[str] | None = None,
    explained_reason_categories: Mapping[str, int] | None = None,
    explained_fields: Iterable[str] = (),
) -> dict[str, Any]:
    """Compare source rows by an exact source key without demanding byte equality."""
    legacy_keys = _key_values(legacy, key)
    new_keys = _key_values(new, key)
    legacy_set = set(legacy_keys.tolist())
    new_set = set(new_keys.tolist())
    overlap = sorted(legacy_set & new_set)
    legacy_only = sorted(legacy_set - new_set)
    new_only = sorted(new_set - legacy_set)
    legacy_duplicate_rows = int(legacy_keys.duplicated(keep=False).sum())
    new_duplicate_rows = int(new_keys.duplicated(keep=False).sum())

    if mapped_fields is None:
        fields = sorted((set(legacy.columns) & set(new.columns)) - {key})
    else:
        fields = [field for field in mapped_fields if field in legacy.columns and field in new.columns]

    changed_fields: dict[str, int] = {}
    if legacy_duplicate_rows == 0 and new_duplicate_rows == 0 and overlap:
        legacy_index = legacy[key].astype("string").str.strip()
        new_index = new[key].astype("string").str.strip()
        legacy_indexed = legacy.assign(_parity_key=legacy_index).set_index("_parity_key")
        new_indexed = new.assign(_parity_key=new_index).set_index("_parity_key")
        for field in fields:
            changed = 0
            for source_id in overlap:
                if _normalized_value(legacy_indexed.at[source_id, field]) != _normalized_value(
                    new_indexed.at[source_id, field]
                ):
                    changed += 1
            if changed:
                changed_fields[field] = changed

    explained = dict(explained_reason_categories or {})
    explicitly_explained_fields = set(explained_fields)
    unexplained_changed_fields = sorted(set(changed_fields) - explicitly_explained_fields)
    discrepancy_categories: dict[str, int] = {
        "legacy_only_ids": len(legacy_only),
        "new_only_ids": len(new_only),
        "legacy_duplicate_key_rows": legacy_duplicate_rows,
        "new_duplicate_key_rows": new_duplicate_rows,
        "changed_mapped_cells": sum(changed_fields.values()),
        "unexplained_changed_fields": len(unexplained_changed_fields),
        **explained,
    }
    discrepancy_categories = {
        category: count for category, count in discrepancy_categories.items() if count
    }

    structural_difference = bool(
        legacy_only
        or new_only
        or legacy_duplicate_rows
        or new_duplicate_rows
        or len(legacy) != len(new)
    )
    if not structural_difference and not changed_fields:
        status = "EQUAL"
    elif explained and not structural_difference and not unexplained_changed_fields:
        status = "EXPLAINED_DIVERGENCE"
    else:
        status = "UNEXPLAINED_DIVERGENCE"

    summary = build_parity_report(
        legacy_dataset=legacy_ref,
        new_dataset=new_ref,
        key_overlap=len(overlap),
        legacy_rows=len(legacy),
        new_rows=len(new),
        total_comparisons=len(overlap) * len(fields),
        legacy_only=len(legacy_only),
        new_only=len(new_only),
        numerical_differences=sum(changed_fields.values()),
        discrepancy_categories=discrepancy_categories,
        status=status,
    )
    return {
        "summary": summary,
        "key": key,
        "legacy_only_ids": legacy_only,
        "new_only_ids": new_only,
        "changed_mapped_fields": changed_fields,
        "unexplained_changed_fields": unexplained_changed_fields,
        "reason_categories": explained,
        "explained_fields": sorted(explicitly_explained_fields),
    }
