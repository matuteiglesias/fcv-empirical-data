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

## Source authority before commissioning

Commissioning must start from the canonical HR ingestion path:

```text
official DHS <release>.DAT
        +
distributed <release>.DCT
        |
        v
canonical fixed-width HR Silver
        |
        +-- source_row_id
        +-- source_household_weight = source HV005 unchanged
        +-- every dictionary-declared HR variable
        |
        v
codebook-backed household semantic measurements
        |
        v
external-reference commissioning
```

Convenience CSV exports are not canonical commissioning inputs. Legacy CSVs may be compared against
the authoritative decoded table as parity evidence, but they cannot establish release completeness.

The canonical package-level API is:

```python
from fcv_empirical.surveys import materialize_dhs_hr_silver
```

and requires `source_path=<release>.DAT` plus `dictionary_path=<release>.DCT`.

## Existing products reused

No new survey-design substrate or persistent joined household table is introduced.

```text
surveys.dhs.hr_households
    grain = source_row_id
        +
surveys.dhs.hr_household_measurements
    grain = source_row_id x measurement_id
        |
        v
ephemeral source_row_id join
        |
        v
surveys.dhs.commissioning_results
    grain = benchmark_id x cell_id
```

`HV005` remains a source survey-design fact. `HV012` and `HV025`, when required by a published
denominator/domain, remain source-native auxiliary fields in HR Silver. They are not added to the
semantic variable registry merely to make commissioning convenient.

Joined microdata are never persisted by the commissioning materializer.

## Commissioning API

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

The durable commissioning dataset is `L2_DERIVED`. It contains aggregate reference/observed
percentages and discrepancy diagnostics only.

## Eight predeclared checks

The first wave exercises three empirical mechanisms and deliberately repeats household-vs-de-jure
denominator reconstruction across the three releases where the same authoritative table exposes both.

### Nigeria 2018

Canonical local release inputs are expected to be the verified companions:

```text
NGHR7BFL.DAT
NGHR7BFL.DCT
```

`DhsHrMetadata` still requires independently verified survey/release identity and exact source
filename. Survey identity must never be inferred from the filename alone.

Four checks are declared by `nigeria_2018_commissioning_specs(...)`.

1. National household electricity, NDHS 2018 Final Report `FR359`, Table 2.4, p. 26:
   `No = 40.6%`, `Yes = 59.4%`.
2. National de jure population electricity, `FR359`, Table 2.4, p. 26:
   `No = 43.5%`, `Yes = 56.5%`.
3. National detailed household drinking-water source distribution, `FR359`, Table 2.1.1, p. 21.
4. Urban de jure wealth-quintile distribution, `FR359`, Table 2.6, p. 28:
   `4.2 / 8.0 / 18.9 / 30.6 / 38.4%`.

Household electricity consumes `dhs.household.electricity_access` and the preserved source household
weight.

De jure electricity uses the same semantic measurement with effective weight `HV005 x HV012`.
This is intentionally a denominator/weighting commissioning check, not a second electricity
measurement definition.

Urban wealth consumes `dhs.household.wealth_quintile`, uses `HV005 x HV012`, and restricts the
domain using `HV025 == 1` after release validation.

The water benchmark intentionally ships without a guessed `HV201 -> report cell` mapping.
Nigeria-release `.FRQ`, `.MAP`, `.DO`, or equivalent distributed metadata must be inspected locally
and an explicit release-local map supplied:

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

Canonical release inputs:

```text
UGHR7BFL.DAT
UGHR7BFL.DCT
```

`uganda_2016_commissioning_specs()` declares two national electricity checks against Uganda DHS 2016
Final Report `FR333`, Table 2.4, p. 23:

```text
Households:   No = 71.4%, Yes = 28.6%
De jure pop.: No = 73.3%, Yes = 26.7%
```

The second uses the same electricity measurement with effective weight `HV005 x HV012`.

### Zambia 2018

Canonical release inputs:

```text
ZMHR71FL.DAT
ZMHR71FL.DCT
```

`zambia_2018_commissioning_specs()` declares two national electricity checks against Zambia DHS 2018
Final Report `FR361`, Table 2.4, p. 22:

```text
Households:   No = 65.8%, Yes = 34.2%
De jure pop.: No = 67.2%, Yes = 32.8%
```

Again, the population check differs only by the explicit `HV012` population multiplier.

## Quantitative semantics

The denominator is the full commissioned domain with valid design/effective weight.

Missing semantic states remain in the denominator and are diagnosed separately. They are not
silently converted to another category or dropped.

An observed semantic value that cannot be mapped to a declared reference cell contributes to
`unmapped_measurement_or_category_weight` and blocks quantitative recovery.

Published cells are considered recovered when the observed percentage lies within the interval
consistent with the publication's declared decimal precision. For one-decimal published values the
comparison tolerance is 0.05 percentage points.

A mismatch against a published table is valid commissioning evidence and is persisted with RED
quantitative QA. It is not treated as a materialization failure. In contrast, broken provenance,
hash mismatch, survey mismatch, non-bijective `source_row_id` linkage, invalid/nonpositive source
weights, or invalid required population multipliers fail closed before a commissioning result can
be claimed.

## Real-data boundary

Protected DHS rows remain external. GitHub CI uses synthetic fixed-width fixtures only.

A local real run may persist protected HR Silver and semantic measurement products inside the
configured external `DataRoot`, but review/PR material should expose only:

- survey/release identity;
- source/dictionary and dataset hashes;
- dictionary field count/schema fingerprint;
- row counts;
- aggregate commissioning percentages;
- aggregate denominator/missing/unmapped diagnostics;
- QA status.

Do not commit microdata, generated HR Silver, semantic household rows, local source paths, or release
archives.

The canonical HR source snapshot intentionally contains exactly the `.DAT` bytes and the `.DCT`
dictionary required to reproduce decoding. Other distributed companions such as `.DO`, `.MAP`,
`.SAS`, `.SPS`, `.FRQ`, and `.FRW` remain release-validation/code-label evidence and are not added to
the HR snapshot merely because they are available.
