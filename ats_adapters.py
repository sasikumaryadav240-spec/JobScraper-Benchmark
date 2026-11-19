"""
Platform-specific Adapters for Greenhouse, Lever, and Ashby.

Interacts strictly with official public job board APIs to collect,
validate, and transform raw ATS records into canonical JobRecords.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ats_config import DEFAULT_USER_AGENT, HTTP_TIMEOUT_SECONDS
from ats_filter import is_cybersecurity_role, is_india_or_remote_eligible
from ats_registry import EmployerBoard
from linkedin_parser import (
    DescriptionType,
    classify_description_type,
    extract_experience_from_text,
    extract_remote_eligibility,
    extract_work_mode,
)
from models import (
    FieldOrigin,
    JobRecord,
    QualityIndicators,
    VerificationStatus,
    WorkMode,
)

logger = logging.getLogger("ATSAdapters")


def html_to_plain_text(html_content: str | None) -> str:
    """Convert HTML job description into formatted plain text."""
    if not html_content:
        return ""
    try:
        s = str(html_content)
        s = re.sub(r"<(?:p|div|li|br|h[1-6])[^>]*>", "\n", s, flags=re.IGNORECASE)
        s = re.sub(r"</(?:p|div|li|h[1-6])>", "\n", s, flags=re.IGNORECASE)
        s = re.sub(r"<[^>]+>", "", s)
        s = html.unescape(s)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in s.splitlines()]
        return "\n".join(line for line in lines if line)
    except Exception:
        clean = re.sub(r"<[^>]+>", " ", str(html_content))
        return re.sub(r"\s+", " ", html.unescape(clean)).strip()


def normalize_ats_url(url: str | None) -> str:
    """Normalize ATS job posting URL by removing tracking query parameters."""
    if not url:
        return ""
    try:
        parts = urlsplit(str(url).strip())
        host = parts.netloc.lower().removeprefix("www.")
        path = parts.path.rstrip("/")
        return urlunsplit((parts.scheme or "https", host, path, "", ""))
    except Exception:
        return str(url).strip()


def parse_iso_or_epoch_date(val: Any) -> str | None:
    """Parse date from ISO string or epoch milliseconds into UTC ISO format."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        # Epoch ms or seconds
        try:
            ts = val / 1000.0 if val > 1e11 else val
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            return None

    s = str(val).strip()
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass

    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return s if s else None


class BaseATSAdapter:
    platform_name: str = "base"

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT, timeout: float = HTTP_TIMEOUT_SECONDS):
        self.user_agent = user_agent
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

    def fetch_raw_board(self, board: EmployerBoard) -> list[dict[str, Any]]:
        raise NotImplementedError

    def parse_record(self, raw: dict[str, Any], board: EmployerBoard, run_id: str) -> JobRecord | None:
        raise NotImplementedError


class GreenhouseAdapter(BaseATSAdapter):
    platform_name: str = "greenhouse"

    def fetch_raw_board(self, board: EmployerBoard) -> list[dict[str, Any]]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{board.board_identifier}/jobs?content=true"
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        return data.get("jobs", [])

    def parse_record(self, raw: dict[str, Any], board: EmployerBoard, run_id: str) -> JobRecord | None:
        title = str(raw.get("title") or "").strip()
        raw_desc = raw.get("content") or ""
        plain_desc = html_to_plain_text(raw_desc)

        # 1. Cybersecurity Filter
        is_sec, matched_kw = is_cybersecurity_role(title, plain_desc)
        if not is_sec:
            return None

        # 2. Location & Remote Filter
        loc_dict = raw.get("location") or {}
        location_name = str(loc_dict.get("name") or "India").strip()
        is_eligible, std_loc, remote_elig = is_india_or_remote_eligible(location_name, plain_desc)
        if not is_eligible:
            return None

        # 3. Field Extraction
        job_id = str(raw.get("id"))
        job_url = normalize_ats_url(raw.get("absolute_url"))
        date_posted = parse_iso_or_epoch_date(raw.get("updated_at"))
        desc_type = classify_description_type(plain_desc)

        # Experience extraction
        min_exp, max_exp, pref_exp, exp_wording = extract_experience_from_text(plain_desc)

        # Work Mode
        work_mode, mode_ev = extract_work_mode(title, location_name, plain_desc)
        if remote_elig and work_mode == WorkMode.UNKNOWN:
            work_mode = WorkMode.REMOTE

        # Provenance & Metadata
        now_iso = datetime.now(timezone.utc).isoformat()
        metadata = {
            "ats_platform": "greenhouse",
            "board_identifier": board.board_identifier,
            "source_job_id": job_id,
            "description_type": desc_type.value,
            "departments": raw.get("departments", []),
            "offices": raw.get("offices", []),
            "matched_filter_keyword": matched_kw,
            "raw_location": location_name,
            "raw_work_mode_evidence": mode_ev,
            "raw_experience_wording": exp_wording,
        }

        field_origins = {
            "title": FieldOrigin.SOURCE_PROVIDED,
            "company": FieldOrigin.SOURCE_PROVIDED,
            "location": FieldOrigin.SOURCE_PROVIDED,
            "description": FieldOrigin.SOURCE_PROVIDED,
            "date_posted": FieldOrigin.SOURCE_PROVIDED if date_posted else FieldOrigin.UNKNOWN,
            "job_url": FieldOrigin.SOURCE_PROVIDED,
            "min_experience": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if min_exp is not None else FieldOrigin.UNKNOWN,
            "max_experience": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if max_exp is not None else FieldOrigin.UNKNOWN,
            "work_mode": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if mode_ev else FieldOrigin.SOURCE_PROVIDED,
        }

        rec = JobRecord(
            source="greenhouse",
            source_job_id=job_id,
            collection_method="greenhouse_public_api",
            verification_status=VerificationStatus.VERIFIED,
            title=title,
            company=board.company_name,
            location=std_loc,
            country="India",
            work_mode=work_mode,
            remote_eligibility=remote_elig,
            description=plain_desc,
            date_posted=date_posted,
            date_collected=now_iso,
            job_url=job_url,
            application_url=job_url,  # Greenhouse direct apply form is on the same absolute_url
            min_experience=min_exp,
            max_experience=max_exp,
            mandatory_experience=exp_wording,
            preferred_experience=f"{pref_exp}+ years" if pref_exp else None,
            source_metadata=metadata,
            field_origins=field_origins,
            matched_keywords=[matched_kw] if matched_kw else [],
            matched_locations=[location_name],
            matched_search_types=["official_ats_board"],
            matched_run_ids=[run_id],
        )
        rec.compute_quality_indicators()
        return rec


class LeverAdapter(BaseATSAdapter):
    platform_name: str = "lever"

    def fetch_raw_board(self, board: EmployerBoard) -> list[dict[str, Any]]:
        url = f"https://api.lever.co/v0/postings/{board.board_identifier}?mode=json"
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def parse_record(self, raw: dict[str, Any], board: EmployerBoard, run_id: str) -> JobRecord | None:
        title = str(raw.get("text") or "").strip()
        plain_desc = str(raw.get("descriptionPlain") or "").strip()
        if not plain_desc and raw.get("description"):
            plain_desc = html_to_plain_text(raw.get("description"))

        # 1. Cybersecurity Filter
        is_sec, matched_kw = is_cybersecurity_role(title, plain_desc)
        if not is_sec:
            return None

        # 2. Location & Remote Filter
        categories = raw.get("categories") or {}
        location_name = str(categories.get("location") or "India").strip()
        workplace_type = str(raw.get("workplaceType") or "").lower()
        is_remote_flag = workplace_type == "remote"

        is_eligible, std_loc, remote_elig = is_india_or_remote_eligible(location_name, plain_desc, is_remote_flag)
        if not is_eligible:
            return None

        # 3. Field Extraction
        job_id = str(raw.get("id"))
        job_url = normalize_ats_url(raw.get("hostedUrl"))
        app_url = normalize_ats_url(raw.get("applyUrl"))
        date_posted = parse_iso_or_epoch_date(raw.get("createdAt"))
        desc_type = classify_description_type(plain_desc)

        # Experience extraction
        min_exp, max_exp, pref_exp, exp_wording = extract_experience_from_text(plain_desc)

        # Work Mode
        work_mode = WorkMode.UNKNOWN
        if workplace_type == "remote":
            work_mode = WorkMode.REMOTE
        elif workplace_type == "hybrid":
            work_mode = WorkMode.HYBRID
        elif workplace_type == "onsite":
            work_mode = WorkMode.ONSITE
        else:
            work_mode, _ = extract_work_mode(title, location_name, plain_desc)

        # Employment type
        commitment = categories.get("commitment")

        now_iso = datetime.now(timezone.utc).isoformat()
        metadata = {
            "ats_platform": "lever",
            "board_identifier": board.board_identifier,
            "source_job_id": job_id,
            "description_type": desc_type.value,
            "categories": categories,
            "workplace_type": workplace_type,
            "matched_filter_keyword": matched_kw,
            "raw_experience_wording": exp_wording,
        }

        field_origins = {
            "title": FieldOrigin.SOURCE_PROVIDED,
            "company": FieldOrigin.SOURCE_PROVIDED,
            "location": FieldOrigin.SOURCE_PROVIDED,
            "description": FieldOrigin.SOURCE_PROVIDED,
            "date_posted": FieldOrigin.SOURCE_PROVIDED if date_posted else FieldOrigin.UNKNOWN,
            "job_url": FieldOrigin.SOURCE_PROVIDED,
            "application_url": FieldOrigin.SOURCE_PROVIDED if app_url else FieldOrigin.UNKNOWN,
            "employment_type": FieldOrigin.SOURCE_PROVIDED if commitment else FieldOrigin.UNKNOWN,
            "work_mode": FieldOrigin.SOURCE_PROVIDED if workplace_type else FieldOrigin.UNKNOWN,
            "min_experience": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if min_exp is not None else FieldOrigin.UNKNOWN,
            "max_experience": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if max_exp is not None else FieldOrigin.UNKNOWN,
        }

        rec = JobRecord(
            source="lever",
            source_job_id=job_id,
            collection_method="lever_public_api",
            verification_status=VerificationStatus.VERIFIED,
            title=title,
            company=board.company_name,
            location=std_loc,
            country="India",
            work_mode=work_mode,
            remote_eligibility=remote_elig,
            description=plain_desc,
            date_posted=date_posted,
            date_collected=now_iso,
            job_url=job_url,
            application_url=app_url,
            employment_type=commitment,
            min_experience=min_exp,
            max_experience=max_exp,
            mandatory_experience=exp_wording,
            preferred_experience=f"{pref_exp}+ years" if pref_exp else None,
            source_metadata=metadata,
            field_origins=field_origins,
            matched_keywords=[matched_kw] if matched_kw else [],
            matched_locations=[location_name],
            matched_search_types=["official_ats_board"],
            matched_run_ids=[run_id],
        )
        rec.compute_quality_indicators()
        return rec


class AshbyAdapter(BaseATSAdapter):
    platform_name: str = "ashby"

    def fetch_raw_board(self, board: EmployerBoard) -> list[dict[str, Any]]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{board.board_identifier}"
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        return data.get("jobs", [])

    def parse_record(self, raw: dict[str, Any], board: EmployerBoard, run_id: str) -> JobRecord | None:
        title = str(raw.get("title") or "").strip()
        plain_desc = str(raw.get("descriptionPlain") or "").strip()
        if not plain_desc and raw.get("descriptionHtml"):
            plain_desc = html_to_plain_text(raw.get("descriptionHtml"))

        # 1. Cybersecurity Filter
        is_sec, matched_kw = is_cybersecurity_role(title, plain_desc)
        if not is_sec:
            return None

        # 2. Location & Remote Filter
        location_name = str(raw.get("location") or "India").strip()
        is_remote_flag = bool(raw.get("isRemote", False))

        is_eligible, std_loc, remote_elig = is_india_or_remote_eligible(location_name, plain_desc, is_remote_flag)
        if not is_eligible:
            return None

        # 3. Field Extraction
        job_id = str(raw.get("id"))
        job_url = normalize_ats_url(raw.get("jobUrl"))
        app_url = normalize_ats_url(raw.get("applyUrl"))
        date_posted = parse_iso_or_epoch_date(raw.get("publishedAt"))
        desc_type = classify_description_type(plain_desc)

        # Experience extraction
        min_exp, max_exp, pref_exp, exp_wording = extract_experience_from_text(plain_desc)

        # Work Mode
        work_mode = WorkMode.REMOTE if is_remote_flag else WorkMode.UNKNOWN
        if work_mode == WorkMode.UNKNOWN:
            work_mode, _ = extract_work_mode(title, location_name, plain_desc)

        # Employment type
        emp_type = raw.get("employmentType")

        now_iso = datetime.now(timezone.utc).isoformat()
        metadata = {
            "ats_platform": "ashby",
            "board_identifier": board.board_identifier,
            "source_job_id": job_id,
            "description_type": desc_type.value,
            "department": raw.get("department"),
            "is_remote_flag": is_remote_flag,
            "matched_filter_keyword": matched_kw,
            "raw_experience_wording": exp_wording,
        }

        field_origins = {
            "title": FieldOrigin.SOURCE_PROVIDED,
            "company": FieldOrigin.SOURCE_PROVIDED,
            "location": FieldOrigin.SOURCE_PROVIDED,
            "description": FieldOrigin.SOURCE_PROVIDED,
            "date_posted": FieldOrigin.SOURCE_PROVIDED if date_posted else FieldOrigin.UNKNOWN,
            "job_url": FieldOrigin.SOURCE_PROVIDED,
            "application_url": FieldOrigin.SOURCE_PROVIDED if app_url else FieldOrigin.UNKNOWN,
            "employment_type": FieldOrigin.SOURCE_PROVIDED if emp_type else FieldOrigin.UNKNOWN,
            "work_mode": FieldOrigin.SOURCE_PROVIDED if is_remote_flag else FieldOrigin.UNKNOWN,
            "min_experience": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if min_exp is not None else FieldOrigin.UNKNOWN,
            "max_experience": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if max_exp is not None else FieldOrigin.UNKNOWN,
        }

        rec = JobRecord(
            source="ashby",
            source_job_id=job_id,
            collection_method="ashby_public_api",
            verification_status=VerificationStatus.VERIFIED,
            title=title,
            company=board.company_name,
            location=std_loc,
            country="India",
            work_mode=work_mode,
            remote_eligibility=remote_elig,
            description=plain_desc,
            date_posted=date_posted,
            date_collected=now_iso,
            job_url=job_url,
            application_url=app_url,
            employment_type=emp_type,
            min_experience=min_exp,
            max_experience=max_exp,
            mandatory_experience=exp_wording,
            preferred_experience=f"{pref_exp}+ years" if pref_exp else None,
            source_metadata=metadata,
            field_origins=field_origins,
            matched_keywords=[matched_kw] if matched_kw else [],
            matched_locations=[location_name],
            matched_search_types=["official_ats_board"],
            matched_run_ids=[run_id],
        )
        rec.compute_quality_indicators()
        return rec
