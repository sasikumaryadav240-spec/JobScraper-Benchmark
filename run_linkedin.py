"""
LinkedIn Standalone Collector CLI Runner.

Usage:
  # Validate configuration and print search plan without scraping
  python run_linkedin.py --dry-run

  # Run a minimal controlled sample test (2 searches, 5 jobs each)
  python run_linkedin.py --sample-test

  # Run a representative test (6 searches across roles, cities, and remote)
  python run_linkedin.py --representative-test

  # Run the full configured search plan
  python run_linkedin.py --full-run
"""

import argparse
import json
import sys
from pathlib import Path

from linkedin_collector import LinkedInCollector
from linkedin_config import (
    LINKEDIN_CHECKPOINT,
    LINKEDIN_ERRORS_JSON,
    LINKEDIN_JOBS_CSV,
    LINKEDIN_JOBS_JSON,
    LINKEDIN_QUALITY_REPORT,
    LINKEDIN_RUN_SUMMARY,
    build_search_plan,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="LinkedIn Standalone Collector CLI Runner")
    parser.add_argument("--dry-run", action="store_true", help="Print planned searches without scraping")
    parser.add_argument("--sample-test", action="store_true", help="Run 2 searches for smoke verification")
    parser.add_argument("--representative-test", action="store_true", help="Run 6 searches for representative validation")
    parser.add_argument("--full-run", action="store_true", help="Run all 160 configured searches")
    parser.add_argument("--clean", action="store_true", help="Clear existing checkpoint before starting")
    parser.add_argument("--limit", type=int, default=None, help="Custom limit on number of searches")
    args = parser.parse_args()

    full_plan = build_search_plan()

    if args.dry_run:
        print("=" * 72)
        print("LINKEDIN STANDALONE COLLECTOR — DRY RUN")
        print("=" * 72)
        print(f"Total planned searches: {len(full_plan)}")
        city_searches = [s for s in full_plan if s["search_type"] == "city"]
        remote_searches = [s for s in full_plan if s["search_type"] == "remote_india"]
        print(f"  - City searches: {len(city_searches)}")
        print(f"  - Remote searches: {len(remote_searches)}")
        print("\nFirst 5 planned queries:")
        for i, s in enumerate(full_plan[:5], 1):
            print(f"  {i}. Role: {s['role']} | Location: {s['location']} | Remote: {s['is_remote']}")
        print("=" * 72)
        return

    if args.clean:
        print("Clean requested. Resetting checkpoint and output files...")
        for p in [
            LINKEDIN_CHECKPOINT,
            LINKEDIN_ERRORS_JSON,
            LINKEDIN_JOBS_CSV,
            LINKEDIN_JOBS_JSON,
            LINKEDIN_QUALITY_REPORT,
            LINKEDIN_RUN_SUMMARY,
        ]:
            if p.exists():
                p.unlink()

    collector = LinkedInCollector()

    if args.sample_test:
        print("Running LinkedIn sample test (2 searches: 1 city, 1 remote)...")
        plan = [
            {"role": "SOC Analyst", "location": "Bengaluru", "is_remote": False, "search_type": "city"},
            {"role": "Cybersecurity Analyst", "location": "India", "is_remote": True, "search_type": "remote_india"},
        ]
        collector.results_per_search = 3
        collector.delays = (4.0, 6.0)
        collector.detail_delays = (1.0, 1.8)
        collector.run(plan)
    elif args.representative_test:
        print("Running LinkedIn representative test (6 searches)...")
        plan = [
            {"role": "SOC Analyst", "location": "Bengaluru", "is_remote": False, "search_type": "city"},
            {"role": "Cybersecurity Analyst", "location": "Hyderabad", "is_remote": False, "search_type": "city"},
            {"role": "SIEM Analyst", "location": "Pune", "is_remote": False, "search_type": "city"},
            {"role": "Cloud Security Analyst", "location": "Chennai", "is_remote": False, "search_type": "city"},
            {"role": "SOC Analyst", "location": "India", "is_remote": True, "search_type": "remote_india"},
            {"role": "Cybersecurity Analyst", "location": "India", "is_remote": True, "search_type": "remote_india"},
        ]
        collector.results_per_search = 10
        collector.delays = (6.0, 10.0)
        collector.detail_delays = (1.2, 2.0)
        collector.run(plan)
    elif args.full_run:
        print(f"Running LinkedIn full run ({len(full_plan)} searches)...")
        collector.run(full_plan, limit=args.limit)
    else:
        print("No execution mode specified. Please specify --dry-run, --sample-test, --representative-test, or --full-run.")
        sys.exit(1)

    print("\nCollection finished. Quality report:")
    if LINKEDIN_QUALITY_REPORT.exists():
        with open(LINKEDIN_QUALITY_REPORT, "r", encoding="utf-8") as f:
            print(f.read())


if __name__ == "__main__":
    main()

