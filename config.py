"""
India-focused JobSpy configuration.

Primary sources: LinkedIn, Indeed, Glassdoor, Naukri.
Bayt is intentionally excluded because the previous run showed location
fallbacks to other countries for India city searches.

Remote searches exclude LinkedIn because the installed JobSpy version warned
that LinkedIn does not support is_remote and ignored the filter.
"""

# Keep the list explicit. Bayt is excluded from the India-focused benchmark.
SITES = ["linkedin", "indeed", "naukri", "glassdoor"]

# Sources used for city-based searches.
CITY_SITES = SITES.copy()

# LinkedIn is deliberately excluded from remote-only searches because JobSpy
# reports that LinkedIn ignores the is_remote filter.
REMOTE_SITES = ["indeed", "naukri", "glassdoor"]

ROLES = [
    "SOC Analyst",
    "Cybersecurity Analyst",
    "Information Security Analyst",
    "SIEM Analyst",
    "Junior Security Engineer",
    "Cloud Security Engineer",
    "Application Security Analyst",
    "Vulnerability Management Analyst",
    "IAM Analyst",
    "GRC Analyst",
    "IT Support Specialist",
    "Junior System Administrator",
    "Network Support Associate",
]

LOCATIONS = [
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
REMOTE_ROLES = [
    "SOC Analyst",
    "Cybersecurity Analyst",
    "Information Security Analyst",
    "SIEM Analyst",
    "Cloud Security Engineer",
    "Application Security Analyst",
    "IAM Analyst",
    "GRC Analyst",
]

SEARCHES = [
    {
        "keyword": role,
        "location": location,
        "is_remote": False,
        "search_type": "city",
    }
    for role in ROLES
    for location in LOCATIONS
]

SEARCHES.extend(
    {
        "keyword": role,
        "location": REMOTE_LOCATION,
        "is_remote": True,
        "search_type": "remote_india",
    }
    for role in REMOTE_ROLES
)

# Start conservatively with 50 results per search for sustainable runs.
RESULTS_PER_SEARCH = 50
POSTED_WITHIN_HOURS = 24
COUNTRY_INDEED = "India"

# Keep False for broad collection. Enrich descriptions only after filtering
# candidates in a separate, targeted stage to control runtime and requests.
FETCH_DESCRIPTIONS = False

# A pause helps avoid bursts; it does not bypass or guarantee against blocks.
DELAY_BETWEEN_SEARCHES_SECONDS = (8, 14)

# LinkedIn is stopped for the remainder of a run after an explicit 429/block.
STOP_LINKEDIN_AFTER_RATE_LIMIT = True

VERBOSE = 1
