"""
Indeed Data Extraction & Parsing Module.

Extracts structured fields from Indeed job payloads and descriptions:
- Experience requirements (mandatory min/max, preferred, raw wording)
- Work mode (onsite, hybrid, remote, unknown) and remote geographic eligibility
- Compensation details (min, max, currency, interval)
- URLs (source listing URL, employer direct application URL, verification status)
"""

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from models import FieldOrigin, WorkMode


WORD_NUMS = {
    "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0,
    "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0, "ten": 10.0,
}
_WORDS_PATTERN = "|".join(WORD_NUMS.keys())

# Regex patterns for experience extraction supporting ASCII and Unicode dashes & word numbers
EXP_PATTERNS = [
    # "2-5 years of experience", "2–5 yrs of direct operational security experience"
    re.compile(
        rf"(?:minimum\s+of\s+|at\s+least\s+)?(\d+(?:\.\d+)?|{_WORDS_PATTERN})\s*(?:[-–—~]|to)\s*(\d+(?:\.\d+)?|{_WORDS_PATTERN})\s*\+?\s*(?:years?|yrs?)(?:\s+(?:of|in|as\s+a?))?(?:\s+[\w\-/]+){{0,4}}\s*(?:experience|exp)?",
        re.I
    ),
    # "3+ years of experience", "5+ yrs of IT security experience", "10+ years in cybersecurity"
    re.compile(
        rf"(?:minimum\s+of\s+|at\s+least\s+|min\.?\s+)?(\d+(?:\.\d+)?|{_WORDS_PATTERN})\s*\+\s*(?:years?|yrs?)(?:\s+(?:of|in|as\s+a?))?(?:\s+[\w\-/]+){{0,4}}\s*(?:experience|exp)?",
        re.I
    ),
    # "Experience: 2 years", "Experience : 3-5 years", "Experience: 3–6 years"
    re.compile(
        rf"experience\s*:\s*(?:overall\s+)?(\d+(?:\.\d+)?|{_WORDS_PATTERN})(?:\s*(?:[-–—~]|to)\s*(\d+(?:\.\d+)?|{_WORDS_PATTERN}))?\s*\+?\s*(?:years?|yrs?)",
        re.I
    ),
    # "2 years of experience", "3 years as a Security Operations Analyst", "at least six years of experience"
    re.compile(
        rf"(?:minimum\s+of\s+|at\s+least\s+|min\.?\s+)?(\d+(?:\.\d+)?|{_WORDS_PATTERN})\s*(?:years?|yrs?)(?:\s+(?:of|in|as\s+a?))(?:\s+[\w\-/]+){{0,4}}\s*(?:experience|exp)?",
        re.I
    ),
]

# Noise patterns to exclude (company history, product longevity, education degree duration)
COMPANY_HISTORY_PATTERNS = [
    re.compile(r"(?:for|over|past)\s+\d+\s+years\s+(?:in\s+business|of\s+history|delivering|operating|serving|experience\s+in\s+providing)", re.I),
    re.compile(r"(?:founded|established)\s+\d+\s+years\s+ago", re.I),
    re.compile(r"more\s+than\s+\d+\s+years\s+ago", re.I),
    re.compile(r"done\s+that\s+for\s+\d+\s+years", re.I),
    re.compile(r"heritage\s+of\s+\d+\s+years", re.I),
    re.compile(r"(?:degree|diploma|graduation)\s*\(\s*\d+(?:\s*(?:[-–—~]|to)\s*\d+)?\s*(?:years?|yrs?)\s*\)", re.I),
    re.compile(r"\b\d+\s*(?:[-–—~]|to)\s*\d+\s*(?:year|yr)\s+(?:degree|diploma|program|course)\b", re.I),
    re.compile(r"\b\d+\s*(?:year|yr)\s+(?:degree|diploma)\b", re.I),
]

PREFERRED_KEYWORDS = [
    "preferred", "good to have", "plus", "desirable", "bonus",
    "advantage", "nice to have", "ideal", "preference"
]

MANDATORY_KEYWORDS = [
    "required", "must have", "minimum", "mandatory", "essential",
    "basic qualifications", "prerequisites"
]


def parse_exp_num(val: Any) -> Optional[float]:
    """Parse number string or number word into float."""
    if not val:
        return None
    s = str(val).strip().lower()
    if s in WORD_NUMS:
        return WORD_NUMS[s]
    try:
        f = float(s)
        return f if 0.5 <= f <= 35 else None
    except (ValueError, TypeError):
        return None


def extract_experience(
    description: Optional[str]
) -> Tuple[Optional[float], Optional[float], Optional[str], Optional[str], Optional[str]]:
    """
    Extract experience requirements from job description.
    Returns:
        (min_experience, max_experience, mandatory_experience, preferred_experience, experience_raw_text)
    Never assumes unmentioned experience is 0.
    """
    if not description or not isinstance(description, str):
        return None, None, None, None, None

    # Split into lines and sentences
    lines = description.split("\n")
    candidate_matches = []

    current_section = "general"

    for line in lines:
        cleaned_line = line.strip()
        if not cleaned_line:
            continue

        lower_line = cleaned_line.lower()

        # Check section headers
        if any(h in lower_line for h in ["preferred qualification", "nice to have", "desired skill", "bonus point"]):
            current_section = "preferred"
        elif any(h in lower_line for h in ["basic qualification", "minimum qualification", "requirement", "must have"]):
            current_section = "mandatory"

        # Check if line contains company history noise
        if any(p.search(cleaned_line) for p in COMPANY_HISTORY_PATTERNS):
            continue

        for pattern in EXP_PATTERNS:
            match = pattern.search(cleaned_line)
            if match:
                groups = match.groups()
                # Ensure it's not a generic false positive like "within 30 days"
                val1 = parse_exp_num(groups[0])
                if val1 is not None:
                    val2 = parse_exp_num(groups[1]) if len(groups) > 1 and groups[1] else None
                    is_preferred = (
                        current_section == "preferred"
                        or any(kw in lower_line for kw in PREFERRED_KEYWORDS)
                    )
                    candidate_matches.append({
                        "min": val1,
                        "max": val2,
                        "text": cleaned_line,
                        "is_preferred": is_preferred,
                    })

    if not candidate_matches:
        return None, None, None, None, None

    # Prioritize mandatory/general requirements over preferred
    mandatory_candidates = [m for m in candidate_matches if not m["is_preferred"]]
    preferred_candidates = [m for m in candidate_matches if m["is_preferred"]]

    min_exp = None
    max_exp = None
    mandatory_text = None
    preferred_text = None
    raw_text_parts = []

    if mandatory_candidates:
        # Sort by min experience to find baseline minimum
        mandatory_candidates.sort(key=lambda x: x["min"])
        best = mandatory_candidates[0]
        min_exp = best["min"]
        max_exp = best["max"]
        mandatory_text = best["text"]
        raw_text_parts.append(best["text"])

    if preferred_candidates:
        preferred_candidates.sort(key=lambda x: x["min"], reverse=True)
        best_pref = preferred_candidates[0]
        preferred_text = best_pref["text"]
        raw_text_parts.append(best_pref["text"])
        if min_exp is None:
            # If only preferred experience was mentioned, keep min_exp None to avoid false mandatory classification
            pass

    raw_text = " | ".join(raw_text_parts) if raw_text_parts else None

    return min_exp, max_exp, mandatory_text, preferred_text, raw_text


def extract_work_mode(
    location_str: Optional[str],
    description: Optional[str],
    is_remote_flag: Optional[bool]
) -> Tuple[WorkMode, Optional[str], Optional[str]]:
    """
    Classify work mode and remote eligibility:
    Returns:
        (work_mode, remote_eligibility, geographic_restrictions)
    """
    loc_clean = str(location_str or "").strip()
    loc_lower = loc_clean.lower()
    desc_clean = str(description or "")
    desc_lower = desc_clean.lower()

    work_mode = WorkMode.UNKNOWN
    remote_eligibility = None
    geographic_restrictions = None

    # 1. Determine Work Mode
    # Check for hybrid explicitly first (to avoid false "remote" match on "hybrid remote")
    is_hybrid = False
    if "hybrid" in loc_lower:
        is_hybrid = True
    elif re.search(r"\b(?:hybrid\s+work|hybrid\s+model|hybrid\s+working|hybrid\s+remote|workplace\s+type:\s*hybrid|work\s+location:\s*.*hybrid|#li\s*#?\s*hybrid|india\s*\|\s*hybrid)\b", desc_lower):
        is_hybrid = True
    elif re.search(r"\b(?:2|3)\s*days\s+(?:in\s+office|from\s+office|on-site)\b", desc_lower):
        is_hybrid = True
    elif re.search(r"\bspend\s+at\s+least\s+\d+%\s+of\s+(?:their\s+)?time\s+in-office\b", desc_lower):
        is_hybrid = True
    elif re.search(r"\bhybrid\s*\(\s*remote\s*/\s*on\s*site\s*\)\b", desc_lower):
        is_hybrid = True
    elif re.search(r"\bwork\s*model\s*:\s*hybrid\b", desc_lower):
        is_hybrid = True

    if is_hybrid:
        work_mode = WorkMode.HYBRID
    elif is_remote_flag is True or "remote in" in loc_lower or loc_lower == "remote":
        work_mode = WorkMode.REMOTE
    elif is_remote_flag is False and loc_clean and loc_lower != "unknown":
        work_mode = WorkMode.ONSITE
    else:
        work_mode = WorkMode.UNKNOWN

    # 2. Determine Remote Eligibility & Geographic Restrictions
    if work_mode in (WorkMode.REMOTE, WorkMode.HYBRID) or is_remote_flag is True:
        # Check India eligibility
        has_india_mention = (
            "india" in loc_lower
            or "remote in india" in loc_lower
            or re.search(r"\b(?:candidates?\s+in\s+india|based\s+in\s+india|work\s+from\s+india|india\s+residents?)\b", desc_lower)
            or any(city in loc_lower for city in ["bengaluru", "hyderabad", "chennai", "pune", "mumbai", "gurugram", "noida", "kochi", "coimbatore"])
        )

        has_foreign_restriction = (
            re.search(r"\b(?:us\s+citizens?|united\s+states\s+only|must\s+reside\s+in\s+(?:us|usa|uk|canada)|emea\s+only)\b", desc_lower)
            or ("remote in usa" in loc_lower or "remote in us" in loc_lower)
        )

        has_anywhere_mention = bool(
            re.search(r"\b(?:work\s+from\s+anywhere|global\s+remote|anywhere\s+in\s+the\s+world)\b", desc_lower)
        )

        if has_foreign_restriction:
            remote_eligibility = "country_restricted"
            geographic_restrictions = "Non-India / Foreign restricted"
        elif has_anywhere_mention:
            remote_eligibility = "location_independent"
            geographic_restrictions = "Worldwide / Anywhere"
        elif has_india_mention:
            remote_eligibility = "india_only"
            geographic_restrictions = "India"
        else:
            remote_eligibility = "unknown"
            geographic_restrictions = None

    return work_mode, remote_eligibility, geographic_restrictions


def extract_indeed_job_key(raw_id: Optional[str], job_url: Optional[str]) -> Optional[str]:
    """
    Extract canonical Indeed job key (jk) from raw ID or URL.
    Examples:
        'in-587eb9fc6b17e8d9' -> '587eb9fc6b17e8d9'
        'https://in.indeed.com/viewjob?jk=587eb9fc6b17e8d9' -> '587eb9fc6b17e8d9'
    """
    if raw_id:
        s = str(raw_id).strip()
        if s.startswith("in-"):
            return s[3:].strip()
        match = re.search(r"^[a-f0-9]{16}$", s, re.I)
        if match:
            return s.lower()

    if job_url:
        try:
            parts = urlsplit(str(job_url).strip())
            query = parse_qs(parts.query)
            for key in ["jk", "vjk"]:
                if key in query and query[key]:
                    return query[key][0].strip().lower()
        except Exception:
            pass

    return None


def extract_salary_info(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract standardized salary/compensation info from raw Indeed payload.
    """
    min_amt = raw.get("min_amount")
    max_amt = raw.get("max_amount")
    currency = raw.get("currency")
    interval = raw.get("interval")

    def clean_num(val):
        if val is None:
            return None
        try:
            f = float(val)
            return f if f > 0 else None
        except (ValueError, TypeError):
            return None

    def clean_str(val):
        if not val:
            return None
        s = str(val).strip()
        return s if s and s.lower() not in ("none", "nan", "null") else None

    return {
        "min_amount": clean_num(min_amt),
        "max_amount": clean_num(max_amt),
        "currency": clean_str(currency),
        "interval": clean_str(interval),
        "has_salary": bool(clean_num(min_amt) or clean_num(max_amt)),
    }
