#!/usr/bin/env python3
import json
import os
import re
from pathlib import Path

import pandas as pd

RAW_ROOT = Path("raw/investments")
OUTDIR = Path("data/interim/source_audit")
OUTDIR.mkdir(parents=True, exist_ok=True)

TABLE_EXTS = {".csv", ".tsv", ".xlsx", ".xls", ".jsonl", ".json", ".parquet"}

def source_family_from_path(path: Path) -> str:
    s = str(path).lower()
    if "canonical_china" in s or "aiddata_china" in s or "geogcdf" in s:
        return "china"
    if "canonical_worldbank" in s or "worldbank" in s:
        return "worldbank"
    if "legacy" in s:
        return "legacy"
    return "unknown"

def source_id_from_path(path: Path) -> str:
    parts = [p for p in path.parts if p.startswith(("aiddata_", "worldbank_", "iati_", "oecd_", "adb_", "afdb_", "aiib_"))]
    return parts[-1] if parts else "unknown_source"

def safe_read_table(path: Path, nrows=5000):
    suffix = path.suffix.lower()

    try:
        if suffix == ".csv":
            return pd.read_csv(path, dtype=str, nrows=nrows, low_memory=False)
        if suffix == ".tsv":
            return pd.read_csv(path, dtype=str, sep="\t", nrows=nrows, low_memory=False)
        if suffix in {".xlsx", ".xls"}:
            # Read first sheet only for audit.
            return pd.read_excel(path, dtype=str, nrows=nrows)
        if suffix == ".jsonl":
            return pd.read_json(path, lines=True, dtype=False, nrows=nrows)
        if suffix == ".json":
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(obj, list):
                return pd.json_normalize(obj[:nrows])
            if isinstance(obj, dict):
                for key in ["projects", "project", "data", "features", "records", "items"]:
                    if key in obj:
                        val = obj[key]
                        if isinstance(val, dict):
                            return pd.json_normalize(list(val.values())[:nrows])
                        if isinstance(val, list):
                            return pd.json_normalize(val[:nrows])
                return pd.json_normalize([obj])
        if suffix == ".parquet":
            return pd.read_parquet(path).head(nrows)
    except Exception as e:
        return e

    return None

def count_rows_fast(path: Path):
    suffix = path.suffix.lower()

    try:
        if suffix in {".csv", ".tsv", ".jsonl"}:
            with path.open("rb") as f:
                return max(0, sum(1 for _ in f) - (1 if suffix in {".csv", ".tsv"} else 0))
        if suffix == ".parquet":
            return len(pd.read_parquet(path, columns=[]))
    except Exception:
        return None

    return None

def classify_column(col: str) -> str:
    c = col.lower()
    rules = [
        ("project_id", r"(project.*id|id$|^p\d+$|aiddata.*id)"),
        ("title", r"(title|project.*name|name$)"),
        ("description", r"(description|descr|summary|objective|purpose|abstract|project.*development.*objective|pdo)"),
        ("country", r"(country|recipient|borrower|iso3|iso|adm0)"),
        ("region", r"(region)"),
        ("sector", r"(sector|theme|purpose|dac|crs|industry)"),
        ("amount", r"(amount|commitment|committed|usd|value|loan|grant|finance|financing|cost)"),
        ("date_year", r"(year|date|approval|commitment|start|end|close|completion|implementation)"),
        ("status", r"(status|pipeline|active|closed|stage)"),
        ("location", r"(location|lat|lon|latitude|longitude|geo|coord|adm|place|precision)"),
        ("url", r"(url|link|source|website|document)"),
    ]
    for role, pat in rules:
        if re.search(pat, c):
            return role
    return "other"

file_rows = []
schema_rows = []
missing_rows = []
preview_lines = []

for path in sorted(RAW_ROOT.rglob("*")):
    if not path.is_file():
        continue

    suffix = path.suffix.lower()
    size_mb = path.stat().st_size / 1024 / 1024

    if suffix not in TABLE_EXTS:
        file_rows.append({
            "path": str(path),
            "source_family": source_family_from_path(path),
            "source_id_guess": source_id_from_path(path),
            "ext": suffix,
            "size_mb": round(size_mb, 3),
            "is_table_candidate": False,
            "n_rows_fast": None,
            "n_cols_sample": None,
            "read_status": "skipped_non_table",
        })
        continue

    obj = safe_read_table(path)
    n_rows_fast = count_rows_fast(path)

    if isinstance(obj, Exception):
        file_rows.append({
            "path": str(path),
            "source_family": source_family_from_path(path),
            "source_id_guess": source_id_from_path(path),
            "ext": suffix,
            "size_mb": round(size_mb, 3),
            "is_table_candidate": True,
            "n_rows_fast": n_rows_fast,
            "n_cols_sample": None,
            "read_status": f"error: {repr(obj)[:250]}",
        })
        continue

    if obj is None or not isinstance(obj, pd.DataFrame):
        file_rows.append({
            "path": str(path),
            "source_family": source_family_from_path(path),
            "source_id_guess": source_id_from_path(path),
            "ext": suffix,
            "size_mb": round(size_mb, 3),
            "is_table_candidate": True,
            "n_rows_fast": n_rows_fast,
            "n_cols_sample": None,
            "read_status": "unreadable_or_unknown_shape",
        })
        continue

    df = obj
    file_rows.append({
        "path": str(path),
        "source_family": source_family_from_path(path),
        "source_id_guess": source_id_from_path(path),
        "ext": suffix,
        "size_mb": round(size_mb, 3),
        "is_table_candidate": True,
        "n_rows_fast": n_rows_fast,
        "n_rows_sample": len(df),
        "n_cols_sample": len(df.columns),
        "read_status": "ok",
    })

    for col in df.columns:
        s = df[col]
        non_null = s.notna().sum()
        example_values = (
            s.dropna()
             .astype(str)
             .replace("", pd.NA)
             .dropna()
             .head(3)
             .tolist()
        )
        schema_rows.append({
            "path": str(path),
            "source_family": source_family_from_path(path),
            "source_id_guess": source_id_from_path(path),
            "column": col,
            "column_role_guess": classify_column(str(col)),
            "sample_non_null": int(non_null),
            "sample_missing_share": round(1 - non_null / max(len(df), 1), 4),
            "examples": " | ".join(example_values)[:500],
        })

    # Preview only for reasonably table-like files.
    preview_lines.append(f"\n\n## {path}\n")
    preview_lines.append(f"- source_family: {source_family_from_path(path)}\n")
    preview_lines.append(f"- rows_sample: {len(df)}\n")
    preview_lines.append(f"- cols_sample: {len(df.columns)}\n\n")
    preview_lines.append(df.head(5).to_markdown(index=False))

pd.DataFrame(file_rows).to_csv(OUTDIR / "raw_file_inventory.csv", index=False)
pd.DataFrame(schema_rows).to_csv(OUTDIR / "raw_table_schema_profile.csv", index=False)
Path(OUTDIR / "raw_table_previews.md").write_text("\n".join(preview_lines), encoding="utf-8")

print(f"Wrote {OUTDIR / 'raw_file_inventory.csv'}")
print(f"Wrote {OUTDIR / 'raw_table_schema_profile.csv'}")
print(f"Wrote {OUTDIR / 'raw_table_previews.md'}")
