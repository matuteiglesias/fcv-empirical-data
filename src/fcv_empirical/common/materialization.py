from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from empirical_contracts import DatasetRef, QAResult, RunManifest, SourceSnapshotRef
from spatial_foundation import DataRoot, sha256_file

PACKAGE_NAME = "fcv-empirical-data"
RUN_NAMESPACE = "fcv-empirical-data"
MATERIALIZATION_STATUS_CHECK_ID = "materialization.status"
PUBLICATION_TRANSACTION_FILE = "publication_transaction.json"
PUBLICATION_TRANSACTION_SCHEMA = "fcv-materialization-publication-v1"


@dataclass(frozen=True)
class FileMaterialization:
    """One file-shaped durable output requested by a source adapter.

    ``destination_base`` is optional. When omitted, the artifact is published under
    the run directory. When supplied, it must live inside the shared ``DataRoot``;
    this lets source adapters publish canonical Silver/Gold products while retaining
    the same staging, hashing, overwrite, failure, and manifest behavior.
    """

    dataset: DatasetRef
    relative_path: str | PurePosixPath
    writer: Callable[[Path], None]
    destination_base: Path | None = None


def _package_version() -> str:
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "0+unknown"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _component(value: str) -> str:
    if not value:
        raise ValueError("path identity components must be non-empty")
    return quote(value, safe="-_.")


def _relative_path(value: str | PurePosixPath) -> Path:
    raw = str(value)
    if "\\" in raw:
        raise ValueError("relative output paths must use POSIX separators")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("relative output paths must stay within the materialization directory")
    return Path(*path.parts)


def run_path(data_root: DataRoot, run_id: str) -> Path:
    """Return the stable FCV empirical run directory without creating it."""
    return data_root.run(RUN_NAMESPACE, _component(run_id))


def run_artifact_path(
    data_root: DataRoot,
    run_id: str,
    relative_path: str | PurePosixPath,
) -> Path:
    """Return a confined path for a non-dataset run artifact."""
    return run_path(data_root, run_id) / "artifacts" / _relative_path(relative_path)


def output_path(
    data_root: DataRoot,
    run_id: str,
    dataset: DatasetRef,
    relative_path: str | PurePosixPath,
) -> Path:
    """Construct a deterministic run-scoped path for one durable output file."""
    return (
        run_path(data_root, run_id)
        / "outputs"
        / dataset.layer.value
        / _component(dataset.dataset_id)
        / _component(dataset.version)
        / _relative_path(relative_path)
    )


def _destination_for(
    data_root: DataRoot,
    run_id: str,
    request: FileMaterialization,
) -> Path:
    if request.destination_base is None:
        return output_path(data_root, run_id, request.dataset, request.relative_path)

    root = data_root.root.resolve()
    base = Path(request.destination_base).expanduser().resolve()
    if base != root and root not in base.parents:
        raise ValueError("stable destination_base must remain inside the shared DataRoot")
    return base / _relative_path(request.relative_path)


def _publish_staged(staged: Path, destination: Path, *, overwrite: bool) -> None:
    """Publish a staged file, atomically refusing an existing destination by default."""
    if overwrite:
        os.replace(staged, destination)
        return
    try:
        os.link(staged, destination)
    except FileExistsError as error:
        raise FileExistsError(f"refusing to overwrite existing artifact: {destination}") from error
    staged.unlink()


def _atomic_write_text(path: Path, content: str, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, staged_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _publish_staged(staged, path, overwrite=overwrite)
    finally:
        staged.unlink(missing_ok=True)


def persist_run_manifest(
    data_root: DataRoot,
    manifest: RunManifest,
    *,
    overwrite: bool = False,
) -> Path:
    """Persist the shared RunManifest contract under the FCV run namespace."""
    if manifest.package != PACKAGE_NAME:
        raise ValueError(f"manifest package must be {PACKAGE_NAME!r}")
    path = run_path(data_root, manifest.run_id) / "run_manifest.json"
    content = json.dumps(manifest.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
    _atomic_write_text(path, content, overwrite=overwrite)
    return path


def persist_run_artifact(
    data_root: DataRoot,
    run_id: str,
    relative_path: str | PurePosixPath,
    content: str,
    *,
    overwrite: bool = False,
) -> Path:
    """Persist a deterministic text/JSON sidecar under the run artifact namespace."""
    path = run_artifact_path(data_root, run_id, relative_path)
    _atomic_write_text(path, content, overwrite=overwrite)
    return path


def _status_result(
    *,
    state: str,
    message: str,
    requested_output_count: int,
    published_output_count: int,
) -> QAResult:
    return QAResult(
        check_id=MATERIALIZATION_STATUS_CHECK_ID,
        state=state,
        message=message,
        metrics={
            "status": "succeeded" if state == "GREEN" else "failed",
            "requested_output_count": requested_output_count,
            "published_output_count": published_output_count,
        },
    )


def _with_content_hash(dataset: DatasetRef, digest: str) -> DatasetRef:
    payload = dataset.model_dump(mode="python")
    payload["content_sha256"] = digest
    return DatasetRef.model_validate(payload)


def _validate_caller_qa(qa: Iterable[QAResult]) -> tuple[QAResult, ...]:
    results = tuple(qa)
    if any(result.check_id == MATERIALIZATION_STATUS_CHECK_ID for result in results):
        raise ValueError(f"{MATERIALIZATION_STATUS_CHECK_ID!r} is reserved by the kernel")
    return results


def _resolve_inputs(
    *,
    source_snapshot: SourceSnapshotRef | None,
    inputs: Iterable[SourceSnapshotRef | DatasetRef] | None,
) -> tuple[SourceSnapshotRef | DatasetRef, ...]:
    if source_snapshot is not None and inputs is not None:
        raise ValueError("provide either source_snapshot or inputs, not both")
    if source_snapshot is not None:
        return (source_snapshot,)
    if inputs is None:
        raise ValueError("materialization requires source_snapshot or inputs")
    refs = tuple(inputs)
    if not refs:
        raise ValueError("materialization inputs must be non-empty")
    return refs


def _failed_manifest(
    *,
    run_id: str,
    input_refs: tuple[SourceSnapshotRef | DatasetRef, ...],
    parameters: Mapping[str, Any],
    code_commit: str | None,
    started_at: datetime,
    qa: tuple[QAResult, ...],
    requested_output_count: int,
    published_output_count: int,
    error: Exception,
) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        package=PACKAGE_NAME,
        package_version=_package_version(),
        code_commit=code_commit,
        started_at=started_at,
        finished_at=_utcnow(),
        inputs=input_refs,
        parameters=dict(parameters),
        outputs=(),
        qa=(
            *qa,
            _status_result(
                state="RED",
                message=f"materialization failed: {type(error).__name__}: {error}",
                requested_output_count=requested_output_count,
                published_output_count=published_output_count,
            ),
        ),
    )


def _success_manifest(
    *,
    run_id: str,
    input_refs: tuple[SourceSnapshotRef | DatasetRef, ...],
    parameters: Mapping[str, Any],
    code_commit: str | None,
    started_at: datetime,
    qa: tuple[QAResult, ...],
    outputs: tuple[DatasetRef, ...],
) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        package=PACKAGE_NAME,
        package_version=_package_version(),
        code_commit=code_commit,
        started_at=started_at,
        finished_at=_utcnow(),
        inputs=input_refs,
        parameters=dict(parameters),
        outputs=outputs,
        qa=(
            *qa,
            _status_result(
                state="GREEN",
                message="materialization completed",
                requested_output_count=len(outputs),
                published_output_count=len(outputs),
            ),
        ),
    )


def _stage_output(
    request: FileMaterialization,
    destination: Path,
) -> tuple[Path, DatasetRef]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, staged_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".staged", dir=destination.parent
    )
    os.close(fd)
    staged = Path(staged_name)
    try:
        request.writer(staged)
        if not staged.is_file():
            raise RuntimeError("writer did not produce a regular file")
        digest = sha256_file(staged)
        if request.dataset.content_sha256 is not None and request.dataset.content_sha256 != digest:
            raise ValueError(
                "materialized content hash does not match the predeclared DatasetRef hash"
            )
        return staged, _with_content_hash(request.dataset, digest)
    except Exception:
        staged.unlink(missing_ok=True)
        raise


def _publication_transaction_path(data_root: DataRoot, run_id: str) -> Path:
    return run_path(data_root, run_id) / PUBLICATION_TRANSACTION_FILE


def _destination_relative_to_root(data_root: DataRoot, destination: Path) -> str:
    root = data_root.root.resolve()
    resolved = destination.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("publication transaction destination must remain inside the shared DataRoot")
    return resolved.relative_to(root).as_posix()


def _resolve_transaction_destination(data_root: DataRoot, relative: str) -> Path:
    root = data_root.root.resolve()
    candidate = (root / _relative_path(relative)).resolve()
    if candidate != root and root not in candidate.parents:
        raise RuntimeError("publication transaction destination escapes the shared DataRoot")
    return candidate


def _dataset_identity_without_hash(dataset: DatasetRef) -> dict[str, Any]:
    payload = dataset.model_dump(mode="json")
    payload["content_sha256"] = None
    return payload


def _persist_publication_transaction(
    *,
    data_root: DataRoot,
    run_id: str,
    manifest: RunManifest,
    destinations: tuple[Path, ...],
) -> Path:
    if len(destinations) != len(manifest.outputs):
        raise RuntimeError("publication transaction output count does not match manifest")
    entries = []
    for destination, dataset in zip(destinations, manifest.outputs, strict=True):
        if dataset.content_sha256 is None:
            raise RuntimeError("publication transaction requires hashed output DatasetRefs")
        entries.append(
            {
                "dataset_id": dataset.dataset_id,
                "destination": _destination_relative_to_root(data_root, destination),
                "sha256": dataset.content_sha256,
            }
        )
    payload = {
        "schema_version": PUBLICATION_TRANSACTION_SCHEMA,
        "run_id": run_id,
        "manifest": manifest.model_dump(mode="json"),
        "outputs": entries,
    }
    path = _publication_transaction_path(data_root, run_id)
    _atomic_write_text(
        path,
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        overwrite=False,
    )
    return path


def _load_publication_transaction(
    *,
    data_root: DataRoot,
    run_id: str,
) -> tuple[RunManifest, tuple[dict[str, str], ...]]:
    path = _publication_transaction_path(data_root, run_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise RuntimeError(f"invalid publication transaction for run {run_id!r}") from error
    if payload.get("schema_version") != PUBLICATION_TRANSACTION_SCHEMA:
        raise RuntimeError("unsupported publication transaction schema")
    if payload.get("run_id") != run_id:
        raise RuntimeError("publication transaction run_id does not match requested run")
    try:
        manifest = RunManifest.model_validate(payload["manifest"])
    except Exception as error:
        raise RuntimeError("publication transaction contains an invalid RunManifest") from error
    raw_entries = payload.get("outputs")
    if not isinstance(raw_entries, list) or len(raw_entries) != len(manifest.outputs):
        raise RuntimeError("publication transaction output metadata is incomplete")
    entries: list[dict[str, str]] = []
    for raw, dataset in zip(raw_entries, manifest.outputs, strict=True):
        if not isinstance(raw, dict):
            raise TypeError("publication transaction output entry is invalid")
        destination = raw.get("destination")
        digest = raw.get("sha256")
        dataset_id = raw.get("dataset_id")
        if not isinstance(destination, str) or not isinstance(digest, str):
            raise TypeError("publication transaction output entry is incomplete")
        if dataset_id != dataset.dataset_id or digest != dataset.content_sha256:
            raise RuntimeError("publication transaction output metadata disagrees with manifest")
        entries.append(
            {"destination": destination, "sha256": digest, "dataset_id": str(dataset_id)}
        )
    return manifest, tuple(entries)


def _validate_recovery_request(
    *,
    prepared: RunManifest,
    entries: tuple[dict[str, str], ...],
    input_refs: tuple[SourceSnapshotRef | DatasetRef, ...],
    parameters: Mapping[str, Any],
    code_commit: str | None,
    requests: tuple[FileMaterialization, ...],
    destinations: tuple[Path, ...],
    data_root: DataRoot,
) -> None:
    if prepared.inputs != input_refs:
        raise RuntimeError("publication recovery inputs do not match the prepared transaction")
    if prepared.parameters != dict(parameters):
        raise RuntimeError("publication recovery parameters do not match the prepared transaction")
    if prepared.code_commit != code_commit:
        raise RuntimeError("publication recovery code_commit does not match the prepared transaction")
    if len(requests) != len(prepared.outputs) or len(destinations) != len(entries):
        raise RuntimeError("publication recovery output count does not match the prepared transaction")
    for request, prepared_dataset, destination, entry in zip(
        requests, prepared.outputs, destinations, entries, strict=True
    ):
        if _dataset_identity_without_hash(request.dataset) != _dataset_identity_without_hash(
            prepared_dataset
        ):
            raise RuntimeError("publication recovery DatasetRef does not match prepared transaction")
        relative = _destination_relative_to_root(data_root, destination)
        if relative != entry["destination"]:
            raise RuntimeError("publication recovery destination does not match prepared transaction")


def _recover_publication_transaction(
    *,
    data_root: DataRoot,
    run_id: str,
    manifest_destination: Path,
    input_refs: tuple[SourceSnapshotRef | DatasetRef, ...],
    parameters: Mapping[str, Any],
    code_commit: str | None,
    requests: tuple[FileMaterialization, ...],
    destinations: tuple[Path, ...],
) -> RunManifest | None:
    transaction_path = _publication_transaction_path(data_root, run_id)
    if not transaction_path.exists():
        return None

    prepared, entries = _load_publication_transaction(data_root=data_root, run_id=run_id)
    _validate_recovery_request(
        prepared=prepared,
        entries=entries,
        input_refs=input_refs,
        parameters=parameters,
        code_commit=code_commit,
        requests=requests,
        destinations=destinations,
        data_root=data_root,
    )

    if manifest_destination.exists():
        try:
            persisted = RunManifest.model_validate_json(
                manifest_destination.read_text(encoding="utf-8")
            )
        except Exception as error:
            raise RuntimeError("existing run manifest is invalid during publication recovery") from error
        if persisted != prepared:
            raise RuntimeError("existing run manifest disagrees with prepared publication transaction")
        transaction_path.unlink()
        return persisted

    existing: list[Path] = []
    missing = 0
    for entry in entries:
        destination = _resolve_transaction_destination(data_root, entry["destination"])
        if not destination.exists():
            missing += 1
            continue
        if not destination.is_file():
            raise RuntimeError("publication recovery destination exists but is not a regular file")
        digest = sha256_file(destination)
        if digest != entry["sha256"]:
            raise RuntimeError(
                "publication recovery found destination content that does not match prepared hash"
            )
        existing.append(destination)

    if missing == 0:
        persist_run_manifest(data_root, prepared, overwrite=False)
        transaction_path.unlink()
        return prepared

    for destination in existing:
        destination.unlink()
    transaction_path.unlink()
    return None


def materialize_files(
    *,
    data_root: DataRoot,
    run_id: str,
    outputs: Iterable[FileMaterialization],
    source_snapshot: SourceSnapshotRef | None = None,
    inputs: Iterable[SourceSnapshotRef | DatasetRef] | None = None,
    parameters: Mapping[str, Any] | None = None,
    code_commit: str | None = None,
    qa: Iterable[QAResult] = (),
    overwrite: bool = False,
) -> RunManifest:
    """Materialize one or more files under one shared upstream RunManifest.

    All writers finish and all hashes validate before publication begins. Outputs
    may remain run-scoped or publish into a stable directory inside ``DataRoot``.

    Default no-overwrite publication is both exception-atomic and recoverable across
    abrupt process death. After staging/hashing, a small durable publication
    transaction is written before canonical outputs are linked into place. A later
    invocation with the same run identity can roll forward a fully published,
    hash-matching transaction without recomputation, or remove only hash-verified
    partial publications before retrying. Unknown/mismatched destination bytes fail
    closed. Explicit overwrite remains destructive and cannot promise crash rollback.

    Source rebuilds normally provide ``source_snapshot``. Derived products may
    instead provide upstream ``DatasetRef`` objects via ``inputs`` so lineage says
    Silver -> derived rather than pretending the derived view read Bronze directly.
    """
    requests = tuple(outputs)
    if not requests:
        raise ValueError("at least one output materialization is required")

    input_refs = _resolve_inputs(source_snapshot=source_snapshot, inputs=inputs)
    caller_qa = _validate_caller_qa(qa)
    params = dict(parameters or {})
    started_at = _utcnow()
    manifest_destination = run_path(data_root, run_id) / "run_manifest.json"
    transaction_destination = _publication_transaction_path(data_root, run_id)
    destinations = tuple(_destination_for(data_root, run_id, request) for request in requests)
    if len(set(destinations)) != len(destinations):
        raise ValueError("multiple requested outputs resolve to the same destination")

    if not overwrite:
        recovered = _recover_publication_transaction(
            data_root=data_root,
            run_id=run_id,
            manifest_destination=manifest_destination,
            input_refs=input_refs,
            parameters=params,
            code_commit=code_commit,
            requests=requests,
            destinations=destinations,
        )
        if recovered is not None:
            return recovered
        if manifest_destination.exists():
            raise FileExistsError(
                f"refusing to overwrite existing artifact: {manifest_destination}"
            )
        existing = next((path for path in destinations if path.exists()), None)
        if existing is not None:
            raise FileExistsError(f"refusing to overwrite existing artifact: {existing}")

    staged_outputs: list[tuple[Path, Path, DatasetRef]] = []
    published: list[Path] = []
    transaction_prepared = False
    try:
        for request, destination in zip(requests, destinations, strict=True):
            staged, hashed_dataset = _stage_output(request, destination)
            staged_outputs.append((staged, destination, hashed_dataset))

        hashed_outputs = tuple(dataset for _staged, _destination, dataset in staged_outputs)
        manifest = _success_manifest(
            run_id=run_id,
            input_refs=input_refs,
            parameters=params,
            code_commit=code_commit,
            started_at=started_at,
            qa=caller_qa,
            outputs=hashed_outputs,
        )
        if not overwrite:
            _persist_publication_transaction(
                data_root=data_root,
                run_id=run_id,
                manifest=manifest,
                destinations=destinations,
            )
            transaction_prepared = True

        for staged, destination, _dataset in staged_outputs:
            _publish_staged(staged, destination, overwrite=overwrite)
            published.append(destination)

        persist_run_manifest(data_root, manifest, overwrite=overwrite)
        if transaction_prepared:
            transaction_destination.unlink()
        return manifest
    except Exception as error:
        if not overwrite:
            for path in published:
                path.unlink(missing_ok=True)
            durable_published_count = 0
            if transaction_prepared:
                transaction_destination.unlink(missing_ok=True)
        else:
            durable_published_count = len(published)
        if not manifest_destination.exists() or overwrite:
            failure = _failed_manifest(
                run_id=run_id,
                input_refs=input_refs,
                parameters=params,
                code_commit=code_commit,
                started_at=started_at,
                qa=caller_qa,
                requested_output_count=len(requests),
                published_output_count=durable_published_count,
                error=error,
            )
            persist_run_manifest(data_root, failure, overwrite=overwrite)
        raise
    finally:
        for staged, _destination, _dataset in staged_outputs:
            staged.unlink(missing_ok=True)


def materialize_file(
    *,
    data_root: DataRoot,
    run_id: str,
    output: DatasetRef,
    relative_path: str | PurePosixPath,
    writer: Callable[[Path], None],
    source_snapshot: SourceSnapshotRef | None = None,
    inputs: Iterable[SourceSnapshotRef | DatasetRef] | None = None,
    destination_base: Path | None = None,
    parameters: Mapping[str, Any] | None = None,
    code_commit: str | None = None,
    qa: Iterable[QAResult] = (),
    overwrite: bool = False,
) -> RunManifest:
    """Convenience wrapper for a single file-shaped durable output."""
    return materialize_files(
        data_root=data_root,
        run_id=run_id,
        source_snapshot=source_snapshot,
        inputs=inputs,
        outputs=(
            FileMaterialization(
                dataset=output,
                relative_path=relative_path,
                writer=writer,
                destination_base=destination_base,
            ),
        ),
        parameters=parameters,
        code_commit=code_commit,
        qa=qa,
        overwrite=overwrite,
    )
