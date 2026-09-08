from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from empirical_contracts import QAResult

from . import dhs_hr_release as _release
from .dhs_hr import normalize_dhs_hr

_PARQUET_COMPRESSION = "NONE"
_PARQUET_USE_DICTIONARY = False
_PARQUET_WRITE_STATISTICS = False
_PARQUET_WRITE_BATCH_SIZE = 64

_ORIGINAL_BUILD_STREAMING_QA = _release._build_streaming_qa


def _build_low_memory_streaming_qa(
    *,
    dictionary: _release.DhsFixedWidthDictionary,
    profile: Any,
    chunk_rows: int,
) -> tuple[QAResult, ...]:
    base = _ORIGINAL_BUILD_STREAMING_QA(
        dictionary=dictionary,
        profile=profile,
        chunk_rows=chunk_rows,
    )
    return (
        *base,
        QAResult(
            check_id="dhs.hr.low_memory_parquet_writer",
            state="GREEN",
            message=(
                "ultra-wide HR Silver uses a plain Parquet writer profile that avoids "
                "per-column dictionary and statistics state"
            ),
            metrics={
                "compression": _PARQUET_COMPRESSION,
                "use_dictionary": _PARQUET_USE_DICTIONARY,
                "write_statistics": _PARQUET_WRITE_STATISTICS,
                "write_batch_size": _PARQUET_WRITE_BATCH_SIZE,
                "row_group_rows": chunk_rows,
                "source_column_count": len(dictionary.fields),
            },
        ),
    )


def _write_low_memory_hr_parquet(
    output: Path,
    *,
    source_path: Path,
    dictionary: _release.DhsFixedWidthDictionary,
    metadata: Any,
    snapshot: Any,
    column_map: Any,
    expected_rows: int,
    expected_schema_sha256: str,
    chunk_rows: int,
) -> None:
    """Write ultra-wide DHS HR Silver without width-scaled Parquet encoder state.

    DHS HR releases can contain roughly five thousand source columns. PyArrow's default
    dictionary encoding and column statistics are reasonable for ordinary tables but create
    thousands of simultaneous encoder/statistics states for this unusually wide schema. Row
    chunking alone cannot bound that width-dependent memory.

    The canonical source-native Silver therefore uses plain, uncompressed Parquet pages with
    dictionary encoding and statistics disabled. Decode chunks remain row groups, so callers
    should prefer the normal 512-row chunk unless host memory requires a different value; making
    chunks arbitrarily tiny increases Parquet row-group metadata and is not a free memory win.
    """

    writer: pq.ParquetWriter | None = None
    arrow_schema: pa.Schema | None = None
    rows_written = 0
    try:
        for raw in _release.iter_dhs_fixed_width_dat_chunks(
            source_path,
            dictionary,
            chunk_rows=chunk_rows,
        ):
            normalized = normalize_dhs_hr(
                raw,
                metadata=metadata,
                snapshot=snapshot,
                source_path=source_path,
                column_map=column_map,
            )
            if normalized.schema_sha256 != expected_schema_sha256:
                raise RuntimeError("streamed DHS source schema changed across decode chunks")

            positions = range(rows_written, rows_written + len(normalized.frame))
            source_row_ids = [
                f"{snapshot.snapshot_id}:{position:09d}" for position in positions
            ]
            normalized.frame["source_row_id"] = source_row_ids
            normalized.frame["household_observation_id"] = source_row_ids

            table = pa.Table.from_pandas(normalized.frame, preserve_index=False)
            if writer is None:
                arrow_schema = table.schema
                writer = pq.ParquetWriter(
                    output,
                    arrow_schema,
                    compression=_PARQUET_COMPRESSION,
                    use_dictionary=_PARQUET_USE_DICTIONARY,
                    write_statistics=_PARQUET_WRITE_STATISTICS,
                    write_batch_size=_PARQUET_WRITE_BATCH_SIZE,
                )
            elif table.schema != arrow_schema:
                if arrow_schema is None:
                    raise RuntimeError("canonical DHS Parquet schema was not initialized")
                table = table.cast(arrow_schema)

            writer.write_table(table, row_group_size=len(table))
            rows_written += len(normalized.frame)
            del table, normalized, raw
    finally:
        if writer is not None:
            writer.close()

    if rows_written != expected_rows:
        raise RuntimeError(
            f"canonical DHS writer emitted {rows_written} rows; expected {expected_rows}"
        )
    if writer is None:
        raise RuntimeError("canonical DHS writer produced no Parquet row groups")


def install_low_memory_dhs_hr_writer() -> None:
    """Install the ultra-wide writer profile into the canonical HR release adapter."""

    _release._write_streaming_hr_parquet = _write_low_memory_hr_parquet
    _release._build_streaming_qa = _build_low_memory_streaming_qa


__all__ = ["install_low_memory_dhs_hr_writer"]
