from __future__ import annotations

import json
import zipfile
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path


WORLD_BANK_ARCHIVE = "AllWorldBank_IBRDIDA.csv.zip"
AFDB_ARCHIVE = "AfDB_2009_2010_AllApprovedProjects.xlsx.zip"


class BriggsHistoricalSourceError(ValueError):
    """Raised when the historical Briggs source authority is missing or ambiguous."""


@dataclass(frozen=True)
class HistoricalAidArchive:
    donor_id: str
    expected_file_name: str
    path: str
    sha256: str
    size_bytes: int
    archive_members: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BriggsHistoricalAidPreflight:
    schema: str
    world_bank: HistoricalAidArchive
    afdb: HistoricalAidArchive
    source_policy: str

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "world_bank": self.world_bank.to_dict(),
            "afdb": self.afdb.to_dict(),
            "source_policy": self.source_policy,
        }


def _sha256_file(path: Path, chunk_bytes: int = 1024 * 1024) -> str:
    h = sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_bytes)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _archive_members(path: Path) -> tuple[str, ...]:
    if not zipfile.is_zipfile(path):
        raise BriggsHistoricalSourceError(f"expected ZIP archive: {path}")
    with zipfile.ZipFile(path) as zf:
        members = tuple(sorted(x.filename for x in zf.infolist() if not x.is_dir()))
    if not members:
        raise BriggsHistoricalSourceError(f"historical archive contains no files: {path}")
    return members


def _resolve_exact(root: Path, expected_file_name: str, donor_id: str) -> HistoricalAidArchive:
    matches = [p for p in root.rglob(expected_file_name) if p.is_file()]
    if not matches:
        raise BriggsHistoricalSourceError(
            f"missing Briggs historical {donor_id} archive {expected_file_name!r} under {root}"
        )
    if len(matches) > 1:
        rendered = ", ".join(str(p) for p in sorted(matches))
        raise BriggsHistoricalSourceError(
            f"ambiguous Briggs historical {donor_id} archive {expected_file_name!r}: {rendered}"
        )
    path = matches[0]
    return HistoricalAidArchive(
        donor_id=donor_id,
        expected_file_name=expected_file_name,
        path=str(path.resolve()),
        sha256=_sha256_file(path),
        size_bytes=path.stat().st_size,
        archive_members=_archive_members(path),
    )


def preflight_briggs_historical_aid_sources(source_root: str | Path) -> BriggsHistoricalAidPreflight:
    """Resolve and fingerprint the two historical AidData archives required by Lane B.

    This function deliberately does not guess alternate file names, use current donor APIs,
    or substitute another donor family. It is a source-authority preflight only; extraction
    and semantic mapping follow after the operator has inspected the exact archive members.
    """

    root = Path(source_root)
    if not root.exists() or not root.is_dir():
        raise BriggsHistoricalSourceError(f"source_root is not a directory: {root}")

    return BriggsHistoricalAidPreflight(
        schema="briggs_historical_aid_preflight.v1",
        world_bank=_resolve_exact(root, WORLD_BANK_ARCHIVE, "world_bank"),
        afdb=_resolve_exact(root, AFDB_ARCHIVE, "afdb"),
        source_policy=(
            "exact_historical_archives_only; no current-API, China-finance, or donor-family substitution"
        ),
    )


def write_briggs_historical_aid_preflight(
    result: BriggsHistoricalAidPreflight, output_path: str | Path
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"
    output.write_text(payload, encoding="utf-8")
    return output
