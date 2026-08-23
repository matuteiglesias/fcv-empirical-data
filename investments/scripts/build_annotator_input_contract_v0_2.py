#!/usr/bin/env python3
from pathlib import Path
import hashlib
import pandas as pd
import re

OUTDIR = Path("data/interim/annotation_contract_v0_2")
OUTDIR.mkdir(parents=True, exist_ok=True)

AIDDATA_RECORDS = Path("data/interim/aiddata_clg_lmic_relational_v1_0/tables/aiddata_records.csv")
WB_PROJECTS = Path("raw/investments/canonical_worldbank/worldbank_projects_api_pull_2026-06-28/worldbank_projects_flat.csv")

CANONICAL_COLUMNS = [
    "annotation_record_id",
    "source_family",
    "source_id",
    "source_project_id",
    "source_parent_id",
    "project_title",
    "project_description",
    "project_objective",
    "staff_notes",
    "country_name",
    "country_iso3",
    "region_name",
    "commitment_year",
    "approval_year",
    "implementation_start_year",
    "completion_year",
    "sector_code",
    "sector_name",
    "theme_or_intent",
    "infrastructure_flag",
    "finance_type",
    "finance_type_simplified",
    "flow_class",
    "amount_usd",
    "amount_usd_basis",
    "implementation_status",
    "recommended_for_aggregates",
    "umbrella_flag",
    "location_text",
    "has_location_text",
    "source_url",
    "all_source_urls",
    "source_quality_score",
    "data_completeness_score",
    "implementation_detail_score",
    "needs_text_enrichment",
    "annotation_universe_flag",
    "text_bundle_for_annotation",
    "mapping_notes",
]

def stable_id(*parts):
    raw = "||".join("" if x is None else str(x) for x in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

def get(df, col):
    if col and col in df.columns:
        return df[col]
    return pd.Series([pd.NA] * len(df), index=df.index)

def literal(value, n, index):
    return pd.Series([value] * n, index=index)

def nonempty_bool(s):
    return (s.notna() & (s.astype(str).str.strip() != "")).map({True: "true", False: "false"})

def year_from(s):
    return s.astype(str).str.extract(r"((?:19|20)\d{2})")[0]

def number_from(s):
    return s.astype(str).str.replace(",", "", regex=False).str.extract(r"([-+]?\d*\.?\d+)")[0]

def clean_text_value(x):
    if pd.isna(x):
        return ""
    x = str(x).strip()
    if x.lower() in {"nan", "none", "<na>"}:
        return ""
    return re.sub(r"\s+", " ", x)

def text_bundle(row):
    fields = [
        ("Title", "project_title"),
        ("Description", "project_description"),
        ("Objective", "project_objective"),
        ("Staff notes", "staff_notes"),
        ("Country", "country_name"),
        ("Region", "region_name"),
        ("Sector", "sector_name"),
        ("Sector code", "sector_code"),
        ("Theme/intent", "theme_or_intent"),
        ("Infrastructure", "infrastructure_flag"),
        ("Finance type", "finance_type"),
        ("Flow class", "flow_class"),
        ("Status", "implementation_status"),
        ("Location", "location_text"),
    ]
    parts = []
    for label, col in fields:
        val = clean_text_value(row.get(col))
        if val:
            parts.append(f"{label}: {val}")
    return "\n".join(parts)[:7000]

def finalize(out):
    for c in CANONICAL_COLUMNS:
        if c not in out.columns:
            out[c] = pd.NA

    out["text_bundle_for_annotation"] = out.apply(text_bundle, axis=1)

    return out[CANONICAL_COLUMNS]

def build_aiddata():
    df = pd.read_csv(AIDDATA_RECORDS, dtype=str, low_memory=False)
    out = pd.DataFrame(index=df.index)

    out["source_family"] = "china"
    out["source_id"] = "aiddata_china_clg_lmic_v1_0"
    out["source_project_id"] = get(df, "aiddata_record_id")
    out["source_parent_id"] = get(df, "parent_id")

    out["project_title"] = get(df, "title")
    out["project_description"] = get(df, "narrative_description")
    out["project_objective"] = pd.NA
    out["staff_notes"] = get(df, "staff_comments")

    out["country_name"] = get(df, "country_of_activity")
    out["country_iso3"] = get(df, "country_of_activity_iso3")
    out["region_name"] = get(df, "region_of_activity")

    out["commitment_year"] = get(df, "commitment_year")
    out["approval_year"] = pd.NA
    out["implementation_start_year"] = get(df, "implementation_start_year")
    out["completion_year"] = get(df, "completion_year")

    out["sector_code"] = get(df, "sector_code")
    out["sector_name"] = get(df, "sector_name")
    out["theme_or_intent"] = get(df, "intent")
    out["infrastructure_flag"] = get(df, "infrastructure")

    out["finance_type"] = get(df, "flow_type")
    out["finance_type_simplified"] = get(df, "flow_type_simplified")
    out["flow_class"] = get(df, "flow_class")
    out["amount_usd"] = number_from(get(df, "amount_constant_usd_2023"))
    out["amount_usd_basis"] = "constant_2023_usd"

    out["implementation_status"] = get(df, "status")
    out["recommended_for_aggregates"] = get(df, "recommended_for_aggregates")
    out["umbrella_flag"] = get(df, "umbrella")

    out["location_text"] = get(df, "location_narrative")
    out["has_location_text"] = nonempty_bool(out["location_text"])

    out["source_url"] = get(df, "original_agreement_url")
    out["all_source_urls"] = get(df, "all_source_urls")
    out["source_quality_score"] = get(df, "source_quality_score")
    out["data_completeness_score"] = get(df, "data_completeness_score")
    out["implementation_detail_score"] = get(df, "implementation_detail_score")

    title_ok = out["project_title"].notna() & (out["project_title"].astype(str).str.strip() != "")
    desc_ok = out["project_description"].notna() & (out["project_description"].astype(str).str.len() >= 80)
    out["needs_text_enrichment"] = (~desc_ok).map({True: "true", False: "false"})

    # Conservative: this is about whether it can enter annotation, not final treatment.
    out["annotation_universe_flag"] = (title_ok & desc_ok).map({True: "true", False: "false"})

    out["annotation_record_id"] = [
        stable_id("china", "aiddata_china_clg_lmic_v1_0", pid)
        for pid in out["source_project_id"]
    ]

    out["mapping_notes"] = "aiddata_records_project_activity_level"

    return finalize(out)

def build_worldbank():
    df = pd.read_csv(WB_PROJECTS, dtype=str, low_memory=False)
    out = pd.DataFrame(index=df.index)

    out["source_family"] = "worldbank"
    out["source_id"] = "worldbank_projects_api"
    out["source_project_id"] = get(df, "id")
    out["source_parent_id"] = pd.NA

    out["project_title"] = get(df, "project_name")
    out["project_description"] = pd.NA
    out["project_objective"] = pd.NA
    out["staff_notes"] = pd.NA

    out["country_name"] = get(df, "countryshortname")
    out["country_iso3"] = get(df, "countrycode")
    out["region_name"] = get(df, "regionname")

    out["commitment_year"] = pd.NA
    out["approval_year"] = year_from(get(df, "approvalfy"))
    out["implementation_start_year"] = year_from(get(df, "boardapprovaldate"))
    out["completion_year"] = year_from(get(df, "closingdate"))

    # Use sector1.Name first; fallback to sector if missing.
    sector1 = get(df, "sector1.Name")
    sector_fallback = get(df, "sector")
    out["sector_name"] = sector1.where(sector1.notna() & (sector1.astype(str).str.strip() != ""), sector_fallback)

    out["sector_code"] = get(df, "sectorcode")

    theme = get(df, "theme_list")
    theme1 = get(df, "theme1")
    theme_namecode = get(df, "theme_namecode")
    out["theme_or_intent"] = theme.where(theme.notna() & (theme.astype(str).str.strip() != ""), theme1)
    out["theme_or_intent"] = out["theme_or_intent"].where(
        out["theme_or_intent"].notna() & (out["theme_or_intent"].astype(str).str.strip() != ""),
        theme_namecode
    )

    out["infrastructure_flag"] = pd.NA

    out["finance_type"] = get(df, "prodlinetext")
    out["finance_type_simplified"] = get(df, "lendinginstr")
    out["flow_class"] = get(df, "source")
    out["amount_usd"] = number_from(get(df, "totalcommamt"))
    out["amount_usd_basis"] = "source_reported_total_commitment"

    out["implementation_status"] = get(df, "projectstatusdisplay")
    out["recommended_for_aggregates"] = pd.NA
    out["umbrella_flag"] = get(df, "supplementprojectflg")

    out["location_text"] = pd.NA
    out["has_location_text"] = "false"

    out["source_url"] = get(df, "url")
    out["all_source_urls"] = get(df, "projectdocs")
    out["source_quality_score"] = pd.NA
    out["data_completeness_score"] = pd.NA
    out["implementation_detail_score"] = pd.NA

    title_ok = out["project_title"].notna() & (out["project_title"].astype(str).str.strip() != "")
    out["needs_text_enrichment"] = "true"

    # WB can enter review, but should be marked weak until text enrichment.
    out["annotation_universe_flag"] = title_ok.map({True: "true_weak_text", False: "false"})

    out["annotation_record_id"] = [
        stable_id("worldbank", "worldbank_projects_api", pid)
        for pid in out["source_project_id"]
    ]

    out["mapping_notes"] = "worldbank_flat_api_project_level_needs_text_enrichment"

    return finalize(out)

def coverage_report(df):
    rows = []
    for (source_family, source_id), g in df.groupby(["source_family", "source_id"], dropna=False):
        row = {"source_family": source_family, "source_id": source_id, "n_rows": len(g)}
        for c in CANONICAL_COLUMNS:
            if c in ["text_bundle_for_annotation"]:
                nonmissing = g[c].notna() & (g[c].astype(str).str.len() > 30)
            else:
                nonmissing = g[c].notna() & (g[c].astype(str).str.strip() != "")
            row[c + "_coverage"] = round(float(nonmissing.mean()), 4)
        rows.append(row)
    return pd.DataFrame(rows)

def main():
    outputs = []

    if AIDDATA_RECORDS.exists():
        outputs.append(build_aiddata())
    else:
        print(f"WARNING missing AidData records: {AIDDATA_RECORDS}")

    if WB_PROJECTS.exists():
        outputs.append(build_worldbank())
    else:
        print(f"WARNING missing WB projects: {WB_PROJECTS}")

    if not outputs:
        raise SystemExit("No source inputs found.")

    all_df = pd.concat(outputs, ignore_index=True)

    all_df.to_csv(OUTDIR / "annotation_input_candidates_v0_2.csv", index=False)
    coverage_report(all_df).to_csv(OUTDIR / "annotation_input_coverage_v0_2.csv", index=False)

    dupes = (
        all_df.groupby(["source_family", "source_id", "source_project_id"], dropna=False)
        .size()
        .reset_index(name="n")
        .query("n > 1")
    )
    dupes.to_csv(OUTDIR / "duplicate_source_project_ids_v0_2.csv", index=False)

    sample = (
        all_df
        .groupby("source_family", group_keys=False)
        .apply(lambda g: g.sample(n=min(100, len(g)), random_state=42))
    )
    sample.to_csv(OUTDIR / "annotation_review_sample_v0_2.csv", index=False)

    print("Rows by source:")
    print(all_df.groupby(["source_family", "source_id"]).size().reset_index(name="n").to_string(index=False))
    print()
    print("Duplicates:")
    print(dupes.head(20).to_string(index=False))
    print()
    print(f"Wrote: {OUTDIR}")

if __name__ == "__main__":
    main()
