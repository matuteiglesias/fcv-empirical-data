import json

from empirical_contracts import AuthorityLevel, DataLayer, DatasetRef, GrainSpec

from fcv_empirical.common.parity import build_parity_report, serialize_parity_report


def ref(dataset_id: str) -> DatasetRef:
    return DatasetRef(
        dataset_id=dataset_id,
        version="v1",
        schema_version="1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L1_NORMALIZED,
        grain=GrainSpec(keys=("source_id",)),
    )


def test_parity_can_record_equality() -> None:
    report = build_parity_report(
        legacy_dataset=ref("legacy"),
        new_dataset=ref("new"),
        key_overlap=10,
        legacy_rows=10,
        new_rows=10,
        total_comparisons=20,
        legacy_only=0,
        new_only=0,
        numerical_differences=0,
        status="EQUAL",
    )
    assert report["status"] == "EQUAL"
    assert json.loads(serialize_parity_report(report))["key_overlap"] == 10


def test_parity_can_record_explained_divergence_without_failure() -> None:
    report = build_parity_report(
        legacy_dataset=ref("legacy"),
        new_dataset=ref("new"),
        key_overlap=8,
        legacy_rows=10,
        new_rows=11,
        total_comparisons=16,
        legacy_only=2,
        new_only=3,
        numerical_differences=4,
        discrepancy_categories={"documented_filter_change": 4},
        status="EXPLAINED_DIVERGENCE",
        notes=("Legacy parity is evidence, not authority.",),
    )
    assert report["status"] == "EXPLAINED_DIVERGENCE"
    assert report["discrepancy_categories"] == {"documented_filter_change": 4}
