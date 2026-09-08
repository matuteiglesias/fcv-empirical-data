from __future__ import annotations

import pandas as pd

from fcv_empirical.investments.briggs_pragmatic_aid import (
    normalize_afdb_pragmatic,
    normalize_world_bank_pragmatic,
)


def test_world_bank_pragmatic_filter_dedup_and_allocation():
    raw = pd.DataFrame(
        {
            "Project ID": ["P1", "P1", "P1", "P2"],
            "GeoNameID": ["A", "A", "B", "C"],
            "Approval Date": ["2009-01-01", "2009-01-01", "2009-01-01", "2008-01-01"],
            "Precision": [2, 2, 4.2, 2],
            "Total Amt": [100, 100, 100, 200],
            "Latitude": [1, 1, 2, 3],
            "Longitude": [10, 10, 20, 30],
            "ADM1": ["R1", "R1", "R2", "R3"],
        }
    )
    pairs, stats = normalize_world_bank_pragmatic(raw)
    assert len(pairs) == 2
    assert stats["eligible_rows_before_pair_dedup"] == 3
    assert stats["eligible_parent_child_pairs"] == 2
    assert pairs["allocated_parent_value"].tolist() == [50.0, 50.0]
    assert set(pairs["parent_child_identity_policy"]) == {"source_project_id_plus_geonameid"}


def test_afdb_uses_normalized_project_name_only_as_declared_fallback():
    raw = pd.DataFrame(
        {
            "Project Name": ["  Water   Project ", "water project", "Other"],
            "GeoNameID": ["A", "B", "C"],
            "Board Approval": [2009, 2010, 2010],
            "Precision": [3, 4, 6],
            "Project Cost": [90, 90, 10],
            "Latitude": [1, 2, 3],
            "Longitude": [10, 20, 30],
            "ADM 1 Name": ["R1", "R2", "R3"],
        }
    )
    pairs, stats = normalize_afdb_pragmatic(raw)
    assert len(pairs) == 2
    assert stats["parent_count"] == 1
    assert pairs["allocated_parent_value"].tolist() == [45.0, 45.0]
    assert set(pairs["parent_child_identity_policy"]) == {"normalized_project_name_plus_geonameid_fallback"}


def test_parent_amount_conflict_is_visible_not_tuned_away():
    raw = pd.DataFrame(
        {
            "Project ID": ["P1", "P1"],
            "GeoNameID": ["A", "B"],
            "Approval Date": [2009, 2009],
            "Precision": [2, 2],
            "Total Amt": [100, 120],
            "Latitude": [1, 2],
            "Longitude": [10, 20],
            "ADM1": ["R1", "R2"],
        }
    )
    _, stats = normalize_world_bank_pragmatic(raw)
    assert stats["parent_amount_conflicts"] == 1
