# Integrated DHS substrate

The DHS implementation deliberately remains a set of related empirical products rather than a single wide analysis table.

```text
                         SurveyCatalogEntry
                                |
              +-----------------+-----------------+
              |                 |                 |
              v                 v                 v
          HR household       GE/GPS cluster      GC cluster
             Silver              Silver             Silver
              |                   |                  |
              | cluster_id        |                  |
              +-------------------+------------------+
                                  |
                         cluster identity audit
                                  |
                    +-------------+-------------+
                    |                           |
                    v                           v
           reported-coordinate          GC cluster measurements
           geography relation           + temporal semantics

HR Silver
    |
    +-- codebook-backed DHS variable registry
            |
            v
       household semantic measurements
```

No step above constructs treatment, outcome, control, covariate role, regression weights, or a joined experiment frame.

## Cross-product integration

`build_dhs_survey_integration_report` is a QA operation. It requires HR, GPS, and GC products to resolve to one explicit `SurveyCatalogEntry`, optionally checks their `DatasetRef` identities and declared grains against the supplied rows, and compares cluster support without inner-joining away discrepancies.

The report keeps HR-only, GPS-only, and GC-only clusters visible. GC uses `DHSCLUST` as the cross-product cluster link key when the source provides it; `DHSID` remains a distinct DHS identity. Numeric-equivalent text IDs such as `001` and `1` are reported as a possible normalization issue but are never silently rewritten or joined.

A `DatasetRef.grain` is treated as a factual uniqueness claim. If its declared keys are missing, contain nulls, or do not uniquely identify the supplied rows, integrated DHS validation fails. This is intentionally stricter than source-native QA: a source defect may remain visible in Silver, but a contracted consumer may not pretend that a non-unique natural key is unique.

The integration report is not a canonical DHS mega-table and carries no analysis values. It is evidence that separately materialized natural-grain products can be related safely.

## GPS geography

Public DHS coordinates remain reported, potentially displaced measurements. The geography relation is therefore `reported_coordinate_membership`, not a claim about true cluster location. Displacement metadata and ambiguity remain first-class evidence. A future uncertainty-aware candidate-geography product can be added without replacing the reported-coordinate relation.

## GC temporal semantics

DHS Geospatial Covariates remain cluster-linked measurements. They are not authoritative polygon-wide covariates and are not expanded into an Africa area-period panel.

Temporal meaning is registry/documentation driven. `static`, `survey_time`, `annual`, `epoch`, `climatology`, `retrospective`, and `unknown` remain distinct. In particular, lack of a source year never licenses assignment to the survey year. Missing GC values are not imputed and missing cluster-variable rows do not become zero.

## DHS household variable registry

The first semantic registry is deliberately small and DHS-VII/HR-specific. Each definition requires explicit codebook provenance and maps a source variable to a reusable empirical measurement, never to an experiment role.

Initial definitions are:

| Source variable | Empirical measurement | Boundary |
| --- | --- | --- |
| `HV206` | `dhs.household.electricity_access` | Standard 0/1 coding; documented missing code remains missing. |
| `HV270` | `dhs.household.wealth_quintile` | Ordered source quintile; explicitly survey-relative rather than an absolute wealth scale. |
| `HV201` | `dhs.household.drinking_water_source_code` | Source category code only; detailed categories may be country-specific, so no improved/safe-water harmonization is inferred. |

The registry intentionally does **not** reproduce historical notebook interpretations such as treating `HV215` as electricity or `HV040` as household-head gender. Those variables have different DHS standard-recode meanings and cannot receive a semantic measurement without a matching codebook-backed definition.

Semantic measurement materialization consumes a content-hashed HR Silver `DatasetRef`, verifies the Parquet bytes, and writes one row per `source_row_id x measurement_id`. Source missing codes, absent source values, and unmapped codes remain distinct statuses. Unknown codes never become zero.

## Authority and real-data acceptance

All rebuilt products remain L3 until a real release receives research validation. GitHub tests use synthetic survey-shaped fixtures only; protected DHS microdata must remain external. A real local acceptance review should expose only non-sensitive evidence such as snapshot hashes, survey identity, row/cluster counts, cross-product support counts, missingness, geography-status counts, and registry diagnostics.

The next acceptance gate is one real DHS survey for which HR, GE/GPS, and GC can all be registered and the integration report can be reviewed. Only after that gate should the harness construct household-level experimental exposure or estimation inputs.
