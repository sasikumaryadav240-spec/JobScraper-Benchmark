"""
LinkedIn data parser and field extractor.

Extracts canonical fields from raw LinkedIn / JobSpy records:
- Job titles, employer companies, and standardized locations
- Full descriptions vs snippets with explicit DescriptionType classification
- Experience requirements (min, max, preferred, raw wording) with false positive filtering
- Strict evidence-based work mode classification (onsite, hybrid, remote, unknown)
- Remote eligibility classification (india_only, country_restricted, international, unknown)
- Canonical LinkedIn job URLs and source job IDs
- Provenance and field origin metadata
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

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


# --- Work Mode Regex Filters & Patterns ---
REMOTE_FALSE_POSITIVES = re.compile(
    r"\b(?:remote\s+access|remote\s+monitoring|remote\s+support|remote\s+desktop|"
    r"remote\s+control|remote\s+site|remote\s+code(?:\s+execution)?|remote\s+debugging|"
    r"remote\s+administration|remote\s+management|remote\s+hands)\b",
    re.IGNORECASE,
)

HYBRID_FALSE_POSITIVES = re.compile(
    r"\b(?:hybrid\s+cloud|hybrid\s+infrastructure|hybrid\s+model\s+of\s+(?:cloud|deployment|security)|"
    r"hybrid\s+architecture|hybrid\s+environment|hybrid\s+network|hybrid\s+setup\s+for\s+cloud)\b",
    re.IGNORECASE,
)

EXPLICIT_HYBRID_PATTERN = re.compile(
    r"\b(?:hybrid\s+work(?:ing)?|hybrid\s+mode|hybrid\s+role|hybrid\s+setup|"
    r"work\s+mode\s*:\s*hybrid|workplace\s*:\s*hybrid|work\s+from\s+home\s+\d+\s+days|"
    r"\d+\s+days\s+(?:in|from)\s+office|flexible\s+work\s+arrangement)\b",
    re.IGNORECASE,
)

EXPLICIT_REMOTE_PATTERN = re.compile(
    r"\b(?:100%\s+remote|fully\s+remote|work\s+from\s+home|wfh|remote\s+work(?:ing)?|"
    r"remote\s+first|permanent\s+remote|work\s+mode\s*:\s*remote|remote\s+role|"
    r"remote\s+position|remote\s+job|remote\s+opportunity)\b",
    re.IGNORECASE,
)

EXPLICIT_ONSITE_PATTERN = re.compile(
    r"\b(?:work\s+from\s+office|wfo|100%\s+onsite|fully\s+onsite|on-site\s+work(?:ing)?|"
    r"in-office\s+only|work\s+mode\s*:\s*onsite|on-site\s+role|on-site\s+position|"
    r"office\s+based|working\s+from\s+office)\b",
    re.IGNORECASE,
)

# --- Experience Extraction Regex Patterns ---
EXP_RANGE_PATTERN = re.compile(
    r"(?:require[sd]?|minimum|at\s+least|mandatory)?\s*(\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)(?:\s+(?:of|in|as\s+a?)\s+[a-zA-Z\s]{0,40})?(?:experience|exp)?",
    re.IGNORECASE,
)

EXP_MIN_PATTERN = re.compile(
    r"(?:require[sd]?|minimum|at\s+least|mandatory)?\s*(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)(?:\s+(?:of|in|as\s+a?)\s+[a-zA-Z\s]{0,40})?(?:experience|exp)",
    re.IGNORECASE,
)

EXP_PREFERRED_PATTERN = re.compile(
    r"(?:(?:preferred|plus|bonus|desirable|nice\s+to\s+have|advantageous)[^.\n]{0,40}?(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)|(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)[^.\n]{0,40}?(?:preferred|plus|bonus|desirable|nice\s+to\s+have|advantageous))",
    re.IGNORECASE,
)

FRESHER_PATTERN = re.compile(
    r"\b(?:fresher|freshers|0\s*(?:-|–|—|to)\s*0\s*(?:years?|yrs?)|entry[\s-]level\s+no\s+experience)\b",
    re.IGNORECASE,
)

# Experience False-Positive Patterns
EXP_FALSE_POSITIVES = re.compile(
    r"\b(?:\d+[\s-]year\s+(?:degree|diploma|bachelor|master|program|course)|"
    r"over\s+\d+\s+years\s+(?:in\s+business|of\s+experience\s+as\s+a\s+company|of\s+industry\s+leadership)|"
    r"founded\s+\d+\s+years\s+ago|for\s+more\s+than\s+\d+\s+years\s+we\s+have|"
    r"24[\s/]+7|8[\s-]hour\s+shift)\b",
    re.IGNORECASE,
)


def clean_text_token(value: Any) -> str:
    """Normalize text for parsing and identity comparison."""
    if not value:
        return ""
    val = html.unescape(str(value))
    val = re.sub(r"<[^>]+>", " ", val)
    return re.sub(r"\s+", " ", val).strip()


def extract_linkedin_job_id(raw_id: Any, url: Any) -> str | None:
    """
    Extract stable numeric LinkedIn job ID.
    Examples:
      - 'li-4492063072' -> '4492063072'
      - 'https://www.linkedin.com/jobs/view/4492063072' -> '4492063072'
      - 'https://in.linkedin.com/jobs/view/soc-analyst-at-cgi-4492063072' -> '4492063072'
    """
    if raw_id:
        s = str(raw_id).strip()
        if s.startswith("li-"):
            s = s[3:]
        if s.isdigit():
            return s

    if url:
        u = str(url).strip()
        match = re.search(r"/jobs/view/(?:[a-zA-Z0-9\-_]+-)?(\d+)", u)
        if match:
            return match.group(1)
        param_match = re.search(r"currentJobId=(\d+)", u)
        if param_match:
            return param_match.group(1)

    return None


def normalize_linkedin_url(url: Any) -> str:
    """
    Strip tracking and session parameters from LinkedIn job URL.
    Standardizes to https://www.linkedin.com/jobs/view/{job_id} if ID is present.
    """
    if not url or not isinstance(url, str):
        return ""
    job_id = extract_linkedin_job_id(None, url)
    if job_id:
        return f"https://www.linkedin.com/jobs/view/{job_id}"

    try:
        parts = urlsplit(url.strip())
        host = parts.netloc.lower().removeprefix("www.")
        path = parts.path.rstrip("/")
        tracking = {
            "trackingid", "refid", "position", "pagenum", "trk",
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        }
        filtered_q = [
            (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in tracking
        ]
        filtered_q.sort()
        from urllib.parse import urlencode
        return urlunsplit(("https", host, path, urlencode(filtered_q, doseq=True), ""))
    except Exception:
        return url.strip()


def classify_description_type(description: str | None) -> DescriptionType:
    """
    Categorize description quality based on length and structure.
    - full_description: complete job posting (>= 500 characters)
    - search_result_snippet: brief search snippet (< 350 characters)
    - partial_description: truncated or partial description (350-500 characters)
    - missing: null or empty
    """
    if not description or not str(description).strip():
        return DescriptionType.MISSING
    text = str(description).strip()
    if len(text) >= 500:
        return DescriptionType.FULL_DESCRIPTION
    elif len(text) >= 350:
        return DescriptionType.PARTIAL_DESCRIPTION
    else:
        return DescriptionType.SEARCH_RESULT_SNIPPET


def extract_experience_from_text(text: str) -> tuple[float | None, float | None, float | None, str | None]:
    """
    Extract (min_exp, max_exp, preferred_exp, raw_wording) from text.
    Strictly filters out degree durations, company history, tool versions, and shifts.
    Never infers 0 years unless explicitly stated as fresher.
    """
    if not text:
        return None, None, None, None

    cleaned_text = clean_text_token(text)

    # 1. Check for explicit fresher phrasing
    if FRESHER_PATTERN.search(cleaned_text):
        return 0.0, 0.0, None, "Fresher / Entry level"

    # 2. Extract preferred experience if mentioned
    preferred_val: float | None = None
    pref_match = EXP_PREFERRED_PATTERN.search(cleaned_text)
    if pref_match:
        try:
            val_str = pref_match.group(1) or pref_match.group(2)
            if val_str:
                preferred_val = float(val_str)
        except (ValueError, TypeError):
            preferred_val = None

    # Helper: check if match overlaps with a known false positive
    def is_false_positive(match_start: int, match_end: int) -> bool:
        context_start = max(0, match_start - 40)
        context_end = min(len(cleaned_text), match_end + 40)
        surrounding = cleaned_text[context_start:context_end]
        return bool(EXP_FALSE_POSITIVES.search(surrounding))

    # 3. Check for range matches
    for m in EXP_RANGE_PATTERN.finditer(cleaned_text):
        if is_false_positive(m.start(), m.end()):
            continue
        try:
            low = float(m.group(1))
            high = float(m.group(2))
            if 0.0 <= low <= 30.0 and 0.0 <= high <= 35.0 and low <= high:
                raw_wording = m.group(0).strip()
                return low, high, preferred_val, raw_wording
        except (ValueError, TypeError):
            continue

    # 4. Check for minimum-only matches
    for m in EXP_MIN_PATTERN.finditer(cleaned_text):
        if is_false_positive(m.start(), m.end()):
            continue
        try:
            val = float(m.group(1))
            if 0.0 <= val <= 30.0:
                raw_wording = m.group(0).strip()
                return val, None, preferred_val, raw_wording
        except (ValueError, TypeError):
            continue

    return None, None, preferred_val, None


def extract_work_mode(
    title: str,
    location_str: str,
    description: str,
) -> tuple[WorkMode, str | None]:
    """
    Classify work mode based strictly on explicit evidence.
    Rule: A city name or physical location alone does NOT imply onsite work.
    Returns (WorkMode, evidence_string).
    """
    loc_clean = clean_text_token(location_str)
    title_clean = clean_text_token(title)
    desc_clean = clean_text_token(description)

    # 1. Check title and location annotations first (high confidence)
    for target in [loc_clean, title_clean]:
        if "hybrid" in target.lower():
            return WorkMode.HYBRID, f"Location/Title explicit: '{target}'"
        if "remote" in target.lower() or "wfh" in target.lower():
            if not REMOTE_FALSE_POSITIVES.search(target):
                return WorkMode.REMOTE, f"Location/Title explicit: '{target}'"
        if "on-site" in target.lower() or "onsite" in target.lower():
            return WorkMode.ONSITE, f"Location/Title explicit: '{target}'"

    # 2. Check description with false positive exclusions
    masked_desc = REMOTE_FALSE_POSITIVES.sub(" [remote_tech_term] ", desc_clean)
    masked_desc = HYBRID_FALSE_POSITIVES.sub(" [hybrid_tech_term] ", masked_desc)

    hybrid_match = EXPLICIT_HYBRID_PATTERN.search(masked_desc)
    remote_match = EXPLICIT_REMOTE_PATTERN.search(masked_desc)
    onsite_match = EXPLICIT_ONSITE_PATTERN.search(masked_desc)

    if hybrid_match:
        return WorkMode.HYBRID, f"Description clause: '{hybrid_match.group(0)}'"
    if remote_match:
        return WorkMode.REMOTE, f"Description clause: '{remote_match.group(0)}'"
    if onsite_match:
        return WorkMode.ONSITE, f"Description clause: '{onsite_match.group(0)}'"

    return WorkMode.UNKNOWN, None


def extract_remote_eligibility(
    location_str: str,
    description: str,
    work_mode: WorkMode,
) -> tuple[str | None, str | None]:
    """
    Determine remote eligibility when work mode is REMOTE.
    Returns (eligibility_category, evidence).
    Categories: 'india_only', 'country_restricted', 'international', 'unknown'.
    """
    if work_mode != WorkMode.REMOTE:
        return None, None

    combined = f"{location_str} {description}".lower()

    # India-only indicators
    india_patterns = [
        r"\b(?:within\s+india|in\s+india|resident\s+of\s+india|india[\s-]only|located\s+in\s+india|india\s+based)\b",
        r"\b(?:work\s+authorization\s+in\s+india|eligible\s+to\s+work\s+in\s+india)\b",
    ]
    for p in india_patterns:
        m = re.search(p, combined)
        if m:
            return "india_only", f"Clause: '{m.group(0)}'"

    # Check if location string is solely India or Indian city
    if re.search(r"\b(?:india|bengaluru|hyderabad|pune|chennai|mumbai|gurugram|noida)\b", location_str.lower()):
        return "india_only", f"Remote role registered in Indian location: '{location_str}'"

    # Restricted country patterns
    restricted_patterns = [
        r"\b(?:us[\s-]only|united\s+states\s+only|must\s+reside\s+in\s+the\s+us|eu[\s-]only)\b",
    ]
    for p in restricted_patterns:
        m = re.search(p, combined)
        if m:
            return "country_restricted", f"Clause: '{m.group(0)}'"

    # International patterns
    intl_patterns = [
        r"\b(?:anywhere\s+in\s+the\s+world|globally\s+remote|international\s+remote|worldwide)\b",
    ]
    for p in intl_patterns:
        m = re.search(p, combined)
        if m:
            return "international", f"Clause: '{m.group(0)}'"

    return "unknown", None


def parse_linkedin_date(date_val: Any) -> str | None:
    """Parse LinkedIn date into ISO 8601 string."""
    if date_val is None:
        return None
    if isinstance(date_val, datetime):
        if date_val.tzinfo is None:
            return date_val.replace(tzinfo=timezone.utc).isoformat()
        return date_val.astimezone(timezone.utc).isoformat()
    if isinstance(date_val, date):
        return datetime(date_val.year, date_val.month, date_val.day, tzinfo=timezone.utc).isoformat()

    s = str(date_val).strip()
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue

    return s if s else None


def parse_linkedin_record(raw: dict[str, Any], run_id: str) -> JobRecord | None:
    """
    Transform a raw LinkedIn record from JobSpy into a canonical JobRecord.
    Enforces quality checks: non-empty title, non-empty company, explicit description types,
    strict work mode evidence, and structured error avoidance.
    """
    title = clean_text_token(raw.get("title"))
    company = clean_text_token(raw.get("company"))
    if not title or not company:
        return None

    raw_url = raw.get("job_url")
    job_url = normalize_linkedin_url(raw_url)
    source_job_id = extract_linkedin_job_id(raw.get("id"), raw_url)
    direct_url = raw.get("job_url_direct")
    application_url = normalize_linkedin_url(direct_url) if direct_url else None

    location = clean_text_token(raw.get("location"))
    raw_desc = raw.get("description")
    description = clean_text_token(raw_desc)
    desc_type = classify_description_type(description)

    # Date handling
    date_posted = parse_linkedin_date(raw.get("date_posted"))

    # Experience extraction
    min_exp, max_exp, pref_exp, exp_wording = extract_experience_from_text(description)

    # Work mode & remote eligibility
    work_mode, mode_evidence = extract_work_mode(title, location, description)
    remote_elig, remote_evidence = extract_remote_eligibility(location, description, work_mode)

    # Salary handling (avoid storing float('nan'))
    salary_min = None
    salary_max = None
    if raw.get("min_amount") is not None:
        try:
            val = float(raw["min_amount"])
            if not (val != val):
                salary_min = val
        except (ValueError, TypeError):
            pass

    if raw.get("max_amount") is not None:
        try:
            val = float(raw["max_amount"])
            if not (val != val):
                salary_max = val
        except (ValueError, TypeError):
            pass

    salary_currency = raw.get("currency")
    salary_interval = raw.get("interval")

    salary_text = None
    if salary_min is not None or salary_max is not None:
        curr = salary_currency or "INR"
        inter = f"/{salary_interval}" if salary_interval else ""
        if salary_min is not None and salary_max is not None:
            salary_text = f"{curr} {salary_min:,.0f} - {salary_max:,.0f}{inter}"
        elif salary_min is not None:
            salary_text = f"{curr} {salary_min:,.0f}+{inter}"
        else:
            salary_text = f"Up to {curr} {salary_max:,.0f}{inter}"

    # Employment type
    employment_type = clean_text_token(raw.get("job_type")) or None

    # Track search provenance
    keyword = clean_text_token(raw.get("test_keyword") or raw.get("search_term"))
    search_loc = clean_text_token(raw.get("test_location") or raw.get("search_location"))
    is_remote_query = bool(raw.get("test_is_remote", False))
    search_type = clean_text_token(raw.get("test_search_type") or ("remote_india" if is_remote_query else "city"))

    source_metadata = {
        "source": "linkedin",
        "source_job_id": source_job_id,
        "description_type": desc_type.value,
        "job_level": raw.get("job_level"),
        "job_function": raw.get("job_function"),
        "company_industry": raw.get("company_industry"),
        "company_url": raw.get("company_url"),
        "raw_date_posted": str(raw.get("date_posted")),
        "raw_work_mode_evidence": mode_evidence,
        "raw_remote_evidence": remote_evidence,
        "raw_experience_wording": exp_wording,
        "preferred_experience": pref_exp,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
        "salary_interval": str(salary_interval) if salary_interval else None,
        "salary_text": salary_text,
    }

    field_origins = {
        "title": FieldOrigin.SOURCE_PROVIDED,
        "company": FieldOrigin.SOURCE_PROVIDED,
        "location": FieldOrigin.SOURCE_PROVIDED,
        "description": FieldOrigin.SOURCE_PROVIDED if desc_type != DescriptionType.MISSING else FieldOrigin.UNKNOWN,
        "date_posted": FieldOrigin.SOURCE_PROVIDED if date_posted else FieldOrigin.UNKNOWN,
        "job_url": FieldOrigin.SOURCE_PROVIDED if job_url else FieldOrigin.UNKNOWN,
        "application_url": FieldOrigin.SOURCE_PROVIDED if application_url else FieldOrigin.UNKNOWN,
        "min_experience": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if min_exp is not None else FieldOrigin.UNKNOWN,
        "max_experience": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if max_exp is not None else FieldOrigin.UNKNOWN,
        "work_mode": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if mode_evidence else FieldOrigin.UNKNOWN,
        "remote_eligibility": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if remote_elig else FieldOrigin.UNKNOWN,
    }

    collected_at_str = datetime.now(timezone.utc).isoformat()
    raw_coll = raw.get("collected_at")
    if raw_coll:
        try:
            collected_at_str = datetime.fromisoformat(str(raw_coll)).isoformat()
        except ValueError:
            pass

    return JobRecord(
        source="linkedin",
        source_job_id=source_job_id,
        collection_method="python-jobspy",
        verification_status=VerificationStatus.UNVERIFIED,
        title=title,
        company=company,
        location=location or "India",
        country="India",
        work_mode=work_mode,
        remote_eligibility=remote_elig,
        description=description or None,
        min_experience=min_exp,
        max_experience=max_exp,
        mandatory_experience=exp_wording,
        preferred_experience=f"{pref_exp}+ years" if pref_exp else None,
        date_posted=date_posted,
        date_collected=collected_at_str,
        job_url=job_url,
        application_url=application_url,
        employment_type=employment_type,
        source_metadata=source_metadata,
        field_origins=field_origins,
        matched_keywords=[keyword] if keyword else [],
        matched_locations=[search_loc] if search_loc else [],
        matched_search_types=[search_type] if search_type else [],
        matched_run_ids=[run_id] if run_id else [],
    )
