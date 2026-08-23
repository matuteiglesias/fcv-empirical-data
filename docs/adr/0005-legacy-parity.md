# ADR 0005: Legacy parity is evidence, not authority

**Status:** Accepted

## Decision

Parity checks compare recovered/legacy products with rebuilt products and categorize discrepancies.
Exact equality is one possible result, not the definition of correctness. The parity convention must
represent both equality and explained divergence.

## Consequences

A justified filter, corrected source interpretation, version change, or repaired defect can produce a
reviewable discrepancy without being mislabeled as failure. Unexplained divergence remains visible
and should block claims of equivalence until understood.
