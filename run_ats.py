"""
ATS Standalone Collector CLI Runner.

Usage:
  # Print registered employer boards without making network calls
  python run_ats.py --dry-run

  # Run smoke verification on 1 verified board per platform
  python run_ats.py --sample-test

  # Run all verified employer boards across Greenhouse, Lever, and Ashby
  python run_ats.py --full-run

  # Run only a specific platform
  python run_ats.py --platform greenhouse
"""

import argparse
import json
import sys
from pathlib import Path

from ats_collector import ATSCollector
from ats_config import (
    ASHBY_JOBS_JSON,
    ATS_CHECKPOINT,
    ATS_ERRORS_JSON,
    ATS_JOBS_CSV,
    ATS_JOBS_JSON,
    ATS_QUALITY_REPORT,
    ATS_RUN_SUMMARY,
    GREENHOUSE_JOBS_JSON,
    LEVER_JOBS_JSON,
)
from ats_registry import EmployerBoard, get_verified_registry


def main() -> None:
    parser = argparse.ArgumentParser(description="ATS Standalone Collector CLI Runner")
    parser.add_argument("--dry-run", action="store_true", help="Print verified boards without network calls")
    parser.add_argument("--sample-test", action="store_true", help="Run 1 board per platform for smoke testing")
    parser.add_argument("--full-run", action="store_true", help="Run all verified boards across all platforms")
    parser.add_argument("--platform", type=str, choices=["greenhouse", "lever", "ashby"], help="Filter by ATS platform")
    parser.add_argument("--clean", action="store_true", help="Clear existing checkpoint before starting")
    args = parser.parse_args()

    registry = get_verified_registry(platform=args.platform, active_only=True)

    if args.dry_run:
        print("=" * 72)
        print("OFFICIAL ATS COLLECTOR — DRY RUN")
        print("=" * 72)
        print(f"Total verified employer boards configured: {len(registry)}")
        by_platform: dict[str, list[EmployerBoard]] = {}
        for b in registry:
            by_platform.setdefault(b.ats_platform, []).append(b)

        for plat, boards in by_platform.items():
            print(f"\n[{plat.upper()}] ({len(boards)} boards):")
            for b in boards:
                print(f"  - {b.company_name:15} | ID: {b.board_identifier:15} | URL: {b.board_url}")
        print("=" * 72)
        return

    if args.clean:
        print("Clean requested. Resetting checkpoint and output files...")
        for p in [
            ATS_CHECKPOINT,
            ATS_ERRORS_JSON,
            ATS_JOBS_CSV,
            ATS_JOBS_JSON,
            GREENHOUSE_JOBS_JSON,
            LEVER_JOBS_JSON,
            ASHBY_JOBS_JSON,
            ATS_QUALITY_REPORT,
            ATS_RUN_SUMMARY,
        ]:
            if p.exists():
                p.unlink()

    collector = ATSCollector()

    if args.sample_test:
        print("Running ATS sample test (1 board per platform)...")
        sample_boards = [
            # Greenhouse
            EmployerBoard(
                company_name="Netskope",
                ats_platform="greenhouse",
                board_identifier="netskope",
                board_url="https://boards-api.greenhouse.io/v1/boards/netskope/jobs?content=true",
                country_or_market="India & Global",
                verification_timestamp="2026-11-19T03:39:00Z",
                discovery_source="verified_public_job_board",
            ),
            # Lever
            EmployerBoard(
                company_name="Meesho",
                ats_platform="lever",
                board_identifier="meesho",
                board_url="https://api.lever.co/v0/postings/meesho?mode=json",
                country_or_market="India",
                verification_timestamp="2026-11-19T03:39:00Z",
                discovery_source="verified_public_postings_api",
            ),
            # Ashby
            EmployerBoard(
                company_name="Supabase",
                ats_platform="ashby",
                board_identifier="supabase",
                board_url="https://api.ashbyhq.com/posting-api/job-board/supabase",
                country_or_market="Global Remote",
                verification_timestamp="2026-11-19T03:39:00Z",
                discovery_source="verified_public_posting_api",
            ),
        ]
        collector.run(sample_boards)
    elif args.full_run or args.platform:
        print(f"Running ATS collection across {len(registry)} boards...")
        collector.run(registry)
    else:
        print("No execution mode specified. Please specify --dry-run, --sample-test, or --full-run.")
        sys.exit(1)

    print("\nCollection finished. Quality report:")
    if ATS_QUALITY_REPORT.exists():
        with open(ATS_QUALITY_REPORT, "r", encoding="utf-8") as f:
            print(f.read())


if __name__ == "__main__":
    main()
