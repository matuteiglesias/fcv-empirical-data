# DHS Household Recode (HR) Silver

This vertical materializes externally stored DHS Household Recode releases at their natural
**household-within-survey** grain. It is a source-data product, not a measurement or experiment
product.

## Canonical source authority

Canonical HR ingestion no longer treats convenience CSV exports as authoritative source files.
The governed path is the source-native fixed-width release distributed by DHS:

```text
official <release>.DAT
        +
distributed <release>.DCT
        |
        v
verified fixed-width decoding
        |
        v
source-native household table
        |
        v
surveys.dhs.hr_households Silver
```

The `.DAT` contains the protected household records. The matching Stata `.DCT` is the executable
field layout: variable names, physical record number, and byte positions. Both files are registered
in the same immutable `SourceSnapshotRef`, so the materialized dataset is bound to both the source
bytes and the exact schema used to decode them.

This migration was motivated by a real local failure mode: legacy CSV representations of several
DHS-VII HR releases omitted standard variables that remained explicitly present in the distributed
`.DCT`, `.DO`, `.MAP`, `.SAS`, `.SPS`, `.FRQ`, and underlying fixed-width layout. A convenience CSV
therefore cannot establish completeness of an HR release.

The former tabular materializer remains available only as
`materialize_dhs_hr_legacy_tabular_silver` for explicit legacy/parity investigation. Package-level
`materialize_dhs_hr_silver` now resolves to the canonical fixed-width implementation.

## Public API

```python
from fcv_empirical.surveys import (
    STANDARD_DHS_HR_COLUMNS,
    DhsHrMetadata,
    materialize_dhs_hr_silver,
)

snapshot, silver, manifest, dataset, output = materialize_dhs_hr_silver(
    source_path="NGHR7BFL.DAT",
    dictionary_path="NGHR7BFL.DCT",
    metadata=DhsHrMetadata(..., source_file_name="NGHR7BFL.DAT"),
    column_map=STANDARD_DHS_HR_COLUMNS,
    data_root=data_root,
    run_id="...",
    code_commit="...",
)
```

`materialize_dhs_hr_release_silver(...)` is the explicit canonical function name.
`materialize_dhs_hr_silver(...)` is its package-level alias.

The source and dictionary must:

- be `.DAT` and `.DCT` respectively;
- have the same release-file stem;
- match the verified `DhsHrMetadata` source filename;
- appear exactly once in the supplied snapshot;
- retain their registered SHA-256 hashes.

The fixed-width reader currently supports the single physical-record layout used by the household
recode files commissioned here. A dictionary declaring multiple physical record numbers fails
closed rather than being guessed into a household table.

## Identity

`DhsHrMetadata` requires a verified DHS survey identifier plus country, year, phase, release,
recode family, and exact source data filename. `survey_id` is built from the verified DHS survey
identifier, **not** inferred from the release filename.

The `.DAT` file remains the linked survey data file in `SurveyFileLink`; the `.DCT` is a companion
schema member of the same source snapshot. Because `source_row_id` contains the source snapshot
identity, migrating from a lossy CSV snapshot to the authoritative `.DAT + .DCT` snapshot
intentionally creates a new physical row-identity namespace.

One survey may also have PR, IR, GE/GPS, GC, and other acquisition files in independent source
snapshots. No cross-recode identity is inferred here.

## Decoding semantics

`parse_dhs_stata_dictionary(...)` parses the distributed fixed-width declarations and validates:

- non-empty field schema;
- unique variable names, case-insensitively;
- positive ordered byte positions;
- no overlapping field ranges;
- one physical record per household line.

The decoder accepts both sized DHS `str#` declarations and the unsized `str` declarations found in
real distributed dictionaries. A short physical record is right-padded only with blank bytes,
matching DHS's omitted all-blank suffix convention; non-whitespace bytes beyond the dictionary width
still fail closed.

All parsed dictionary variables are decoded. The ingestion layer does not select only variables used
by the current FCV research design.

### Bounded-memory canonical materialization

`read_dhs_fixed_width_dat(...)` remains a convenience whole-table decoder for tiny fixtures and
local discovery. **Canonical materialization does not call it for a real release.** A full DHS HR
release can have thousands of dictionary fields, so constructing the complete release as one pandas
string DataFrame is not a supported production strategy.

The canonical materializer instead performs two bounded operations:

1. a cheap preflight pass over the physical records that reads only the release-verified identity and
   design fields needed for QA (`HHID`, cluster, weight, PSU and stratum); and
2. complete dictionary decoding in bounded row chunks, with each normalized chunk written directly
   to Parquet before the next chunk is decoded.

The default chunk size is 512 household records and may be lowered explicitly through
`decode_chunk_rows` when a host has tighter memory constraints. The complete source-native table is
therefore durable in `hr_households.parquet`, but it is deliberately not returned as one in-memory
pandas DataFrame.

`source_row_id` remains global across chunks. Physical row numbering uses the absolute source-record
position, so chunk boundaries cannot create duplicate row identities.

The returned `DhsHrReleaseSilverResult` is a bounded-memory summary containing catalog/link, source
column mapping, schema fingerprint, row/column counts, and QA. The durable Parquet file remains the
complete HR Silver data product.

## Source-variable and design preservation

The materializer adds a normalized envelope while retaining every dictionary-decoded source column
and value.

The release-specific `DhsHrColumnMap` remains explicit. `STANDARD_DHS_HR_COLUMNS` uses the standard
DHS names:

```text
HHID   household identifier
HV001  cluster
HV005  household sample weight
HV021  PSU
HV022  stratum
```

The source household weight is copied unchanged. This layer does not divide `HV005` by one million,
normalize weights, or select an estimator design.

Standard variables such as `HV025`, `HV201`, `HV206`, `HV270`, and `HV271` remain ordinary
source-native columns when declared by the release dictionary. Semantic interpretation remains the
responsibility of the separate DHS variable registry.

## QA and provenance

Canonical fixed-width runs add explicit QA for:

- canonical `.DAT + .DCT` source representation;
- bounded-memory chunked decoding with `whole_release_dataframe_materialized = false`;
- dictionary field count and maximum record width;
- dictionary schema SHA-256;
- complete dictionary-to-decoded-column coverage.

The HR QA continues to record:

- input and output household-row counts;
- missing and duplicate household IDs;
- missing cluster and PSU IDs;
- missing, invalid, and nonpositive source weights;
- missing stratum IDs;
- source-column preservation;
- source-table schema fingerprint.

The run additionally persists a non-sensitive dictionary schema sidecar:

```text
artifacts/mappings/dhs_hr_fixed_width_dictionary.json
```

It contains field names, source storage types, byte positions, dictionary fingerprint, field count,
and record width, but no household values.

The resulting dataset remains:

```text
surveys.dhs.hr_households
```

with schema version:

```text
dhs-hr-household-silver-v3-fixed-width
```

and `L3_REBUILT` authority. Nothing in ingestion grants L4 research authority.

## Protected-data boundary

The repository never copies DHS microdata into Git. GitHub tests generate tiny synthetic `.DAT` and
`.DCT` fixtures only.

Real `.DAT` files and generated HR Silver remain under the configured external `DataRoot`. Review
material may expose release identity, hashes, schema metadata, row counts, and aggregate QA, but not
household rows, local protected paths, or identifiers.

Companion documentation such as `.DO`, `.MAP`, `.SAS`, `.SPS`, `.FRQ`, and `.FRW` remains valuable
for local release validation and code-label interpretation. It is not required to decode the HR
fixed-width table and is therefore not added to the canonical HR snapshot merely for convenience.
