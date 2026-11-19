"""
CLI Runner for Phase 5 Unified Processing Pipeline.

Usage:
    python run_unified.py            # Run full unified pipeline
    python run_unified.py --dry-run  # Inspect source datasets and print record counts
    python run_unified.py --clean    # Clean old outputs and rerun
"""

import argparse
import logging
import sys
from pathlib import Path

from unified_pipeline import UnifiedPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("RunUnified")


def parse_args():
    parser = argparse.ArgumentParser(description="Phase 5 Unified Pipeline Runner")
    parser.add_argument("--dry-run", action="store_true", help="Inspect source files and print summary")
    parser.add_argument("--clean", action="store_true", help="Remove existing unified outputs before running")
    parser.add_argument("--dir", default="jobspy", help="Base directory containing source datasets")
    return parser.parse_args()


def main():
    args = parse_args()
    base_dir = Path(args.dir)

    source_files = {
        "Indeed": base_dir / "indeed_jobs.json",
        "Naukri": base_dir / "naukri_jobs.json",
        "LinkedIn": base_dir / "linkedin_jobs.json",
        "ATS (Greenhouse/Lever/Ashby)": base_dir / "ats_jobs.json",
    }

    if args.dry_run:
        print("=" * 60)
        print("PHASE 5 UNIFIED PIPELINE — DRY RUN INSPECTION")
        print("=" * 60)
        total_found = 0
        for name, path in source_files.items():
            if path.exists():
                try:
                    import json
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    count = len(data) if isinstance(data, list) else 1
                    print(f"  - {name:<30}: {count:>4} records ({path})")
                    total_found += count
                except Exception as e:
                    print(f"  - {name:<30}: ERROR reading ({e})")
            else:
                print(f"  - {name:<30}: NOT FOUND ({path})")
        print("-" * 60)
        print(f"Total available source records: {total_found}")
        print("=" * 60)
        return

    if args.clean:
        logger.info("Cleaning previously generated Phase 5 unified output files...")
        target_files = [
            "unified_jobs.json", "unified_jobs.csv", "unified_jobs.xlsx",
            "matched_jobs.json", "matched_jobs.csv", "matched_jobs.xlsx",
            "supporting_it_jobs.json", "manual_verification_jobs.json",
            "internships.json", "excluded_jobs.json",
            "duplicate_audit.json", "matching_report.json", "unified_quality_report.json",
        ]
        for tf in target_files:
            p = base_dir / tf
            if p.exists():
                p.unlink()
                logger.info(f"Removed {p}")

    pipeline = UnifiedPipeline(base_dir=base_dir)
    res = pipeline.run()
    print("\n" + "=" * 60)
    print("PHASE 5 EXECUTION SUMMARY")
    print("=" * 60)
    for k, v in res.items():
        print(f"  {k:<28}: {v}")
    print("=" * 60)


if __name__ == "__main__":
    main()

