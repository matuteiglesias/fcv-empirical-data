# Briggs (2017) Lane B — source-faithful reconstruction

## Purpose

This lane reconstructs the empirical substrate for the Briggs regional-aid benchmark from source authority rather than importing the published replication table.

The published replication `.dta` and `.do` are **evaluation oracles only**. They must never be used as source inputs to the Lane B measurements.

## Frozen benchmark target

The reconstruction targets:

- 17 sub-Saharan African countries;
- 195 historical survey-era ADM1 regions;
- one DHS household survey per country, selected according to Briggs's Appendix A rules;
- World Bank and African Development Bank projects approved in 2009 or 2010;
- AidData geocoding precision `< 5` for the analytical aid sample;
- 2007–2008 ACLED Battles as the conflict control;
- country fixed-effects regressions with country-clustered robust standard errors.

The central published positive-control pattern is a large positive association between the regional share of the richest wealth quintile and regional aid allocation, with little comparable targeting toward the poorest quintile.

## Required external source releases

### Historical AidData World Bank geocoded release

Expected upstream archive:

`AllWorldBank_IBRDIDA.csv.zip`

This historical AidData release is donor-correct and spatially suitable for Briggs. The current FCV World Bank Projects API Silver is **not** a substitute because it contains project records but no governed project-location relation.

### Historical AidData African Development Bank geocoded release

Expected upstream archive:

`AfDB_2009_2010_AllApprovedProjects.xlsx.zip`

This is a separate source family. World Bank and AfDB records must remain distinct through source reconstruction and may only be combined in a downstream Briggs-specific measurement that explicitly declares the aggregation rule.

### DHS household releases

Use the authoritative DHS fixed-width `.DAT + .DCT` releases for the survey roster declared by the benchmark specification. Household facts remain protected and external to Git.

### Historical administrative geography

Preferred authority is the historical GAUL representation corresponding to the survey-era ADM1 regions used by Briggs. A different polygon provider is acceptable only if it represents the same historical regional entities through an explicit crosswalk.

Raw modern GADM ADM1 units are not automatically equivalent to the Briggs region universe.

### ACLED

Use Battles in 2007–2008 assigned to the same historical region universe. A later ACLED historical export may be used as a documented source-vintage substitution if the original historical release cannot be recovered. Such a substitution must remain visible in benchmark provenance.

## Scientific boundaries

The Lane B source layer must preserve:

- source project identity;
- donor identity;
- parent-project versus geocoded-location identity;
- source-native approval/date fields;
- source-native amount fields and units;
- geocoding precision and source location labels;
- all source provenance needed to reconstruct filtering decisions.

It must **not** silently:

- convert current World Bank API rows into historical geocoded records;
- use Chinese development finance as a donor substitute;
- infer missing project locations;
- duplicate parent-project amounts across multiple locations;
- coerce modern ADM1 polygons into historical regions without a crosswalk;
- treat the Briggs replication table as source authority.

## Planned governed products

The minimum useful upstream products are:

1. source-native historical World Bank geocoded location Silver;
2. source-native historical AfDB geocoded location Silver;
3. a historical-region identity/crosswalk relation;
4. DHS regional wealth-quintile shares using `HV005 * HV012`;
5. a Briggs aid-allocation Gold product that allocates parent-project value equally across included geocoded subprojects/locations before regional aggregation;
6. 2007–2008 ACLED Battles by the same historical regions.

The experiment harness owns the benchmark specification, joins, parity report, estimator and comparison to the external oracle.