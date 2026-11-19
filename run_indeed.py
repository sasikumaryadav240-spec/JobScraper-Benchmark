"""
CLI Runner for Indeed Standalone Cybersecurity Job Collector.

Usage:
    # 1. Quick dry-run plan inspection:
    python run_indeed.py --dry-run

    # 2. Small controlled smoke test:
    python run_indeed.py --sample-test

    # 3. Representative test across key roles and locations:
    python run_indeed.py --representative-test

    # 4. Full collection across all 16 roles and 9 cities:
    python run_indeed.py --full-run
"""

import argparse
import sys
from pathlib import Path

import indeed_config as config
from indeed_collector import IndeedCollector


def main():
    parser = argparse.ArgumentParser(description="Indeed Standalone Cybersecurity Job Collector")
    parser.add_argument("--dry-run", action="store_true", help="Display search plan and exit without scraping")
    parser.add_argument("--clean", action="store_true", help="Clear existing checkpoint and run fresh")
    parser.add_argument("--sample-test", action="store_true", help="Run 2 controlled searches (1 city + 1 remote)")
    parser.add_argument("--representative-test", action="store_true", help="Run representative test across key roles & locations")
    parser.add_argument("--full-run", action="store_true", help="Run full search matrix (16 roles x 9 locations + remote)")
    parser.add_argument("--limit-searches", type=int, default=None, help="Limit number of searches to execute")
    parser.add_argument("--results-per-search", type=int, default=config.RESULTS_PER_SEARCH, help="Maximum results per query")
    parser.add_argument("--hours-old", type=int, default=config.POSTED_WITHIN_HOURS, help="Posting window in hours")
    parser.add_argument("--roles", type=str, default=None, help="Comma-separated custom roles")
    parser.add_argument("--locations", type=str, default=None, help="Comma-separated custom locations")

    args = parser.parse_args()

    # Determine searches to execute
    if args.sample_test:
        searches = [
            {"keyword": "SOC Analyst", "location": "Bengaluru", "is_remote": False, "search_type": "city"},
            {"keyword": "Cybersecurity Analyst", "location": "India", "is_remote": True, "search_type": "remote_india"},
        ]
    elif args.representative_test:
        rep_roles = ["SOC Analyst", "Cybersecurity Analyst", "SIEM Analyst", "Cloud Security Analyst"]
        rep_locations = ["Bengaluru", "Hyderabad"]
        searches = config.generate_search_plan(roles=rep_roles, locations=rep_locations, include_remote=True)
    elif args.roles or args.locations:
        roles = [r.strip() for r in args.roles.split(",")] if args.roles else config.INDEED_ROLES
        locations = [l.strip() for l in args.locations.split(",")] if args.locations else config.INDEED_LOCATIONS
        searches = config.generate_search_plan(roles=roles, locations=locations, include_remote=True)
    else:
        searches = config.SEARCHES

    collector = IndeedCollector(
        results_per_search=args.results_per_search,
        posted_within_hours=args.hours_old,
    )

    summary = collector.run(
        searches=searches,
        resume=not args.clean,
        clean=args.clean,
        limit_searches=args.limit_searches,
        dry_run=args.dry_run,
    )

    return 0 if summary else 1


if __name__ == "__main__":
    sys.exit(main())

