#!/usr/bin/env python3
import json
import time
from pathlib import Path
from datetime import date

import requests
import pandas as pd

BASE = "https://search.worldbank.org/api/v2/projects"

OUTDIR = Path("raw/investments/canonical_worldbank/worldbank_projects_api_pull_" + date.today().isoformat())
OUTDIR.mkdir(parents=True, exist_ok=True)

ROWS = 500
OS = 0
all_records = []
query_log = []

def extract_projects(payload):
    """
    World Bank search APIs often return metadata plus a dict of records.
    This tries to keep the script robust to small response-shape changes.
    """
    if isinstance(payload, dict):
        # Common shape: {"total": "...", "projects": {"P123": {...}, ...}}
        for key in ["projects", "project"]:
            if key in payload and isinstance(payload[key], dict):
                return list(payload[key].values())
        # Fallback: collect dict values that look like project records.
        vals = []
        for v in payload.values():
            if isinstance(v, dict) and ("project_name" in v or "id" in v or "projectid" in v):
                vals.append(v)
        if vals:
            return vals
    return []

while True:
    params = {
        "format": "json",
        "rows": ROWS,
        "os": OS,
    }
    print(f"Fetching os={OS} rows={ROWS}")
    r = requests.get(BASE, params=params, timeout=60)
    query_log.append({"url": r.url, "status_code": r.status_code})
    r.raise_for_status()

    payload = r.json()
    batch = extract_projects(payload)

    raw_file = OUTDIR / f"page_os_{OS:07d}.json"
    raw_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not batch:
        print("No batch returned; stopping.")
        break

    all_records.extend(batch)

    if len(batch) < ROWS:
        print("Last page reached.")
        break

    OS += ROWS
    time.sleep(0.25)

jsonl_path = OUTDIR / "worldbank_projects_raw.jsonl"
with jsonl_path.open("w", encoding="utf-8") as f:
    for rec in all_records:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

df = pd.json_normalize(all_records)
df.to_csv(OUTDIR / "worldbank_projects_flat.csv", index=False)

(OUTDIR / "api_query_log.json").write_text(json.dumps(query_log, indent=2), encoding="utf-8")

metadata = {
    "source_id": "worldbank_projects_api",
    "source_name": "World Bank Projects & Operations API",
    "url": BASE,
    "accessed_date": date.today().isoformat(),
    "rows_requested_per_page": ROWS,
    "n_records_raw": len(all_records),
    "n_columns_flat": len(df.columns),
    "raw_policy": "downloaded source API responses; do not edit in raw",
}
(OUTDIR / "source_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

print(f"Wrote {len(all_records)} records to {OUTDIR}")
print("Columns:")
for c in df.columns:
    print(" -", c)
