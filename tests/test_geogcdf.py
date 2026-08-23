from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon

from fcv_empirical.investments.geogcdf import (
    materialize_geogcdf_silver,
    normalize_geogcdf_projects,
    register_geogcdf_snapshot,
)
from spatial_foundation import DataRoot


def _source_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "id": [101, 102],
            "Recipient": ["Example A", "Example A"],
            "Recipient.ISO-3": ["EXA", "EXA"],
            "Title": ["Road", "Clinic"],
            "Amount.(Constant.USD.2021)": ["1000", "not-a-number"],
            "Status": ["Completion", "Implementation"],
            "Sector.Name": ["TRANSPORT", "HEALTH"],
            "Infrastructure": ["Yes", "Yes"],
            "Commitment.Year": [2011, 2012],
            "Implementation.Start.Year": [2011, 2013],
            "Completion.Year": [2012, None],
            "Commitment.Date.(MM/DD/YYYY)": ["01/01/2011", "05/20/2012"],
            "Actual.Implementation.Start.Date.(MM/DD/YYYY)": [
                "10/09/2011",
                "01/03/2013",
            ],
            "Actual.Completion.Date.(MM/DD/YYYY)": ["05/21/2012", None],
            "feature_count": [1, 1],
            "osm_links": ["https://www.openstreetmap.org/way/1", None],
            "osm_precision_list": ["precise", "approximate"],
        },
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Point(2, 2),
        ],
        crs="EPSG:4326",
    )


def test_geogcdf_silver_preserves_source_rows_geometry_and_native_fields(tmp_path: Path) -> None:
    source = tmp_path / "geogcdf.geojson"
    _source_frame().to_file(source, driver="GeoJSON")
    snapshot = register_geogcdf_snapshot(source, release="v3.0.1")
    raw = gpd.read_file(source)

    result = normalize_geogcdf_projects(raw, snapshot=snapshot)

    assert len(result.frame) == 2
    assert result.frame["source_project_id"].tolist() == ["101", "102"]
    assert result.frame.geometry.geom_type.tolist() == ["Polygon", "Point"]
    assert result.frame.loc[0, "reported_amount_constant_usd_2021"] == 1000
    assert pd.isna(result.frame.loc[1, "reported_amount_constant_usd_2021"])
    assert result.frame.loc[1, "source__Amount.(Constant.USD.2021)"] == "not-a-number"
    assert result.frame.loc[0, "commitment_year"] == 2011
    assert result.frame.loc[0, "commitment_date"].year == 2011
    assert "source__Sector.Name" in result.frame.columns

    amount_qa = next(item for item in result.qa if item.check_id == "geogcdf.projects.amount_parse")
    assert amount_qa.state == "YELLOW"
    assert amount_qa.metrics["amount_parse_failures"] == 1


def test_geogcdf_duplicate_project_ids_are_visible_not_deduplicated(tmp_path: Path) -> None:
    raw = _source_frame().copy()
    raw.loc[1, "id"] = 101
    source = tmp_path / "geogcdf.geojson"
    raw.to_file(source, driver="GeoJSON")
    snapshot = register_geogcdf_snapshot(source, release="v3.0.1")

    result = normalize_geogcdf_projects(gpd.read_file(source), snapshot=snapshot)

    assert len(result.frame) == 2
    source_id_qa = next(item for item in result.qa if item.check_id == "geogcdf.projects.source_id")
    assert source_id_qa.state == "RED"
    assert source_id_qa.metrics["duplicate_source_id_rows"] == 2


def test_materialized_geogcdf_silver_keeps_external_snapshot_hash_in_lineage(tmp_path: Path) -> None:
    source = tmp_path / "geogcdf.geojson"
    _source_frame().to_file(source, driver="GeoJSON")
    root = DataRoot.from_path(tmp_path / "data")

    snapshot, _silver, manifest, dataset, output = materialize_geogcdf_silver(
        source_path=source,
        data_root=root,
        run_id="geogcdf-silver-test",
        release="v3.0.1",
    )

    assert output.exists()
    assert dataset.content_sha256 is not None
    assert manifest.inputs == (snapshot,)
    assert manifest.inputs[0].files[0].sha256 == snapshot.files[0].sha256
    assert manifest.parameters["amount_allocation"] is None


def test_registered_geogcdf_snapshot_detects_source_mutation_before_materialization(
    tmp_path: Path,
) -> None:
    source = tmp_path / "geogcdf.geojson"
    _source_frame().to_file(source, driver="GeoJSON")
    snapshot = register_geogcdf_snapshot(source, release="v3.0.1")
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    root = DataRoot.from_path(tmp_path / "data")
    try:
        materialize_geogcdf_silver(
            source_path=source,
            data_root=root,
            run_id="mutated-source",
            release="v3.0.1",
            source_snapshot=snapshot,
        )
    except ValueError as error:
        assert "changed after snapshot registration" in str(error)
    else:
        raise AssertionError("mutated external source must fail closed")
