#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from fcv_empirical.investments.briggs_historical import (
    BriggsHistoricalSourceError,
    preflight_briggs_historical_aid_sources,
    write_briggs_historical_aid_preflight,
)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Resolve, fingerprint and inventory the exact historical AidData archives required by Briggs Lane B."
    )
    p.add_argument("--source-root", required=True, help="Directory tree containing the historical AidData archives")
    p.add_argument("--out", required=True, help="JSON output path for the non-sensitive preflight report")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = preflight_briggs_historical_aid_sources(Path(args.source_root))
    except BriggsHistoricalSourceError as exc:
        print(f"BRIGGS_SOURCE_PREFLIGHT=BLOCKED reason={exc}", file=sys.stderr)
        return 2

    output = write_briggs_historical_aid_preflight(result, args.out)
    print("BRIGGS_SOURCE_PREFLIGHT=PASS")
    print(f"world_bank_sha256={result.world_bank.sha256}")
    print(f"afdb_sha256={result.afdb.sha256}")
    print(f"report={output}")
    for donor, archive in (("world_bank", result.world_bank), ("afdb", result.afdb)):
        print(f"{donor}_archive={archive.path}")
        for member in archive.archive_members:
            print(f"{donor}_member={member}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
