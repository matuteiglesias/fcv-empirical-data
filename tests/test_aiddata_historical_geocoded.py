import zipfile
from pathlib import Path

import pandas as pd
import pytest
from spatial_foundation import DataRoot

from fcv_empirical.investments.aiddata_historical_geocoded import (
    HistoricalAidDataRelease,
    materialize_historical_aiddata_silver,
    read_historical_aiddata_archive,
)


def _release(filename: str) -> HistoricalAidDataRelease:
    return HistoricalAidDataRelease(
        release_id="fixture-release",
        donor="Fixture Bank",
        source_id="fixture_historical_geocoded",
        origin="https://example.invalid/fixture.zip",
        archive_filename=filename,
        published_scope="fixture source scope",
    )


def _csv_archive(tmp_path: Path, filename: str = "fixture.csv.zip") -> Path:
    path = tmp_path / filename
    payload = (
        "project_id,location_id,approval_year,precision,region,cost\n"
        "P1,L1,2009,2,North,100\n"
        "P1,L2,2009,4,South,100\n"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fixture.csv", payload)
        archive.writestr("README.txt", "documentation is not a source table")
    return path


def test_archive_reader_preserves_source_columns_and_only_surfaces_candidates(tmp_path: Path) -> None:
    path = _csv_archive(tmp_path)
    extraction = read_historical_aiddata_archive(path, release=_release(path.name))

    assert extraction.source_columns == (
        "project_id",
        "location_id",
        "approval_year",
        "precision",
        "region",
        "cost",
    )
    assert extraction.rows["fcv_source_row_number"].tolist() == [1, 2]
    assert extraction.rows["fcv_source_donor"].unique().tolist() == ["Fixture Bank"]
    assert extraction.audit["semantic_mapping_selected"] is False
    assert "precision" in extraction.audit["candidate_columns_for_review_only"]["precision"]


def test_archive_reader_fails_when_multiple_data_tables_are_present(tmp_path: Path) -> None:
    path = tmp_path / "fixture.csv.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("one.csv", "id\n1\n")
        archive.writestr("two.csv", "id\n2\n")
    with pytest.raises(ValueError, match="exactly one CSV/XLSX"):
        read_historical_aiddata_archive(path, release=_release(path.name))


def test_materializer_binds_archive_and_persists_source_native_rows(tmp_path: Path) -> None:
    path = _csv_archive(tmp_path)
    result = materialize_historical_aiddata_silver(
        archive_path=path,
        release=_release(path.name),
        data_root=DataRoot(tmp_path / "data"),
        run_id="fixture-historical-aid",
        code_commit="fixture",
    )

    output = result.paths["rows"]
    assert output.exists()
    rows = pd.read_parquet(output)
    assert len(rows) == 2
    assert rows["project_id"].tolist() == ["P1", "P1"]
    assert result.datasets["rows"].content_sha256 is not None


def test_filename_is_part_of_release_identity(tmp_path: Path) -> None:
    path = _csv_archive(tmp_path, filename="wrong.csv.zip")
    release = _release("expected.csv.zip")
    with pytest.raises(ValueError, match="archive filename must be"):
        read_historical_aiddata_archive(path, release=release)
