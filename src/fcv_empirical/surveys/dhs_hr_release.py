from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from empirical_contracts import (
    AuthorityLevel,
    DataLayer,
    DatasetRef,
    GrainSpec,
    QAResult,
    RunManifest,
    SourceSnapshotRef,
)
from spatial_foundation import DataRoot, register_external_snapshot, sha256_file

from fcv_empirical.common import FileMaterialization, materialize_files, persist_run_artifact

from .catalog import SurveyCatalogEntry, SurveyFileLink
from .dhs_hr import (
    DHS_HR_RECODE,
    DHS_ORIGIN,
    DHS_SOURCE,
    DhsHrColumnMap,
    DhsHrMetadata,
    build_dhs_hr_file_link,
    build_dhs_survey_catalog,
    normalize_dhs_hr,
)

_DCT_FIELD_RE = re.compile(
    r"^\s*(?P<storage>byte|int|long|float|double|str\d*)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+"
    r"(?P<record>\d+):\s*(?P<start>\d+)\s*-\s*(?P<end>\d+)\s*$",
    re.IGNORECASE,
)
_DCT_DECLARATION_HINT_RE = re.compile(r"\b\d+\s*:\s*\d+\s*-\s*\d+\b")
_DEFAULT_DECODE_CHUNK_ROWS = 512


@dataclass(frozen=True)
class DhsFixedWidthField:
    """One field declaration from a DHS-distributed Stata fixed-width dictionary."""

    name: str
    storage_type: str
    record: int
    start: int
    end: int

    @property
    def width(self) -> int:
        return self.end - self.start + 1

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("fixed-width field name must be non-empty")
        if self.record <= 0:
            raise ValueError("fixed-width record number must be positive")
        if self.start <= 0 or self.end < self.start:
            raise ValueError("fixed-width positions must be positive and ordered")


@dataclass(frozen=True)
class DhsFixedWidthDictionary:
    """Parsed authoritative layout for one single-record DHS fixed-width file."""

    fields: tuple[DhsFixedWidthField, ...]
    schema_sha256: str
    record_width: int

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields)


@dataclass(frozen=True)
class DhsHrReleaseSilverResult:
    """Bounded-memory canonical HR Silver summary.

    The complete household table is durable at ``hr_households.parquet`` and is intentionally not
    retained as one in-memory pandas DataFrame. This summary carries the same catalog/link/QA facts
    needed by downstream orchestration without recreating the memory failure the canonical adapter
    is designed to avoid.
    """

    qa: tuple[QAResult, ...]
    catalog: SurveyCatalogEntry
    file_link: SurveyFileLink
    source_columns: dict[str, str | None]
    schema_sha256: str
    row_count: int
    source_column_count: int


@dataclass(frozen=True)
class _DhsHrPreflight:
    row_count: int
    source_columns: dict[str, str | None]
    source_schema_sha256: str
    missing_household_ids: int
    duplicate_household_id_rows: int
    missing_cluster_ids: int
    cluster_count: int
    missing_psu_ids: int
    missing_stratum_ids: int
    missing_weights: int
    invalid_weights: int
    nonpositive_weights: int


def _dictionary_schema_sha256(fields: tuple[DhsFixedWidthField, ...]) -> str:
    payload = [
        {
            "name": field.name,
            "storage_type": field.storage_type,
            "record": field.record,
            "start": field.start,
            "end": field.end,
        }
        for field in fields
    ]
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_schema_sha256(dictionary: DhsFixedWidthDictionary) -> str:
    entries = [(name, "string") for name in dictionary.column_names]
    payload = json.dumps(entries, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_dhs_stata_dictionary(dictionary_path: str | Path) -> DhsFixedWidthDictionary:
    """Parse the fixed-width field layout from a DHS-distributed Stata ``.DCT`` file."""

    path = Path(dictionary_path)
    if path.suffix.casefold() != ".dct":
        raise ValueError("canonical DHS fixed-width ingestion requires a .DCT dictionary")

    fields: list[DhsFixedWidthField] = []
    for line_number, line in enumerate(path.read_text(encoding="latin-1").splitlines(), start=1):
        match = _DCT_FIELD_RE.match(line)
        if match is not None:
            storage = match.group("storage").casefold()
            fields.append(
                DhsFixedWidthField(
                    name=match.group("name"),
                    storage_type=storage,
                    record=int(match.group("record")),
                    start=int(match.group("start")),
                    end=int(match.group("end")),
                )
            )
            continue
        if _DCT_DECLARATION_HINT_RE.search(line):
            raise ValueError(f"unparsed fixed-width declaration in {path.name} at line {line_number}")

    if not fields:
        raise ValueError(f"no fixed-width field declarations found in {path.name}")

    records = {field.record for field in fields}
    if records != {1}:
        raise ValueError(
            "DHS HR fixed-width ingestion currently requires one physical record per household line"
        )

    folded_names = [field.name.casefold() for field in fields]
    if len(folded_names) != len(set(folded_names)):
        raise ValueError("DHS fixed-width dictionary contains duplicate variable names")

    ordered = tuple(sorted(fields, key=lambda field: (field.start, field.end, field.name.casefold())))
    previous_end = 0
    for field in ordered:
        if field.start <= previous_end:
            raise ValueError("DHS fixed-width dictionary contains overlapping field positions")
        previous_end = field.end

    return DhsFixedWidthDictionary(
        fields=ordered,
        schema_sha256=_dictionary_schema_sha256(ordered),
        record_width=max(field.end for field in ordered),
    )


def _canonical_record_bytes(
    raw_line: bytes,
    *,
    dictionary: DhsFixedWidthDictionary,
    line_number: int,
) -> bytes:
    line = raw_line.rstrip(b"\r\n")
    if len(line) < dictionary.record_width:
        # DHS release writers may omit an all-blank suffix instead of serializing it.
        line = line.ljust(dictionary.record_width, b" ")
    trailing = line[dictionary.record_width :]
    if trailing.strip():
        raise ValueError(
            f"DHS fixed-width record {line_number} has non-whitespace bytes beyond dictionary width"
        )
    return line[: dictionary.record_width]


def _token_from_record(record: bytes, field: DhsFixedWidthField) -> str | None:
    token = record[field.start - 1 : field.end].decode("latin-1").strip()
    return token or None


def read_dhs_fixed_width_dat(
    source_path: str | Path,
    dictionary: DhsFixedWidthDictionary,
) -> pd.DataFrame:
    """Decode a complete fixed-width file into memory for small fixtures/discovery only.

    Canonical materialization does not call this function. Real DHS HR releases are streamed through
    ``iter_dhs_fixed_width_dat_chunks`` so the complete wide release is never represented as one
    pandas string table in memory.
    """

    frames = list(iter_dhs_fixed_width_dat_chunks(source_path, dictionary, chunk_rows=4096))
    if not frames:
        return pd.DataFrame(columns=dictionary.column_names, dtype="string")
    return pd.concat(frames, ignore_index=True)


def iter_dhs_fixed_width_dat_chunks(
    source_path: str | Path,
    dictionary: DhsFixedWidthDictionary,
    *,
    chunk_rows: int = _DEFAULT_DECODE_CHUNK_ROWS,
) -> Iterator[pd.DataFrame]:
    """Yield complete dictionary-width source tables in bounded row chunks."""

    path = Path(source_path)
    if path.suffix.casefold() != ".dat":
        raise ValueError("canonical DHS fixed-width ingestion requires a .DAT source file")
    if chunk_rows <= 0:
        raise ValueError("chunk_rows must be positive")

    colspecs = [(field.start - 1, field.end) for field in dictionary.fields]
    reader = pd.read_fwf(
        path,
        colspecs=colspecs,
        names=list(dictionary.column_names),
        dtype="string",
        chunksize=chunk_rows,
        encoding="latin-1",
        keep_default_na=False,
    )
    for chunk in reader:
        frame = chunk.astype("string")
        frame = frame.mask(frame.fillna("").eq(""), pd.NA)
        if tuple(str(column) for column in frame.columns) != dictionary.column_names:
            raise RuntimeError("fixed-width decoder did not preserve dictionary column order")
        yield frame


def register_dhs_hr_release_snapshot(
    source_path: str | Path,
    dictionary_path: str | Path,
    *,
    release: str,
    origin: str = DHS_ORIGIN,
) -> SourceSnapshotRef:
    """Register canonical HR source bytes and exact dictionary as one immutable snapshot."""

    source = Path(source_path)
    dictionary = Path(dictionary_path)
    snapshot = register_external_snapshot(DHS_SOURCE, release, [source, dictionary])
    return snapshot.model_copy(update={"origin": origin})


def _matching_snapshot_file(snapshot: SourceSnapshotRef, path: str | Path):
    resolved = Path(path).expanduser().resolve()
    matches = [ref for ref in snapshot.files if Path(ref.path).expanduser().resolve() == resolved]
    if len(matches) != 1:
        raise ValueError("canonical DHS release file must appear exactly once in SourceSnapshotRef")
    return matches[0]


def _validate_snapshot_file(snapshot: SourceSnapshotRef, path: str | Path) -> None:
    source_file = _matching_snapshot_file(snapshot, path)
    if sha256_file(Path(path).expanduser().resolve()) != source_file.sha256:
        raise ValueError(f"DHS release file {Path(path).name!r} changed after snapshot registration")


def _validate_release_inputs(
    *,
    source_path: Path,
    dictionary_path: Path,
    metadata: DhsHrMetadata,
) -> None:
    if source_path.suffix.casefold() != ".dat":
        raise ValueError("canonical DHS HR materialization requires the official .DAT source")
    if dictionary_path.suffix.casefold() != ".dct":
        raise ValueError("canonical DHS HR materialization requires the distributed .DCT dictionary")
    if source_path.name != metadata.source_file_name:
        raise ValueError("source_path filename does not match verified DHS source_file_name")
    if source_path.stem.casefold() != dictionary_path.stem.casefold():
        raise ValueError("DHS HR .DAT and .DCT must be release companions with the same file stem")


def _resolve_dictionary_field(
    dictionary: DhsFixedWidthDictionary,
    requested: str | None,
) -> DhsFixedWidthField | None:
    if requested is None:
        return None
    matches = [
        field for field in dictionary.fields if field.name.casefold() == requested.casefold()
    ]
    if len(matches) != 1:
        if not matches:
            raise ValueError(
                f"DHS fixed-width dictionary is missing release-verified HR field {requested!r}"
            )
        raise ValueError(f"DHS fixed-width dictionary has ambiguous field {requested!r}")
    return matches[0]


def _resolved_source_columns(
    dictionary: DhsFixedWidthDictionary,
    column_map: DhsHrColumnMap,
) -> tuple[dict[str, str | None], dict[str, DhsFixedWidthField | None]]:
    fields = {
        "household_id": _resolve_dictionary_field(dictionary, column_map.household_id),
        "cluster_id": _resolve_dictionary_field(dictionary, column_map.cluster_id),
        "source_weight": _resolve_dictionary_field(dictionary, column_map.source_weight),
        "psu_id": _resolve_dictionary_field(dictionary, column_map.psu_id),
        "stratum_id": _resolve_dictionary_field(dictionary, column_map.stratum_id),
    }
    names = {key: None if field is None else field.name for key, field in fields.items()}
    return names, fields


def _profile_release(
    source_path: Path,
    dictionary: DhsFixedWidthDictionary,
    column_map: DhsHrColumnMap,
) -> _DhsHrPreflight:
    source_columns, fields = _resolved_source_columns(dictionary, column_map)
    household_field = fields["household_id"]
    cluster_field = fields["cluster_id"]
    weight_field = fields["source_weight"]
    if household_field is None or cluster_field is None or weight_field is None:
        raise RuntimeError("required HR dictionary fields were unexpectedly unresolved")

    household_counts: Counter[str] = Counter()
    cluster_ids: set[str] = set()
    row_count = 0
    missing_household_ids = 0
    missing_cluster_ids = 0
    missing_psu_ids = 0
    missing_stratum_ids = 0
    missing_weights = 0
    invalid_weights = 0
    nonpositive_weights = 0

    with source_path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            record = _canonical_record_bytes(
                raw_line,
                dictionary=dictionary,
                line_number=line_number,
            )
            row_count += 1

            household = _token_from_record(record, household_field)
            if household is None:
                missing_household_ids += 1
            else:
                household_counts[household] += 1

            cluster = _token_from_record(record, cluster_field)
            if cluster is None:
                missing_cluster_ids += 1
            else:
                cluster_ids.add(cluster)

            psu_field = fields["psu_id"]
            if psu_field is not None and _token_from_record(record, psu_field) is None:
                missing_psu_ids += 1

            stratum_field = fields["stratum_id"]
            if stratum_field is not None and _token_from_record(record, stratum_field) is None:
                missing_stratum_ids += 1

            weight = _token_from_record(record, weight_field)
            if weight is None:
                missing_weights += 1
            else:
                try:
                    numeric_weight = float(weight)
                except ValueError:
                    invalid_weights += 1
                else:
                    if numeric_weight <= 0:
                        nonpositive_weights += 1

    if row_count == 0:
        raise ValueError("DHS fixed-width source contains no household records")

    duplicate_household_id_rows = sum(
        count for count in household_counts.values() if count > 1
    )
    return _DhsHrPreflight(
        row_count=row_count,
        source_columns=source_columns,
        source_schema_sha256=_source_schema_sha256(dictionary),
        missing_household_ids=missing_household_ids,
        duplicate_household_id_rows=duplicate_household_id_rows,
        missing_cluster_ids=missing_cluster_ids,
        cluster_count=len(cluster_ids),
        missing_psu_ids=missing_psu_ids,
        missing_stratum_ids=missing_stratum_ids,
        missing_weights=missing_weights,
        invalid_weights=invalid_weights,
        nonpositive_weights=nonpositive_weights,
    )


def _dataset_ref(snapshot: SourceSnapshotRef) -> DatasetRef:
    return DatasetRef(
        dataset_id="surveys.dhs.hr_households",
        version=snapshot.snapshot_id,
        schema_version="dhs-hr-household-silver-v3-fixed-width",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("source_row_id",)),
    )


def _hashed_output(manifest: RunManifest, dataset_id: str) -> DatasetRef:
    matches = [dataset for dataset in manifest.outputs if dataset.dataset_id == dataset_id]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one materialized dataset {dataset_id!r}")
    return matches[0]


def _json_text(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _build_streaming_qa(
    *,
    dictionary: DhsFixedWidthDictionary,
    profile: _DhsHrPreflight,
    chunk_rows: int,
) -> tuple[QAResult, ...]:
    return (
        QAResult(
            check_id="dhs.hr.canonical_fixed_width_source",
            state="GREEN",
            message="HR Silver is decoded from official fixed-width source bytes and dictionary",
            metrics={
                "source_representation": "official_fixed_width_dat+dct",
                "dictionary_field_count": len(dictionary.fields),
                "dictionary_record_width": dictionary.record_width,
                "dictionary_schema_sha256": dictionary.schema_sha256,
            },
        ),
        QAResult(
            check_id="dhs.hr.bounded_memory_decode",
            state="GREEN",
            message="canonical HR materialization streams bounded row chunks into Parquet",
            metrics={
                "decode_chunk_rows": chunk_rows,
                "whole_release_dataframe_materialized": False,
            },
        ),
        QAResult(
            check_id="dhs.hr.dictionary_schema_coverage",
            state="GREEN",
            message="every parsed dictionary variable is emitted by the canonical decoder",
            metrics={
                "dictionary_field_count": len(dictionary.fields),
                "decoded_column_count": len(dictionary.fields),
                "missing_dictionary_columns": 0,
            },
        ),
        QAResult(
            check_id="dhs.hr.row_retention",
            state="GREEN",
            message="canonical HR Silver preserves one output row per supplied source record",
            metrics={"input_rows": profile.row_count, "output_rows": profile.row_count},
        ),
        QAResult(
            check_id="dhs.hr.household_identity",
            state=(
                "GREEN"
                if profile.missing_household_ids == 0
                and profile.duplicate_household_id_rows == 0
                else "RED"
            ),
            message="household source identifiers are preserved and anomalies remain visible",
            metrics={
                "missing_household_ids": profile.missing_household_ids,
                "duplicate_household_id_rows": profile.duplicate_household_id_rows,
            },
        ),
        QAResult(
            check_id="dhs.hr.cluster_identity",
            state="GREEN" if profile.missing_cluster_ids == 0 else "YELLOW",
            message="source cluster identity is profiled without dropping households",
            metrics={
                "missing_cluster_ids": profile.missing_cluster_ids,
                "cluster_count": profile.cluster_count,
                "missing_psu_ids": profile.missing_psu_ids,
            },
        ),
        QAResult(
            check_id="dhs.hr.source_weight",
            state=(
                "GREEN"
                if profile.missing_weights == 0
                and profile.invalid_weights == 0
                and profile.nonpositive_weights == 0
                else "YELLOW"
            ),
            message="source household weight is unchanged; numeric parsing is diagnostic only",
            metrics={
                "missing_weights": profile.missing_weights,
                "invalid_weights": profile.invalid_weights,
                "nonpositive_weights": profile.nonpositive_weights,
                "source_weight_variable": profile.source_columns["source_weight"],
            },
        ),
        QAResult(
            check_id="dhs.hr.stratum",
            state="GREEN" if profile.missing_stratum_ids == 0 else "YELLOW",
            message="source stratum is preserved without selecting an estimation design",
            metrics={"missing_stratum_ids": profile.missing_stratum_ids},
        ),
        QAResult(
            check_id="dhs.hr.source_variable_preservation",
            state="GREEN",
            message="every dictionary-declared source column is streamed into canonical Silver",
            metrics={
                "source_column_count": len(dictionary.fields),
                "preserved_source_column_count": len(dictionary.fields),
                "schema_sha256": profile.source_schema_sha256,
            },
        ),
    )


def _write_streaming_hr_parquet(
    output: Path,
    *,
    source_path: Path,
    dictionary: DhsFixedWidthDictionary,
    metadata: DhsHrMetadata,
    snapshot: SourceSnapshotRef,
    column_map: DhsHrColumnMap,
    expected_rows: int,
    expected_schema_sha256: str,
    chunk_rows: int,
) -> None:
    writer: pq.ParquetWriter | None = None
    arrow_schema: pa.Schema | None = None
    rows_written = 0
    try:
        for raw in iter_dhs_fixed_width_dat_chunks(
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
                writer = pq.ParquetWriter(output, arrow_schema, compression="zstd")
            elif table.schema != arrow_schema:
                if arrow_schema is None:
                    raise RuntimeError("canonical DHS Parquet schema was not initialized")
                table = table.cast(arrow_schema)
            writer.write_table(table)
            rows_written += len(normalized.frame)
    finally:
        if writer is not None:
            writer.close()

    if rows_written != expected_rows:
        raise RuntimeError(
            f"canonical DHS writer emitted {rows_written} rows; expected {expected_rows}"
        )
    if writer is None:
        raise RuntimeError("canonical DHS writer produced no Parquet row groups")


def materialize_dhs_hr_release_silver(
    *,
    source_path: str | Path,
    dictionary_path: str | Path,
    metadata: DhsHrMetadata,
    column_map: DhsHrColumnMap,
    data_root: DataRoot,
    run_id: str,
    source_snapshot: SourceSnapshotRef | None = None,
    code_commit: str | None = None,
    overwrite: bool = False,
    decode_chunk_rows: int = _DEFAULT_DECODE_CHUNK_ROWS,
) -> tuple[SourceSnapshotRef, DhsHrReleaseSilverResult, RunManifest, DatasetRef, Path]:
    """Publish canonical HR Silver from official DHS ``.DAT + .DCT`` in bounded memory."""

    if decode_chunk_rows <= 0:
        raise ValueError("decode_chunk_rows must be positive")
    source = Path(source_path)
    dictionary_file = Path(dictionary_path)
    _validate_release_inputs(
        source_path=source,
        dictionary_path=dictionary_file,
        metadata=metadata,
    )

    snapshot = source_snapshot or register_dhs_hr_release_snapshot(
        source,
        dictionary_file,
        release=metadata.release,
    )
    if snapshot.source != DHS_SOURCE:
        raise ValueError("canonical DHS HR snapshot must have source='dhs'")
    if snapshot.release != metadata.release:
        raise ValueError("supplied DHS snapshot release does not match verified DHS metadata")
    if len(snapshot.files) != 2:
        raise ValueError("canonical DHS HR snapshot must contain exactly the .DAT and .DCT files")
    _validate_snapshot_file(snapshot, source)
    _validate_snapshot_file(snapshot, dictionary_file)

    dictionary = parse_dhs_stata_dictionary(dictionary_file)
    profile = _profile_release(source, dictionary, column_map)
    catalog = build_dhs_survey_catalog(metadata)
    file_link = build_dhs_hr_file_link(
        catalog=catalog,
        snapshot=snapshot,
        source_path=source,
    )
    qa = _build_streaming_qa(
        dictionary=dictionary,
        profile=profile,
        chunk_rows=decode_chunk_rows,
    )
    silver = DhsHrReleaseSilverResult(
        qa=qa,
        catalog=catalog,
        file_link=file_link,
        source_columns=profile.source_columns,
        schema_sha256=profile.source_schema_sha256,
        row_count=profile.row_count,
        source_column_count=len(dictionary.fields),
    )

    dataset = _dataset_ref(snapshot)
    destination = data_root.silver(
        "surveys", f"dhs/{catalog.survey_id}", snapshot.snapshot_id
    )
    manifest = materialize_files(
        data_root=data_root,
        run_id=run_id,
        source_snapshot=snapshot,
        outputs=(
            FileMaterialization(
                dataset=dataset,
                relative_path="hr_households.parquet",
                destination_base=destination,
                writer=lambda output: _write_streaming_hr_parquet(
                    output,
                    source_path=source,
                    dictionary=dictionary,
                    metadata=metadata,
                    snapshot=snapshot,
                    column_map=column_map,
                    expected_rows=profile.row_count,
                    expected_schema_sha256=profile.source_schema_sha256,
                    chunk_rows=decode_chunk_rows,
                ),
            ),
        ),
        parameters={
            "survey_id": catalog.survey_id,
            "dhs_survey_id": metadata.dhs_survey_id,
            "country_iso3": metadata.country_iso3,
            "survey_year": metadata.survey_year,
            "survey_phase": metadata.survey_phase,
            "source_release": snapshot.release,
            "source_recode": DHS_HR_RECODE,
            "source_file_name": metadata.source_file_name,
            "source_representation": "official_fixed_width_dat+dct",
            "source_dictionary_file_name": dictionary_file.name,
            "source_dictionary_schema_sha256": dictionary.schema_sha256,
            "source_dictionary_field_count": len(dictionary.fields),
            "source_dictionary_record_width": dictionary.record_width,
            "source_schema_sha256": profile.source_schema_sha256,
            "source_column_map": profile.source_columns,
            "decode_strategy": "bounded_row_chunks_to_parquet",
            "decode_chunk_rows": decode_chunk_rows,
            "whole_release_dataframe_materialized": False,
            "physical_row_key": "source_row_id",
            "natural_household_key": ["survey_id", "household_id"],
            "natural_household_key_semantics": (
                "source identity audited for missingness and duplicates; not assumed unique"
            ),
            "source_weight_transformation": None,
            "aggregation": None,
        },
        code_commit=code_commit,
        qa=qa,
        overwrite=overwrite,
    )
    hashed = _hashed_output(manifest, dataset.dataset_id)

    run_artifacts = {
        "catalog/dhs_survey.json": {
            "survey_id": catalog.survey_id,
            "source_family": catalog.source_family,
            "country_iso3": catalog.country_iso3,
            "survey_year": catalog.survey_year,
            "survey_phase": catalog.survey_phase,
            "release": catalog.release,
        },
        "catalog/dhs_hr_file_link.json": {
            "survey_id": file_link.survey_id,
            "source_snapshot_id": file_link.source_snapshot_id,
            "source_file": file_link.source_file.model_dump(mode="json"),
            "instrument": file_link.instrument,
        },
        "mappings/dhs_hr_source_columns.json": {
            "resolved_columns": profile.source_columns,
            "schema_sha256": profile.source_schema_sha256,
        },
        "mappings/dhs_hr_fixed_width_dictionary.json": {
            "dictionary_file_name": dictionary_file.name,
            "schema_sha256": dictionary.schema_sha256,
            "field_count": len(dictionary.fields),
            "record_width": dictionary.record_width,
            "fields": [
                {
                    "name": field.name,
                    "storage_type": field.storage_type,
                    "record": field.record,
                    "start": field.start,
                    "end": field.end,
                }
                for field in dictionary.fields
            ],
        },
    }
    for relative_path, payload in run_artifacts.items():
        persist_run_artifact(
            data_root,
            run_id,
            relative_path,
            _json_text(payload),
            overwrite=overwrite,
        )

    return snapshot, silver, manifest, hashed, destination / "hr_households.parquet"


__all__ = [
    "DhsFixedWidthDictionary",
    "DhsFixedWidthField",
    "DhsHrReleaseSilverResult",
    "iter_dhs_fixed_width_dat_chunks",
    "materialize_dhs_hr_release_silver",
    "parse_dhs_stata_dictionary",
    "read_dhs_fixed_width_dat",
    "register_dhs_hr_release_snapshot",
]
