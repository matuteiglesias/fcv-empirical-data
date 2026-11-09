"""Source-native WB geocoded release tests; no treatment or geography modeling."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd
import pytest
from spatial_foundation import DataRoot

from fcv_empirical.investments.worldbank_geocoded import (
    SOURCE_PREFIX,
    extract_wb_geocoded_release,
    materialize_wb_geocoded_silver,
    register_wb_geocoded_snapshot,
)


def fixture_zip(
    path: Path,
    *,
    orphan=False,
    duplicate_location=False,
    contradictory_date=False,
    incomplete_ids=False,
) -> Path:
    projects = pd.DataFrame(
        {
            "project_id": ["P1", "P2"],
            "is_geocoded": [1, 0],
            "project_title": ["Test project", "Unlocated project"],
            "start_actual_isodate": ["2003-01-01", "2013-11-09"],
            "recipients_iso3": ["TST", "TST"],
            "total_commitments": [100000, 22000],
            "total_disbursements": [35000, None],
        }
    )
    locations = pd.DataFrame(
        {
            "project_id": ["P999" if orphan else "P1"],
            "project_location_id": ["P1_1"],
            "place_name": ["Test town"],
            "latitude": [0.25],
            "longitude": [0.25],
            "precision_code": [1],
        }
    )
    if duplicate_location:
        locations = pd.concat([locations, locations], ignore_index=True)
    if incomplete_ids:
        locations["project_location_id"] = ""
    transactions = pd.DataFrame(
        {
            "transaction_id": ["T1", "T2"],
            "project_id": ["P1", "P2"],
            "transaction_year": [2003, 2013],
            "transaction_value_code": ["C", "C"],
            "transaction_currency": ["USD", "USD"],
            "transaction_value": [100000, 22000],
        }
    )
    ancillary = pd.DataFrame(
        {
            "PROJECT ID": ["P1", "P1", "P2"],
            "APPROVAL DATE": [
                "01-JAN-2003",
                "02-JAN-2003" if contradictory_date else "01-JAN-2003",
                "09-NOV-2013",
            ],
        }
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, frame in [
            ("projects.csv", projects),
            ("locations.csv", locations),
            ("transactions.csv", transactions),
            ("projects_ancillary.csv", ancillary),
        ]:
            z.writestr(SOURCE_PREFIX + name, frame.to_csv(index=False))
    return path


def test_source_native_silver_materialization_preserves_relations_and_nulls(tmp_path: Path) -> None:
    source = fixture_zip(tmp_path / "source.zip")
    root = DataRoot.from_path(tmp_path / "data")
    snapshot = register_wb_geocoded_snapshot(source)
    result = materialize_wb_geocoded_silver(
        source_zip=source,
        data_root=root,
        run_id="wb-native-fixture",
        source_snapshot=snapshot,
        code_commit="unit-test",
    )
    assert result.manifest.inputs == (snapshot,)
    assert len(result.manifest.outputs) == 4
    assert all(item.content_sha256 for item in result.manifest.outputs)
    projects = pd.read_parquet(result.paths["projects"])
    locations = pd.read_parquet(result.paths["locations"])
    anc = pd.read_parquet(result.paths["ancillary"])
    assert len(projects) == 2
    assert len(locations) == 1
    assert len(anc) == 3
    assert projects.project_id.tolist() == ["P1", "P2"]
    assert pd.isna(projects.loc[projects.project_id.eq("P2"), "total_disbursements"].iloc[0])
    assert projects.loc[projects.project_id.eq("P2"), "total_commitments"].iloc[0] == 22000
    assert result.extraction.source_counts["approval_date_parity_projects"] == 2
    assert (root.run("fcv-empirical-data", "wb-native-fixture") / "run_manifest.json").exists()
    with pytest.raises(FileExistsError):
        materialize_wb_geocoded_silver(
            source_zip=source,
            data_root=root,
            run_id="wb-native-fixture",
            source_snapshot=snapshot,
            code_commit="unit-test",
        )


@pytest.mark.parametrize(
    ("modify", "expected"),
    [
        ({"orphan": True}, "orphan"),
        ({"duplicate_location": True}, "duplicated"),
        ({"contradictory_date": True}, "conflicting approval"),
        ({"incomplete_ids": True}, "missing source identifier"),
    ],
)
def test_source_integrity_failures_are_not_silently_repaired(
    tmp_path: Path, modify, expected
) -> None:
    source = fixture_zip(tmp_path / "source.zip", **modify)
    with pytest.raises(ValueError, match=expected):
        extract_wb_geocoded_release(source)


def test_source_snapshot_drift_is_not_accepted(tmp_path: Path) -> None:
    source = fixture_zip(tmp_path / "source.zip")
    snapshot = register_wb_geocoded_snapshot(source)
    with zipfile.ZipFile(source, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr("extraneous.txt", "changed bytes")
    with pytest.raises(ValueError, match="source ZIP has changed"):
        materialize_wb_geocoded_silver(
            source_zip=source,
            data_root=DataRoot.from_path(tmp_path / "data"),
            run_id="mutated-source",
            source_snapshot=snapshot,
        )


def test_source_release_is_not_mistaken_for_world_bank_api_or_new_version(tmp_path: Path) -> None:
    source = fixture_zip(tmp_path / "source.zip")
    with pytest.raises(ValueError, match="exactly the pinned"):
        register_wb_geocoded_snapshot(source, release="v9.9")
