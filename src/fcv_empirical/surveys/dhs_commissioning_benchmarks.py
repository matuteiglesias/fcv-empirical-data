from __future__ import annotations

from collections.abc import Mapping

from .dhs_commissioning import DhsCommissioningSpec


def nigeria_2018_commissioning_specs(
    *,
    water_category_map: Mapping[str, str] | None = None,
) -> tuple[DhsCommissioningSpec, ...]:
    """Return the four predeclared Nigeria 2018 external-reference checks."""

    return (
        DhsCommissioningSpec(
            benchmark_id="dhs.ng2018.household_electricity.national",
            survey_id="dhs-NG2018DHS",
            measurement_id="dhs.household.electricity_access",
            reference_authority="The DHS Program",
            reference_publication="Nigeria Demographic and Health Survey 2018",
            reference_publication_id="FR359",
            reference_url="https://www.dhsprogram.com/pubs/pdf/FR359/FR359.pdf",
            reference_table="2.4 Household characteristics",
            reference_page=26,
            expected_percentages={"no": 40.6, "yes": 59.4},
            category_map={"false": "no", "true": "yes"},
            notes=("National household distribution; source missing remains in the denominator.",),
        ),
        DhsCommissioningSpec(
            benchmark_id="dhs.ng2018.de_jure_electricity.national",
            survey_id="dhs-NG2018DHS",
            measurement_id="dhs.household.electricity_access",
            reference_authority="The DHS Program",
            reference_publication="Nigeria Demographic and Health Survey 2018",
            reference_publication_id="FR359",
            reference_url="https://www.dhsprogram.com/pubs/pdf/FR359/FR359.pdf",
            reference_table="2.4 Household characteristics",
            reference_page=26,
            expected_percentages={"no": 43.5, "yes": 56.5},
            category_map={"false": "no", "true": "yes"},
            population_multiplier_variable="HV012",
            notes=("National de jure population; effective weight is HV005 x HV012.",),
        ),
        DhsCommissioningSpec(
            benchmark_id="dhs.ng2018.drinking_water_source.national",
            survey_id="dhs-NG2018DHS",
            measurement_id="dhs.household.drinking_water_source_code",
            reference_authority="The DHS Program",
            reference_publication="Nigeria Demographic and Health Survey 2018",
            reference_publication_id="FR359",
            reference_url="https://www.dhsprogram.com/pubs/pdf/FR359/FR359.pdf",
            reference_table="2.1.1 Household drinking water",
            reference_page=21,
            expected_percentages={
                "piped_dwelling_yard_plot": 3.0,
                "piped_neighbour": 0.7,
                "public_tap_standpipe": 7.5,
                "tube_well_borehole": 37.2,
                "protected_dug_well": 11.4,
                "protected_spring": 0.5,
                "rainwater": 2.0,
                "tanker_or_cart": 2.6,
                "bottled_water": 0.7,
                "unprotected_dug_well": 13.5,
                "unprotected_spring": 1.6,
                "surface_water": 9.4,
                "sachet_water": 9.8,
                "other": 0.1,
            },
            category_map=dict(water_category_map or {}),
            require_release_category_map=True,
            notes=(
                (
                    "Release-local HV201 code-to-report-cell mapping must be verified "
                    "before execution."
                ),
                "This benchmark does not infer improved/unimproved drinking-water semantics.",
            ),
        ),
        DhsCommissioningSpec(
            benchmark_id="dhs.ng2018.wealth_quintile.urban_de_jure",
            survey_id="dhs-NG2018DHS",
            measurement_id="dhs.household.wealth_quintile",
            reference_authority="The DHS Program",
            reference_publication="Nigeria Demographic and Health Survey 2018",
            reference_publication_id="FR359",
            reference_url="https://www.dhsprogram.com/pubs/pdf/FR359/FR359.pdf",
            reference_table="2.6 Wealth quintiles",
            reference_page=28,
            expected_percentages={
                "lowest": 4.2,
                "second": 8.0,
                "middle": 18.9,
                "fourth": 30.6,
                "highest": 38.4,
            },
            category_map={
                "1": "lowest",
                "2": "second",
                "3": "middle",
                "4": "fourth",
                "5": "highest",
            },
            population_multiplier_variable="HV012",
            domain_variable="HV025",
            domain_allowed_values=("1",),
            notes=("Urban de jure population distribution; effective weight is HV005 x HV012.",),
        ),
    )


def uganda_2016_commissioning_specs() -> tuple[DhsCommissioningSpec, ...]:
    return (
        DhsCommissioningSpec(
            benchmark_id="dhs.ug2016.household_electricity.national",
            survey_id="dhs-UG2016DHS",
            measurement_id="dhs.household.electricity_access",
            reference_authority="The DHS Program",
            reference_publication="Uganda Demographic and Health Survey 2016",
            reference_publication_id="FR333",
            reference_url="https://www.dhsprogram.com/Pubs/Pdf/Fr333/Fr333.Pdf",
            reference_table="2.4 Household characteristics",
            reference_page=23,
            expected_percentages={"no": 71.4, "yes": 28.6},
            category_map={"false": "no", "true": "yes"},
        ),
    )


def zambia_2018_commissioning_specs() -> tuple[DhsCommissioningSpec, ...]:
    return (
        DhsCommissioningSpec(
            benchmark_id="dhs.zm2018.household_electricity.national",
            survey_id="dhs-ZM2018DHS",
            measurement_id="dhs.household.electricity_access",
            reference_authority="The DHS Program",
            reference_publication="Zambia Demographic and Health Survey 2018",
            reference_publication_id="FR361",
            reference_url="https://www.dhsprogram.com/pubs/pdf/FR361/FR361.pdf",
            reference_table="2.4 Household characteristics",
            reference_page=22,
            expected_percentages={"no": 65.8, "yes": 34.2},
            category_map={"false": "no", "true": "yes"},
        ),
    )


__all__ = [
    "nigeria_2018_commissioning_specs",
    "uganda_2016_commissioning_specs",
    "zambia_2018_commissioning_specs",
]
