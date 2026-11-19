"""
Canonical Data Models for Phase 5 Unified Processing Pipeline.

Provides a unified schema across Indeed, Naukri, LinkedIn, and ATS datasets,
preserving full provenance, URLs, experience breakdown, and matching metrics.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class DescriptionType(str, Enum):
    FULL_DESCRIPTION = "full_description"
    SEARCH_RESULT_SNIPPET = "search_result_snippet"
    PARTIAL_DESCRIPTION = "partial_description"
    MISSING = "missing"


class JobCategory(str, Enum):
    DIRECT_CYBERSECURITY = "direct_cybersecurity"
    SUPPORTING_IT = "supporting_it"
    INTERNSHIP = "internship"
    MANUAL_VERIFICATION = "manual_verification"
    EXCLUDED = "excluded"
    OTHER_POTENTIALLY_RELEVANT = "other_potentially_relevant"


class EligibilityStatus(str, Enum):
    PASS_STATUS = "PASS"
    REVIEW_STATUS = "REVIEW"
    EXCLUDE_STATUS = "EXCLUDE"


class WorkModeEnum(str, Enum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class RemoteEligibilityEnum(str, Enum):
    INDIA_ONLY = "india_only"
    INTERNATIONAL = "international"
    COUNTRY_RESTRICTED = "country_restricted"
    UNKNOWN = "unknown"


class CanonicalJobRecord(BaseModel):
    # 1. Identity & Cross-Platform Tracking
    canonical_job_id: str
    source: str
    source_job_id: str
    all_source_ids: dict[str, list[str]] = Field(default_factory=dict)
    duplicate_group_id: Optional[str] = None

    # 2. Job Details
    job_title: str
    normalized_job_title: str
    company_name: str
    normalized_company_name: str
    job_description: str
    description_type: str = DescriptionType.MISSING.value
    job_category: str = JobCategory.MANUAL_VERIFICATION.value

    # 3. Location & Work Mode
    original_location: str = ""
    normalized_location: str = ""
    city: Optional[str] = None
    state: Optional[str] = None
    country: str = "India"
    work_mode: str = WorkModeEnum.UNKNOWN.value
    remote_eligibility: Optional[str] = None
    remote_evidence: Optional[str] = None

    # 4. Employment & Seniority
    employment_type: Optional[str] = None
    seniority_level: Optional[str] = None
    internship_status: bool = False

    # 5. Experience
    original_experience_text: Optional[str] = None
    mandatory_min_experience: Optional[float] = None
    mandatory_max_experience: Optional[float] = None
    preferred_min_experience: Optional[float] = None
    preferred_max_experience: Optional[float] = None
    experience_extraction_status: str = "unmentioned"

    # 6. Compensation
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = None
    salary_period: Optional[str] = None

    # 7. Dates
    original_posted_date: Optional[str] = None
    normalized_posted_date: Optional[str] = None
    collected_at: str
    date_verification_status: str = "unknown"

    # 8. URLs
    original_job_url: Optional[str] = None
    all_source_urls: list[str] = Field(default_factory=list)
    application_url: Optional[str] = None
    all_application_urls: list[str] = Field(default_factory=list)
    url_verification_status: str = "verified"

    # 9. Matching & Eligibility
    technical_skill_match_score: float = 0.0
    overall_match_score: float = 0.0
    primary_role_family: Optional[str] = None
    secondary_role_families: list[str] = Field(default_factory=list)
    component_scores: dict[str, float] = Field(default_factory=dict)
    eligibility_decisions: dict[str, str] = Field(default_factory=dict)
    matched_skills: list[dict[str, Any]] = Field(default_factory=list)
    partially_matched_skills: list[dict[str, Any]] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    learning_target_overlap: list[str] = Field(default_factory=list)
    matched_requirements: list[str] = Field(default_factory=list)
    partially_matched_requirements: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    relevant_experience: str = ""
    eligibility_status: str = EligibilityStatus.REVIEW_STATUS.value
    rejection_reasons: list[str] = Field(default_factory=list)
    manual_review_reasons: list[str] = Field(default_factory=list)
    decision_reasons: list[str] = Field(default_factory=list)

    # 10. Provenance & Quality
    source_provenance: list[dict[str, Any]] = Field(default_factory=list)
    field_origins: dict[str, str] = Field(default_factory=dict)
    field_confidence: dict[str, float] = Field(default_factory=dict)
    data_quality_flags: list[str] = Field(default_factory=list)
    verification_status: str = "verified"
    raw_record_ref: Optional[dict[str, Any]] = None

    def to_flat_dict(self) -> dict[str, Any]:
        """Convert to a flattened dictionary suitable for CSV / Excel export."""
        data = self.model_dump(mode="json")
        # Flatten complex structures
        data["all_source_ids"] = "; ".join(f"{k}: {', '.join(v)}" for k, v in (self.all_source_ids or {}).items())
        data["all_source_urls"] = "; ".join(self.all_source_urls or [])
        data["all_application_urls"] = "; ".join(self.all_application_urls or [])
        data["secondary_role_families"] = "; ".join(self.secondary_role_families or [])
        data["component_scores"] = "; ".join(f"{k}: {v:.1f}%" for k, v in (self.component_scores or {}).items())
        data["eligibility_decisions"] = "; ".join(f"{k}: {v}" for k, v in (self.eligibility_decisions or {}).items())
        data["matched_skills"] = "; ".join(
            f"{s.get('name', '')} ({s.get('evidence_level', '')}:{s.get('match_value', 0)})"
            for s in (self.matched_skills or [])
        )
        data["partially_matched_skills"] = "; ".join(
            f"{s.get('name', '')} ({s.get('evidence_level', '')}:{s.get('match_value', 0)})"
            for s in (self.partially_matched_skills or [])
        )
        data["missing_skills"] = "; ".join(self.missing_skills or [])
        data["learning_target_overlap"] = "; ".join(self.learning_target_overlap or [])
        data["matched_requirements"] = "; ".join(self.matched_requirements or [])
        data["partially_matched_requirements"] = "; ".join(self.partially_matched_requirements or [])
        data["missing_requirements"] = "; ".join(self.missing_requirements or [])
        data["rejection_reasons"] = " | ".join(self.rejection_reasons or [])
        data["manual_review_reasons"] = " | ".join(self.manual_review_reasons or [])
        data["decision_reasons"] = " | ".join(self.decision_reasons or [])
        data["data_quality_flags"] = "; ".join(self.data_quality_flags or [])
        data.pop("source_provenance", None)
        data.pop("field_origins", None)
        data.pop("field_confidence", None)
        data.pop("raw_record_ref", None)
        return data

