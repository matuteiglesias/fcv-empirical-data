# ADR 0001: Empirical domain boundary

**Status:** Accepted

## Decision

`fcv-empirical-data` owns empirical measurements, provenance, coverage/measurement declarations,
source-native semantics, and durable data products. `fcv-experiment-harness` owns treatment/control,
outcome timing, matching, estimators, fixed effects, estimator imputation, falsification, and causal
interpretation.

The domain layer reuses `empirical-data-contracts` and `spatial-data-foundation`; it does not fork
shared contracts, geography, or period logic.

## Consequences

Source verticals can evolve independently without smuggling causal-design assumptions into ingestion.
The experiment harness can change estimators without requiring empirical sources to be rebuilt merely
because the research design changed.
