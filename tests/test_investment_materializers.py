import json
from pathlib import Path

import pandas as pd
from empirical_contracts import DataLayer, SourceSnapshotRef
from spatial_foundation import DataRoot

from fcv_empirical.investments.aiddata_clg import materialize_aiddata_silver
from fcv_empirical.investments.worldbank import materialize_worldbank_silver


def _write_aiddata_workbook(path: Path) -> None:
    records = pd.DataFrame(
        {
            "AidData Record ID": ["p1", "p2"],
            "Parent ID": [None, "p1"],
            "Title": ["One", "Two"],
            "Amount Constant USD 2023": ["1000", "malformed"],
        }
    )
    ownership = pd.DataFrame(
        {
            "AidData Record ID": ["p1", "p1", "p2"],
            "Borrower Type": ["central", "local", "central"],
        }
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        records.to_excel(writer, sheet_name="CLG-LMIC 1.0_Records", index=False)
        ownership.to_excel(writer, sheet_name="Borrower Ownership", index=False)
        pd.DataFrame({"Country": ["Example"]}).to_excel(
            writer, sheet_name="CountryList", index=False, startrow=4
        )
        pd.DataFrame({"Variable": ["AidData Record ID"], "Definition": ["ID"]}).to_excel(
            writer, sheet_name="Definitions_Records", index=False, startrow=3
        )
        pd.DataFrame({"Variable": ["Borrower Type"], "Definition": ["Type"]}).to_excel(
            writer,
            sheet_name="Definitions_Borrower Ownership",
            index=False,
            startrow=3,
        )


def test_aiddata_materializer_writes_relational_silver_contracts_and_sidecars(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "aiddata.xlsx"
    _write_aiddata_workbook(workbook)
    root = DataRoot.from_path(tmp_path / "data")

    result = materialize_aiddata_silver(
        workbook_path=workbook,
        data_root=root,
        run_id="aiddata-synthetic",
        release="v1.0-test",
    )

    assert isinstance(result.manifest.inputs[0], SourceSnapshotRef)
    assert result.manifest.inputs[0].files[0].sha256
    assert result.parity["status"] == "NOT_RUN"
    assert set(result.datasets) == {
        "records",
        "borrower_ownership",
        "country_list",
        "definitions_records",
        "definitions_borrower_ownership",
        "column_name_mapping",
    }
    assert all(ref.layer == DataLayer.SILVER for ref in result.datasets.values())
    assert all(ref.content_sha256 for ref in result.datasets.values())
    assert all(path.exists() for path in result.paths.values())

    records = pd.read_parquet(result.paths["records"])
    ownership = pd.read_parquet(result.paths["borrower_ownership"])
    assert records["aiddata_record_id"].tolist() == ["p1", "p2"]
    assert ownership["aiddata_record_id"].tolist() == ["p1", "p1", "p2"]
    assert records.loc[1, "amount_constant_usd_2023"] == "malformed"

    run = root.run("fcv-empirical-data", "aiddata-synthetic")
    assert (run / "run_manifest.json").exists()
    assert (run / "artifacts/qa/qa_results.json").exists()
    assert (run / "artifacts/contracts/dataset_refs.json").exists()
    assert (run / "artifacts/provenance/inputs.json").exists()
    assert (run / "artifacts/parity/parity_report.json").exists()


def test_worldbank_materializer_registers_page_json_and_writes_project_silver(tmp_path: Path) -> None:
    source = tmp_path / "worldbank-source"
    source.mkdir()
    payload = {
        "projects": {
            "P1": {
                "id": "P1",
                "project_name": "Project one",
                "boardapprovaldate": "2018-01-02",
                "totalcommamt": "bad-number",
                "sector1": {"Name": "Water"},
            },
            "P2": {"id": "P2", "project_name": "Project two"},
        }
    }
    page = source / "page_os_0000000.json"
    page.write_text(json.dumps(payload), encoding="utf-8")
    (source / "source_metadata.json").write_text(
        json.dumps({"accessed_date": "2026-06-28"}), encoding="utf-8"
    )
    (source / "api_query_log.json").write_text("[]", encoding="utf-8")

    root = DataRoot.from_path(tmp_path / "data")
    result = materialize_worldbank_silver(
        source_dir=source,
        data_root=root,
        run_id="worldbank-synthetic",
    )

    snapshot = result.manifest.inputs[0]
    assert isinstance(snapshot, SourceSnapshotRef)
    assert any(file.path.endswith("page_os_0000000.json") for file in snapshot.files)
    assert not any(file.path.endswith("worldbank_projects_flat.csv") for file in snapshot.files)
    assert result.parity["status"] == "NOT_RUN"
    assert result.datasets["projects"].layer == DataLayer.SILVER
    assert result.datasets["projects"].content_sha256
    projects = pd.read_parquet(result.paths["projects"])
    assert projects["id"].tolist() == ["P1", "P2"]
    assert projects.loc[0, "boardapprovaldate"] == "2018-01-02"
    assert projects.loc[0, "totalcommamt"] == "bad-number"
    assert projects.loc[0, "sector1.Name"] == "Water"

    run = root.run("fcv-empirical-data", "worldbank-synthetic")
    assert (run / "run_manifest.json").exists()
    assert (run / "artifacts/qa/page_audit.json").exists()
    assert (run / "artifacts/provenance/inputs.json").exists()
    assert (run / "artifacts/parity/parity_report.json").exists()
