import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from empirical_contracts import (
    AuthorityLevel,
    DataLayer,
    DatasetRef,
    GeographySpec,
    GrainSpec,
    PeriodScheme,
)
from shapely.geometry import Polygon
from spatial_foundation import DataRoot

from fcv_empirical.violence.acled_pipeline import materialize_acled_vertical
from fcv_empirical.violence.acled_parity import compare_acled_legacy


def _source(path: Path) -> None:
    pd.DataFrame(
        {
            "EVENT_ID_CNTY": ["A1", "A2", "A3"],
            "EVENT_DATE": ["2001-01-01", "2001-03-01", "2001-04-01"],
            "COUNTRY": ["X", "X", "X"],
            "LATITUDE": [0.25, 0.50, 0.75],
            "LONGITUDE": [0.25, 0.50, 1.00],
            "EVENT_TYPE": ["Protests", "Violence against civilians", "Riots"],
            "SUB_EVENT_TYPE": ["Peaceful protest", "Attack", "Mob violence"],
            "FATALITIES": [0, 2, 1],
            "GEO_PRECISION": [2, 1, 1],
            "TIME_PRECISION": [1, 1, 1],
        }
    ).to_csv(path, index=False)


def _polygons() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"geo_uid": ["g1", "g2"], "geometry_role": ["analytical", "analytical"]},
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
        ],
        crs="EPSG:4326",
    )


def _geography_ref(geography: GeographySpec) -> DatasetRef:
    return DatasetRef(
        dataset_id="geography.gadm.units",
        version="fixture",
        schema_version="fixture-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("geo_uid",)),
        geography=geography,
        content_sha256="a" * 64,
    )


def test_vertical_materializes_two_step_lineage_and_contracts(tmp_path: Path):
    source = tmp_path / "acled.csv"
    _source(source)
    data_root = DataRoot.from_path(tmp_path / "data")
    geography = GeographySpec(provider="gadm", version="4.1", scheme="admin", level="2")

    result = materialize_acled_vertical(
        source_path=source,
        release="fixture-release",
        data_root=data_root,
        run_id="acled-fixture",
        polygons=_polygons(),
        geography=geography,
        geography_dataset=_geography_ref(geography),
        period_scheme=PeriodScheme(width_years=2, anchor_year=2001),
        code_commit="deadbeef",
    )

    assert result.paths["silver"].exists()
    assert result.paths["geography_membership"].exists()
    assert result.paths["period_membership"].exists()
    assert result.paths["gold"].exists()
    assert result.silver_dataset.content_sha256 is not None
    assert result.gold_dataset.content_sha256 is not None

    silver_input = result.silver_manifest.inputs[0]
    assert silver_input.files[0].sha256 == result.snapshot.files[0].sha256
    assert result.measurement_manifest.inputs[0] == result.silver_dataset
    assert result.measurement_manifest.inputs[1].dataset_id == "geography.gadm.units"
    assert result.measurement_contract.coverage.absent_row_semantics == "unknown"
    assert result.measurement_contract.parameters["geo_precision_filter"] is None

    coverage_path = (
        data_root.run("fcv-empirical-data", "acled-fixture-measurement")
        / "artifacts/contracts/coverage.json"
    )
    payload = json.loads(coverage_path.read_text())
    assert payload["absent_row_semantics"] == "unknown"


def test_parity_reports_filters_without_applying_them():
    silver = pd.DataFrame(
        {
            "geo_precision": [1, 2],
            "fatalities": [2.0, 5.0],
            "native_event_type": ["Violence against civilians", "Protests"],
        }
    )
    gold = pd.DataFrame(columns=["geo_uid", "period_id", "native_event_type", "fatalities"])
    report = compare_acled_legacy(silver=silver, gold=gold)

    assert report["status"] == "NOT_RUN"
    assert report["legacy_filter_diagnostics"]["silver_known_fatalities"] == 7.0
    assert report["legacy_filter_diagnostics"]["geo_precision_1_known_fatalities"] == 2.0
    assert report["legacy_filter_diagnostics"]["geo_precision_filter_removed_known_fatalities"] == 5.0


def test_registered_snapshot_drift_is_rejected(tmp_path: Path):
    from fcv_empirical.violence.acled_events import register_acled_snapshot
    from fcv_empirical.violence.acled_pipeline import materialize_acled_silver

    source = tmp_path / "acled.csv"
    _source(source)
    snapshot = register_acled_snapshot(source, release="fixture-release")
    source.write_text(source.read_text() + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="changed after snapshot registration"):
        materialize_acled_silver(
            source_path=source,
            release="fixture-release",
            data_root=DataRoot.from_path(tmp_path / "data"),
            run_id="drift",
            source_snapshot=snapshot,
        )
