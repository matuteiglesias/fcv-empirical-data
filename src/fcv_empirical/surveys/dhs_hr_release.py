from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pandas as pd
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

from .dhs_hr import (
    DHS_HR_RECODE,
    DHS_ORIGIN,
    DHS_SOURCE,
    DhsHrColumnMap,
    DhsHrMetadata,
    DhsHrSilverResult,
    normalize_dhs_hr,
)

_DCT_FIELD_RE = re.compile(
    r"^\s*(?P<storage>byte|int|long|float|double|str\d*)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+"
    r"(?P<record>\d+):\s*(?P<start>\d+)\s*-\s*(?P<end>\d+)\s*$",
    re.IGNORECASE,
)
_DCT_DECLARATION_HINT_RE = re.compile(r"\b\d+\s*:\s*\d+\s*-\s*\d+\b")


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


def parse_dhs_stata_dictionary(dictionary_path: str | Path) -> DhsFixedWidthDictionary:
    """Parse the fixed-width field layout from a DHS-distributed Stata ``.DCT`` file."""

    path = Path(dictionary_path)
    if path.suffix.casefold() != ".dct":
        raise ValueError("canonical DHS fixed-width ingestion requires a .DCT dictionary")

    fields: list[DhsFixedWidthField] = []
    for line_number, line in enumerate(path.read_text(encoding="latin-1").splitlines(), start=1):
        match = _DCT_FIELD_RE.match(line)
        if match is not None:
            fields.append(
                DhsFixedWidthField(
                    name=match.group("name"),
                    storage_type=match.group("storage").casefold(),
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


def read_dhs_fixed_width_dat(
    source_path: str | Path,
    dictionary: DhsFixedWidthDictionary,
) -> pd.DataFrame:
    """Decode one official DHS fixed-width ``.DAT`` into a plain source-native table."""

    path = Path(source_path)
    if path.suffix.casefold() != ".dat":
        raise ValueError("canonical DHS fixed-width ingestion requires a .DAT source file")

    records: list[dict[str, object]] = []
    with path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.rstrip(b"\r\n")
            if len(line) < dictionary.record_width:
                # DHS release writers omit an all-blank suffix instead of serializing it.
                # Restore that source-equivalent padding before fixed-position decoding.
                line = line.ljust(dictionary.record_width, b" ")
            trailing = line[dictionary.record_width :]
            if trailing.strip():
                raise ValueError(
                    f"DHS fixed-width record {line_number} has non-whitespace bytes beyond "
                    "dictionary width"
                )

            row: dict[str, object] = {}
            for field in dictionary.fields:
                token = line[field.start - 1 : field.end].decode("latin-1").strip()
                row[field.name] = pd.NA if token == "" else token
            records.append(row)

    return pd.DataFrame(records, columns=dictionary.column_names, dtype="string")


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
) -> tuple[SourceSnapshotRef, DhsHrSilverResult, RunManifest, DatasetRef, Path]:
    """Publish canonical HR Silver from official DHS ``.DAT + .DCT`` release files."""

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
    raw = read_dhs_fixed_width_dat(source, dictionary)
    silver = normalize_dhs_hr(
        raw,
        metadata=metadata,
        snapshot=snapshot,
        source_path=source,
        column_map=column_map,
    )

    required_source_columns = tuple(
        value
        for value in (
            column_map.household_id,
            column_map.cluster_id,
            column_map.source_weight,
            column_map.psu_id,
            column_map.stratum_id,
        )
        if value is not None
    )
    dictionary_columns = {name.casefold() for name in dictionary.column_names}
    missing_required = sorted(
        variable
        for variable in required_source_columns
        if variable.casefold() not in dictionary_columns
    )
    if missing_required:
        raise ValueError(
            "DHS fixed-width dictionary is missing release-verified HR fields: "
            + ", ".join(missing_required)
        )

    fixed_width_qa = (
        QAResult(
            check_id="dhs.hr.canonical_fixed_width_source",
            state="GREEN",
            message="HR Silver was decoded from official fixed-width source bytes and dictionary",
            metrics={
                "source_representation": "official_fixed_width_dat+dct",
                "dictionary_field_count": len(dictionary.fields),
                "dictionary_record_width": dictionary.record_width,
                "dictionary_schema_sha256": dictionary.schema_sha256,
            },
        ),
        QAResult(
            check_id="dhs.hr.dictionary_schema_coverage",
            state="GREEN",
            message="every parsed dictionary variable is present in the decoded HR table",
            metrics={
                "dictionary_field_count": len(dictionary.fields),
                "decoded_column_count": len(raw.columns),
                "missing_dictionary_columns": 0,
            },
        ),
    )
    silver = replace(silver, qa=fixed_width_qa + silver.qa)

    dataset = _dataset_ref(snapshot)
    destination = data_root.silver(
        "surveys", f"dhs/{silver.catalog.survey_id}", snapshot.snapshot_id
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
                writer=lambda output: silver.frame.to_parquet(output, index=False),
            ),
        ),
        parameters={
            "survey_id": silver.catalog.survey_id,
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
            "source_schema_sha256": silver.schema_sha256,
            "source_column_map": silver.source_columns,
            "physical_row_key": "source_row_id",
            "natural_household_key": ["survey_id", "household_id"],
            "natural_household_key_semantics": (
                "source identity audited for missingness and duplicates; not assumed unique"
            ),
            "source_weight_transformation": None,
            "aggregation": None,
        },
        code_commit=code_commit,
        qa=silver.qa,
        overwrite=overwrite,
    )
    hashed = _hashed_output(manifest, dataset.dataset_id)

    run_artifacts = {
        "catalog/dhs_survey.json": {
            "survey_id": silver.catalog.survey_id,
            "source_family": silver.catalog.source_family,
            "country_iso3": silver.catalog.country_iso3,
            "survey_year": silver.catalog.survey_year,
            "survey_phase": silver.catalog.survey_phase,
            "release": silver.catalog.release,
        },
        "catalog/dhs_hr_file_link.json": {
            "survey_id": silver.file_link.survey_id,
            "source_snapshot_id": silver.file_link.source_snapshot_id,
            "source_file": silver.file_link.source_file.model_dump(mode="json"),
            "instrument": silver.file_link.instrument,
        },
        "mappings/dhs_hr_source_columns.json": {
            "resolved_columns": silver.source_columns,
            "schema_sha256": silver.schema_sha256,
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
    "materialize_dhs_hr_release_silver",
    "parse_dhs_stata_dictionary",
    "read_dhs_fixed_width_dat",
    "register_dhs_hr_release_snapshot",
]
