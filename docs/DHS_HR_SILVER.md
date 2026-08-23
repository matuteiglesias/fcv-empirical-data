# DHS Household Recode (HR) Silver

This vertical materializes externally stored DHS Household Recode files at their natural
**household-within-survey** grain. It is a source-data product, not a measurement or experiment
product.

## Boundary

The flow is:

```text
external authoritative HR file
  -> SourceSnapshotRef (path + SHA-256)
  -> verified DHS survey/file identity
  -> source-native household Silver
  -> QA + RunManifest
```

The repository never copies DHS microdata into Git. GitHub tests use synthetic fixtures only.
A real run may persist protected values only in the configured external data root; review material
must be limited to non-sensitive aggregate QA, survey identity, counts, and hashes.

## Identity

`DhsHrMetadata` requires a verified DHS survey identifier plus country, year, phase, release,
recode family, and exact source file name. `survey_id` is built from the verified DHS survey
identifier, **not** inferred from the filename. A filename mismatch fails instead of guessing.

One survey may later have HR, PR, IR, GE/GPS, GC, or other files in different source snapshots.
The HR source file is therefore linked through the S0 `SurveyFileLink`; the survey catalog itself
is not coupled to one acquisition snapshot.

## Source-variable and design preservation

The materializer adds a normalized envelope while retaining every original HR column and value.
The release-specific `DhsHrColumnMap` is explicit at materialization time. The exported
`STANDARD_DHS_HR_COLUMNS` reflects common DHS standard recode names (`HHID`, `HV001`, `HV005`,
`HV021`, `HV022`) but must be checked against the survey's official recode metadata/final-report
sample design before use.

The source household weight is copied unchanged. In particular, this layer does not divide the
stored DHS weight by one million or otherwise normalize it. `SurveyDesignRecord` views expose the
source weight, cluster, PSU, and stratum facts without selecting an estimation design.

## QA

The run manifest records:

- input and output household-row counts;
- missing and duplicate household IDs;
- missing cluster IDs and distinct cluster count;
- missing PSU IDs;
- missing, invalid, and nonpositive source weights;
- missing stratum IDs;
- source-column/value preservation and source-schema fingerprint.

Duplicates and missing values remain in Silver. There is no deduplication or aggregation step.

## Real local execution

A caller should verify the survey metadata and design-column mapping from official DHS material,
then call `materialize_dhs_hr_silver(...)` with the local HR path. `.dta`, `.csv`, and `.parquet`
inputs are accepted; DHS Stata files are read with value-label conversion disabled so source codes
remain source codes.

The durable output is versioned below the shared data root under the conceptual path:

```text
silver/surveys/dhs/<survey_id>/<source_snapshot_id>/hr_households.parquet
```

The `DatasetRef` has L3 rebuilt authority. Nothing in this vertical grants L4 research authority or
assigns outcome, treatment, covariate, geography-exposure, or estimator meaning to HR variables.
