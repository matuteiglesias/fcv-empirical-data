#!/usr/bin/env python3
import json
from pathlib import Path
from datetime import date
import pandas as pd

pulls = sorted(Path("raw/investments/canonical_worldbank").glob("worldbank_projects_api_pull_*"))
if not pulls:
    raise SystemExit("No World Bank pull folder found.")

INDIR = pulls[-1]
pages = sorted(INDIR.glob("page_os_*.json"))

print(f"Input dir: {INDIR}")
print(f"Pages found: {len(pages)}")

def extract_projects(payload):
    if isinstance(payload, dict):
        for key in ["projects", "project"]:
            if key in payload and isinstance(payload[key], dict):
                return list(payload[key].values())
    return []

records = []
page_counts = []

for p in pages:
    payload = json.loads(p.read_text(encoding="utf-8"))
    batch = extract_projects(payload)
    page_counts.append({"page_file": p.name, "n_records": len(batch)})
    records.extend(batch)

if not records:
    raise SystemExit("No records extracted from partial pages.")

df = pd.json_normalize(records)

jsonl_path = INDIR / "worldbank_projects_partial_raw.jsonl"
with jsonl_path.open("w", encoding="utf-8") as f:
    for rec in records:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

df.to_csv(INDIR / "worldbank_projects_partial_flat.csv", index=False)
pd.DataFrame(page_counts).to_csv(INDIR / "partial_page_counts.csv", index=False)

print(f"Records: {len(records):,}")
print(f"Columns: {len(df.columns):,}")
print(f"Wrote: {INDIR / 'worldbank_projects_partial_flat.csv'}")
print("First columns:")
for c in list(df.columns)[:80]:
    print(" -", c)
