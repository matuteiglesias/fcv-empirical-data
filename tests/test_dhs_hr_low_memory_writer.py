from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from fcv_empirical.surveys import dhs_hr_release
from fcv_empirical.surveys import dhs_hr_low_memory_writer as low_memory


def test_low_memory_writer_is_installed_on_canonical_release_adapter():
    assert dhs_hr_release._write_streaming_hr_parquet is low_memory._write_low_memory_hr_parquet
    assert dhs_hr_release._build_streaming_qa is low_memory._build_low_memory_streaming_qa


def test_low_memory_writer_disables_width_scaled_parquet_state(
    tmp_path: Path,
    monkeypatch,
):
    captured = {}

    class FakeWriter:
        def __init__(self, output, schema, **kwargs):
            captured["output"] = output
            captured["schema"] = schema
            captured["kwargs"] = kwargs

        def write_table(self, table, row_group_size=None):
            captured.setdefault("row_group_sizes", []).append(row_group_size)

        def close(self):
            captured["closed"] = True

    raw = pd.DataFrame({"hv001": pd.Series(["1"], dtype="string")})
    normalized_frame = pd.DataFrame(
        {
            "survey_id": pd.Series(["dhs-ZZ2020DHS"], dtype="string"),
            "source_row_id": pd.Series(["placeholder"], dtype="string"),
            "household_observation_id": pd.Series(["placeholder"], dtype="string"),
            "hv001": pd.Series(["1"], dtype="string"),
        }
    )

    monkeypatch.setattr(
        dhs_hr_release,
        "iter_dhs_fixed_width_dat_chunks",
        lambda *args, **kwargs: iter((raw,)),
    )
    monkeypatch.setattr(
        low_memory,
        "normalize_dhs_hr",
        lambda *args, **kwargs: SimpleNamespace(
            frame=normalized_frame.copy(),
            schema_sha256="schema",
        ),
    )
    monkeypatch.setattr(low_memory.pq, "ParquetWriter", FakeWriter)

    dictionary = SimpleNamespace(fields=(1, 2, 3))
    snapshot = SimpleNamespace(snapshot_id="snapshot")

    low_memory._write_low_memory_hr_parquet(
        tmp_path / "wide.parquet",
        source_path=tmp_path / "fixture.DAT",
        dictionary=dictionary,
        metadata=object(),
        snapshot=snapshot,
        column_map=object(),
        expected_rows=1,
        expected_schema_sha256="schema",
        chunk_rows=512,
    )

    assert captured["kwargs"] == {
        "compression": "NONE",
        "use_dictionary": False,
        "write_statistics": False,
        "write_batch_size": 64,
    }
    assert captured["row_group_sizes"] == [1]
    assert captured["closed"] is True
