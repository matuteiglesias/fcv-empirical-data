# Survey substrate

`fcv_empirical.surveys` is a small source-native vocabulary for complex surveys whose natural
observations are households, people, respondents, clusters, enumeration areas, or other explicit
grains. It is metadata substrate, not an ingestion framework.

The substrate keeps five distinctions durable:

- one survey is not one source file or one acquisition snapshot: `SurveyCatalogEntry` identifies
  the survey, while `SurveyFileLink` relates any number of contract-backed `SourceFileRef` objects
  and snapshot identities to it. `validate_survey_file_link` checks that the linked file is really
  present in the declared `SourceSnapshotRef`;
- observation grain is an explicit extensible string rather than a closed universal ontology;
- source sampling facts, including the source weight variable/value, are preserved by
  `SurveyDesignRecord` without selecting a weight for analysis. If a normalized weight is carried,
  its normalization method must be named explicitly;
- geography is a candidate relation. Every `SurveyGeographyLink` carries the exact
  `GeographySpec`, reuses `spatial-data-foundation`'s `MembershipStatus`, and enforces consistency
  between assignment status and `geo_uid`. Several rows may represent plausible memberships;
- variable timing is semantic metadata, not inferred timestamps. `SurveyVariableMetadata` preserves
  source-facing variable identity, instrument/recode/round context, missing-value metadata,
  codebook provenance, and a deliberately small `TemporalSemantics` vocabulary. `UNKNOWN` is the
  default.

Actual spatial membership computation remains a `spatial-data-foundation` responsibility. The
survey substrate does not invent a second geography/status ontology, and it does not claim a
reported survey coordinate is an exact true location.

This package does not assign scientific roles to variables, aggregate surveys to area-period panels,
or perform weighted estimation. Those choices belong downstream.
