"""
Naukri data parser and field extractor.

Extracts canonical fields from raw Naukri / JobSpy records:
- Native experience ranges ("4-7 Yrs", "Fresher", "0-2 Yrs", "8+ Yrs")
- Work modes (onsite, hybrid, remote, unknown) and remote eligibility
- Description type classification (full_description, search_result_snippet, partial_description, missing)
- Stable source job ID
- Clean application & job URLs
- Source provenance tracking
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from models import (
    FieldOrigin,
    JobRecord,
    QualityIndicators,
    VerificationStatus,
    WorkMode,
)


class DescriptionType(str, Enum):
    FULL_DESCRIPTION = "full_description"
    SEARCH_RESULT_SNIPPET = "search_result_snippet"
    PARTIAL_DESCRIPTION = "partial_description"
    MISSING = "missing"


# Regex patterns for parsing Naukri's native experience text (e.g., "4-7 Yrs", "0-2 Yrs", "8+ Yrs")
RANGE_PATTERN = re.compile(
    r"^(\d+)\s*(?:-|–|—|to)\s*(\d+)\s*(?:yrs|years)?",
    re.IGNORECASE,
)
MIN_ONLY_PATTERN = re.compile(
    r"^(\d+)\+?\s*(?:yrs|years)?",
    re.IGNORECASE,
)
FRESHER_PATTERN = re.compile(
    r"^(?:fresher|0\s*(?:-|–|—|to)\s*0|entry[\s-]level)",
    re.IGNORECASE,
)

# Text-based experience patterns for description snippets (fallback)
FALLBACK_EXP_PATTERNS = [
    re.compile(
        r"(?:require[sd]?|minimum|at\s+least|mandatory)?\s*(\d+(?:\.\d+)?)\s*(?:[-–—~]|to)\s*(\d+(?:\.\d+)?)\s*\+?\s*years?(?:\s+(?:of|in|as\s+a?)\s+[a-zA-Z\s]{0,40})?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(\d+(?:\.\d+)?)\s*\+\s*years?(?:\s+(?:of|in|as\s+a?)\s+[a-zA-Z\s]{0,40})?(?:experience|exp)",
        re.IGNORECASE,
    ),
]

# Work mode false-positive filters (technology or operational terms, NOT work arrangements)
REMOTE_FALSE_POSITIVES = re.compile(
    r"\b(?:remote\s+access|remote\s+monitoring|remote\s+support|remote\s+desktop|remote\s+control|remote\s+site|remote\s+code|remote\s+execution|remote\s+debugging)\b",
    re.IGNORECASE,
)
HYBRID_FALSE_POSITIVES = re.compile(
    r"\b(?:hybrid\s+cloud|hybrid\s+infrastructure|hybrid\s+model|hybrid\s+architecture|hybrid\s+environment|hybrid\s+network)\b",
    re.IGNORECASE,
)

# Explicit work-mode patterns
EXPLICIT_HYBRID_PATTERN = re.compile(
    r"\b(?:hybrid\s+work(?:ing)?|hybrid\s+mode|hybrid\s+role|hybrid\s+setup|work\s+mode\s*:\s*hybrid|workplace\s*:\s*hybrid|days\s+(?:in|from)\s+office)\b",
    re.IGNORECASE,
)
EXPLICIT_REMOTE_PATTERN = re.compile(
    r"\b(?:100%\s+remote|fully\s+remote|work\s+from\s+home|wfh|remote\s+work(?:ing)?|remote\s+first|permanent\s+remote|work\s+mode\s*:\s*remote)\b",
    re.IGNORECASE,
)
EXPLICIT_ONSITE_PATTERN = re.compile(
    r"\b(?:work\s+from\s+office|wfo|100%\s+onsite|fully\s+onsite|on-site\s+work(?:ing)?|in-office\s+only|work\s+mode\s*:\s*onsite|walk-in(?:\s+drive)?)\b",
    re.IGNORECASE,
)


def clean_text_token(value: Any) -> str:
    """Normalize text for parsing and identity comparison."""
    if not value:
        return ""
    val = html.unescape(str(value))
    val = re.sub(r"<[^>]+>", " ", val)
    return re.sub(r"\s+", " ", val).strip()


def normalize_naukri_url(url: Any) -> str:
    """Strip tracking and non-job query parameters from Naukri URL."""
    if not url or not isinstance(url, str):
        return ""
    try:
        parts = urlsplit(url.strip())
        if not parts.netloc:
            return url.strip()
        host = parts.netloc.lower().removeprefix("www.")
        path = parts.path.rstrip("/")
        tracking = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term",
            "utm_content", "src", "tracking", "ref", "trk", "fbclid", "gclid",
        }
        query_items = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() not in tracking:
                query_items.append((key, value))
        query_items.sort()
        query = urlencode(query_items, doseq=True)
        return urlunsplit((parts.scheme or "https", host, path, query, ""))
    except Exception:
        return url.strip()


def extract_naukri_job_id(raw_id: str | None, job_url: str | None) -> str | None:
    """
    Extract the stable numeric job ID from Naukri raw ID (e.g. 'nk-171126013925')
    or canonical URL (e.g. '...-171126013925').
    """
    if raw_id:
        clean = str(raw_id).strip()
        if clean.startswith("nk-"):
            clean = clean[3:]
        if clean.isdigit():
            return clean

    if job_url:
        match = re.search(r"-(\d{8,14})(?:\?|$)", str(job_url))
        if match:
            return match.group(1)

    return None


def parse_naukri_experience(
    exp_text: str | None,
    snippet: str | None = None,
) -> tuple[float | None, float | None, str | None, FieldOrigin]:
    """
    Extract minimum and maximum experience from Naukri's native experienceText
    or fallback to description snippet.
    """
    if exp_text and str(exp_text).strip():
        text = str(exp_text).strip()

        # Check fresher
        if FRESHER_PATTERN.search(text):
            return 0.0, 0.0, text, FieldOrigin.SOURCE_PROVIDED

        # Check range: "4-7 Yrs"
        range_match = RANGE_PATTERN.search(text)
        if range_match:
            min_exp = float(range_match.group(1))
            max_exp = float(range_match.group(2))
            return min_exp, max_exp, text, FieldOrigin.SOURCE_PROVIDED

        # Check min only: "5+ Yrs" or "5 Yrs"
        min_match = MIN_ONLY_PATTERN.search(text)
        if min_match:
            min_exp = float(min_match.group(1))
            return min_exp, None, text, FieldOrigin.SOURCE_PROVIDED

    # Fallback to snippet if native field is unavailable
    if snippet:
        for pattern in FALLBACK_EXP_PATTERNS:
            match = pattern.search(snippet)
            if match:
                groups = match.groups()
                if len(groups) == 2 and groups[1] is not None:
                    return float(groups[0]), float(groups[1]), match.group(0), FieldOrigin.EXTRACTED_FROM_DESCRIPTION
                elif len(groups) >= 1 and groups[0] is not None:
                    return float(groups[0]), None, match.group(0), FieldOrigin.EXTRACTED_FROM_DESCRIPTION

    return None, None, None, FieldOrigin.UNKNOWN


def parse_naukri_work_mode(
    work_from_home_type: str | None,
    location_str: str | None,
    snippet: str | None = None,
    title: str | None = None,
) -> tuple[WorkMode, str | None, str]:
    """
    Classify work mode and remote eligibility from explicit evidence only.
    Distinguishes Onsite, Hybrid, Remote, and Unknown.

    NON-NEGOTIABLE RULE:
    Never assume that a city name (e.g. Bengaluru, Hyderabad) automatically implies onsite.
    Without explicit evidence, work mode must be classified as UNKNOWN.
    """
    wfh = (work_from_home_type or "").lower().strip()
    loc = (location_str or "").lower().strip()
    snip = (snippet or "").lower()
    t_str = (title or "").lower()

    # Sanitized strings for regex matching (stripping technology false positives)
    snip_clean = HYBRID_FALSE_POSITIVES.sub(" ", snip)
    snip_clean = REMOTE_FALSE_POSITIVES.sub(" ", snip_clean)

    # 1. Check EXPLICIT HYBRID evidence
    # - Native attribute says Hybrid
    # - Location string explicitly says "Hybrid - City" or "(Hybrid)"
    # - Title contains "--Hybrid" or "(Hybrid)" (excluding hybrid cloud)
    if wfh == "hybrid":
        return WorkMode.HYBRID, None, "source_native_hybrid"

    if " - " in loc and "hybrid" in loc.rpartition(" - ")[0]:
        return WorkMode.HYBRID, None, "location_prefix_hybrid"

    if "hybrid" in loc and not HYBRID_FALSE_POSITIVES.search(loc):
        return WorkMode.HYBRID, None, "location_explicit_hybrid"

    if "hybrid" in t_str and not HYBRID_FALSE_POSITIVES.search(t_str):
        return WorkMode.HYBRID, None, "title_explicit_hybrid"

    if EXPLICIT_HYBRID_PATTERN.search(snip_clean):
        return WorkMode.HYBRID, None, "snippet_explicit_hybrid"

    # 2. Check EXPLICIT REMOTE evidence
    # - Native attribute says Remote
    # - Location is "Remote" or starts with "Remote - "
    # - Title or snippet has explicit remote work phrase
    if wfh == "remote":
        evidence = "source_native_remote"
        if "india" in loc or "india" in snip or "anywhere in india" in snip:
            return WorkMode.REMOTE, "india_only", evidence
        return WorkMode.REMOTE, "india_only", evidence

    if loc == "remote" or ((" - " in loc) and loc.rpartition(" - ")[0].strip() == "remote"):
        evidence = "location_explicit_remote"
        return WorkMode.REMOTE, "india_only", evidence

    if "remote" in t_str and not REMOTE_FALSE_POSITIVES.search(t_str):
        evidence = "title_explicit_remote"
        return WorkMode.REMOTE, "india_only", evidence

    if EXPLICIT_REMOTE_PATTERN.search(snip_clean):
        evidence = "snippet_explicit_remote"
        return WorkMode.REMOTE, "india_only", evidence

    # 3. Check EXPLICIT ONSITE evidence
    # Note: JobSpy's wfh defaults to "Work from office" for plain cities, so wfh alone
    # without explicit confirmation cannot be trusted blindly if loc is just a city.
    if EXPLICIT_ONSITE_PATTERN.search(t_str):
        return WorkMode.ONSITE, None, "title_explicit_onsite"

    if EXPLICIT_ONSITE_PATTERN.search(snip_clean):
        return WorkMode.ONSITE, None, "snippet_explicit_onsite"

    if "onsite" in loc or "on-site" in loc or "work from office" in loc:
        return WorkMode.ONSITE, None, "location_explicit_onsite"

    # 4. INSUFFICIENT EVIDENCE -> UNKNOWN (Do NOT assume city implies onsite!)
    return WorkMode.UNKNOWN, None, "insufficient_evidence"


def classify_description_type(
    description: str | None,
    is_source_snippet: bool = True,
) -> DescriptionType:
    """
    Classify the description into an explicit type:
    - full_description (complete verified description >= 500 chars)
    - search_result_snippet (native snippet from search payload, e.g. 200-400 chars)
    - partial_description (truncated or incomplete text)
    - missing (None or empty)
    """
    if description is None or not str(description).strip():
        return DescriptionType.MISSING

    text = str(description).strip()
    length = len(text)

    if is_source_snippet:
        return DescriptionType.SEARCH_RESULT_SNIPPET

    if length >= 500:
        if text.endswith("...") or text.endswith("…"):
            return DescriptionType.PARTIAL_DESCRIPTION
        return DescriptionType.FULL_DESCRIPTION
    elif length >= 50:
        return DescriptionType.SEARCH_RESULT_SNIPPET
    else:
        return DescriptionType.PARTIAL_DESCRIPTION


def parse_naukri_date(raw_date: Any) -> str | None:
    """
    Parse date from epoch milliseconds, date object, or ISO string to ISO format string.
    """
    if raw_date is None:
        return None

    if isinstance(raw_date, (int, float)):
        # Epoch ms or seconds
        if raw_date > 1e11:  # epoch ms
            dt = datetime.fromtimestamp(raw_date / 1000.0, tz=timezone.utc)
        else:
            dt = datetime.fromtimestamp(raw_date, tz=timezone.utc)
        return dt.isoformat()

    if hasattr(raw_date, "strftime"):  # date or datetime
        if not hasattr(raw_date, "hour"):  # date object
            dt = datetime.combine(raw_date, datetime.min.time(), tzinfo=timezone.utc)
        elif raw_date.tzinfo is None:
            dt = raw_date.replace(tzinfo=timezone.utc)
        else:
            dt = raw_date.astimezone(timezone.utc)
        return dt.isoformat()

    if isinstance(raw_date, str):
        raw_str = raw_date.strip()
        for fmt in (
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S%z",
        ):
            try:
                dt = datetime.strptime(raw_str, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                return dt.isoformat()
            except ValueError:
                continue

    return None


def parse_naukri_record(raw: dict[str, Any], run_id: str) -> JobRecord | None:
    """
    Convert a raw Naukri / JobSpy dictionary into a canonical JobRecord.
    Never fabricates missing values; sets origins appropriately.
    """
    title = clean_text_token(raw.get("title"))
    company = clean_text_token(raw.get("company"))

    if not title or not company:
        return None

    # Job URLs & IDs
    raw_job_url = raw.get("job_url") or raw.get("jdURL")
    if raw_job_url and raw_job_url.startswith("/"):
        raw_job_url = "https://www.naukri.com" + raw_job_url
    job_url = normalize_naukri_url(raw_job_url)

    job_id = extract_naukri_job_id(raw.get("id") or raw.get("jobId"), job_url)
    app_url = normalize_naukri_url(raw.get("job_url_direct") or raw.get("applyRedirectUrl"))

    # Location & Work Mode
    location = clean_text_token(raw.get("location")) or "India"
    description = raw.get("description") or raw.get("jobDescription") or None
    wfh_type = raw.get("work_from_home_type")

    work_mode, remote_elig, mode_evidence = parse_naukri_work_mode(
        work_from_home_type=wfh_type,
        location_str=location,
        snippet=description,
        title=title,
    )

    # Experience
    exp_text = raw.get("experience_range") or raw.get("experienceText")
    min_exp, max_exp, raw_exp_text, exp_origin = parse_naukri_experience(
        exp_text=exp_text,
        snippet=description,
    )

    # Date
    date_posted = parse_naukri_date(raw.get("date_posted") or raw.get("createdDate"))

    # Salary: NaN handling
    salary_min = None
    salary_max = None
    currency = raw.get("currency")
    if raw.get("min_amount") is not None:
        try:
            val = float(raw["min_amount"])
            if not (val != val):  # check for NaN
                salary_min = val
        except (ValueError, TypeError):
            pass
    if raw.get("max_amount") is not None:
        try:
            val = float(raw["max_amount"])
            if not (val != val):  # check for NaN
                salary_max = val
        except (ValueError, TypeError):
            pass

    # Description Classification
    desc_type = classify_description_type(
        description=description,
        is_source_snippet=True,  # Naukri search API returns snippets because v4/job is CAPTCHA gated
    )

    field_origins = {
        "title": FieldOrigin.SOURCE_PROVIDED,
        "company": FieldOrigin.SOURCE_PROVIDED,
        "location": FieldOrigin.SOURCE_PROVIDED,
        "description": FieldOrigin.SOURCE_PROVIDED if desc_type != DescriptionType.MISSING else FieldOrigin.UNKNOWN,
        "date_posted": FieldOrigin.SOURCE_PROVIDED if date_posted else FieldOrigin.UNKNOWN,
        "experience": exp_origin,
        "work_mode": FieldOrigin.SOURCE_PROVIDED if work_mode != WorkMode.UNKNOWN else FieldOrigin.UNKNOWN,
    }

    metadata = {
        "raw_id": raw.get("id"),
        "skills": raw.get("skills") or raw.get("tagsAndSkills"),
        "company_apply": raw.get("companyApplyJob"),
        "mode_evidence": mode_evidence,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": currency,
        "description_type": desc_type.value,
    }

    kw = raw.get("test_keyword")
    loc = raw.get("test_location")
    matched_kws = [kw] if kw else []
    matched_locs = [loc] if loc else []
    matched_runs = [run_id]

    rec = JobRecord(
        source="naukri",
        source_job_id=job_id,
        collection_method="naukri_api_v3",
        verification_status=VerificationStatus.UNVERIFIED,
        title=title,
        company=company,
        location=location,
        country="India",
        work_mode=work_mode,
        remote_eligibility=remote_elig,
        description=description,
        min_experience=min_exp,
        max_experience=max_exp,
        mandatory_experience=raw_exp_text if exp_origin == FieldOrigin.SOURCE_PROVIDED else None,
        date_posted=date_posted,
        job_url=job_url or None,
        application_url=app_url or None,
        source_metadata=metadata,
        field_origins=field_origins,
        deduplication_key=job_url or f"{company}:{title}:{location}",
        matched_keywords=matched_kws,
        matched_locations=matched_locs,
        matched_run_ids=matched_runs,
    )
    rec.quality_indicators = rec.compute_quality_indicators()
    return rec
