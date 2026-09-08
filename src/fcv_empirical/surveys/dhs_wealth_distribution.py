# ruff: noqa: I001
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
from empirical_contracts import QAResult


QUINTILE_LABELS = {
    1: "poorest",
    2: "second_poorest",
    3: "middle",
    4: "second_richest",
    5: "richest",
}


@dataclass(frozen=True)
class DhsWealthRegionSpec:
    """Declare one survey's source variables for regional wealth-distribution measurement.

    The measurement estimates, for each national wealth quintile, the share of that
    quintile's de-jure population living in each survey region. It deliberately does
    not assign survey regions to a modern polygon system.
    """

    survey_id: str
    region_variable: str
    wealth_quintile_variable: str = "HV270"
    sample_weight_variable: str = "HV005"
    dejure_household_size_variable: str = "HV012"

    def __post_init__(self) -> None:
        for name in (
            "survey_id",
            "region_variable",
            "wealth_quintile_variable",
            "sample_weight_variable",
            "dejure_household_size_variable",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty")


@dataclass(frozen=True)
class DhsWealthRegionResult:
    frame: pd.DataFrame
    wide: pd.DataFrame
    diagnostics: dict[str, Any]
    qa: tuple[QAResult, ...]


def _resolve_column(frame: pd.DataFrame, requested: str) -> str:
    matches = [column for column in frame.columns if str(column).casefold() == requested.casefold()]
    if not matches:
        raise ValueError(f"DHS HR frame is missing required source variable {requested!r}")
    if len(matches) != 1:
        raise ValueError(f"DHS HR frame has ambiguous case variants for {requested!r}")
    return str(matches[0])


def _numeric_required(series: pd.Series, *, variable: str) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    invalid = numeric.isna()
    if invalid.any():
        raise ValueError(
            f"{variable} has {int(invalid.sum())} missing/non-numeric values; "
            "the wealth-distribution measurement fails closed rather than silently dropping households"
        )
    return numeric.astype("float64")


def build_dhs_wealth_region_shares(
    hr_frame: pd.DataFrame,
    spec: DhsWealthRegionSpec,
) -> DhsWealthRegionResult:
    """Estimate region shares of each DHS national wealth quintile.

    Source-faithful weighting is ``HV005 * HV012`` (or the explicitly declared
    release-specific aliases). The source scale of HV005 is preserved: no division by
    one million is required because only ratios of weighted population mass are used.

    A zero cell is licensed only inside the observed survey-region x {1..5} universe.
    Missing required source values are never recoded to zero or discarded implicitly.
    """

    if hr_frame.empty:
        raise ValueError("DHS HR frame must be non-empty")

    survey_col = (
        _resolve_column(hr_frame, "survey_id")
        if "survey_id" in {str(column).casefold() for column in hr_frame.columns}
        else None
    )
    if survey_col is not None:
        survey_values = hr_frame[survey_col].astype("string").str.strip().dropna().unique().tolist()
        if survey_values != [spec.survey_id]:
            raise ValueError(
                f"DHS HR frame must contain exactly survey_id={spec.survey_id!r}; "
                f"found {sorted(str(value) for value in survey_values)}"
            )

    region_col = _resolve_column(hr_frame, spec.region_variable)
    quintile_col = _resolve_column(hr_frame, spec.wealth_quintile_variable)
    weight_col = _resolve_column(hr_frame, spec.sample_weight_variable)
    size_col = _resolve_column(hr_frame, spec.dejure_household_size_variable)

    region = hr_frame[region_col].astype("string").str.strip()
    missing_region = region.isna() | region.eq("")
    if missing_region.any():
        raise ValueError(
            f"{spec.region_variable} has {int(missing_region.sum())} missing region codes; "
            "region membership must be resolved upstream"
        )

    quintile_numeric = _numeric_required(hr_frame[quintile_col], variable=spec.wealth_quintile_variable)
    non_integer = ~quintile_numeric.mod(1).eq(0)
    if non_integer.any():
        raise ValueError(f"{spec.wealth_quintile_variable} contains non-integer quintile values")
    quintile = quintile_numeric.astype("int64")
    invalid_quintile = ~quintile.isin(QUINTILE_LABELS)
    if invalid_quintile.any():
        values = sorted(quintile.loc[invalid_quintile].unique().tolist())
        raise ValueError(
            f"{spec.wealth_quintile_variable} must contain DHS quintiles 1..5; found {values}"
        )

    sample_weight = _numeric_required(hr_frame[weight_col], variable=spec.sample_weight_variable)
    nonpositive_weight = sample_weight.le(0)
    if nonpositive_weight.any():
        raise ValueError(
            f"{spec.sample_weight_variable} has {int(nonpositive_weight.sum())} non-positive values"
        )

    household_size = _numeric_required(hr_frame[size_col], variable=spec.dejure_household_size_variable)
    negative_size = household_size.lt(0)
    if negative_size.any():
        raise ValueError(
            f"{spec.dejure_household_size_variable} has {int(negative_size.sum())} negative values"
        )

    working = pd.DataFrame(
        {
            "survey_id": spec.survey_id,
            "region_code": region,
            "wealth_quintile": quintile,
            "weighted_dejure_population": sample_weight * household_size,
        }
    )

    regions = tuple(sorted(working["region_code"].unique().tolist()))
    quintiles = tuple(QUINTILE_LABELS)
    support = pd.MultiIndex.from_product(
        [regions, quintiles], names=["region_code", "wealth_quintile"]
    ).to_frame(index=False)

    observed = (
        working.groupby(["region_code", "wealth_quintile"], as_index=False, sort=True)[
            "weighted_dejure_population"
        ]
        .sum()
    )
    long = support.merge(observed, on=["region_code", "wealth_quintile"], how="left")
    long["weighted_dejure_population"] = long["weighted_dejure_population"].fillna(0.0)
    national = (
        long.groupby("wealth_quintile", as_index=False)["weighted_dejure_population"]
        .sum()
        .rename(columns={"weighted_dejure_population": "national_quintile_weight"})
    )
    empty_quintiles = national.loc[national["national_quintile_weight"].le(0), "wealth_quintile"]
    if len(empty_quintiles):
        raise ValueError(
            "national weighted de-jure population is zero for wealth quintiles "
            + ",".join(str(value) for value in empty_quintiles.tolist())
        )

    long = long.merge(national, on="wealth_quintile", how="left", validate="many_to_one")
    long["wealth_quintile_region_share"] = (
        long["weighted_dejure_population"] / long["national_quintile_weight"]
    )
    long["wealth_quintile_label"] = long["wealth_quintile"].map(QUINTILE_LABELS)
    long.insert(0, "survey_id", spec.survey_id)
    long = (
        long[
            [
                "survey_id",
                "region_code",
                "wealth_quintile",
                "wealth_quintile_label",
                "weighted_dejure_population",
                "national_quintile_weight",
                "wealth_quintile_region_share",
            ]
        ]
        .sort_values(["region_code", "wealth_quintile"])
        .reset_index(drop=True)
    )

    sums = long.groupby("wealth_quintile")["wealth_quintile_region_share"].sum()
    max_sum_error = float((sums - 1.0).abs().max())
    if max_sum_error > 1e-10:
        raise ValueError(f"wealth-quintile region shares do not sum to one: max error={max_sum_error}")

    wide = long.pivot(
        index=["survey_id", "region_code"],
        columns="wealth_quintile_label",
        values="wealth_quintile_region_share",
    ).reset_index()
    wide.columns.name = None
    ordered_share_columns = [QUINTILE_LABELS[index] for index in sorted(QUINTILE_LABELS)]
    wide = wide[["survey_id", "region_code", *ordered_share_columns]]
    for column in ordered_share_columns:
        wide = wide.rename(columns={column: f"{column}_share"})
    share_columns = [f"{label}_share" for label in ordered_share_columns]
    wide["expected_population_share"] = wide[share_columns].sum(axis=1) / 5.0
    wide = wide.sort_values("region_code").reset_index(drop=True)

    zero_cells = int(long["weighted_dejure_population"].eq(0).sum())
    diagnostics = {
        "survey_id": spec.survey_id,
        "region_variable": spec.region_variable,
        "wealth_quintile_variable": spec.wealth_quintile_variable,
        "sample_weight_variable": spec.sample_weight_variable,
        "dejure_household_size_variable": spec.dejure_household_size_variable,
        "weight_formula": (
            f"{spec.sample_weight_variable} * {spec.dejure_household_size_variable}"
        ),
        "source_weight_scale_normalized": False,
        "household_rows": len(hr_frame),
        "region_count": len(regions),
        "dense_region_quintile_rows": len(long),
        "zero_region_quintile_cells": zero_cells,
        "max_quintile_share_sum_error": max_sum_error,
    }
    qa = (
        QAResult(
            check_id="dhs.wealth_region.required_source_values",
            state="GREEN",
            message="required survey region, quintile, weight, and de-jure size values are resolved",
            metrics={"household_rows": len(hr_frame), "region_count": len(regions)},
        ),
        QAResult(
            check_id="dhs.wealth_region.quintile_mass",
            state="GREEN",
            message="each national wealth quintile's regional shares sum to one",
            metrics={"max_abs_sum_error": max_sum_error},
        ),
        QAResult(
            check_id="dhs.wealth_region.zero_cells",
            state="GREEN",
            message=(
                "zero region-quintile cells are materialized only inside the observed survey-region "
                "universe after source values are validated"
            ),
            metrics={"zero_region_quintile_cells": zero_cells, "dense_rows": len(long)},
        ),
    )
    return DhsWealthRegionResult(frame=long, wide=wide, diagnostics=diagnostics, qa=qa)
