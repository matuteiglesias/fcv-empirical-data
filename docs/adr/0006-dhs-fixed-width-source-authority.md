# ADR 0006 — DHS HR source authority is fixed-width release data plus dictionary

## Status

Accepted by implementation on the DHS HR fixed-width migration branch.

## Decision

Canonical DHS Household Recode ingestion uses the official fixed-width `.DAT` file together with the
distributed Stata `.DCT` dictionary required to decode it.

Convenience `.csv`, `.dta`, or `.parquet` representations are not authoritative HR ingestion inputs
unless a future explicit parity product proves their completeness against the source-native release.
The existing tabular materializer remains available only as an explicitly named legacy/parity path.

## Rationale

Real DHS-VII release directories for Nigeria 2018, Uganda 2016, and Zambia 2018 showed that legacy CSV
representations omitted standard variables such as `HV025`, `HV206`, and `HV270`, while the distributed
release dictionaries and companion syntax files continued to declare those variables at fixed source
positions. Treating the convenience CSV as authoritative therefore confused a lossy derivative with
the source release.

The `.DAT + .DCT` pair is sufficient to reproduce the complete household table used by the governed
HR Silver layer. Both files are hash-bound in one `SourceSnapshotRef`.

## Consequences

- package-level `materialize_dhs_hr_silver` points to canonical fixed-width ingestion;
- canonical HR Silver moves to schema `dhs-hr-household-silver-v3-fixed-width`;
- migration creates a new source snapshot and therefore a new physical `source_row_id` namespace;
- all dictionary-declared variables are decoded, rather than selecting only currently used fields;
- malformed dictionaries, overlapping fields, short records, changed source bytes, and multi-record
  layouts fail closed;
- legacy tabular ingestion remains accessible only for explicit comparison or archaeology;
- semantic variable definitions and commissioning formulas do not change merely because source
  authority changed.
