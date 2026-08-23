from __future__ import annotations

import hashlib
import json
import numbers
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping

import pandas as pd
from empirical_contracts import (
    AuthorityLevel,
    CoverageContract,
    DataLayer,
    DatasetRef,
    GrainSpec,
    MeasurementContract,
    QAResult,
    RunManifest,
)
from spatial_foundation import DataRoot, sha256_file

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact

from .catalog import SurveyCatalogEntry
from .variables import TemporalSemantics

TransformKind = Literal["binary_standard", "ordinal_standard", "categorical_passthrough"]
ComparabilityStatus = Literal[
    "standard_definition_release_validation_required",
    "survey_relative_scale",
    "country_specific_categories",
    "unknown",
]

_DHS_VII_MANUAL = "https://www.dhsprogram.com/pubs/pdf/DHSG4/Recode7_DHS_10Sep2018_DHSG4.pdf"
_DHS_VII_MAP = "https://www.dhsprogram.com/pubs/pdf/DHSG4/Recode7_Map_31Aug2018_DHSG4.pdf"


@dataclass(frozen=True)
class DhsVariableDefinition:
    """Codebook-backed semantic definition for one DHS source variable.

    This maps a source variable to a reusable empirical measurement. It deliberately contains no
    treatment/outcome/covariate role; experiment use belongs downstream.
    """

    survey_phase: str
    recode_family: str
    source_variable: str
    measurement_id: str
    source_label: str
    unit: str
    transform: TransformKind
    comparability_status: ComparabilityStatus
    temporal_semantics: TemporalSemantics = TemporalSemantics.SURVEY_TIME
    value_labels: Mapping[str, str] = field(default_factory=dict)
    missing_codes: tuple[str, ...] = ()
    allowed_codes: tuple[str, ...] | None = None
    codebook_provenance: Mapping[str, str] = field(default_factory=dict)
    notes: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "survey_phase",
            "recode_family",
            "source_variable",
            "measurement_id",
            "source_label",
            "unit",
        ):
            value = getattr(self, name)
            if not value or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.recode_family.upper() != "HR":
            raise ValueError("initial DHS semantic registry supports HR definitions only")
        if self.source_variable != self.source_variable.upper():
            raise ValueError("DHS registry source_variable must use canonical uppercase recode name")
        if len(set(self.missing_codes)) != len(self.missing_codes):
            raise ValueError("missing_codes must be unique")
        if self.allowed_codes is not None:
            if len(set(self.allowed_codes)) != len(self.allowed_codes):
                raise ValueError("allowed_codes must be unique")
            missing_not_allowed = sorted(set(self.missing_codes) - set(self.allowed_codes))
            if missing_not_allowed:
                raise ValueError(
                    "missing_codes must be represented in allowed_codes when allowed_codes is set"
                )
        if self.notes is not None and not self.notes.strip():
            raise ValueError("notes must be non-empty when supplied")


_DHS_VII_PROVENANCE = {
    "publication_id": "DHSG4",
    "manual": "DHS-VII Standard Recode Manual, August 29 2018",
    "manual_url": _DHS_VII_MANUAL,
    "recode_map": "DHS-VII Standard Recode Map, August 31 2018",
    "recode_map_url": _DHS_VII_MAP,
}

DHS_VII_STANDARD_HR_REGISTRY: tuple[DhsVariableDefinition, ...] = (
    DhsVariableDefinition(
        survey_phase="DHS-VII",
        recode_family="HR",
        source_variable="HV206",
        measurement_id="dhs.household.electricity_access",
        source_label="Household has electricity",
        unit="boolean",
        transform="binary_standard",
        comparability_status="standard_definition_release_validation_required",
        value_labels={"0": "No", "1": "Yes", "9": "Missing"},
        missing_codes=("9",),
        allowed_codes=("0", "1", "9"),
        codebook_provenance=_DHS_VII_PROVENANCE,
        notes=(
            "Standard recode definition is stable in DHS-VII; each concrete survey release should "
            "still be checked against its distributed recode documentation before L4 validation."
        ),
    ),
    DhsVariableDefinition(
        survey_phase="DHS-VII",
        recode_family="HR",
        source_variable="HV270",
        measurement_id="dhs.household.wealth_quintile",
        source_label="Wealth index combined",
        unit="ordered quintile",
        transform="ordinal_standard",
        comparability_status="survey_relative_scale",
        value_labels={
            "1": "Poorest",
            "2": "Poorer",
            "3": "Middle",
            "4": "Richer",
            "5": "Richest",
        },
        allowed_codes=("1", "2", "3", "4", "5"),
        codebook_provenance=_DHS_VII_PROVENANCE,
        notes=(
            "Quintile order is source-defined, but the wealth index is relative to the survey "
            "population and is not an absolute cross-survey wealth scale."
        ),
    ),
    DhsVariableDefinition(
        survey_phase="DHS-VII",
        recode_family="HR",
        source_variable="HV201",
        measurement_id="dhs.household.drinking_water_source_code",
        source_label="Main source of drinking water",
        unit="source category code",
        transform="categorical_passthrough",
        comparability_status="country_specific_categories",
        missing_codes=("99",),
        allowed_codes=None,
        codebook_provenance=_DHS_VII_PROVENANCE,
        notes=(
            "DHS documents individual water-source codes as country-specific even when major "
            "categories are standardized. This measurement therefore preserves the source code "
            "and does not infer improved/unimproved or safe/unsafe water."
        ),
    ),
)


@dataclass(frozen=True)
class DhsHouseholdMeasurementResult:
    frame: pd.DataFrame
    definitions: tuple[DhsVariableDefinition, ...]
    contracts: tuple[MeasurementContract, ...]
    qa: tuple[QAResult, ...]
    registry_sha256: str


def _registry_payload(definitions: tuple[DhsVariableDefinition, ...]) -> list[dict[str, object]]:
    return [
        {
            "survey_phase": item.survey_phase,
            "recode_family": item.recode_family,
            "source_variable": item.source_variable,
            "measurement_id": item.measurement_id,
            "source_label": item.source_label,
            "unit": item.unit,
            "transform": item.transform,
            "comparability_status": item.comparability_status,
            "temporal_semantics": item.temporal_semantics.value,
            "value_labels": dict(sorted(item.value_labels.items())),
            "missing_codes": list(item.missing_codes),
            "allowed_codes": list(item.allowed_codes) if item.allowed_codes is not None else None,
            "codebook_provenance": dict(sorted(item.codebook_provenance.items())),
            "notes": item.notes,
        }
        for item in definitions
    ]


def registry_sha256(definitions: tuple[DhsVariableDefinition, ...]) -> str:
    payload = json.dumps(
        _registry_payload(definitions), sort_keys=True, separators=(",", ":"), ensure_ascii=False
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


def _resolve_source_column(frame: pd.DataFrame, source_variable: str) -> str:
    matches = [
        column for column in frame.columns if str(column).casefold() == source_variable.casefold()
    ]
    if len(matches) != 1:
        if not matches:
            raise ValueError(
                f"DHS HR Silver is missing registry source variable {source_variable!r}"
            )
        raise ValueError(
            f"DHS HR Silver has ambiguous case variants for registry variable {source_variable!r}"
        )
    return str(matches[0])


def _normalize_measurement(
    token: str | None,
    definition: DhsVariableDefinition,
) -> tuple[str | None, float | None, str | None, str]:
    if token is None:
        return None, None, None, "missing_source_value"
    if token in definition.missing_codes:
        return None, None, definition.value_labels.get(token), "source_missing_code"
    if definition.allowed_codes is not None and token not in definition.allowed_codes:
        return None, None, None, "unmapped_source_code"

    label = definition.value_labels.get(token)
    if definition.transform == "binary_standard":
        if token == "0":
            return "false", 0.0, label, "observed"
        if token == "1":
            return "true", 1.0, label, "observed"
        return None, None, label, "unmapped_source_code"
    if definition.transform == "ordinal_standard":
        return token, float(int(token)), label, "observed"
    return token, None, label, "observed"


def _validate_registry(
    survey: SurveyCatalogEntry,
    definitions: tuple[DhsVariableDefinition, ...],
) -> None:
    if survey.source_family.casefold() != "dhs":
        raise ValueError("DHS variable registry requires a DHS SurveyCatalogEntry")
    if not definitions:
        raise ValueError("at least one DHS variable definition is required")
    measurement_ids = [item.measurement_id for item in definitions]
    source_variables = [item.source_variable for item in definitions]
    if len(set(measurement_ids)) != len(measurement_ids):
        raise ValueError("DHS registry measurement_id values must be unique")
    if len(set(source_variables)) != len(source_variables):
        raise ValueError("DHS registry source_variable values must be unique")
    missing_provenance = [
        item.source_variable for item in definitions if not item.codebook_provenance
    ]
    if missing_provenance:
        raise ValueError(
            "DHS variable definitions require codebook provenance: "
            + ", ".join(missing_provenance)
        )
    mismatched = [
        item.source_variable
        for item in definitions
        if survey.survey_phase is None or item.survey_phase.casefold() != survey.survey_phase.casefold()
    ]
    if mismatched:
        raise ValueError(
            "DHS registry definitions are not validated for survey phase "
            f"{survey.survey_phase!r}: {', '.join(mismatched)}"
        )


def _coverage(survey: SurveyCatalogEntry, definition: DhsVariableDefinition) -> CoverageContract:
    return CoverageContract(
        geography_scope=(
            f"households observed in DHS survey {survey.survey_id}; no polygon-wide coverage claim"
        ),
        temporal_start=survey.fieldwork_start,
        temporal_end=survey.fieldwork_end,
        observation_semantics=(
            f"household-grain semantic measurement derived from {definition.source_variable} "
            "using an explicit codebook-backed registry definition"
        ),
        absent_row_semantics="not_observed",
        authority=AuthorityLevel.L3_REBUILT,
        basis=(
            f"{definition.codebook_provenance.get('manual', 'DHS codebook')} and release-specific "
            "survey documentation required before research validation"
        ),
    )


def build_dhs_household_measurements(
    hr: pd.DataFrame,
    *,
    survey: SurveyCatalogEntry,
    hr_dataset: DatasetRef,
    definitions: tuple[DhsVariableDefinition, ...] = DHS_VII_STANDARD_HR_REGISTRY,
) -> DhsHouseholdMeasurementResult:
    """Build codebook-backed household measurements without assigning experiment roles."""
    _validate_registry(survey, definitions)
    if hr_dataset.dataset_id != "surveys.dhs.hr_households":
        raise ValueError("hr_dataset must identify contract-backed DHS HR household Silver")
    required = {"survey_id", "source_row_id", "household_id"}
    missing = sorted(required - set(hr.columns))
    if missing:
        raise ValueError("DHS HR Silver is missing identity columns: " + ", ".join(missing))
    if hr["source_row_id"].isna().any() or hr["source_row_id"].duplicated().any():
        raise ValueError("source_row_id must uniquely identify every HR Silver source row")
    survey_ids = set(hr["survey_id"].astype("string").dropna())
    if survey_ids != {survey.survey_id}:
        raise ValueError("HR Silver survey_id does not match the requested SurveyCatalogEntry")
    hr = hr.reset_index(drop=True)

    pieces: list[pd.DataFrame] = []
    status_counts: dict[str, int] = {}
    for definition in definitions:
        source_column = _resolve_source_column(hr, definition.source_variable)
        rows = hr[["source_row_id", "survey_id", "household_id"]].copy()
        rows["measurement_id"] = definition.measurement_id
        rows["source_variable"] = definition.source_variable
        rows["source_value"] = hr[source_column].map(_source_token).astype("string")

        normalized = [
            _normalize_measurement(_source_token(value), definition) for value in hr[source_column]
        ]
        rows["normalized_value"] = pd.Series(
            [item[0] for item in normalized], dtype="string"
        )
        rows["normalized_numeric_value"] = pd.Series(
            [item[1] for item in normalized], dtype="Float64"
        )
        rows["value_label"] = pd.Series([item[2] for item in normalized], dtype="string")
        rows["measurement_status"] = pd.Series(
            [item[3] for item in normalized], dtype="string"
        )
        rows["temporal_semantics"] = definition.temporal_semantics.value
        rows["comparability_status"] = definition.comparability_status
        rows["unit"] = definition.unit
        rows["codebook_publication_id"] = definition.codebook_provenance.get("publication_id")
        for status, count in rows["measurement_status"].value_counts().items():
            status_counts[str(status)] = status_counts.get(str(status), 0) + int(count)
        pieces.append(rows)

    frame = pd.concat(pieces, ignore_index=True)
    expected_rows = len(hr) * len(definitions)
    unresolved = status_counts.get("unmapped_source_code", 0)
    contracts = tuple(
        MeasurementContract(
            measure_id=definition.measurement_id,
            description=definition.source_label,
            source_dataset=hr_dataset,
            output_grain=GrainSpec(keys=("source_row_id",)),
            unit=definition.unit,
            aggregation=None,
            coverage=_coverage(survey, definition),
            parameters={
                "source_variable": definition.source_variable,
                "recode_family": definition.recode_family,
                "survey_phase": definition.survey_phase,
                "transform": definition.transform,
                "temporal_semantics": definition.temporal_semantics.value,
                "comparability_status": definition.comparability_status,
                "codebook_provenance": dict(definition.codebook_provenance),
            },
        )
        for definition in definitions
    )
    qa = (
        QAResult(
            check_id="dhs.variables.row_accounting",
            state="GREEN" if len(frame) == expected_rows else "RED",
            message="each selected semantic measurement retains one row per HR Silver source row",
            metrics={
                "hr_source_rows": len(hr),
                "registry_measurements": len(definitions),
                "expected_measurement_rows": expected_rows,
                "measurement_rows": len(frame),
            },
        ),
        QAResult(
            check_id="dhs.variables.source_code_mapping",
            state="GREEN" if unresolved == 0 else "YELLOW",
            message=(
                "unsupported source codes remain unresolved; they are never coerced to zero or "
                "another documented category"
            ),
            metrics={
                "observed_rows": status_counts.get("observed", 0),
                "missing_source_value_rows": status_counts.get("missing_source_value", 0),
                "source_missing_code_rows": status_counts.get("source_missing_code", 0),
                "unmapped_source_code_rows": unresolved,
            },
        ),
        QAResult(
            check_id="dhs.variables.experiment_firewall",
            state="GREEN",
            message="registry definitions create reusable empirical meanings only",
            metrics={"scientific_roles_assigned": 0},
        ),
    )
    return DhsHouseholdMeasurementResult(
        frame=frame,
        definitions=definitions,
        contracts=contracts,
        qa=qa,
        registry_sha256=registry_sha256(definitions),
    )


def _measurement_dataset_ref(
    hr_dataset: DatasetRef,
    registry_hash: str,
) -> DatasetRef:
    return DatasetRef(
        dataset_id="surveys.dhs.hr_household_measurements",
        version=f"{hr_dataset.version}--registry-{registry_hash[:12]}",
        schema_version="dhs-household-semantic-measurements-v1",
        layer=DataLayer.GOLD,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("source_row_id", "measurement_id")),
    )


def _hashed_output(manifest: RunManifest, dataset_id: str) -> DatasetRef:
    matches = [item for item in manifest.outputs if item.dataset_id == dataset_id]
    if len(matches) != 1:
        raise RuntimeError(f"expected one materialized dataset {dataset_id!r}")
    return matches[0]


def _json_text(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def materialize_dhs_household_measurements(
    *,
    hr_path: str | Path,
    hr_dataset: DatasetRef,
    survey: SurveyCatalogEntry,
    data_root: DataRoot,
    run_id: str,
    definitions: tuple[DhsVariableDefinition, ...] = DHS_VII_STANDARD_HR_REGISTRY,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> tuple[DhsHouseholdMeasurementResult, RunManifest, DatasetRef, Path]:
    """Materialize reusable DHS household measurements from hashed HR Silver."""
    path = Path(hr_path).expanduser().resolve()
    if hr_dataset.content_sha256 is None:
        raise ValueError("hr_dataset must carry content_sha256 before semantic materialization")
    if sha256_file(path) != hr_dataset.content_sha256:
        raise ValueError("HR Silver bytes do not match the supplied DatasetRef content hash")

    result = build_dhs_household_measurements(
        pd.read_parquet(path),
        survey=survey,
        hr_dataset=hr_dataset,
        definitions=definitions,
    )
    dataset = _measurement_dataset_ref(hr_dataset, result.registry_sha256)
    destination = data_root.gold(
        "surveys", f"dhs/{survey.survey_id}/household_measurements", dataset.version
    )
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        inputs=(hr_dataset,),
        outputs=(
            FileMaterialization(
                dataset=dataset,
                relative_path="household_measurements.parquet",
                destination_base=destination,
                writer=lambda output: result.frame.to_parquet(output, index=False),
            ),
        ),
        parameters={
            "survey_id": survey.survey_id,
            "registry_sha256": result.registry_sha256,
            "measurement_ids": [item.measurement_id for item in result.definitions],
            "aggregation": None,
            "imputation": None,
        },
        code_commit=code_commit,
        qa=result.qa,
        overwrite=overwrite,
    )
    hashed = _hashed_output(manifest, dataset.dataset_id)
    persist_run_artifact(
        data_root,
        run_id,
        "registry/dhs_hr_variable_registry.json",
        _json_text(
            {
                "registry_sha256": result.registry_sha256,
                "definitions": _registry_payload(result.definitions),
            }
        ),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "contracts/dhs_household_measurements.json",
        _json_text([contract.model_dump(mode="json") for contract in result.contracts]),
        overwrite=overwrite,
    )
    return result, manifest, hashed, destination / "household_measurements.parquet"


__all__ = [
    "DHS_VII_STANDARD_HR_REGISTRY",
    "DhsHouseholdMeasurementResult",
    "DhsVariableDefinition",
    "build_dhs_household_measurements",
    "materialize_dhs_household_measurements",
    "registry_sha256",
]
