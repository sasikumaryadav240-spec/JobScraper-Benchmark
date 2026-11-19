"""
Deterministic Candidate Skill Matching & Eligibility Engine.
Authoritative Implementation for Candidate Skills Profile Version 2.0 (9 November 2026).

Implements:
1. All 15 Role Families (10 Cybersecurity + 5 IT Support & Infrastructure).
2. Strict proficiency hierarchy:
   - Professional experience (employment/internship)
   - Hands-on / practical (labs/projects - NOT employment)
   - Familiarity / fundamentals (conceptual, limited)
   - Learning target (NEVER credited as existing skill)
   - Certification (credential - NOT employment)
3. Authoritative 6-Component Weighted Scoring Model (Part E, Section 4):
   - Required technical skills: 35%
   - Core responsibilities: 25%
   - Experience compatibility: 15%
   - Tools and technologies: 10%
   - Education and certifications: 5%
   - Transferable skills: 10%
   Total = 100%
4. Strict threshold override: overall_match_score > 60.0 (strictly > 60.0%).
5. Alternative-tool rules (Splunk/Sentinel, Nessus/Qualys, EDR, ServiceNow/ITSM, VMware, AWS/Azure).
6. Mandatory eligibility filters (Experience, Education, Batch, Employment type, Location, Seniority).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from candidate_profile import CANDIDATE_PROFILE, SCORING_WEIGHTS
from unified_models import CanonicalJobRecord, EligibilityStatus, JobCategory

logger = logging.getLogger("CandidateMatchingEngine")

# --- REGEX DEFINITIONS ---

# Seniority guardrail (Manager, Architect, Director, Lead, Principal, etc.)
SENIOR_ROLE_RE = re.compile(
    r"\b(?:manager|architect|director|vice\s*president|vp\b|head\s*of|principal|senior\s*lead|staff\s*engineer|lead\s*engineer|engineering\s*lead)\b",
    re.IGNORECASE,
)

# Mandatory professional cybersecurity experience phrases
PROFESSIONAL_SECURITY_EXP_RE = re.compile(
    r"\b(\d+)\+?\s*(?:years?|yrs?)\b.*?(?:professional|work|production|enterprise|prior|proven|industry|relevant)\s*(?:experience|exp)\b.*?(?:soc|cyber|security|infosec|threat|incident)|"
    r"\b(?:minimum|at\s*least|min\.?)\s*(\d+)\+?\s*(?:years?|yrs?)\b.*?(?:in|of)\s*(?:a\s*)?(?:production\s*soc|security\s*operations|cybersecurity|infosec|incident\s*response)",
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


def _word_regex(kw: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(kw.strip()) + r"(?![A-Za-z0-9])", re.IGNORECASE)


class CandidateMatchingEngine:
    def __init__(self, profile: dict[str, Any] = CANDIDATE_PROFILE):
        self.profile = profile
        self.role_families = profile.get("role_families", [])
        self.weights = SCORING_WEIGHTS

        # Build skill maps
        self.practical_skills: set[str] = set()
        self.familiarity_skills: set[str] = set()
        self.learning_targets: set[str] = set()

        for fam in self.role_families:
            for s in fam.get("practical_skills", []):
                self.practical_skills.add(s.lower())
            for s in fam.get("familiarity_skills", []):
                self.familiarity_skills.add(s.lower())
            for s in fam.get("learning_targets", []):
                self.learning_targets.add(s.lower())

        # Compile regexes for all 15 role families
        self.family_matchers: list[dict[str, Any]] = []
        for fam in self.role_families:
            patterns = [re.compile(p, re.IGNORECASE) for p in fam.get("title_patterns", [])]
            self.family_matchers.append({
                "id": fam["id"],
                "name": fam["name"],
                "group": fam["group"],
                "patterns": patterns,
                "practical": [s.lower() for s in fam.get("practical_skills", [])],
                "familiarity": [s.lower() for s in fam.get("familiarity_skills", [])],
                "learning_targets": [s.lower() for s in fam.get("learning_targets", [])],
            })

    def identify_role_families(self, title: str, description: str) -> tuple[str, list[str]]:
        """Identify primary and secondary role families from the 15 authoritative families."""
        title_lower = title.lower()
        desc_lower = description.lower()

        scores: list[tuple[str, int, int]] = []  # (name, title_match_weight, skill_match_count)

        for fam in self.family_matchers:
            title_match = 10 if any(p.search(title_lower) for p in fam["patterns"]) else 0
            skill_hits = sum(1 for s in fam["practical"] if s in desc_lower)
            if title_match > 0 or skill_hits > 0:
                scores.append((fam["name"], title_match, skill_hits))

        scores.sort(key=lambda x: (x[1], x[2]), reverse=True)

        if scores:
            primary = scores[0][0]
            secondaries = [s[0] for s in scores[1:4] if s[1] > 0 or s[2] >= 2]
            return primary, secondaries

        # Default fallback
        if any(k in title_lower for k in ("network", "desktop", "help desk", "it support", "sysadmin", "system administrator")):
            return "IT Support Specialist", ["Junior System Administrator"]
        return "SOC Analyst / Security Operations", ["Security Analyst / Information Security Analyst"]

    def extract_matched_and_missing(self, text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
        """
        Analyze text against practical skills, familiarity, learning targets, and missing skills.
        Returns: (matched_skills, partially_matched_skills, missing_skills, learning_target_overlap)
        """
        text_lower = text.lower()
        matched: list[dict[str, Any]] = []
        partially_matched: list[dict[str, Any]] = []
        learning_targets_found: list[str] = []

        # 1. Check practical skills (hands-on)
        for s in sorted(self.practical_skills):
            if len(s) >= 3 and _word_regex(s).search(text_lower):
                matched.append({
                    "name": s.title(),
                    "evidence_level": "practical",
                    "match_value": 1.0,
                })

        # 2. Check familiarity / alternative skills
        for s in sorted(self.familiarity_skills):
            if len(s) >= 3 and _word_regex(s).search(text_lower):
                partially_matched.append({
                    "name": s.title(),
                    "evidence_level": "familiarity",
                    "match_value": 0.4,
                })

        # 3. Check learning targets (NEVER credited as candidate skill, tracked for gap analysis)
        for s in sorted(self.learning_targets):
            if len(s) >= 3 and _word_regex(s).search(text_lower):
                learning_targets_found.append(s.title())

        # 4. Alternative Tool Rules (Part E, Section 2)
        # Splunk OR Sentinel
        if ("splunk" in text_lower or "sentinel" in text_lower) and not any(m["name"].lower() == "microsoft sentinel" for m in matched):
            matched.append({"name": "Microsoft Sentinel (SIEM Alternative)", "evidence_level": "practical", "match_value": 0.85})

        # Nessus / Qualys / OpenVAS
        if ("qualys" in text_lower or "openvas" in text_lower) and not any(m["name"].lower() == "nessus" for m in matched):
            partially_matched.append({"name": "Nessus (VA Alternative)", "evidence_level": "practical", "match_value": 0.75})

        # EDR (Defender XDR / LimaCharlie)
        if any(e in text_lower for e in ("edr", "endpoint detection", "crowdstrike", "sentinelone", "defender xdr")):
            if not any("edr" in m["name"].lower() for m in matched):
                matched.append({"name": "LimaCharlie EDR / Defender XDR Exposure", "evidence_level": "practical", "match_value": 0.6})

        # ITSM (ServiceNow / Jira / Freshservice)
        if any(k in text_lower for k in ("servicenow", "itsm", "jira service", "freshservice")):
            partially_matched.append({"name": "ITSM Concepts (Not Platform Admin)", "evidence_level": "familiarity", "match_value": 0.4})

        # Virtualization (Hyper-V / VMware)
        if any(k in text_lower for k in ("hyper-v", "vmware", "esxi", "vsphere", "virtualbox")):
            partially_matched.append({"name": "Virtualization Fundamentals (Not Production Admin)", "evidence_level": "familiarity", "match_value": 0.4})

        # Cloud (Azure / AWS)
        if any(k in text_lower for k in ("azure", "aws", "cloud security", "cloud monitoring")):
            if not any("azure" in m["name"].lower() for m in matched):
                matched.append({"name": "Azure Exposure & AWS Fundamentals", "evidence_level": "basic", "match_value": 0.5})

        # Missing skills identification
        core_targets = ["splunk", "sentinel", "kql", "wireshark", "nessus", "linux", "siem", "tcp/ip"]
        found_names = {m["name"].lower() for m in matched} | {p["name"].lower() for p in partially_matched}
        missing = [t.title() for t in core_targets if not any(t in f for f in found_names) and t in text_lower]

        return matched, partially_matched, missing, learning_targets_found

    def calculate_weighted_scores(
        self,
        record: CanonicalJobRecord,
        matched: list[dict[str, Any]],
        partially_matched: list[dict[str, Any]],
        missing: list[str],
        learning_targets: list[str],
    ) -> tuple[float, float, dict[str, float]]:
        """
        Calculate the 6 components from Part E, Section 4:
        1. Required technical skills (35%)
        2. Core responsibilities (25%)
        3. Experience compatibility (15%)
        4. Tools and technologies (10%)
        5. Education and certifications (5%)
        6. Transferable skills (10%)
        Total = 100%
        """
        full_text = f"{record.job_title}\n{record.job_description}".lower()

        # Component 1: Required Technical Skills (35%)
        all_hits = matched + partially_matched
        if all_hits:
            tech_skill_avg = sum(h["match_value"] for h in all_hits) / len(all_hits)
            c1_tech_skills = min(100.0, round(tech_skill_avg * 100.0, 2))
        else:
            c1_tech_skills = 0.0

        # Component 2: Core Responsibilities (25%)
        resp_keywords = [
            "monitor", "triage", "investigate", "alert", "incident", "vulnerability",
            "troubleshoot", "analysis", "reporting", "documentation", "support", "escalate",
        ]
        resp_found = sum(1 for r in resp_keywords if r in full_text)
        c2_responsibilities = min(100.0, round((resp_found / max(1, min(len(resp_keywords), 6))) * 100.0, 2))

        # Component 3: Experience Compatibility (15%)
        min_exp = record.mandatory_min_experience
        if FRESHER_ACCEPTANCE_RE.search(full_text) or "fresher" in full_text or min_exp == 0.0:
            c3_experience = 100.0
        elif min_exp is not None and min_exp <= 1.0:
            c3_experience = 90.0
        elif min_exp is not None and min_exp <= 2.0:
            c3_experience = 70.0
        elif min_exp is not None and min_exp >= 3.0:
            c3_experience = 0.0
        else:
            # Unmentioned or ambiguous
            c3_experience = 55.0

        # Component 4: Tools and Technologies (10%)
        tools_keywords = ["sentinel", "splunk", "wireshark", "nessus", "linux", "windows", "powershell", "bash", "limacharlie", "docker", "git"]
        tools_found = sum(1 for t in tools_keywords if t in full_text)
        c4_tools = min(100.0, round((tools_found / max(1, min(len(tools_keywords), 4))) * 100.0, 2))

        # Component 5: Education and Certifications (5%)
        # Candidate has B.E. ECE + CompTIA Security+ + Google Cybersecurity Certificate
        c5_edu_certs = 80.0
        if "security+" in full_text or "comptia" in full_text or "certification" in full_text:
            c5_edu_certs = 100.0
        elif CS_IT_ONLY_RE.search(full_text):
            c5_edu_certs = 0.0

        # Component 6: Transferable Skills (10%)
        # Varnarc MERN internship + programming (Node.js, Express, MongoDB, REST APIs, JWT, RBAC, Python, Bash, Troubleshooting)
        transfer_keywords = ["rest api", "api", "python", "bash", "scripting", "jwt", "rbac", "troubleshooting", "logging", "git"]
        transfer_found = sum(1 for k in transfer_keywords if k in full_text)
        c6_transferable = min(100.0, max(50.0, round((transfer_found / 3.0) * 100.0, 2)))

        component_scores = {
            "required_technical_skills": c1_tech_skills,
            "core_responsibilities": c2_responsibilities,
            "experience_compatibility": c3_experience,
            "tools_and_technologies": c4_tools,
            "education_and_certifications": c5_edu_certs,
            "transferable_skills": c6_transferable,
        }

        # Weighted calculation
        overall_match_score = round(
            self.weights["required_technical_skills"] * c1_tech_skills
            + self.weights["core_responsibilities"] * c2_responsibilities
            + self.weights["experience_compatibility"] * c3_experience
            + self.weights["tools_and_technologies"] * c4_tools
            + self.weights["education_and_certifications"] * c5_edu_certs
            + self.weights["transferable_skills"] * c6_transferable,
            2,
        )

        technical_match_score = c1_tech_skills

        return technical_match_score, overall_match_score, component_scores


MATCHER_ENGINE = CandidateMatchingEngine()


def evaluate_experience_eligibility(record: CanonicalJobRecord) -> tuple[str, list[str]]:
    """
    Evaluate experience eligibility according to authoritative rules:
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

    # Check advertised text
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
    - CS/IT only -> EXCLUDE (Candidate holds B.E. ECE)
    - Any engineering / ECE -> PASS
    - Batch 2024/2025 only -> EXCLUDE (Candidate is Class of 2026)
    - Unclear -> REVIEW
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

    # General degree mentions
    if "degree" in desc or "bachelor" in desc or "b.e" in desc or "b.tech" in desc:
        if "computer science" in desc or "information technology" in desc:
            return "REVIEW", ["Degree asks for CS/IT or related field — verify ECE acceptance"]
        return "PASS", ["Degree requirement is general or non-restrictive"]

    return "REVIEW", ["Education requirements not explicitly stated — verify candidate degree eligibility"]


def evaluate_candidate_matching(
    record: CanonicalJobRecord,
    engine: CandidateMatchingEngine = MATCHER_ENGINE,
    recalculate_scores: bool = True,
) -> CanonicalJobRecord:
    """
    Evaluate every canonical unique job against the authoritative Candidate Skills Profile.
    Applies strict > 60.0% threshold, 6-component scoring, eligibility checks, and transparent explanations.
    """
    full_text = f"{record.job_title}\n{record.job_description}"

    # 1. Identify Role Families
    primary_fam, secondary_fams = engine.identify_role_families(record.job_title, record.job_description)
    record.primary_role_family = primary_fam
    record.secondary_role_families = secondary_fams

    # 2. Extract Skills & Tool Matches
    matched, partially_matched, missing, learning_targets = engine.extract_matched_and_missing(full_text)
    record.matched_skills = matched
    record.partially_matched_skills = partially_matched
    record.missing_skills = missing
    record.learning_target_overlap = learning_targets
    record.matched_requirements = [m["name"] for m in matched]
    record.partially_matched_requirements = [p["name"] for p in partially_matched]
    record.missing_requirements = missing

    # 3. Calculate Scores
    if recalculate_scores:
        tech_score, overall_score, comp_scores = engine.calculate_weighted_scores(
            record, matched, partially_matched, missing, learning_targets
        )
        record.technical_skill_match_score = tech_score
        record.overall_match_score = overall_score
        record.component_scores = comp_scores
    else:
        overall_score = record.overall_match_score
        tech_score = record.technical_skill_match_score

    rejections: list[str] = []
    reviews: list[str] = []
    decisions: dict[str, str] = {
        "experience": "PASS",
        "degree": "PASS",
        "batch": "PASS",
        "employment_type": "PASS",
        "location": "PASS",
        "seniority": "PASS",
    }

    # 4. Mandatory Eligibility Filters

    # Filter 4.1: Experience Eligibility
    exp_status, exp_reasons = evaluate_experience_eligibility(record)
    decisions["experience"] = exp_status
    if exp_status == "EXCLUDE":
        rejections.extend(exp_reasons)
    elif exp_status == "REVIEW":
        reviews.extend(exp_reasons)

    # Filter 4.1b: Mandatory Professional Cybersecurity Experience Guardrail
    desc_lower = record.job_description.lower()
    if PROFESSIONAL_SECURITY_EXP_RE.search(desc_lower) and not FRESHER_ACCEPTANCE_RE.search(desc_lower):
        m_years = PROFESSIONAL_SECURITY_EXP_RE.search(desc_lower)
        yr_val = m_years.group(1) or m_years.group(2) if m_years else None
        if yr_val and float(yr_val) >= 2.0:
            decisions["experience"] = "EXCLUDE"
            rejections.append(
                f"Mandatory professional cybersecurity/SOC experience required ({yr_val}+ yrs); candidate possesses 0 professional security years"
            )

    # Filter 4.2: Education Eligibility & Batch
    edu_status, edu_reasons = evaluate_education_eligibility(record)
    decisions["degree"] = edu_status
    if edu_status == "EXCLUDE":
        rejections.extend(edu_reasons)
        if any("batch" in r.lower() for r in edu_reasons):
            decisions["batch"] = "EXCLUDE"
    elif edu_status == "REVIEW":
        reviews.extend(edu_reasons)

    # Filter 4.3: Employment Type
    title_lower = record.job_title.lower()
    if "intern" in title_lower or record.internship_status or "trainee" in title_lower:
        record.internship_status = True
        decisions["employment_type"] = "PASS"
    elif any(k in title_lower for k in ("part-time", "part time", "freelance", "contract")):
        decisions["employment_type"] = "REVIEW"
        reviews.append("Non-full-time / contract position — verify employment terms")

    # Filter 4.4: Location & Remote Eligibility
    if record.remote_eligibility == "country_restricted":
        decisions["location"] = "EXCLUDE"
        rejections.append("Position is remote but restricted to a foreign country (not eligible for India-based applicant)")
    elif record.remote_eligibility == "unknown" and record.work_mode == "remote":
        decisions["location"] = "REVIEW"
        reviews.append("Remote position with unspecified country restrictions — verify India eligibility")

    # Filter 4.5: Seniority & Role-Level Suitability (Manager, Architect, Director Exclusions)
    if SENIOR_ROLE_RE.search(title_lower) and not any(k in title_lower for k in ("intern", "trainee", "associate")):
        decisions["seniority"] = "EXCLUDE"
        rejections.append(
            f"Role seniority level in title '{record.job_title}' (Manager/Architect/Director) is inconsistent with entry-level candidate target"
        )

    # Check senior responsibilities in description
    if any(k in desc_lower for k in ("manage team of", "manage a team of", "direct reports", "10+ years architecting", "hire and mentor")):
        decisions["seniority"] = "EXCLUDE"
        rejections.append("Job description requires direct team management / senior architecture responsibilities")

    # 5. Score Threshold: STRICTLY GREATER THAN 60.0%
    if overall_score <= 60.0:
        rejections.append(f"Overall match score {overall_score:.2f}% does not strictly exceed the 60.0% threshold")

    # 6. Reconcile Decisions and Reasons
    record.eligibility_decisions = decisions
    decision_reasons: list[str] = []

    if rejections:
        record.eligibility_status = EligibilityStatus.EXCLUDE_STATUS.value
        record.rejection_reasons = rejections
        decision_reasons.extend(rejections)
    elif reviews:
        record.eligibility_status = EligibilityStatus.REVIEW_STATUS.value
        record.manual_review_reasons = reviews
        decision_reasons.extend(reviews)
    else:
        record.eligibility_status = EligibilityStatus.PASS_STATUS.value
        decision_reasons.append("Mandatory experience is within the accepted fresher range (<= 2 years)")
        decision_reasons.append("Candidate B.E. ECE engineering degree accepted")
        decision_reasons.append(f"Primary role family '{primary_fam}' matched with relevant technical skills")
        decision_reasons.append(f"Overall score {overall_score:.2f}% strictly exceeds 60.0% threshold")

    record.decision_reasons = decision_reasons

    # 7. Category Assignment
    if record.internship_status or "intern" in title_lower:
        record.job_category = JobCategory.INTERNSHIP.value
    elif rejections:
        record.job_category = JobCategory.EXCLUDED.value
    elif reviews:
        record.job_category = JobCategory.MANUAL_VERIFICATION.value
    elif primary_fam in (
        "Help Desk Technician", "Desktop Support Engineer", "IT Support Specialist",
        "Junior System Administrator", "Network Support Associate"
    ) or any(k in title_lower for k in SUPPORTING_IT_KEYWORDS):
        record.job_category = JobCategory.SUPPORTING_IT.value
    elif overall_score > 60.0:
        record.job_category = JobCategory.DIRECT_CYBERSECURITY.value
    else:
        record.job_category = JobCategory.EXCLUDED.value

    return record


def calculate_job_scores(
    record: CanonicalJobRecord,
    engine: CandidateMatchingEngine = MATCHER_ENGINE,
) -> tuple[float, float, list[dict[str, Any]], list[str]]:
    """Helper preserving legacy signature for backward compatibility with tests."""
    full_text = f"{record.job_title}\n{record.job_description}"
    matched, partially_matched, missing, learning_targets = engine.extract_matched_and_missing(full_text)
    tech_score, overall_score, comp_scores = engine.calculate_weighted_scores(
        record, matched, partially_matched, missing, learning_targets
    )
    return tech_score, overall_score, matched + partially_matched, missing


SUPPORTING_IT_KEYWORDS = (
    "network engineer", "systems administrator", "system administrator",
    "cloud engineer", "cloud infrastructure", "devops engineer", "infrastructure engineer",
    "linux administrator", "site reliability", "sre", "database administrator",
    "desktop support", "it support", "desktop engineer", "help desk", "service desk",
    "noc engineer", "noc analyst", "network associate", "network technician",
)


def evaluate_job_category(record: CanonicalJobRecord, overall_score: float, is_pass: bool) -> str:
    """Helper preserving legacy signature."""
    title_lower = record.job_title.lower()
    if "intern" in title_lower or record.internship_status:
        return JobCategory.INTERNSHIP.value
    if any(k in title_lower for k in SUPPORTING_IT_KEYWORDS):
        return JobCategory.SUPPORTING_IT.value
    if is_pass and overall_score > 60.0:
        return JobCategory.DIRECT_CYBERSECURITY.value
    if not is_pass and overall_score <= 60.0:
        return JobCategory.EXCLUDED.value
    return JobCategory.MANUAL_VERIFICATION.value
