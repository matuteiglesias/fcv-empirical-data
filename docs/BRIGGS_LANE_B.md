# Briggs (2016) Lane B — independent source reconstruction

## Purpose

Lane B is an external positive-control reconstruction of Briggs (2016), *Does Foreign Aid Target the Poorest?* It asks whether the FCV measurement system can independently rebuild the empirical quantities that feed the published regional regressions.

The published replication `.dta` and `.do` are **oracle evidence only**. They may be used to state targets and diagnose mismatches, but they must never be read as inputs to the Lane B source reconstruction.

```text
source releases
    ↓
source-native Silver
    ↓
explicit historical-region relations
    ↓
semantic measurements
    ↓
independent 17-country / historical-region analysis table
    ↓
Briggs estimator reproduction + parity diagnostics
```

A mismatch is evidence. Do not change source decoding, geography, weights, amount allocation, or sample rules merely to move a coefficient toward the published number.

## Frozen external target

The paper and supplied replication package establish the following benchmark surface:

- 17 sub-Saharan African countries;
- 195 historical regional units;
- aid approved in 2009 and 2010;
- donors: World Bank and African Development Bank;
- AidData geolocation precision `< 5` for analysis;
- 1,351 included geolocated subprojects/lowest-level aid units in the supplied final table:
  - 856 World Bank;
  - 495 African Development Bank;
- 145 regions with at least one included aid unit and 50 zero-aid regions in the supplied final table;
- country fixed-effects OLS with robust standard errors clustered by country.

The main published Table 3 targets are diagnostic oracles, not optimization objectives.

## Historical source ledger

### DHS Household Recode

The survey roster is fixed by Appendix A:

| Country | DHS survey year |
|---|---:|
| Benin | 2006 |
| DRC | 2007 |
| Ethiopia | 2005 |
| Ghana | 2008 |
| Guinea | 2005 |
| Kenya | 2003 |
| Lesotho | 2004 |
| Malawi | 2004 |
| Mali | 2006 |
| Mozambique | 2003 |
| Namibia | 2006 |
| Niger | 2006 |
| Nigeria | 2008 |
| Rwanda | 2007-2008 |
| Sierra Leone | 2008 |
| Tanzania | 2004-2005 |
| Zambia | 2007 |

Canonical FCV ingestion remains the official DHS fixed-width `.DAT + .DCT` path. Historical release identity and the release-specific survey-region variable must be verified from source authority; do not infer survey identity from filenames and do not assume a modern region variable mapping.

The wealth measurement is:

```text
household membership weight = HV005 × HV012
```

(or explicit release-specific source-variable aliases after verification), followed by:

```text
weighted de-jure population in region r and national wealth quintile q
---------------------------------------------------------------------
weighted de-jure population in national wealth quintile q
```

`build_dhs_wealth_region_shares(...)` implements only this semantic measurement. It does not assign historical survey regions to modern geography.

Primary DHS positive controls before aid is joined:

1. each national quintile's regional shares sum to 1;
2. the survey's observed region count matches the intended historical survey geography;
3. Kenya and Ghana can be compared against Appendix A's published quintile-by-region tables;
4. `expected_population_share = (poorest + second poorest + middle + second richest + richest) / 5` can be compared with the paper's census validation.

### Historical geocoded World Bank source

Candidate source-faithful archive:

```text
AllWorldBank_IBRDIDA.csv.zip
```

Historical AidData release identity:

```text
All World Bank IBRD/IDA, 2011
```

Registered source URL in code:

```text
https://github.com/AidData-WM/public_datasets/raw/master/geocoded/AllWorldBank_IBRDIDA.csv.zip
```

The historical release covers a broader approval window than Briggs; the scientific projection must later select approvals in 2009 and 2010 explicitly.

### Historical geocoded African Development Bank source

Candidate source-faithful archive:

```text
AfDB_2009_2010_AllApprovedProjects.xlsx.zip
```

Registered source URL in code:

```text
https://github.com/AidData-WM/public_datasets/raw/master/geocoded/AfDB_2009_2010_AllApprovedProjects.xlsx.zip
```

Its published scope is already 2009-2010, but the Lane B scientific measurement must still state the period rule explicitly.

`materialize_historical_aiddata_silver(...)` ingests either historical archive directly into source-row Silver and persists a sanitized schema audit. It deliberately does **not** guess which source columns mean project identity, subproject/location identity, approval date, precision, region, or amount.

### Historical regional geography

The paper's geography is the survey-era standard regional system and cites FAO GAUL for ADM1 shapefiles. Historical regional identity has priority over polygon provider.

Preferred order:

1. recover the historical GAUL representation used by or compatible with the paper;
2. otherwise use another polygon representation of the same historical region entities;
3. otherwise construct a documented aggregation/crosswalk from a modern source into those historical entities.

Using current GADM ADM1 entities *as-is* is not a valid substitute when boundaries changed (for example, Kenya's historical eight provinces versus current county geography).

A region crosswalk must be explicit, versioned, reviewable, and one-to-one at the analysis-region identity level. No fuzzy string matching is allowed in a canonical run.

### ACLED

The control is battles in 2007-2008, mapped to the same historical regions. The original ACLED vintage should be recovered if practical. A later historical export is an allowed documented source-vintage substitution only if the exact vintage cannot be recovered; any mismatch against the replication oracle remains visible.

## Aid measurement rules that must be commissioned from the historical schemas

The paper establishes:

- donor = World Bank or African Development Bank;
- approval year = 2009 or 2010;
- geolocation precision `< 5`;
- the unit called a project in the regional analysis is the lowest-level geolocated subproject/location unit;
- the parent project's reported cost is divided equally across its subprojects before regional aggregation.

Before implementing the amount measurement, the historical archive audit must pin exact source fields for:

```text
parent project identity
subproject/location identity
approval year/date
AidData precision code
recipient country
historical region label/code
parent project amount and currency/unit basis
```

The allocation denominator must also be stated explicitly from source semantics and replication evidence. No amount field or malformed amount may be silently coerced to zero.

## Current implementation boundary

This foundation wave adds:

- `surveys.dhs_wealth_distribution`: reusable DHS regional quintile-share measurement;
- `investments.aiddata_historical_geocoded`: direct ZIP-backed source-native Silver for the two historical AidData candidate releases;
- schema audits that expose candidate fields for human/local review but select none automatically.

It intentionally does **not** yet implement:

- a historical-region polygon product;
- a release-specific AidData semantic field map;
- amount allocation;
- region-level aid Gold;
- ACLED historical-region aggregation;
- the final 195-region regression frame.

Those are gated on the local source census below.

## Local B0 handoff

Download the two historical archives to external source storage, never into Git:

```bash
ROOT=/media/matias/Elements1/sources/AidData/Briggs2016
mkdir -p "$ROOT"

curl -L --fail --retry 3 \
  -o "$ROOT/AllWorldBank_IBRDIDA.csv.zip" \
  'https://github.com/AidData-WM/public_datasets/raw/master/geocoded/AllWorldBank_IBRDIDA.csv.zip'

curl -L --fail --retry 3 \
  -o "$ROOT/AfDB_2009_2010_AllApprovedProjects.xlsx.zip" \
  'https://github.com/AidData-WM/public_datasets/raw/master/geocoded/AfDB_2009_2010_AllApprovedProjects.xlsx.zip'

sha256sum "$ROOT"/*.zip
```

Run a sanitized schema census from a checkout containing this branch:

```bash
PYTHONPATH=src python3 - <<'PY'
from pathlib import Path
from fcv_empirical.investments import (
    BRIGGS_AFDB_2009_2010,
    BRIGGS_WB_2011,
    write_historical_aiddata_schema_audit,
)

root = Path('/media/matias/Elements1/sources/AidData/Briggs2016')
out = Path.home() / '.local/share/fcv-briggs-lane-b'
out.mkdir(parents=True, exist_ok=True)

for release in (BRIGGS_WB_2011, BRIGGS_AFDB_2009_2010):
    write_historical_aiddata_schema_audit(
        root / release.archive_filename,
        release=release,
        output_path=out / f'{release.release_id}-schema-audit.json',
    )
    print(out / f'{release.release_id}-schema-audit.json')
PY
```

Then inventory the local DHS estate for the 17 historical surveys. The handoff should contain only non-sensitive source metadata:

- country and survey year;
- verified DHS survey identifier;
- exact HR `.DAT` and `.DCT` filenames;
- source hashes;
- whether `HV005`, `HV012`, `HV270` exist;
- candidate historical survey-region variable(s), especially `HV024` and release-specific alternatives;
- distinct region codes and region count only (no household rows).

Finally report any existing historical GAUL/ADM1 files on local storage, including filenames, version/vintage if known, hashes, CRS, and country/region counts. Do not download or fabricate a modern replacement merely to fill this slot.

## Acceptance ladder

Lane B is advanced in layers, never by final-coefficient search:

```text
B0 source identity and schema census
B1 DHS regional wealth measurement parity
B2 historical region identity/geography parity
B3 WB + AfDB project-count and allocated-value parity
B4 ACLED / area / capital controls
B5 independent regional analysis table
B6 published estimator reproduction
```

Useful oracle checks include 17 countries, 195 regions, 1,351 included aid units, 856 WB, 495 AfDB, and the published Table 3 coefficients. They are diagnostics, not knobs.
