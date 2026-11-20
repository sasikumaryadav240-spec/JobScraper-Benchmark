"""
Configuration for Indeed Standalone Cybersecurity Job Collector.
"""

from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "jobspy"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 16 Target Cybersecurity Roles
INDEED_ROLES = [
    "SOC Analyst",
    "SOC Analyst L1",
    "Cybersecurity Analyst",
    "Information Security Analyst",
    "Information Security Associate",
    "Junior Security Engineer",
    "Security Operations Analyst",
    "SIEM Analyst",
    "Incident Response Analyst",
    "Vulnerability Management Analyst",
    "Cloud Security Analyst",
    "Junior Cloud Security Engineer",
    "Application Security Analyst",
    "IT Security Analyst",
    "Security Monitoring Analyst",
    "MDR Analyst",
]

# Configured Indian Cities
INDEED_LOCATIONS = [
    "Bengaluru",
    "Hyderabad",
    "Chennai",
    "Pune",
    "Mumbai",
    "Gurugram",
    "Noida",
    "Kochi",
    "Coimbatore",
]

# Remote Search Configuration
INDEED_REMOTE_LOCATION = "India"
INDEED_COUNTRY = "India"

# Search Parameters
RESULTS_PER_SEARCH = 25
POSTED_WITHIN_HOURS = 72
FETCH_DESCRIPTIONS = True

# Reliability & Rate Limiting Settings
DEFAULT_REQUEST_INTERVAL_SECONDS = 10.0
DELAY_BETWEEN_SEARCHES_SECONDS: Tuple[float, float] = (10.0, 14.0)
MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 2.0
TIMEOUT_SECONDS = 30

# File Outputs
JSON_OUTPUT = OUTPUT_DIR / "indeed_jobs.json"
CSV_OUTPUT = OUTPUT_DIR / "indeed_jobs.csv"
CHECKPOINT_OUTPUT = OUTPUT_DIR / "indeed_checkpoint.jsonl"
ERROR_OUTPUT = OUTPUT_DIR / "indeed_errors.json"
QUALITY_OUTPUT = OUTPUT_DIR / "indeed_quality_report.json"
SUMMARY_OUTPUT = OUTPUT_DIR / "indeed_run_summary.json"


def generate_search_plan(
    roles: List[str] = INDEED_ROLES,
    locations: List[str] = INDEED_LOCATIONS,
    include_remote: bool = True
) -> List[Dict[str, any]]:
    """Generate search combination list for city and remote queries."""
    searches = []
    # City-based searches
    for role in roles:
        for location in locations:
            searches.append({
                "keyword": role,
                "location": location,
                "is_remote": False,
                "search_type": "city",
            })

    # Remote searches
    if include_remote:
        for role in roles:
            searches.append({
                "keyword": role,
                "location": INDEED_REMOTE_LOCATION,
                "is_remote": True,
                "search_type": "remote_india",
            })

    return searches


SEARCHES = generate_search_plan()

