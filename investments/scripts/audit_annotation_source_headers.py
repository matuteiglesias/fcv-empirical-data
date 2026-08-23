#!/usr/bin/env python3
from pathlib import Path
import re
import pandas as pd

MANIFEST = Path("data/interim/annotation_contract/source_tables_v0_1.csv")
OUTDIR = Path("data/interim/header_audit")
OUTDIR.mkdir(parents=True, exist_ok=True)

ROLE_PATTERNS = {
    "source_project_id": r"(aiddata_record_id|^id$|project.*id|activity.*id)",
    "parent_project_id": r"(parent_id|parent.*project|umbrella)",
    "title": r"(title|project_name|project.*name|activity.*title|name$)",
    "description": r"(narrative_description|description|summary|objective|purpose|pdo)",
    "country": r"(country_of_activity|countryname|countryshortname|countrycode|iso3|recipient|borrower)",
    "region": r"(region_of_activity|regionname|region)",
    "year_date": r"(commitment_year|approvalfy|boardapprovaldate|closingdate|start|completion|date|year)",
    "sector_theme": r"(sector|theme|intent|purpose|mjtheme)",
    "finance_type": r"(flow_type|flow_class|prodline|prodlinetext|lendinginstr|source)",
    "amount": r"(amount|commitment|commamt|ibrdcommamt|idacommamt|totalamt|usd|cost|grantamt)",
    "status": r"(status|projectstatusdisplay|recommended_for_aggregates)",
    "location_geo": r"(location|lat|lon|geo|coord|adm|precision)",
    "url_source": r"(url|source_url|all_source_urls|projectdocs|agreement)",
    "quality": r"(quality|score|completeness|detail)",
}

def guess_role(col):
    c = str(col).lower()
    hits = []
    for role, pat in ROLE_PATTERNS.items():
        if re.search(pat, c):
            hits.append(role)
    return "|".join(hits) if hits else "other"

def read_csv_sample(path, nrows=None):
    return pd.read_csv(path, dtype=str, low_memory=False, nrows=nrows)

manifest = pd.read_csv(MANIFEST, dtype=str)

file_rows = []
col_rows = []
preview_rows = []

for _, m in manifest.iterrows():
    path = Path(m["path"])
    source_family = m["source_family"]
    source_id = m["source_id"]
    table_role = m["table_role"]

    if not path.exists():
        file_rows.append({
            "source_family": source_family,
            "source_id": source_id,
            "table_role": table_role,
            "path": str(path),
            "status": "missing_file",
        })
        continue

    print(f"Reading {source_family} / {table_role}: {path}")
    df = read_csv_sample(path)

    file_rows.append({
        "source_family": source_family,
        "source_id": source_id,
        "table_role": table_role,
        "path": str(path),
        "status": "ok",
        "n_rows": len(df),
        "n_cols": len(df.columns),
        "size_mb": round(path.stat().st_size / 1024 / 1024, 3),
    })

    for col in df.columns:
        s = df[col]
        nonmissing = s.notna() & (s.astype(str).str.strip() != "")
        examples = (
            s[nonmissing]
            .astype(str)
            .drop_duplicates()
            .head(5)
            .tolist()
        )

        col_rows.append({
            "source_family": source_family,
            "source_id": source_id,
            "table_role": table_role,
            "column": col,
            "role_guess": guess_role(col),
            "n_rows": len(df),
            "n_nonmissing": int(nonmissing.sum()),
            "nonmissing_share": round(float(nonmissing.mean()), 4) if len(df) else None,
            "n_unique": int(s[nonmissing].nunique()),
            "examples": " | ".join(examples)[:1000],
        })

    keep_cols = list(df.columns[:40])
    preview = df.head(3)[keep_cols].copy()
    preview.insert(0, "_source_family", source_family)
    preview.insert(1, "_table_role", table_role)
    preview_rows.append(preview)

pd.DataFrame(file_rows).to_csv(OUTDIR / "source_file_inventory.csv", index=False)
pd.DataFrame(col_rows).to_csv(OUTDIR / "source_column_profile.csv", index=False)

if preview_rows:
    pd.concat(preview_rows, ignore_index=True).to_csv(OUTDIR / "source_table_previews.csv", index=False)

print(f"Wrote {OUTDIR / 'source_file_inventory.csv'}")
print(f"Wrote {OUTDIR / 'source_column_profile.csv'}")
print(f"Wrote {OUTDIR / 'source_table_previews.csv'}")
