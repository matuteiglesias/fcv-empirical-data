from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd
from empirical_contracts import GeographySpec, QAResult
from spatial_foundation.geography import MembershipStatus, assign_points

from .dhs_gps_models import (
    POSSIBLE_GEOGRAPHY_UNDER_DISPLACEMENT,
    REPORTED_COORDINATE_MEMBERSHIP,
    DhsReportedMembershipResult,
)
from .geography import SurveyGeographyLink


def _uncertainty_status(row: Any) -> str:
    if row.coordinate_validity != "valid":
        return "reported_coordinate_unusable"
    if bool(row.coordinate_is_displaced):
        return "reported_coordinate_displaced"
    return "reported_coordinate_not_displaced"


def assign_dhs_reported_coordinate_membership(
    silver: pd.DataFrame,
    polygons: gpd.GeoDataFrame,
    *,
    geography: GeographySpec,
    polygon_id_col: str = "geo_uid",
) -> DhsReportedMembershipResult:
    """Assign only the reported DHS point to analytical geography.

    The result is deliberately named ``reported_coordinate_membership``. It is not a claim about
    the true cluster location and it does not enumerate locations allowed by a displacement radius.
    """
    required = {
        "cluster_row_id",
        "survey_id",
        "cluster_id",
        "reported_latitude",
        "reported_longitude",
        "coordinate_validity",
        "coordinate_is_displaced",
        "displacement_policy_class",
        "displacement_max_km",
        "displacement_urban_max_km",
        "displacement_rural_max_km",
        "exceptional_rural_displacement_possible",
        "exceptional_rural_displacement_max_km",
        "displacement_policy_source",
        "displacement_urban_rural_rule",
    }
    missing = sorted(required - set(silver.columns))
    if missing:
        raise ValueError(f"DHS GPS Silver missing required columns: {', '.join(missing)}")
    if silver["cluster_row_id"].duplicated().any():
        raise ValueError("cluster_row_id must be unique before spatial assignment")

    valid_mask = silver["coordinate_validity"].astype("string").eq("valid")
    latitude = pd.to_numeric(silver["reported_latitude"], errors="coerce")
    longitude = pd.to_numeric(silver["reported_longitude"], errors="coerce")
    valid_mask &= latitude.notna() & longitude.notna()

    pieces: list[pd.DataFrame] = []
    if valid_mask.any():
        point_rows = silver.loc[valid_mask, ["cluster_row_id"]].copy()
        points = gpd.GeoDataFrame(
            point_rows,
            geometry=gpd.points_from_xy(longitude.loc[valid_mask], latitude.loc[valid_mask]),
            crs="EPSG:4326",
        )
        candidates, _audit = assign_points(
            points,
            polygons,
            point_id_col="cluster_row_id",
            polygon_id_col=polygon_id_col,
        )
        candidates = candidates.rename(columns={polygon_id_col: "geo_uid"})
        pieces.append(candidates)

    invalid_rows = silver.loc[~valid_mask, ["cluster_row_id"]].copy()
    if len(invalid_rows):
        invalid_rows["geo_uid"] = pd.NA
        invalid_rows["candidate_count"] = 0
        invalid_rows["assignment_status"] = MembershipStatus.INVALID_POINT.value
        pieces.append(invalid_rows)

    base_columns = ["cluster_row_id", "geo_uid", "candidate_count", "assignment_status"]
    if pieces:
        relation = pd.concat(pieces, ignore_index=True)[base_columns]
    else:
        relation = pd.DataFrame(columns=base_columns)

    relation = relation.merge(
        silver[
            [
                "cluster_row_id",
                "survey_id",
                "cluster_id",
                "coordinate_validity",
                "coordinate_is_displaced",
                "displacement_policy_class",
                "displacement_max_km",
                "displacement_urban_max_km",
                "displacement_rural_max_km",
                "exceptional_rural_displacement_possible",
                "exceptional_rural_displacement_max_km",
                "displacement_policy_source",
                "displacement_urban_rural_rule",
            ]
        ],
        on="cluster_row_id",
        how="left",
        validate="many_to_one",
    )
    relation["membership_semantics"] = REPORTED_COORDINATE_MEMBERSHIP
    relation["assignment_method"] = "spatial_foundation.geography.assign_points"
    relation["geography_id"] = geography.id
    relation["uncertainty_status"] = [
        _uncertainty_status(row) for row in relation.itertuples(index=False)
    ]
    relation["membership_row_id"] = [
        f"{row.cluster_row_id}:{position:03d}"
        for position, row in enumerate(relation.itertuples(index=False))
    ]

    links: list[SurveyGeographyLink] = []
    for row in relation.itertuples(index=False):
        geo_uid = None if pd.isna(row.geo_uid) else str(row.geo_uid)
        metadata = {
            "membership_semantics": REPORTED_COORDINATE_MEMBERSHIP,
            "coordinate_is_displaced": bool(row.coordinate_is_displaced),
            "uncertainty_status": str(row.uncertainty_status),
            "candidate_count": int(row.candidate_count),
            "displacement_policy_class": row.displacement_policy_class,
            "displacement_max_km": row.displacement_max_km,
            "displacement_urban_max_km": row.displacement_urban_max_km,
            "displacement_rural_max_km": row.displacement_rural_max_km,
            "exceptional_rural_displacement_possible": (
                row.exceptional_rural_displacement_possible
            ),
            "exceptional_rural_displacement_max_km": (
                row.exceptional_rural_displacement_max_km
            ),
        }
        links.append(
            SurveyGeographyLink(
                survey_id=str(row.survey_id),
                source_object_id=(
                    str(row.cluster_id)
                    if not pd.isna(row.cluster_id)
                    else str(row.cluster_row_id)
                ),
                source_object_type="survey_cluster",
                geography=geography,
                geo_uid=geo_uid,
                assignment_status=str(row.assignment_status),
                assignment_method=str(row.assignment_method),
                uncertainty_metadata=metadata,
            )
        )

    status_by_cluster_row = relation.drop_duplicates("cluster_row_id")[
        ["cluster_row_id", "assignment_status"]
    ]
    status_counts = status_by_cluster_row["assignment_status"].value_counts()
    matched_unique = int(status_counts.get(MembershipStatus.MATCHED_UNIQUE.value, 0))
    ambiguous = int(status_counts.get(MembershipStatus.AMBIGUOUS_MULTIPLE.value, 0))
    outside = int(status_counts.get(MembershipStatus.UNMATCHED_OUTSIDE.value, 0))
    invalid = int(status_counts.get(MembershipStatus.INVALID_POINT.value, 0))

    qa = (
        QAResult(
            check_id="dhs.gps.geography.reported_membership",
            state="GREEN" if len(status_by_cluster_row) == len(silver) else "RED",
            message="every GPS Silver row has an explicit reported-coordinate membership status",
            metrics={
                "cluster_rows": len(silver),
                "matched_unique": matched_unique,
                "ambiguous": ambiguous,
                "outside": outside,
                "invalid_coordinate_rows": invalid,
                "membership_semantics": REPORTED_COORDINATE_MEMBERSHIP,
            },
        ),
        QAResult(
            check_id="dhs.gps.geography.ambiguity",
            state="GREEN" if ambiguous == 0 else "YELLOW",
            message="boundary/overlap ambiguity remains explicit candidate rows",
            metrics={"ambiguous_cluster_rows": ambiguous},
        ),
        QAResult(
            check_id="dhs.gps.geography.displacement",
            state="GREEN",
            message="reported-coordinate membership does not claim true cluster location",
            metrics={
                "displaced_cluster_rows": int(
                    silver["coordinate_is_displaced"].astype("boolean").fillna(False).sum()
                ),
                "uncertainty_product_implemented": False,
                "future_uncertainty_semantics": POSSIBLE_GEOGRAPHY_UNDER_DISPLACEMENT,
            },
        ),
    )
    return DhsReportedMembershipResult(frame=relation, links=tuple(links), qa=qa)
