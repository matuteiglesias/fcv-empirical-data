from pathlib import Path

import pandas as pd
import pytest
from empirical_contracts import AuthorityLevel
from spatial_foundation import DataRoot

from fcv_empirical.surveys import dhs_hr_release
from fcv_empirical.surveys.dhs_hr import STANDARD_DHS_HR_COLUMNS, DhsHrMetadata
from fcv_empirical.surveys.dhs_hr_release import (
    materialize_dhs_hr_release_silver,
    parse_dhs_stata_dictionary,
    read_dhs_fixed_width_dat,
    register_dhs_hr_release_snapshot,
)


def _metadata(source_file_name: str = "ZZHR71FL.DAT") -> DhsHrMetadata:
    return DhsHrMetadata(
        dhs_survey_id="ZZ2020DHS",
        country_iso3="ZZZ",
        country="Synthetic Republic",
        survey_year=2020,
        survey_phase="DHS-VII",
        release="synthetic-release-v1",
        source_file_name=source_file_name,
    )


def _dictionary_text() -> str:
    return """infix dictionary using ZZHR71FL.DAT {\n str6 hhid 1: 1-6\n int hv001 1: 7-9\n long hv005 1: 10-15\n int hv021 1: 16-18\n int hv022 1: 19-21\n byte hv012 1: 22-23\n byte hv025 1: 24-24\n byte hv201 1: 25-26\n byte hv206 1: 27-27\n byte hv270 1: 28-28\n long hv271 1: 29-36\n str2 note 1: 37-38\n}\n"""


def test_dictionary_parser_accepts_dhs_unsized_str_declarations(tmp_path: Path):
    dct = tmp_path / "ZZHR71FL.DCT"
    dct.write_text(
        'infix dictionary using "ZZHR71FL.DAT" {\n str hhid 1: 1-12\n}\n',
        encoding="latin-1",
    )

    dictionary = parse_dhs_stata_dictionary(dct)

    assert dictionary.column_names == ("hhid",)
    assert dictionary.record_width == 12


def _record(
    *,
    hhid: str,
    cluster: int,
    weight: int,
    psu: int,
    stratum: int,
    members: int,
    residence: int,
    water: int,
    electricity: int,
    wealth: int,
    wealth_score: int,
    note: str,
) -> str:
    return (
        f"{hhid:<6}"
        f"{cluster:>3}"
        f"{weight:>6}"
        f"{psu:>3}"
        f"{stratum:>3}"
        f"{members:>2}"
        f"{residence:>1}"
        f"{water:>2}"
        f"{electricity:>1}"
        f"{wealth:>1}"
        f"{wealth_score:>8}"
        f"{note:<2}"
    )


def _release(tmp_path: Path) -> tuple[Path, Path]:
    dat = tmp_path / "ZZHR71FL.DAT"
    dct = tmp_path / "ZZHR71FL.DCT"
    dct.write_text(_dictionary_text(), encoding="latin-1")
    dat.write_text(
        "\n".join(
            [
                _record(
                    hhid="001001",
                    cluster=1,
                    weight=100000,
                    psu=101,
                    stratum=11,
                    members=4,
                    residence=1,
                    water=11,
                    electricity=1,
                    wealth=5,
                    wealth_score=12345,
                    note="a",
                ),
                _record(
                    hhid="001002",
                    cluster=1,
                    weight=75000,
                    psu=101,
                    stratum=11,
                    members=6,
                    residence=2,
                    water=21,
                    electricity=0,
                    wealth=2,
                    wealth_score=-2345,
                    note="b",
                ),
            ]
        )
        + "\n",
        encoding="latin-1",
    )
    return dat, dct


def test_dictionary_decoder_recovers_standard_dhs_fields(tmp_path: Path):
    dat, dct = _release(tmp_path)
    dictionary = parse_dhs_stata_dictionary(dct)

    assert dictionary.record_width == 38
    assert dictionary.column_names == (
        "hhid",
        "hv001",
        "hv005",
        "hv021",
        "hv022",
        "hv012",
        "hv025",
        "hv201",
        "hv206",
        "hv270",
        "hv271",
        "note",
    )
    assert dictionary.schema_sha256

    frame = read_dhs_fixed_width_dat(dat, dictionary)
    assert frame["hhid"].tolist() == ["001001", "001002"]
    assert frame["hv025"].tolist() == ["1", "2"]
    assert frame["hv206"].tolist() == ["1", "0"]
    assert frame["hv270"].tolist() == ["5", "2"]
    assert frame["hv271"].tolist() == ["12345", "-2345"]
    assert all(str(dtype) == "string" for dtype in frame.dtypes)


def test_canonical_materialization_streams_chunks_and_binds_dat_dictionary(tmp_path: Path):
    dat, dct = _release(tmp_path)
    data_root = DataRoot.from_path(tmp_path / "data")

    snapshot, silver, manifest, dataset, output = materialize_dhs_hr_release_silver(
        source_path=dat,
        dictionary_path=dct,
        metadata=_metadata(),
        column_map=STANDARD_DHS_HR_COLUMNS,
        data_root=data_root,
        run_id="dhs-hr-release-fixture",
        code_commit="deadbeef",
        decode_chunk_rows=1,
    )

    assert len(snapshot.files) == 2
    assert {Path(item.path).name for item in snapshot.files} == {"ZZHR71FL.DAT", "ZZHR71FL.DCT"}
    assert output.exists()
    materialized = pd.read_parquet(output)
    assert materialized["hv025"].tolist() == ["1", "2"]
    assert materialized["hv206"].tolist() == ["1", "0"]
    assert materialized["hv270"].tolist() == ["5", "2"]
    assert materialized["source_row_id"].is_unique
    assert materialized["source_row_id"].str.endswith(("000000000", "000000001")).all()
    assert silver.file_link.source_file.path.endswith("ZZHR71FL.DAT")
    assert silver.row_count == 2
    assert silver.source_column_count == 12

    assert dataset.authority == AuthorityLevel.L3_REBUILT
    assert dataset.schema_version == "dhs-hr-household-silver-v3-fixed-width"
    assert dataset.content_sha256 is not None
    assert manifest.inputs == (snapshot,)
    assert manifest.parameters["source_representation"] == "official_fixed_width_dat+dct"
    assert manifest.parameters["source_dictionary_file_name"] == "ZZHR71FL.DCT"
    assert manifest.parameters["source_dictionary_field_count"] == 12
    assert manifest.parameters["source_dictionary_record_width"] == 38
    assert manifest.parameters["source_weight_transformation"] is None
    assert manifest.parameters["decode_strategy"] == "bounded_row_chunks_to_parquet"
    assert manifest.parameters["decode_chunk_rows"] == 1
    assert manifest.parameters["whole_release_dataframe_materialized"] is False

    qa = {item.check_id: item for item in silver.qa}
    assert qa["dhs.hr.canonical_fixed_width_source"].state == "GREEN"
    assert qa["dhs.hr.bounded_memory_decode"].state == "GREEN"
    assert qa["dhs.hr.dictionary_schema_coverage"].state == "GREEN"

    run = data_root.run("fcv-empirical-data", "dhs-hr-release-fixture")
    assert (run / "artifacts/mappings/dhs_hr_fixed_width_dictionary.json").exists()


def test_canonical_materializer_never_calls_whole_release_dataframe_decoder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    dat, dct = _release(tmp_path)

    def _forbidden(*args, **kwargs):
        raise AssertionError("whole-release DataFrame decoder must not be used by materialization")

    monkeypatch.setattr(dhs_hr_release, "read_dhs_fixed_width_dat", _forbidden)

    _, silver, manifest, _, output = materialize_dhs_hr_release_silver(
        source_path=dat,
        dictionary_path=dct,
        metadata=_metadata(),
        column_map=STANDARD_DHS_HR_COLUMNS,
        data_root=DataRoot.from_path(tmp_path / "data"),
        run_id="dhs-hr-streaming-regression",
        decode_chunk_rows=1,
    )

    assert output.exists()
    assert silver.row_count == 2
    assert manifest.parameters["whole_release_dataframe_materialized"] is False


def test_canonical_materializer_refuses_lossy_tabular_source(tmp_path: Path):
    source = tmp_path / "ZZHR71FL.csv"
    source.write_text("hhid,hv001,hv005\n001001,1,100000\n", encoding="utf-8")
    dct = tmp_path / "ZZHR71FL.DCT"
    dct.write_text(_dictionary_text(), encoding="latin-1")

    with pytest.raises(ValueError, match="official .DAT source"):
        materialize_dhs_hr_release_silver(
            source_path=source,
            dictionary_path=dct,
            metadata=_metadata("ZZHR71FL.csv"),
            column_map=STANDARD_DHS_HR_COLUMNS,
            data_root=DataRoot.from_path(tmp_path / "data"),
            run_id="dhs-hr-csv-refused",
        )


def test_release_snapshot_mutation_of_dictionary_fails_closed(tmp_path: Path):
    dat, dct = _release(tmp_path)
    snapshot = register_dhs_hr_release_snapshot(dat, dct, release="synthetic-release-v1")
    dct.write_text(dct.read_text(encoding="latin-1") + "\n", encoding="latin-1")

    with pytest.raises(ValueError, match="changed after snapshot registration"):
        materialize_dhs_hr_release_silver(
            source_path=dat,
            dictionary_path=dct,
            metadata=_metadata(),
            column_map=STANDARD_DHS_HR_COLUMNS,
            data_root=DataRoot.from_path(tmp_path / "data"),
            run_id="dhs-hr-dictionary-drift",
            source_snapshot=snapshot,
        )


def test_short_fixed_width_record_is_right_padded_as_blank_dhs_suffix(tmp_path: Path):
    dat, dct = _release(tmp_path)
    dat.write_text("001001  1\n", encoding="latin-1")
    dictionary = parse_dhs_stata_dictionary(dct)

    frame = read_dhs_fixed_width_dat(dat, dictionary)

    assert frame.loc[0, "hhid"] == "001001"
    assert frame.loc[0, "hv001"] == "1"
    assert pd.isna(frame.loc[0, "hv005"])


def test_overlapping_dictionary_positions_fail_closed(tmp_path: Path):
    dct = tmp_path / "ZZHR71FL.DCT"
    dct.write_text(
        "infix dictionary {\n byte hv001 1: 1-3\n byte hv002 1: 3-5\n}\n",
        encoding="latin-1",
    )

    with pytest.raises(ValueError, match="overlapping field positions"):
        parse_dhs_stata_dictionary(dct)
