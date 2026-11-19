"""
Command-line interface for the standalone Naukri cybersecurity job collector.

Usage:
    python run_naukri.py --dry-run
    python run_naukri.py --sample-test
    python run_naukri.py --representative-test
    python run_naukri.py --full-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from naukri_collector import NaukriCollector
from naukri_config import (
    POSTED_WITHIN_HOURS,
    RESULTS_PER_SEARCH,
    TARGET_CITIES,
    TARGET_ROLES,
    build_search_plan,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone Naukri Cybersecurity Job Collector")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--dry-run", action="store_true", help="Print search plan without network requests")
    mode_group.add_argument("--sample-test", action="store_true", help="Execute 2 searches as a rapid integration check")
    mode_group.add_argument("--representative-test", action="store_true", help="Execute 12 searches across 4 roles and 3 cities")
    mode_group.add_argument("--full-run", action="store_true", help="Execute the complete 160-search matrix")

    parser.add_argument("--results-per-search", type=int, default=RESULTS_PER_SEARCH, help="Results wanted per query")
    parser.add_argument("--hours-old", type=int, default=POSTED_WITHIN_HOURS, help="Max posting age in hours")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.dry_run:
        plan = build_search_plan()
        print(f"=== NAUKRI COLLECTOR DRY RUN ===")
        print(f"Total target roles: {len(TARGET_ROLES)}")
        print(f"Total target cities: {len(TARGET_CITIES)}")
        print(f"Total planned searches: {len(plan)}")
        print(f"Sample queries (first 5):")
        for q in plan[:5]:
            print(f"  - Role: {q['role']}, Location: {q['location']}, Remote: {q['is_remote']}")
        print(f"Sample queries (last 3):")
        for q in plan[-3:]:
            print(f"  - Role: {q['role']}, Location: {q['location']}, Remote: {q['is_remote']}")
        return 0

    if args.sample_test:
        sample_roles = ["SOC Analyst", "Cybersecurity Analyst"]
        sample_cities = ["Bengaluru"]
        plan = build_search_plan(roles=sample_roles, cities=sample_cities, include_remote=False)
        print(f"=== NAUKRI SAMPLE TEST ({len(plan)} searches) ===")
        collector = NaukriCollector(
            run_id=datetime.now(timezone.utc).strftime("naukri_sample_%Y%m%d_%H%M%S"),
            results_per_search=args.results_per_search,
            hours_old=args.hours_old,
        )
        unique_jobs = collector.run(plan=plan)
        print(f"\nSample test complete. Collected {len(unique_jobs)} unique jobs.")
        return 0

    if args.representative_test:
        rep_roles = ["SOC Analyst", "Cybersecurity Analyst", "SIEM Analyst", "Cloud Security Analyst"]
        rep_cities = ["Bengaluru", "Hyderabad"]
        plan = build_search_plan(roles=rep_roles, cities=rep_cities, include_remote=True)
        # 4 roles * 2 cities = 8 + 4 remote = 12 searches
        print(f"=== NAUKRI REPRESENTATIVE TEST ({len(plan)} searches) ===")
        collector = NaukriCollector(
            run_id=datetime.now(timezone.utc).strftime("naukri_rep_%Y%m%d_%H%M%S"),
            results_per_search=args.results_per_search,
            hours_old=args.hours_old,
        )
        unique_jobs = collector.run(plan=plan)
        print(f"\nRepresentative test complete. Collected {len(unique_jobs)} unique jobs.")
        return 0

    if args.full_run:
        plan = build_search_plan()
        print(f"=== NAUKRI FULL RUN ({len(plan)} searches) ===")
        collector = NaukriCollector(
            run_id=datetime.now(timezone.utc).strftime("naukri_full_%Y%m%d_%H%M%S"),
            results_per_search=args.results_per_search,
            hours_old=args.hours_old,
        )
        unique_jobs = collector.run(plan=plan)
        print(f"\nFull run complete. Collected {len(unique_jobs)} unique jobs.")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
