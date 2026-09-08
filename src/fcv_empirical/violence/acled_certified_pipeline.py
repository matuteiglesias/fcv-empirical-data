from __future__ import annotations

from pathlib import Path

import pandas as pd
from empirical_contracts import (
    AuthorityLevel,
    DataLayer,
    DatasetRef,
    GeographySpec,
    GrainSpec,
    MeasurementContract,
    PeriodScheme,
    RunManifest,
)
from spatial_foundation import DataRoot

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact

from .acled_certified import (
    AcledCertifiedGoldResult,
    AcledCoverageCertification,
    build_acled_certified_coverage,
    build_acled_certified_gold,
    build_acled_certified_measurement_contract,
)


def _json_text(payload: object) -> str:
    import json

    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _certified_gold_ref(
    sparse_gold_dataset: DatasetRef,
    geography: GeographySpec,
    period_scheme: PeriodScheme,
    certification: AcledCoverageCertification,
) -> DatasetRef:
    return DatasetRef(
        dataset_id="violence.acled.area_period_native_event_certified",
        version=f"{sparse_gold_dataset.version}--cert-{certification.sha256[:12]}",
        schema_version="acled-native-event-certified-gold-v1",
        layer=DataLayer.GOLD,
        authority=AuthorityLevel.L2_DERIVED,
        grain=GrainSpec(keys=("geo_uid", "period_id", "native_event_type")),
        geography=geography,
        period_scheme=period_scheme,
    )


def _hashed_output(manifest: RunManifest, dataset_id: str) -> DatasetRef:
    matches = [dataset for dataset in manifest.outputs if dataset.dataset_id == dataset_id]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one materialized dataset {dataset_id!r}")
    return matches[0]


def _require_compatible_input(
    dataset: DatasetRef,
    *,
    geography: GeographySpec,
    period_scheme: PeriodScheme,
) -> None:
    if dataset.geography is None or dataset.geography.id != geography.id:
        raise ValueError("ACLED sparse Gold geography does not match certified target geography")
    if dataset.period_scheme is None or dataset.period_scheme.id != period_scheme.id:
        raise ValueError("ACLED sparse Gold period scheme does not match certified period scheme")


def materialize_acled_certified_measurement(
    *,
    sparse_gold: pd.DataFrame,
    sparse_gold_dataset: DatasetRef,
    geography_units: pd.DataFrame,
    geography_dataset: DatasetRef,
    geography: GeographySpec,
    period_scheme: PeriodScheme,
    certification: AcledCoverageCertification,
    data_root: DataRoot,
    run_id: str,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> tuple[
    AcledCertifiedGoldResult,
    RunManifest,
    MeasurementContract,
    DatasetRef,
    Path,
]:
    """Materialize a coverage-certified dense derivative of sparse ACLED Gold.

    The sparse source measurement remains unchanged. This function can license
    structural zeros only because the caller supplies an explicit coverage
    certification; it never infers completeness from the data itself.
    """
    if sparse_gold_dataset.dataset_id != "violence.acled.area_period_native_event":
        raise ValueError("coverage certification requires the canonical sparse ACLED Gold dataset")
    _require_compatible_input(
        sparse_gold_dataset,
        geography=geography,
        period_scheme=period_scheme,
    )
    if geography_dataset.geography is None or geography_dataset.geography.id != geography.id:
        raise ValueError("geography DatasetRef does not match certified target geography")

    result = build_acled_certified_gold(
        sparse_gold,
        geography_units,
        period_scheme=period_scheme,
        certification=certification,
    )
    dataset = _certified_gold_ref(
        sparse_gold_dataset,
        geography,
        period_scheme,
        certification,
    )
    destination = data_root.gold("violence", "acled_area_period_certified", dataset.version)
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        inputs=(sparse_gold_dataset, geography_dataset),
        outputs=(
            FileMaterialization(
                dataset=dataset,
                relative_path="certified_area_period.parquet",
                destination_base=destination,
                writer=lambda path: result.frame.to_parquet(path, index=False),
            ),
        ),
        parameters={
            "source_sparse_dataset_id": sparse_gold_dataset.dataset_id,
            "source_sparse_dataset_version": sparse_gold_dataset.version,
            "geography": geography.model_dump(mode="json"),
            "period_scheme": period_scheme.model_dump(mode="json"),
            "coverage_certification": certification.to_dict(),
            "coverage_certification_sha256": certification.sha256,
            "completeness_inferred_from_rows": False,
            "structural_zeros_materialized": True,
        },
        code_commit=code_commit,
        qa=result.qa,
        overwrite=overwrite,
    )
    hashed = _hashed_output(manifest, dataset.dataset_id)
    coverage = build_acled_certified_coverage(
        result,
        certification=certification,
        geography=geography,
    )
    measurement = build_acled_certified_measurement_contract(
        sparse_gold_dataset=sparse_gold_dataset,
        geography=geography,
        period_scheme=period_scheme,
        coverage=coverage,
        certification=certification,
        result=result,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "contracts/coverage_certification.json",
        _json_text(certification.to_dict()),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "contracts/coverage.json",
        _json_text(coverage.model_dump(mode="json")),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "contracts/measurement_contract.json",
        _json_text(measurement.model_dump(mode="json")),
        overwrite=overwrite,
    )
    return result, manifest, measurement, hashed, destination / "certified_area_period.parquet"


__all__ = ["materialize_acled_certified_measurement"]
