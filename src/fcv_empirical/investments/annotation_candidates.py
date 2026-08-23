from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from empirical_contracts import AuthorityLevel, DataLayer, DatasetRef, GrainSpec, QAResult
from spatial_foundation import DataRoot, sha256_file

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact
from fcv_empirical.investments.common import (
    InvestmentMaterializationResult,
    blocked_parity,
    json_text,
    persist_contract_artifacts,
)
from fcv_empirical.investments.parity import compare_unique_key_tables, legacy_dataset_ref

ANNOTATION_SCHEMA_VERSION = "investment-annotation-candidates-v1"
AIDDATA_ANNOTATION_SOURCE_FAMILY = "china"
AIDDATA_ANNOTATION_SOURCE_ID = "aiddata_china_clg_lmic_v1_0"
WORLD_BANK_ANNOTATION_SOURCE_FAMILY = "worldbank"
WORLD_BANK_ANNOTATION_SOURCE_ID = "worldbank_projects_api"
TEXT_BUNDLE_MAX_CHARS = 7000

CANDIDATE_COLUMNS = (
    "annotation_record_id",
    "annotation_schema_version",
    "source_family",
    "source_id",
    "source_project_id",
    "source_parent_id",
    "project_title",
    "project_description",
    "country_name",
    "country_iso3",
    "region_name",
    "commitment_year",
    "implementation_start_year",
    "completion_year",
    "board_approval_date",
    "approval_fiscal_year",
    "closing_date",
    "sector_code",
    "sector_name",
    "theme_or_intent",
    "finance_type",
    "flow_class",
    "implementation_status",
    "source_recommended_for_aggregates",
    "source_umbrella_flag",
    "source_amount_raw",
    "source_amount_field",
    "source_amount_basis",
    "source_url",
    "annotation_text_available",
    "text_bundle_for_annotation",
    "mapping_provenance",
)


@dataclass(frozen=True)
class SilverTableInput:
    dataset: DatasetRef
    path: Path


def _series(df: pd.DataFrame, field: str) -> pd.Series:
    if field in df.columns:
        return df[field].copy()
    return pd.Series(pd.NA, index=df.index, dtype="string")


def _clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "<na>"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _stable_annotation_id(source_family: str, source_id: str, project_id: Any) -> str:
    # Preserve the legacy identifier convention for compatibility only.
    raw = "||".join((source_family, source_id, "" if pd.isna(project_id) else str(project_id)))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _bundle(row: pd.Series) -> str:
    fields = (
        ("Title", "project_title"),
        ("Description", "project_description"),
        ("Country", "country_name"),
        ("Region", "region_name"),
        ("Sector", "sector_name"),
        ("Sector code", "sector_code"),
        ("Theme/intent", "theme_or_intent"),
        ("Finance type", "finance_type"),
        ("Flow class", "flow_class"),
        ("Status", "implementation_status"),
        ("Commitment year", "commitment_year"),
        ("Implementation start year", "implementation_start_year"),
        ("Completion year", "completion_year"),
        ("Board approval date", "board_approval_date"),
        ("Approval fiscal year", "approval_fiscal_year"),
        ("Closing date", "closing_date"),
    )
    parts = []
    for label, field in fields:
        value = _clean_text(row.get(field))
        if value:
            parts.append(f"{label}: {value}")
    return "\n".join(parts)[:TEXT_BUNDLE_MAX_CHARS]


def _finalize(out: pd.DataFrame, mapping: dict[str, str | None]) -> pd.DataFrame:
    for column in CANDIDATE_COLUMNS:
        if column not in out.columns:
            out[column] = pd.NA
    out["annotation_schema_version"] = ANNOTATION_SCHEMA_VERSION
    out["mapping_provenance"] = json.dumps(mapping, sort_keys=True, ensure_ascii=False)
    title = out["project_title"].map(_clean_text)
    description = out["project_description"].map(_clean_text)
    out["annotation_text_available"] = (title != "") | (description != "")
    out["text_bundle_for_annotation"] = out.apply(_bundle, axis=1)
    return out.loc[:, CANDIDATE_COLUMNS].copy()


def build_aiddata_annotation_candidates(records: pd.DataFrame) -> pd.DataFrame:
    """Map exact AidData Silver fields into a derived review view."""
    mapping = {
        "source_project_id": "aiddata_record_id",
        "source_parent_id": "parent_id",
        "project_title": "title",
        "project_description": "narrative_description",
        "country_name": "country_of_activity",
        "country_iso3": "country_of_activity_iso3",
        "region_name": "region_of_activity",
        "commitment_year": "commitment_year",
        "implementation_start_year": "implementation_start_year",
        "completion_year": "completion_year",
        "sector_code": "sector_code",
        "sector_name": "sector_name",
        "theme_or_intent": "intent",
        "finance_type": "flow_type",
        "flow_class": "flow_class",
        "implementation_status": "status",
        "source_recommended_for_aggregates": "recommended_for_aggregates",
        "source_umbrella_flag": "umbrella",
        "source_amount_raw": "amount_constant_usd_2023",
        "source_url": "original_agreement_url",
    }
    out = pd.DataFrame(index=records.index)
    out["source_family"] = AIDDATA_ANNOTATION_SOURCE_FAMILY
    out["source_id"] = AIDDATA_ANNOTATION_SOURCE_ID
    for target, source in mapping.items():
        out[target] = _series(records, source)
    out["source_amount_field"] = "amount_constant_usd_2023"
    out["source_amount_basis"] = "source field amount_constant_usd_2023; no spatial allocation"
    out["annotation_record_id"] = [
        _stable_annotation_id(
            AIDDATA_ANNOTATION_SOURCE_FAMILY,
            AIDDATA_ANNOTATION_SOURCE_ID,
            project_id,
        )
        for project_id in out["source_project_id"]
    ]
    return _finalize(out, mapping)


def build_worldbank_annotation_candidates(projects: pd.DataFrame) -> pd.DataFrame:
    """Map exact WB source fields without relabeling approval/closing as implementation dates."""
    mapping = {
        "source_project_id": "id",
        "project_title": "project_name",
        "country_name": "countryshortname",
        "country_iso3": "countrycode",
        "region_name": "regionname",
        "board_approval_date": "boardapprovaldate",
        "approval_fiscal_year": "approvalfy",
        "closing_date": "closingdate",
        "sector_code": "sectorcode",
        "sector_name": "sector1.Name",
        "theme_or_intent": "theme_list",
        "finance_type": "prodlinetext",
        "flow_class": "source",
        "implementation_status": "projectstatusdisplay",
        "source_umbrella_flag": "supplementprojectflg",
        "source_amount_raw": "totalcommamt",
        "source_url": "url",
    }
    out = pd.DataFrame(index=projects.index)
    out["source_family"] = WORLD_BANK_ANNOTATION_SOURCE_FAMILY
    out["source_id"] = WORLD_BANK_ANNOTATION_SOURCE_ID
    for target, source in mapping.items():
        out[target] = _series(projects, source)

    # These remain unavailable rather than being inferred from approval/closing dates.
    out["implementation_start_year"] = pd.NA
    out["completion_year"] = pd.NA
    out["commitment_year"] = pd.NA
    out["source_parent_id"] = pd.NA
    out["source_recommended_for_aggregates"] = pd.NA
    out["project_description"] = pd.NA
    out["source_amount_field"] = "totalcommamt"
    out["source_amount_basis"] = "source field totalcommamt; no local-spending interpretation"
    out["annotation_record_id"] = [
        _stable_annotation_id(
            WORLD_BANK_ANNOTATION_SOURCE_FAMILY,
            WORLD_BANK_ANNOTATION_SOURCE_ID,
            project_id,
        )
        for project_id in out["source_project_id"]
    ]
    return _finalize(out, mapping)


def build_annotation_candidates(
    *,
    aiddata_records: pd.DataFrame | None = None,
    worldbank_projects: pd.DataFrame | None = None,
) -> pd.DataFrame:
    frames = []
    if aiddata_records is not None:
        frames.append(build_aiddata_annotation_candidates(aiddata_records))
    if worldbank_projects is not None:
        frames.append(build_worldbank_annotation_candidates(worldbank_projects))
    if not frames:
        raise ValueError("at least one Silver source table is required")
    return pd.concat(frames, ignore_index=True)


def build_annotation_qa(candidates: pd.DataFrame, source_count: int) -> tuple[QAResult, ...]:
    source_ids = candidates["source_project_id"].astype("string").str.strip()
    missing = int((source_ids.isna() | (source_ids == "")).sum())
    annotation_ids = candidates["annotation_record_id"].astype("string")
    duplicate_annotation_rows = int(annotation_ids.duplicated(keep=False).sum())
    jobs_like_columns = [column for column in candidates.columns if "job" in column.lower()]
    return (
        QAResult(
            check_id="annotation_candidates.silver_lineage",
            state="GREEN",
            message="derived candidates declare Silver DatasetRef inputs",
            metrics={"silver_input_count": source_count},
        ),
        QAResult(
            check_id="annotation_candidates.source_identity",
            state="GREEN" if missing == 0 and duplicate_annotation_rows == 0 else "RED",
            message="source identity and stable annotation identity are retained",
            metrics={
                "row_count": len(candidates),
                "missing_source_project_id": missing,
                "duplicate_annotation_id_rows": duplicate_annotation_rows,
            },
        ),
        QAResult(
            check_id="annotation_candidates.no_jobs_semantics",
            state="GREEN" if not jobs_like_columns else "RED",
            message="annotation candidates do not classify jobs/non-jobs",
            metrics={"jobs_like_column_count": len(jobs_like_columns)},
        ),
    )


def _verify_silver_input(value: SilverTableInput) -> None:
    if value.dataset.layer != DataLayer.SILVER:
        raise ValueError(f"annotation input must be Silver: {value.dataset.dataset_id}")
    if not value.path.exists():
        raise FileNotFoundError(value.path)
    if value.dataset.content_sha256 is not None:
        actual = sha256_file(value.path)
        if actual != value.dataset.content_sha256:
            raise ValueError(f"Silver input hash mismatch: {value.dataset.dataset_id}")


def _annotation_dataset_ref(version: str) -> DatasetRef:
    return DatasetRef(
        dataset_id="investments.annotation_candidates",
        version=version,
        schema_version=ANNOTATION_SCHEMA_VERSION,
        layer=DataLayer.GOLD,
        authority=AuthorityLevel.L2_DERIVED,
        grain=GrainSpec(keys=("annotation_record_id",)),
    )


def _compatibility_report(
    *,
    candidates: pd.DataFrame,
    new_ref: DatasetRef,
    legacy_candidate_path: str | Path | None,
) -> dict[str, Any]:
    if legacy_candidate_path is None:
        return blocked_parity("legacy annotation candidate file path was not supplied")
    path = Path(legacy_candidate_path)
    if not path.exists():
        return blocked_parity(f"legacy annotation candidate file is absent: {path}")

    legacy = pd.read_csv(path, dtype=str, low_memory=False)
    new = candidates.copy()
    for frame in (legacy, new):
        frame["_compat_key"] = (
            frame["source_family"].astype("string")
            + "||"
            + frame["source_id"].astype("string")
            + "||"
            + frame["source_project_id"].astype("string")
        )

    wb_legacy = legacy[legacy["source_family"].astype("string") == "worldbank"]
    board_alias_count = 0
    closing_alias_count = 0
    if "implementation_start_year" in wb_legacy.columns:
        values = wb_legacy["implementation_start_year"].astype("string").str.strip()
        board_alias_count = int((values.notna() & (values != "")).sum())
    if "completion_year" in wb_legacy.columns:
        values = wb_legacy["completion_year"].astype("string").str.strip()
        closing_alias_count = int((values.notna() & (values != "")).sum())

    explained = {
        "worldbank_boardapprovaldate_no_longer_aliases_implementation_start_year": board_alias_count,
        "worldbank_closingdate_no_longer_aliases_completion_year": closing_alias_count,
        "legacy_annotation_eligibility_not_carried_as_source_truth": len(legacy),
    }
    mapped_fields = (
        "source_family",
        "source_id",
        "source_project_id",
        "source_parent_id",
        "project_title",
        "project_description",
        "country_name",
        "country_iso3",
        "region_name",
        "commitment_year",
        "implementation_start_year",
        "completion_year",
        "sector_code",
        "sector_name",
        "theme_or_intent",
        "finance_type",
        "flow_class",
        "implementation_status",
        "source_url",
    )
    report = compare_unique_key_tables(
        legacy=legacy,
        new=new,
        key="_compat_key",
        legacy_ref=legacy_dataset_ref(
            "legacy.investments.annotation_candidates",
            "annotation-contract-v0.2",
            ("source_family", "source_id", "source_project_id"),
        ),
        new_ref=new_ref,
        mapped_fields=mapped_fields,
        explained_reason_categories=explained,
        explained_fields=("implementation_start_year", "completion_year"),
    )
    return {
        "status": report["summary"]["status"],
        "candidates": report,
        "explicit_semantic_corrections": [
            "World Bank boardapprovaldate remains board_approval_date and is not implementation_start_year.",
            "World Bank closingdate remains closing_date and is not completion_year.",
            "Legacy amount_usd parsing is replaced by source_amount_raw plus an explicit source field/basis.",
            "Legacy annotation_universe_flag is not treated as source or treatment eligibility.",
        ],
    }


def materialize_annotation_candidates(
    *,
    data_root: DataRoot,
    run_id: str,
    version: str,
    aiddata_records: SilverTableInput | None = None,
    worldbank_projects: SilverTableInput | None = None,
    legacy_candidate_path: str | Path | None = None,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> InvestmentMaterializationResult:
    """Build an optional L2 derived annotation review view strictly from Silver inputs."""
    silver_inputs = [item for item in (aiddata_records, worldbank_projects) if item is not None]
    if not silver_inputs:
        raise ValueError("at least one Silver input is required")
    for item in silver_inputs:
        _verify_silver_input(item)

    aiddata_df = pd.read_parquet(aiddata_records.path) if aiddata_records is not None else None
    worldbank_df = (
        pd.read_parquet(worldbank_projects.path) if worldbank_projects is not None else None
    )
    candidates = build_annotation_candidates(
        aiddata_records=aiddata_df,
        worldbank_projects=worldbank_df,
    )
    qa = build_annotation_qa(candidates, len(silver_inputs))
    ref = _annotation_dataset_ref(version)
    gold_base = data_root.gold("investments", "annotation_candidates", version)
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        inputs=tuple(item.dataset for item in silver_inputs),
        outputs=(
            FileMaterialization(
                dataset=ref,
                relative_path="annotation_candidates.parquet",
                destination_base=gold_base,
                writer=lambda path: candidates.to_parquet(path, index=False, engine="pyarrow"),
            ),
        ),
        parameters={
            "transformation": ANNOTATION_SCHEMA_VERSION,
            "input_layer": "silver",
            "authority": "L2-derived",
            "annotation_execution": "none",
            "treatment_semantics": "none",
            "amount_allocation": "none",
        },
        code_commit=code_commit,
        qa=qa,
        overwrite=overwrite,
    )
    hashed_ref = manifest.outputs[0]
    parity = _compatibility_report(
        candidates=candidates,
        new_ref=hashed_ref,
        legacy_candidate_path=legacy_candidate_path,
    )
    full_qa = manifest.qa
    persist_contract_artifacts(
        data_root=data_root,
        run_id=run_id,
        manifest=manifest,
        qa=full_qa,
        parity=parity,
        overwrite=overwrite,
    )
    mapping_rows = (
        candidates[["source_family", "source_id", "mapping_provenance"]]
        .drop_duplicates()
        .to_dict(orient="records")
    )
    persist_run_artifact(
        data_root,
        run_id,
        "provenance/annotation_mapping.json",
        json_text(mapping_rows),
        overwrite=overwrite,
    )
    return InvestmentMaterializationResult(
        manifest=manifest,
        datasets={"annotation_candidates": hashed_ref},
        paths={"annotation_candidates": gold_base / "annotation_candidates.parquet"},
        qa=full_qa,
        parity=parity,
    )
