# DHS Geospatial Covariates (GC)

`fcv_empirical.surveys.dhs_gc` represents DHS Geospatial Covariates as measurements attached to
DHS survey clusters. It does not reinterpret GC as an Africa-wide polygon-by-period covariate panel.

## Source and survey identity

GC source files remain external immutable inputs. `register_dhs_gc_snapshot` records their file hash
through `SourceSnapshotRef`; materialization rechecks that hash and fails if the registered file has
changed. The source file is resolved explicitly to a `SurveyCatalogEntry` through `SurveyFileLink`
with instrument `GC`. Survey identity is never guessed from a cluster identifier or filename.

The authoritative Silver table is wide and cluster-native. Its declared grain is:

```text
survey_id × cluster_id
```

It carries `source_release`, `source_snapshot_id`, and every source-native field under a reversible
`source__<original-column>` mapping. Missing source values remain missing. No GID/geography
aggregation occurs.

## Derived long measurement view

A second Silver view may be materialized at:

```text
survey_id × cluster_id × source_variable
```

The long view is derived from the cluster Silver dataset, so its `RunManifest` input is the hashed
Silver `DatasetRef`, not a fictional direct read of Bronze. The authoritative typed values remain in
the wide Silver table; the long view stores a lexical `source_value` plus `source_value_type` for
cross-variable durability.

Cluster availability is reported per survey and source variable. This is measurement availability
among DHS clusters. It is not a claim of polygon, country, or raster coverage, and missing values are
never interpreted as zero.

## Temporal semantics

Temporal meaning is registry-driven. `GCTemporalRule` maps documented source-variable patterns to the
shared survey `TemporalSemantics` vocabulary:

- `static`
- `survey_time`
- `annual`
- `epoch`
- `climatology`
- `retrospective`
- `unknown`

No registry match means `unknown`. If multiple rules match, semantics remain `unknown` and the
conflict is reported. A year is parsed only when the rule explicitly names a regex capture group or
provides a documented year. A variable without a year suffix does **not** acquire the survey year.
Even `survey_time` variables do not receive a synthetic `measurement_year`; the survey catalog already
carries fieldwork/survey timing separately.

The time-parsing report keeps visible:

- variables with explicit parsed years;
- static, annual, epoch, climatology, survey-time, retrospective, and unknown variables;
- impossible year tokens;
- overlapping/conflicting temporal rules.

## Explicit non-operations

The GC adapter does not perform forward fill, backward fill, interpolation, static-value replication
across periods, implicit survey-year assignment, raster reconstruction, zonal statistics, GID
aggregation, regression-role assignment, or `_DHSGC` compatibility-panel construction. Those are not
source facts.
