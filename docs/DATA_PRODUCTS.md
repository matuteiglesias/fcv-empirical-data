# Data products

This repository expects source verticals to publish a small set of interoperable artifacts rather
than a universal event/project/survey class hierarchy.

## Source snapshot reference

A durable source input is identified by `SourceSnapshotRef`: source, release, snapshot identity,
storage mode, file paths, sizes, and SHA-256 hashes. External immutable storage is supported and is
preferred to gratuitous duplication of large files.

## Materialized dataset

Every durable output has a `DatasetRef` with its natural grain, layer, authority, schema/version
identity, and content hash. Source-native IDs must remain columns/keys in the underlying table even
when analytical annotations are added later.

A source adapter may emit multiple dataset refs when the source naturally contains distinct grains
(for example project records and project-location records). It must not force them into one lattice.

## Run manifest

Every materialization run persists the shared `RunManifest` under
`runs/fcv-empirical-data/<run_id>/run_manifest.json`. It records inputs, parameters, code revision
when supplied, outputs, timestamps, and QA. Failure manifests carry no successful outputs.

## QA artifact

QA remains a collection of upstream `QAResult` objects. The kernel can accumulate and serialize
these results; it does not define a dashboard or transform missingness into scientific meaning.

## Parity report

Parity reports are generic JSON-compatible conventions recording legacy/new dataset refs, row counts,
key overlap, legacy-only/new-only counts, numerical differences, discrepancy categories, status, and
notes. `EXPLAINED_DIVERGENCE` is a valid status: legacy parity is evidence to explain, not an
automatic reproduction requirement.

## Future source verticals

ACLED, World Bank, AidData, DHS, Afrobarometer, and other adapters should live in separate
source-focused modules/PRs. They may use this kernel for paths, manifests, hashes, QA, and parity but
must define source semantics explicitly and must not import experiment-design semantics upstream.
