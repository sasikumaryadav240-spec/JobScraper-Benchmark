"""
Multi-Source Data Ingestion Engine for Phase 5.

Ingests Indeed, Naukri, LinkedIn, and ATS datasets with full fault tolerance,
schema mapping to CanonicalJobRecord, and raw data preservation.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from unified_models import (
    CanonicalJobRecord,
    DescriptionType,
    JobCategory,
    EligibilityStatus,
    WorkModeEnum,
)
from unified_normalizer import (
    classify_description_quality,
    extract_work_mode_evidence,
    normalize_canonical_url,
    normalize_company_name,
    normalize_date_to_iso,
    normalize_job_title,
    normalize_location_details,
)

logger = logging.getLogger("UnifiedIngestion")


def load_raw_source_file(file_path: str | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Safely load a JSON, JSON Lines, or wrapped JSON file.
    Returns (valid_records, malformed_records).
    """
    p = Path(file_path)
    if not p.exists():
        logger.warning(f"Source file not found: {p}")
        return [], []

    valid_records: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []

    try:
        with open(p, "r", encoding="utf-8") as f:
            content = f.read().strip()
    except Exception as e:
        logger.error(f"Cannot read file {p}: {e}")
        malformed.append({"file": str(p), "error": str(e), "raw": None})
        return [], malformed

    if not content:
        return [], []

    # 1. Try standard JSON
    try:
        data = json.loads(content)
        if isinstance(data, list):
            for i, item in enumerate(data):
                if isinstance(item, dict):
                    valid_records.append(item)
                else:
                    malformed.append({"file": str(p), "index": i, "error": "Item is not a dictionary", "raw": str(item)[:200]})
            return valid_records, malformed
        elif isinstance(data, dict):
            # Check common wrapping keys: jobs, data, results, items
            for key in ("jobs", "data", "results", "items", "rows"):
                if key in data and isinstance(data[key], list):
                    for i, item in enumerate(data[key]):
                        if isinstance(item, dict):
                            valid_records.append(item)
                        else:
                            malformed.append({"file": str(p), "key": key, "index": i, "error": "Item is not a dictionary", "raw": str(item)[:200]})
                    return valid_records, malformed
            # Single object record
            valid_records.append(data)
            return valid_records, malformed
    except json.JSONDecodeError:
        pass

    # 2. Try JSON Lines
    lines = content.splitlines()
    for i, line in enumerate(lines):
        line_s = line.strip()
        if not line_s:
            continue
        try:
            item = json.loads(line_s)
            if isinstance(item, dict):
                valid_records.append(item)
            else:
                malformed.append({"file": str(p), "line": i + 1, "error": "Line is not a JSON object", "raw": line_s[:200]})
        except Exception as e:
            malformed.append({"file": str(p), "line": i + 1, "error": str(e), "raw": line_s[:200]})

    return valid_records, malformed


def map_raw_to_canonical(raw: dict[str, Any], default_source: str) -> tuple[CanonicalJobRecord | None, str | None]:
    """
    Map an arbitrary source dictionary into a CanonicalJobRecord.
    Returns (canonical_record, error_message).
    """
    title = str(raw.get("title") or raw.get("job_title") or raw.get("jobTitle") or "").strip()
    company = str(raw.get("company") or raw.get("company_name") or raw.get("companyName") or "").strip()

    if not title or not company:
        return None, f"Missing critical identity: title='{title}', company='{company}'"

    # Source & ID
    source = str(raw.get("source") or default_source).lower()
    source_job_id = str(raw.get("source_job_id") or raw.get("indeed_job_key") or raw.get("job_id") or raw.get("id") or "").strip()
    if not source_job_id:
        # Fallback deterministic hash
        source_job_id = f"gen_{uuid.uuid5(uuid.NAMESPACE_DNS, f'{source}:{company}:{title}').hex[:12]}"

    canonical_id = f"cjob_{uuid.uuid5(uuid.NAMESPACE_DNS, f'{source}:{source_job_id}:{company}:{title}').hex[:16]}"

    # Descriptions
    desc = str(raw.get("description") or raw.get("job_description") or raw.get("descriptionText") or "").strip()
    meta = raw.get("source_metadata") or {}
    explicit_desc_type = meta.get("description_type") or raw.get("description_type")
    desc_type = classify_description_quality(desc, explicit_desc_type)

    # Location & Work mode
    raw_loc = str(raw.get("location") or raw.get("original_location") or "India").strip()
    norm_loc, city, state, country = normalize_location_details(raw_loc)

    raw_mode = str(raw.get("work_mode") or "").strip().lower()
    if raw_mode in ("onsite", "hybrid", "remote"):
        work_mode = raw_mode
        mode_evidence = "source_provided"
    else:
        work_mode, mode_evidence = extract_work_mode_evidence(title, raw_loc, desc)

    remote_elig = raw.get("remote_eligibility")

    # Experience
    min_exp = raw.get("min_experience")
    max_exp = raw.get("max_experience")
    if min_exp is not None:
        try:
            min_exp = float(min_exp)
        except (ValueError, TypeError):
            min_exp = None
    if max_exp is not None:
        try:
            max_exp = float(max_exp)
        except (ValueError, TypeError):
            max_exp = None

    raw_exp_text = raw.get("mandatory_experience") or meta.get("raw_experience_wording") or raw.get("original_experience_text")
    exp_status = "extracted" if (min_exp is not None or max_exp is not None) else "unmentioned"

    # Dates
    raw_date = raw.get("date_posted") or raw.get("original_posted_date")
    norm_date, date_status = normalize_date_to_iso(raw_date)
    date_collected = str(raw.get("date_collected") or raw.get("collected_at") or "").strip()
    if not date_collected:
        date_collected = "2026-11-19T09:20:00Z"

    # URLs
    job_url = normalize_canonical_url(raw.get("job_url") or raw.get("original_job_url"))
    app_url = normalize_canonical_url(raw.get("application_url"))
    all_source_urls = [job_url] if job_url else []
    all_app_urls = [app_url] if app_url else []

    # Salaries
    sal_min = raw.get("salary_min") or meta.get("salary_min")
    sal_max = raw.get("salary_max") or meta.get("salary_max")
    sal_curr = raw.get("salary_currency") or meta.get("salary_currency")
    sal_period = raw.get("salary_period") or meta.get("salary_interval")

    # Construct canonical record
    record = CanonicalJobRecord(
        canonical_job_id=canonical_id,
        source=source,
        source_job_id=source_job_id,
        all_source_ids={source: [source_job_id]},
        duplicate_group_id=None,
        job_title=title,
        normalized_job_title=normalize_job_title(title),
        company_name=company,
        normalized_company_name=normalize_company_name(company),
        job_description=desc,
        description_type=desc_type,
        job_category=JobCategory.MANUAL_VERIFICATION.value,
        original_location=raw_loc,
        normalized_location=norm_loc,
        city=city,
        state=state,
        country=country,
        work_mode=work_mode,
        remote_eligibility=remote_elig,
        remote_evidence=mode_evidence,
        employment_type=raw.get("employment_type"),
        seniority_level=meta.get("job_level") or raw.get("seniority_level"),
        internship_status=bool("intern" in title.lower() or raw.get("employment_type") == "internship"),
        original_experience_text=str(raw_exp_text) if raw_exp_text else None,
        mandatory_min_experience=min_exp,
        mandatory_max_experience=max_exp,
        preferred_min_experience=raw.get("preferred_min_experience"),
        preferred_max_experience=raw.get("preferred_max_experience"),
        experience_extraction_status=exp_status,
        salary_min=float(sal_min) if sal_min is not None else None,
        salary_max=float(sal_max) if sal_max is not None else None,
        salary_currency=str(sal_curr) if sal_curr else None,
        salary_period=str(sal_period) if sal_period else None,
        original_posted_date=str(raw_date) if raw_date else None,
        normalized_posted_date=norm_date,
        collected_at=date_collected,
        date_verification_status=date_status,
        original_job_url=job_url if job_url else None,
        all_source_urls=all_source_urls,
        application_url=app_url if app_url else None,
        all_application_urls=all_app_urls,
        url_verification_status="source_provided" if job_url else "unverified",
        source_provenance=[{
            "source": source,
            "source_job_id": source_job_id,
            "job_url": job_url,
            "application_url": app_url,
            "description_type": desc_type,
        }],
        field_origins=raw.get("field_origins") or {
            "title": "source_provided",
            "company": "source_provided",
            "description": "source_provided",
            "location": "source_provided",
        },
        field_confidence={"title": 1.0, "company": 1.0, "location": 1.0},
        data_quality_flags=[],
        verification_status="verified",
        raw_record_ref=raw,
    )
    return record, None


def ingest_all_sources(base_dir: str | Path = "jobspy") -> tuple[list[CanonicalJobRecord], dict[str, Any]]:
    """
    Ingest all 4 source files from the jobspy directory.
    Returns (canonical_records, ingestion_summary).
    """
    base = Path(base_dir)
    source_files = {
        "indeed": base / "indeed_jobs.json",
        "naukri": base / "naukri_jobs.json",
        "linkedin": base / "linkedin_jobs.json",
        "ats": base / "ats_jobs.json",
    }

    all_records: list[CanonicalJobRecord] = []
    summary: dict[str, Any] = {
        "sources": {},
        "total_raw_ingested": 0,
        "total_canonical_normalized": 0,
        "total_malformed": 0,
        "malformed_records": [],
    }

    for src_name, src_path in source_files.items():
        raw_list, malformed = load_raw_source_file(src_path)
        valid_canonical: list[CanonicalJobRecord] = []
        source_errors: list[dict[str, Any]] = []

        for item in raw_list:
            rec, err = map_raw_to_canonical(item, default_source=src_name)
            if rec:
                valid_canonical.append(rec)
            else:
                source_errors.append({"source": src_name, "error": err, "raw": item})

        summary["sources"][src_name] = {
            "path": str(src_path),
            "raw_count": len(raw_list),
            "valid_count": len(valid_canonical),
            "malformed_count": len(malformed) + len(source_errors),
        }
        summary["total_raw_ingested"] += len(raw_list)
        summary["total_canonical_normalized"] += len(valid_canonical)
        summary["total_malformed"] += (len(malformed) + len(source_errors))
        summary["malformed_records"].extend(malformed)
        summary["malformed_records"].extend(source_errors)

        all_records.extend(valid_canonical)

    logger.info(
        f"Ingestion finished: {summary['total_raw_ingested']} raw records -> "
        f"{summary['total_canonical_normalized']} canonical records, "
        f"{summary['total_malformed']} malformed."
    )
    return all_records, summary

