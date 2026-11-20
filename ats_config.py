"""
ATS Standalone Collector Configuration.

Supports Greenhouse, Lever, and Ashby public job board integrations.
"""

from pathlib import Path

# Base paths
ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "jobspy"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

GREENHOUSE_JOBS_JSON = OUTPUT_DIR / "greenhouse_jobs.json"
LEVER_JOBS_JSON = OUTPUT_DIR / "lever_jobs.json"
ASHBY_JOBS_JSON = OUTPUT_DIR / "ashby_jobs.json"
ATS_JOBS_JSON = OUTPUT_DIR / "ats_jobs.json"
ATS_JOBS_CSV = OUTPUT_DIR / "ats_jobs.csv"
ATS_CHECKPOINT = OUTPUT_DIR / "ats_checkpoint.jsonl"
ATS_ERRORS_JSON = OUTPUT_DIR / "ats_errors.json"
ATS_QUALITY_REPORT = OUTPUT_DIR / "ats_quality_report.json"
ATS_RUN_SUMMARY = OUTPUT_DIR / "ats_run_summary.json"

# Operational request settings
HTTP_TIMEOUT_SECONDS = 15.0
DEFAULT_REQUEST_INTERVAL_SECONDS = 10.0
DELAY_BETWEEN_BOARDS_SECONDS = (10.0, 12.0)
MAX_RETRIES = 2
INITIAL_BACKOFF_SECONDS = 10.0
MAX_BACKOFF_SECONDS = 30.0

DEFAULT_USER_AGENT = "JobScraper-Benchmark/1.0 (+https://github.com/sasik/JobScraper-Benchmark)"

# Target canonical cybersecurity roles (16)
CANONICAL_CYBERSECURITY_ROLES = [
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

# Additional verified cybersecurity title keywords
EXTENDED_CYBERSECURITY_KEYWORDS = [
    "security engineer",
    "information security",
    "infosec",
    "appsec",
    "application security",
    "cloud security",
    "product security",
    "threat intelligence",
    "threat intel",
    "detection and response",
    "detection & response",
    "incident response",
    "security operations",
    "vulnerability management",
    "security architect",
    "cyber security",
    "cybersecurity",
    "penetration tester",
    "red team",
    "blue team",
    "security compliance",
    "grc analyst",
]

# Negative title exclusions to avoid capturing non-cybersecurity roles
NEGATIVE_ROLE_EXCLUSIONS = [
    "financial analyst",
    "equity analyst",
    "investment analyst",
    "business analyst",
    "sales analyst",
    "marketing analyst",
    "security guard",
    "physical security",
    "facilities security",
    "campus security",
    "loss prevention",
    "social security",
    "national security",
    "securities",
]

# Target Indian locations
TARGET_INDIAN_LOCATIONS = [
    "bengaluru",
    "bangalore",
    "hyderabad",
    "pune",
    "chennai",
    "mumbai",
    "gurugram",
    "gurgaon",
    "noida",
    "delhi",
    "new delhi",
    "kochi",
    "coimbatore",
    "india",
]

