from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from empirical_contracts import (
    AuthorityLevel,
    DataLayer,
    DatasetRef,
    GrainSpec,
    QAResult,
    SourceSnapshotRef,
)
from spatial_foundation import DataRoot, register_external_snapshot

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact
from fcv_empirical.investments.common import (
    InvestmentMaterializationResult,
    blocked_parity,
    coverage_profile,
    json_text,
    persist_contract_artifacts,
)
from fcv_empirical.investments.parity import compare_unique_key_tables, legacy_dataset_ref

WORLD_BANK_SOURCE = "worldbank_projects_api"
WORLD_BANK_ORIGIN = "https://search.worldbank.org/api/v2/projects"
WORLD_BANK_PROJECT_ID = "id"
WB_PARITY_FIELDS = (
    "id",
    "project_name",
    "boardapprovaldate",
    "closingdate",
    "approvalfy",
    "totalcommamt",
    "projectstatusdisplay",
    "countryshortname",
    "countrycode",
    "regionname",
)


@dataclass(frozen=True)
class WorldBankExtraction:
    projects: pd.DataFrame
    qa: tuple[QAResult, ...]
    coverage: pd.DataFrame
    page_audit: pd.DataFrame
    parse_errors: tuple[dict[str, str], ...]


def extract_projects(payload: Any) -> list[dict[str, Any]]:
    """Recover project records from the response shapes preserved by the legacy puller."""
    if not isinstance(payload, dict):
        return []
    for key in ("projects", "project"):
        value = payload.get(key)
        if isinstance(value, dict):
            return [record for record in value.values() if isinstance(record, dict)]
    records = []
    for value in payload.values():
        if isinstance(value, dict) and (
            "project_name" in value or WORLD_BANK_PROJECT_ID in value or "projectid" in value
        ):
            records.append(value)
    return records


def _scalar_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def flatten_worldbank_record(record: dict[str, Any], prefix: str = "") -> dict[str, str | None]:
    """Flatten nested dictionaries deterministically; serialize lists without semantic parsing."""
    flat: dict[str, str | None] = {}
    for key in sorted(record):
        value = record[key]
        field = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            nested = flatten_worldbank_record(value, field)
            for nested_key, nested_value in nested.items():
                if nested_key in flat:
                    raise ValueError(f"World Bank flattened field collision: {nested_key}")
                flat[nested_key] = nested_value
        elif isinstance(value, list):
            flat[field] = json.dumps(
                value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )
        else:
            flat[field] = _scalar_text(value)
    return flat


def _page_offset(path: Path) -> int | None:
    match = re.search(r"page_os_(\d+)\.json$", path.name)
    return int(match.group(1)) if match else None


def _read_api_error_count(source_dir: Path) -> int:
    path = source_dir / "api_errors.json"
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return 1
    return len(payload) if isinstance(payload, list) else 1


def load_worldbank_pages(source_dir: str | Path) -> WorldBankExtraction:
    root = Path(source_dir)
    pages = sorted(root.glob("page_os_*.json"))
    rows: list[dict[str, Any]] = []
    page_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for page in pages:
        try:
            payload = json.loads(page.read_text(encoding="utf-8"))
            records = extract_projects(payload)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            errors.append({"page_file": page.name, "error": f"{type(error).__name__}: {error}"})
            page_rows.append(
                {
                    "page_file": page.name,
                    "page_offset": _page_offset(page),
                    "record_count": 0,
                    "parse_status": "error",
                }
            )
            continue

        page_rows.append(
            {
                "page_file": page.name,
                "page_offset": _page_offset(page),
                "record_count": len(records),
                "parse_status": "ok",
            }
        )
        for position, record in enumerate(records):
            flat = flatten_worldbank_record(record)
            flat["fcv_source_page_file"] = page.name
            flat["fcv_source_page_offset"] = _page_offset(page)
            flat["fcv_source_record_position"] = position
            flat["fcv_source_record_json"] = json.dumps(
                record, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )
            rows.append(flat)

    projects = pd.DataFrame(rows)
    page_audit = pd.DataFrame(page_rows)
    coverage = coverage_profile(projects, "projects")

    source_id_field_present = WORLD_BANK_PROJECT_ID in projects.columns
    if not source_id_field_present:
        missing_ids = len(projects)
        duplicate_rows = 0
        unique_ids = 0
    else:
        ids = projects[WORLD_BANK_PROJECT_ID].astype("string").str.strip()
        missing_mask = ids.isna() | (ids == "")
        missing_ids = int(missing_mask.sum())
        present = ids[~missing_mask]
        duplicate_rows = int(present.duplicated(keep=False).sum())
        unique_ids = int(present.nunique())
    identity_ok = (
        source_id_field_present
        and len(projects) > 0
        and missing_ids == 0
        and duplicate_rows == 0
    )

    acquisition_errors = _read_api_error_count(root)
    qa = (
        QAResult(
            check_id="worldbank.pages.downloaded",
            state="GREEN" if pages else "RED",
            message="downloaded API page responses are the Bronze authority",
            metrics={"downloaded_pages": len(pages)},
        ),
        QAResult(
            check_id="worldbank.records.raw_count",
            state="GREEN" if len(projects) > 0 else "RED",
            message="raw project records recovered from downloaded page responses",
            metrics={"raw_record_count": len(projects)},
        ),
        QAResult(
            check_id="worldbank.projects.source_id",
            state="GREEN" if identity_ok else "RED",
            message="World Bank project identity uses the exact source field id",
            metrics={
                "source_id_field_present": source_id_field_present,
                "missing_source_id": missing_ids,
                "duplicate_source_id_rows": duplicate_rows,
                "unique_source_ids": unique_ids,
            },
        ),
        QAResult(
            check_id="worldbank.pages.parse_errors",
            state="GREEN" if not errors else "RED",
            message="page JSON parse errors remain visible",
            metrics={"page_parse_errors": len(errors)},
        ),
        QAResult(
            check_id="worldbank.acquisition.errors",
            state="GREEN" if acquisition_errors == 0 else "YELLOW",
            message="acquisition errors from the resumable pull remain visible",
            metrics={"acquisition_error_count": acquisition_errors},
        ),
        QAResult(
            check_id="worldbank.coverage_profile",
            state="GREEN",
            message="column coverage profile recorded without assigning meaning to absence",
            metrics={"profiled_columns": len(coverage)},
        ),
    )
    return WorldBankExtraction(
        projects=projects,
        qa=qa,
        coverage=coverage,
        page_audit=page_audit,
        parse_errors=tuple(errors),
    )


def _source_metadata(source_dir: Path) -> dict[str, Any]:
    path = source_dir / "source_metadata.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def register_worldbank_snapshot(
    source_dir: str | Path,
    *,
    release: str | None = None,
) -> SourceSnapshotRef:
    """Register downloaded page responses plus acquisition metadata, excluding derived flat files."""
    root = Path(source_dir)
    pages = sorted(root.glob("page_os_*.json"))
    if not pages:
        raise FileNotFoundError(f"no World Bank page_os_*.json files found under {root}")
    sidecars = [
        root / name
        for name in ("source_metadata.json", "api_query_log.json", "api_errors.json", "page_counts.csv")
        if (root / name).exists()
    ]
    metadata = _source_metadata(root)
    resolved_release = release or str(metadata.get("accessed_date") or root.name)
    snapshot = register_external_snapshot(WORLD_BANK_SOURCE, resolved_release, [*pages, *sidecars])
    return snapshot.model_copy(update={"origin": WORLD_BANK_ORIGIN})


def _dataset_ref(version: str) -> DatasetRef:
    return DatasetRef(
        dataset_id="investments.worldbank.projects",
        version=version,
        schema_version="source-native-silver-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=(WORLD_BANK_PROJECT_ID,)),
    )


def _worldbank_parity(
    *,
    projects: pd.DataFrame,
    new_ref: DatasetRef,
    legacy_flat_path: str | Path | None,
) -> dict[str, Any]:
    if legacy_flat_path is None:
        return blocked_parity("legacy worldbank_projects_flat.csv path was not supplied")
    path = Path(legacy_flat_path)
    if not path.exists():
        return blocked_parity(f"legacy World Bank flat file is absent: {path}")
    legacy = pd.read_csv(path, dtype=str, low_memory=False)
    fields = [field for field in WB_PARITY_FIELDS if field in legacy.columns and field in projects.columns]
    report = compare_unique_key_tables(
        legacy=legacy,
        new=projects,
        key=WORLD_BANK_PROJECT_ID,
        legacy_ref=legacy_dataset_ref(
            "legacy.investments.worldbank.projects_flat",
            "worldbank-projects-flat-csv",
            (WORLD_BANK_PROJECT_ID,),
        ),
        new_ref=new_ref,
        mapped_fields=fields,
    )
    return {
        "status": report["summary"]["status"],
        "projects": report,
        "notes": [
            "Downloaded page JSON is Bronze authority; the legacy flat CSV is parity evidence only.",
            "Nested list serialization may differ without changing row/key parity.",
        ],
    }


def materialize_worldbank_silver(
    *,
    source_dir: str | Path,
    data_root: DataRoot,
    run_id: str,
    source_snapshot: SourceSnapshotRef | None = None,
    release: str | None = None,
    legacy_flat_path: str | Path | None = None,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> InvestmentMaterializationResult:
    """Materialize downloaded World Bank page JSON to one source-project-per-row Silver table."""
    snapshot = source_snapshot or register_worldbank_snapshot(source_dir, release=release)
    extraction = load_worldbank_pages(source_dir)
    version = snapshot.snapshot_id
    silver_base = data_root.silver("investments", "worldbank", version)
    ref = _dataset_ref(version)
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        source_snapshot=snapshot,
        outputs=(
            FileMaterialization(
                dataset=ref,
                relative_path="projects.parquet",
                destination_base=silver_base,
                writer=lambda path: extraction.projects.to_parquet(
                    path, index=False, engine="pyarrow"
                ),
            ),
        ),
        parameters={
            "source": WORLD_BANK_SOURCE,
            "snapshot": snapshot.snapshot_id,
            "transformation": "worldbank-api-source-native-silver-v1",
            "project_id_field": WORLD_BANK_PROJECT_ID,
            "nested_lists": "canonical_json_string",
            "amount_allocation": "none",
            "date_reinterpretation": "none",
        },
        code_commit=code_commit,
        qa=extraction.qa,
        overwrite=overwrite,
    )
    hashed_ref = manifest.outputs[0]
    parity = _worldbank_parity(
        projects=extraction.projects,
        new_ref=hashed_ref,
        legacy_flat_path=legacy_flat_path,
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
    persist_run_artifact(
        data_root,
        run_id,
        "qa/coverage_profile.json",
        json_text(extraction.coverage.to_dict(orient="records")),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "qa/page_audit.json",
        json_text(extraction.page_audit.to_dict(orient="records")),
        overwrite=overwrite,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "qa/page_parse_errors.json",
        json_text(list(extraction.parse_errors)),
        overwrite=overwrite,
    )
    return InvestmentMaterializationResult(
        manifest=manifest,
        datasets={"projects": hashed_ref},
        paths={"projects": silver_base / "projects.parquet"},
        qa=full_qa,
        parity=parity,
    )
