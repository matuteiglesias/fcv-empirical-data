# fcv-empirical-data

Reproducible, source-faithful empirical data infrastructure for the FCV research program.

This repository owns what was observed and how it was measured. It sits between shared contracts /
spatial foundations and downstream experiment definitions, estimators, observability, and
calibration.

## Scientific boundary

`fcv-empirical-data` owns:

- source snapshots and immutable external-file identity;
- source-native Silver products;
- reusable empirical measurements and their contracts;
- survey/source semantics, provenance, coverage, and QA;
- parity and commissioning evidence about measurement infrastructure.

It does **not** own treatment/control assignment, estimator roles, causal timing, counterfactuals,
matching, regression specifications, or substantive causal interpretation. Those belong downstream.

## Current source verticals

The package currently includes:

- AidData CLG-LMIC relational Silver;
- World Bank Projects API Silver;
- AidData GeoGCDF source-native project geometry and commitment-period measurements;
- ACLED source-native events and sparse area-period-native-event-type measurements;
- a generic survey substrate;
- DHS Household Recode (HR) source-native household Silver;
- DHS GPS/GE cluster Silver and reported-coordinate geography;
- DHS GC cluster-linked covariates with explicit temporal semantics;
- DHS integration QA;
- a first codebook-backed DHS household semantic measurement registry;
- DHS external-reference commissioning against authoritative final-report tables.

### DHS HR source authority

Canonical DHS Household Recode ingestion uses the official fixed-width release bytes plus the
matching distributed Stata dictionary:

```text
<release>.DAT + <release>.DCT
        -> bounded-memory fixed-width decoding
        -> surveys.dhs.hr_households
```

Convenience CSV representations are not authoritative because real legacy exports were found to omit
standard variables still declared in the official DHS-VII release dictionaries. Canonical
materialization therefore preserves every dictionary-declared field and streams bounded row chunks
directly to Parquet rather than constructing the full wide release as one pandas DataFrame.

The prior tabular HR path remains available only as an explicit legacy/parity surface.

See:

- `docs/DHS_HR_SILVER.md`
- `docs/DHS_INTEGRATED_SUBSTRATE.md`
- `docs/DHS_COMMISSIONING.md`

## Development

```bash
python -m pip install ".[dev]"
python -m pytest
python -m ruff check src tests
```

GitHub tests use small synthetic fixtures. Protected empirical source files and generated protected
products stay outside Git under the configured shared `DataRoot`.
