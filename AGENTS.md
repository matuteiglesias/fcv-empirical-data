# Agent Instructions — fcv-empirical-data

## Mission

This repository is the FCV empirical-data domain layer between `empirical-data-contracts` +
`spatial-data-foundation` and `fcv-experiment-harness`. It owns faithful source measurements,
provenance, coverage/measurement contracts, source-native semantics, durable materialization,
QA evidence, and parity evidence. It does not own causal design.

## Scientific firewall

Never introduce treatment, control, counterfactual, estimator, fixed-effect, matching,
outcome-timing, estimator-imputation, falsification, or causal-interpretation semantics here.
Those belong in the experiment harness.

Never:

- convert missing values or absent rows to zero by default;
- infer untreated/no-event/no-project meaning from row absence;
- destroy source names, source-native identifiers, releases, snapshots, or hashes;
- allocate or multiply a financial amount across locations without a named explicit allocation rule;
- silently harmonize one empirical source to another;
- implement another point-in-polygon engine or use display geometry for analytics;
- reimplement period formulas or `T`/`y0` logic;
- force events, projects, project locations, or respondents into one common grain;
- grant new materializations L4 research authority automatically;
- treat legacy equality as a correctness requirement;
- commit large empirical files, generated source snapshots, or recovered archives.

## Required upstream reuse

Use public models from `empirical-data-contracts`, especially `SourceSnapshotRef`, `DatasetRef`,
`CoverageContract`, `MeasurementContract`, `QAResult`, and `RunManifest`. Do not fork these models.

Use `spatial-data-foundation` for `DataRoot`, analytical geography, spatial membership, and
`PeriodIndex`. FCV configuration may select geographies or period schemes, but this package must not
reimplement those engines.

## Data and materialization rules

- Natural/source-native grain survives ingestion and durable normalized layers.
- Bronze is a provenance concept. An externally stored immutable multi-GB file does not need to be
  duplicated under a local `bronze/` directory.
- Every durable output must trace to input snapshot identity, code revision when supplied, output
  content hash, parameters, and contract-backed QA.
- Default behavior refuses overwrite. A rerun that changes artifacts must be explicit.
- Failed materialization must remain visibly failed; it must not leave a success manifest.
- Derived annotation products remain derived and versioned; prompts/models/configuration are part of
  their provenance. Annotation does not grant research validation authority.

## Development style

Prefer contracts + plain tables + pure functions + small adapters. Keep generic modules free of
ACLED, World Bank, AidData, DHS, Afrobarometer, treatment, regression, and matching concepts. Use
tiny synthetic fixtures in CI. Preserve existing legacy/source-specific material unless a separate,
explicit migration is requested.
