import json
from pathlib import Path

from fcv_empirical.investments.worldbank import load_worldbank_pages


def _qa(results, check_id):
    return next(result for result in results if result.check_id == check_id)


def test_worldbank_nested_payload_flattens_without_reinterpreting_dates_or_amounts(tmp_path: Path) -> None:
    payload = {
        "projects": {
            "P1": {
                "id": "P1",
                "project_name": "Water project",
                "boardapprovaldate": "2017-05-12T00:00:00Z",
                "closingdate": "2021-12-31T00:00:00Z",
                "totalcommamt": "USD malformed 12x",
                "sector1": {"Name": "Water Supply", "Code": "WX"},
                "themes": [{"Name": "Institutions"}, {"Name": "Water"}],
            }
        }
    }
    (tmp_path / "page_os_0000000.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "api_errors.json").write_text("[]", encoding="utf-8")

    extraction = load_worldbank_pages(tmp_path)
    row = extraction.projects.iloc[0]

    assert row["id"] == "P1"
    assert row["sector1.Name"] == "Water Supply"
    assert json.loads(row["themes"])[0]["Name"] == "Institutions"
    assert row["boardapprovaldate"] == "2017-05-12T00:00:00Z"
    assert row["closingdate"] == "2021-12-31T00:00:00Z"
    assert row["totalcommamt"] == "USD malformed 12x"
    assert json.loads(row["fcv_source_record_json"])["totalcommamt"] == "USD malformed 12x"


def test_worldbank_qa_exposes_duplicate_and_missing_source_ids_and_parse_errors(tmp_path: Path) -> None:
    payload = {
        "projects": {
            "row1": {"id": "P1", "project_name": "First"},
            "row2": {"id": "P1", "project_name": "Duplicate"},
            "row3": {"project_name": "Missing ID"},
        }
    }
    (tmp_path / "page_os_0000000.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "page_os_0000100.json").write_text("{malformed", encoding="utf-8")
    (tmp_path / "api_errors.json").write_text(
        json.dumps([{"os": 300, "error": "synthetic acquisition failure"}]),
        encoding="utf-8",
    )

    extraction = load_worldbank_pages(tmp_path)

    source_id = _qa(extraction.qa, "worldbank.projects.source_id")
    assert source_id.state == "RED"
    assert source_id.metrics["missing_source_id"] == 1
    assert source_id.metrics["duplicate_source_id_rows"] == 2

    parse_errors = _qa(extraction.qa, "worldbank.pages.parse_errors")
    assert parse_errors.state == "RED"
    assert parse_errors.metrics["page_parse_errors"] == 1

    acquisition = _qa(extraction.qa, "worldbank.acquisition.errors")
    assert acquisition.state == "YELLOW"
    assert acquisition.metrics["acquisition_error_count"] == 1


def test_empty_worldbank_page_is_not_green_source_identity(tmp_path: Path) -> None:
    (tmp_path / "page_os_0000000.json").write_text(
        json.dumps({"projects": {}}), encoding="utf-8"
    )

    extraction = load_worldbank_pages(tmp_path)

    record_count = _qa(extraction.qa, "worldbank.records.raw_count")
    source_id = _qa(extraction.qa, "worldbank.projects.source_id")
    assert record_count.state == "RED"
    assert source_id.state == "RED"
    assert source_id.metrics["source_id_field_present"] is False
