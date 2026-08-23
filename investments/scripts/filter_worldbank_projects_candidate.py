#!/usr/bin/env python3
from pathlib import Path
import re
import pandas as pd

pulls = sorted(Path("raw/investments/canonical_worldbank").glob("worldbank_projects_api_pull_*"))
if not pulls:
    raise SystemExit("No World Bank pull folder found.")

INDIR = pulls[-1]
csv_path = INDIR / "worldbank_projects_flat.csv"
df = pd.read_csv(csv_path, dtype=str)

print(f"Input: {csv_path}")
print(f"Rows: {len(df):,}")
print("Columns:")
for c in df.columns:
    print(" -", c)

def first_existing(candidates):
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    # fuzzy contains fallback
    for cand in candidates:
        for c in df.columns:
            if cand.lower() in c.lower():
                return c
    return None

project_id_col = first_existing(["id", "projectid", "project_id"])
name_col = first_existing(["project_name", "projectname", "project_title", "name"])
region_col = first_existing(["regionname", "region_name", "region"])
country_col = first_existing(["countryname", "country_name", "countryshortname", "country"])
approvalfy_col = first_existing(["approvalfy", "approval_fy", "boardapprovaldate", "approvaldate"])
product_col = first_existing(["prodlinetext", "productline", "product_line", "lendinginstr", "lending_instrument"])
status_col = first_existing(["projectstatusdisplay", "project_status", "status"])

print("\nDetected columns:")
for label, col in {
    "project_id": project_id_col,
    "name": name_col,
    "region": region_col,
    "country": country_col,
    "approval/fy/date": approvalfy_col,
    "product/line/instrument": product_col,
    "status": status_col,
}.items():
    print(f" - {label}: {col}")

work = df.copy()

# Year extraction: supports FY2015, 2015, date strings, etc.
if approvalfy_col:
    years = work[approvalfy_col].astype(str).str.extract(r"((?:19|20)\d{2})")[0]
    work["_approval_year_guess"] = pd.to_numeric(years, errors="coerce")
else:
    work["_approval_year_guess"] = pd.NA

# Region filter. Keep broad enough for source inventory.
target_regions = [
    "Africa",
    "Eastern and Southern Africa",
    "Western and Central Africa",
    "East Asia and Pacific",
    "South Asia",
    "Middle East and North Africa",
]

if region_col:
    region_pat = "|".join(re.escape(x) for x in target_regions)
    m_region = work[region_col].astype(str).str.contains(region_pat, case=False, na=False)
else:
    m_region = True

m_year = work["_approval_year_guess"].between(2015, 2026, inclusive="both")

# Product-line filter: keep it loose; inspect output before treating as final.
if product_col:
    m_product = work[product_col].astype(str).str.contains("IBRD|IDA|Investment|Development Policy|Program-for-Results|PforR|IPF|DPF", case=False, na=False)
else:
    m_product = True

candidate = work[m_year & m_region & m_product].copy()

OUTDIR = Path("data/interim/worldbank_projects_candidates")
OUTDIR.mkdir(parents=True, exist_ok=True)

candidate.to_csv(OUTDIR / "worldbank_projects_2015_2026_regions_ibrd_ida_candidate.csv", index=False)

summary_cols = [c for c in [region_col, product_col, status_col] if c]
if summary_cols:
    summary = candidate.groupby(summary_cols, dropna=False).size().reset_index(name="n")
    summary.to_csv(OUTDIR / "worldbank_projects_candidate_summary.csv", index=False)

print(f"\nCandidate rows: {len(candidate):,}")
print(f"Wrote: {OUTDIR}")
