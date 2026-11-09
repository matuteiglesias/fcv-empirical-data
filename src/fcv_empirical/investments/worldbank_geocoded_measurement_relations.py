"""AidData WB Geocoded v1.4.2 → GADM/period facts, without treatment semantics.

A geocoded administrative centroid is NOT a verified physical project site.
The optional broad proxy relation is explicitly distinguished from exact-name
and verified-district records. Coarse national/ADM1/capital locations remain
unassigned to ADM2 in this source-specific measurement.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd
from empirical_contracts import (
    AuthorityLevel,
    DataLayer,
    DatasetRef,
    GeographySpec,
    GrainSpec,
    PeriodScheme,
    QAResult,
    RunManifest,
    SourceSnapshotRef,
)
from spatial_foundation import DataRoot, PeriodIndex, register_external_snapshot, sha256_file
from spatial_foundation.geography import assign_points

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact


@dataclass(frozen=True)
class WBGeocodedRelations:
    geography: pd.DataFrame
    periods: pd.DataFrame
    qa: tuple[QAResult, ...]


@dataclass(frozen=True)
class WBGeocodedRelationsMaterialization:
    manifest: RunManifest
    datasets: dict[str, DatasetRef]
    paths: dict[str, Path]
    geography_qa: tuple[QAResult, ...]


_ADMIN_WORDS = re.compile(
    r"\b(districts?|departments?|departements?|departamentos?|provinces?|"
    r"provincias?|prefectures?|prefecturas?|municipalities?|municipios?|"
    r"communes?|councils?|subcounties?)\b"
)


def _area_name_key(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    # Canonical-equivalence only: no fuzzy name guessing, nearest-name
    # match, or forced tie-breaking across different administrative vintages.
    t = unicodedata.normalize("NFKD", str(value))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = _ADMIN_WORDS.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def build_wb_geocoded_relations(
    projects: pd.DataFrame,
    locations: pd.DataFrame,
    geography: gpd.GeoDataFrame,
    adm2_names: pd.DataFrame,
    *,
    period_scheme: PeriodScheme,
) -> WBGeocodedRelations:
    """Source-specific measured links with explicit uncertainty and country scope.

    All projects whose recipient country exists in target GADM are retained,
    including projects without geocoded source locations.
    """
    if projects.project_id.isna().any() or projects.project_id.duplicated().any():
        raise ValueError("WB projects must have unique non-null project_id")
    if (
        locations.project_location_id.isna().any()
        or locations.project_location_id.duplicated().any()
    ):
        raise ValueError("WB source location IDs must be unique")
    if geography.geo_uid.isna().any() or geography.geo_uid.duplicated().any():
        raise ValueError("GADM analytical geo_uid must be unique")
    if geography.crs is None:
        raise ValueError("GADM geometry CRS is required")
    if geography["geometry_role"].ne("analytical").any():
        raise ValueError("WB measurements require analytical geography, not display geometries")
    if adm2_names.GID_2.isna().any() or adm2_names.GID_2.duplicated().any():
        raise ValueError("GADM source-name IDs must be unique")
    if set(geography.source_geo_id) != set(adm2_names.GID_2):
        raise ValueError("GADM source-name inventory is not the exact analytical geography version")

    target = set(geography.country_iso3.dropna().astype(str))
    p = projects.loc[
        projects.recipients_iso3.astype("string").isin(target),
        ["project_id", "recipients_iso3", "start_actual_isodate"],
    ].copy()
    if not p.project_id.is_unique:
        raise ValueError("WB project subset has duplicated IDs")
    location_scope = locations.merge(
        p[["project_id", "recipients_iso3"]],
        on="project_id",
        how="inner",
        validate="many_to_one",
    )
    p["approval_date"] = pd.to_datetime(p.start_actual_isodate, errors="coerce")
    if p.approval_date.isna().any():
        raise ValueError("WB source approval/start dates must be present and parseable")
    index = PeriodIndex(period_scheme)
    periods = pd.DataFrame(
        {
            "project_id": p.project_id.to_numpy(),
            "recipient_iso3": p.recipients_iso3.to_numpy(),
            "source_approval_date": p.approval_date.to_numpy(),
            "event_type": "board_approval",
            "period_id": [index.period_for(d.date()).period_id for d in p.approval_date],
            "time_assignment_status": "assigned",
            "temporal_basis": "source_exact_start_date_verified_against_approval",
        }
    )

    candidate = location_scope.loc[location_scope.precision_code.isin([1, 2, 3])].copy()
    if not candidate.empty:
        points = gpd.GeoDataFrame(
            candidate[["project_location_id"]].copy(),
            geometry=gpd.points_from_xy(candidate.longitude, candidate.latitude),
            crs="EPSG:4326",
        )
        candidates, _audit = assign_points(
            points, geography, point_id_col="project_location_id", polygon_id_col="geo_uid"
        )
        # Ambiguous boundary candidates are never tied by ordering.
        point_counts = candidates.groupby("project_location_id")["candidate_count"].max()
        unique = candidates.loc[
            candidates.assignment_status.eq("matched_unique") & candidates.geo_uid.notna(),
            ["project_location_id", "geo_uid"],
        ].drop_duplicates("project_location_id")
        unique = unique.merge(
            geography[["geo_uid", "country_iso3", "source_geo_id"]],
            on="geo_uid",
            validate="many_to_one",
        )
        unique = unique.merge(
            adm2_names[["GID_2", "NAME_2"]],
            left_on="source_geo_id",
            right_on="GID_2",
            validate="many_to_one",
        )
        lookup = unique.set_index("project_location_id")
    else:
        point_counts = pd.Series(dtype="int64")
        lookup = pd.DataFrame()

    # Every in-scope source location appears exactly once, even if the point
    # has no defensible ADM2 association. Approximate and proxy matches are
    # preserved separately from strong spatial matches.
    result = location_scope[
        [
            "project_id",
            "project_location_id",
            "recipients_iso3",
            "precision_code",
            "place_name",
            "latitude",
            "longitude",
            "location_type_code",
            "geographic_exactness",
        ]
    ].copy()
    rows = []
    for row in result.itertuples(index=False):
        uid = row.project_location_id
        precision = row.precision_code
        match = lookup.loc[uid] if uid in lookup.index else None
        status = "unresolved_coarse_or_capital_proxy"
        confidence = "unresolved"
        assigned_geo_uid = None
        assigned_name = None
        candidates_count = int(point_counts.get(uid, 0))
        if precision in (1, 2, 3):
            if match is None:
                status = "unresolved_point_outside_or_ambiguous"
            elif str(match.country_iso3) != str(row.recipients_iso3):
                status = "unresolved_cross_country_point"
            else:
                assigned_geo_uid = match.geo_uid
                assigned_name = match.NAME_2
                if precision == 1:
                    status = "exact_named_place_point"
                    confidence = "exact_named_place"
                elif precision == 2:
                    status = "near_point_proxy"
                    confidence = "proxy"
                elif _area_name_key(row.place_name) == _area_name_key(match.NAME_2):
                    status = "adm2_centroid_name_verified"
                    confidence = "verified_adm2"
                else:
                    status = "adm2_centroid_name_unverified"
                    confidence = "proxy"
        rows.append(
            {
                "project_location_id": uid,
                "geo_uid": assigned_geo_uid,
                "relation_status": status,
                "geographic_confidence": confidence,
                "broad_source_point_assigned": assigned_geo_uid is not None,
                "high_confidence_assigned": confidence in ("exact_named_place", "verified_adm2"),
                "candidate_count": candidates_count,
                "gadm_name2_at_point": assigned_name,
            }
        )
    metadata = pd.DataFrame(rows)
    relation = result.merge(metadata, on="project_location_id", validate="one_to_one")
    if len(relation) != len(location_scope):
        raise RuntimeError("WB source location rows were lost or duplicated by geography relation")
    if relation.high_confidence_assigned.sum() > relation.broad_source_point_assigned.sum():
        raise AssertionError("high-confidence location is missing from broad geography assignment")
    if not bool(relation.loc[relation.geo_uid.notna(), "geo_uid"].isin(geography.geo_uid).all()):
        raise AssertionError("WB output contains unknown GADM geo_uid")
    if (
        relation.loc[relation.relation_status.eq("unresolved_coarse_or_capital_proxy"), "geo_uid"]
        .notna()
        .any()
    ):
        raise AssertionError("coarse WB geocodes must never be assigned precise ADM2 membership")

    status_counts = {str(k): int(v) for k, v in relation.relation_status.value_counts().items()}
    qa = (
        QAResult(
            check_id="aiddata_wb.geography.precision_aware",
            state="YELLOW" if any(x.startswith("unresolved_") for x in status_counts) else "GREEN",
            message=(
                "exact named points, near-point proxies and ADM2 centroids retain distinct status; "
                "coarse/capital locations are never forced into ADM2"
            ),
            metrics={
                "source_project_locations": len(location_scope),
                "assigned_broad": int(relation.broad_source_point_assigned.sum()),
                "assigned_high_confidence": int(relation.high_confidence_assigned.sum()),
                **{f"status_{key}": count for key, count in status_counts.items()},
            },
        ),
        QAResult(
            check_id="aiddata_wb.period.approval",
            state="GREEN",
            message="World Bank source approval event assigned using shared PeriodIndex",
            metrics={
                "target_country_projects": len(periods),
                "unique_project_ids": periods.project_id.nunique(),
            },
        ),
    )
    return WBGeocodedRelations(geography=relation, periods=periods, qa=qa)


def materialize_wb_geocoded_relations(
    *,
    wb_silver_dataset_refs: tuple[DatasetRef, DatasetRef],
    gadm_dataset_ref: DatasetRef,
    wb_source_snapshot_id: str,
    projects: pd.DataFrame,
    locations: pd.DataFrame,
    geography: gpd.GeoDataFrame,
    adm2_names_file: str | Path,
    geography_spec: GeographySpec,
    period_scheme: PeriodScheme,
    data_root: DataRoot,
    run_id: str,
    code_commit: str | None = None,
) -> WBGeocodedRelationsMaterialization:
    """Publish governed source facts; no treatment or money allocation."""
    name_file = Path(adm2_names_file).expanduser().resolve()
    name_snapshot: SourceSnapshotRef = register_external_snapshot(
        "gadm41_admin2_name_crosswalk", "4.1", [name_file]
    )
    names = pd.read_csv(name_file, dtype="string")
    relation = build_wb_geocoded_relations(
        projects, locations, geography, names, period_scheme=period_scheme
    )
    name_digest = sha256_file(name_file)[:12]
    geo_token = hashlib.sha256(geography_spec.id.encode()).hexdigest()[:12]
    token = f"{wb_source_snapshot_id}--geo-{geo_token}--names-{name_digest}"
    refs = {
        "geography": DatasetRef(
            dataset_id="investments.aiddata_worldbank_geocoded.project_geography",
            version=token,
            schema_version="aiddata-wb-geocoded-geography-relation-v1",
            layer=DataLayer.SILVER,
            authority=AuthorityLevel.L3_REBUILT,
            grain=GrainSpec(keys=("project_location_id",)),
            geography=geography_spec,
        ),
        "periods": DatasetRef(
            dataset_id="investments.aiddata_worldbank_geocoded.project_period",
            version=f"{wb_source_snapshot_id}--{period_scheme.id}",
            schema_version="aiddata-wb-geocoded-approval-period-v1",
            layer=DataLayer.SILVER,
            authority=AuthorityLevel.L3_REBUILT,
            grain=GrainSpec(keys=("project_id",)),
            period_scheme=period_scheme,
        ),
    }
    outputs = {
        "geography": data_root.silver("investments", "aiddata_wb_project_geography", token)
        / "project_geography.parquet",
        "periods": data_root.silver(
            "investments",
            "aiddata_wb_project_period",
            f"{wb_source_snapshot_id}--{period_scheme.id}",
        )
        / "project_period.parquet",
    }
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        inputs=(*wb_silver_dataset_refs, gadm_dataset_ref, name_snapshot),
        outputs=tuple(
            FileMaterialization(
                dataset=refs[k],
                destination_base=path.parent,
                relative_path=path.name,
                writer=lambda p, df=df: df.to_parquet(p, index=False),
            )
            for k, path, df in (
                ("geography", outputs["geography"], relation.geography),
                ("periods", outputs["periods"], relation.periods),
            )
        ),
        parameters={
            "date_type": "board_approval",
            "period_scheme": period_scheme.model_dump(mode="json"),
            "geography": geography_spec.model_dump(mode="json"),
            "precision_policy": "exact_named_point | near_proxy | matched_admin2_name | unverified_admin_centroid_proxy | coarse_unresolved",
            "name_source_sha256": sha256_file(name_file),
            "source_dollar_allocation": None,
            "no_project_eligibility_or_treatment": True,
        },
        qa=relation.qa,
        code_commit=code_commit,
    )
    persist_run_artifact(
        data_root,
        run_id,
        "qa/wb_source_location_resolution.json",
        json.dumps(
            {
                "source_locations": len(relation.geography),
                "relation_status_counts": relation.geography.relation_status.value_counts().to_dict(),
                "high_confidence_count": int(relation.geography.high_confidence_assigned.sum()),
                "broad_source_point_count": int(
                    relation.geography.broad_source_point_assigned.sum()
                ),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
    )
    datasets = {
        k: next(x for x in manifest.outputs if x.dataset_id == refs[k].dataset_id) for k in refs
    }
    return WBGeocodedRelationsMaterialization(
        manifest=manifest, datasets=datasets, paths=outputs, geography_qa=relation.qa
    )
