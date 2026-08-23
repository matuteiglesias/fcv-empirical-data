#!/usr/bin/env python3
import json
import time
from pathlib import Path
from datetime import date
import requests
import pandas as pd

BASE = "https://search.worldbank.org/api/v2/projects"
ROWS = 100
MAX_OS = 20000
SLEEP = 0.3
MAX_RETRIES = 5

OUTDIR = Path("raw/investments/canonical_worldbank/worldbank_projects_api_pull_" + date.today().isoformat())
OUTDIR.mkdir(parents=True, exist_ok=True)

def extract_projects(payload):
    if isinstance(payload, dict):
        for key in ["projects", "project"]:
            if key in payload and isinstance(payload[key], dict):
                return list(payload[key].values())
        vals = []
        for v in payload.values():
            if isinstance(v, dict) and ("project_name" in v or "id" in v or "projectid" in v):
                vals.append(v)
        if vals:
            return vals
    return []

def fetch_page(os_value):
    params = {"format": "json", "rows": ROWS, "os": os_value}
    last_exc = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(BASE, params=params, timeout=60)
            if r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} server error", response=r)
            r.raise_for_status()
            return r.url, r.json(), None
        except Exception as e:
            last_exc = repr(e)
            wait = min(30, 2 ** attempt)
            print(f"  attempt {attempt}/{MAX_RETRIES} failed for os={os_value}: {last_exc}; sleeping {wait}s")
            time.sleep(wait)

    return None, None, last_exc

query_log = []
errors = []

for os_value in range(0, MAX_OS + 1, ROWS):
    raw_file = OUTDIR / f"page_os_{os_value:07d}.json"

    if raw_file.exists() and raw_file.stat().st_size > 0:
        print(f"Skipping existing os={os_value}")
        continue

    print(f"Fetching os={os_value} rows={ROWS}")
    url, payload, err = fetch_page(os_value)

    if err:
        print(f"ERROR os={os_value}: {err}")
        errors.append({"os": os_value, "rows": ROWS, "error": err})
        # Do not kill the whole run. Keep going a bit.
        if len(errors) >= 20:
            print("Too many errors; stopping.")
            break
        continue

    batch = extract_projects(payload)
    raw_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    query_log.append({"os": os_value, "rows": ROWS, "url": url, "n_records": len(batch)})

    if not batch:
        print("No records returned; stopping.")
        break

    if len(batch) < ROWS:
        print("Last page likely reached.")
        break

    time.sleep(SLEEP)

# Always assemble whatever exists.
pages = sorted(OUTDIR.glob("page_os_*.json"))
records = []
page_counts = []

for p in pages:
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        batch = extract_projects(payload)
    except Exception as e:
        batch = []
        errors.append({"file": str(p), "error": repr(e)})

    page_counts.append({"page_file": p.name, "n_records": len(batch)})
    records.extend(batch)

if records:
    with (OUTDIR / "worldbank_projects_raw.jsonl").open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    df = pd.json_normalize(records)
    df.to_csv(OUTDIR / "worldbank_projects_flat.csv", index=False)
    print(f"Assembled records: {len(records):,}")
    print(f"Wrote: {OUTDIR / 'worldbank_projects_flat.csv'}")
else:
    print("No records assembled.")

pd.DataFrame(page_counts).to_csv(OUTDIR / "page_counts.csv", index=False)
(OUTDIR / "api_query_log.json").write_text(json.dumps(query_log, indent=2), encoding="utf-8")
(OUTDIR / "api_errors.json").write_text(json.dumps(errors, indent=2), encoding="utf-8")

metadata = {
    "source_id": "worldbank_projects_api",
    "source_name": "World Bank Projects & Operations API",
    "url": BASE,
    "accessed_date": date.today().isoformat(),
    "rows_requested_per_page": ROWS,
    "max_os": MAX_OS,
    "n_pages_found": len(pages),
    "n_errors": len(errors),
    "raw_policy": "downloaded source API responses; do not edit in raw",
}
(OUTDIR / "source_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

print(f"Errors: {len(errors)}")
