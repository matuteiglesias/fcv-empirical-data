from __future__ import annotations

import re
from collections.abc import Iterable
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
    validate_source_snapshot,
)
from fcv_empirical.investments.parity import compare_unique_key_tables, legacy_dataset_ref

AIDDATA_SOURCE = "aiddata_china_clg_lmic"
AIDDATA_ORIGIN = "https://docs.aiddata.org/ad4/datasets/AidDatas_CLG_LMIC_Dataset_v1.0.zip"
PROVENANCE_COLUMNS = {
    "fcv_source_sheet",
    "fcv_source_table",
    "fcv_source_sheet_row_number",
}

AIDDATA_SHEETS: dict[str, dict[str, Any]] = {
    "CLG-LMIC 1.0_Records": {
        "table": "records",
        "filename": "records.parquet",
        "header": 0,
        "grain": ("aiddata_record_id",),
    },
    "Borrower Ownership": {
        "table": "borrower_ownership",
        "filename": "borrower_ownership.parquet",
        "header": 0,
        "grain": ("aiddata_record_id", "fcv_source_sheet_row_number"),
    },
    "CountryList": {
        "table": "country_list",
        "filename": "country_list.parquet",
        "header": 4,
        "grain": ("fcv_source_sheet_row_number",),
    },
    "Definitions_Records": {
        "table": "definitions_records",
        "filename": "definitions_records.parquet",
        "header": 3,
        "grain": ("fcv_source_sheet_row_number",),
    },
    "Definitions_Borrower Ownership": {
        "table": "definitions_borrower_ownership",
        "filename": "definitions_borrower_ownership.parquet",
        "header": 3,
        "grain": ("fcv_source_sheet_row_number",),
    },
}


@dataclass(frozen=True)
class AidDataExtraction:
    tables: dict[str, pd.DataFrame]
    column_mapping: pd.DataFrame
    qa: tuple[QAResult, ...]
    coverage: pd.DataFrame


def _clean_column_name(value: object) -> str:
    text = "" if value is None else str(value).strip()
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^0-9a-zA-Z_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_").lower()
    if not text:
        text = "unnamed"
    if text[0].isdigit():
        text = f"col_{text}"
    if text in PROVENANCE_COLUMNS:
        text = f"source_{text}"
    return text


def normalize_columns(
    columns: Iterable[object],
    *,
    sheet_name: str,
    table_name: str,
) -> tuple[list[str], pd.DataFrame]:
    seen: dict[str, int] = {}
    normalized: list[str] = []
    rows: list[dict[str, Any]] = []
    for position, original in enumerate(columns):
        base = _clean_column_name(original)
        occurrence = seen.get(base, 0)
        seen[base] = occurrence + 1
        final = base if occurrence == 0 else f"{base}_{occurrence}"
        normalized.append(final)
        rows.append(
            {
                "source_sheet": sheet_name,
                "source_table": table_name,
                "column_position": position,
                "original_column": "" if original is None else str(original),
                "normalized_column": final,
                "base_normalized_column": base,
                "was_duplicate": final != base,
                "excluded": False,
                "exclusion_reason": None,
            }
        )
    return normalized, pd.DataFrame(rows)


def _present_mask(series: pd.Series) -> pd.Series:
    values = series.astype("string")
    return values.notna() & (values.str.strip().fillna("") != "")


def read_aiddata_sheet(
    workbook_path: str | Path,
    *,
    sheet_name: str,
    table_name: str,
    header: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read one known source table, retaining source columns and row provenance."""
    raw = pd.read_excel(
        Path(workbook_path),
        sheet_name=sheet_name,
        header=header,
        dtype=str,
    )
    source_row_numbers = pd.Series(raw.index + header + 2, index=raw.index)
    normalized, mapping = normalize_columns(raw.columns, sheet_name=sheet_name, table_name=table_name)
    raw = raw.copy()
    raw.columns = normalized

    # Only unnamed Excel layout columns that are completely empty are excluded.
    # Named source variables are retained even when their observed coverage is zero.
    excluded: list[str] = []
    for position, column in enumerate(normalized):
        original = str(mapping.loc[position, "original_column"])
        if original.startswith("Unnamed:") and not _present_mask(raw[column]).any():
            excluded.append(column)
            mapping.loc[position, "excluded"] = True
            mapping.loc[position, "exclusion_reason"] = "fully_empty_unnamed_excel_layout_column"

    if excluded:
        raw = raw.drop(columns=excluded)

    source_fields = list(raw.columns)
    if source_fields:
        row_present = pd.concat([_present_mask(raw[column]) for column in source_fields], axis=1).any(axis=1)
        raw = raw.loc[row_present].copy()
        source_row_numbers = source_row_numbers.loc[row_present]

    raw["fcv_source_sheet"] = sheet_name
    raw["fcv_source_table"] = table_name
    raw["fcv_source_sheet_row_number"] = source_row_numbers.astype("int64")
    return raw.reset_index(drop=True), mapping


def register_aiddata_snapshot(
    workbook_path: str | Path,
    *,
    release: str = "v1.0",
    additional_files: Iterable[str | Path] = (),
) -> SourceSnapshotRef:
    """Register the immutable workbook/release files without copying them."""
    paths = [Path(workbook_path), *(Path(path) for path in additional_files)]
    snapshot = register_external_snapshot(AIDDATA_SOURCE, release, paths)
    return snapshot.model_copy(update={"origin": AIDDATA_ORIGIN})


def _source_id_qa(records: pd.DataFrame) -> QAResult:
    key = "aiddata_record_id"
    if key not in records.columns:
        return QAResult(
            check_id="aiddata.records.source_id",
            state="RED",
            message="source records do not contain aiddata_record_id",
            metrics={"row_count": len(records), "source_id_field_present": False},
        )
    values = records[key].astype("string").str.strip()
    missing = int((values.isna() | (values == "")).sum())
    present = values[values.notna() & (values != "")]
    duplicate_rows = int(present.duplicated(keep=False).sum())
    identity_ok = len(records) > 0 and missing == 0 and duplicate_rows == 0
    return QAResult(
        check_id="aiddata.records.source_id",
        state="GREEN" if identity_ok else "RED",
        message="AidData record identity remains source-native",
        metrics={
            "row_count": len(records),
            "source_id_field_present": True,
            "missing_source_id": missing,
            "duplicate_source_id_rows": duplicate_rows,
            "unique_source_ids": int(present.nunique()),
        },
    )


def _relationship_qa(records: pd.DataFrame, ownership: pd.DataFrame) -> tuple[QAResult, QAResult]:
    key = "aiddata_record_id"
    if key not in records.columns or key not in ownership.columns:
        missing = QAResult(
            check_id="aiddata.borrower_ownership.referential_integrity",
            state="RED",
            message="aiddata_record_id is missing from parent or child table",
        )
        return missing, missing.model_copy(
            update={
                "check_id": "aiddata.borrower_ownership.multiplicity",
                "message": "borrower ownership multiplicity cannot be evaluated",
            }
        )

    parent_values = records[key].astype("string").str.strip()
    parent_ids = set(parent_values[parent_values.notna() & (parent_values != "")].tolist())
    child_values = ownership[key].astype("string").str.strip()
    child_present = child_values[child_values.notna() & (child_values != "")]
    child_missing = int((child_values.isna() | (child_values == "")).sum())
    orphan_mask = ~child_present.isin(parent_ids)
    orphan_rows = int(orphan_mask.sum())
    counts = child_present.value_counts()
    parents_with_multiple_rows = int((counts > 1).sum())

    referential = QAResult(
        check_id="aiddata.borrower_ownership.referential_integrity",
        state="GREEN" if child_missing == 0 and orphan_rows == 0 else "RED",
        message="borrower ownership remains a child table linked by aiddata_record_id",
        metrics={
            "child_rows": len(ownership),
            "missing_parent_id": child_missing,
            "orphan_child_rows": orphan_rows,
            "unique_parent_ids_in_child": int(child_present.nunique()),
        },
    )
    multiplicity = QAResult(
        check_id="aiddata.borrower_ownership.multiplicity",
        state="GREEN",
        message="multiple borrower ownership rows per project are preserved rather than flattened",
        metrics={
            "parents_with_multiple_child_rows": parents_with_multiple_rows,
            "max_child_rows_per_parent": int(counts.max()) if len(counts) else 0,
        },
    )
    return referential, multiplicity


def build_aiddata_qa(tables: dict[str, pd.DataFrame]) -> tuple[tuple[QAResult, ...], pd.DataFrame]:
    results: list[QAResult] = []
    coverage_parts: list[pd.DataFrame] = []
    for table_name, table in tables.items():
        results.append(
            QAResult(
                check_id=f"aiddata.{table_name}.shape",
                state="GREEN",
                message="source table shape recorded",
                metrics={"row_count": len(table), "column_count": len(table.columns)},
            )
        )
        coverage_parts.append(coverage_profile(table, table_name))

    records = tables["records"]
    ownership = tables["borrower_ownership"]
    results.append(_source_id_qa(records))
    results.extend(_relationship_qa(records, ownership))
    coverage = pd.concat(coverage_parts, ignore_index=True)
    results.append(
        QAResult(
            check_id="aiddata.coverage_profile",
            state="GREEN",
            message="column coverage profile recorded without assigning meaning to absence",
            metrics={"profiled_columns": len(coverage)},
        )
    )
    return tuple(results), coverage


def extract_aiddata_workbook(workbook_path: str | Path) -> AidDataExtraction:
    tables: dict[str, pd.DataFrame] = {}
    mappings: list[pd.DataFrame] = []
    for sheet_name, spec in AIDDATA_SHEETS.items():
        table, mapping = read_aiddata_sheet(
            workbook_path,
            sheet_name=sheet_name,
            table_name=spec["table"],
            header=spec["header"],
        )
        tables[spec["table"]] = table
        mappings.append(mapping)
    column_mapping = pd.concat(mappings, ignore_index=True)
    qa, coverage = build_aiddata_qa(tables)
    return AidDataExtraction(
        tables=tables,
        column_mapping=column_mapping,
        qa=qa,
        coverage=coverage,
    )


def _dataset_ref(table_name: str, version: str, grain: tuple[str, ...]) -> DatasetRef:
    return DatasetRef(
        dataset_id=f"investments.aiddata_clg.{table_name}",
        version=version,
        schema_version="source-native-silver-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=grain),
    )


def _mapping_dataset_ref(version: str) -> DatasetRef:
    return DatasetRef(
        dataset_id="investments.aiddata_clg.column_name_mapping",
        version=version,
        schema_version="column-mapping-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L1_NORMALIZED,
        grain=GrainSpec(keys=("source_sheet", "column_position")),
    )


def _aiddata_parity(
    *,
    extraction: AidDataExtraction,
    new_refs: dict[str, DatasetRef],
    legacy_relational_dir: str | Path | None,
) -> dict[str, Any]:
    if legacy_relational_dir is None:
        return blocked_parity("legacy AidData relational extraction path was not supplied")
    root = Path(legacy_relational_dir)
    tables_root = root / "tables" if (root / "tables").exists() else root
    records_path = tables_root / "aiddata_records.csv"
    if not records_path.exists():
        return blocked_parity(f"legacy AidData records file is absent: {records_path}")

    legacy_records = pd.read_csv(records_path, dtype=str, low_memory=False)
    new_records = extraction.tables["records"]
    source_fields = [
        field
        for field in legacy_records.columns
        if field in new_records.columns and not field.startswith("fcv_")
    ]
    records_report = compare_unique_key_tables(
        legacy=legacy_records,
        new=new_records,
        key="aiddata_record_id",
        legacy_ref=legacy_dataset_ref(
            "legacy.investments.aiddata_clg.records",
            "relational-csv-v1",
            ("aiddata_record_id",),
        ),
        new_ref=new_refs["records"],
        mapped_fields=source_fields,
    )

    table_rows: dict[str, Any] = {}
    row_mismatch = False
    legacy_names = {
        "records": "aiddata_records.csv",
        "borrower_ownership": "aiddata_borrower_ownership.csv",
        "country_list": "aiddata_country_list.csv",
        "definitions_records": "aiddata_definitions_records.csv",
        "definitions_borrower_ownership": "aiddata_definitions_borrower_ownership.csv",
    }
    for table_name, filename in legacy_names.items():
        path = tables_root / filename
        if not path.exists():
            table_rows[table_name] = {"status": "NOT_RUN", "reason": f"missing {path}"}
            continue
        legacy_table = pd.read_csv(path, dtype=str, low_memory=False)
        new_table = extraction.tables[table_name]
        mismatch = len(legacy_table) != len(new_table)
        row_mismatch = row_mismatch or mismatch
        table_rows[table_name] = {
            "legacy_rows": len(legacy_table),
            "new_rows": len(new_table),
            "row_count_equal": not mismatch,
        }

    record_status = records_report["summary"]["status"]
    overall = "EQUAL" if record_status == "EQUAL" and not row_mismatch else "UNEXPLAINED_DIVERGENCE"
    return {
        "status": overall,
        "records": records_report,
        "table_row_counts": table_rows,
        "notes": [
            "CSV-vs-Parquet byte equality is not required.",
            "Added fcv_* provenance columns are not source-field discrepancies.",
        ],
    }


def materialize_aiddata_silver(
    *,
    workbook_path: str | Path,
    data_root: DataRoot,
    run_id: str,
    release: str = "v1.0",
    source_snapshot: SourceSnapshotRef | None = None,
    additional_snapshot_files: Iterable[str | Path] = (),
    legacy_relational_dir: str | Path | None = None,
    code_commit: str | None = None,
    overwrite: bool = False,
) -> InvestmentMaterializationResult:
    """Materialize the CLG-LMIC workbook into relational source-native Silver tables."""
    snapshot = source_snapshot or register_aiddata_snapshot(
        workbook_path,
        release=release,
        additional_files=additional_snapshot_files,
    )
    validate_source_snapshot(
        snapshot,
        expected_source=AIDDATA_SOURCE,
        expected_release=release,
        required_paths=(workbook_path,),
    )
    extraction = extract_aiddata_workbook(workbook_path)
    silver_base = data_root.silver("investments", "aiddata_clg", release)

    refs: dict[str, DatasetRef] = {}
    requests: list[FileMaterialization] = []
    paths: dict[str, Path] = {}
    for spec in AIDDATA_SHEETS.values():
        table_name = spec["table"]
        ref = _dataset_ref(table_name, release, spec["grain"])
        refs[table_name] = ref
        table = extraction.tables[table_name]
        requests.append(
            FileMaterialization(
                dataset=ref,
                relative_path=spec["filename"],
                destination_base=silver_base,
                writer=lambda path, frame=table: frame.to_parquet(
                    path, index=False, engine="pyarrow"
                ),
            )
        )
        paths[table_name] = silver_base / spec["filename"]

    mapping_ref = _mapping_dataset_ref(release)
    refs["column_name_mapping"] = mapping_ref
    requests.append(
        FileMaterialization(
            dataset=mapping_ref,
            relative_path="column_name_mapping.parquet",
            destination_base=silver_base,
            writer=lambda path: extraction.column_mapping.to_parquet(
                path, index=False, engine="pyarrow"
            ),
        )
    )
    paths["column_name_mapping"] = silver_base / "column_name_mapping.parquet"

    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        source_snapshot=snapshot,
        outputs=requests,
        parameters={
            "source": AIDDATA_SOURCE,
            "release": release,
            "transformation": "aiddata-clg-source-native-silver-v1",
            "column_normalization": "snake_case_with_explicit_mapping",
            "amount_allocation": "none",
        },
        code_commit=code_commit,
        qa=extraction.qa,
        overwrite=overwrite,
    )
    hashed_by_id = {dataset.dataset_id: dataset for dataset in manifest.outputs}
    hashed_refs = {name: hashed_by_id[ref.dataset_id] for name, ref in refs.items()}
    parity = _aiddata_parity(
        extraction=extraction,
        new_refs=hashed_refs,
        legacy_relational_dir=legacy_relational_dir,
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
        "provenance/column_name_mapping.json",
        json_text(extraction.column_mapping.to_dict(orient="records")),
        overwrite=overwrite,
    )
    return InvestmentMaterializationResult(
        manifest=manifest,
        datasets=hashed_refs,
        paths=paths,
        qa=full_qa,
        parity=parity,
    )
