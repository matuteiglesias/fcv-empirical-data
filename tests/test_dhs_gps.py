import geopandas as gpd
import pandas as pd
import pytest
from empirical_contracts import GeographySpec, SourceFileRef, SourceSnapshotRef
from shapely.geometry import Polygon
from spatial_foundation.geography import MembershipStatus

from fcv_empirical.surveys import SurveyCatalogEntry
from fcv_empirical.surveys.dhs_gps import (
    REPORTED_COORDINATE_MEMBERSHIP,
    DhsDisplacementPolicy,
    assign_dhs_reported_coordinate_membership,
    normalize_dhs_gps_clusters,
    validate_dhs_gps_linkage,
)


def _survey() -> SurveyCatalogEntry:
    return SurveyCatalogEntry(
        survey_id="dhs-tst-2020",
        source_family="dhs",
        country_iso3="TST",
        survey_year=2020,
        release="survey-release",
    )


def _snapshot() -> SourceSnapshotRef:
    return SourceSnapshotRef(
        source="dhs_gps",
        release="gps-release",
        snapshot_id="snapshot-dhs-gps-test",
        files=(
            SourceFileRef(
                path="external/test_ge.shp",
                sha256="a" * 64,
                size_bytes=100,
            ),
        ),
    )


def _policy() -> DhsDisplacementPolicy:
    return DhsDisplacementPolicy(
        coordinate_is_displaced=True,
        policy_class="documented-public-coordinate-displacement",
        policy_source="synthetic fixture policy",
    )


def _raw(rows: list[tuple[str, str, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "DHSCLUST": [row[0] for row in rows],
            "DHSID": [row[1] for row in rows],
            "LATNUM": [row[2] for row in rows],
            "LONGNUM": [row[3] for row in rows],
            "URBAN_RURA": ["U"] * len(rows),
        }
    )


def _geography() -> GeographySpec:
    return GeographySpec(provider="gadm", version="4.1", scheme="admin", level="2")


def _polygons(*, role: str = "analytical") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "geo_uid": ["left", "right"],
            "geometry_role": [role, role],
        },
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
        ],
        crs="EPSG:4326",
    )


def _silver(rows: list[tuple[str, str, float, float]]) -> pd.DataFrame:
    return normalize_dhs_gps_clusters(
        _raw(rows),
        survey=_survey(),
        snapshot=_snapshot(),
        displacement_policy=_policy(),
    ).frame


def test_cluster_silver_preserves_source_and_displacement_metadata() -> None:
    result = normalize_dhs_gps_clusters(
        _raw([("1", "TST2020000001", 0.5, 0.5)]),
        survey=_survey(),
        snapshot=_snapshot(),
        displacement_policy=_policy(),
    )

    row = result.frame.iloc[0]
    assert row["survey_id"] == "dhs-tst-2020"
    assert row["cluster_id"] == "1"
    assert row["dhsid"] == "TST2020000001"
    assert row["reported_latitude"] == 0.5
    assert row["reported_longitude"] == 0.5
    assert row["coordinate_validity"] == "valid"
    assert bool(row["coordinate_is_displaced"]) is True
    assert pd.isna(row["displacement_max_km"])
    assert row["source__LATNUM"] == 0.5
    assert row["source__LONGNUM"] == 0.5


def test_unique_interior_cluster_has_reported_coordinate_membership() -> None:
    result = assign_dhs_reported_coordinate_membership(
        _silver([("1", "TST2020000001", 0.5, 0.5)]),
        _polygons(),
        geography=_geography(),
    )

    assert len(result.frame) == 1
    row = result.frame.iloc[0]
    assert row["geo_uid"] == "left"
    assert row["assignment_status"] == MembershipStatus.MATCHED_UNIQUE.value
    assert row["membership_semantics"] == REPORTED_COORDINATE_MEMBERSHIP
    assert bool(row["coordinate_is_displaced"]) is True
    assert row["uncertainty_status"] == "reported_coordinate_displaced"


def test_exact_boundary_cluster_remains_ambiguous() -> None:
    result = assign_dhs_reported_coordinate_membership(
        _silver([("2", "TST2020000002", 0.5, 1.0)]),
        _polygons(),
        geography=_geography(),
    )

    assert len(result.frame) == 2
    assert set(result.frame["geo_uid"]) == {"left", "right"}
    assert set(result.frame["assignment_status"]) == {
        MembershipStatus.AMBIGUOUS_MULTIPLE.value
    }
    assert all(
        link.assignment_status is MembershipStatus.AMBIGUOUS_MULTIPLE for link in result.links
    )


def test_outside_cluster_remains_outside() -> None:
    result = assign_dhs_reported_coordinate_membership(
        _silver([("3", "TST2020000003", 0.5, 3.0)]),
        _polygons(),
        geography=_geography(),
    )

    row = result.frame.iloc[0]
    assert pd.isna(row["geo_uid"])
    assert row["assignment_status"] == MembershipStatus.UNMATCHED_OUTSIDE.value


def test_two_clusters_in_same_adm_do_not_collapse() -> None:
    result = assign_dhs_reported_coordinate_membership(
        _silver(
            [
                ("4", "TST2020000004", 0.2, 0.2),
                ("5", "TST2020000005", 0.8, 0.8),
            ]
        ),
        _polygons(),
        geography=_geography(),
    )

    assert len(result.frame) == 2
    assert set(result.frame["cluster_id"]) == {"4", "5"}
    assert set(result.frame["geo_uid"]) == {"left"}
    assert result.frame["membership_row_id"].nunique() == 2


def test_unmatched_survey_gps_clusters_are_visible_in_linkage_audit() -> None:
    gps = _silver(
        [
            ("1", "TST2020000001", 0.5, 0.5),
            ("3", "TST2020000003", 0.5, 1.5),
        ]
    )
    survey_clusters = pd.DataFrame(
        {
            "survey_id": ["dhs-tst-2020", "dhs-tst-2020"],
            "cluster_id": ["1", "2"],
        }
    )

    audit = validate_dhs_gps_linkage(
        survey_clusters,
        gps,
        survey=_survey(),
    )

    issues = set(zip(audit.discrepancies["issue"], audit.discrepancies["cluster_id"]))
    assert ("survey_cluster_absent_gps", "2") in issues
    assert ("gps_cluster_absent_survey", "3") in issues


def test_duplicate_and_conflicting_survey_identity_are_not_clean_linkage() -> None:
    gps = _silver(
        [
            ("1", "TST2020000001", 0.5, 0.5),
            ("1", "TST2020000001B", 0.6, 0.6),
        ]
    ).copy()
    gps.loc[gps.index[1], "survey_id"] = "dhs-other-2020"
    survey_clusters = pd.DataFrame(
        {"survey_id": ["dhs-tst-2020"], "cluster_id": ["1"]}
    )

    audit = validate_dhs_gps_linkage(
        survey_clusters,
        gps,
        survey=_survey(),
    )

    assert "duplicate_gps_cluster" in set(audit.discrepancies["issue"])
    assert "conflicting_gps_survey_id" in set(audit.discrepancies["issue"])
    identity_qa = next(item for item in audit.qa if item.check_id == "dhs.gps.linkage.identity")
    assert identity_qa.state == "RED"


def test_display_geography_is_rejected_by_spatial_foundation() -> None:
    with pytest.raises(ValueError, match="analytical geometry"):
        assign_dhs_reported_coordinate_membership(
            _silver([("6", "TST2020000006", 0.5, 0.5)]),
            _polygons(role="display"),
            geography=_geography(),
        )


def test_invalid_and_placeholder_coordinates_skip_point_in_polygon() -> None:
    raw = _raw(
        [
            ("7", "TST2020000007", 95.0, 0.5),
            ("8", "TST2020000008", 0.0, 0.0),
        ]
    )
    silver_result = normalize_dhs_gps_clusters(
        raw,
        survey=_survey(),
        snapshot=_snapshot(),
        displacement_policy=_policy(),
        placeholder_coordinates={(0.0, 0.0)},
    )
    relation = assign_dhs_reported_coordinate_membership(
        silver_result.frame,
        _polygons(),
        geography=_geography(),
    )

    assert set(silver_result.frame["coordinate_validity"]) == {
        "invalid",
        "source_placeholder",
    }
    assert set(relation.frame["assignment_status"]) == {MembershipStatus.INVALID_POINT.value}
    assert relation.frame["geo_uid"].isna().all()


def test_relation_never_claims_reported_coordinate_is_true_cluster_location() -> None:
    result = assign_dhs_reported_coordinate_membership(
        _silver([("9", "TST2020000009", 0.5, 0.5)]),
        _polygons(),
        geography=_geography(),
    )

    assert set(result.frame["membership_semantics"]) == {REPORTED_COORDINATE_MEMBERSHIP}
    assert "true_cluster_location" not in result.frame.columns
    assert "true_location" not in result.frame.columns
    assert result.links[0].uncertainty_metadata["coordinate_is_displaced"] is True
