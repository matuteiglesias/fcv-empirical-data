# fcv-empirical-data

**Reproducible, source-faithful empirical data infrastructure for the FCV research stack.**

`fcv-empirical-data` is the empirical measurement and materialization layer between shared data contracts / spatial infrastructure and downstream experimental analysis.

It turns registered source snapshots into durable, provenance-rich empirical products while preserving source semantics, natural observation grain, missingness, geographic uncertainty, and reproducible lineage.

```text
empirical-data-contracts        spatial-data-foundation
          \                         /
           \                       /
                 fcv_empirical
                      |
                      v
             empirical data products
                      |
                      v
             fcv-experiment-harness
```

The central boundary is deliberate:

> **This repository describes what was observed and how it was measured.
> It does not decide what constitutes treatment, control, an outcome, a counterfactual, or a causal effect.**

---

## Status

Current package version: **0.1.0**

Python: **3.10+**

CI currently tests Python **3.10** and **3.12**.

The repository contains working empirical infrastructure for:

* investment and development-finance sources;
* DHS household, GPS/geography, geospatial-covariate, and survey metadata products;
* ACLED conflict-event measurements;
* generic materialization, QA, provenance, parity, and run-manifest infrastructure.

Most automated tests use small synthetic fixtures. Protected, licensed, or large empirical source files remain external to the repository.

---

## Why this repository exists

Empirical research pipelines often collapse several distinct jobs into one script:

```text
download data
    ↓
clean data
    ↓
change units / geography / time
    ↓
decide treatment
    ↓
construct outcomes
    ↓
estimate model
```

That makes it difficult to distinguish source facts from analytical decisions.

`fcv-empirical-data` separates the empirical layer:

```text
source snapshot
    ↓
source-native reconstruction
    ↓
QA + provenance
    ↓
contract-backed geography / period relations
    ↓
durable empirical measurements
    ↓
downstream experimental design
```

The result is an empirical substrate that can be inspected, reproduced, challenged, and reused independently of a particular estimator or research design.

---

# Core principles

## 1. Preserve source identity

Source releases, snapshot IDs, original identifiers, field names, file hashes, and source-specific semantics remain recoverable.

Normalization may make data easier to work with, but it must not erase where a measurement came from.

---

## 2. Preserve natural grain

Projects, project locations, households, respondents, survey clusters, conflict events, and area-period measurements are different empirical objects.

They are not forced into a universal table merely because a later analysis may relate them.

A single source may therefore materialize several `DatasetRef` objects with different grains.

---

## 3. Missing is not zero

Absence is never silently converted into substantive zero.

Examples:

* no project row does not automatically mean no investment;
* no ACLED Gold row does not automatically mean zero violence;
* a missing financial amount is not zero finance;
* an unmapped survey code is not a zero-valued measurement.

Structural-zero interpretations require explicit coverage and measurement contracts.

---

## 4. Source systems remain distinct

Independent source systems are not silently concatenated, deduplicated, harmonized, or treated as additive.

For example:

```text
AidData ≠ World Bank
ACLED   ≠ UCDP
```

Any future cross-source reconciliation must be an explicit, versioned empirical product.

---

## 5. Geography is an empirical relation

Spatial membership is delegated to `spatial-data-foundation`.

This package does not maintain a competing point-in-polygon implementation, and display geometry is never substituted for analytical geometry.

Ambiguous, boundary, outside, missing-coordinate, and invalid-coordinate cases remain visible rather than being silently assigned.

---

## 6. Time is contract-backed

Shared `PeriodIndex` / `PeriodScheme` infrastructure owns periodization.

Source adapters preserve source dates and timing semantics rather than inventing local `T`, `y0`, event-time, or treatment-time formulas.

---

## 7. Derived products declare their authority

A convenient derived table is not automatically a scientifically validated measurement.

The repository distinguishes, among other things:

* rebuilt source-native products;
* derived review or annotation products;
* coverage and measurement contracts;
* legacy parity evidence;
* research-validation authority.

Producing a table does not by itself grant that table stronger scientific authority.

---

## 8. Provenance is part of the product

Durable outputs should trace back to:

```text
source snapshot / upstream DatasetRef
        +
parameters
        +
code revision, when supplied
        +
QA evidence
        +
output content hash
```

A successful data file without recoverable lineage is incomplete infrastructure.

---

# Architecture

## Upstream contracts

The package reuses public contracts from `empirical-data-contracts`, including:

* `SourceSnapshotRef`
* `DatasetRef`
* `CoverageContract`
* `MeasurementContract`
* `QAResult`
* `RunManifest`

These serialized contracts are part of the compatibility boundary. Local substitutes should not be created.

## Shared spatial infrastructure

`spatial-data-foundation` provides:

* `DataRoot`
* analytical geography
* spatial membership
* source snapshot registration helpers
* `PeriodIndex`
* shared periodization infrastructure

## Downstream analysis

`fcv-experiment-harness` owns analytical and causal design, including:

* treatment/control definitions;
* treatment timing;
* outcome timing;
* matching;
* fixed effects;
* estimator-specific imputation;
* regressions and estimators;
* falsification;
* causal interpretation.

Those concepts should not leak upstream into this repository.

---

# Data layers

The repository uses the familiar Bronze / Silver / Gold vocabulary, but treats these as empirical roles rather than rigid storage requirements.

## Bronze

Bronze identifies source authority.

A large immutable source file may remain in external storage. It does **not** need to be copied into a local `bronze/` directory merely to qualify as Bronze.

A `SourceSnapshotRef` records the source files, release, snapshot identity, sizes, hashes, and storage mode.

## Silver

Silver contains reconstructed, source-faithful empirical records.

Typical properties:

* natural/source-native grain;
* source IDs retained;
* source semantics preserved;
* descriptive normalization allowed;
* missingness remains explicit;
* no treatment or causal interpretation.

## Gold

Gold contains explicit empirical measurements or derived products.

Gold does **not** automatically mean “final” or “research validated.”

Authority remains encoded separately. For example, an annotation-review table may live under `gold/` while remaining explicitly `L2_DERIVED`.

---

# Materialization kernel

The generic kernel is exposed from `fcv_empirical`:

```python
from fcv_empirical import (
    FileMaterialization,
    materialize_file,
    materialize_files,
    output_path,
    persist_run_artifact,
    persist_run_manifest,
    run_artifact_path,
    run_path,
)
```

`materialize_files(...)` handles one or more durable outputs under a shared run manifest.

The lifecycle is approximately:

```text
input contracts
    ↓
construct destinations
    ↓
stage all outputs
    ↓
hash + validate all outputs
    ↓
publish
    ↓
persist RunManifest
```

Important operational properties include:

* deterministic paths;
* staged writes;
* SHA-256 output hashes;
* default no-overwrite behavior;
* path confinement inside the shared `DataRoot`;
* one manifest covering multiple natural-grain outputs;
* explicit source-vs-derived lineage;
* failure manifests;
* cleanup of newly published outputs when a default no-overwrite run fails.

A failed materialization remains visibly failed rather than leaving behind a success manifest.

Explicit `overwrite=True` is intentionally destructive and cannot guarantee rollback of prior artifacts.

---

# Data root

Consumers construct and pass an explicit shared `DataRoot`.

The package does not automatically read an environment variable or maintain global mutable data-root state.

```python
from spatial_foundation import DataRoot

data_root = DataRoot.from_path("/path/to/empirical-data")
```

The conventional layout is:

```text
<DATA_ROOT>/
├── bronze/
├── silver/
├── gold/
└── runs/
    └── fcv-empirical-data/
        └── <run_id>/
            ├── run_manifest.json
            └── artifacts/
```

Canonical products may be published under `silver/` or `gold/`.

Run-specific manifests, QA reports, mappings, parity reports, input-contract evidence, and other sidecars remain under:

```text
runs/fcv-empirical-data/<run_id>/
```

---

# Current empirical verticals

## Investments

The investment domain currently includes several deliberately separate source systems.

### AidData CLG-LMIC

The AidData CLG-LMIC pipeline reconstructs the source workbook as relational Silver data.

The materialization preserves, among other products:

```text
records.parquet
borrower_ownership.parquet
country_list.parquet
definitions_records.parquet
definitions_borrower_ownership.parquet
column_name_mapping.parquet
```

Borrower ownership remains a child relation and may contain several rows per `aiddata_record_id`.

Source columns remain recoverable, and normalization is reversible through the column mapping.

```python
from fcv_empirical.investments import (
    extract_aiddata_workbook,
    materialize_aiddata_silver,
    register_aiddata_snapshot,
)
```

### World Bank Projects API

World Bank downloaded API page JSON is treated as source authority.

The Silver product preserves exact World Bank project identity and source-native field meanings.

Nested objects may be flattened for analytical access, but the original source record remains reconstructible.

Important examples of semantic preservation:

```text
boardapprovaldate = board approval date
closingdate       = closing date
totalcommamt      = source total-commitment field
```

They are not silently reinterpreted as implementation start, project completion, or local spending.

```python
from fcv_empirical.investments import (
    flatten_worldbank_record,
    load_worldbank_pages,
    materialize_worldbank_silver,
    register_worldbank_snapshot,
)
```

### AidData GeoGCDF

The GeoGCDF vertical registers an official bulk geospatial artifact and reconstructs source-native project geometry Silver.

It preserves source project IDs and attributes, keeps original source columns, audits geometry rather than coercing it, and converts analytical geometry to a declared CRS without inventing centroid-based semantics.

The vertical also provides explicit geography, period, coverage, measurement, and Gold commitment-measurement infrastructure.

```python
from fcv_empirical.investments import (
    materialize_geogcdf_measurement,
    materialize_geogcdf_silver,
    materialize_geogcdf_vertical,
    register_geogcdf_snapshot,
)
```

### Annotation candidates

AidData and World Bank may be projected into a common **review-oriented derived surface**.

This exists for annotation convenience, not source harmonization.

The annotation product:

* consumes content-hashed Silver `DatasetRef` inputs;
* records exact mapping provenance;
* remains `L2_DERIVED`;
* does not modify Silver;
* does not run an LLM itself;
* does not classify projects as treated/non-treated;
* does not promote convenience aliases into source ontology.

Reusable flow assets for annotation experiments are also kept under:

```text
investments/flows/annotation_v1/
```

---

## Surveys and DHS

`fcv_empirical.surveys` provides a source-native substrate for complex surveys.

Its purpose is to preserve distinctions that are often lost when survey data are immediately flattened into analysis tables.

### Generic survey substrate

Core objects include:

```python
from fcv_empirical.surveys import (
    ObservationGrain,
    SurveyCatalogEntry,
    SurveyDesignRecord,
    SurveyFileLink,
    SurveyGeographyLink,
    SurveyVariableMetadata,
    TemporalSemantics,
    WeightValue,
)
```

The substrate distinguishes:

```text
survey identity
≠ source file
≠ acquisition snapshot
≠ observation grain
≠ sampling design
≠ geography membership
≠ variable temporal semantics
```

Sampling weights are preserved as source facts without selecting a weight for estimation.

Geography is represented as a candidate relation.

Variable timing is explicit semantic metadata; unknown timing remains `UNKNOWN`.

### DHS HR household Silver

The DHS HR implementation reconstructs household-level Silver data while preserving source-facing identity and metadata.

It provides:

```python
from fcv_empirical.surveys import (
    build_dhs_survey_catalog,
    materialize_dhs_hr_silver,
    normalize_dhs_hr,
    register_dhs_hr_snapshot,
)
```

The physical grain recorded in the resulting `DatasetRef` is treated as a factual uniqueness claim rather than a descriptive label.

### DHS GPS / GE

Public DHS coordinates are treated as **reported, potentially displaced coordinates**, not exact household or cluster locations.

The package can materialize GPS cluster Silver and a separate reported-coordinate geography relation.

```python
from fcv_empirical.surveys import (
    assign_dhs_reported_coordinate_membership,
    materialize_dhs_gps_silver,
    materialize_dhs_reported_coordinate_membership,
    register_dhs_gps_snapshot,
)
```

Geographic uncertainty remains evidence rather than being silently resolved.

### DHS Geospatial Covariates

DHS Geospatial Covariates remain cluster-linked empirical measurements.

They are not automatically expanded into polygon-wide covariates or an area-period panel.

Temporal semantics remain explicit, including distinctions such as:

```text
static
survey_time
annual
epoch
climatology
retrospective
unknown
```

Missing source year does not license assignment to survey year.

### Integrated DHS substrate

HR, GPS, and GC products remain separate natural-grain datasets.

`build_dhs_survey_integration_report(...)` validates whether they can be related consistently without constructing a canonical mega-table.

The integration report keeps discrepancies visible, including:

* HR-only clusters;
* GPS-only clusters;
* GC-only clusters;
* missing identities;
* non-unique contracted grains;
* possible textual ID normalization issues.

No treatment, outcome, regression weight, or experimental role is created by this integration step.

### DHS semantic measurement registry

A small codebook-backed variable registry maps selected DHS variables to reusable **empirical measurements**, not analysis roles.

The current DHS-VII HR registry includes definitions for measurements such as:

```text
HV206 → household electricity access
HV270 → household wealth quintile
HV201 → drinking-water source code
```

Definitions require explicit codebook provenance.

Missing codes, absent source values, unknown codes, and mapped values remain distinguishable.

---

## Violence / ACLED

The ACLED vertical reconstructs conflict events as a source-specific empirical measurement system.

```text
external ACLED snapshot
        ↓
Silver event records
        ↓
geography candidate relation
        +
shared PeriodIndex
        ↓
sparse area × period × native event-type measurement
```

Silver retains every supplied event row.

In particular:

* no implicit `GEO_PRECISION` filter is applied;
* zero-fatality events remain events;
* malformed fatalities do not become zero;
* native event/sub-event taxonomies survive;
* source event identity remains separate from stable row identity.

```python
from fcv_empirical.violence import (
    materialize_acled_measurement,
    materialize_acled_silver,
    materialize_acled_vertical,
    normalize_acled_events,
    register_acled_snapshot,
)
```

### Geography

ACLED spatial membership is a separate relation.

The first Gold policy includes uniquely matched events while leaving ambiguous candidates visible rather than duplicating or tie-breaking them silently.

### Gold measurement

The source-specific Gold product uses a sparse grain of:

```text
geo_uid × period_id × native_event_type
```

Measurements include event counts, fatal-event counts, known fatalities, and missing-fatality diagnostics.

A missing Gold row remains unknown unless an explicit `CoverageContract` licenses a stronger interpretation.

### Legacy parity

Historical pipelines may be reproduced for diagnostic comparison, but legacy equality is not the correctness criterion.

Differences caused by historical filters, zero-fatality dropping, old geographies, or other known transformations should be explained rather than silently reintroduced.

---

# Run manifests and provenance

Every durable materialization records a shared `RunManifest`.

A typical run directory may contain:

```text
runs/fcv-empirical-data/<run_id>/
├── run_manifest.json
└── artifacts/
    ├── contracts/
    ├── mappings/
    ├── provenance/
    ├── qa/
    └── parity/
```

The exact sidecars depend on the source vertical.

The manifest captures:

* input `SourceSnapshotRef` and/or `DatasetRef` contracts;
* package and package version;
* run ID;
* parameters;
* code commit when supplied;
* timestamps;
* output `DatasetRef` objects;
* output SHA-256 hashes;
* QA results.

This allows derived lineage to remain truthful:

```text
SourceSnapshotRef
    ↓
Silver DatasetRef
```

and:

```text
Silver DatasetRef(s)
    ↓
derived DatasetRef
```

rather than pretending every downstream artifact directly reads raw source data.

---

# QA

QA is represented through contract-backed `QAResult` objects.

The generic package provides:

```python
from fcv_empirical import (
    accumulate_qa,
    presence_counts,
    serialize_qa,
)
```

QA may cover, depending on the source:

* row preservation;
* source-ID missingness or duplication;
* referential integrity;
* natural-grain uniqueness;
* geometry validity;
* coordinate validity;
* parse failures;
* schema coverage;
* missingness;
* source-vs-output reconciliation;
* cluster support;
* period assignment;
* geography membership;
* materialization status.

QA describes empirical and operational evidence. It does not automatically convert questionable observations into analytical decisions.

---

# Parity

Legacy pipelines are useful evidence.

They are not automatically normative.

The generic parity infrastructure supports reports containing concepts such as:

```text
legacy row count
new row count
key overlap
legacy-only keys
new-only keys
numeric differences
discrepancy categories
status
notes
```

A valid result may be:

```text
EXPLAINED_DIVERGENCE
```

when the rebuilt empirical product intentionally fixes or exposes behavior from the historical pipeline.

```python
from fcv_empirical import (
    build_parity_report,
    serialize_parity_report,
)
```

---

# Installation

Clone the repository:

```bash
git clone https://github.com/matuteiglesias/fcv-empirical-data.git
cd fcv-empirical-data
```

Create or activate a Python 3.10+ environment, then install:

```bash
python -m pip install .
```

For development:

```bash
python -m pip install -e ".[dev]"
```

Core dependencies currently include:

```text
empirical-data-contracts >=0.1,<0.2
spatial-data-foundation
pandas
pyarrow
openpyxl
geopandas
```

`spatial-data-foundation` is currently pinned in `pyproject.toml` to a specific Git commit to make the infrastructure dependency explicit and reproducible.

---

# Quick start

There is intentionally no universal “run everything” command.

Source verticals require explicit source snapshots and source-specific configuration.

A small GeoGCDF Silver materialization, for example, looks like:

```python
from spatial_foundation import DataRoot
from fcv_empirical.investments import materialize_geogcdf_silver

data_root = DataRoot.from_path("/data/empirical")

snapshot, silver, manifest, dataset, path = materialize_geogcdf_silver(
    source_path="/external/GeoGCDF_v3.0.1.gpkg",
    data_root=data_root,
    run_id="geogcdf-v3.0.1-rebuild-001",
    release="v3.0.1",
)

print(snapshot.snapshot_id)
print(dataset.content_sha256)
print(path)
```

The source file remains an external immutable snapshot.

The materialization creates a content-hashed Silver artifact and persists the corresponding run manifest and QA evidence.

For real research runs, supplying the producing repository commit is recommended:

```python
snapshot, silver, manifest, dataset, path = materialize_geogcdf_silver(
    source_path="/external/GeoGCDF_v3.0.1.gpkg",
    data_root=data_root,
    run_id="geogcdf-v3.0.1-rebuild-002",
    release="v3.0.1",
    code_commit="<git-sha>",
)
```

---

# Repository layout

```text
fcv-empirical-data/
├── AGENTS.md
├── pyproject.toml
├── .github/
│   └── workflows/
│       └── ci.yml
│
├── src/
│   └── fcv_empirical/
│       ├── common/
│       │   ├── materialization.py
│       │   ├── parity.py
│       │   └── qa.py
│       │
│       ├── investments/
│       │   ├── aiddata_clg.py
│       │   ├── worldbank.py
│       │   ├── geogcdf.py
│       │   ├── geogcdf_measurements.py
│       │   ├── geogcdf_pipeline.py
│       │   ├── annotation_candidates.py
│       │   └── parity.py
│       │
│       ├── surveys/
│       │   ├── catalog.py
│       │   ├── design.py
│       │   ├── geography.py
│       │   ├── variables.py
│       │   ├── dhs_hr.py
│       │   ├── dhs_gps.py
│       │   ├── dhs_gps_geography.py
│       │   ├── dhs_gps_linkage.py
│       │   ├── dhs_gps_pipeline.py
│       │   ├── dhs_gc.py
│       │   ├── dhs_integration.py
│       │   └── dhs_variables.py
│       │
│       └── violence/
│           ├── acled_events.py
│           ├── acled_index.py
│           ├── acled_measurements.py
│           ├── acled_parity.py
│           └── acled_pipeline.py
│
├── investments/
│   ├── flows/
│   │   └── annotation_v1/
│   └── scripts/
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── DATA_PRODUCTS.md
│   ├── INVESTMENTS.md
│   ├── SURVEY_SUBSTRATE.md
│   ├── DHS_HR_SILVER.md
│   ├── DHS_GPS_GEOGRAPHY.md
│   ├── DHS_GC.md
│   ├── DHS_INTEGRATED_SUBSTRATE.md
│   ├── VIOLENCE_ACLED.md
│   └── adr/
│
└── tests/
```

---

# Testing and development

Install development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run the test suite:

```bash
python -m pytest
```

Run Ruff:

```bash
python -m ruff check src tests
```

CI performs:

```text
Python 3.10
Python 3.12
    ↓
install package + dev dependencies
    ↓
installed-package import smoke test
    ↓
pytest
    ↓
ruff
```

The import smoke test is deliberately executed outside the repository working directory so that CI verifies the installed package rather than accidentally importing local source files.

---

# Adding a new source vertical

A new source adapter should normally follow this progression:

```text
1. Register immutable source snapshot
              ↓
2. Reconstruct source-native records
              ↓
3. Preserve natural grain + source IDs
              ↓
4. Produce contract-backed QA
              ↓
5. Define DatasetRef(s)
              ↓
6. Materialize + hash + manifest
              ↓
7. Add explicit geography / time products only when needed
              ↓
8. Add coverage / measurement contracts for derived measurements
              ↓
9. Compare with legacy products where useful
              ↓
10. Document source semantics and scientific boundaries
```

New generic infrastructure belongs in `fcv_empirical.common` only when it is genuinely source-neutral.

Generic modules should not acquire concepts specific to AidData, World Bank, ACLED, DHS, Afrobarometer, or a particular experimental design.

---

# What does not belong here

The following are intentionally downstream concerns:

```text
treatment definition
control definition
treatment eligibility
counterfactual construction
event-study T / y0 logic
matching
estimator choice
fixed effects
estimator-specific imputation
outcome-role assignment
regression weighting
falsification design
causal interpretation
```

Likewise, this repository should not silently:

```text
convert missing values to zero
infer no-event from absent rows
merge independent source ontologies
deduplicate records across independent sources
allocate project amounts across locations
multiply project finance by the number of locations
flatten natural relational grains for convenience
replace analytical geography with display geometry
rewrite shared period formulas locally
treat legacy equality as correctness
```

These constraints are part of the scientific design of the package, not merely coding style.

---

# Data policy

Large empirical datasets, protected survey microdata, downloaded source snapshots, and recovered archives should not be committed to this repository.

Prefer:

```text
external immutable source file
        +
SourceSnapshotRef
        +
SHA-256
```

over gratuitous source duplication.

Small synthetic fixtures and deliberately bounded review samples may be committed when they are required for tests or reproducible development workflows.

DHS protected microdata in particular must remain external.

---

# Real-data acceptance

Passing CI establishes software behavior against the repository's test fixtures.

It does **not** by itself establish that a particular real source release has received scientific validation.

Source verticals should distinguish:

```text
implementation complete
        ≠
real-data acceptance complete
        ≠
research validation complete
```

When a required real-data artifact is unavailable, acceptance or parity should be reported as `NOT_RUN` rather than inferred.

---

# Documentation

Detailed design and source-specific semantics live under `docs/`.

Start with:

* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — package boundary, data-root policy, materialization lifecycle.
* [`docs/DATA_PRODUCTS.md`](docs/DATA_PRODUCTS.md) — snapshots, dataset refs, manifests, QA, parity.
* [`docs/INVESTMENTS.md`](docs/INVESTMENTS.md) — AidData and World Bank source semantics.
* [`docs/SURVEY_SUBSTRATE.md`](docs/SURVEY_SUBSTRATE.md) — generic survey vocabulary and boundaries.
* [`docs/DHS_HR_SILVER.md`](docs/DHS_HR_SILVER.md) — DHS household reconstruction.
* [`docs/DHS_GPS_GEOGRAPHY.md`](docs/DHS_GPS_GEOGRAPHY.md) — displaced coordinates and geography relations.
* [`docs/DHS_GC.md`](docs/DHS_GC.md) — DHS Geospatial Covariates.
* [`docs/DHS_INTEGRATED_SUBSTRATE.md`](docs/DHS_INTEGRATED_SUBSTRATE.md) — cross-product DHS integration and semantic registry.
* [`docs/VIOLENCE_ACLED.md`](docs/VIOLENCE_ACLED.md) — ACLED Silver, geography, periods, Gold measurements, and parity.
* [`docs/adr/`](docs/adr/) — architectural decisions.

Contributors and automated agents should also read [`AGENTS.md`](AGENTS.md) before changing empirical semantics or package boundaries.

---

# Relationship to the FCV stack

The intended dependency flow is:

```text
empirical-data-contracts
        |
        +------------------+
        |                  |
        v                  v
spatial-data-foundation    |
        |                  |
        +--------+---------+
                 |
                 v
        fcv-empirical-data
                 |
                 v
       fcv-experiment-harness
```

In plain terms:

```text
contracts define interoperable empirical objects
spatial foundation owns geography and periods
fcv-empirical-data reconstructs measurements
experiment harness turns measurements into research designs
```

Keeping those boundaries explicit is what allows each layer to be reused and audited independently.

---

# Contributing

Changes should be small enough that their empirical consequences can be reviewed.

When modifying a source vertical:

1. preserve the source-native contract unless an explicit migration is intended;
2. add or update synthetic tests;
3. test adversarial boundary cases, not only happy paths;
4. update source-specific documentation when semantics change;
5. preserve provenance and content hashes;
6. make missingness and ambiguity visible;
7. avoid introducing downstream research assumptions;
8. explain intentional divergence from legacy products.

Before submitting a change:

```bash
python -m pytest
python -m ruff check src tests
```
