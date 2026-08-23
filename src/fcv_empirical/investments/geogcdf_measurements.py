from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import geopandas as gpd
import pandas as pd
from empirical_contracts import (
    AuthorityLevel,
    CoverageContract,
    DatasetRef,
    GeographySpec,
    GrainSpec,
    MeasurementContract,
    PeriodScheme,
    QAResult,
)
from spatial_foundation import PeriodIndex
from spatial_foundation.geography import assign_points, relate_areal_objects


@dataclass(frozen=True)
class GeoGCDFGeographyResult:
    frame: pd.DataFrame
    qa: tuple[QAResult, ...]


@dataclass(frozen=True)
class GeoGCDFPeriodResult:
    frame: pd.DataFrame
    qa: tuple[QAResult, ...]


@dataclass(frozen=True)
class GeoGCDFGoldResult:
    frame: pd.DataFrame
    qa: tuple[QAResult, ...]
    covered_country_iso3: tuple[str, ...]
    full_coverage_period_ids: tuple[str, ...]


def relate_geogcdf_geography(
    silver: gpd.GeoDataFrame,
    polygons: gpd.GeoDataFrame,
) -> GeoGCDFGeographyResult:
    """Relate project geometries to analytical geography without centroid coercion.

    Points use the shared ambiguity-preserving point kernel. Polygon/MultiPolygon
    projects use the shared many-to-many positive-area overlap relation. A project
    footprint spanning several administrative units is therefore represented in all
    of them rather than treated as ambiguous.
    """
    required = {"project_geometry_row_id", "source_project_id", "geometry"}
    missing = sorted(required - set(silver.columns))
    if missing:
        raise ValueError(f"GeoGCDF Silver is missing required columns: {missing}")
    if silver["project_geometry_row_id"].duplicated().any():
        raise ValueError("GeoGCDF project geometry row IDs must be unique")

    geometry_type = silver.geometry.geom_type.astype("string")
    point_mask = geometry_type.eq("Point") & silver.geometry.notna() & ~silver.geometry.is_empty
    areal_mask = geometry_type.isin(["Polygon", "MultiPolygon"])

    rows: list[pd.DataFrame] = []
    if point_mask.any():
        point_input = silver.loc[point_mask, ["project_geometry_row_id", "geometry"]].copy()
        point_relation, _audit = assign_points(
            point_input,
            polygons,
            point_id_col="project_geometry_row_id",
            polygon_id_col="geo_uid",
        )
        point_relation = point_relation.rename(columns={"assignment_status": "relation_status"})
        point_relation["relation_method"] = "point_intersects"
        point_relation["overlap_area_m2"] = pd.NA
        point_relation["overlap_share_of_object"] = pd.NA
        rows.append(
            point_relation[
                [
                    "project_geometry_row_id",
                    "geo_uid",
                    "relation_method",
                    "relation_status",
                    "candidate_count",
                    "overlap_area_m2",
                    "overlap_share_of_object",
                ]
            ]
        )

    if areal_mask.any():
        areal_input = silver.loc[areal_mask, ["project_geometry_row_id", "geometry"]].copy()
        areal_relation, _audit = relate_areal_objects(
            areal_input,
            polygons,
            object_id_col="project_geometry_row_id",
            polygon_id_col="geo_uid",
        )
        areal_relation["relation_method"] = "positive_area_overlap"
        areal_relation["candidate_count"] = areal_relation["overlap_count"]
        rows.append(
            areal_relation[
                [
                    "project_geometry_row_id",
                    "geo_uid",
                    "relation_method",
                    "relation_status",
                    "candidate_count",
                    "overlap_area_m2",
                    "overlap_share_of_object",
                ]
            ]
        )

    supported_mask = point_mask | areal_mask
    unsupported = silver.loc[~supported_mask, ["project_geometry_row_id"]].copy()
    if len(unsupported):
        unsupported["geo_uid"] = pd.NA
        unsupported["relation_method"] = "none"
        unsupported["relation_status"] = "unsupported_or_invalid_geometry"
        unsupported["candidate_count"] = 0
        unsupported["overlap_area_m2"] = pd.NA
        unsupported["overlap_share_of_object"] = pd.NA
        rows.append(unsupported)

    relation = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    relation = relation.merge(
        silver[["project_geometry_row_id", "source_project_id", "recipient_iso3"]],
        on="project_geometry_row_id",
        how="left",
        validate="many_to_one",
    )

    resolved_point = relation["relation_method"].eq("point_intersects") & relation[
        "relation_status"
    ].eq("matched_unique")
    resolved_areal = relation["relation_method"].eq("positive_area_overlap") & relation[
        "relation_status"
    ].isin(["matched_single", "matched_multiple"])
    resolved = (resolved_point | resolved_areal) & relation["geo_uid"].notna()
    unresolved_ids = relation.loc[~resolved, "project_geometry_row_id"].drop_duplicates()
    multi_area_ids = relation.loc[
        relation["relation_method"].eq("positive_area_overlap")
        & relation["relation_status"].eq("matched_multiple"),
        "project_geometry_row_id",
    ].drop_duplicates()
    ambiguous_point_ids = relation.loc[
        relation["relation_method"].eq("point_intersects")
        & relation["relation_status"].eq("ambiguous_multiple"),
        "project_geometry_row_id",
    ].drop_duplicates()

    qa = (
        QAResult(
            check_id="geogcdf.geography.relation",
            state="GREEN" if len(relation) >= len(silver) else "RED",
            message=(
                "project geometry relations preserve point ambiguity and legitimate many-to-many "
                "areal overlap"
            ),
            metrics={
                "silver_project_rows": len(silver),
                "relation_rows": len(relation),
                "resolved_relation_rows": int(resolved.sum()),
                "unresolved_project_rows": int(unresolved_ids.nunique()),
                "multi_area_areal_projects": int(multi_area_ids.nunique()),
                "ambiguous_point_projects": int(ambiguous_point_ids.nunique()),
            },
        ),
        QAResult(
            check_id="geogcdf.geography.no_amount_allocation",
            state="GREEN",
            message="spatial relation contains geometric facts only; no finance is allocated",
            metrics={"amount_allocation_applied": False},
        ),
    )
    return GeoGCDFGeographyResult(frame=relation, qa=qa)


def assign_geogcdf_periods(
    silver: pd.DataFrame,
    *,
    scheme: PeriodScheme,
) -> GeoGCDFPeriodResult:
    """Assign source commitment/implementation/completion facts to shared periods."""
    required = {
        "project_geometry_row_id",
        "source_project_id",
        "commitment_date",
        "commitment_year",
        "implementation_start_date",
        "implementation_start_year",
        "completion_date",
        "completion_year",
    }
    missing = sorted(required - set(silver.columns))
    if missing:
        raise ValueError(f"GeoGCDF Silver is missing temporal columns: {missing}")

    index = PeriodIndex(scheme)
    specifications = (
        ("commitment", "commitment_date", "commitment_year"),
        ("implementation_start", "implementation_start_date", "implementation_start_year"),
        ("completion", "completion_date", "completion_year"),
    )
    rows: list[dict[str, object]] = []
    for row in silver.itertuples(index=False):
        row_dict = row._asdict()
        for date_type, date_col, year_col in specifications:
            source_date = row_dict[date_col]
            source_year = row_dict[year_col]
            value: date | int | None
            temporal_basis: str
            if pd.notna(source_date):
                timestamp = pd.Timestamp(source_date)
                value = timestamp.date()
                temporal_basis = "source_exact_date"
            elif pd.notna(source_year):
                numeric_year = float(source_year)
                if not numeric_year.is_integer():
                    value = None
                    temporal_basis = "invalid_source_year"
                else:
                    value = int(numeric_year)
                    temporal_basis = "source_year"
            else:
                value = None
                temporal_basis = "source_date_missing"

            if value is None:
                period_id = pd.NA
                status = "unresolved"
            else:
                period_id = index.period_for(value).period_id
                status = "assigned"
            rows.append(
                {
                    "project_geometry_row_id": row_dict["project_geometry_row_id"],
                    "source_project_id": row_dict["source_project_id"],
                    "project_date_type": date_type,
                    "source_date": source_date,
                    "source_year": source_year,
                    "temporal_basis": temporal_basis,
                    "period_id": period_id,
                    "period_assignment_status": status,
                }
            )

    frame = pd.DataFrame(rows)
    commitment = frame["project_date_type"].eq("commitment")
    unresolved_commitments = int(
        (commitment & frame["period_assignment_status"].ne("assigned")).sum()
    )
    qa = (
        QAResult(
            check_id="geogcdf.period.assignment",
            state="GREEN" if unresolved_commitments == 0 else "YELLOW",
            message="source temporal facts are assigned through the shared PeriodIndex",
            metrics={
                "period_rows": len(frame),
                "commitment_rows": int(commitment.sum()),
                "unresolved_commitment_rows": unresolved_commitments,
                "unresolved_all_date_rows": int(
                    frame["period_assignment_status"].ne("assigned").sum()
                ),
            },
        ),
    )
    return GeoGCDFPeriodResult(frame=frame, qa=qa)


def _resolved_geography_pairs(relation: pd.DataFrame) -> pd.DataFrame:
    point = relation["relation_method"].eq("point_intersects") & relation[
        "relation_status"
    ].eq("matched_unique")
    areal = relation["relation_method"].eq("positive_area_overlap") & relation[
        "relation_status"
    ].isin(["matched_single", "matched_multiple"])
    return relation.loc[
        (point | areal) & relation["geo_uid"].notna(),
        ["project_geometry_row_id", "geo_uid"],
    ].drop_duplicates()


def _full_periods_within(
    scheme: PeriodScheme,
    *,
    start_year: int,
    end_year: int,
):
    periods = PeriodIndex(scheme).range(start_year, end_year)
    return tuple(
        period
        for period in periods
        if period.start_year >= start_year and period.end_year <= end_year
    )


def build_geogcdf_commitment_gold(
    silver: pd.DataFrame,
    geography_relation: pd.DataFrame,
    period_relation: pd.DataFrame,
    geography_units: pd.DataFrame,
    *,
    period_scheme: PeriodScheme,
    source_universe_start_year: int = 2000,
    source_universe_end_year: int = 2021,
    require_complete_resolution: bool = True,
) -> GeoGCDFGoldResult:
    """Build dense commitment-period project-count measurement over verified source support.

    Structural zeros mean zero *source-reported GeoGCDF commitment projects intersecting
    the area-period* within recipient countries represented by the source and periods fully
    contained in the declared source project universe. They do not mean no investment exists
    in reality.
    """
    if source_universe_end_year < source_universe_start_year:
        raise ValueError("source universe end year must be >= start year")
    required_geo = {"geo_uid", "country_iso3"}
    missing_geo = sorted(required_geo - set(geography_units.columns))
    if missing_geo:
        raise ValueError(f"geography units are missing required columns: {missing_geo}")
    if geography_units["geo_uid"].duplicated().any():
        raise ValueError("geography units must be unique by geo_uid")

    source_ids = silver["source_project_id"]
    if source_ids.isna().any() or source_ids.duplicated().any():
        raise ValueError(
            "commitment Gold requires non-missing unique GeoGCDF source project IDs; "
            "fix source identity rather than deduplicating"
        )

    source_countries = tuple(
        sorted(silver["recipient_iso3"].dropna().astype(str).str.strip().unique().tolist())
    )
    target_country_set = set(geography_units["country_iso3"].dropna().astype(str))
    covered_countries = tuple(country for country in source_countries if country in target_country_set)
    if not covered_countries:
        raise ValueError("no overlap between GeoGCDF recipient countries and target geography")

    target_project_rows = silver.loc[
        silver["recipient_iso3"].astype("string").isin(covered_countries),
        [
            "project_geometry_row_id",
            "source_project_id",
            "reported_amount_constant_usd_2021",
        ],
    ].copy()
    resolved_geo = _resolved_geography_pairs(geography_relation)
    resolved_project_rows = set(resolved_geo["project_geometry_row_id"].astype(str))
    unresolved_geo = target_project_rows.loc[
        ~target_project_rows["project_geometry_row_id"].astype(str).isin(resolved_project_rows)
    ]

    commitment_period = period_relation.loc[
        period_relation["project_date_type"].eq("commitment"),
        ["project_geometry_row_id", "period_id", "period_assignment_status"],
    ].copy()
    assigned_period_rows = set(
        commitment_period.loc[
            commitment_period["period_assignment_status"].eq("assigned")
            & commitment_period["period_id"].notna(),
            "project_geometry_row_id",
        ].astype(str)
    )
    unresolved_time = target_project_rows.loc[
        ~target_project_rows["project_geometry_row_id"].astype(str).isin(assigned_period_rows)
    ]
    if require_complete_resolution and (len(unresolved_geo) or len(unresolved_time)):
        raise ValueError(
            "cannot license structural-zero commitment support while target-country projects "
            f"remain unresolved (geography={len(unresolved_geo)}, time={len(unresolved_time)})"
        )

    full_periods = _full_periods_within(
        period_scheme,
        start_year=source_universe_start_year,
        end_year=source_universe_end_year,
    )
    if not full_periods:
        raise ValueError("period scheme has no periods fully inside source universe")
    full_period_ids = tuple(period.period_id for period in full_periods)

    eligible = (
        target_project_rows.merge(
            resolved_geo,
            on="project_geometry_row_id",
            how="inner",
            validate="one_to_many",
        )
        .merge(
            commitment_period.loc[
                commitment_period["period_assignment_status"].eq("assigned")
                & commitment_period["period_id"].isin(full_period_ids),
                ["project_geometry_row_id", "period_id"],
            ],
            on="project_geometry_row_id",
            how="inner",
            validate="many_to_one",
        )
    )
    eligible = eligible.drop_duplicates(["source_project_id", "geo_uid", "period_id"])
    eligible["_positive_amount"] = (
        pd.to_numeric(eligible["reported_amount_constant_usd_2021"], errors="coerce") > 0
    ).fillna(False)
    eligible["_amount_known"] = pd.to_numeric(
        eligible["reported_amount_constant_usd_2021"], errors="coerce"
    ).notna()

    observed_rows: list[dict[str, object]] = []
    for (geo_uid, period_id), group in eligible.groupby(["geo_uid", "period_id"], sort=True):
        observed_rows.append(
            {
                "geo_uid": geo_uid,
                "period_id": period_id,
                "project_count": int(group["source_project_id"].nunique()),
                "positive_reported_amount_project_count": int(
                    group.loc[group["_positive_amount"], "source_project_id"].nunique()
                ),
                "reported_amount_known_project_count": int(
                    group.loc[group["_amount_known"], "source_project_id"].nunique()
                ),
            }
        )
    observed = pd.DataFrame(
        observed_rows,
        columns=[
            "geo_uid",
            "period_id",
            "project_count",
            "positive_reported_amount_project_count",
            "reported_amount_known_project_count",
        ],
    )

    covered_geographies = geography_units.loc[
        geography_units["country_iso3"].astype("string").isin(covered_countries),
        ["geo_uid", "country_iso3"],
    ].drop_duplicates()
    universe = covered_geographies.assign(_key=1).merge(
        pd.DataFrame({"period_id": full_period_ids, "_key": 1}), on="_key"
    ).drop(columns="_key")
    gold = universe.merge(observed, on=["geo_uid", "period_id"], how="left", validate="one_to_one")
    count_columns = [
        "project_count",
        "positive_reported_amount_project_count",
        "reported_amount_known_project_count",
    ]
    for column in count_columns:
        gold[column] = gold[column].fillna(0).astype("int64")
    gold["record_present"] = gold["project_count"] > 0
    gold["measurement_status"] = gold["record_present"].map(
        {True: "aggregated_from_observed", False: "structural_zero"}
    )
    gold = gold[
        [
            "geo_uid",
            "period_id",
            "country_iso3",
            *count_columns,
            "record_present",
            "measurement_status",
        ]
    ].sort_values(["geo_uid", "period_id"]).reset_index(drop=True)

    qa = (
        QAResult(
            check_id="geogcdf.gold.resolution",
            state="GREEN" if len(unresolved_geo) == 0 and len(unresolved_time) == 0 else "YELLOW",
            message="structural-zero support is conditioned on explicit geography/time resolution",
            metrics={
                "target_country_project_rows": len(target_project_rows),
                "unresolved_geography_projects": len(unresolved_geo),
                "unresolved_commitment_time_projects": len(unresolved_time),
            },
        ),
        QAResult(
            check_id="geogcdf.gold.dense_support",
            state="GREEN",
            message=(
                "Gold explicitly materializes source-defined structural zeros only for covered "
                "recipient countries and periods fully inside the declared commitment universe"
            ),
            metrics={
                "covered_countries": len(covered_countries),
                "covered_geographies": len(covered_geographies),
                "full_coverage_periods": len(full_period_ids),
                "gold_rows": len(gold),
                "structural_zero_rows": int(gold["measurement_status"].eq("structural_zero").sum()),
                "observed_rows": int(gold["record_present"].sum()),
            },
        ),
        QAResult(
            check_id="geogcdf.gold.amount_semantics",
            state="GREEN",
            message=(
                "Gold counts projects with positive reported project amounts but never allocates "
                "or sums project finance across geography"
            ),
            metrics={"amount_allocation_applied": False, "amount_sum_materialized": False},
        ),
    )
    return GeoGCDFGoldResult(
        frame=gold,
        qa=qa,
        covered_country_iso3=covered_countries,
        full_coverage_period_ids=full_period_ids,
    )


def build_geogcdf_commitment_coverage(
    result: GeoGCDFGoldResult,
    *,
    geography: GeographySpec,
    period_scheme: PeriodScheme,
) -> CoverageContract:
    index = PeriodIndex(period_scheme)
    periods = [index.period_for(int(period_id.split("-", 1)[0])) for period_id in result.full_coverage_period_ids]
    temporal_start = min(period.start_date for period in periods)
    temporal_end = max(period.end_date_exclusive - timedelta(days=1) for period in periods)
    return CoverageContract(
        geography_scope=(
            f"{geography.id}; recipient countries represented in source and target geography: "
            + ",".join(result.covered_country_iso3)
        ),
        temporal_start=temporal_start,
        temporal_end=temporal_end,
        observation_semantics=(
            "dense counts of GeoGCDF source projects by commitment period and analytical geography; "
            "areal projects may legitimately expose multiple geographies"
        ),
        absent_row_semantics="not_observed",
        authority=AuthorityLevel.L3_REBUILT,
        basis=(
            "GeoGCDF v3 documents the known project universe for 2000-2021 and geospatial "
            "representations for those projects. Structural zeros are materialized as rows only "
            "for periods fully inside that declared range and countries represented in both source "
            "and target geography."
        ),
    )


def build_geogcdf_commitment_measurement_contract(
    *,
    silver_dataset: DatasetRef,
    geography: GeographySpec,
    period_scheme: PeriodScheme,
    coverage: CoverageContract,
    covered_country_iso3: tuple[str, ...],
) -> MeasurementContract:
    return MeasurementContract(
        measure_id="aiddata.geogcdf.commitment_exposure.area_period",
        description=(
            "GeoGCDF project counts by source commitment period and analytical geography, "
            "including explicit source-defined structural-zero rows over verified support"
        ),
        source_dataset=silver_dataset,
        output_grain=GrainSpec(keys=("geo_uid", "period_id")),
        unit="source-reported projects",
        aggregation=(
            "count unique source projects intersecting geography; separately count projects with "
            "known and positive reported project-level amount"
        ),
        coverage=coverage,
        geography=geography,
        period_scheme=period_scheme,
        parameters={
            "project_date_type": "commitment",
            "point_geography_policy": "matched_unique_only",
            "areal_geography_policy": "all_positive_area_overlaps",
            "amount_allocation": None,
            "amount_sum_materialized": False,
            "structural_zeros_materialized": True,
            "covered_country_iso3": list(covered_country_iso3),
        },
    )
