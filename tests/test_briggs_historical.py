from __future__ import annotations

import json
import zipfile

import pytest

from fcv_empirical.investments.briggs_historical import (
    AFDB_ARCHIVE,
    WORLD_BANK_ARCHIVE,
    BriggsHistoricalSourceError,
    preflight_briggs_historical_aid_sources,
    write_briggs_historical_aid_preflight,
)


def _write_zip(path, member, payload=b"fixture"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(member, payload)


def test_preflight_requires_both_exact_historical_archives(tmp_path):
    _write_zip(tmp_path / WORLD_BANK_ARCHIVE, "worldbank.csv")
    with pytest.raises(BriggsHistoricalSourceError, match="missing Briggs historical afdb"):
        preflight_briggs_historical_aid_sources(tmp_path)


def test_preflight_fingerprints_exact_archives_without_substitution(tmp_path):
    _write_zip(tmp_path / "nested" / WORLD_BANK_ARCHIVE, "AllWorldBank_IBRDIDA.csv", b"wb")
    _write_zip(tmp_path / AFDB_ARCHIVE, "AfDB_2009_2010.xlsx", b"afdb")

    result = preflight_briggs_historical_aid_sources(tmp_path)

    assert result.world_bank.expected_file_name == WORLD_BANK_ARCHIVE
    assert result.afdb.expected_file_name == AFDB_ARCHIVE
    assert result.world_bank.sha256 != result.afdb.sha256
    assert result.world_bank.archive_members == ("AllWorldBank_IBRDIDA.csv",)
    assert result.afdb.archive_members == ("AfDB_2009_2010.xlsx",)
    assert "no current-API" in result.source_policy
    assert "China-finance" in result.source_policy

    out = write_briggs_historical_aid_preflight(result, tmp_path / "preflight.json")
    payload = json.loads(out.read_text())
    assert payload["schema"] == "briggs_historical_aid_preflight.v1"
    assert payload["world_bank"]["sha256"] == result.world_bank.sha256


def test_preflight_fails_on_ambiguous_duplicate_archive(tmp_path):
    _write_zip(tmp_path / "a" / WORLD_BANK_ARCHIVE, "wb.csv")
    _write_zip(tmp_path / "b" / WORLD_BANK_ARCHIVE, "wb.csv")
    _write_zip(tmp_path / AFDB_ARCHIVE, "afdb.xlsx")

    with pytest.raises(BriggsHistoricalSourceError, match="ambiguous Briggs historical world_bank"):
        preflight_briggs_historical_aid_sources(tmp_path)
