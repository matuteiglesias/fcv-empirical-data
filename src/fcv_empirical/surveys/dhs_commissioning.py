from __future__ import annotations

import hashlib
import json
import math
import numbers
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from empirical_contracts import (
    AuthorityLevel,
    DataLayer,
    DatasetRef,
    GrainSpec,
    QAResult,
    RunManifest,
)
from spatial_foundation import DataRoot, sha256_file

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact

from .catalog import SurveyCatalogEntry

_DHS_HR_DATASET_ID = "surveys.dhs.hr_households"
_DHS_MEASUREMENT_DATASET_ID = "surveys.dhs.hr_household_measurements"
_DHS_COMMISSIONING_DATASET_ID = "surveys.dhs.commissioning_results"
_DEFAULT_WEIGHT_VARIABLE = "HV005"


@dataclass(frozen=True)
class DhsCommissioningSpec:
    """External-reference commissioning declaration for one DHS survey statistic."""

    benchmark_id: str
    survey_id: str
    measurement_id: str
    reference_authority: str
    reference_publication: str
    reference_publication_id: str
    reference_url: str
    reference_table: str
    reference_page: int
    expected_percentages: Mapping[str, float]
    category_map: Mapping[str, str] = field(default_factory=dict)
    published_decimals: int = 1
    expected_source_weight_variable: str = _DEFAULT_WEIGHT_VARIABLE
    population_multiplier_variable: str | None = None
    domain_variable: str | None = None
    domain_allowed_values: tuple[str, ...] = ()
    require_release_category_map: bool = False
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "benchmark_id",
            "survey_id",
            "measurement_id",
            "reference_authority",
            "reference_publication",
            "reference_publication_id",
            "reference_url",
            "reference_table",
            "expected_source_weight_variable",
        ):
            value = getattr(self, name)
            if not value or not str(value).strip():
                raise ValueError(f"{name} must be non-empty")
        if self.reference_page <= 0:
            raise ValueError("reference_page must be positive")
        if self.published_decimals < 0:
            raise ValueError("published_decimals must be non-negative")
        if not self.expected_percentages:
            raise ValueError("expected_percentages must be non-empty")
        if len(set(self.expected_percentages)) != len(self.expected_percentages):
            raise ValueError("expected_percentages keys must be unique")
        for cell_id, value in self.expected_percentages.items():
            if not cell_id or not str(cell_id).strip():
                raise ValueError("expected_percentages cell ids must be non-empty")
            numeric = float(value)
            if not math.isfinite(numeric) or numeric < 0 or numeric > 100:
                raise ValueError("expected percentages must be finite values in [0, 100]")
        if self.domain_variable is None and self.domain_allowed_values:
            raise ValueError("domain_allowed_values require domain_variable")
        if self.domain_variable is not None and not self.domain_allowed_values:
            raise ValueError("domain_variable requires at least one allowed value")

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "survey_id": self.survey_id,
            "measurement_id": self.measurement_id,
            "reference": {
                "authority": self.reference_authority,
                "publication": self.reference_publication,
                "publication_id": self.reference_publication_id,
                "url": self.reference_url,
                "table": self.reference_table,
                "page": self.reference_page,
            },
            "expected_percentages": dict(self.expected_percentages),
            "category_map": dict(self.category_map),
            "published_decimals": self.published_decimals,
            "expected_source_weight_variable": self.expected_source_weight_variable,
            "population_multiplier_variable": self.population_multiplier_variable,
            "domain_variable": self.domain_variable,
            "domain_allowed_values": list(self.domain_allowed_values),
            "require_release_category_map": self.require_release_category_map,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class DhsCommissioningResult:
    frame: pd.DataFrame
    diagnostics: dict[str, Any]
    qa: tuple[QAResult, ...]
    spec_sha256: str


def _json_text(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _spec_sha256(specs: tuple[DhsCommissioningSpec, ...]) -> str:
    payload = json.dumps(
        [spec.to_dict() for spec in specs],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_token(value: object) -> str | None:
    if pd.isna(value):
        return None
    if isinstance(value, numbers.Integral) and not isinstance(value, bool):
        return str(int(value))
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        numeric = float(value)
        if numeric.is_integer():
            return str(int(numeric))
    text = str(value).strip()
    return text or None


def _resolve_source_column(frame: pd.DataFrame, requested: str) -> str:
    matches = [column for column in frame.columns if str(column).casefold() == requested.casefold()]
    if not matches:
        raise ValueError(f"DHS HR Silver is missing required source variable {requested!r}")
    if len(matches) != 1:
        raise ValueError(f"DHS HR Silver has ambiguous case variants for {requested!r}")
    return str(matches[0])


def _validate_dataset(
    dataset: DatasetRef,
    *,
    expected_id: str,
    required_grain: tuple[str, ...],
) -> None:
    if dataset.dataset_id != expected_id:
        raise ValueError(f"expected DatasetRef {expected_id!r}, got {dataset.dataset_id!r}")
    if dataset.content_sha256 is None:
        raise ValueError(f"{expected_id} DatasetRef must carry content_sha256")
    if tuple(dataset.grain.keys) != required_grain:
        raise ValueError(
            f"{expected_id} must declare grain {required_grain!r}; got {dataset.grain.keys!r}"
        )


def _validate_frame_identity(
    hr: pd.DataFrame,
    measurements: pd.DataFrame,
    *,
    survey: SurveyCatalogEntry,
) -> None:
    hr_required = {
        "survey_id",
        "source_row_id",
        "source_weight_variable",
        "source_household_weight",
    }
    measurement_required = {
        "survey_id",
        "source_row_id",
        "measurement_id",
        "measurement_status",
        "normalized_value",
    }
    missing_hr = sorted(hr_required - set(hr.columns))
    missing_measurements = sorted(measurement_required - set(measurements.columns))
    if missing_hr:
        raise ValueError("DHS HR Silver is missing commissioning columns: " + ", ".join(missing_hr))
    if missing_measurements:
        raise ValueError(
            "DHS measurement product is missing commissioning columns: "
            + ", ".join(missing_measurements)
        )
    if hr["source_row_id"].isna().any() or hr["source_row_id"].duplicated().any():
        raise ValueError("HR source_row_id must be non-null and unique")
    if measurements[["source_row_id", "measurement_id"]].isna().any().any():
        raise ValueError("measurement source_row_id and measurement_id must be non-null")
    if measurements[["source_row_id", "measurement_id"]].duplicated().any():
        raise ValueError("measurement source_row_id x measurement_id must be unique")
    hr_surveys = set(hr["survey_id"].astype("string").dropna())
    measurement_surveys = set(measurements["survey_id"].astype("string").dropna())
    if hr_surveys != {survey.survey_id}:
        raise ValueError("HR Silver survey_id does not match SurveyCatalogEntry")
    if measurement_surveys != {survey.survey_id}:
        raise ValueError("measurement survey_id does not match SurveyCatalogEntry")


def _finite_numeric(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    numeric = pd.to_numeric(series, errors="coerce")
    finite = numeric.map(lambda value: bool(pd.notna(value) and math.isfinite(float(value))))
    return numeric, finite


def _numeric_positive(series: pd.Series, *, name: str) -> pd.Series:
    numeric, finite = _finite_numeric(series)
    invalid = ~finite | numeric.le(0).fillna(True)
    if invalid.any():
        raise ValueError(f"{name} must be finite and strictly positive for all commissioned rows")
    return numeric.astype(float)


def _numeric_nonnegative(series: pd.Series, *, name: str) -> pd.Series:
    numeric, finite = _finite_numeric(series)
    invalid = ~finite | numeric.lt(0).fillna(True)
    if invalid.any():
        raise ValueError(f"{name} must be finite and non-negative for all commissioned rows")
    return numeric.astype(float)


def _commission_one(
    hr: pd.DataFrame,
    measurements: pd.DataFrame,
    *,
    spec: DhsCommissioningSpec,
) -> tuple[pd.DataFrame, dict[str, Any], tuple[QAResult, ...]]:
    if spec.require_release_category_map and not spec.category_map:
        raise ValueError(
            f"{spec.benchmark_id} requires an explicit release-local category_map before execution"
        )

    selected = measurements.loc[measurements["measurement_id"] == spec.measurement_id].copy()
    if len(selected) != len(hr):
        raise ValueError(
            f"{spec.benchmark_id}: selected measurement rows do not match HR row count "
            f"({len(selected)} != {len(hr)})"
        )
    if set(selected["source_row_id"].astype(str)) != set(hr["source_row_id"].astype(str)):
        raise ValueError(
            f"{spec.benchmark_id}: measurement and HR source_row_id support do not match exactly"
        )

    joined = selected.merge(
        hr,
        on=["source_row_id", "survey_id"],
        how="inner",
        validate="one_to_one",
        suffixes=("_measurement", "_hr"),
    )
    if len(joined) != len(hr):
        raise ValueError(f"{spec.benchmark_id}: commissioning join lost HR rows")

    source_weight_tokens = {
        str(value).casefold()
        for value in joined["source_weight_variable"].dropna().astype("string").tolist()
    }
    if source_weight_tokens != {spec.expected_source_weight_variable.casefold()}:
        raise ValueError(
            f"{spec.benchmark_id}: source weight variable is not uniformly "
            f"{spec.expected_source_weight_variable}"
        )
    base_weight = _numeric_positive(
        joined["source_household_weight"],
        name=f"{spec.benchmark_id} source household weight",
    )

    if spec.population_multiplier_variable is None:
        multiplier = pd.Series(1.0, index=joined.index)
    else:
        multiplier_column = _resolve_source_column(joined, spec.population_multiplier_variable)
        multiplier = _numeric_nonnegative(
            joined[multiplier_column],
            name=f"{spec.benchmark_id} {spec.population_multiplier_variable}",
        )
    effective_weight = base_weight * multiplier

    if spec.domain_variable is None:
        domain_mask = pd.Series(True, index=joined.index)
    else:
        domain_column = _resolve_source_column(joined, spec.domain_variable)
        allowed = set(spec.domain_allowed_values)
        domain_mask = joined[domain_column].map(_source_token).isin(allowed)

    domain = joined.loc[domain_mask].copy()
    domain_weight = effective_weight.loc[domain_mask].copy()
    denominator = float(domain_weight.sum())
    if denominator <= 0:
        raise ValueError(f"{spec.benchmark_id}: commissioned denominator is not positive")

    status = domain["measurement_status"].astype("string")
    observed_mask = status.eq("observed")
    unmapped_mask = status.eq("unmapped_source_code")
    missing_mask = ~observed_mask & ~unmapped_mask

    observed_tokens = domain["normalized_value"].map(_source_token)
    if spec.category_map:
        reference_cells = observed_tokens.map(
            lambda token: spec.category_map.get(token) if token is not None else None
        )
    else:
        reference_cells = observed_tokens

    unmapped_category_mask = observed_mask & reference_cells.isna()
    unmapped_weight = float(domain_weight.loc[unmapped_mask | unmapped_category_mask].sum())
    missing_weight = float(domain_weight.loc[missing_mask].sum())
    observed_weight = float(domain_weight.loc[observed_mask].sum())

    rows: list[dict[str, Any]] = []
    tolerance = 0.5 * (10 ** (-spec.published_decimals))
    all_cells_compatible = True
    for cell_id, expected in spec.expected_percentages.items():
        category_weight = float(domain_weight.loc[reference_cells.eq(cell_id)].sum())
        observed_percent = 100.0 * category_weight / denominator
        difference_pp = observed_percent - float(expected)
        compatible = abs(difference_pp) <= tolerance + 1e-12
        all_cells_compatible = all_cells_compatible and compatible
        rows.append(
            {
                "benchmark_id": spec.benchmark_id,
                "survey_id": spec.survey_id,
                "measurement_id": spec.measurement_id,
                "cell_id": cell_id,
                "reference_percent": float(expected),
                "observed_percent": observed_percent,
                "difference_pp": difference_pp,
                "published_decimals": spec.published_decimals,
                "rounding_tolerance_pp": tolerance,
                "rounding_compatible": compatible,
                "category_weight": category_weight,
                "weighted_denominator": denominator,
            }
        )

    quantitative_pass = all_cells_compatible and unmapped_weight == 0.0
    diagnostics = {
        "benchmark_id": spec.benchmark_id,
        "survey_id": spec.survey_id,
        "measurement_id": spec.measurement_id,
        "hr_rows": len(hr),
        "joined_rows": len(joined),
        "domain_rows": int(domain_mask.sum()),
        "domain_excluded_rows": int((~domain_mask).sum()),
        "weighted_denominator": denominator,
        "observed_weight": observed_weight,
        "missing_measurement_weight": missing_weight,
        "unmapped_measurement_or_category_weight": unmapped_weight,
        "population_multiplier_variable": spec.population_multiplier_variable,
        "domain_variable": spec.domain_variable,
        "domain_allowed_values": list(spec.domain_allowed_values),
        "source_weight_variable": spec.expected_source_weight_variable,
        "quantitative_recovery": "PASS" if quantitative_pass else "FAIL",
    }
    qa = (
        QAResult(
            check_id=f"dhs.commissioning.{spec.benchmark_id}.row_linkage",
            state="GREEN",
            message="semantic measurement rows join one-to-one to the commissioned HR rows",
            metrics={
                "hr_rows": len(hr),
                "measurement_rows": len(selected),
                "joined_rows": len(joined),
            },
        ),
        QAResult(
            check_id=f"dhs.commissioning.{spec.benchmark_id}.measurement_accounting",
            state="GREEN" if unmapped_weight == 0.0 else "RED",
            message=(
                "missing measurement states remain in the denominator; unmapped positive weight "
                "blocks quantitative recovery"
            ),
            metrics={
                "weighted_denominator": denominator,
                "missing_measurement_weight": missing_weight,
                "unmapped_measurement_or_category_weight": unmapped_weight,
            },
        ),
        QAResult(
            check_id=f"dhs.commissioning.{spec.benchmark_id}.quantitative_recovery",
            state="GREEN" if quantitative_pass else "RED",
            message=(
                "all published cells are recovered within their reported rounding precision"
                if quantitative_pass
                else "one or more published cells are not recovered at reported rounding precision"
            ),
            metrics={
                "reference_cell_count": len(spec.expected_percentages),
                "rounding_compatible_cells": sum(
                    1 for row in rows if bool(row["rounding_compatible"])
                ),
                "unmapped_measurement_or_category_weight": unmapped_weight,
            },
        ),
    )
    return pd.DataFrame(rows), diagnostics, qa


def run_dhs_commissioning_suite(
    hr: pd.DataFrame,
    measurements: pd.DataFrame,
    *,
    survey: SurveyCatalogEntry,
    hr_dataset: DatasetRef,
    measurement_dataset: DatasetRef,
    specs: tuple[DhsCommissioningSpec, ...],
) -> DhsCommissioningResult:
    """Run aggregate external-reference checks without persisting joined DHS microdata."""

    if not specs:
        raise ValueError("at least one DHS commissioning spec is required")
    if len({spec.benchmark_id for spec in specs}) != len(specs):
        raise ValueError("DHS commissioning benchmark_id values must be unique")
    mismatched = [spec.benchmark_id for spec in specs if spec.survey_id != survey.survey_id]
    if mismatched:
        raise ValueError(
            "DHS commissioning specs do not match SurveyCatalogEntry: " + ", ".join(mismatched)
        )

    _validate_dataset(
        hr_dataset,
        expected_id=_DHS_HR_DATASET_ID,
        required_grain=("source_row_id",),
    )
    _validate_dataset(
        measurement_dataset,
        expected_id=_DHS_MEASUREMENT_DATASET_ID,
        required_grain=("source_row_id", "measurement_id"),
    )
    _validate_frame_identity(hr, measurements, survey=survey)

    frames: list[pd.DataFrame] = []
    diagnostics: dict[str, Any] = {}
    qa: list[QAResult] = []
    for spec in specs:
        frame, benchmark_diagnostics, benchmark_qa = _commission_one(
            hr,
            measurements,
            spec=spec,
        )
        frames.append(frame)
        diagnostics[spec.benchmark_id] = benchmark_diagnostics
        qa.extend(benchmark_qa)

    return DhsCommissioningResult(
        frame=pd.concat(frames, ignore_index=True),
        diagnostics=diagnostics,
        qa=tuple(qa),
        spec_sha256=_spec_sha256(specs),
    )


def _commissioning_dataset_ref(
    survey: SurveyCatalogEntry,
    measurement_dataset: DatasetRef,
    spec_hash: str,
) -> DatasetRef:
    return DatasetRef(
        dataset_id=_DHS_COMMISSIONING_DATASET_ID,
        version=(
            f"{survey.survey_id}--{measurement_dataset.version}--commissioning-{spec_hash[:12]}"
        ),
        schema_version="dhs-external-reference-commissioning-v1",
        layer=DataLayer.GOLD,
        authority=AuthorityLevel.L2_DERIVED,
        grain=GrainSpec(keys=("benchmark_id", "cell_id")),
    )


def _hashed_output(manifest: RunManifest) -> DatasetRef:
    matches = [
        item for item in manifest.outputs if item.dataset_id == _DHS_COMMISSIONING_DATASET_ID
    ]
    if len(matches) != 1:
        raise RuntimeError("expected exactly one DHS commissioning output DatasetRef")
    return matches[0]


def materialize_dhs_commissioning_suite(
    *,
    hr_path: str | Path,
    hr_dataset: DatasetRef,
    measurement_path: str | Path,
    measurement_dataset: DatasetRef,
    survey: SurveyCatalogEntry,
    specs: tuple[DhsCommissioningSpec, ...],
    data_root: DataRoot,
    run_id: str,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> tuple[DhsCommissioningResult, RunManifest, DatasetRef, Path]:
    """Materialize only aggregate commissioning evidence from verified DHS products."""

    hr_file = Path(hr_path).expanduser().resolve()
    measurement_file = Path(measurement_path).expanduser().resolve()
    _validate_dataset(
        hr_dataset,
        expected_id=_DHS_HR_DATASET_ID,
        required_grain=("source_row_id",),
    )
    _validate_dataset(
        measurement_dataset,
        expected_id=_DHS_MEASUREMENT_DATASET_ID,
        required_grain=("source_row_id", "measurement_id"),
    )
    if sha256_file(hr_file) != hr_dataset.content_sha256:
        raise ValueError("HR Silver bytes do not match the supplied DatasetRef content hash")
    if sha256_file(measurement_file) != measurement_dataset.content_sha256:
        raise ValueError("DHS measurement bytes do not match the supplied DatasetRef content hash")

    result = run_dhs_commissioning_suite(
        pd.read_parquet(hr_file),
        pd.read_parquet(measurement_file),
        survey=survey,
        hr_dataset=hr_dataset,
        measurement_dataset=measurement_dataset,
        specs=specs,
    )
    dataset = _commissioning_dataset_ref(survey, measurement_dataset, result.spec_sha256)
    destination = data_root.gold(
        "surveys",
        f"dhs/{survey.survey_id}/commissioning",
        dataset.version,
    )
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        inputs=(hr_dataset, measurement_dataset),
        outputs=(
            FileMaterialization(
                dataset=dataset,
                relative_path="commissioning_results.parquet",
                destination_base=destination,
                writer=lambda output: result.frame.to_parquet(output, index=False),
            ),
        ),
        parameters={
            "survey_id": survey.survey_id,
            "purpose": "external_reference_commissioning",
            "benchmark_ids": [spec.benchmark_id for spec in specs],
            "commissioning_spec_sha256": result.spec_sha256,
            "microdata_output": None,
            "joined_microdata_persisted": False,
        },
        code_commit=code_commit,
        qa=result.qa,
        overwrite=overwrite,
    )
    hashed = _hashed_output(manifest)
    persist_run_artifact(
        data_root,
        run_id,
        "commissioning/specs.json",
        _json_text(
            {
                "spec_sha256": result.spec_sha256,
                "specs": [spec.to_dict() for spec in specs],
            }
        ),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "commissioning/diagnostics.json",
        _json_text(result.diagnostics),
        overwrite=overwrite,
    )
    return result, manifest, hashed, destination / "commissioning_results.parquet"


__all__ = [
    "DhsCommissioningResult",
    "DhsCommissioningSpec",
    "materialize_dhs_commissioning_suite",
    "run_dhs_commissioning_suite",
]
