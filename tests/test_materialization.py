import hashlib
import json
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

from fcv_empirical.common.materialization import (
    FileMaterialization,
    materialize_file,
    materialize_files,
    output_path,
    run_path,
)


def snapshot() -> SourceSnapshotRef:
    return SourceSnapshotRef(
        source="dummy-source",
        release="2026-08",
        snapshot_id="dummy-2026-08-abc",
        files=(SourceFileRef(path="external/input.csv", sha256="a" * 64, size_bytes=10),),
    )


def dataset(dataset_id: str = "dummy.records", grain: str = "source_record_id") -> DatasetRef:
    return DatasetRef(
        dataset_id=dataset_id,
        version="v1",
        schema_version="1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L1_NORMALIZED,
        grain=GrainSpec(keys=(grain,)),
    )


def test_source_identity_and_hash_survive_materialization(tmp_path: Path) -> None:
    root = DataRoot.from_path(tmp_path)
    source = snapshot()
    payload = b"source_record_id,value\na,1\n"

    manifest = materialize_file(
        data_root=root,
        run_id="run-001",
        source_snapshot=source,
        output=dataset(),
        relative_path="records.csv",
        writer=lambda path: path.write_bytes(payload),
        parameters={"mode": "synthetic"},
        code_commit="deadbeef",
    )

    assert manifest.inputs == (source,)
    assert manifest.code_commit == "deadbeef"
    assert manifest.outputs[0].content_sha256 == hashlib.sha256(payload).hexdigest()
    assert manifest.outputs[0].grain.keys == ("source_record_id",)
    persisted = json.loads((run_path(root, "run-001") / "run_manifest.json").read_text())
    assert persisted["inputs"][0]["source"] == "dummy-source"
    assert persisted["inputs"][0]["snapshot_id"] == "dummy-2026-08-abc"
    assert persisted["outputs"][0]["content_sha256"] == hashlib.sha256(payload).hexdigest()


def test_multiple_outputs_keep_distinct_natural_grains(tmp_path: Path) -> None:
    root = DataRoot.from_path(tmp_path)
    records = dataset("dummy.projects", "project_id")
    locations = dataset("dummy.project_locations", "location_id")

    manifest = materialize_files(
        data_root=root,
        run_id="run-multi",
        source_snapshot=snapshot(),
        outputs=(
            FileMaterialization(
                dataset=records,
                relative_path="projects.csv",
                writer=lambda path: path.write_text("project_id\np1\n", encoding="utf-8"),
            ),
            FileMaterialization(
                dataset=locations,
                relative_path="locations.csv",
                writer=lambda path: path.write_text("location_id\nl1\n", encoding="utf-8"),
            ),
        ),
    )

    assert [item.dataset_id for item in manifest.outputs] == [
        "dummy.projects",
        "dummy.project_locations",
    ]
    assert [item.grain.keys for item in manifest.outputs] == [("project_id",), ("location_id",)]
    assert all(item.content_sha256 for item in manifest.outputs)


def test_output_path_is_deterministic_and_confined(tmp_path: Path) -> None:
    root = DataRoot.from_path(tmp_path)
    first = output_path(root, "run 1", dataset(), "part/data.csv")
    second = output_path(root, "run 1", dataset(), "part/data.csv")
    assert first == second
    assert "runs/fcv-empirical-data" in first.as_posix()
    with pytest.raises(ValueError):
        output_path(root, "run 1", dataset(), "../escape.csv")
    with pytest.raises(ValueError):
        output_path(root, "run 1", dataset(), "..\\escape.csv")


def test_overwrite_is_refused_unless_explicit(tmp_path: Path) -> None:
    root = DataRoot.from_path(tmp_path)
    kwargs = {
        "data_root": root,
        "run_id": "run-overwrite",
        "source_snapshot": snapshot(),
        "output": dataset(),
        "relative_path": "records.csv",
        "writer": lambda path: path.write_text("first\n", encoding="utf-8"),
    }
    materialize_file(**kwargs)
    with pytest.raises(FileExistsError):
        materialize_file(**kwargs)

    changed = dict(kwargs)
    changed["writer"] = lambda path: path.write_text("second\n", encoding="utf-8")
    materialize_file(**changed, overwrite=True)
    destination = output_path(root, "run-overwrite", dataset(), "records.csv")
    assert destination.read_text(encoding="utf-8") == "second\n"


def test_failure_is_persisted_and_has_no_successful_output(tmp_path: Path) -> None:
    root = DataRoot.from_path(tmp_path)

    def fail(_path: Path) -> None:
        raise RuntimeError("synthetic failure")

    with pytest.raises(RuntimeError, match="synthetic failure"):
        materialize_file(
            data_root=root,
            run_id="run-fail",
            source_snapshot=snapshot(),
            output=dataset(),
            relative_path="records.csv",
            writer=fail,
        )

    persisted = json.loads((run_path(root, "run-fail") / "run_manifest.json").read_text())
    assert persisted["outputs"] == []
    status = next(item for item in persisted["qa"] if item["check_id"] == "materialization.status")
    assert status["state"] == "RED"
    assert status["metrics"]["status"] == "failed"
    assert status["metrics"]["requested_output_count"] == 1
    assert status["metrics"]["published_output_count"] == 0
    assert not list(run_path(root, "run-fail").rglob("*.staged"))


def test_multi_output_writer_failure_publishes_nothing(tmp_path: Path) -> None:
    root = DataRoot.from_path(tmp_path)
    first = dataset("dummy.first", "first_id")
    second = dataset("dummy.second", "second_id")

    def fail(_path: Path) -> None:
        raise RuntimeError("second writer failed")

    with pytest.raises(RuntimeError, match="second writer failed"):
        materialize_files(
            data_root=root,
            run_id="run-multi-fail",
            source_snapshot=snapshot(),
            outputs=(
                FileMaterialization(
                    dataset=first,
                    relative_path="first.csv",
                    writer=lambda path: path.write_text("first_id\na\n", encoding="utf-8"),
                ),
                FileMaterialization(
                    dataset=second,
                    relative_path="second.csv",
                    writer=fail,
                ),
            ),
        )

    assert not output_path(root, "run-multi-fail", first, "first.csv").exists()
    assert not output_path(root, "run-multi-fail", second, "second.csv").exists()
    persisted = json.loads((run_path(root, "run-multi-fail") / "run_manifest.json").read_text())
    assert persisted["outputs"] == []
