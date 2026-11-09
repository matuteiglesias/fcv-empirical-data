"""Source-qualified World Bank Geocoded Research Release project-count measurements.

Reads commissioned Silver geography and approval-period relations, never
constructs treatment/control or assigns a project budget to an ADM2 unit.
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
    MeasurementContract,
    PeriodScheme,
    QAResult,
    RunManifest,
)
from spatial_foundation import DataRoot, PeriodIndex

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact


@dataclass(frozen=True)
class WBGeocodedGold:
    gold: pd.DataFrame
    project_roster: pd.DataFrame
    country_period_support: pd.DataFrame
    qa: tuple[QAResult, ...]
    covered_country_iso3: tuple[str, ...]
    period_ids: tuple[str, ...]


@dataclass(frozen=True)
class WBGeocodedGoldMaterialization:
    manifest: RunManifest
    measurement_contract: MeasurementContract
    coverage_contract: CoverageContract
    datasets: dict[str, DatasetRef]
    paths: dict[str, Path]
    result: WBGeocodedGold


def build_wb_geocoded_gold(
    geography_relation: pd.DataFrame,
    period_relation: pd.DataFrame,
    geography_units: pd.DataFrame,
    *,
    period_scheme: PeriodScheme,
    source_universe_start_year: int = 1995,
    source_universe_end_year: int = 2014,
) -> WBGeocodedGold:
    """Dense counts of eligible, *mapped* source projects; never true no-investment.

    The release's source-covered geography is the supplied GADM analytical
    universe. In-source zero means no *resolved eligible source-geocoded WB*
    project in the ADM2 and approval biennium, not zero World Bank investment
    and not evidence for a wholly unexposed causal control.
    """
    expected_geo = {"geo_uid", "country_iso3"}
    if missing := (expected_geo - set(geography_units.columns)):
        raise ValueError(f"missing GADM columns: {sorted(missing)}")
    if geography_units.geo_uid.isna().any() or geography_units.geo_uid.duplicated().any():
        raise ValueError("GADM geography IDs must be unique and non-null")
    if geography_units.country_iso3.isna().any():
        raise ValueError("GADM country identifiers must not be null")
    if not period_relation.project_id.is_unique:
        raise ValueError("one and only one approval-period record per WB project is required")
    if geography_relation.project_location_id.isna().any():
        raise ValueError("WB relation has missing source project_location_id")
    if geography_relation.project_location_id.duplicated().any():
        raise ValueError("WB relation must preserve unique WB source project locations")
    required = {
        "project_id",
        "project_location_id",
        "geo_uid",
        "recipients_iso3",
        "broad_source_point_assigned",
        "high_confidence_assigned",
        "relation_status",
    }
    if missing := (required - set(geography_relation.columns)):
        raise ValueError(f"missing WB geography relation fields: {sorted(missing)}")
    if not set(geography_relation.project_id).issubset(set(period_relation.project_id)):
        raise ValueError("WB geographic project IDs lack source-native approval periods")
    if not set(geography_relation.geo_uid.dropna()).issubset(set(geography_units.geo_uid)):
        raise ValueError("WB geographic assignments not present in frozen GADM")
    if (
        geography_relation.high_confidence_assigned
        & ~geography_relation.broad_source_point_assigned
    ).any():
        raise ValueError("high confidence cannot exist outside broad source project support")
    if (
        geography_relation.loc[~geography_relation.broad_source_point_assigned, "geo_uid"]
        .notna()
        .any()
    ):
        raise ValueError("unassigned WB relation must not have a geo_uid")
    if period_relation.time_assignment_status.ne("assigned").any():
        raise ValueError("WB approval-period source relation contains unresolved time assignments")

    # Verify source geocoding never moves a WB project into another country.
    geo_country = geography_units[["geo_uid", "country_iso3"]]
    assigned = geography_relation.loc[geography_relation.broad_source_point_assigned].merge(
        geo_country, on="geo_uid", validate="many_to_one"
    )
    if assigned.country_iso3.ne(assigned.recipients_iso3).any():
        raise ValueError("source recipient ISO3 differs from assigned GADM country")

    if source_universe_start_year > source_universe_end_year:
        raise ValueError("invalid source year coverage")
    index = PeriodIndex(period_scheme)
    candidates = index.range(source_universe_start_year, source_universe_end_year)
    eligible_periods = [
        p
        for p in candidates
        if p.start_year >= source_universe_start_year and p.end_year <= source_universe_end_year
    ]
    if not eligible_periods:
        raise ValueError("no full biennium inside WB source universe")
    period_ids = tuple(x.period_id for x in eligible_periods)
    candidate_country = tuple(sorted(geography_units.country_iso3.unique().tolist()))

    matching_periods = period_relation.loc[
        period_relation.period_id.isin(period_ids),
        ["project_id", "period_id", "recipient_iso3"],
    ]
    # Count each unique project within each ADM2-period once, even if source
    # records describe several locations of the same project in that ADM2.
    roster = assigned.merge(
        matching_periods,
        on="project_id",
        how="inner",
        validate="many_to_one",
        suffixes=("", "_period"),
    )
    if roster.recipients_iso3.ne(roster.recipient_iso3).any():
        raise ValueError("WB project recipient country changed across source relation")
    roster = (
        roster[
            [
                "project_id",
                "project_location_id",
                "geo_uid",
                "period_id",
                "relation_status",
                "geographic_confidence",
                "high_confidence_assigned",
            ]
        ]
        .sort_values(["project_id", "project_location_id", "geo_uid", "period_id"])
        .reset_index(drop=True)
    )
    if roster.duplicated(["project_location_id", "geo_uid", "period_id"]).any():
        raise ValueError("duplicate WB source-project/location geographic-period relation")

    pairs = roster.drop_duplicates(["project_id", "geo_uid", "period_id"])
    broad = pairs.groupby(["geo_uid", "period_id"]).project_id.nunique().rename("project_count")
    strict = roster.loc[roster.high_confidence_assigned].drop_duplicates(
        ["project_id", "geo_uid", "period_id"]
    )
    high = (
        strict.groupby(["geo_uid", "period_id"])
        .project_id.nunique()
        .rename("high_confidence_project_count")
    )
    universe = (
        geography_units[["geo_uid", "country_iso3"]]
        .assign(_link=1)
        .merge(pd.DataFrame({"period_id": period_ids, "_link": 1}), on="_link")
        .drop(columns="_link")
    )
    gold = universe.merge(
        broad, on=["geo_uid", "period_id"], how="left", validate="one_to_one"
    ).merge(high, on=["geo_uid", "period_id"], how="left", validate="one_to_one")
    for column in ("project_count", "high_confidence_project_count"):
        gold[column] = gold[column].fillna(0).astype("int64")
    gold["proxy_only_project_count"] = (
        gold.project_count - gold.high_confidence_project_count
    ).astype("int64")
    if (gold.proxy_only_project_count < 0).any():
        raise ValueError("high-confidence WB project count exceeded broad count")
    gold["record_present"] = gold.project_count.gt(0)
    gold["measurement_status"] = gold.record_present.map(
        {
            True: "aggregated_from_resolved_source_geocodes",
            False: "zero_among_resolved_eligible_source_geocodes",
        }
    )
    gold = gold.sort_values(["geo_uid", "period_id"]).reset_index(drop=True)
    if len(gold) != len(geography_units) * len(period_ids):
        raise RuntimeError("WB Gold area-period universe missing or multiplied")

    # Country-period audit exposes source projects with no resolved geocodes.
    source = period_relation.loc[
        period_relation.period_id.isin(period_ids),
        ["project_id", "recipient_iso3", "period_id"],
    ]
    project_per_country = source.groupby(["recipient_iso3", "period_id"]).project_id.nunique()
    broad_per_country = (
        roster.merge(geo_country, on="geo_uid", how="left", validate="many_to_one")
        .groupby(["country_iso3", "period_id"])
        .project_id.nunique()
    )
    high_per_country = (
        roster.loc[roster.high_confidence_assigned]
        .merge(geo_country, on="geo_uid", how="left", validate="many_to_one")
        .groupby(["country_iso3", "period_id"])
        .project_id.nunique()
    )
    support = (
        pd.DataFrame(
            {
                "country_iso3": candidate_country,
                "_link": 1,
            }
        )
        .merge(pd.DataFrame({"period_id": period_ids, "_link": 1}), on="_link")
        .drop(columns="_link")
    )
    support = support.set_index(["country_iso3", "period_id"])
    support["source_approved_project_count"] = project_per_country.rename_axis(
        ["country_iso3", "period_id"]
    )
    support["broad_resolved_project_count"] = broad_per_country
    support["high_confidence_project_count"] = high_per_country
    for name in (
        "source_approved_project_count",
        "broad_resolved_project_count",
        "high_confidence_project_count",
    ):
        support[name] = support[name].fillna(0).astype("int64")
    support["source_projects_without_broad_adm2_assignment"] = (
        support.source_approved_project_count - support.broad_resolved_project_count
    )
    if (support.source_projects_without_broad_adm2_assignment < 0).any():
        raise ValueError("WB assigned distinct projects exceeded source project inventory")
    support = (
        support.reset_index().sort_values(["country_iso3", "period_id"]).reset_index(drop=True)
    )

    qa = (
        QAResult(
            check_id="aiddata_wb.gold.support",
            state="GREEN",
            message="WB Gold covers GADM source countries and biennia inside WB 1995–2014 release",
            metrics={
                "area_period_rows": len(gold),
                "periods": len(period_ids),
                "covered_countries": len(candidate_country),
                "covered_geographies": len(geography_units),
                "zero_source_eligible_rows": int((~gold.record_present).sum()),
            },
        ),
        QAResult(
            check_id="aiddata_wb.gold.dedup_precision",
            state="GREEN",
            message="distinct WB projects per ADM2-period; high confidence subset; no financial allocations",
            metrics={
                "source_locations_eligible": len(geography_relation),
                "resolved_roster_rows_in_periods": len(roster),
                "unique_project_geography_period_rows": len(pairs),
                "gold_source_project_incidence_sum": int(gold.project_count.sum()),
                "amount_sum_materialized": False,
            },
        ),
        QAResult(
            check_id="aiddata_wb.gold.geocoding_limitations",
            state="YELLOW",
            message="absence of recorded resolved source geocodes does not establish absence of WB investment",
            metrics={
                "source_project_count_in_periods": int(support.source_approved_project_count.sum()),
                "projects_without_any_broad_adm2_assignment": int(
                    support.source_projects_without_broad_adm2_assignment.sum()
                ),
                "source_rows_outside_broad_geocoding": int(
                    (~geography_relation.broad_source_point_assigned).sum()
                ),
            },
        ),
    )
    return WBGeocodedGold(
        gold=gold,
        project_roster=roster,
        country_period_support=support,
        qa=qa,
        covered_country_iso3=candidate_country,
        period_ids=period_ids,
    )


def _datasets(
    source_snapshot_id: str,
    geography: GeographySpec,
    period_scheme: PeriodScheme,
) -> dict[str, DatasetRef]:
    token = f"{source_snapshot_id}--geo-{hashlib.sha256(geography.id.encode()).hexdigest()[:12]}--{period_scheme.id}"
    return {
        "gold": DatasetRef(
            dataset_id="investments.aiddata_worldbank_geocoded.approval_area_period",
            version=token,
            schema_version="wb-approval-project-count-area-period-v1",
            layer=DataLayer.GOLD,
            authority=AuthorityLevel.L3_REBUILT,
            grain=GrainSpec(keys=("geo_uid", "period_id")),
            geography=geography,
            period_scheme=period_scheme,
        ),
        "roster": DatasetRef(
            dataset_id="investments.aiddata_worldbank_geocoded.approval_project_roster",
            version=token,
            schema_version="wb-approval-project-geo-period-roster-v1",
            layer=DataLayer.GOLD,
            authority=AuthorityLevel.L3_REBUILT,
            grain=GrainSpec(keys=("project_location_id", "geo_uid", "period_id")),
            geography=geography,
            period_scheme=period_scheme,
        ),
        "support": DatasetRef(
            dataset_id="investments.aiddata_worldbank_geocoded.approval_country_period_support",
            version=token,
            schema_version="wb-approval-country-period-support-v1",
            layer=DataLayer.GOLD,
            authority=AuthorityLevel.L3_REBUILT,
            grain=GrainSpec(keys=("country_iso3", "period_id")),
            period_scheme=period_scheme,
        ),
    }


def materialize_wb_geocoded_gold(
    *,
    wb_geography_ref: DatasetRef,
    wb_period_ref: DatasetRef,
    gadm_ref: DatasetRef,
    source_wb_silver_ref: DatasetRef,
    wb_source_snapshot_id: str,
    geography_relation: pd.DataFrame,
    period_relation: pd.DataFrame,
    geography_units: pd.DataFrame,
    geography: GeographySpec,
    period_scheme: PeriodScheme,
    data_root: DataRoot,
    run_id: str,
    source_start_year: int = 1995,
    source_end_year: int = 2014,
    code_commit: str | None = None,
) -> WBGeocodedGoldMaterialization:
    if wb_geography_ref.geography != geography or gadm_ref.geography != geography:
        raise ValueError("WB geography and GADM DatasetRefs must agree")
    if wb_period_ref.period_scheme != period_scheme:
        raise ValueError("WB project-period and requested Gold period schemes must agree")
    result = build_wb_geocoded_gold(
        geography_relation,
        period_relation,
        geography_units,
        period_scheme=period_scheme,
        source_universe_start_year=source_start_year,
        source_universe_end_year=source_end_year,
    )
    full_periods = [
        PeriodIndex(period_scheme).period_for(int(x.split("-")[0])) for x in result.period_ids
    ]
    coverage = CoverageContract(
        geography_scope=f"{geography.id}; source-geocoded WB project universe in "
        + ",".join(result.covered_country_iso3),
        temporal_start=min(x.start_date for x in full_periods),
        temporal_end=max(x.end_date_exclusive - timedelta(days=1) for x in full_periods),
        observation_semantics="dense distinct World Bank geocoded-source projects by board-approval period; "
        "geographical proxies and unresolved source sites are reported separately",
        absent_row_semantics="not_observed",
        authority=AuthorityLevel.L3_REBUILT,
        basis="AidData World Bank geocoded research release v1.4.2 covers project approvals "
        "1995–2014. Explicit rows with zero only mean zero resolvable source-geocoded projects "
        "under the stated coordinate/precision policy, not zero actual development investment.",
    )
    refs = _datasets(wb_source_snapshot_id, geography, period_scheme)
    contract = MeasurementContract(
        measure_id="aiddata.worldbank_geocoded.approval_project_exposure.area_period",
        description="Source-qualified WB geocoded project counts by board approval, not intervention start",
        source_dataset=source_wb_silver_ref,
        output_grain=GrainSpec(keys=("geo_uid", "period_id")),
        unit="distinct World Bank source projects",
        aggregation="COUNT(DISTINCT project_id) per eligible reported ADM2×approval biennium; "
        "high_confidence subset; no finance allocation",
        coverage=coverage,
        geography=geography,
        period_scheme=period_scheme,
        parameters={
            "project_date_type": "board_approval",
            "high_confidence": "exact named point or name-verified ADM2 centroid",
            "broad": "exact named point, name verified/unverified ADM2 centroid, or near-point proxy",
            "source_universe_years": [source_start_year, source_end_year],
            "source_snapshot_id": wb_source_snapshot_id,
            "amount_allocation": None,
            "source_subset_zero_semantics": True,
            "unlocated_projects_survive_in_country_period_support": True,
        },
    )
    dest = data_root.gold("investments", "aiddata_wb_geocoded_approval", refs["gold"].version)
    data = {
        "gold": (result.gold, "approval_area_period.parquet"),
        "roster": (result.project_roster, "project_roster.parquet"),
        "support": (result.country_period_support, "country_period_support.parquet"),
    }
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        inputs=(wb_geography_ref, wb_period_ref, gadm_ref, source_wb_silver_ref),
        outputs=tuple(
            FileMaterialization(
                dataset=refs[name],
                relative_path=filename,
                destination_base=dest,
                writer=lambda path, frame=frame: frame.to_parquet(path, index=False),
            )
            for name, (frame, filename) in data.items()
        ),
        parameters={
            "source_universe": [source_start_year, source_end_year],
            "source_name": "aiddata_worldbank_geocoded",
            "period_scheme": period_scheme.model_dump(mode="json"),
            "geography": geography.model_dump(mode="json"),
            "measurement_semantics": "geocoded-source resolution only",
            "amount_allocation": None,
        },
        code_commit=code_commit,
        qa=result.qa,
    )
    files = {name: dest / filename for name, (_frame, filename) in data.items()}
    hashed = {
        name: next(x for x in manifest.outputs if x.dataset_id == refs[name].dataset_id)
        for name in refs
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
        "contracts/measurement_contract.json",
        json.dumps(contract.model_dump(mode="json"), sort_keys=True, indent=2) + "\n",
    )
    persist_run_artifact(
        data_root,
        run_id,
        "qa/wb_source_country_period_gaps.json",
        json.dumps(
            {
                "source_total_projects": int(
                    result.country_period_support.source_approved_project_count.sum()
                ),
                "source_projects_without_broad_adm2": int(
                    result.country_period_support.source_projects_without_broad_adm2_assignment.sum()
                ),
                "sources_without_spatial_assignment_policy": "remain source-recorded but not counted in ADM2 Gold",
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
    )
    return WBGeocodedGoldMaterialization(
        manifest=manifest,
        measurement_contract=contract,
        coverage_contract=coverage,
        datasets=hashed,
        paths=files,
        result=result,
    )
