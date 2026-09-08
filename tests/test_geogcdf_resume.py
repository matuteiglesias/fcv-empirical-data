from pathlib import Path

from empirical_contracts import (
    AuthorityLevel,
    DataLayer,
    DatasetRef,
    GeographySpec,
    GrainSpec,
    PeriodScheme,
)
from shapely.geometry import Point
from spatial_foundation import DataRoot

from fcv_empirical.investments.geogcdf import register_geogcdf_snapshot
from fcv_empirical.investments.geogcdf_measurements import (
    assign_geogcdf_periods,
    relate_geogcdf_geography,
)
from fcv_empirical.investments.geogcdf_resume import (
    materialize_geogcdf_gold_from_governed_relations,
)
from tests.test_geogcdf_measurements import _geography, _silver


def _dataset_refs(geography, scheme):
    silver = DatasetRef(
        dataset_id="investments.aiddata_geogcdf.projects",
        version="fixture-silver",
        schema_version="v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("project_geometry_row_id",)),
        content_sha256="a" * 64,
    )
    geography_dataset = DatasetRef(
        dataset_id="gadm_native_adm2",
        version="fixture-geography",
        schema_version="v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("geo_uid",)),
        geography=geography,
        content_sha256="b" * 64,
    )
    geography_relation = DatasetRef(
        dataset_id="investments.aiddata_geogcdf.project_geography",
        version="fixture-relation",
        schema_version="v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("project_geometry_row_id", "geo_uid")),
        geography=geography,
        content_sha256="c" * 64,
    )
    period_relation = DatasetRef(
        dataset_id="investments.aiddata_geogcdf.project_period",
        version="fixture-period",
        schema_version="v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("project_geometry_row_id", "project_date_type")),
        period_scheme=scheme,
        content_sha256="d" * 64,
    )
    return silver, geography_dataset, geography_relation, period_relation


def test_gold_only_resume_reuses_hash_bound_relations_without_republishing_them(tmp_path: Path):
    source = tmp_path / "source.gpkg"
    source.write_bytes(b"fixture-source")
    snapshot = register_geogcdf_snapshot(source, release="v3.0.1")

    silver = _silver().copy()
    # Exact boundary between EXA.1 and EXA.2: deliberately unresolved point.
    silver.loc[1, "geometry"] = Point(1, 0.5)
    units = _geography()
    geography = GeographySpec(provider="gadm", version="4.1", scheme="native", level="adm2")
    scheme = PeriodScheme(width_years=2, anchor_year=2001)
    geography_relation_frame = relate_geogcdf_geography(silver, units).frame
    period_relation_frame = assign_geogcdf_periods(silver, scheme=scheme).frame
    silver_ref, geography_ref, geography_relation_ref, period_relation_ref = _dataset_refs(
        geography, scheme
    )

    root = DataRoot.from_path(tmp_path / "data")
    result, manifest, contract, gold_ref, gold_path = (
        materialize_geogcdf_gold_from_governed_relations(
            snapshot=snapshot,
            silver=silver,
            silver_dataset=silver_ref,
            geography_relation=geography_relation_frame,
            geography_relation_dataset=geography_relation_ref,
            period_relation=period_relation_frame,
            period_relation_dataset=period_relation_ref,
            geography_units=units,
            geography=geography,
            geography_dataset=geography_ref,
            period_scheme=scheme,
            data_root=root,
            run_id="gold-only-resume",
            require_complete_resolution=False,
        )
    )

    assert gold_path.exists()
    assert gold_ref.content_sha256 is not None
    assert result.resolution_policy == "exclude_unresolved"
    assert result.excluded_unresolved_project_count == 1
    assert manifest.parameters["reused_governed_relations"] is True
    assert geography_relation_ref in manifest.inputs
    assert period_relation_ref in manifest.inputs
    assert {output.dataset_id for output in manifest.outputs} == {
        "investments.aiddata_geogcdf.commitment_area_period"
    }
    assert contract.source_dataset == silver_ref
    assert contract.parameters["resolution_policy"] == "exclude_unresolved"
    assert not any(
        path.name in {"project_geography.parquet", "project_period.parquet"}
        for path in (tmp_path / "data").rglob("*.parquet")
    )


def test_gold_only_resume_requires_hash_bound_relation_refs(tmp_path: Path):
    source = tmp_path / "source.gpkg"
    source.write_bytes(b"fixture-source")
    snapshot = register_geogcdf_snapshot(source, release="v3.0.1")
    silver = _silver()
    units = _geography()
    geography = GeographySpec(provider="gadm", version="4.1", scheme="native", level="adm2")
    scheme = PeriodScheme(width_years=2, anchor_year=2001)
    geography_relation_frame = relate_geogcdf_geography(silver, units).frame
    period_relation_frame = assign_geogcdf_periods(silver, scheme=scheme).frame
    silver_ref, geography_ref, geography_relation_ref, period_relation_ref = _dataset_refs(
        geography, scheme
    )
    unbound = geography_relation_ref.model_copy(update={"content_sha256": None})

    try:
        materialize_geogcdf_gold_from_governed_relations(
            snapshot=snapshot,
            silver=silver,
            silver_dataset=silver_ref,
            geography_relation=geography_relation_frame,
            geography_relation_dataset=unbound,
            period_relation=period_relation_frame,
            period_relation_dataset=period_relation_ref,
            geography_units=units,
            geography=geography,
            geography_dataset=geography_ref,
            period_scheme=scheme,
            data_root=DataRoot.from_path(tmp_path / "data"),
            run_id="must-fail",
        )
    except ValueError as error:
        assert "content SHA-256" in str(error)
    else:
        raise AssertionError("unhashed governed relation must fail closed")
