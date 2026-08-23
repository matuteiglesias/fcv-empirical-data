from pathlib import Path

import pandas as pd
from empirical_contracts import AuthorityLevel, DataLayer, DatasetRef, GrainSpec
from spatial_foundation import DataRoot, sha256_file

from fcv_empirical.investments.annotation_candidates import (
    SilverTableInput,
    build_worldbank_annotation_candidates,
    materialize_annotation_candidates,
)


def test_worldbank_annotation_keeps_native_date_meanings_and_raw_amount() -> None:
    projects = pd.DataFrame(
        {
            "id": ["P1"],
            "project_name": ["Project"],
            "boardapprovaldate": ["2017-05-12"],
            "approvalfy": ["FY2017"],
            "closingdate": ["2021-12-31"],
            "totalcommamt": ["12x malformed"],
            "sector1.Name": ["Water"],
            "projectstatusdisplay": ["Closed"],
        }
    )

    candidates = build_worldbank_annotation_candidates(projects)
    row = candidates.iloc[0]

    assert row["board_approval_date"] == "2017-05-12"
    assert pd.isna(row["implementation_start_year"])
    assert row["closing_date"] == "2021-12-31"
    assert pd.isna(row["completion_year"])
    assert row["source_amount_raw"] == "12x malformed"
    assert row["source_amount_field"] == "totalcommamt"
    assert "Board approval date: 2017-05-12" in row["text_bundle_for_annotation"]
    assert "Implementation start year" not in row["text_bundle_for_annotation"]
    assert not any("job" in column.lower() for column in candidates.columns)


def test_derived_annotation_materialization_does_not_replace_silver(tmp_path: Path) -> None:
    root = DataRoot.from_path(tmp_path)
    silver_dir = root.silver("investments", "worldbank", "snapshot-1")
    silver_dir.mkdir(parents=True)
    silver_path = silver_dir / "projects.parquet"
    projects = pd.DataFrame(
        {
            "id": ["P1"],
            "project_name": ["Project"],
            "boardapprovaldate": ["2017-05-12"],
            "closingdate": ["2021-12-31"],
            "totalcommamt": ["1000"],
        }
    )
    projects.to_parquet(silver_path, index=False, engine="pyarrow")
    input_hash = sha256_file(silver_path)
    silver_ref = DatasetRef(
        dataset_id="investments.worldbank.projects",
        version="snapshot-1",
        schema_version="source-native-silver-v1",
        layer=DataLayer.SILVER,
        authority=AuthorityLevel.L3_REBUILT,
        grain=GrainSpec(keys=("id",)),
        content_sha256=input_hash,
    )

    legacy = build_worldbank_annotation_candidates(projects)
    legacy["implementation_start_year"] = "2017"
    legacy["completion_year"] = "2021"
    legacy_path = tmp_path / "legacy_annotation.csv"
    legacy.to_csv(legacy_path, index=False)

    result = materialize_annotation_candidates(
        data_root=root,
        run_id="annotation-derived",
        version="v1",
        worldbank_projects=SilverTableInput(dataset=silver_ref, path=silver_path),
        legacy_candidate_path=legacy_path,
    )

    assert sha256_file(silver_path) == input_hash
    assert result.paths["annotation_candidates"] != silver_path
    assert result.paths["annotation_candidates"].parent == root.gold(
        "investments", "annotation_candidates", "v1"
    )
    assert result.manifest.inputs == (silver_ref,)
    assert result.datasets["annotation_candidates"].authority == AuthorityLevel.L2_DERIVED
    assert result.parity["status"] == "EXPLAINED_DIVERGENCE"
