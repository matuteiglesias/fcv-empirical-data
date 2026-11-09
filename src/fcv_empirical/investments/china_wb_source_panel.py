"""Common AidData Chinese and World Bank source-measurement panel.

Preserves the two independently commissioned project-count measurements.
Not a merged financing account, an intervention-treatment table, or an
assertion that a source-zero is a truly investment-free geographic area.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pandas as pd
from empirical_contracts import (
    AuthorityLevel,
    CoverageContract,
    DataLayer,
    DatasetRef,
    GrainSpec,
    QAResult,
)
from spatial_foundation import DataRoot, PeriodIndex

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact

WB_POSITIVE = "aggregated_from_resolved_source_geocodes"
WB_ZERO = "zero_among_resolved_eligible_source_geocodes"
CN_POSITIVE = "aggregated_from_observed"
CN_ZERO = "structural_zero"


@dataclass(frozen=True)
class CommonAidDataPanel:
    panel: pd.DataFrame
    roster: pd.DataFrame
    coverage_countries: tuple[str, ...]
    period_ids: tuple[str, ...]
    support_counts: dict[str, int]
    qa: tuple[QAResult, ...]


@dataclass(frozen=True)
class CommonAidDataPanelPublication:
    panel: CommonAidDataPanel
    datasets: dict[str, DatasetRef]
    paths: dict[str, Path]
    run_manifest: object


def _assert_area_frame(frame: pd.DataFrame, label: str) -> None:
    cols = {
        "geo_uid",
        "period_id",
        "country_iso3",
        "project_count",
        "record_present",
        "measurement_status",
    }
    if missing := cols - set(frame.columns):
        raise ValueError(f"{label}: missing required source Gold columns {sorted(missing)}")
    if frame[["geo_uid", "period_id", "country_iso3"]].isna().any().any():
        raise ValueError(f"{label}: missing area-period or country key")
    if frame.duplicated(["geo_uid", "period_id"]).any():
        raise ValueError(f"{label}: duplicated area-period Gold grain")
    if frame.project_count.isna().any() or (frame.project_count < 0).any():
        raise ValueError(f"{label}: project_count must be observed and nonnegative")
    if not (frame.record_present.astype(bool) == frame.project_count.gt(0)).all():
        raise ValueError(f"{label}: reported presence contradicts source project count")
    positive, zero = (WB_POSITIVE, WB_ZERO) if label == "WB" else (CN_POSITIVE, CN_ZERO)
    expected = frame.project_count.gt(0).map({True: positive, False: zero})
    if not frame.measurement_status.eq(expected).all():
        raise ValueError(f"{label}: source Gold status contradicts actual source count")


def _cn_roster(
    geography_relation: pd.DataFrame,
    period_relation: pd.DataFrame,
    covered_countries: set[str],
    covered_geo_uids: set[str],
    period_ids: set[str],
) -> pd.DataFrame:
    req_geo = {
        "project_geometry_row_id",
        "geo_uid",
        "relation_status",
        "relation_method",
        "source_project_id",
        "recipient_iso3",
    }
    req_period = {
        "project_geometry_row_id",
        "project_date_type",
        "period_id",
        "period_assignment_status",
    }
    if missing := req_geo - set(geography_relation):
        raise ValueError(f"China geographic relation missing {sorted(missing)}")
    if missing := req_period - set(period_relation):
        raise ValueError(f"China temporal relation missing {sorted(missing)}")
    point = geography_relation.relation_method.eq(
        "point_intersects"
    ) & geography_relation.relation_status.eq("matched_unique")
    areal = geography_relation.relation_method.eq(
        "positive_area_overlap"
    ) & geography_relation.relation_status.isin(["matched_single", "matched_multiple"])
    geo = geography_relation.loc[
        (point | areal)
        & geography_relation.geo_uid.isin(covered_geo_uids)
        & geography_relation.recipient_iso3.isin(covered_countries),
        [
            "project_geometry_row_id",
            "source_project_id",
            "recipient_iso3",
            "geo_uid",
            "relation_status",
            "relation_method",
        ],
    ].copy()
    timing = period_relation.loc[
        period_relation.project_date_type.eq("commitment")
        & period_relation.period_assignment_status.eq("assigned")
        & period_relation.period_id.isin(period_ids),
        ["project_geometry_row_id", "period_id"],
    ]
    if timing.project_geometry_row_id.duplicated().any():
        raise ValueError("China commitment period is not unique per source geometry row")
    roster = geo.merge(timing, on="project_geometry_row_id", validate="many_to_one")
    if (
        roster[["source_project_id", "project_geometry_row_id", "geo_uid", "period_id"]]
        .isna()
        .any()
        .any()
    ):
        raise ValueError("China roster contains missing source identity or area-period")
    roster["source_family"] = "china"
    roster["source_project_id"] = roster.source_project_id.astype("string")
    roster["source_location_id"] = roster.project_geometry_row_id.astype("string")
    roster["geographic_confidence"] = "source_geometry_original"
    roster["event_type"] = "commitment"
    roster = roster.rename(
        columns={
            "relation_status": "geography_relation_status",
            "recipient_iso3": "source_recipient_iso3",
        }
    )
    return roster[
        [
            "source_family",
            "source_project_id",
            "source_location_id",
            "source_recipient_iso3",
            "geo_uid",
            "period_id",
            "geography_relation_status",
            "geographic_confidence",
            "event_type",
        ]
    ]


def _wb_roster(
    source_roster: pd.DataFrame,
    period_ids: set[str],
    covered_geo_uids: set[str],
    geo_countries: pd.DataFrame,
) -> pd.DataFrame:
    required = {
        "project_id",
        "project_location_id",
        "geo_uid",
        "period_id",
        "relation_status",
        "geographic_confidence",
    }
    if missing := required - set(source_roster):
        raise ValueError(f"World Bank roster missing {sorted(missing)}")
    roster = source_roster.loc[
        source_roster.geo_uid.isin(covered_geo_uids) & source_roster.period_id.isin(period_ids),
        [
            "project_id",
            "project_location_id",
            "geo_uid",
            "period_id",
            "relation_status",
            "geographic_confidence",
        ],
    ].copy()
    roster = roster.merge(geo_countries, on="geo_uid", validate="many_to_one")
    roster = roster.rename(
        columns={
            "project_id": "source_project_id",
            "project_location_id": "source_location_id",
            "relation_status": "geography_relation_status",
            "country_iso3": "source_recipient_iso3",
        }
    )
    roster["source_family"] = "world_bank"
    roster["event_type"] = "board_approval"
    return roster[
        [
            "source_family",
            "source_project_id",
            "source_location_id",
            "source_recipient_iso3",
            "geo_uid",
            "period_id",
            "geography_relation_status",
            "geographic_confidence",
            "event_type",
        ]
    ]


def build_aiddata_china_wb_panel(
    *,
    wb_gold: pd.DataFrame,
    cn_gold: pd.DataFrame,
    wb_project_roster: pd.DataFrame,
    cn_project_geography: pd.DataFrame,
    cn_project_period: pd.DataFrame,
    geography_units: pd.DataFrame,
    target_period_ids: tuple[str, ...],
) -> CommonAidDataPanel:
    """Combine only *certified source-covered* area-periods, with exact roster parity.

    Inclusion is based on the published CN country/period support; WB must
    independently cover every requested area-period. No unknown→zero fill.
    """
    _assert_area_frame(wb_gold, "WB")
    _assert_area_frame(cn_gold, "CN")
    if not target_period_ids or len(set(target_period_ids)) != len(target_period_ids):
        raise ValueError("target periods must be nonempty and unique")
    if geography_units.geo_uid.isna().any() or geography_units.geo_uid.duplicated().any():
        raise ValueError("GADM geography keys must be unique")

    common_countries = set(cn_gold.country_iso3.astype(str).unique()).intersection(
        set(wb_gold.country_iso3.astype(str).unique())
    )
    if common_countries != set(cn_gold.country_iso3.astype(str).unique()):
        raise ValueError("World Bank source Gold lacks CN-covered country support")
    target_geo = geography_units.loc[
        geography_units.country_iso3.isin(common_countries),
        ["geo_uid", "country_iso3"],
    ].copy()
    if not set(target_geo.geo_uid).issubset(set(wb_gold.geo_uid)) or not set(
        target_geo.geo_uid
    ).issubset(set(cn_gold.geo_uid)):
        raise ValueError("source Gold does not cover all target GADM geo_uids")
    period_ids = set(target_period_ids)
    grid = (
        target_geo.assign(_join=1)
        .merge(pd.DataFrame({"period_id": target_period_ids, "_join": 1}), on="_join")
        .drop(columns="_join")
    )
    wb = wb_gold.loc[
        wb_gold.period_id.isin(period_ids),
        [
            "geo_uid",
            "period_id",
            "country_iso3",
            "project_count",
            "high_confidence_project_count",
            "proxy_only_project_count",
            "record_present",
            "measurement_status",
        ],
    ].copy()
    cn = cn_gold.loc[
        cn_gold.period_id.isin(period_ids),
        [
            "geo_uid",
            "period_id",
            "country_iso3",
            "project_count",
            "positive_reported_amount_project_count",
            "reported_amount_known_project_count",
            "record_present",
            "measurement_status",
        ],
    ].copy()
    wb = wb.rename(columns={col: f"wb_{col}" for col in wb if col not in ("geo_uid", "period_id")})
    cn = cn.rename(columns={col: f"cn_{col}" for col in cn if col not in ("geo_uid", "period_id")})
    result = grid.merge(wb, on=["geo_uid", "period_id"], how="left", validate="one_to_one").merge(
        cn, on=["geo_uid", "period_id"], how="left", validate="one_to_one"
    )
    if len(result) != len(grid):
        raise ValueError("source Gold join changed declared common area-period grain")
    if (
        result[
            [
                "wb_project_count",
                "cn_project_count",
                "wb_measurement_status",
                "cn_measurement_status",
            ]
        ]
        .isna()
        .any()
        .any()
    ):
        raise ValueError(
            "incomplete source coverage: cannot invent a zero or discard an area-period"
        )
    if (
        result.country_iso3.ne(result.wb_country_iso3)
        | result.country_iso3.ne(result.cn_country_iso3)
    ).any():
        raise ValueError("source Gold country identity conflicts with canonical GADM")
    result = result.drop(columns=["wb_country_iso3", "cn_country_iso3"])
    if (result.wb_high_confidence_project_count > result.wb_project_count).any():
        raise ValueError("WB high-confidence count exceeds broad count")
    if (
        not result.wb_proxy_only_project_count.add(result.wb_high_confidence_project_count)
        .eq(result.wb_project_count)
        .all()
    ):
        raise ValueError("WB strict/proxy source Gold accounting does not balance")
    if (result.cn_positive_reported_amount_project_count > result.cn_project_count).any():
        raise ValueError("China positive reported amount projects exceed project count")
    if (result.cn_reported_amount_known_project_count > result.cn_project_count).any():
        raise ValueError("China known amount projects exceed project count")

    accepted_geo = set(target_geo.geo_uid)
    wb_roster = _wb_roster(wb_project_roster, period_ids, accepted_geo, target_geo)
    cn_roster = _cn_roster(
        cn_project_geography, cn_project_period, common_countries, accepted_geo, period_ids
    )
    roster = pd.concat([wb_roster, cn_roster], ignore_index=True)
    if roster.duplicated(["source_family", "source_location_id", "geo_uid", "period_id"]).any():
        raise ValueError("duplicate source location / area / period roster row")
    counts = roster.groupby(["source_family", "geo_uid", "period_id"]).source_project_id.nunique()
    for source_family, target_col in (
        ("world_bank", "wb_project_count"),
        ("china", "cn_project_count"),
    ):
        expected = counts.xs(source_family, level="source_family").rename("roster_count")
        verified = result.join(expected, on=["geo_uid", "period_id"])
        verified["roster_count"] = verified.roster_count.fillna(0).astype(int)
        failed = verified[verified.roster_count.ne(verified[target_col])]
        if len(failed):
            raise ValueError(
                f"{source_family}: {len(failed)} source Gold cells disagree with reconstructed "
                "project roster; verify recipient-country/time/geographic eligibility"
            )

    both = result.wb_project_count.gt(0) & result.cn_project_count.gt(0)
    wb_only = result.wb_project_count.gt(0) & result.cn_project_count.eq(0)
    cn_only = result.wb_project_count.eq(0) & result.cn_project_count.gt(0)
    neither = result.wb_project_count.eq(0) & result.cn_project_count.eq(0)
    groups = {
        "recorded_both": int(both.sum()),
        "recorded_wb_only": int(wb_only.sum()),
        "recorded_china_only": int(cn_only.sum()),
        "recorded_neither": int(neither.sum()),
    }
    if sum(groups.values()) != len(result):
        raise AssertionError("source-defined descriptive presence partition is incomplete")
    result = result.sort_values(["geo_uid", "period_id"]).reset_index(drop=True)
    roster = roster.sort_values(
        ["source_family", "source_project_id", "geo_uid", "period_id", "source_location_id"]
    ).reset_index(drop=True)
    qa = (
        QAResult(
            check_id="aiddata_china_wb.common_geography_time",
            state="GREEN",
            message="Common area-period join is complete, one-to-one, and country-consistent",
            metrics={
                "areas": len(target_geo),
                "periods": len(period_ids),
                "area_periods": len(result),
                "countries": len(common_countries),
            },
        ),
        QAResult(
            check_id="aiddata_china_wb.project_roster_parity",
            state="GREEN",
            message="Both source Gold project counts reproduced exactly from eligible project-level rosters",
            metrics={
                "source_roster_rows": len(roster),
                "worldbank_roster_rows": len(wb_roster),
                "china_roster_rows": len(cn_roster),
                "mismatched_gold_counts": 0,
            },
        ),
        QAResult(
            check_id="aiddata_china_wb.source_qualified_zero",
            state="YELLOW",
            message="Neither source's observed zero proves absence of investment; incomplete source geocoding is retained in upstream coverage contracts",
            metrics=groups,
        ),
    )
    return CommonAidDataPanel(
        panel=result,
        roster=roster,
        coverage_countries=tuple(sorted(common_countries)),
        period_ids=target_period_ids,
        support_counts=groups,
        qa=qa,
    )


def materialize_aiddata_china_wb_panel(
    *,
    wb_gold_ref: DatasetRef,
    wb_roster_ref: DatasetRef,
    cn_gold_ref: DatasetRef,
    cn_geo_ref: DatasetRef,
    cn_period_ref: DatasetRef,
    gadm_ref: DatasetRef,
    wb_gold: pd.DataFrame,
    cn_gold: pd.DataFrame,
    wb_project_roster: pd.DataFrame,
    cn_project_geography: pd.DataFrame,
    cn_project_period: pd.DataFrame,
    geography_units: pd.DataFrame,
    target_period_ids: tuple[str, ...],
    data_root: DataRoot,
    run_id: str,
    code_commit: str | None = None,
) -> CommonAidDataPanelPublication:
    """Versioned source-fusion artifact from two independently accepted Golds."""
    g = gadm_ref.geography
    p = wb_gold_ref.period_scheme
    if g is None or p is None or wb_gold_ref.geography != g or cn_gold_ref.geography != g:
        raise ValueError("WB, China and GADM must use identical GeographySpec")
    if cn_gold_ref.period_scheme != p or cn_period_ref.period_scheme != p:
        raise ValueError("WB and China must use exactly the same PeriodScheme")
    if wb_roster_ref.geography != g or cn_geo_ref.geography != g:
        raise ValueError("project-roster relations must use exact accepted GADM geography")
    source_refs = (wb_gold_ref, wb_roster_ref, cn_gold_ref, cn_geo_ref, cn_period_ref, gadm_ref)
    if any(x.content_sha256 is None for x in source_refs):
        raise ValueError("all input DatasetRefs need verified output hashes")
    measured = build_aiddata_china_wb_panel(
        wb_gold=wb_gold,
        cn_gold=cn_gold,
        wb_project_roster=wb_project_roster,
        cn_project_geography=cn_project_geography,
        cn_project_period=cn_project_period,
        geography_units=geography_units,
        target_period_ids=target_period_ids,
    )
    version_digest = hashlib.sha256(
        json.dumps(
            {
                "inputs": [x.content_sha256 for x in source_refs],
                "periods": target_period_ids,
                "semantics": "aiddata-source-counts-v1",
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]
    version = f"aiddata-cn-wb-source-v1-{version_digest}"
    refs = {
        "panel": DatasetRef(
            dataset_id="investments.aiddata.china_wb.source_area_period",
            version=version,
            schema_version="aiddata-cn-wb-source-panel-v1",
            layer=DataLayer.GOLD,
            authority=AuthorityLevel.L3_REBUILT,
            grain=GrainSpec(keys=("geo_uid", "period_id")),
            geography=g,
            period_scheme=p,
        ),
        "roster": DatasetRef(
            dataset_id="investments.aiddata.china_wb.source_project_roster",
            version=version,
            schema_version="aiddata-cn-wb-project-roster-v1",
            layer=DataLayer.GOLD,
            authority=AuthorityLevel.L3_REBUILT,
            grain=GrainSpec(keys=("source_family", "source_location_id", "geo_uid", "period_id")),
            geography=g,
            period_scheme=p,
        ),
    }
    destination = data_root.gold("investments", "aiddata_china_wb_source_panel", version)
    artifacts = {
        "panel": (measured.panel, "source_area_period.parquet"),
        "roster": (measured.roster, "source_project_roster.parquet"),
    }
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        inputs=source_refs,
        outputs=tuple(
            FileMaterialization(
                dataset=refs[k],
                destination_base=destination,
                relative_path=name,
                writer=lambda path, frame=frame: frame.to_parquet(path, index=False),
            )
            for k, (frame, name) in artifacts.items()
        ),
        parameters={
            "geography": g.model_dump(mode="json"),
            "period_scheme": p.model_dump(mode="json"),
            "target_periods": list(target_period_ids),
            "worldbank_event_type": "board_approval",
            "china_event_type": "commitment",
            "source_zero_semantics": "zero_eligible_resolved_source_records_only",
            "financial_amount_pooling": False,
            "research_treatment_classification": None,
        },
        qa=measured.qa,
        code_commit=code_commit,
    )
    periods = [PeriodIndex(p).period_for(int(x.split("-")[0])) for x in target_period_ids]
    coverage = CoverageContract(
        geography_scope=f"{g.id}; covered countries " + ",".join(measured.coverage_countries),
        temporal_start=min(x.start_date for x in periods),
        temporal_end=max(x.end_date_exclusive - timedelta(days=1) for x in periods),
        observation_semantics="dense source-qualified Chinese commitments and World Bank approvals per ADM2/biennium; source project count, not monetary amount or actual project implementation",
        absent_row_semantics="not_observed",
        authority=AuthorityLevel.L3_REBUILT,
        basis="Intersection of individually accepted AidData GeoGCDF v3.0.1 and World Bank v1.4.2 source measurements, exact GADM4.1 geography and T2_y2001 periods. Explicit source-zero is limited to eligible resolved geocoded source records; neither=0 does not imply absence of all financing.",
    )
    persist_run_artifact(
        data_root,
        run_id,
        "contracts/coverage.json",
        json.dumps(coverage.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
    )
    persist_run_artifact(
        data_root,
        run_id,
        "qa/source_support.json",
        json.dumps(
            {
                "coverage_countries": measured.coverage_countries,
                "periods": measured.period_ids,
                "support_counts": measured.support_counts,
                "gold_count_roster_parity": True,
                "no_finance_amount_pooling": True,
                "project_status_unknown_outside_published_support": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    hashed = {
        k: next(x for x in manifest.outputs if x.dataset_id == refs[k].dataset_id) for k in refs
    }
    return CommonAidDataPanelPublication(
        panel=measured,
        datasets=hashed,
        paths={k: destination / name for k, (_, name) in artifacts.items()},
        run_manifest=manifest,
    )
