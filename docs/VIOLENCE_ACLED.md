# ACLED violence vertical

## Boundary

This vertical rebuilds ACLED as a source-specific empirical measurement system. It does not define a treatment, an outcome for a particular experiment, or an ACLED/UCDP harmonization.

```text
external immutable ACLED export
        |
        v
SourceSnapshotRef
        |
        v
Silver ACLED events
        |
        +--> event -> analytical geography candidates
        |
        +--> event -> shared PeriodIndex
        |
        v
sparse Gold area x period x native event type
        |
        +--> CoverageContract (absence = unknown by default)
        +--> MeasurementContract
        +--> legacy parity evidence
```

## Silver event contract

`violence.acled.events` retains every supplied source row. No `GEO_PRECISION` filter is applied and zero-fatality events are preserved. A stable `event_row_id` identifies the row within the registered source snapshot while the native source event ID remains a separate field. This allows source-ID anomalies to be reported without destroying source rows.

The normalized envelope includes event date, coordinates, fatalities, native event/sub-event type, geography precision, time precision, country identifiers when present, source release, and source snapshot identity. Every original source column is also retained with the `source__` prefix; the exact canonical-to-source mapping and source-schema hash are written as run artifacts.

Malformed dates, coordinates, or fatalities are not converted to substantive zeros. QA records missing/invalid values, duplicates, distributions, and fatality-total reconciliation.

## Geography and time

Point membership is delegated to `spatial-data-foundation`. Analytical polygons are required by that dependency. The ACLED layer does not implement another point-in-polygon algorithm.

Membership is a relation, not a column embedded permanently into the event table. Unique, outside, boundary/multiple, missing-coordinate, and invalid-coordinate states remain distinguishable. The first Gold policy includes only `matched_unique` events; ambiguous candidates remain in the membership product and are reported as excluded rather than tie-broken or duplicated.

Period assignment is delegated to `PeriodIndex`. The legacy-compatible acceptance scheme is `T2_y2001`, but it is a parameter rather than hard-coded source logic.

## Gold measurement

`violence.acled.area_period_native_event` is sparse and source-specific. Its grain is:

```text
geo_uid x period_id x native_event_type
```

It contains:

- `event_count`;
- `fatal_event_count`;
- `fatalities` (sum of known reported fatality values);
- known/missing fatality event counts;
- `record_present = true` for represented aggregate rows.

A missing Gold row is not materialized as zero. The initial coverage contract deliberately uses `absent_row_semantics = unknown`. Observed minimum/maximum event dates are recorded as descriptive support only and do not prove complete reporting over the interval.

## Lineage

The vertical uses two explicit runs:

```text
SourceSnapshotRef -> Silver DatasetRef

Silver DatasetRef + geography DatasetRef
    -> geography membership DatasetRef
    -> period membership DatasetRef
    -> Gold DatasetRef
```

This keeps the raw source hash attached to the Silver rebuild and the exact hashed Silver/geography inputs attached to the measurement run. Geography and period parameters, code commit when supplied, content hashes, QA, coverage, and measurement contracts remain recoverable from persisted run artifacts.

## Legacy parity

The recovered historical pipeline filtered `GEO_PRECISION == 1`, discarded zero-fatality events in an intermediate ACLED table, spatially overlaid points against legacy geographies, and later widened native event types into death columns. Those are not reintroduced into the authoritative rebuild.

Parity therefore explains differences rather than enforcing equality. It records diagnostic effects of the legacy precision filter and zero-fatality event retention. When the old aggregate and an explicit new-geo-to-legacy-GID crosswalk are supplied, it also compares GID-period support and the legacy `deaths_Violence against civilians` field. Crosswalk ambiguity or duplicate keys block numerical comparison rather than being aggregated away silently.

Recovered notebook reference totals (approximately 787,814 raw fatalities and 512,722 after the old precision-1 filter) are historical diagnostics for that old source snapshot only. They are not tests for future ACLED releases.

## Real-data acceptance status

The package implementation and CI tests use synthetic fixtures. A PR must not claim real ACLED/GADM/legacy parity unless the corresponding local source snapshot, contract-backed geography materialization, and legacy aggregate were actually supplied and executed. Missing real-data evidence is reported as `NOT_RUN`, not inferred.

## Non-goals

This vertical does not implement UCDP, ACLED/UCDP reconciliation, downloading/authentication, treatment/control semantics, lagged outcomes, regressions, matching, population normalization, or causal interpretation.
