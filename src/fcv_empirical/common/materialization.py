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


@dataclass(frozen=True)
class FileMaterialization:
    """One file-shaped durable output requested by a source adapter."""

    dataset: DatasetRef
    relative_path: str | PurePosixPath
    writer: Callable[[Path], None]


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


def output_path(
    data_root: DataRoot,
    run_id: str,
    dataset: DatasetRef,
    relative_path: str | PurePosixPath,
) -> Path:
    """Construct a deterministic path for one durable output file."""
    return (
        run_path(data_root, run_id)
        / "outputs"
        / dataset.layer.value
        / _component(dataset.dataset_id)
        / _component(dataset.version)
        / _relative_path(relative_path)
    )


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


def _failed_manifest(
    *,
    run_id: str,
    source_snapshot: SourceSnapshotRef,
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
        inputs=(source_snapshot,),
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


def materialize_files(
    *,
    data_root: DataRoot,
    run_id: str,
    source_snapshot: SourceSnapshotRef,
    outputs: Iterable[FileMaterialization],
    parameters: Mapping[str, Any] | None = None,
    code_commit: str | None = None,
    qa: Iterable[QAResult] = (),
    overwrite: bool = False,
) -> RunManifest:
    """Materialize one or more files under one shared upstream RunManifest.

    All writers finish and all hashes validate before publication begins. With the
    default no-overwrite policy, publication is no-clobber and published files are
    removed if a later publication or manifest write fails. Explicit overwrite is
    destructive and cannot promise rollback of prior artifacts.
    """
    requests = tuple(outputs)
    if not requests:
        raise ValueError("at least one output materialization is required")

    caller_qa = _validate_caller_qa(qa)
    params = dict(parameters or {})
    started_at = _utcnow()
    manifest_destination = run_path(data_root, run_id) / "run_manifest.json"
    destinations = tuple(
        output_path(data_root, run_id, request.dataset, request.relative_path)
        for request in requests
    )
    if len(set(destinations)) != len(destinations):
        raise ValueError("multiple requested outputs resolve to the same destination")
    if not overwrite:
        if manifest_destination.exists():
            raise FileExistsError(
                f"refusing to overwrite existing artifact: {manifest_destination}"
            )
        existing = next((path for path in destinations if path.exists()), None)
        if existing is not None:
            raise FileExistsError(f"refusing to overwrite existing artifact: {existing}")

    staged_outputs: list[tuple[Path, Path, DatasetRef]] = []
    published: list[Path] = []
    try:
        for request, destination in zip(requests, destinations, strict=True):
            staged, hashed_dataset = _stage_output(request, destination)
            staged_outputs.append((staged, destination, hashed_dataset))

        for staged, destination, _dataset in staged_outputs:
            _publish_staged(staged, destination, overwrite=overwrite)
            published.append(destination)

        hashed_outputs = tuple(dataset for _staged, _destination, dataset in staged_outputs)
        manifest = RunManifest(
            run_id=run_id,
            package=PACKAGE_NAME,
            package_version=_package_version(),
            code_commit=code_commit,
            started_at=started_at,
            finished_at=_utcnow(),
            inputs=(source_snapshot,),
            parameters=params,
            outputs=hashed_outputs,
            qa=(
                *caller_qa,
                _status_result(
                    state="GREEN",
                    message="materialization completed",
                    requested_output_count=len(requests),
                    published_output_count=len(hashed_outputs),
                ),
            ),
        )
        persist_run_manifest(data_root, manifest, overwrite=overwrite)
        return manifest
    except Exception as error:
        if not overwrite:
            for path in published:
                path.unlink(missing_ok=True)
            durable_published_count = 0
        else:
            durable_published_count = len(published)
        if not manifest_destination.exists() or overwrite:
            failure = _failed_manifest(
                run_id=run_id,
                source_snapshot=source_snapshot,
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
    source_snapshot: SourceSnapshotRef,
    output: DatasetRef,
    relative_path: str | PurePosixPath,
    writer: Callable[[Path], None],
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
        outputs=(
            FileMaterialization(dataset=output, relative_path=relative_path, writer=writer),
        ),
        parameters=parameters,
        code_commit=code_commit,
        qa=qa,
        overwrite=overwrite,
    )
