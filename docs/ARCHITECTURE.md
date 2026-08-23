# FCV empirical-data architecture

## Boundary

```text
empirical-data-contracts        spatial-data-foundation
          \                         /
           \                       /
                 fcv_empirical
                      |
                      v
              source data products
                      |
                      v
             fcv-experiment-harness
```

`fcv_empirical` is the domain boundary for faithful empirical measurements and their provenance. It
may normalize source-native records, attach contract-backed geography/period annotations, describe
coverage and uncertainty, and materialize durable data products. It must preserve natural grain and
source identity.

The experiment harness owns treatment/control definitions, outcome timing, matching, estimators,
fixed effects, estimator-specific imputation, falsification, and causal interpretation. No generic
helper in this repository may infer those concepts from source data.

## Shared contracts and infrastructure

Use `empirical_contracts.SourceSnapshotRef`, `DatasetRef`, `CoverageContract`,
`MeasurementContract`, `QAResult`, and `RunManifest` as the compatibility contracts. Serialized
forms are upstream public API; do not create local substitutes.

Use `spatial_foundation.DataRoot` for path roots, `spatial_foundation` geography/membership APIs for
analytical geography, and `PeriodIndex` with `PeriodScheme` for periodization. Display geometry is
never an analytical substitute.

## Data root policy

Consumers may construct one shared root from an explicit configuration such as
`EMPIRICAL_DATA_ROOT`:

```text
bronze/
silver/
gold/
runs/
```

The package does not read that environment variable automatically and keeps no global mutable root.
External immutable snapshots are first-class: bronze provenance does not require copying a large
source file.

Canonical rebuilt products may publish into `DataRoot.silver(...)` or `DataRoot.gold(...)` while run
contracts and audit sidecars remain under:

```text
runs/fcv-empirical-data/<run_id>/
```

The kernel percent-encodes run/dataset identity components and confines relative artifact paths. A
caller-supplied stable destination must resolve inside the shared `DataRoot`; it cannot be used as an
arbitrary filesystem escape hatch.

## Materialization lifecycle

`materialize_files` accepts one or more small `FileMaterialization` requests, parameters, optional
code commit, caller QA, and one of two input forms:

- a `SourceSnapshotRef` for a source rebuild; or
- one or more upstream `DatasetRef` objects for a derived product.

Each request pairs an existing `DatasetRef` with a relative path and writer. It may stay run-scoped or
publish under a stable Silver/Gold base. `materialize_file` is the one-output convenience wrapper. The
kernel:

1. constructs deterministic run or stable layer paths through `DataRoot`;
2. refuses an existing output or manifest unless overwrite is explicit;
3. runs every writer against a staged file in its destination directory;
4. computes SHA-256 and records it in each output `DatasetRef`;
5. validates all staged outputs before publication begins;
6. publishes with atomic no-clobber behavior on the default path;
7. persists one upstream `RunManifest` with input contracts, all outputs, and QA.

This distinction makes lineage accurate. A source materialization can say:

```text
SourceSnapshotRef -> Silver DatasetRef
```

while a derived annotation view can say:

```text
Silver DatasetRef(s) -> L2 derived DatasetRef
```

instead of pretending that every downstream product reads raw source files directly.

A failure produces a finished manifest with no successful outputs and a reserved RED
`materialization.status` QA result, then re-raises the exception. Under the default no-overwrite
policy, outputs published by the failing call are removed before the failure manifest is written.
Explicit overwrite is destructive and cannot promise rollback of prior artifacts. The operational
status is not scientific authority.

Because each output carries its own upstream `DatasetRef.grain`, a single source run can emit, for
example, project records and project-location records without coercing them into one lattice.

Non-dataset QA, parity, input-contract, and mapping sidecars can be written under the confined
`artifacts/` run namespace. They supplement rather than replace the `RunManifest` contracts.

## Absence and zero

Generic helpers never resolve absence. `presence_counts` deliberately counts `0` as observed and
`None` as missing. A structural-zero interpretation requires an explicit upstream
`CoverageContract`/`MeasurementContract` that licenses it. Creating the analysis lattice and
applying treatment/control meaning remains downstream work.

## Investment implementation

The first source vertical is documented in `docs/INVESTMENTS.md`. AidData CLG-LMIC and World Bank
Projects API remain separate source-native Silver products; the optional common annotation surface is
explicitly derived and lower-authority. None of those products defines treated projects.
