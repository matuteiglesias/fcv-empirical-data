import hashlib
from pathlib import Path

import pytest
from empirical_contracts import (
    AuthorityLevel,
    DataLayer,
    DatasetRef,
    GrainSpec,
    SourceFileRef,
    SourceSnapshotRef,
)
from spatial_foundation import DataRoot

from fcv_empirical.common import materialization
from fcv_empirical.common.materialization import (
    FileMaterialization,
    PUBLICATION_TRANSACTION_FILE,
    materialize_file,
    materialize_files,
    output_path,
    run_path,
)


def _snapshot() -> SourceSnapshotRef:
    return SourceSnapshotRef(
        source="dummy-source",
        release="2026-09",
        snapshot_id="dummy-2026-09-crash-recovery",
        files=(
            SourceFileRef(
                path="external/input.csv",
                sha256="a" * 64,
                size_bytes=10,
            ),
        ),
    )


def _dataset(dataset_id: str = "dummy.records", grain: str = "record_id") -> DatasetRef:
    return DatasetRef(
        dataset_id=dataset_id,
        version="v1",
        schema_version="1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L1_NORMALIZED,
        grain=GrainSpec(keys=(grain,)),
    )


def test_hard_stop_after_publication_rolls_forward_without_rerunning_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = DataRoot.from_path(tmp_path)
    payload = b"record_id,value\na,1\n"
    writer_calls = 0

    def writer(path: Path) -> None:
        nonlocal writer_calls
        writer_calls += 1
        path.write_bytes(payload)

    original_persist = materialization.persist_run_manifest

    def hard_stop(*_args, **_kwargs):
        raise SystemExit("synthetic hard stop after output publication")

    monkeypatch.setattr(materialization, "persist_run_manifest", hard_stop)
    kwargs = {
        "data_root": root,
        "run_id": "run-hard-stop-roll-forward",
        "source_snapshot": _snapshot(),
        "output": _dataset(),
        "relative_path": "records.csv",
        "writer": writer,
        "parameters": {"mode": "crash-recovery"},
        "code_commit": "deadbeef",
    }

    with pytest.raises(SystemExit, match="synthetic hard stop"):
        materialize_file(**kwargs)

    destination = output_path(root, kwargs["run_id"], kwargs["output"], "records.csv")
    transaction = run_path(root, kwargs["run_id"]) / PUBLICATION_TRANSACTION_FILE
    manifest_path = run_path(root, kwargs["run_id"]) / "run_manifest.json"
    assert destination.read_bytes() == payload
    assert transaction.exists()
    assert not manifest_path.exists()
    assert writer_calls == 1

    monkeypatch.setattr(materialization, "persist_run_manifest", original_persist)
    recovered = materialize_file(**kwargs)

    assert writer_calls == 1
    assert recovered.outputs[0].content_sha256 == hashlib.sha256(payload).hexdigest()
    assert manifest_path.exists()
    assert not transaction.exists()
    assert destination.read_bytes() == payload


def test_partial_publication_is_hash_verified_rolled_back_and_recomputed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = DataRoot.from_path(tmp_path)
    first = _dataset("dummy.first", "first_id")
    second = _dataset("dummy.second", "second_id")
    writer_calls = {"first": 0, "second": 0}

    def first_writer(path: Path) -> None:
        writer_calls["first"] += 1
        path.write_text("first_id\na\n", encoding="utf-8")

    def second_writer(path: Path) -> None:
        writer_calls["second"] += 1
        path.write_text("second_id\nb\n", encoding="utf-8")

    original_publish = materialization._publish_staged
    crash_enabled = True

    def crash_during_second_publish(staged: Path, destination: Path, *, overwrite: bool) -> None:
        nonlocal crash_enabled
        if crash_enabled and destination.name == "second.csv":
            raise SystemExit("synthetic hard stop during partial publication")
        original_publish(staged, destination, overwrite=overwrite)

    monkeypatch.setattr(materialization, "_publish_staged", crash_during_second_publish)
    run_id = "run-partial-publication"
    requests = (
        FileMaterialization(
            dataset=first,
            relative_path="first.csv",
            writer=first_writer,
        ),
        FileMaterialization(
            dataset=second,
            relative_path="second.csv",
            writer=second_writer,
        ),
    )

    with pytest.raises(SystemExit, match="partial publication"):
        materialize_files(
            data_root=root,
            run_id=run_id,
            source_snapshot=_snapshot(),
            outputs=requests,
            parameters={"mode": "partial-recovery"},
            code_commit="deadbeef",
        )

    first_path = output_path(root, run_id, first, "first.csv")
    second_path = output_path(root, run_id, second, "second.csv")
    transaction = run_path(root, run_id) / PUBLICATION_TRANSACTION_FILE
    assert first_path.exists()
    assert not second_path.exists()
    assert transaction.exists()
    assert writer_calls == {"first": 1, "second": 1}

    crash_enabled = False
    manifest = materialize_files(
        data_root=root,
        run_id=run_id,
        source_snapshot=_snapshot(),
        outputs=requests,
        parameters={"mode": "partial-recovery"},
        code_commit="deadbeef",
    )

    assert writer_calls == {"first": 2, "second": 2}
    assert [item.dataset_id for item in manifest.outputs] == ["dummy.first", "dummy.second"]
    assert first_path.exists()
    assert second_path.exists()
    assert not transaction.exists()


def test_recovery_fails_closed_if_published_bytes_no_longer_match_prepared_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = DataRoot.from_path(tmp_path)
    payload = b"record_id,value\na,1\n"
    writer_calls = 0

    def writer(path: Path) -> None:
        nonlocal writer_calls
        writer_calls += 1
        path.write_bytes(payload)

    original_persist = materialization.persist_run_manifest

    def hard_stop(*_args, **_kwargs):
        raise SystemExit("synthetic hard stop")

    monkeypatch.setattr(materialization, "persist_run_manifest", hard_stop)
    kwargs = {
        "data_root": root,
        "run_id": "run-mismatched-orphan",
        "source_snapshot": _snapshot(),
        "output": _dataset(),
        "relative_path": "records.csv",
        "writer": writer,
        "parameters": {"mode": "mismatch"},
        "code_commit": "deadbeef",
    }

    with pytest.raises(SystemExit):
        materialize_file(**kwargs)

    destination = output_path(root, kwargs["run_id"], kwargs["output"], "records.csv")
    transaction = run_path(root, kwargs["run_id"]) / PUBLICATION_TRANSACTION_FILE
    destination.write_bytes(b"different bytes\n")
    monkeypatch.setattr(materialization, "persist_run_manifest", original_persist)

    with pytest.raises(RuntimeError, match="does not match prepared hash"):
        materialize_file(**kwargs)

    assert destination.read_bytes() == b"different bytes\n"
    assert transaction.exists()
    assert writer_calls == 1
