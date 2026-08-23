# DHS GPS geography

`fcv_empirical.surveys.dhs_gps` represents DHS GE/GPS cluster files as spatial measurements whose
published coordinates may be intentionally displaced. The durable flow is:

```text
external DHS GE/GPS source
        -> SourceSnapshotRef
        -> source-native cluster Silver
        -> explicit survey/GPS linkage audit
        -> reported-coordinate spatial membership
        -> SurveyGeographyLink
```

## Cluster Silver

The natural grain is a supplied GPS cluster row. Silver keeps a technical `cluster_row_id` so source
duplicates remain observable, while preserving `cluster_id`/`DHSID`, the reported latitude and
longitude, source-defined urban/rural classification when available, source release and snapshot
identity, coordinate validity, and displacement metadata. Every source-native column is retained
with a `source__` prefix. If the source is a `GeoDataFrame`, its native geometry is serialized to WKT
as `source__geometry` rather than being treated as an undisplaced location.

GPS source files remain external. `register_dhs_gps_snapshot` delegates hashing and immutable external
registration to `spatial-data-foundation`; the repository does not copy DHS microdata or GPS source
files into Git.

## Displacement semantics

`DhsDisplacementPolicy` is release-specific provenance, not a universal DHS rule table. A caller must
state whether the registered coordinates are displaced and may provide only documentation-supported
metadata such as policy class, urban/rural limits, exceptional rural displacement, and a policy
source. The package deliberately has no default displacement radius and performs no de-displacement
or inference of a true cluster coordinate.

A zero coordinate is profiled in QA but is treated as a source placeholder only when the caller
explicitly identifies it as one of the source release's placeholder coordinate conventions.

## Survey / GPS identity

`validate_dhs_gps_linkage` compares survey-side cluster identity against GPS Silver without using an
inner join. Its discrepancy table keeps survey-only clusters, GPS-only clusters, duplicates, canonical
survey-ID conflicts, and conflicting source survey IDs visible. These conditions are also summarized
as contract-backed QA results.

## Reported-coordinate membership

`assign_dhs_reported_coordinate_membership` sends only valid reported points to
`spatial_foundation.geography.assign_points`. It therefore inherits the upstream analytical-geometry
role check and boundary behavior: exact boundaries and overlapping polygons remain
`ambiguous_multiple`, outside points remain `unmatched_outside`, and unusable coordinates are
represented as `invalid_point` without entering point-in-polygon assignment.

The relation's semantic label is exactly `reported_coordinate_membership`. It describes which
analytical polygon contains the coordinate supplied in the public GPS file; it is not a statement
about the true cluster location. `coordinate_is_displaced` and an `uncertainty_status` travel with the
relation and are also retained in the corresponding `SurveyGeographyLink` uncertainty metadata.

## Future uncertainty products

The model reserves a distinct semantic name, `possible_geography_under_displacement`, for a future
product that could enumerate geographies compatible with an authoritative displacement policy. Such
a product would be derived uncertainty evidence alongside reported-coordinate membership; it would
not replace cluster Silver or reinterpret the reported point as a true location. This implementation
does not construct displacement buffers or candidate sets.

No household variables, GC covariates, area aggregates, treatment exposure, nearest-project matches,
or experiment-harness semantics are introduced here.
