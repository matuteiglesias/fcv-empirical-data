# DHS external-reference commissioning

This module closes the real-data acceptance gap for the rebuilt DHS household measurement system
without moving experiment or causal semantics upstream.

The commissioning question is deliberately narrow:

> Given one verified DHS HR release, its contract-backed household Silver product, the
> codebook-backed semantic measurement product, and an authoritative published DHS table, does the
> rebuilt empirical system reproduce the published weighted distribution under the same denominator,
> domain, and missing-value conventions?

Commissioning is evidence about the empirical instrument. It is not a substantive FCV result and it
does not grant a generic DHS registry definition L4 authority.

## Existing products reused

No new household or survey-design substrate is introduced.

```text
external DHS HR file
    -> SourceSnapshotRef
    -> surveys.dhs.hr_households
         grain = source_row_id
         source_household_weight preserved unchanged
         all source HVxxx columns retained
    -> surveys.dhs.hr_household_measurements
         grain = source_row_id x measurement_id
         explicit measurement_status
         normalized semantic value
    -> ephemeral source_row_id join
    -> aggregate external-reference commissioning result
```

The join exists only during calculation. Joined microdata are never persisted by the commissioning
materializer.

`HV005` remains a source survey-design fact. `HV012` and `HV025`, when required by a published
denominator/domain, remain source-native auxiliary fields in HR Silver. They are not added to the
semantic variable registry merely to make commissioning convenient.

## Public API

```python
from fcv_empirical.surveys import (
    materialize_dhs_commissioning_suite,
    nigeria_2018_commissioning_specs,
    uganda_2016_commissioning_specs,
    zambia_2018_commissioning_specs,
)
```

`run_dhs_commissioning_suite(...)` is the pure in-memory path.
`materialize_dhs_commissioning_suite(...)` additionally verifies both input artifact hashes,
materializes the aggregate result through the existing FCV materialization kernel, and persists
reference/spec and diagnostic sidecars.

The durable commissioning dataset is:

```text
surveys.dhs.commissioning_results
```

with grain:

```text
benchmark_id x cell_id
```

and `L2_DERIVED` authority. It contains aggregate reference/observed percentages and discrepancy
diagnostics only.

## Five predeclared checks

The first wave intentionally exercises three mechanisms and then repeats the simplest mechanism
across releases.

### Nigeria 2018

Candidate local HR data file:

```text
sources/DHS/HR_data/NGHR7BFL/NGHR7BFL.csv
```

The filename is only a candidate binding. `DhsHrMetadata` still requires independently verified
survey/release metadata and exact source filename; source identity must never be inferred from the
filename alone.

Three checks are declared by `nigeria_2018_commissioning_specs(...)`.

1. National household electricity, NDHS 2018 Final Report `FR359`, Table 2.4, p. 26:
   `No = 40.6%`, `Yes = 59.4%`.
2. National detailed household drinking-water source distribution, `FR359`, Table 2.1.1, p. 21.
3. Urban de jure wealth-quintile distribution, `FR359`, Table 2.6, p. 28:
   `4.2 / 8.0 / 18.9 / 30.6 / 38.4%`.

Electricity consumes `dhs.household.electricity_access` and the preserved source household weight.

Urban wealth consumes `dhs.household.wealth_quintile`, uses `HV005 x HV012` as the effective
de-jure-population weight, and restricts the domain using `HV025 == 1` after release verification.

The water benchmark intentionally ships without a guessed `HV201 -> report cell` mapping.
Nigeria-release `.FRQ`, `.MAP`, `.DCT`, `.DO`, or equivalent distributed metadata must be inspected
locally and an explicit map supplied:

```python
specs = nigeria_2018_commissioning_specs(
    water_category_map={
        "<verified raw HV201 code>": "piped_dwelling_yard_plot",
        # ...
    }
)
```

No improved/unimproved or safe/unsafe classification is introduced.

### Uganda 2016

Candidate local HR data file:

```text
sources/DHS/HR_data/UGHR7BFL/UGHR7BFL.csv
```

`uganda_2016_commissioning_specs()` declares national household electricity against the Uganda DHS
2016 Final Report `FR333`, Table 2.4, p. 23:

```text
No = 71.4%
Yes = 28.6%
```

### Zambia 2018

Candidate local HR data file:

```text
sources/DHS/HR_data/ZMHR71FL/ZMHR71FL.csv
```

`zambia_2018_commissioning_specs()` declares national household electricity against the Zambia DHS
2018 Final Report `FR361`, Table 2.4, p. 22:

```text
No = 65.8%
Yes = 34.2%
```

## Quantitative semantics

The denominator is the full commissioned domain with valid design/effective weight.

Missing semantic states remain in the denominator and are diagnosed separately. They are not
silently converted to another category or dropped.

An observed semantic value that cannot be mapped to a declared reference cell contributes to
`unmapped_measurement_or_category_weight` and blocks quantitative recovery.

Published cells are considered recovered when the observed percentage lies within the interval
consistent with the publication's declared decimal precision. For one-decimal published values the
comparison tolerance is 0.05 percentage points.

A mismatch against a published table is a valid commissioning result and is persisted with RED
quantitative QA. It is not treated as a materialization failure. In contrast, broken provenance,
hash mismatch, survey mismatch, non-bijective `source_row_id` linkage, invalid/nonpositive source
weights, or invalid required population multipliers fail closed before a commissioning result can
be claimed.

## Real-data boundary

Protected DHS rows remain external. GitHub CI uses synthetic fixtures only.

A local real run may persist protected HR Silver and semantic measurement products inside the
configured external `DataRoot`, but review/PR material should expose only:

- survey/release identity;
- snapshot and dataset hashes;
- row counts;
- aggregate commissioning percentages;
- aggregate denominator/missing/unmapped diagnostics;
- QA status.

Do not commit microdata, generated HR Silver, semantic household rows, local source paths, or
release archives.

The HR source snapshot should remain the data artifact actually read by the HR materializer. Do not
casually expand that snapshot with `.FRQ`, `.MAP`, `.DCT`, `.DO`, or other documentation files:
`source_row_id` contains the snapshot identity, so changing the snapshot file set can churn every
physical household row identity. Treat those companion files as local release-validation evidence
unless a separately designed documentation snapshot is needed.
