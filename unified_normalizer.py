"""
Lossless Normalization Engine for Canonical Job Records.

Normalizes titles, companies, locations, work modes, dates, URLs,
and experience ranges while preserving all raw values and provenance.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from unified_models import DescriptionType, RemoteEligibilityEnum, WorkModeEnum


def normalize_job_title(title: str | None) -> str:
    """Standardize job title by removing noisy prefixes/suffixes while preserving semantics."""
    if not title:
        return ""
    text = html.unescape(str(title).strip())
    # Remove bracketed tags like [Remote], (Hybrid), (Urgent), - Full Time
    text = re.sub(r"\[(?:remote|hybrid|onsite|urgent|immediate)\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\((?:remote|hybrid|onsite|urgent|immediate|full[- ]time|part[- ]time)\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[-–—]\s*(?:remote|hybrid|onsite|full[- ]time|india)\s*$", "", text, flags=re.IGNORECASE)
    # Collapse excess whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_company_name(company: str | None) -> str:
    """Standardize company name by stripping legal suffixes and normalizing whitespace."""
    if not company:
        return ""
    text = html.unescape(str(company).strip())
    # Remove common corporate suffixes
    pattern = r"\b(?:pvt\.?\s*ltd\.?|private\s*limited|ltd\.?|inc\.?|incorporated|llc|corp\.?|corporation|technologies|solutions|services|group)\b"
    cleaned = re.sub(pattern, "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else text.strip()


def normalize_canonical_url(url: str | None) -> str:
    """Normalize URLs by stripping tracking query params while preserving identifying query parameters."""
    if not url:
        return ""
    s = str(url).strip()
    try:
        parts = urlsplit(s)
        host = parts.netloc.lower().removeprefix("www.")
        path = parts.path.rstrip("/")
        if not path and parts.netloc:
            path = "/"

        query_params = parse_qsl(parts.query, keep_blank_values=True)
        # Tracking params to drop
        drop_params = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "ref", "referer", "referrer", "fbclid", "gclid", "msclkid",
            "indeed-apply-token", "iis", "iisn",
        }
        filtered_params = [(k, v) for k, v in query_params if k.lower() not in drop_params]
        sorted_query = urlencode(sorted(filtered_params))
        return urlunsplit((parts.scheme or "https", host, path, sorted_query, ""))
    except Exception:
        return s


def normalize_date_to_iso(val: Any) -> tuple[Optional[str], str]:
    """
    Parse a date value into UTC ISO 8601 string and return (iso_string, verification_status).
    """
    if val is None or val == "":
        return None, "unknown"
    if isinstance(val, (int, float)):
        try:
            ts = val / 1000.0 if val > 1e11 else float(val)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.isoformat(), "source_provided"
        except Exception:
            return None, "unknown"

    s = str(val).strip()
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(), "source_provided"
    except ValueError:
        pass

    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat(), "parsed"
        except ValueError:
            continue

    return None, "unknown"


def classify_description_quality(desc: str | None, current_type: str | None = None) -> str:
    """Classify description quality into full, snippet, partial, or missing."""
    if not desc or not str(desc).strip():
        return DescriptionType.MISSING.value
    clean = str(desc).strip()
    if current_type in [t.value for t in DescriptionType]:
        return current_type

    # Snippet indicators (e.g. ends with ellipsis, or short search snippets under 450 chars)
    if len(clean) < 450 and ("..." in clean or "…" in clean):
        return DescriptionType.SEARCH_RESULT_SNIPPET.value
    if len(clean) < 500:
        return DescriptionType.PARTIAL_DESCRIPTION.value
    return DescriptionType.FULL_DESCRIPTION.value


# Work mode classification
REMOTE_EXCLUSIONS = re.compile(
    r"\b(?:remote\s+access|remote\s+monitoring|remote\s+management|remote\s+support|"
    r"remote\s+desktop|remote\s+hands|remote\s+administration|remote\s+execution|"
    r"remote\s+code\s+execution|remote\s+troubleshooting)\b",
    re.IGNORECASE,
)
HYBRID_EXCLUSIONS = re.compile(
    r"\b(?:hybrid\s+cloud|hybrid\s+infrastructure|hybrid\s+model|hybrid\s+environment|"
    r"hybrid\s+architecture|hybrid\s+deployment|hybrid\s+network|hybrid\s+identity)\b",
    re.IGNORECASE,
)
ONSITE_EXCLUSIONS = re.compile(
    r"\b(?:on-?site\s+audit|on-?site\s+incident|on-?site\s+client\s+visit|"
    r"on-?site\s+support\s+as\s+needed|on-?site\s+inspection)\b",
    re.IGNORECASE,
)

EXPLICIT_REMOTE = re.compile(
    r"\b(?:100%\s*remote|fully\s*remote|work\s+from\s+home|wfh|remote\s+work|"
    r"remote\s+position|remote\s+role|remote\s+job|permanent\s+remote|"
    r"remote\s*-\s*india|india\s*-\s*remote|location:\s*remote)\b",
    re.IGNORECASE,
)
EXPLICIT_HYBRID = re.compile(
    r"\b(?:hybrid\s+work|hybrid\s+role|hybrid\s+position|hybrid\s+schedule|"
    r"days?\s+in\s+office|days?\s+from\s+home|in-?office\s+and\s+remote|"
    r"work\s+mode:\s*hybrid|model:\s*hybrid)\b",
    re.IGNORECASE,
)
EXPLICIT_ONSITE = re.compile(
    r"\b(?:work\s+from\s+office|wfo|100%\s*in-?office|on-?site\s+only|"
    r"on-?site\s+work|on-?site\s+role|on-?site\s+position|location:\s*in\s*person|"
    r"work\s+location:\s*in\s*person|mandatory\s+in-?office)\b",
    re.IGNORECASE,
)


def extract_work_mode_evidence(title: str, location: str, desc: str) -> tuple[str, Optional[str]]:
    """Determine work mode strictly from evidence."""
    title_loc = f"{title} {location}".lower()
    full_text = f"{title}\n{location}\n{desc}"

    # Check title / location first
    if "remote" in title_loc and not REMOTE_EXCLUSIONS.search(title_loc):
        return WorkModeEnum.REMOTE.value, "title/location explicitly mentions remote"
    if "hybrid" in title_loc and not HYBRID_EXCLUSIONS.search(title_loc):
        return WorkModeEnum.HYBRID.value, "title/location explicitly mentions hybrid"
    if any(k in title_loc for k in ("on-site", "onsite", "in-person", "work from office", "wfo")):
        return WorkModeEnum.ONSITE.value, "title/location explicitly mentions onsite"

    # Check description with false positive exclusion
    clean_desc = REMOTE_EXCLUSIONS.sub("", full_text)
    clean_desc = HYBRID_EXCLUSIONS.sub("", clean_desc)
    clean_desc = ONSITE_EXCLUSIONS.sub("", clean_desc)

    m_hyb = EXPLICIT_HYBRID.search(clean_desc)
    if m_hyb:
        return WorkModeEnum.HYBRID.value, f"description match: {m_hyb.group(0)}"

    m_rem = EXPLICIT_REMOTE.search(clean_desc)
    if m_rem:
        return WorkModeEnum.REMOTE.value, f"description match: {m_rem.group(0)}"

    m_ons = EXPLICIT_ONSITE.search(clean_desc)
    if m_ons:
        return WorkModeEnum.ONSITE.value, f"description match: {m_ons.group(0)}"

    return WorkModeEnum.UNKNOWN.value, None


def normalize_location_details(loc_str: str | None) -> tuple[str, Optional[str], Optional[str], str]:
    """Parse raw location string into (normalized_location, city, state, country)."""
    if not loc_str:
        return "India", None, None, "India"
    s = html.unescape(str(loc_str).strip())
    s_clean = re.sub(r"\s+", " ", s).strip()

    cities = [
        "Bengaluru", "Bangalore", "Hyderabad", "Pune", "Chennai", "Mumbai",
        "Gurugram", "Gurgaon", "Noida", "Delhi", "New Delhi", "Kolkata",
        "Ahmedabad", "Kochi", "Coimbatore", "Chandigarh", "Jaipur",
    ]
    matched_city = None
    for c in cities:
        if re.search(rf"\b{c}\b", s_clean, re.IGNORECASE):
            matched_city = "Bengaluru" if c.lower() in ("bengaluru", "bangalore") else (
                "Gurugram" if c.lower() in ("gurugram", "gurgaon") else c
            )
            break

    states = [
        "Karnataka", "Telangana", "Maharashtra", "Tamil Nadu", "Delhi",
        "Haryana", "Uttar Pradesh", "West Bengal", "Gujarat", "Kerala",
    ]
    matched_state = None
    for st in states:
        if re.search(rf"\b{st}\b", s_clean, re.IGNORECASE):
            matched_state = st
            break

    return s_clean, matched_city, matched_state, "India"

