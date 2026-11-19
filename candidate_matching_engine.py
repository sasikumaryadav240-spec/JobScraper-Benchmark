"""
Deterministic Candidate Skill Matching & Eligibility Engine for Phase 5.

Implements the authoritative candidate skills profile, transparent weighted scoring,
strict > 60.0% threshold, exact experience eligibility rules, education rules,
and multi-category classification.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from candidate_profile import CANDIDATE_PROFILE
from unified_models import CanonicalJobRecord, EligibilityStatus, JobCategory

logger = logging.getLogger("CandidateMatchingEngine")

# Regex for detecting mandatory experience phrases
SENIOR_ROLE_RE = re.compile(
    r"\b(?:manager|architect|director|vice\s*president|vp\b|head\s*of|principal|senior\s*lead|staff\s*engineer)\b",
    re.IGNORECASE,
)

PROFESSIONAL_YEARS_RE = re.compile(
    r"\b(\d+)\+?\s*(?:years?|yrs?)\b.*?(?:professional|work|production|enterprise|prior|proven|industry|relevant)\s*(?:experience|exp)\b|"
    r"\b(?:minimum|at\s*least|min\.?)\s*(\d+)\+?\s*(?:years?|yrs?)\b.*?(?:soc|cyber|security|infosec|incident|threat)",
    re.IGNORECASE,
)

FRESHER_ACCEPTANCE_RE = re.compile(
    r"\b(?:freshers?\s*(?:are\s*)?(?:welcome|eligible|can\s*apply)|entry[- ]level|no\s*experience\s*required|"
    r"college\s*graduates?|campus\s*hire|graduate\s*trainee|0[- ](?:to[- ])?1\s*years?)\b",
    re.IGNORECASE,
)

CS_IT_ONLY_RE = re.compile(
    r"\b(?:b\.?e\.?|b\.?tech\.?|degree|bachelor'?s)\s*(?:in|of)?\s*(?:computer\s*science|cs|it|information\s*technology)\s*(?:only|mandatory|required)\b|"
    r"\b(?:only\s*(?:cs|it|computer\s*science)\s*(?:graduates?|engineers?))\b",
    re.IGNORECASE,
)

ANY_ENGINEERING_RE = re.compile(
    r"\b(?:any\s*engineering\s*graduate|b\.?e\.?|b\.?tech\.?|bachelor'?s\s*in\s*engineering|engineering\s*degree|"
    r"ece|electronics\s*and\s*communication|electronics|telecommunication)\b",
    re.IGNORECASE,
)

BATCH_RESTRICTION_RE = re.compile(
    r"\b(?:2024|2025)\s*(?:batch|passout|graduates?|only)\b",
    re.IGNORECASE,
)


def _alias_regex(pattern: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(pattern.strip()) + r"(?![A-Za-z0-9])", re.IGNORECASE)


class ProfileMatcher:
    def __init__(self, profile: dict[str, Any] = CANDIDATE_PROFILE):
        self.profile = profile
        self.caps = {
            k: float(v.get("max_match", 0.0)) if isinstance(v, dict) else float(v)
            for k, v in profile.get("evidence_levels", {}).items()
        }

        self.skills: list[dict[str, Any]] = []
        alias_map: dict[str, list[int]] = {}

        for i, s in enumerate(profile.get("skills", [])):
            names = [s["name"], *s.get("aliases", [])]
            level = s.get("evidence_level", "none")
            cap = self.caps.get(level, 0.0)
            self.skills.append({
                "name": s["name"],
                "category": s.get("category", ""),
                "level": level,
                "cap": cap,
            })
            for n in names:
                if n and len(n.strip()) >= 2:
                    alias_map.setdefault(n.strip().lower(), []).append(i)

        self._alias_map = alias_map
        alts = sorted(alias_map.keys(), key=len, reverse=True)
        self._master_regex = (
            re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(a) for a in alts) + r")(?![A-Za-z0-9])", re.IGNORECASE)
            if alts else None
        )

        self.restrictions = [
            {
                "id": r["id"],
                "description": r.get("description", ""),
                "max_match": float(r.get("max_match", 0.4)),
                "patterns": [re.compile(r"(?<![A-Za-z0-9])" + re.escape(p) + r"(?![A-Za-z0-9])", re.IGNORECASE) for p in r.get("patterns", [])],
            }
            for r in profile.get("restrictions", [])
        ]

    def match_skills(self, text: str) -> list[dict[str, Any]]:
        """Find candidate skills mentioned in text with supported evidence levels and match values."""
        if not text or self._master_regex is None:
            return []

        matched_indices: set[int] = set()
        for m in self._master_regex.finditer(text):
            matched_indices.update(self._alias_map.get(m.group(0).lower(), ()))

        results = []
        for idx in sorted(matched_indices):
            s = self.skills[idx]
            match_val = s["cap"]
            # Check restrictions
            for r in self.restrictions:
                if any(p.search(text) for p in r["patterns"]):
                    if match_val > r["max_match"]:
                        match_val = r["max_match"]

            results.append({
                "name": s["name"],
                "category": s["category"],
                "evidence_level": s["level"],
                "match_value": match_val,
            })
        return results


MATCHER = ProfileMatcher()


def calculate_job_scores(
    record: CanonicalJobRecord,
    profile_matcher: ProfileMatcher = MATCHER,
) -> tuple[float, float, list[dict[str, Any]], list[str]]:
    """
    Calculate Technical Skill Match Score and Overall Match Score.
    Default weights: Mandatory 60%, Responsibilities 25%, Preferred 15%.
    """
    full_text = f"{record.job_title}\n{record.job_description}"
    matched = profile_matcher.match_skills(full_text)

    if not matched:
        # Check core generic terms
        return 0.0, 0.0, [], ["Core cybersecurity skills not detected in job description"]

    # Calculate average technical skill score
    avg_skill_val = sum(s["match_value"] for s in matched) / len(matched)
    tech_score = round(avg_skill_val * 100.0, 2)

    # Weight distribution
    # Check if preferred requirements section is explicitly detected
    has_preferred = bool(re.search(r"\b(?:preferred|nice[- ]to[- ]have|good[- ]to[- ]have|plus)\b", full_text, re.IGNORECASE))

    # High-relevance security skills (SIEM, SOC, EDR, Vulnerability Management)
    core_sec_skills = [s for s in matched if s["category"] in ("SIEM", "SOC Operations", "EDR", "Vulnerability Management")]
    core_sec_avg = (sum(s["match_value"] for s in core_sec_skills) / len(core_sec_skills)) if core_sec_skills else avg_skill_val

    # Responsibilities/duty alignment
    resp_skills = [s for s in matched if s["category"] in ("Networking", "Operating Systems", "Scripting", "Security Fundamentals")]
    resp_avg = (sum(s["match_value"] for s in resp_skills) / len(resp_skills)) if resp_skills else avg_skill_val

    # Preferred skills
    pref_skills = [s for s in matched if s["category"] in ("Cloud Security", "Containers", "Development")]
    pref_avg = (sum(s["match_value"] for s in pref_skills) / len(pref_skills)) if pref_skills else avg_skill_val

    if has_preferred:
        mand_points = core_sec_avg * 60.0
        resp_points = resp_avg * 25.0
        pref_points = pref_avg * 15.0
        overall_score = round(mand_points + resp_points + pref_points, 2)
    else:
        # Transparent normalization across mandatory (60/85 = 70.59%) and responsibilities (25/85 = 29.41%)
        mand_points = core_sec_avg * (60.0 / 85.0 * 100.0)
        resp_points = resp_avg * (25.0 / 85.0 * 100.0)
        overall_score = round(mand_points + resp_points, 2)

    # Missing skills analysis
    matched_names = {s["name"].lower() for s in matched}
    key_target_skills = ["splunk", "microsoft sentinel", "kql", "siem", "wireshark", "nessus", "linux"]
    missing = [k.title() for k in key_target_skills if k not in matched_names]

    return tech_score, overall_score, matched, missing


def evaluate_experience_eligibility(record: CanonicalJobRecord) -> tuple[str, list[str]]:
    """
    Evaluate experience eligibility according to strict Phase 5 rules:
    ACCEPT:
      - Mandatory min experience <= 2 years (0, 1, 2)
      - Ranges: 0-3, 1-3, 2-5, 2-7 (where min <= 2)
    REJECT:
      - Mandatory min experience >= 3 years
    REVIEW:
      - Experience unmentioned (never infer 0 years)
      - Ambiguous / conflicting statements
    """
    min_exp = record.mandatory_min_experience
    max_exp = record.mandatory_max_experience
    desc = record.job_description.lower()

    # Check fresher keywords
    if FRESHER_ACCEPTANCE_RE.search(desc) or "fresher" in desc:
        if min_exp is None or min_exp <= 2.0:
            return "PASS", ["Freshers / entry-level explicitly accepted"]

    # Explicit numeric minimum
    if min_exp is not None:
        if min_exp >= 3.0:
            return "EXCLUDE", [f"Mandatory minimum experience {min_exp:g}+ years exceeds limit of 2 years"]
        if min_exp <= 2.0:
            rng_str = f"Range {min_exp:g}-{max_exp:g} years" if max_exp else f"{min_exp:g} years minimum"
            return "PASS", [f"Mandatory experience acceptable: {rng_str} (<= 2 years)"]

    # Check if range is mentioned in text without parsed numbers
    if record.original_experience_text:
        text = record.original_experience_text.lower()
        if any(r in text for r in ("0-3", "1-3", "2-5", "2-7", "0 to 3", "1 to 3", "2 to 5", "2 to 7", "0-2", "1-2")):
            return "PASS", [f"Advertised range within acceptable threshold: {record.original_experience_text}"]
        if any(r in text for r in ("3+", "3-5", "4+", "5+", "6+", "7+", "8+", "10+")):
            return "EXCLUDE", [f"Mandatory experience in text {record.original_experience_text} exceeds 2 years limit"]

    # Unmentioned or ambiguous
    if min_exp is None and not record.original_experience_text:
        return "REVIEW", ["Experience requirement not stated in job posting — requires manual verification"]

    return "REVIEW", ["Experience requirement ambiguous — requires manual verification"]


def evaluate_education_eligibility(record: CanonicalJobRecord) -> tuple[str, list[str]]:
    """
    Evaluate education eligibility:
    - CS/IT only -> EXCLUDE (Candidate is ECE)
    - CS/IT or related -> REVIEW
    - Any engineering / ECE -> PASS
    - Batch 2024/2025 only -> EXCLUDE (Candidate is Class of 2026)
    """
    desc = record.job_description.lower()

    # Batch check
    if BATCH_RESTRICTION_RE.search(desc):
        return "EXCLUDE", ["Graduation batch restricted to 2024/2025; candidate graduates in 2026"]

    # CS/IT only restriction
    if CS_IT_ONLY_RE.search(desc):
        return "EXCLUDE", ["Degree restricted to CS/IT only; candidate holds B.E. ECE"]

    # Any Engineering or ECE explicitly accepted
    if ANY_ENGINEERING_RE.search(desc):
        return "PASS", ["Engineering degree / ECE explicitly eligible"]

    # If general degree mentioned
    if "degree" in desc or "bachelor" in desc or "b.e" in desc or "b.tech" in desc:
        if "computer science" in desc or "information technology" in desc:
            return "REVIEW", ["Degree asks for CS/IT or related field — verify ECE acceptance"]
        return "PASS", ["Degree requirement is general or non-restrictive"]

    return "REVIEW", ["Education requirements not explicitly stated — verify candidate degree eligibility"]


def evaluate_job_category(record: CanonicalJobRecord, overall_score: float, is_pass: bool) -> str:
    """Classify record into one of 6 mutually exclusive categories."""
    title_lower = record.job_title.lower()

    # Internships
    if "intern" in title_lower or record.internship_status or "trainee" in title_lower:
        return JobCategory.INTERNSHIP.value

    # Supporting IT
    supporting_keywords = [
        "network engineer", "systems administrator", "system administrator",
        "cloud engineer", "devops engineer", "infrastructure engineer",
        "linux administrator", "site reliability", "sre", "database administrator",
    ]
    if any(k in title_lower for k in supporting_keywords):
        return JobCategory.SUPPORTING_IT.value

    # Direct Cybersecurity
    direct_cyber_keywords = [
        "soc", "security analyst", "cybersecurity", "cyber security",
        "information security", "infosec", "incident response", "siem",
        "vulnerability", "threat", "penetration", "red team", "blue team",
        "appsec", "application security", "cloud security", "grc", "iam",
    ]
    is_direct_cyber = any(k in title_lower for k in direct_cyber_keywords)

    if not is_direct_cyber:
        return JobCategory.OTHER_POTENTIALLY_RELEVANT.value

    if is_pass and overall_score > 60.0:
        return JobCategory.DIRECT_CYBERSECURITY.value
    elif not is_pass:
        return JobCategory.EXCLUDED.value
    else:
        return JobCategory.MANUAL_VERIFICATION.value


def evaluate_candidate_matching(record: CanonicalJobRecord, recalculate_scores: bool = True) -> CanonicalJobRecord:
    """
    Run full deterministic candidate matching and eligibility pipeline on a record.
    """
    if recalculate_scores:
        tech_score, overall_score, matched_skills, missing_skills = calculate_job_scores(record)
        record.technical_skill_match_score = tech_score
        record.overall_match_score = overall_score
        record.matched_skills = matched_skills
        record.missing_skills = missing_skills
    else:
        overall_score = record.overall_match_score

    rejections: list[str] = []
    reviews: list[str] = []

    # 1. Score Threshold: STRICTLY GREATER THAN 60.0%
    if overall_score <= 60.0:
        rejections.append(f"Overall match score {overall_score:.2f}% does not strictly exceed the 60.0% threshold")

    # 2. Experience Eligibility
    exp_status, exp_reasons = evaluate_experience_eligibility(record)
    if exp_status == "EXCLUDE":
        rejections.extend(exp_reasons)
    elif exp_status == "REVIEW":
        reviews.extend(exp_reasons)

    # 3. Education Eligibility
    edu_status, edu_reasons = evaluate_education_eligibility(record)
    if edu_status == "EXCLUDE":
        rejections.extend(edu_reasons)
    elif edu_status == "REVIEW":
        reviews.extend(edu_reasons)

    # 4. Mandatory Professional Security Experience Guardrail
    desc_lower = record.job_description.lower()
    if PROFESSIONAL_YEARS_RE.search(desc_lower) and not FRESHER_ACCEPTANCE_RE.search(desc_lower):
        m_years = PROFESSIONAL_YEARS_RE.search(desc_lower)
        yr_val = m_years.group(1) or m_years.group(2) if m_years else None
        if yr_val and float(yr_val) >= 2.0:
            rejections.append(
                f"Mandatory professional cybersecurity/SOC experience required ({yr_val}+ yrs); candidate possesses 0 professional security years"
            )

    # 5. Seniority and Role-Level Suitability (Manager/Architect Exclusions)
    title_lower = record.job_title.lower()
    if SENIOR_ROLE_RE.search(title_lower) and not any(k in title_lower for k in ("intern", "trainee", "associate")):
        rejections.append(
            f"Role seniority level in title '{record.job_title}' (Manager/Architect/Director) is inconsistent with entry-level candidate target"
        )

    # 6. Remote / Location Eligibility
    if record.remote_eligibility == "country_restricted":
        rejections.append("Position is remote but restricted to a foreign country (not eligible for India-based applicant)")

    # 7. Final Status Determination
    if rejections:
        record.eligibility_status = EligibilityStatus.EXCLUDE_STATUS.value
        record.rejection_reasons = rejections
    elif reviews:
        record.eligibility_status = EligibilityStatus.REVIEW_STATUS.value
        record.manual_review_reasons = reviews
    else:
        record.eligibility_status = EligibilityStatus.PASS_STATUS.value

    is_pass = record.eligibility_status == EligibilityStatus.PASS_STATUS.value
    record.job_category = evaluate_job_category(record, overall_score, is_pass)

    return record
