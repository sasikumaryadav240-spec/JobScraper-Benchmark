"""
Cybersecurity Role and Location Filtering Engine for ATS collections.

Ensures only genuine cybersecurity positions relevant to India or India-eligible
remote candidates are collected from employer ATS boards.
"""

from __future__ import annotations

import re
from typing import Any

from ats_config import (
    CANONICAL_CYBERSECURITY_ROLES,
    EXTENDED_CYBERSECURITY_KEYWORDS,
    NEGATIVE_ROLE_EXCLUSIONS,
    TARGET_INDIAN_LOCATIONS,
)


def _clean_str(val: Any) -> str:
    if not val:
        return ""
    return re.sub(r"\s+", " ", str(val).strip().lower())


def is_cybersecurity_role(title: str, description: str | None = None) -> tuple[bool, str | None]:
    """
    Determine if a job title represents a genuine cybersecurity role.
    Excludes non-security roles (e.g. financial analyst, security guard).
    Returns (is_relevant, matched_keyword).
    """
    if not title:
        return False, None

    title_clean = _clean_str(title)

    # 1. Negative title exclusions
    for neg in NEGATIVE_ROLE_EXCLUSIONS:
        if re.search(r"\b" + re.escape(neg) + r"\b", title_clean):
            return False, None

    # 2. Canonical cybersecurity roles (highest confidence)
    for canonical in CANONICAL_CYBERSECURITY_ROLES:
        if canonical.lower() in title_clean:
            return True, canonical

    # 3. Extended cybersecurity keywords
    for ext in EXTENDED_CYBERSECURITY_KEYWORDS:
        if re.search(r"\b" + re.escape(ext) + r"\b", title_clean):
            return True, ext

    # 4. Description fallback for ambiguous titles
    if description and any(t in title_clean for t in ["security", "analyst", "engineer", "associate"]):
        desc_clean = _clean_str(description)
        high_signal_terms = [
            "siem", "soc analyst", "incident response", "threat intelligence",
            "edr", "vulnerability management", "penetration testing",
            "application security", "cloud security", "infosec", "grc",
        ]
        for term in high_signal_terms:
            if re.search(r"\b" + re.escape(term) + r"\b", desc_clean):
                return True, f"description:{term}"

    return False, None


def is_india_or_remote_eligible(
    location_str: str,
    description: str | None = None,
    is_remote_flag: bool | None = None,
) -> tuple[bool, str, str | None]:
    """
    Evaluate whether a position is located in India or is an India-eligible remote role.
    Returns (is_eligible, standardized_location, remote_eligibility).
    Remote eligibility: 'india_only', 'international', 'country_restricted', or None.
    """
    loc_clean = _clean_str(location_str)
    desc_clean = _clean_str(description) if description else ""

    is_remote = bool(
        is_remote_flag or
        "remote" in loc_clean or
        "anywhere" in loc_clean or
        "work from home" in loc_clean or
        re.search(r"\b(?:fully\s+remote|100%\s+remote|work\s+from\s+home)\b", desc_clean)
    )

    # If remote, evaluate remote eligibility first
    if is_remote:
        # Check if India is explicitly included in remote scope
        has_india = any(
            re.search(r"\b" + re.escape(target) + r"\b", loc_clean) or
            re.search(r"\b" + re.escape(target) + r"\b", desc_clean)
            for target in TARGET_INDIAN_LOCATIONS
        )
        if has_india:
            return True, location_str, "india_only"

        # Check for restricted foreign remote (e.g. US only, EMEA only, UK only)
        restricted_patterns = [
            r"\b(?:us[\s-]only|united\s+states\s+only|must\s+reside\s+in\s+(?:the\s+)?us)\b",
            r"\b(?:canada[\s-]only|uk[\s-]only|eu[\s-]only|emea[\s-]only|latam[\s-]only)\b",
            r"\b(?:eligible\s+to\s+work\s+in\s+(?:the\s+)?(?:us|united\s+states|uk))\b",
        ]
        for rp in restricted_patterns:
            if re.search(rp, loc_clean) or re.search(rp, desc_clean):
                return False, location_str, "country_restricted"

        # Check for worldwide / international remote
        intl_patterns = [
            r"\b(?:anywhere\s+in\s+the\s+world|globally\s+remote|international\s+remote|worldwide|global\s+remote|remote,\s*global)\b",
            r"\b(?:remote\s*[-–(]?\s*global|global\s*[-–(]?\s*remote)\b",
        ]
        for ip in intl_patterns:
            if re.search(ip, loc_clean) or re.search(ip, desc_clean):
                return True, location_str, "international"

        return False, location_str, None

    # For non-remote, check for direct Indian location match
    for target in TARGET_INDIAN_LOCATIONS:
        if re.search(r"\b" + re.escape(target) + r"\b", loc_clean):
            return True, location_str, None

    return False, location_str, None
