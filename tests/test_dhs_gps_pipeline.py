from pathlib import Path

import geopandas as gpd
from empirical_contracts import (
    AuthorityLevel,
    DataLayer,
    DatasetRef,
    GeographySpec,
    GrainSpec,
)
from shapely.geometry import Point, Polygon
from spatial_foundation import DataRoot

from fcv_empirical.surveys import SurveyCatalogEntry
from fcv_empirical.surveys.dhs_gps import DhsDisplacementPolicy
from fcv_empirical.surveys.dhs_gps_pipeline import (
    materialize_dhs_gps_silver,
    materialize_dhs_reported_coordinate_membership,
)


def _survey() -> SurveyCatalogEntry:
    return SurveyCatalogEntry(
        survey_id="dhs-tst-2020",
        source_family="dhs",
        country_iso3="TST",
        survey_year=2020,
        release="survey-release",
    )


def _raw_gps() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "DHSCLUST": ["1"],
            "DHSID": ["TST2020000001"],
            "SURVEYID": ["TST2020"],
            "LATNUM": [0.5],
            "LONGNUM": [0.5],
            "URBAN_RURA": ["U"],
        },
        geometry=[Point(0.5, 0.5)],
        crs="EPSG:4326",
    )


def _geography() -> GeographySpec:
    return GeographySpec(provider="gadm", version="4.1", scheme="admin", level="1")


def _polygons() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"geo_uid": ["gadm:4.1:adm1:TST.1_1"], "geometry_role": ["analytical"]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
        crs="EPSG:4326",
    )


def test_materialized_gps_silver_and_membership_keep_explicit_lineage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "synthetic-gps-source.shp"
    source.write_bytes(b"synthetic fixture only; not DHS data")
    monkeypatch.setattr(
        "fcv_empirical.surveys.dhs_gps_pipeline.gpd.read_file",
        lambda _path: _raw_gps(),
    )
    root = DataRoot.from_path(tmp_path / "data")
    policy = DhsDisplacementPolicy(
        coordinate_is_displaced=True,
        policy_class="synthetic-test-policy",
        policy_source="synthetic fixture metadata",
    )

    snapshot, silver, silver_manifest, silver_dataset, silver_path = materialize_dhs_gps_silver(
        source_path=source,
        source_paths=(source,),
        survey=_survey(),
        release="gps-release",
        displacement_policy=policy,
        data_root=root,
        run_id="dhs-gps-silver-test",
        code_commit="test-commit",
    )

    assert snapshot.storage_mode == "external_immutable"
    assert Path(snapshot.files[0].path) == source.resolve()
    assert snapshot.files[0].sha256
    assert silver_path.is_file()
    assert silver_manifest.inputs == (snapshot,)
    assert silver_manifest.outputs[0].content_sha256
    assert silver_manifest.code_commit == "test-commit"
    assert silver.frame.loc[0, "source__geometry"] == "POINT (0.5 0.5)"

    geography = _geography()
    geography_dataset = DatasetRef(
        dataset_id="geography.gadm.adm1",
        version="4.1-test",
        schema_version="test-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("geo_uid",)),
        geography=geography,
    )
    result, membership_manifest, membership_dataset, membership_path = (
        materialize_dhs_reported_coordinate_membership(
            snapshot=snapshot,
            silver=silver.frame,
            silver_dataset=silver_dataset,
            polygons=_polygons(),
            geography=geography,
            geography_dataset=geography_dataset,
            data_root=root,
            run_id="dhs-gps-membership-test",
            code_commit="test-commit",
        )
    )

    assert membership_path.is_file()
    assert membership_dataset.content_sha256
    assert membership_manifest.inputs == (silver_dataset, geography_dataset)
    assert membership_manifest.parameters["membership_semantics"] == (
        "reported_coordinate_membership"
    )
    assert membership_manifest.parameters["true_cluster_location_claim"] is False
    assert result.frame.loc[0, "assignment_status"] == "matched_unique"
    assert bool(result.frame.loc[0, "coordinate_is_displaced"]) is True
