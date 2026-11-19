
import json
import re
import hashlib
import html
import mimetypes

# Guard against PermissionError when reading Windows registry in restricted environments
try:
    mimetypes.init()
except PermissionError:
    mimetypes._winreg = None
    mimetypes.init = lambda *args, **kwargs: None

import pandas as pd

from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urlencode
from rapidfuzz import fuzz


# =====================================================
# 1. PATH CONFIGURATION
# =====================================================

ROOT = Path(__file__).resolve().parent

APIFY_DIR = ROOT / "apify"
JOBSPY_DIR = ROOT / "jobspy"
JOBSPY_FILE = JOBSPY_DIR / "jobspy_all_jobs.json"

REPORT_DIR = ROOT / "reports"
REPORT_DIR.mkdir(exist_ok=True)

OUTPUT_FILE = REPORT_DIR / "deduplicated_comparison.xlsx"


# =====================================================
# 2. FIELD ALIASES
# =====================================================

ALIASES = {
    "title": [
        "title", "jobTitle", "job_title", "position"
    ],

    "company": [
        "company", "companyName", "company_name",
        "employer", "organization"
    ],

    "location": [
        "location", "jobLocation", "job_location",
        "city"
    ],

    "description": [
        "description", "jobDescription",
        "job_description", "descriptionText",
        "snippet"
    ],

    "url": [
        "job_url", "jobUrl", "applyUrl",
        "applicationUrl", "link", "url"
    ],

    "date": [
        "date_posted", "postedAt", "postedDate",
        "publishedAt", "createdAt", "date",
        "updated", "created"
    ],

    "job_id": [
        "jobId", "job_id", "jobPostingId",
        "job_posting_id", "externalId",
        "external_id", "sourceJobId",
        "source_job_id", "jobKey", "jobkey"
    ]
}


# =====================================================
# 3. NORMALIZATION HELPERS
# =====================================================

def extract_value(value):

    if value is None:
        return ""

    if isinstance(value, dict):

        for key in [
            "name", "display_name", "displayName",
            "label", "value", "city"
        ]:

            if value.get(key):
                return extract_value(value[key])

        return ""

    if isinstance(value, list):

        return ", ".join(
            extract_value(item)
            for item in value
            if item
        )

    return str(value).strip()


def get_value(row, field):

    for key in ALIASES[field]:

        value = row.get(key)

        if value is not None and value != "":

            return extract_value(value)

    return ""


def clean_text(value):

    value = html.unescape(str(value or ""))

    value = re.sub(
        r"<[^>]+>",
        " ",
        value
    )

    value = value.lower().strip()

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value
    )

    return re.sub(r"\s+", " ", value).strip()


def description_hash(description):

    cleaned = clean_text(description)

    if not cleaned:
        return ""

    return hashlib.sha256(
        cleaned.encode("utf-8")
    ).hexdigest()


def normalize_site(site):

    site = str(site or "").lower().strip()

    aliases = {
        "linkedin.com": "linkedin",
        "linkedin": "linkedin",
        "indeed.com": "indeed",
        "indeed": "indeed",
        "glassdoor.com": "glassdoor",
        "glassdoor": "glassdoor",
        "naukri.com": "naukri",
        "naukri": "naukri"
    }

    return aliases.get(site, site)


# =====================================================
# 4. PLATFORM-SPECIFIC JOB IDENTIFICATION
# =====================================================

def canonical_url(url, site):

    if not url:
        return ""

    try:

        parts = urlsplit(url.strip())

        host = parts.netloc.lower()
        host = host.removeprefix("www.")

        path = parts.path.rstrip("/")

        query = parse_qs(parts.query)

        site = normalize_site(site)

        # LinkedIn numeric job ID
        if site == "linkedin" or "linkedin.com" in host:

            match = re.search(
                r"/jobs/view/(?:.*?[-/])?(\d+)",
                path,
                re.I
            )

            if match:
                return "linkedin:" + match.group(1)

        # Glassdoor job ID
        if site == "glassdoor" or "glassdoor" in host:

            job_id = query.get("jl", [None])[0]

            if job_id:
                return "glassdoor:" + job_id

        # Indeed job ID
        if site == "indeed" or "indeed." in host:

            for key in ["jk", "vjk"]:

                job_id = query.get(key, [None])[0]

                if job_id:
                    return "indeed:" + job_id

        # Naukri job ID
        if site == "naukri" or "naukri." in host:

            match = re.search(
                r"-(\d{6,})(?:$|/)",
                path
            )

            if match:
                return "naukri:" + match.group(1)

        # Generic known job identifiers
        stable_keys = [
            "jobid", "job_id", "gh_jid"
        ]

        for key in stable_keys:

            value = query.get(key, [None])[0]

            if value:
                return f"{site}:{value}"

        # Generic URL normalization.
        # Remove tracking parameters, retain the path.
        if host and path:

            return host + path

        return ""

    except Exception:

        return ""


# =====================================================
# 5. NORMALIZE EVERY JOB RECORD
# =====================================================

def normalize_job(row, source, site):

    site = normalize_site(site)

    title = get_value(row, "title")
    company = get_value(row, "company")
    location = get_value(row, "location")
    description = get_value(row, "description")
    url = get_value(row, "url")
    date = get_value(row, "date")
    job_id = get_value(row, "job_id")

    url_key = canonical_url(url, site)

    desc_hash = description_hash(description)

    title_key = clean_text(title)
    company_key = clean_text(company)
    location_key = clean_text(location)

    fingerprint = "|".join([
        title_key,
        company_key,
        location_key
    ])

    return {

        "source": source,
        "site": site,

        "job_id": job_id,

        "title": title,
        "company": company,
        "location": location,

        "description": description,
        "date": date,
        "url": url,

        "url_key": url_key,
        "description_hash": desc_hash,

        "title_key": title_key,
        "company_key": company_key,
        "location_key": location_key,

        "fingerprint": fingerprint,

        "has_title": bool(title),
        "has_company": bool(company),
        "has_location": bool(location),
        "has_description": bool(description),
        "has_url": bool(url)
    }


# =====================================================
# 6. LOAD JSON AND JSONL FILES
# =====================================================

def load_json(path):

    if path.suffix.lower() == ".jsonl":

        with open(path, encoding="utf-8") as file:

            return [
                json.loads(line)
                for line in file
                if line.strip()
            ]

    with open(path, encoding="utf-8") as file:

        data = json.load(file)

    if isinstance(data, list):
        return data

    if isinstance(data, dict):

        for key in [
            "jobs", "results", "data", "items"
        ]:

            if isinstance(data.get(key), list):
                return data[key]

    raise ValueError(
        f"Unsupported JSON structure: {path}"
    )


# =====================================================
# 7. GENERATE A SAFE DEDUPLICATION KEY
# =====================================================

def dedup_key(job):

    site = job["site"]

    # First priority: platform-specific URL ID
    if job["url_key"]:

        return "URL|" + job["url_key"]

    # Second priority: explicit source job ID
    if job["job_id"]:

        return (
            "SOURCE_ID|" +
            site + "|" +
            clean_text(job["job_id"])
        )

    # Third priority: exact metadata AND description.
    # Do not merge solely on title/company/location.
    if (
        job["title_key"]
        and job["company_key"]
        and job["location_key"]
        and job["description_hash"]
    ):

        return (
            "CONTENT|" +
            job["fingerprint"] + "|" +
            job["description_hash"]
        )

    # No reliable identifier available.
    # Keep the record rather than risk deleting a real vacancy.
    return ""


# =====================================================
# 8. DEDUPLICATE EACH SOURCE INDEPENDENTLY
# =====================================================

def deduplicate(jobs, source):

    unique = {}

    duplicates = []

    for job in jobs:

        key = dedup_key(job)

        # Cannot safely identify this record.
        if not key:

            unique[
                f"UNVERIFIED|{len(unique)}"
            ] = job

            continue

        if key not in unique:

            unique[key] = job

        else:

            original = unique[key]

            # Keep the record with the more complete
            # description, while preserving duplicate details.
            if len(job["description"]) > len(
                original["description"]
            ):

                unique[key] = job
                kept = job
                removed = original

            else:

                kept = original
                removed = job

            duplicates.append({

                "Source": source,
                "Website": job["site"],

                "Duplicate Reason": (
                    "Same platform job ID / URL / "
                    "exact job metadata and description"
                ),

                "Kept Title": kept["title"],
                "Kept Company": kept["company"],
                "Kept URL": kept["url"],

                "Removed Title": removed["title"],
                "Removed Company": removed["company"],
                "Removed URL": removed["url"]
            })

    return list(unique.values()), duplicates


# =====================================================
# 9. LOAD APIFY DATA
# =====================================================

print("\nLoading Apify datasets...")

if not APIFY_DIR.exists():
    raise FileNotFoundError(
        f"Apify folder not found: {APIFY_DIR}\n"
        "Create an 'apify' folder beside compare.py and place the "
        "Apify JSON/JSONL exports inside it."
    )

if not JOBSPY_FILE.exists():
    raise FileNotFoundError(
        f"JobSpy dataset not found: {JOBSPY_FILE}\n"
        "Expected file: jobspy/jobspy_all_jobs.json"
    )

apify_raw = []

for path in APIFY_DIR.iterdir():

    if path.suffix.lower() not in [
        ".json", ".jsonl"
    ]:
        continue

    site = normalize_site(path.stem)

    records = load_json(path)

    for row in records:

        apify_raw.append(
            normalize_job(row, "Apify", site)
        )

print("Apify raw records:", len(apify_raw))


# =====================================================
# 10. LOAD JOBSPY DATA
# =====================================================

print("\nLoading JobSpy dataset...")

jobspy_records = load_json(JOBSPY_FILE)

jobspy_raw = []

for row in jobspy_records:

    site = normalize_site(
        row.get("benchmark_site") or
        row.get("site") or ""
    )

    jobspy_raw.append(
        normalize_job(row, "JobSpy", site)
    )

print("JobSpy raw records:", len(jobspy_raw))


# =====================================================
# 11. DEDUPLICATE APIFY AND JOBSPY
# =====================================================

print("\nDeduplicating Apify...")

apify_unique = []
apify_duplicates = []

for site in sorted(set(j["site"] for j in apify_raw)):

    records = [
        j for j in apify_raw
        if j["site"] == site
    ]

    unique, duplicates = deduplicate(
        records, "Apify"
    )

    apify_unique.extend(unique)
    apify_duplicates.extend(duplicates)


print("Deduplicating JobSpy...")

jobspy_unique = []
jobspy_duplicates = []

for site in sorted(set(j["site"] for j in jobspy_raw)):

    records = [
        j for j in jobspy_raw
        if j["site"] == site
    ]

    unique, duplicates = deduplicate(
        records, "JobSpy"
    )

    jobspy_unique.extend(unique)
    jobspy_duplicates.extend(duplicates)


all_duplicates = (
    apify_duplicates + jobspy_duplicates
)


# =====================================================
# 12. CROSS-SCRAPER COMPARISON
# =====================================================

def exact_match_key(job):

    key = dedup_key(job)

    if key:
        return key

    return ""


summary = []

common_all = []
apify_only_all = []
jobspy_only_all = []
potential_matches = []

all_sites = sorted(
    set(j["site"] for j in apify_unique) |
    set(j["site"] for j in jobspy_unique)
)


for site in all_sites:

    a = [
        j for j in apify_unique
        if j["site"] == site
    ]

    b = [
        j for j in jobspy_unique
        if j["site"] == site
    ]

    a_map = {}
    b_map = {}

    for job in a:

        key = exact_match_key(job)

        if key:
            a_map[key] = job

    for job in b:

        key = exact_match_key(job)

        if key:
            b_map[key] = job

    common_keys = set(a_map) & set(b_map)

    apify_only_keys = set(a_map) - set(b_map)

    jobspy_only_keys = set(b_map) - set(a_map)

    common = [
        (a_map[k], b_map[k])
        for k in common_keys
    ]

    apify_only = [
        a_map[k] for k in apify_only_keys
    ]

    jobspy_only = [
        b_map[k] for k in jobspy_only_keys
    ]

    # Records without reliable identity keys
    # remain unmatched until manual verification.
    a_unkeyed = [
        j for j in a
        if not exact_match_key(j)
    ]

    b_unkeyed = [
        j for j in b
        if not exact_match_key(j)
    ]

    apify_only.extend(a_unkeyed)
    jobspy_only.extend(b_unkeyed)

    # Save common records
    for aj, jj in common:

        common_all.append({

            "Website": site,

            "Apify Title": aj["title"],
            "JobSpy Title": jj["title"],

            "Apify Company": aj["company"],
            "JobSpy Company": jj["company"],

            "Apify Location": aj["location"],
            "JobSpy Location": jj["location"],

            "Apify URL": aj["url"],
            "JobSpy URL": jj["url"],

            "Match Method": (
                "Platform ID / URL / exact content"
            )
        })

    # Save source-specific jobs
    apify_only_all.extend(apify_only)
    jobspy_only_all.extend(jobspy_only)

    # ---------------------------------------------
    # POTENTIAL CROSS-SCRAPER MATCHES
    # ---------------------------------------------

    matched_apify = set()
    matched_jobspy = set()

    for ai, aj in enumerate(apify_only):

        if not aj["company_key"] or not aj["title_key"]:
            continue

        best_match = None
        best_score = 0

        for bi, jj in enumerate(jobspy_only):

            if bi in matched_jobspy:
                continue

            if not jj["company_key"] or not jj["title_key"]:
                continue

            # Company must match exactly.
            if aj["company_key"] != jj["company_key"]:
                continue

            title_score = fuzz.ratio(
                aj["title_key"],
                jj["title_key"]
            )

            location_score = fuzz.ratio(
                aj["location_key"],
                jj["location_key"]
            ) if aj["location_key"] and jj["location_key"] else 0

            description_score = fuzz.ratio(
                clean_text(aj["description"]),
                clean_text(jj["description"])
            ) if aj["description"] and jj["description"] else 0

            location_ok = (
                bool(aj["location_key"])
                and bool(jj["location_key"])
                and location_score >= 90
            )

            description_ok = (
                bool(aj["description"])
                and bool(jj["description"])
                and description_score >= 90
            )

            if title_score >= 94 and (
                location_ok or description_ok
            ):

                score = (
                    title_score * 0.5 +
                    max(location_score, description_score) * 0.5
                )

                if score > best_score:

                    best_score = score

                    best_match = (
                        bi, jj,
                        title_score,
                        location_score,
                        description_score
                    )

        if best_match:

            bi, jj, ts, ls, ds = best_match

            matched_apify.add(ai)
            matched_jobspy.add(bi)

            potential_matches.append({

                "Website": site,

                "Apify Title": aj["title"],
                "JobSpy Title": jj["title"],

                "Company": aj["company"],

                "Apify Location": aj["location"],
                "JobSpy Location": jj["location"],

                "Apify URL": aj["url"],
                "JobSpy URL": jj["url"],

                "Title Similarity %": round(ts, 2),
                "Location Similarity %": round(ls, 2),
                "Description Similarity %": round(ds, 2),

                "Status": "POTENTIAL MATCH - MANUAL REVIEW"
            })

    # ---------------------------------------------
    # WEBSITE SUMMARY
    # ---------------------------------------------

    matched_count = len(common)

    summary.append({

        "Website": site,

        "Apify Raw": sum(
            1 for j in apify_raw if j["site"] == site
        ),

        "Apify Duplicates Removed": sum(
            1 for j in apify_duplicates if j["Website"] == site
        ),

        "Apify Unique": len(a),

        "JobSpy Raw": sum(
            1 for j in jobspy_raw if j["site"] == site
        ),

        "JobSpy Duplicates Removed": sum(
            1 for j in jobspy_duplicates if j["Website"] == site
        ),

        "JobSpy Unique": len(b),

        "Common Exact Jobs": matched_count,

        "Potential Matches": sum(
            1 for j in potential_matches
            if j["Website"] == site
        ),

        "Apify Only": len(apify_only),
        "JobSpy Only": len(jobspy_only),

        "Apify Exact Coverage %": round(
            matched_count / len(a) * 100, 2
        ) if a else 0,

        "JobSpy Exact Overlap %": round(
            matched_count / len(b) * 100, 2
        ) if b else 0
    })


# =====================================================
# 13. FIELD COMPLETENESS
# =====================================================

def completeness(records, source):

    rows = [
        j for j in records
        if j["source"] == source
    ]

    if not rows:
        return {}

    total = len(rows)

    return {

        "Source": source,
        "Unique Records": total,

        "Title %": round(
            sum(j["has_title"] for j in rows)
            / total * 100, 2
        ),

        "Company %": round(
            sum(j["has_company"] for j in rows)
            / total * 100, 2
        ),

        "Location %": round(
            sum(j["has_location"] for j in rows)
            / total * 100, 2
        ),

        "Description %": round(
            sum(j["has_description"] for j in rows)
            / total * 100, 2
        ),

        "URL %": round(
            sum(j["has_url"] for j in rows)
            / total * 100, 2
        )
    }


fields = [
    completeness(apify_unique, "Apify"),
    completeness(jobspy_unique, "JobSpy")
]


# =====================================================
# 13A. EXCEL-SAFE VALUE CLEANING
# =====================================================

def clean_excel_value(value):
    """
    Make values safe for Excel/openpyxl.

    Excel uses XML-compatible cell strings. Some scraped job descriptions
    contain control characters that cause IllegalCharacterError.
    This removes invalid XML 1.0 characters and enforces Excel's
    32,767-character cell-text limit.
    """

    if value is None:
        return ""

    # Convert containers to readable JSON instead of passing unsupported
    # Python objects into openpyxl.
    if isinstance(value, (dict, list, tuple, set)):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            value = str(value)

    if not isinstance(value, str):
        return value

    # Keep only XML 1.0-valid characters:
    # tab, line feed, carriage return, normal BMP chars (excluding
    # surrogate range), and valid supplementary Unicode characters.
    value = "".join(
        char for char in value
        if (
            char in "\t\n\r"
            or "\x20" <= char <= "\uD7FF"
            or "\uE000" <= char <= "\uFFFD"
            or "\U00010000" <= char <= "\U0010FFFF"
        )
    )

    # Excel's maximum cell text length is 32,767 characters.
    return value[:32767]


def make_dataframe_excel_safe(df):
    """Return an Excel-safe copy of a DataFrame."""
    if df is None or df.empty:
        return df

    safe_df = df.copy()

    for column in safe_df.columns:
        safe_df[column] = safe_df[column].map(clean_excel_value)

    return safe_df


# =====================================================
# 14. EXPORT EXCEL REPORT
# =====================================================

summary_df = pd.DataFrame(summary)

fields_df = pd.DataFrame(fields)

apify_unique_df = pd.DataFrame(apify_unique)

jobspy_unique_df = pd.DataFrame(jobspy_unique)

common_df = pd.DataFrame(common_all)

apify_only_df = pd.DataFrame(apify_only_all)

jobspy_only_df = pd.DataFrame(jobspy_only_all)

duplicates_df = pd.DataFrame(all_duplicates)

potential_df = pd.DataFrame(potential_matches)


overall_df = pd.DataFrame([{

    "Apify Raw": len(apify_raw),

    "Apify Duplicates Removed": len(apify_duplicates),

    "Apify Unique": len(apify_unique),

    "JobSpy Raw": len(jobspy_raw),

    "JobSpy Duplicates Removed": len(jobspy_duplicates),

    "JobSpy Unique": len(jobspy_unique),

    "Common Exact Jobs": len(common_all),

    "Potential Matches": len(potential_matches),

    "Apify Only": len(apify_only_all),

    "JobSpy Only": len(jobspy_only_all)

}])


# Clean every sheet before export. This prevents scraped control characters
# in job titles/descriptions/other fields from crashing openpyxl.
overall_df = make_dataframe_excel_safe(overall_df)
summary_df = make_dataframe_excel_safe(summary_df)
fields_df = make_dataframe_excel_safe(fields_df)
apify_unique_df = make_dataframe_excel_safe(apify_unique_df)
jobspy_unique_df = make_dataframe_excel_safe(jobspy_unique_df)
common_df = make_dataframe_excel_safe(common_df)
apify_only_df = make_dataframe_excel_safe(apify_only_df)
jobspy_only_df = make_dataframe_excel_safe(jobspy_only_df)
duplicates_df = make_dataframe_excel_safe(duplicates_df)
potential_df = make_dataframe_excel_safe(potential_df)


with pd.ExcelWriter(
    OUTPUT_FILE,
    engine="openpyxl"
) as writer:

    overall_df.to_excel(
        writer,
        sheet_name="Overall Summary",
        index=False
    )

    summary_df.to_excel(
        writer,
        sheet_name="Website Comparison",
        index=False
    )

    fields_df.to_excel(
        writer,
        sheet_name="Field Completeness",
        index=False
    )

    apify_unique_df.to_excel(
        writer,
        sheet_name="Apify Unique",
        index=False
    )

    jobspy_unique_df.to_excel(
        writer,
        sheet_name="JobSpy Unique",
        index=False
    )

    common_df.to_excel(
        writer,
        sheet_name="Common Jobs",
        index=False
    )

    apify_only_df.to_excel(
        writer,
        sheet_name="Apify Only",
        index=False
    )

    jobspy_only_df.to_excel(
        writer,
        sheet_name="JobSpy Only",
        index=False
    )

    duplicates_df.to_excel(
        writer,
        sheet_name="Duplicate Records",
        index=False
    )

    potential_df.to_excel(
        writer,
        sheet_name="Potential Matches",
        index=False
    )


# =====================================================
# 15. FINAL TERMINAL SUMMARY
# =====================================================

print("\n============================================")
print("       DEDUPLICATION & COMPARISON")
print("============================================")

print("\nOVERALL RESULTS")

print(overall_df.to_string(index=False))

print("\nWEBSITE-WISE RESULTS")

print(summary_df.to_string(index=False))

print("\n============================================")

print("Apify duplicates removed:", len(apify_duplicates))

print("JobSpy duplicates removed:", len(jobspy_duplicates))

print("Apify unique jobs:", len(apify_unique))

print("JobSpy unique jobs:", len(jobspy_unique))

print("Common exact jobs:", len(common_all))

print("Potential matches:", len(potential_matches))

print("\nREPORT GENERATED:")

print(OUTPUT_FILE)

print("\n============================================")
