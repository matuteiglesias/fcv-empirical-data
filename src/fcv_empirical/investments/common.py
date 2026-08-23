from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from empirical_contracts import DatasetRef, QAResult, RunManifest, SourceSnapshotRef
from spatial_foundation import DataRoot, sha256_file

from fcv_empirical.common import persist_run_artifact, serialize_qa


@dataclass(frozen=True)
class InvestmentMaterializationResult:
    """Paths and contracts produced by one source/derived materialization."""

    manifest: RunManifest
    datasets: dict[str, DatasetRef]
    paths: dict[str, Path]
    qa: tuple[QAResult, ...]
    parity: dict[str, Any]


def validate_source_snapshot(
    snapshot: SourceSnapshotRef,
    *,
    expected_source: str,
    expected_release: str | None = None,
    required_paths: Iterable[str | Path] = (),
    exact_paths: Iterable[str | Path] | None = None,
) -> None:
    """Verify that an external snapshot still identifies the bytes an adapter will read."""
    if snapshot.source != expected_source:
        raise ValueError(
            f"snapshot source {snapshot.source!r} does not match expected source {expected_source!r}"
        )
    if expected_release is not None and snapshot.release != expected_release:
        raise ValueError(
            f"snapshot release {snapshot.release!r} does not match requested release "
            f"{expected_release!r}"
        )

    refs_by_path: dict[Path, list[Any]] = {}
    for ref in snapshot.files:
        resolved = Path(ref.path).expanduser().resolve()
        refs_by_path.setdefault(resolved, []).append(ref)

    duplicate_paths = [path for path, refs in refs_by_path.items() if len(refs) != 1]
    if duplicate_paths:
        raise ValueError("snapshot contains duplicate source file identities")

    for required in required_paths:
        resolved = Path(required).expanduser().resolve()
        if resolved not in refs_by_path:
            raise ValueError(
                f"source path must be represented exactly once in SourceSnapshotRef: {resolved}"
            )

    if exact_paths is not None:
        current_paths = {Path(path).expanduser().resolve() for path in exact_paths}
        if current_paths != set(refs_by_path):
            raise ValueError("source file set changed after snapshot registration")

    for path, refs in refs_by_path.items():
        current = sha256_file(path)
        if current != refs[0].sha256:
            raise ValueError(f"source file changed after snapshot registration: {path}")


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
