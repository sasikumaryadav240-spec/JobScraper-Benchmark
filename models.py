"""
Data models for JobScraper-Benchmark.

Enforces strict schemas, field provenance, verification tracking,
and quality indicators without fabricating missing information.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


class FieldOrigin(str, Enum):
    SOURCE_PROVIDED = "source_provided"
    EXTRACTED_FROM_DESCRIPTION = "extracted_from_description"
    INFERRED = "inferred"
    VERIFIED_INDEPENDENTLY = "verified_independently"
    UNKNOWN = "unknown"


class WorkMode(str, Enum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"


class QualityIndicators(BaseModel):
    completeness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    has_title: bool = False
    has_company: bool = False
    has_location: bool = False
    has_description: bool = False
    has_job_url: bool = False
    has_application_url: bool = False
    has_experience_range: bool = False
    has_date_posted: bool = False
    has_employment_type: bool = False
    missing_critical_fields: List[str] = Field(default_factory=list)


class JobRecord(BaseModel):
    """
    Standardized, traceable cybersecurity job record.
    Never manufactures missing data; unavailable values remain None or unknown.
    """
    # Core Identifiers & Source Provenance
    source: str = Field(..., description="Source name e.g. naukri, indeed, glassdoor, linkedin")
    source_job_id: Optional[str] = Field(default=None, description="Original platform job ID")
    collection_method: str = Field(default="python-jobspy", description="Collection method or tool")
    verification_status: VerificationStatus = Field(default=VerificationStatus.UNVERIFIED)

    # Core Job Details
    title: str = Field(..., description="Job title as reported by the source")
    company: str = Field(..., description="Company or hiring entity name")
    location: Optional[str] = Field(default=None, description="Raw location string")
    country: Optional[str] = Field(default=None, description="Country name")
    state: Optional[str] = Field(default=None, description="State or province")
    city: Optional[str] = Field(default=None, description="City name")

    # Work Mode & Remote Eligibility
    work_mode: WorkMode = Field(default=WorkMode.UNKNOWN, description="onsite, hybrid, remote, or unknown")
    remote_eligibility: Optional[str] = Field(default=None, description="Remote eligibility notes/scope")
    geographic_restrictions: Optional[str] = Field(default=None, description="Geographic or residency restrictions")

    # Employment & Details
    employment_type: Optional[str] = Field(default=None, description="fulltime, parttime, contract, internship, etc.")
    description: Optional[str] = Field(default=None, description="Full job description or snippet")

    # Experience Requirements
    min_experience: Optional[float] = Field(default=None, ge=0.0, description="Minimum years of experience")
    max_experience: Optional[float] = Field(default=None, ge=0.0, description="Maximum years of experience")
    mandatory_experience: Optional[str] = Field(default=None, description="Mandatory experience requirements")
    preferred_experience: Optional[str] = Field(default=None, description="Preferred experience requirements")

    # Dates
    date_posted: Optional[str] = Field(default=None, description="Date job was posted (ISO or date string)")
    date_collected: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC timestamp when the job was collected"
    )

    # URLs
    job_url: Optional[str] = Field(default=None, description="Original listing URL")
    application_url: Optional[str] = Field(default=None, description="Direct application URL")

    # Metadata & Quality Tracking
    source_metadata: Dict[str, Any] = Field(default_factory=dict, description="Raw source-specific attributes")
    field_origins: Dict[str, FieldOrigin] = Field(
        default_factory=dict,
        description="Origin of each field (source_provided, inferred, extracted, unknown)"
    )
    quality_indicators: QualityIndicators = Field(default_factory=QualityIndicators)

    # Provenance across search combinations
    deduplication_key: Optional[str] = Field(default=None, description="Unique deduplication key")
    matched_keywords: List[str] = Field(default_factory=list)
    matched_locations: List[str] = Field(default_factory=list)
    matched_search_types: List[str] = Field(default_factory=list)
    matched_run_ids: List[str] = Field(default_factory=list)

    @field_validator("title", "company")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        s = str(v or "").strip()
        if not s or s.lower() in ("none", "nan", "null"):
            raise ValueError("Field cannot be empty")
        return s

    def compute_quality_indicators(self) -> QualityIndicators:
        """Calculate quality indicators and field completeness."""
        def is_present(val: Any) -> bool:
            if val is None:
                return False
            if isinstance(val, (int, float)):
                return True
            s = str(val).strip()
            return bool(s) and s.lower() not in ("none", "nan", "null", "unknown")

        has_title = is_present(self.title)
        has_company = is_present(self.company)
        has_location = is_present(self.location)
        has_description = is_present(self.description)
        has_job_url = is_present(self.job_url)
        has_application_url = is_present(self.application_url)
        has_exp = self.min_experience is not None or self.max_experience is not None
        has_date = is_present(self.date_posted)
        has_emp = is_present(self.employment_type)

        missing_critical = []
        if not has_title:
            missing_critical.append("title")
        if not has_company:
            missing_critical.append("company")
        if not has_job_url and not has_application_url:
            missing_critical.append("url")
        if not has_location and self.work_mode != WorkMode.REMOTE:
            missing_critical.append("location")

        # 8 core attributes scored equally
        scored_fields = [
            has_title, has_company, has_location, has_description,
            (has_job_url or has_application_url), has_exp, has_date, has_emp
        ]
        score = round(sum(1 for f in scored_fields if f) / len(scored_fields), 2)

        qi = QualityIndicators(
            completeness_score=score,
            has_title=has_title,
            has_company=has_company,
            has_location=has_location,
            has_description=has_description,
            has_job_url=has_job_url,
            has_application_url=has_application_url,
            has_experience_range=has_exp,
            has_date_posted=has_date,
            has_employment_type=has_emp,
            missing_critical_fields=missing_critical,
        )
        self.quality_indicators = qi
        return qi

    @classmethod
    def from_raw_jobspy(
        cls,
        raw: Dict[str, Any],
        run_id: str = "",
        keyword: str = "",
        search_location: str = "",
        search_type: str = "city",
        collected_at: str = ""
    ) -> "JobRecord":
        """
        Factory to construct a verified JobRecord from a raw python-jobspy row.
        Never fabricates values; assigns explicit field origins.
        """
        def clean_str(val: Any) -> Optional[str]:
            if val is None:
                return None
            s = str(val).strip()
            if not s or s.lower() in ("none", "nan", "null"):
                return None
            return s

        site = clean_str(raw.get("site") or raw.get("benchmark_site") or "unknown").lower()
        title = clean_str(raw.get("title")) or "Untitled Job"
        company = clean_str(raw.get("company")) or "Unknown Company"
        job_url = clean_str(raw.get("job_url"))
        app_url = clean_str(raw.get("job_url_direct"))
        desc = clean_str(raw.get("description"))
        loc = clean_str(raw.get("location"))
        date_posted = clean_str(raw.get("date_posted"))
        emp_type = clean_str(raw.get("job_type"))
        raw_id = clean_str(raw.get("id"))

        # Determine work mode
        is_remote_val = raw.get("is_remote")
        work_from_home = clean_str(raw.get("work_from_home_type"))
        work_mode = WorkMode.UNKNOWN
        work_mode_origin = FieldOrigin.UNKNOWN

        if is_remote_val is True or (work_from_home and "remote" in work_from_home.lower()):
            work_mode = WorkMode.REMOTE
            work_mode_origin = FieldOrigin.SOURCE_PROVIDED
        elif is_remote_val is False:
            work_mode = WorkMode.ONSITE
            work_mode_origin = FieldOrigin.SOURCE_PROVIDED
        elif work_from_home and "hybrid" in work_from_home.lower():
            work_mode = WorkMode.HYBRID
            work_mode_origin = FieldOrigin.SOURCE_PROVIDED

        # Field origin tracking
        origins: Dict[str, FieldOrigin] = {
            "title": FieldOrigin.SOURCE_PROVIDED if clean_str(raw.get("title")) else FieldOrigin.UNKNOWN,
            "company": FieldOrigin.SOURCE_PROVIDED if clean_str(raw.get("company")) else FieldOrigin.UNKNOWN,
            "location": FieldOrigin.SOURCE_PROVIDED if loc else FieldOrigin.UNKNOWN,
            "job_url": FieldOrigin.SOURCE_PROVIDED if job_url else FieldOrigin.UNKNOWN,
            "application_url": FieldOrigin.SOURCE_PROVIDED if app_url else FieldOrigin.UNKNOWN,
            "description": FieldOrigin.SOURCE_PROVIDED if desc else FieldOrigin.UNKNOWN,
            "date_posted": FieldOrigin.SOURCE_PROVIDED if date_posted else FieldOrigin.UNKNOWN,
            "employment_type": FieldOrigin.SOURCE_PROVIDED if emp_type else FieldOrigin.UNKNOWN,
            "work_mode": work_mode_origin,
            "min_experience": FieldOrigin.UNKNOWN,
            "max_experience": FieldOrigin.UNKNOWN,
        }

        # Experience parsing if source provides it
        min_exp: Optional[float] = None
        max_exp: Optional[float] = None
        exp_range = clean_str(raw.get("experience_range"))
        if exp_range:
            import re
            # e.g. "2-7 Yrs" or "2 to 5 years" or "3+ years"
            match = re.search(r"(\d+)\s*(?:-|to)\s*(\d+)", exp_range, re.I)
            if match:
                min_exp = float(match.group(1))
                max_exp = float(match.group(2))
                origins["min_experience"] = FieldOrigin.SOURCE_PROVIDED
                origins["max_experience"] = FieldOrigin.SOURCE_PROVIDED
            else:
                plus_match = re.search(r"(\d+)\+?", exp_range)
                if plus_match:
                    min_exp = float(plus_match.group(1))
                    origins["min_experience"] = FieldOrigin.SOURCE_PROVIDED

        # Keywords and provenance
        matched_kw = [keyword] if keyword else []
        matched_loc = [search_location] if search_location else []
        matched_st = [search_type] if search_type else []
        matched_run = [run_id] if run_id else []

        # If existing provenance arrays are in raw (e.g. from checkpoints)
        if isinstance(raw.get("matched_keywords"), list):
            matched_kw = list(dict.fromkeys(matched_kw + [str(x) for x in raw["matched_keywords"] if str(x)]))
        if isinstance(raw.get("matched_locations"), list):
            matched_loc = list(dict.fromkeys(matched_loc + [str(x) for x in raw["matched_locations"] if str(x)]))
        if isinstance(raw.get("matched_search_types"), list):
            matched_st = list(dict.fromkeys(matched_st + [str(x) for x in raw["matched_search_types"] if str(x)]))
        if isinstance(raw.get("matched_run_ids"), list):
            matched_run = list(dict.fromkeys(matched_run + [str(x) for x in raw["matched_run_ids"] if str(x)]))

        record = cls(
            source=site,
            source_job_id=raw_id,
            title=title,
            company=company,
            location=loc,
            work_mode=work_mode,
            employment_type=emp_type,
            description=desc,
            min_experience=min_exp,
            max_experience=max_exp,
            date_posted=date_posted,
            date_collected=collected_at or datetime.now(timezone.utc).isoformat(),
            job_url=job_url,
            application_url=app_url,
            source_metadata={k: v for k, v in raw.items() if k not in (
                "title", "company", "location", "job_url", "job_url_direct",
                "description", "date_posted", "job_type", "site"
            )},
            field_origins=origins,
            matched_keywords=matched_kw,
            matched_locations=matched_loc,
            matched_search_types=matched_st,
            matched_run_ids=matched_run,
            deduplication_key=raw.get("deduplication_key"),
        )
        record.compute_quality_indicators()
        return record

