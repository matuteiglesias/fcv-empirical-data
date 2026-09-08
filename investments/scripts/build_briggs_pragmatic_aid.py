#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import json

from fcv_empirical.investments.briggs_pragmatic_aid import build_briggs_pragmatic_aid_locations


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build the explicit pragmatic Briggs WB/AfDB geocoded-location analogue before GADM assignment.")
    p.add_argument("--source-root", required=True)
    p.add_argument("--out-dir", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    locations, summary = build_briggs_pragmatic_aid_locations(args.source_root)
    locations_path = out / "eligible_aid_parent_child_pairs.parquet"
    summary_path = out / "aid_semantics_summary.json"
    locations.to_parquet(locations_path, index=False)
    summary_path.write_text(json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"BRIGGS_PRAGMATIC_AID=BUILT rows={len(locations)} out={locations_path}")
    print(f"summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
