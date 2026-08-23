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

from fcv_empirical.common import materialize_file


def snapshot() -> SourceSnapshotRef:
    return SourceSnapshotRef(
        source="synthetic",
        release="v1",
        snapshot_id="synthetic-v1",
        files=(SourceFileRef(path="external/source.csv", sha256="a" * 64, size_bytes=1),),
    )


def ref(dataset_id: str, layer: DataLayer, authority: AuthorityLevel) -> DatasetRef:
    return DatasetRef(
        dataset_id=dataset_id,
        version="v1",
        schema_version="1",
        layer=layer,
        authority=authority,
        grain=GrainSpec(keys=("source_id",)),
    )


def test_materializer_can_publish_to_stable_layer_path(tmp_path: Path) -> None:
    root = DataRoot.from_path(tmp_path)
    silver = root.silver("investments", "synthetic", "v1")
    output = ref("investments.synthetic.records", DataLayer.SILVER, AuthorityLevel.L3_REBUILT)

    manifest = materialize_file(
        data_root=root,
        run_id="stable-silver",
        source_snapshot=snapshot(),
        output=output,
        destination_base=silver,
        relative_path="records.csv",
        writer=lambda path: path.write_text("source_id\na\n", encoding="utf-8"),
    )

    assert (silver / "records.csv").exists()
    assert manifest.outputs[0].content_sha256 is not None

    with pytest.raises(ValueError, match="shared DataRoot"):
        materialize_file(
            data_root=root,
            run_id="escape",
            source_snapshot=snapshot(),
            output=output,
            destination_base=tmp_path.parent / "outside",
            relative_path="records.csv",
            writer=lambda path: path.write_text("x", encoding="utf-8"),
        )


def test_derived_manifest_can_point_to_silver_dataset_inputs(tmp_path: Path) -> None:
    root = DataRoot.from_path(tmp_path)
    upstream = ref("investments.synthetic.records", DataLayer.SILVER, AuthorityLevel.L3_REBUILT)
    derived = ref("investments.synthetic.derived", DataLayer.GOLD, AuthorityLevel.L2_DERIVED)
    gold = root.gold("investments", "synthetic_derived", "v1")

    manifest = materialize_file(
        data_root=root,
        run_id="derived-lineage",
        inputs=(upstream,),
        output=derived,
        destination_base=gold,
        relative_path="derived.csv",
        writer=lambda path: path.write_text("source_id\na\n", encoding="utf-8"),
    )

    assert manifest.inputs == (upstream,)
    assert manifest.outputs == (manifest.outputs[0],)
    assert (gold / "derived.csv").exists()
