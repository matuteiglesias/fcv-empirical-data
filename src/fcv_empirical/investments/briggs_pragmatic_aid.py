from __future__ import annotations

from dataclasses import dataclass, asdict
from io import BytesIO
from pathlib import Path
import re
import zipfile

import pandas as pd

from .briggs_historical import (
    AFDB_ARCHIVE,
    WORLD_BANK_ARCHIVE,
    BriggsHistoricalSourceError,
    preflight_briggs_historical_aid_sources,
)


class BriggsPragmaticAidError(ValueError):
    pass


@dataclass(frozen=True)
class BriggsPragmaticAidSummary:
    schema: str
    source_rows: dict[str, int]
    eligible_rows_before_pair_dedup: dict[str, int]
    eligible_parent_child_pairs: dict[str, int]
    parent_count: dict[str, int]
    amount_missing_pairs: dict[str, int]
    parent_amount_conflicts: dict[str, int]
    semantic_policy: dict[str, str]

    def to_dict(self) -> dict:
        return asdict(self)


def _number(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    cleaned = (
        series.astype("string")
        .str.replace(",", "", regex=False)
        .str.replace(r"[^0-9eE+\-.]", "", regex=True)
        .replace("", pd.NA)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def _year(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce")
    out = dt.dt.year.astype("Int64")
    numeric = pd.to_numeric(series, errors="coerce")
    direct = numeric.where(numeric.between(1900, 2100)).astype("Int64")
    return out.fillna(direct)


def _normalized_project_name(value: object) -> str | None:
    if pd.isna(value):
        return None
    text = re.sub(r"\s+", " ", str(value).strip()).casefold()
    return text or None


def _prepare(
    raw: pd.DataFrame,
    *,
    donor: str,
    parent: pd.Series,
    child_col: str,
    approval_col: str,
    precision_col: str,
    amount_col: str,
    latitude_col: str,
    longitude_col: str,
    source_adm1_col: str | None,
) -> tuple[pd.DataFrame, dict]:
    if child_col not in raw or approval_col not in raw or precision_col not in raw:
        raise BriggsPragmaticAidError(f"{donor}: expected historical columns are missing")

    frame = pd.DataFrame(
        {
            "donor": donor,
            "source_row_number": range(1, len(raw) + 1),
            "parent_id": parent,
            "child_id": raw[child_col].astype("string"),
            "approval_year": _year(raw[approval_col]),
            "precision": _number(raw[precision_col]),
            "latitude": _number(raw[latitude_col]),
            "longitude": _number(raw[longitude_col]),
            "parent_amount": _number(raw[amount_col]) if amount_col in raw else pd.NA,
            "source_adm1": raw[source_adm1_col].astype("string") if source_adm1_col and source_adm1_col in raw else pd.NA,
        }
    )
    frame["parent_id"] = frame["parent_id"].astype("string")
    frame["child_id"] = frame["child_id"].astype("string")

    eligible = frame[
        frame["approval_year"].isin([2009, 2010])
        & frame["precision"].lt(5)
        & frame["parent_id"].notna()
        & frame["child_id"].notna()
    ].copy()
    eligible = eligible.sort_values(["parent_id", "child_id", "source_row_number"])

    conflict_count = 0
    for _, group in eligible.groupby("parent_id", sort=False):
        values = group["parent_amount"].dropna().unique()
        if len(values) > 1:
            conflict_count += 1

    pairs = eligible.drop_duplicates(["parent_id", "child_id"], keep="first").copy()
    child_counts = pairs.groupby("parent_id")["child_id"].transform("size")
    pairs["allocated_parent_value"] = pairs["parent_amount"] / child_counts
    pairs["parent_child_identity_policy"] = (
        "source_project_id_plus_geonameid" if donor == "world_bank" else "normalized_project_name_plus_geonameid_fallback"
    )
    pairs["allocation_policy"] = "equal_across_all_eligible_parent_child_pairs_before_analysis_country_filter"

    stats = {
        "source_rows": int(len(raw)),
        "eligible_rows_before_pair_dedup": int(len(eligible)),
        "eligible_parent_child_pairs": int(len(pairs)),
        "parent_count": int(pairs["parent_id"].nunique()),
        "amount_missing_pairs": int(pairs["parent_amount"].isna().sum()),
        "parent_amount_conflicts": int(conflict_count),
    }
    return pairs.reset_index(drop=True), stats


def normalize_world_bank_pragmatic(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    required = {"Project ID", "GeoNameID", "Approval Date", "Precision", "Total Amt", "Latitude", "Longitude"}
    missing = required - set(raw.columns)
    if missing:
        raise BriggsPragmaticAidError(f"world_bank missing columns: {sorted(missing)}")
    return _prepare(
        raw,
        donor="world_bank",
        parent=raw["Project ID"],
        child_col="GeoNameID",
        approval_col="Approval Date",
        precision_col="Precision",
        amount_col="Total Amt",
        latitude_col="Latitude",
        longitude_col="Longitude",
        source_adm1_col="ADM1",
    )


def normalize_afdb_pragmatic(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    required = {"Project Name", "GeoNameID", "Board Approval", "Precision", "Project Cost", "Latitude", "Longitude"}
    missing = required - set(raw.columns)
    if missing:
        raise BriggsPragmaticAidError(f"afdb missing columns: {sorted(missing)}")
    parent = raw["Project Name"].map(_normalized_project_name)
    return _prepare(
        raw,
        donor="afdb",
        parent=parent,
        child_col="GeoNameID",
        approval_col="Board Approval",
        precision_col="Precision",
        amount_col="Project Cost",
        latitude_col="Latitude",
        longitude_col="Longitude",
        source_adm1_col="ADM 1 Name" if "ADM 1 Name" in raw else "ADM1",
    )


def _read_wb_archive(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as zf:
        members = [x for x in zf.namelist() if x.endswith("AllWorldBank_IBRDIDA.csv")]
        if len(members) != 1:
            raise BriggsHistoricalSourceError(f"expected one World Bank CSV member, got {members}")
        with zf.open(members[0]) as fh:
            return pd.read_csv(fh, low_memory=False)


def _read_afdb_archive(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as zf:
        members = [x for x in zf.namelist() if x.endswith("AfDB_2009_2010_AllApprovedProjects.xlsx")]
        if len(members) != 1:
            raise BriggsHistoricalSourceError(f"expected one AfDB XLSX member, got {members}")
        payload = BytesIO(zf.read(members[0]))
    return pd.read_excel(payload, sheet_name="AfDB_All_2009_2010")


def build_briggs_pragmatic_aid_locations(source_root: str | Path) -> tuple[pd.DataFrame, BriggsPragmaticAidSummary]:
    preflight = preflight_briggs_historical_aid_sources(source_root)
    wb_raw = _read_wb_archive(Path(preflight.world_bank.path))
    afdb_raw = _read_afdb_archive(Path(preflight.afdb.path))
    wb, wb_stats = normalize_world_bank_pragmatic(wb_raw)
    afdb, afdb_stats = normalize_afdb_pragmatic(afdb_raw)
    combined = pd.concat([wb, afdb], ignore_index=True)
    summary = BriggsPragmaticAidSummary(
        schema="briggs_pragmatic_aid_summary.v1",
        source_rows={"world_bank": wb_stats["source_rows"], "afdb": afdb_stats["source_rows"]},
        eligible_rows_before_pair_dedup={"world_bank": wb_stats["eligible_rows_before_pair_dedup"], "afdb": afdb_stats["eligible_rows_before_pair_dedup"]},
        eligible_parent_child_pairs={"world_bank": wb_stats["eligible_parent_child_pairs"], "afdb": afdb_stats["eligible_parent_child_pairs"]},
        parent_count={"world_bank": wb_stats["parent_count"], "afdb": afdb_stats["parent_count"]},
        amount_missing_pairs={"world_bank": wb_stats["amount_missing_pairs"], "afdb": afdb_stats["amount_missing_pairs"]},
        parent_amount_conflicts={"world_bank": wb_stats["parent_amount_conflicts"], "afdb": afdb_stats["parent_amount_conflicts"]},
        semantic_policy={
            "world_bank_parent": "Project ID",
            "afdb_parent": "normalized Project Name fallback; no explicit source project ID",
            "child": "GeoNameID",
            "eligibility": "approval year 2009/2010 and numeric precision < 5",
            "allocation": "one parent amount divided equally across all eligible unique parent-child pairs before GADM/country restriction",
            "world_bank_amount": "Total Amt",
            "afdb_amount": "Project Cost",
            "fidelity": "pragmatic analogue; semantics intentionally not tuned to Briggs oracle counts",
        },
    )
    return combined, summary
