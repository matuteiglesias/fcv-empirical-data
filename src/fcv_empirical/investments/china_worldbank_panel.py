"""AidData China and WB project measurement fusion on exact common geography/time.

A source-comparable data product, NOT a treatment panel. In particular, zero
projects means zero *resolved source-geocoded* projects, not a control area
without actual foreign investment.
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
    GeographySpec,
    GrainSpec,
    PeriodScheme,
    QAResult,
    RunManifest,
)
from spatial_foundation import DataRoot, PeriodIndex

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact


@dataclass(frozen=True)
class ChinaWBPanel:
    panel: pd.DataFrame
    project_roster: pd.DataFrame
    country_period_support: pd.DataFrame
    qa: tuple[QAResult, ...]
    countries: tuple[str, ...]
    period_ids: tuple[str, ...]


@dataclass(frozen=True)
class ChinaWBPanelMaterialization:
    manifest: RunManifest
    coverage: CoverageContract
    datasets: dict[str, DatasetRef]
    paths: dict[str, Path]
    result: ChinaWBPanel


def _assert_gold(key: str, gold: pd.DataFrame, geography: pd.DataFrame) -> None:
    required = {"geo_uid", "country_iso3", "period_id", "project_count", "record_present"}
    if missing := (required - set(gold.columns)):
        raise ValueError(f"{key} Gold missing source fields {sorted(missing)}")
    if gold.duplicated(["geo_uid", "period_id"]).any():
        raise ValueError(f"{key} Gold has duplicate (geo_uid,period_id) keys")
    if not set(gold.geo_uid).issubset(set(geography.geo_uid)):
        raise ValueError(f"{key} Gold has unrecognized GADM geography IDs")
    lookup = geography.set_index("geo_uid").country_iso3
    if gold.country_iso3.ne(gold.geo_uid.map(lookup)).any():
        raise ValueError(f"{key} Gold country disagrees with accepted GADM geography")
    if gold.project_count.isna().any() or gold.project_count.lt(0).any():
        raise ValueError(f"{key} Gold contains missing or negative project counts")
    if gold.record_present.ne(gold.project_count.gt(0)).any():
        raise ValueError(f"{key} Gold record-present status does not match project counts")


def _cn_roster(
    cn_silver: pd.DataFrame,
    cn_geography_relation: pd.DataFrame,
    cn_period_relation: pd.DataFrame,
    countries: set[str],
    periods: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replicate the accepted GeoGCDF source resolver, including recipient ISO3.

    Filtering recipient countries BEFORE projecting GIS links is essential:
    source projects outside the Africa comparison set can have broad footprint
    polygons intersecting African administrative units. Their rows are not
    part of the accepted recipient-country GeoGCDF Gold.
    """
    required = {"project_geometry_row_id", "source_project_id", "recipient_iso3"}
    if missing := (required - set(cn_silver.columns)):
        raise ValueError(f"China source Silver missing fields: {sorted(missing)}")
    if cn_silver.project_geometry_row_id.duplicated().any():
        raise ValueError("China source geometry identity must be unique")
    eligible = cn_silver.loc[
        cn_silver.recipient_iso3.isin(countries),
        ["project_geometry_row_id", "source_project_id", "recipient_iso3"],
    ].copy()

    cg = cn_geography_relation.copy()
    resolved = (
        cg.relation_method.eq("point_intersects") & cg.relation_status.eq("matched_unique")
    ) | (
        cg.relation_method.eq("positive_area_overlap")
        & cg.relation_status.isin(["matched_single", "matched_multiple"])
    )
    cg = cg.loc[
        resolved & cg.geo_uid.notna(),
        [
            "project_geometry_row_id",
            "geo_uid",
            "relation_method",
            "relation_status",
            "source_project_id",
        ],
    ].copy()
    cg = cg.merge(
        eligible,
        on="project_geometry_row_id",
        validate="many_to_one",
        suffixes=("_relation", "_source"),
    )
    if cg.source_project_id_relation.ne(cg.source_project_id_source).any():
        raise ValueError("Chinese geography relation project ID differs from source Silver")
    cp = cn_period_relation.loc[
        cn_period_relation.project_date_type.eq("commitment")
        & cn_period_relation.period_assignment_status.eq("assigned")
        & cn_period_relation.period_id.isin(periods),
        ["project_geometry_row_id", "source_project_id", "period_id"],
    ].copy()
    joined = cg.merge(cp, on="project_geometry_row_id", validate="many_to_one")
    if joined.source_project_id_source.ne(joined.source_project_id).any():
        raise ValueError("Chinese commitment relation project ID differs from source Silver")

    unique_projects = joined.drop_duplicates(["source_project_id", "geo_uid", "period_id"])
    frame = unique_projects[
        [
            "source_project_id",
            "project_geometry_row_id",
            "geo_uid",
            "period_id",
            "relation_status",
            "relation_method",
        ]
    ].copy()
    frame["source_family"] = "aiddata_geogcdf"
    frame["location_row_id"] = frame.project_geometry_row_id
    frame["geographic_confidence"] = frame.relation_method.map(
        {
            "point_intersects": "point_geometric_match",
            "positive_area_overlap": "areal_positive_overlap",
        }
    )
    frame["finance_event_type"] = "commitment"
    frame = frame[
        [
            "source_family",
            "source_project_id",
            "location_row_id",
            "geo_uid",
            "period_id",
            "relation_status",
            "geographic_confidence",
            "finance_event_type",
        ]
    ]
    # Geographic incompleteness sidecar (part of accepted non-strict source Gold).
    resolved_ids = set(cg.project_geometry_row_id)
    not_resolved = eligible.loc[
        ~eligible.project_geometry_row_id.isin(resolved_ids),
        ["project_geometry_row_id", "recipient_iso3"],
    ]
    missing_periods = cn_period_relation.loc[
        cn_period_relation.project_date_type.eq("commitment")
        & cn_period_relation.period_id.isin(periods),
        ["project_geometry_row_id", "period_id"],
    ]
    missing = not_resolved.merge(
        missing_periods, on="project_geometry_row_id", validate="one_to_one"
    )
    summary = (
        missing.groupby(["recipient_iso3", "period_id"])
        .size()
        .rename("cn_country_period_unresolved_geography_projects")
        .reset_index()
        .rename(columns={"recipient_iso3": "country_iso3"})
    )
    return frame, summary


def build_china_worldbank_panel(
    cn_gold: pd.DataFrame,
    wb_gold: pd.DataFrame,
    wb_project_roster: pd.DataFrame,
    wb_country_period_support: pd.DataFrame,
    cn_silver: pd.DataFrame,
    cn_geography_relation: pd.DataFrame,
    cn_period_relation: pd.DataFrame,
    geography_units: pd.DataFrame,
    *,
    period_scheme: PeriodScheme,
    start_year: int = 2001,
    end_year: int = 2014,
) -> ChinaWBPanel:
    """Build a **source-only** common panel, with project-ID independent parity."""
    if geography_units.geo_uid.duplicated().any() or geography_units.geo_uid.isna().any():
        raise ValueError("GADM geo_uid must be unique and non-null")
    _assert_gold("China", cn_gold, geography_units)
    _assert_gold("WB", wb_gold, geography_units)

    both = tuple(sorted(set(cn_gold.country_iso3) & set(wb_gold.country_iso3)))
    if not both:
        raise ValueError("no common source-country support")
    index = PeriodIndex(period_scheme)
    periods = tuple(
        p
        for p in index.range(start_year, end_year)
        if p.start_year >= start_year and p.end_year <= end_year
    )
    period_ids = tuple(p.period_id for p in periods)
    if not period_ids:
        raise ValueError("no periods fully within requested common source window")
    areas = geography_units.loc[
        geography_units.country_iso3.isin(both),
        ["geo_uid", "country_iso3"],
    ]
    universe = (
        areas.assign(_join=1)
        .merge(pd.DataFrame({"period_id": period_ids, "_join": 1}), on="_join")
        .drop(columns="_join")
    )

    keys = ["geo_uid", "period_id"]
    cn_select = cn_gold.loc[
        cn_gold.period_id.isin(period_ids) & cn_gold.country_iso3.isin(both),
        keys
        + [
            "project_count",
            "positive_reported_amount_project_count",
            "reported_amount_known_project_count",
            "record_present",
            "measurement_status",
        ],
    ].rename(
        columns={
            "project_count": "cn_project_count",
            "positive_reported_amount_project_count": "cn_positive_source_amount_project_count",
            "reported_amount_known_project_count": "cn_known_source_amount_project_count",
            "record_present": "cn_record_present",
            "measurement_status": "cn_measurement_status",
        }
    )
    wb_select = wb_gold.loc[
        wb_gold.period_id.isin(period_ids) & wb_gold.country_iso3.isin(both),
        keys
        + [
            "project_count",
            "high_confidence_project_count",
            "proxy_only_project_count",
            "record_present",
            "measurement_status",
        ],
    ].rename(
        columns={
            "project_count": "wb_project_count",
            "high_confidence_project_count": "wb_high_confidence_project_count",
            "proxy_only_project_count": "wb_proxy_only_project_count",
            "record_present": "wb_record_present",
            "measurement_status": "wb_measurement_status",
        }
    )
    panel = universe.merge(
        cn_select, on=keys, how="left", validate="one_to_one", indicator="_cn_join"
    ).merge(wb_select, on=keys, how="left", validate="one_to_one", indicator="_wb_join")
    if not panel._cn_join.eq("both").all() or not panel._wb_join.eq("both").all():
        raise ValueError("common panel has incomplete source coverage; missing is not zero")
    panel = panel.drop(columns=["_cn_join", "_wb_join"])
    if len(panel) != len(areas) * len(period_ids):
        raise ValueError("common source panel row count does not match geography×period universe")
    panel["cn_coverage_interpretation"] = "zero_among_resolved_source_reported_geocodes_only"
    panel["wb_coverage_interpretation"] = "zero_among_resolved_source_reported_geocodes_only"

    cn_roster, cn_unresolved = _cn_roster(
        cn_silver, cn_geography_relation, cn_period_relation, set(both), set(period_ids)
    )
    wb = wb_project_roster.loc[
        wb_project_roster.period_id.isin(period_ids),
        [
            "project_id",
            "project_location_id",
            "geo_uid",
            "period_id",
            "relation_status",
            "geographic_confidence",
        ],
    ].copy()
    wb["source_family"] = "aiddata_worldbank_geocoded"
    wb = wb.rename(
        columns={
            "project_id": "source_project_id",
            "project_location_id": "location_row_id",
        }
    )
    wb["finance_event_type"] = "board_approval"
    wb = wb[
        [
            "source_family",
            "source_project_id",
            "location_row_id",
            "geo_uid",
            "period_id",
            "relation_status",
            "geographic_confidence",
            "finance_event_type",
        ]
    ]
    roster = pd.concat([cn_roster, wb], ignore_index=True)
    roster = roster.loc[roster.geo_uid.isin(areas.geo_uid)].copy()
    roster["source_project_id"] = roster.source_project_id.astype(str)
    roster = roster.sort_values(
        ["source_family", "source_project_id", "geo_uid", "period_id", "location_row_id"]
    ).reset_index(drop=True)
    if roster.duplicated(
        ["source_family", "source_project_id", "location_row_id", "geo_uid", "period_id"]
    ).any():
        raise ValueError("duplicate project-location edges in common panel roster")
    for source, column in (
        ("aiddata_geogcdf", "cn_project_count"),
        ("aiddata_worldbank_geocoded", "wb_project_count"),
    ):
        facts = roster.loc[roster.source_family.eq(source)].drop_duplicates(
            ["source_project_id", "geo_uid", "period_id"]
        )
        counted = facts.groupby(keys).size().rename("_independently_recounted")
        check = panel[keys + [column]].join(counted, on=keys)
        check["_independently_recounted"] = check["_independently_recounted"].fillna(0).astype(int)
        mismatch = check.loc[check[column].ne(check["_independently_recounted"])]
        if len(mismatch):
            raise ValueError(
                f"{source} Gold/source roster parity violation in {len(mismatch)} ADM2-period cells"
            )

    support = (
        panel.groupby(["country_iso3", "period_id"], sort=True)
        .agg(
            adm2_units=("geo_uid", "nunique"),
            cn_reported_geocoded_project_incidence=("cn_project_count", "sum"),
            wb_reported_geocoded_project_incidence=("wb_project_count", "sum"),
            cn_adm2_with_record=("cn_record_present", "sum"),
            wb_adm2_with_record=("wb_record_present", "sum"),
        )
        .reset_index()
    )
    extras = wb_country_period_support[
        [
            "country_iso3",
            "period_id",
            "source_approved_project_count",
            "source_projects_without_broad_adm2_assignment",
        ]
    ].rename(
        columns={
            "source_approved_project_count": "wb_source_projects",
            "source_projects_without_broad_adm2_assignment": "wb_source_projects_unassigned_to_adm2",
        }
    )
    support = support.merge(
        extras, on=["country_iso3", "period_id"], how="left", validate="one_to_one"
    )
    if support.wb_source_projects.isna().any():
        raise ValueError("WB country-period support report is incomplete")
    support = support.merge(
        cn_unresolved, on=["country_iso3", "period_id"], how="left", validate="one_to_one"
    )
    support["cn_country_period_unresolved_geography_projects"] = (
        support["cn_country_period_unresolved_geography_projects"].fillna(0).astype(int)
    )
    qa = (
        QAResult(
            check_id="china_wb_panel.exact_geography_and_period",
            state="GREEN",
            message="common source period/geography universe is complete and one-to-one",
            metrics={
                "country_count": len(both),
                "adm2_units": len(areas),
                "biennia": len(period_ids),
                "area_period_rows": len(panel),
                "missing_source_cells": 0,
            },
        ),
        QAResult(
            check_id="china_wb_panel.independent_project_recount",
            state="GREEN",
            message="both source Gold project counts match independent ID-based roster reconstructions",
            metrics={
                "wb_source_project_area_period_incidences": int(panel.wb_project_count.sum()),
                "cn_source_project_area_period_incidences": int(panel.cn_project_count.sum()),
                "source_project_location_roster_rows": len(roster),
                "parity_failed_area_periods": 0,
                "amount_aggregation_applied": False,
            },
        ),
        QAResult(
            check_id="china_wb_panel.geocoded_source_only",
            state="YELLOW",
            message="geocoded-source zero and proxy geography are NOT proof of absent real investment",
            metrics={
                "wb_unassigned_source_projects_sum_by_country_period": int(
                    support.wb_source_projects_unassigned_to_adm2.sum()
                ),
                "cn_unresolved_geography_source_projects": int(
                    support.cn_country_period_unresolved_geography_projects.sum()
                ),
            },
        ),
    )
    return ChinaWBPanel(
        panel=panel.sort_values(keys).reset_index(drop=True),
        project_roster=roster,
        country_period_support=support,
        qa=qa,
        countries=both,
        period_ids=period_ids,
    )


def materialize_china_worldbank_panel(
    *,
    cn_gold_ref: DatasetRef,
    wb_gold_ref: DatasetRef,
    wb_roster_ref: DatasetRef,
    wb_country_support_ref: DatasetRef,
    cn_silver_ref: DatasetRef,
    cn_geo_ref: DatasetRef,
    cn_period_ref: DatasetRef,
    gadm_ref: DatasetRef,
    cn_gold: pd.DataFrame,
    wb_gold: pd.DataFrame,
    wb_project_roster: pd.DataFrame,
    wb_country_period_support: pd.DataFrame,
    cn_silver: pd.DataFrame,
    cn_geography_relation: pd.DataFrame,
    cn_period_relation: pd.DataFrame,
    geography_units: pd.DataFrame,
    geography: GeographySpec,
    period_scheme: PeriodScheme,
    data_root: DataRoot,
    run_id: str,
    source_start_year: int = 2001,
    source_end_year: int = 2014,
    code_commit: str | None = None,
) -> ChinaWBPanelMaterialization:
    for name, ref in (
        ("CN Gold", cn_gold_ref),
        ("WB Gold", wb_gold_ref),
        ("WB roster", wb_roster_ref),
        ("CN relation", cn_geo_ref),
        ("GADM", gadm_ref),
    ):
        if ref.geography != geography:
            raise ValueError(f"{name} geography version is incompatible with common panel")
    for name, ref in (
        ("CN Gold", cn_gold_ref),
        ("WB Gold", wb_gold_ref),
        ("WB roster", wb_roster_ref),
        ("CN period", cn_period_ref),
    ):
        if ref.period_scheme != period_scheme:
            raise ValueError(f"{name} period convention is incompatible with common panel")

    result = build_china_worldbank_panel(
        cn_gold,
        wb_gold,
        wb_project_roster,
        wb_country_period_support,
        cn_silver,
        cn_geography_relation,
        cn_period_relation,
        geography_units,
        period_scheme=period_scheme,
        start_year=source_start_year,
        end_year=source_end_year,
    )
    input_refs = (
        cn_gold_ref,
        wb_gold_ref,
        wb_roster_ref,
        wb_country_support_ref,
        cn_silver_ref,
        cn_geo_ref,
        cn_period_ref,
        gadm_ref,
    )
    version_inputs = json.dumps(
        [(x.dataset_id, x.version, x.content_sha256) for x in input_refs], sort_keys=True
    ).encode()
    version = f"aiddata-china-wb--{hashlib.sha256(version_inputs).hexdigest()[:12]}--{period_scheme.id}--{source_start_year}-{source_end_year}"
    refs = {
        "panel": DatasetRef(
            dataset_id="investments.aiddata_china_worldbank.common_area_period",
            version=version,
            schema_version="aiddata-china-wb-common-source-measurement-v1",
            layer=DataLayer.GOLD,
            authority=AuthorityLevel.L3_REBUILT,
            grain=GrainSpec(keys=("geo_uid", "period_id")),
            geography=geography,
            period_scheme=period_scheme,
        ),
        "roster": DatasetRef(
            dataset_id="investments.aiddata_china_worldbank.common_project_roster",
            version=version,
            schema_version="aiddata-china-wb-traceable-source-roster-v1",
            layer=DataLayer.GOLD,
            authority=AuthorityLevel.L3_REBUILT,
            grain=GrainSpec(
                keys=(
                    "source_family",
                    "source_project_id",
                    "location_row_id",
                    "geo_uid",
                    "period_id",
                )
            ),
            geography=geography,
            period_scheme=period_scheme,
        ),
        "support": DatasetRef(
            dataset_id="investments.aiddata_china_worldbank.common_country_period_support",
            version=version,
            schema_version="aiddata-china-wb-country-period-support-v1",
            layer=DataLayer.GOLD,
            authority=AuthorityLevel.L3_REBUILT,
            grain=GrainSpec(keys=("country_iso3", "period_id")),
            period_scheme=period_scheme,
        ),
    }
    coverage = CoverageContract(
        geography_scope=f"{geography.id}; source-qualified countries " + ",".join(result.countries),
        temporal_start=min(
            PeriodIndex(period_scheme).period_for(int(i.split("-")[0])).start_date
            for i in result.period_ids
        ),
        temporal_end=max(
            PeriodIndex(period_scheme).period_for(int(i.split("-")[0])).end_date_exclusive
            - timedelta(days=1)
            for i in result.period_ids
        ),
        observation_semantics="one source-comparable ADM2-period row: WB approved geocoded projects "
        "and Chinese committed geocoded projects, with precision and unresolved source limitations",
        absent_row_semantics="not_observed",
        authority=AuthorityLevel.L3_REBUILT,
        basis="exact intersection of accepted WB Geocoded v1.4.2 and GeoGCDF v3.0.1 "
        "measurement contracts; source-zero means no resolved eligible source project, "
        "not no WB/China finance in reality",
    )
    datasets = {
        "panel": (result.panel, "common_area_period.parquet"),
        "roster": (result.project_roster, "common_project_roster.parquet"),
        "support": (result.country_period_support, "common_country_period_support.parquet"),
    }
    base = data_root.gold("investments", "aiddata_china_worldbank_comparison", version)
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        inputs=input_refs,
        outputs=tuple(
            FileMaterialization(
                dataset=refs[k],
                relative_path=filename,
                destination_base=base,
                writer=lambda p, df=frame: df.to_parquet(p, index=False),
            )
            for k, (frame, filename) in datasets.items()
        ),
        code_commit=code_commit,
        qa=result.qa,
        parameters={
            "design": "source-only financing decision measurements",
            "world_bank_date_type": "board_approval",
            "china_date_type": "commitment",
            "source_qualified_zeros_only": True,
            "amount_aggregation": None,
            "analytical_exposure_class_defined": False,
            "geography": geography.model_dump(mode="json"),
            "period_scheme": period_scheme.model_dump(mode="json"),
            "source_start_year": source_start_year,
            "source_end_year": source_end_year,
        },
    )
    hashed = {
        k: next(x for x in manifest.outputs if x.dataset_id == refs[k].dataset_id) for k in refs
    }
    persist_run_artifact(
        data_root,
        run_id,
        "contracts/coverage.json",
        json.dumps(coverage.model_dump(mode="json"), sort_keys=True, indent=2) + "\n",
    )
    persist_run_artifact(
        data_root,
        run_id,
        "contracts/comparison_inputs.json",
        json.dumps(
            {
                "inputs": [x.model_dump(mode="json") for x in input_refs],
                "source_clocks": {"wb": "board_approval", "cn": "commitment"},
                "incomparability_note": "different geocoding and finance-event definitions; not a causal panel",
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
    )
    return ChinaWBPanelMaterialization(
        manifest=manifest,
        coverage=coverage,
        datasets=hashed,
        paths={k: base / filename for k, (_f, filename) in datasets.items()},
        result=result,
    )
