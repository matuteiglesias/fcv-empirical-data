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

from fcv_empirical.investments.aiddata_clg import (
    extract_aiddata_workbook,
    materialize_aiddata_silver,
    register_aiddata_snapshot,
)
from fcv_empirical.investments.annotation_candidates import (
    build_aiddata_annotation_candidates,
    build_annotation_candidates,
)
from fcv_empirical.investments.worldbank import (
    materialize_worldbank_silver,
    register_worldbank_snapshot,
)
from fcv_empirical.violence.acled_events import register_acled_snapshot
from fcv_empirical.violence.acled_pipeline import (
    materialize_acled_silver,
    materialize_acled_vertical,
)


def _write_aiddata_workbook(path: Path, *, title: str = "Project") -> None:
    records = pd.DataFrame(
        {
            "AidData Record ID": ["p1"],
            "Title": [title],
            "Amount Constant USD 2023": ["100"],
        }
    )
    ownership = pd.DataFrame(
        {
            "AidData Record ID": ["p1"] * 5,
            "Borrower Type": [f"borrower-{index}" for index in range(5)],
        }
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        records.to_excel(writer, sheet_name="CLG-LMIC 1.0_Records", index=False)
        ownership.to_excel(writer, sheet_name="Borrower Ownership", index=False)
        pd.DataFrame({"Country": ["Example"]}).to_excel(
            writer, sheet_name="CountryList", index=False, startrow=4
        )
        pd.DataFrame({"Variable": ["AidData Record ID"], "Definition": ["ID"]}).to_excel(
            writer, sheet_name="Definitions_Records", index=False, startrow=3
        )
        pd.DataFrame({"Variable": ["Borrower Type"], "Definition": ["Type"]}).to_excel(
            writer,
            sheet_name="Definitions_Borrower Ownership",
            index=False,
            startrow=3,
        )


def _write_worldbank_page(path: Path, project_id: str, *, name: str = "Project") -> None:
    payload = {"projects": {project_id: {"id": project_id, "project_name": name}}}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_acled(path: Path) -> None:
    pd.DataFrame(
        {
            "EVENT_ID_CNTY": ["A1", "A2", "A3"],
            "EVENT_DATE": ["1999-01-01", "2001-01-01", "2001-03-01"],
            "COUNTRY": ["X", "X", "X"],
            "LATITUDE": [0.25, 0.50, 0.75],
            "LONGITUDE": [0.25, 0.50, 1.00],
            "EVENT_TYPE": ["Protests", "Violence against civilians", "Protests"],
            "SUB_EVENT_TYPE": ["Peaceful protest", "Attack", "Peaceful protest"],
            "FATALITIES": [0, 0, 2],
            "GEO_PRECISION": [1, 2, 3],
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


def test_aiddata_registered_snapshot_drift_is_rejected(tmp_path: Path) -> None:
    workbook = tmp_path / "aiddata.xlsx"
    _write_aiddata_workbook(workbook, title="Before")
    snapshot = register_aiddata_snapshot(workbook, release="v1.0-test")
    _write_aiddata_workbook(workbook, title="After")

    with pytest.raises(ValueError, match="changed after snapshot registration"):
        materialize_aiddata_silver(
            workbook_path=workbook,
            data_root=DataRoot.from_path(tmp_path / "data"),
            run_id="aiddata-drift",
            release="v1.0-test",
            source_snapshot=snapshot,
        )


def test_aiddata_snapshot_source_and_release_must_match_materialization(tmp_path: Path) -> None:
    workbook = tmp_path / "aiddata.xlsx"
    _write_aiddata_workbook(workbook)
    snapshot = register_aiddata_snapshot(workbook, release="v1.0-test")

    with pytest.raises(ValueError, match="snapshot source"):
        materialize_aiddata_silver(
            workbook_path=workbook,
            data_root=DataRoot.from_path(tmp_path / "wrong-source"),
            run_id="wrong-source",
            release="v1.0-test",
            source_snapshot=snapshot.model_copy(update={"source": "not-aiddata"}),
        )

    with pytest.raises(ValueError, match="snapshot release"):
        materialize_aiddata_silver(
            workbook_path=workbook,
            data_root=DataRoot.from_path(tmp_path / "wrong-release"),
            run_id="wrong-release",
            release="v2.0",
            source_snapshot=snapshot,
        )


def test_worldbank_registered_snapshot_drift_and_new_pages_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "worldbank"
    source.mkdir()
    page0 = source / "page_os_0000000.json"
    _write_worldbank_page(page0, "P1", name="Before")
    snapshot = register_worldbank_snapshot(source, release="fixture-release")

    _write_worldbank_page(page0, "P1", name="After")
    with pytest.raises(ValueError, match="changed after snapshot registration"):
        materialize_worldbank_silver(
            source_dir=source,
            data_root=DataRoot.from_path(tmp_path / "changed-data"),
            run_id="worldbank-drift",
            source_snapshot=snapshot,
            release="fixture-release",
        )

    _write_worldbank_page(page0, "P1", name="Before")
    snapshot = register_worldbank_snapshot(source, release="fixture-release-2")
    _write_worldbank_page(source / "page_os_0000100.json", "P2")
    with pytest.raises(ValueError, match="source file set changed"):
        materialize_worldbank_silver(
            source_dir=source,
            data_root=DataRoot.from_path(tmp_path / "new-page-data"),
            run_id="worldbank-new-page",
            source_snapshot=snapshot,
            release="fixture-release-2",
        )


def test_worldbank_snapshot_source_must_match_materialization(tmp_path: Path) -> None:
    source = tmp_path / "worldbank"
    source.mkdir()
    _write_worldbank_page(source / "page_os_0000000.json", "P1")
    snapshot = register_worldbank_snapshot(source, release="fixture-release")

    with pytest.raises(ValueError, match="snapshot source"):
        materialize_worldbank_silver(
            source_dir=source,
            data_root=DataRoot.from_path(tmp_path / "data"),
            run_id="worldbank-wrong-source",
            source_snapshot=snapshot.model_copy(update={"source": "not-worldbank"}),
            release="fixture-release",
        )


def test_acled_snapshot_source_identity_cannot_masquerade(tmp_path: Path) -> None:
    source = tmp_path / "acled.csv"
    _write_acled(source)
    snapshot = register_acled_snapshot(source, release="fixture-release")

    with pytest.raises(ValueError, match="snapshot source"):
        materialize_acled_silver(
            source_path=source,
            release="fixture-release",
            data_root=DataRoot.from_path(tmp_path / "data"),
            run_id="acled-wrong-source",
            source_snapshot=snapshot.model_copy(update={"source": "not-acled"}),
        )


def test_aiddata_child_grain_cannot_multiply_project_amount(tmp_path: Path) -> None:
    workbook = tmp_path / "aiddata.xlsx"
    _write_aiddata_workbook(workbook)
    extraction = extract_aiddata_workbook(workbook)

    assert len(extraction.tables["records"]) == 1
    assert len(extraction.tables["borrower_ownership"]) == 5
    candidates = build_aiddata_annotation_candidates(extraction.tables["records"])
    assert len(candidates) == 1
    assert candidates.loc[0, "source_project_id"] == "p1"
    assert candidates.loc[0, "source_amount_raw"] == "100"
    assert candidates.loc[0, "source_amount_basis"].endswith("no spatial allocation")


def test_annotation_combination_preserves_source_identity_instead_of_harmonizing() -> None:
    aiddata = pd.DataFrame(
        {"aiddata_record_id": ["P1"], "title": ["AidData project"], "amount_constant_usd_2023": ["100"]}
    )
    worldbank = pd.DataFrame(
        {"id": ["P1"], "project_name": ["World Bank project"], "totalcommamt": ["200"]}
    )

    candidates = build_annotation_candidates(aiddata_records=aiddata, worldbank_projects=worldbank)

    assert len(candidates) == 2
    assert set(candidates["source_family"]) == {"china", "worldbank"}
    assert candidates["annotation_record_id"].nunique() == 2
    assert "treated" not in candidates.columns
    assert "control" not in candidates.columns
    assert "jobcat" not in candidates.columns


def test_acled_traceability_reconstructs_gold_row_to_source_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "acled.csv"
    _write_acled(source)
    root = DataRoot.from_path(tmp_path / "data")
    geography = GeographySpec(provider="gadm", version="4.1", scheme="admin", level="2")
    scheme = PeriodScheme(width_years=2, anchor_year=2001)

    result = materialize_acled_vertical(
        source_path=source,
        release="fixture-release",
        data_root=root,
        run_id="trace",
        polygons=_polygons(),
        geography=geography,
        geography_dataset=_geography_ref(geography),
        period_scheme=scheme,
        code_commit="deadbeef",
    )

    silver = pd.read_parquet(result.paths["silver"])
    geo = pd.read_parquet(result.paths["geography_membership"])
    periods = pd.read_parquet(result.paths["period_membership"])
    gold = pd.read_parquet(result.paths["gold"])

    target = gold.loc[
        (gold["geo_uid"] == "g1")
        & (gold["period_id"] == "2001-2002")
        & (gold["native_event_type"] == "Violence against civilians")
    ].iloc[0]
    resolved_geo = geo.loc[geo["assignment_status"] == "matched_unique", ["event_row_id", "geo_uid"]]
    resolved_period = periods.loc[
        periods["period_assignment_status"] == "assigned", ["event_row_id", "period_id"]
    ]
    events = (
        silver[["event_row_id", "source_event_id", "native_event_type"]]
        .merge(resolved_geo, on="event_row_id", validate="one_to_one")
        .merge(resolved_period, on="event_row_id", validate="one_to_one")
    )
    contributing = events.loc[
        (events["geo_uid"] == target["geo_uid"])
        & (events["period_id"] == target["period_id"])
        & (events["native_event_type"] == target["native_event_type"])
    ]

    assert target["event_count"] == len(contributing) == 1
    assert contributing["source_event_id"].tolist() == ["A2"]
    assert result.measurement_contract.period_scheme == scheme
    assert result.measurement_manifest.inputs[0] == result.silver_dataset
    assert result.silver_manifest.inputs[0] == result.snapshot
    assert result.snapshot.files[0].sha256
    assert result.silver_manifest.code_commit == "deadbeef"
    assert result.measurement_manifest.code_commit == "deadbeef"
