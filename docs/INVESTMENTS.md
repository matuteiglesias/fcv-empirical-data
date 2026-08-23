# Investment source verticals

## Boundary

The investment vertical reconstructs source records and provenance. It does not construct exposure or
treatment.

```text
AidData CLG-LMIC workbook                 World Bank Projects API page JSON
          |                                           |
          v                                           v
   SourceSnapshotRef                           SourceSnapshotRef
          |                                           |
          v                                           v
source-native relational Silver            source-native project Silver
          \                                           /
           \                                         /
            +---- optional L2 annotation review ----+
```

AidData and World Bank remain separate source systems. No product in this layer says that two records
represent the same project, that the sources are additive, or that an absent row means no project.

## AidData CLG-LMIC

The authoritative input is the registered CLG-LMIC release workbook (plus optional immutable release
files). `register_aiddata_snapshot` records the actual external file paths, sizes, and SHA-256 hashes in
`SourceSnapshotRef`; it does not copy the workbook.

`materialize_aiddata_silver` preserves the relational model recovered by the June extractor and writes:

```text
silver/investments/aiddata_clg/<release>/
  records.parquet
  borrower_ownership.parquet
  country_list.parquet
  definitions_records.parquet
  definitions_borrower_ownership.parquet
  column_name_mapping.parquet
```

The source sheets are not flattened together. Borrower ownership remains a child table and multiple
ownership rows for one `aiddata_record_id` are valid. Records retain the source `aiddata_record_id` and
all named source variables. Engineering provenance columns are prefixed `fcv_`. Snake-case column
normalization is reversible through `column_name_mapping.parquet`.

The only automatically excluded columns are unnamed Excel layout columns that contain no values at all;
the exclusion and reason are recorded in the column map. Named source variables are retained even when
currently all missing.

Silver uses `L3_REBUILT`: this means rebuilt from source authority, not L4 research validation.

### AidData QA

The run records table shapes, source-ID missingness/duplicates, borrower ownership referential integrity,
many-to-one child multiplicity, and a column coverage profile. Missingness is descriptive only. If a
legacy relational extraction path is supplied, parity compares record IDs/source fields and per-table
row counts. If it is absent, parity is `NOT_RUN` rather than fabricated.

## World Bank Projects API

The downloaded `page_os_*.json` responses are Bronze authority. `worldbank_projects_flat.csv` and
`worldbank_projects_raw.jsonl` remain useful legacy derivatives, but they are not registered as the raw
snapshot.

`register_worldbank_snapshot` includes page responses and acquisition sidecars such as query logs,
errors, page counts, and source metadata when present. `materialize_worldbank_silver` writes:

```text
silver/investments/worldbank/<snapshot_id>/
  projects.parquet
```

Natural grain is one source project per exact World Bank `id`. Nested dictionaries are flattened by
path; lists are stored as canonical JSON strings. Each row also carries `fcv_source_record_json`, so the
original project object remains reconstructible even when a nested analytical column is inconvenient.
Raw page file, offset, and record position are retained as provenance.

Dates and amounts remain source fields. In particular:

- `boardapprovaldate` remains board approval date;
- `closingdate` remains closing date;
- `totalcommamt` remains the source total-commitment field.

None of these becomes implementation start, completion, local spending, or spatially allocated finance
inside Silver.

### World Bank QA

The run records downloaded page count, recovered record count, exact-`id` missingness and duplicates,
JSON parse errors, acquisition errors, and column coverage. If the old flat CSV is supplied, parity
compares row/key overlap plus a small set of exact source fields. Flat-vs-Parquet byte equality is not a
goal.

## Derived annotation review view

`materialize_annotation_candidates` reads only contract-backed Silver files whose content hash matches
the supplied `DatasetRef`. The derived manifest lists those Silver refs as its inputs, making the lineage
explicit:

```text
Silver DatasetRef(s)
        |
        v
gold/investments/annotation_candidates/<version>/annotation_candidates.parquet
```

The `gold/` location is the closest shared `DataRoot` layer for a derived product; scientific authority
is explicitly `L2_DERIVED`, not L4. The view carries source family/id/project ID, stable annotation ID,
annotation schema version, mapping provenance, conservative text-bundle fields, and raw source amount
fields with explicit bases.

The view does not run an LLM and contains no jobs/non-jobs classification. It does not filter projects
into treatment eligibility.

### Legacy-derived convenience aliases

Some fields exist only because the recovered annotation workflow wanted one review-friendly schema
across sources. They are **not** promoted into Silver and must not be read as source-native ontology.
Every annotation row carries `mapping_provenance`, which names the exact Silver field behind these
convenience columns.

For World Bank in particular:

- `countrycode -> country_iso3` is retained only as a legacy-derived convenience alias; this PR does not
  independently assert that the source field has ISO3 semantics;
- `prodlinetext -> finance_type` is a review alias for the source product-line text, not a canonical
  finance-type classification;
- `source -> flow_class` is a legacy-derived alias and does not redefine the source field as a flow
  ontology;
- `theme_list -> theme_or_intent` is a review convenience label;
- `supplementprojectflg -> source_umbrella_flag` is a legacy-derived convenience label.

AidData convenience renames in this view are likewise derived. The source-native Silver table remains
the authority for the underlying fields, and the annotation mapping must be consulted before using a
common-schema name substantively.

The old annotation contract mapped World Bank `boardapprovaldate` to `implementation_start_year` and
`closingdate` to `completion_year`. The rebuilt view deliberately does not repeat those aliases. It
carries `board_approval_date` and `closing_date` with their native meanings and records the change in an
annotation compatibility report when the legacy candidate file is available. Legacy amount parsing is
also replaced by `source_amount_raw`, `source_amount_field`, and `source_amount_basis`; malformed source
values therefore remain visible rather than becoming zero.

## Run artifacts

Every successful source or derived materialization has:

```text
runs/fcv-empirical-data/<run_id>/
  run_manifest.json
  artifacts/
    contracts/dataset_refs.json
    provenance/inputs.json
    qa/qa_results.json
    parity/parity_report.json
    ... source-specific QA/provenance sidecars
```

`RunManifest` retains input contracts, parameters, code revision when supplied, output dataset refs,
SHA-256 content hashes, and QA results. Default publication is staged and no-clobber.

## Explicit non-goals

This vertical does not implement project locations, GID assignment, exposure, treatment/control,
current-project filtering, cross-source deduplication, source harmonization, jobs annotation, LLM
execution, matching, regressions, or estimator changes.
