from pathlib import Path

import pandas as pd

from fcv_empirical.investments.aiddata_clg import build_aiddata_qa, extract_aiddata_workbook
from fcv_empirical.investments.annotation_candidates import build_aiddata_annotation_candidates


def _qa(results, check_id):
    return next(result for result in results if result.check_id == check_id)


def test_aiddata_qa_exposes_duplicates_missing_ids_orphans_and_child_multiplicity() -> None:
    records = pd.DataFrame(
        {
            "aiddata_record_id": ["p1", "p1", None, "p2"],
            "title": ["A", "A duplicate", "missing", "B"],
        }
    )
    ownership = pd.DataFrame(
        {
            "aiddata_record_id": ["p1", "p1", "orphan"],
            "borrower_type": ["public", "private", "public"],
        }
    )

    results, _coverage = build_aiddata_qa(
        {"records": records, "borrower_ownership": ownership}
    )

    source_id = _qa(results, "aiddata.records.source_id")
    assert source_id.state == "RED"
    assert source_id.metrics["missing_source_id"] == 1
    assert source_id.metrics["duplicate_source_id_rows"] == 2

    relationship = _qa(results, "aiddata.borrower_ownership.referential_integrity")
    assert relationship.state == "RED"
    assert relationship.metrics["orphan_child_rows"] == 1

    multiplicity = _qa(results, "aiddata.borrower_ownership.multiplicity")
    assert multiplicity.state == "GREEN"
    assert multiplicity.metrics["parents_with_multiple_child_rows"] == 1


def test_workbook_extraction_keeps_relational_tables_and_named_source_columns(tmp_path: Path) -> None:
    workbook = tmp_path / "clg.xlsx"
    records = pd.DataFrame(
        {
            "AidData Record ID": ["p1", "p2"],
            "Parent ID": [None, "p1"],
            "Title": ["Project one", "Project two"],
            "Amount Constant USD 2023": ["malformed amount", "1200"],
            "Never Filled": [None, None],
        }
    )
    ownership = pd.DataFrame(
        {
            "AidData Record ID": ["p1", "p1", "p2"],
            "Borrower Type": ["central", "local", "central"],
        }
    )
    country = pd.DataFrame({"Country": ["Example"], "ISO3": ["EXP"]})
    definitions_records = pd.DataFrame({"Variable": ["AidData Record ID"], "Definition": ["ID"]})
    definitions_ownership = pd.DataFrame(
        {"Variable": ["Borrower Type"], "Definition": ["Borrower classification"]}
    )

    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        records.to_excel(writer, sheet_name="CLG-LMIC 1.0_Records", index=False)
        ownership.to_excel(writer, sheet_name="Borrower Ownership", index=False)
        country.to_excel(writer, sheet_name="CountryList", index=False, startrow=4)
        definitions_records.to_excel(writer, sheet_name="Definitions_Records", index=False, startrow=3)
        definitions_ownership.to_excel(
            writer,
            sheet_name="Definitions_Borrower Ownership",
            index=False,
            startrow=3,
        )

    extraction = extract_aiddata_workbook(workbook)

    assert set(extraction.tables) == {
        "records",
        "borrower_ownership",
        "country_list",
        "definitions_records",
        "definitions_borrower_ownership",
    }
    assert extraction.tables["borrower_ownership"]["aiddata_record_id"].tolist() == [
        "p1",
        "p1",
        "p2",
    ]
    assert "never_filled" in extraction.tables["records"].columns
    assert extraction.tables["records"].loc[0, "amount_constant_usd_2023"] == "malformed amount"
    mapping = extraction.column_mapping
    row = mapping[
        (mapping["source_sheet"] == "CLG-LMIC 1.0_Records")
        & (mapping["original_column"] == "AidData Record ID")
    ].iloc[0]
    assert row["normalized_column"] == "aiddata_record_id"


def test_aiddata_annotation_keeps_raw_amount_without_allocation_or_zero_coercion() -> None:
    records = pd.DataFrame(
        {
            "aiddata_record_id": ["p1"],
            "title": ["Project"],
            "amount_constant_usd_2023": ["not-a-number"],
            "implementation_start_year": ["2018"],
        }
    )

    candidates = build_aiddata_annotation_candidates(records)

    assert len(candidates) == 1
    assert candidates.loc[0, "source_amount_raw"] == "not-a-number"
    assert candidates.loc[0, "source_amount_field"] == "amount_constant_usd_2023"
    assert candidates.loc[0, "implementation_start_year"] == "2018"
    assert not any("location_amount" in column for column in candidates.columns)
