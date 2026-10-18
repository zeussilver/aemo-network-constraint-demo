#!/usr/bin/env python
"""Write data/site_registry.csv for the ops console from AEMO standing data.

Example:

    python scripts/build_site_registry.py \
      --registration-xlsx data/cache/NEM_Registration_and_Exemption_List_20261005.xlsx \
      --dudetailsummary-zip "data/cache/PUBLIC_ARCHIVE#DUDETAILSUMMARY#FILE01#202609010000.zip" \
      --dudetail-zip "data/cache/PUBLIC_ARCHIVE#DUDETAIL#FILE01#202609010000.zip"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.aemo_constraint_demo.site_registry import build_site_registry_from_files, read_duid_list


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the ops console site registry CSV.")
    parser.add_argument("--duids-file", default="data/console_duids.txt")
    parser.add_argument("--registration-xlsx", required=True, help="AEMO NEM Registration and Exemption List.")
    parser.add_argument("--dudetailsummary-zip", required=True, help="MMSDM DUDETAILSUMMARY monthly archive.")
    parser.add_argument("--dudetail-zip", required=True, help="MMSDM DUDETAIL monthly archive.")
    parser.add_argument("--out", default="data/site_registry.csv")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    registry = build_site_registry_from_files(
        read_duid_list(args.duids_file),
        registration_xlsx=args.registration_xlsx,
        dudetailsummary_zip=args.dudetailsummary_zip,
        dudetail_zip=args.dudetail_zip,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    registry.to_csv(out, index=False)
    print(f"Wrote {len(registry)} sites to {out}")
    print(registry[["duid", "station_name", "region_id", "fuel", "registered_capacity_mw"]].to_string(index=False))


if __name__ == "__main__":
    main()
