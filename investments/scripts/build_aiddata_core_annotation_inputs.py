#!/usr/bin/env python3
from pathlib import Path
import hashlib
import re
import pandas as pd

IN = Path("data/interim/aiddata_clg_lmic_relational_v1_0/tables/aiddata_records.csv")
OUTDIR = Path("data/interim/annotation_core")
FLOW_INPUTS = Path("flows/annotation_v1/inputs")

OUTDIR.mkdir(parents=True, exist_ok=True)
FLOW_INPUTS.mkdir(parents=True, exist_ok=True)

RANDOM_SEED = 42
TEXT_BUNDLE_MAX_CHARS = 4500

def stable_id(*parts):
    raw = "||".join("" if x is None else str(x) for x in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

def col(df, name):
    if name in df.columns:
        return df[name]
    return pd.Series([pd.NA] * len(df), index=df.index)

def clean_text(x):
    if pd.isna(x):
        return ""
    x = str(x).strip()
    if x.lower() in {"nan", "none", "<na>"}:
        return ""
    return re.sub(r"\s+", " ", x)

def nonempty(s):
    return s.notna() & (s.astype(str).str.strip() != "")

def make_bundle(row):
    fields = [
        ("Title", "project_title"),
        ("Description", "project_description"),
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
    for label, key in fields:
        v = clean_text(row.get(key))
        if v:
            parts.append(f"{label}: {v}")
    return "\n".join(parts)[:TEXT_BUNDLE_MAX_CHARS]

def main():
    if not IN.exists():
        raise FileNotFoundError(f"Missing input: {IN}")

    df = pd.read_csv(IN, dtype=str, low_memory=False)

    out = pd.DataFrame()
    out["source_family"] = "china"
    out["source_id"] = "aiddata_china_clg_lmic_v1_0"
    out["source_project_id"] = col(df, "aiddata_record_id")
    out["source_parent_id"] = col(df, "parent_id")

    out["project_title"] = col(df, "title")
    out["project_description"] = col(df, "narrative_description")

    out["country_name"] = col(df, "country_of_activity")
    out["country_iso3"] = col(df, "country_of_activity_iso3")
    out["region_name"] = col(df, "region_of_activity")

    out["commitment_year"] = col(df, "commitment_year")

    out["sector_code"] = col(df, "sector_code")
    out["sector_name"] = col(df, "sector_name")
    out["theme_or_intent"] = col(df, "intent")
    out["infrastructure_flag"] = col(df, "infrastructure")

    out["finance_type"] = col(df, "flow_type")
    out["flow_class"] = col(df, "flow_class")
    out["implementation_status"] = col(df, "status")

    out["location_text"] = col(df, "location_narrative")
    out["source_url"] = col(df, "original_agreement_url")

    out["recommended_for_aggregates"] = col(df, "recommended_for_aggregates")
    out["umbrella_flag"] = col(df, "umbrella")

    out["annotation_record_id"] = [
        stable_id("china", "aiddata_china_clg_lmic_v1_0", x)
        for x in out["source_project_id"]
    ]

    title_ok = nonempty(out["project_title"])
    desc_ok = nonempty(out["project_description"])
    id_ok = nonempty(out["source_project_id"])

    out["annotation_universe_flag"] = (id_ok & title_ok & desc_ok).map({True: "true", False: "false"})

    out["text_bundle_for_annotation"] = out.apply(make_bundle, axis=1)

    core_cols = [
        "annotation_record_id",
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
        "sector_code",
        "sector_name",
        "theme_or_intent",
        "infrastructure_flag",
        "finance_type",
        "flow_class",
        "implementation_status",
        "location_text",
        "source_url",
        "recommended_for_aggregates",
        "umbrella_flag",
        "annotation_universe_flag",
        "text_bundle_for_annotation",
    ]

    out = out[core_cols].copy()

    full_csv = OUTDIR / "aiddata_annotator_input_core.csv"
    full_jsonl = OUTDIR / "aiddata_annotator_input_core.jsonl"

    out.to_csv(full_csv, index=False)
    out.to_json(full_jsonl, orient="records", lines=True, force_ascii=False)

    eligible = out[out["annotation_universe_flag"] == "true"].copy()

    for n in [3, 20, 100]:
        sample = eligible.sample(n=min(n, len(eligible)), random_state=RANDOM_SEED)

        csv_path = FLOW_INPUTS / f"aiddata_sample_{n}.csv"
        jsonl_path = FLOW_INPUTS / f"aiddata_sample_{n}.jsonl"

        sample.to_csv(csv_path, index=False)
        sample.to_json(jsonl_path, orient="records", lines=True, force_ascii=False)

    # default flow input = 3-row smoke test
    (FLOW_INPUTS / "aiddata_sample_3.jsonl").replace(FLOW_INPUTS / "data.jsonl")

    print("Full core input:", out.shape, full_csv)
    print("Eligible:", eligible.shape)
    print("Samples written:")
    for n in [3, 20, 100]:
        print(f" - flows/annotation_v1/inputs/aiddata_sample_{n}.csv/jsonl")
    print("Default flow input:")
    print(" - flows/annotation_v1/inputs/data.jsonl")

    print("\nCore coverage:")
    cov = []
    for c in core_cols:
        cov.append({
            "column": c,
            "coverage": round(float(nonempty(out[c]).mean()), 4)
        })
    print(pd.DataFrame(cov).to_string(index=False))

if __name__ == "__main__":
    main()
