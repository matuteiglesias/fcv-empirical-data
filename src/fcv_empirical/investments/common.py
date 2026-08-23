from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from empirical_contracts import DatasetRef, QAResult, RunManifest, SourceSnapshotRef
from spatial_foundation import DataRoot

from fcv_empirical.common import persist_run_artifact, serialize_qa


@dataclass(frozen=True)
class InvestmentMaterializationResult:
    """Paths and contracts produced by one source/derived materialization."""

    manifest: RunManifest
    datasets: dict[str, DatasetRef]
    paths: dict[str, Path]
    qa: tuple[QAResult, ...]
    parity: dict[str, Any]


def json_text(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def coverage_profile(df: pd.DataFrame, table_name: str) -> pd.DataFrame:
    """Return column-level coverage without assigning meaning to missingness."""
    rows: list[dict[str, Any]] = []
    n_rows = len(df)
    for column in df.columns:
        values = df[column]
        present = values.notna() & (values.astype("string").str.strip().fillna("") != "")
        rows.append(
            {
                "table_name": table_name,
                "column_name": str(column),
                "row_count": n_rows,
                "nonmissing_count": int(present.sum()),
                "missing_count": int(n_rows - present.sum()),
                "nonmissing_share": float(present.mean()) if n_rows else None,
                "unique_nonmissing": int(values[present].astype("string").nunique()),
            }
        )
    return pd.DataFrame(rows)


def persist_contract_artifacts(
    *,
    data_root: DataRoot,
    run_id: str,
    manifest: RunManifest,
    qa: tuple[QAResult, ...],
    parity: dict[str, Any],
    overwrite: bool,
) -> None:
    """Persist human/audit sidecars alongside the contract-backed RunManifest."""
    refs = [dataset.model_dump(mode="json") for dataset in manifest.outputs]
    inputs = []
    for ref in manifest.inputs:
        kind = "source_snapshot" if isinstance(ref, SourceSnapshotRef) else "dataset"
        inputs.append({"kind": kind, "ref": ref.model_dump(mode="json")})

    persist_run_artifact(
        data_root,
        run_id,
        "qa/qa_results.json",
        serialize_qa(qa),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "contracts/dataset_refs.json",
        json_text(refs),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "provenance/inputs.json",
        json_text(inputs),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "parity/parity_report.json",
        json_text(parity),
        overwrite=overwrite,
    )


def blocked_parity(reason: str) -> dict[str, Any]:
    """Represent absent legacy evidence honestly rather than manufacturing counts."""
    return {"status": "NOT_RUN", "reason": reason}
