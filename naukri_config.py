"""
Naukri standalone collector configuration.

Supports 16 target cybersecurity roles across 9 target Indian cities
and India-wide remote searches, with adaptive rate limiting and backoff.
"""

from pathlib import Path

# Base paths
ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "jobspy"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

NAUKRI_JOBS_JSON = OUTPUT_DIR / "naukri_jobs.json"
NAUKRI_JOBS_CSV = OUTPUT_DIR / "naukri_jobs.csv"
NAUKRI_CHECKPOINT = OUTPUT_DIR / "naukri_checkpoint.jsonl"
NAUKRI_ERRORS_JSON = OUTPUT_DIR / "naukri_errors.json"
NAUKRI_QUALITY_REPORT = OUTPUT_DIR / "naukri_quality_report.json"
NAUKRI_RUN_SUMMARY = OUTPUT_DIR / "naukri_run_summary.json"

# Target roles (16)
TARGET_ROLES = [
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

# Target cities (9)
TARGET_CITIES = [
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

REMOTE_LOCATION = "India"

# Operational collection parameters
RESULTS_PER_SEARCH = 25
POSTED_WITHIN_HOURS = 72
FETCH_DESCRIPTIONS = False  # Avoids triggering Akamai bot reCAPTCHA on v4/job
DELAY_BETWEEN_SEARCHES_SECONDS = (5.0, 8.5)

# Rate limiting and retry configuration
MAX_RETRIES = 2
INITIAL_BACKOFF_SECONDS = 10.0
MAX_BACKOFF_SECONDS = 30.0

def build_search_plan(
    roles: list[str] | None = None,
    cities: list[str] | None = None,
    include_remote: bool = True,
) -> list[dict]:
    """
    Build the full list of search queries for Naukri.
    Total planned searches = (roles * cities) + (roles * 1 remote if include_remote).
    For 16 roles and 9 cities: 144 city searches + 16 remote searches = 160 searches.
    """
    selected_roles = roles or TARGET_ROLES
    selected_cities = cities or TARGET_CITIES

    plan: list[dict] = []

    # City-specific searches
    for role in selected_roles:
        for city in selected_cities:
            plan.append({
                "role": role,
                "location": city,
                "is_remote": False,
                "search_type": "city",
            })

    # Remote searches
    if include_remote:
        for role in selected_roles:
            plan.append({
                "role": role,
                "location": REMOTE_LOCATION,
                "is_remote": True,
                "search_type": "remote_india",
            })

    return plan

