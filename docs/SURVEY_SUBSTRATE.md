# Survey substrate

`fcv_empirical.surveys` is a small source-native vocabulary for complex surveys whose natural
observations are households, people, respondents, clusters, enumeration areas, or other explicit
grains. It is metadata substrate, not an ingestion framework.

The substrate keeps four distinctions durable:

- one survey is not one source file: `SurveyCatalogEntry` identifies the survey while
  `SurveyFileLink` relates any number of contract-backed `SourceFileRef` objects to it;
- observation grain is an explicit extensible string rather than a closed universal ontology;
- source sampling facts, including the source weight variable/value, are preserved by
  `SurveyDesignRecord` without selecting a weight for analysis;
- geography is a candidate relation. Several `SurveyGeographyLink` rows may represent plausible
  memberships, and an unmatched object may carry no `geo_uid`. Actual spatial membership remains a
  `spatial-data-foundation` responsibility.

`SurveyVariableMetadata` preserves source-facing variable identity, instrument/recode/round context,
missing-value metadata, codebook provenance, and a deliberately small `TemporalSemantics`
vocabulary. `UNKNOWN` is the default; adapters must not manufacture timestamps or temporal meaning.

This package does not assign scientific roles to variables, aggregate surveys to area-period panels,
or perform weighted estimation. Those choices belong downstream.
