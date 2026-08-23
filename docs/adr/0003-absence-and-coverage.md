# ADR 0003: Absence requires a coverage contract

**Status:** Accepted

## Decision

Row absence and missing values have no automatic substantive meaning. Generic helpers never perform
missing-to-zero conversion. Zero from absence is licensed only by an explicit `CoverageContract` or
`MeasurementContract` whose `absent_row_semantics` and basis justify that interpretation.

## Consequences

Sparse event data cannot silently manufacture structural zeroes. Downstream code can still resolve
absence when verified coverage supports it, but the rule is reviewable, versioned, and attributable.
