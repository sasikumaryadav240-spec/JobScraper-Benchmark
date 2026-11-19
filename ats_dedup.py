"""
ATS Deduplication Engine.

Implements 3-tier duplicate detection across official ATS job collections:
- Tier 1: ATS platform + stable source job ID (e.g. 'greenhouse:123456', 'lever:uuid', 'ashby:uuid')
- Tier 2: Normalized original job URL
- Tier 3: Content fingerprint (exact normalized company + title + location)

Never merges distinct jobs merely because company and title match.
Preserves duplicate counts, provenance, and audit logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from models import JobRecord
from ats_adapters import normalize_ats_url


def _normalize_str(val: str | None) -> str:
    if not val:
        return ""
    text = str(val).strip().lower()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def build_ats_content_fingerprint(record: JobRecord) -> str:
    """
    Build content fingerprint: company | title | location.
    Requires all three components to prevent false positive merging across distinct openings.
    """
    comp = _normalize_str(record.company)
    title = _normalize_str(record.title)
    loc = _normalize_str(record.location)
    return f"{comp}|{title}|{loc}"


@dataclass
class ATSDeduplicationAudit:
    raw_records_count: int = 0
    unique_records_count: int = 0
    duplicates_removed_total: int = 0
    tier1_job_id_merges: int = 0
    tier2_url_merges: int = 0
    tier3_content_merges: int = 0
    duplicate_records_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_records_count": self.raw_records_count,
            "unique_records_count": self.unique_records_count,
            "duplicates_removed_total": self.duplicates_removed_total,
            "tier1_job_id_merges": self.tier1_job_id_merges,
            "tier2_url_merges": self.tier2_url_merges,
            "tier3_content_merges": self.tier3_content_merges,
            "duplicate_audit_log_count": len(self.duplicate_records_log),
        }


def merge_ats_records(primary: JobRecord, duplicate: JobRecord) -> JobRecord:
    """
    Merge duplicate into primary by backfilling missing fields and aggregating search provenance.
    If both records possess descriptions, retains the longer, more comprehensive description.
    """
    # Backfill or upgrade description
    if not primary.description and duplicate.description:
        primary.description = duplicate.description
        primary.field_origins["description"] = duplicate.field_origins.get("description", primary.field_origins.get("description"))
        primary.source_metadata["description_type"] = duplicate.source_metadata.get("description_type")
    elif primary.description and duplicate.description:
        if len(str(duplicate.description)) > len(str(primary.description)):
            primary.description = duplicate.description
            primary.source_metadata["description_type"] = duplicate.source_metadata.get("description_type")

    # Backfill experience
    if primary.min_experience is None and duplicate.min_experience is not None:
        primary.min_experience = duplicate.min_experience
        primary.max_experience = duplicate.max_experience
        primary.mandatory_experience = duplicate.mandatory_experience
        primary.preferred_experience = duplicate.preferred_experience
        primary.field_origins["min_experience"] = duplicate.field_origins.get("min_experience")
        primary.field_origins["max_experience"] = duplicate.field_origins.get("max_experience")
        primary.source_metadata["raw_experience_wording"] = duplicate.source_metadata.get("raw_experience_wording")

    # Backfill application URL
    if not primary.application_url and duplicate.application_url:
        primary.application_url = duplicate.application_url
        primary.field_origins["application_url"] = duplicate.field_origins.get("application_url")

    # Merge search provenance
    for kw in duplicate.matched_keywords:
        if kw not in primary.matched_keywords:
            primary.matched_keywords.append(kw)

    for loc in duplicate.matched_locations:
        if loc not in primary.matched_locations:
            primary.matched_locations.append(loc)

    for st in duplicate.matched_search_types:
        if st not in primary.matched_search_types:
            primary.matched_search_types.append(st)

    for run in duplicate.matched_run_ids:
        if run not in primary.matched_run_ids:
            primary.matched_run_ids.append(run)

    primary.source_metadata["duplicate_count"] = primary.source_metadata.get("duplicate_count", 0) + 1

    # Recalculate quality
    primary.quality_indicators = primary.compute_quality_indicators()
    return primary


def deduplicate_ats_records(records: list[JobRecord]) -> tuple[list[JobRecord], ATSDeduplicationAudit]:
    """
    Perform 3-tier deduplication over canonical ATS JobRecords:
      Tier 1: By platform:source_job_id
      Tier 2: By normalized original job URL
      Tier 3: By exact content fingerprint (company|title|location)
    """
    audit = ATSDeduplicationAudit(raw_records_count=len(records))

    by_platform_id: dict[str, JobRecord] = {}
    by_url: dict[str, JobRecord] = {}
    by_content: dict[str, JobRecord] = {}
    unique_records: list[JobRecord] = []

    for rec in records:
        source_id = rec.source_job_id or rec.source_metadata.get("source_job_id")
        platform = rec.source
        platform_key = f"{platform}:{source_id}" if source_id else None
        norm_url = normalize_ats_url(rec.job_url)
        content_key = build_ats_content_fingerprint(rec)

        merged = False

        # Tier 1: platform + source_job_id
        if platform_key and platform_key in by_platform_id:
            primary = by_platform_id[platform_key]
            merge_ats_records(primary, rec)
            audit.tier1_job_id_merges += 1
            audit.duplicates_removed_total += 1
            audit.duplicate_records_log.append({
                "tier": "tier1_platform_job_id",
                "key": platform_key,
                "title": rec.title,
                "company": rec.company,
                "location": rec.location,
            })
            merged = True

        # Tier 2: normalized URL
        elif norm_url and norm_url in by_url:
            primary = by_url[norm_url]
            merge_ats_records(primary, rec)
            audit.tier2_url_merges += 1
            audit.duplicates_removed_total += 1
            audit.duplicate_records_log.append({
                "tier": "tier2_normalized_url",
                "key": norm_url,
                "title": rec.title,
                "company": rec.company,
                "location": rec.location,
            })
            merged = True

        # Tier 3: content fingerprint
        elif content_key and content_key in by_content:
            primary = by_content[content_key]
            merge_ats_records(primary, rec)
            audit.tier3_content_merges += 1
            audit.duplicates_removed_total += 1
            audit.duplicate_records_log.append({
                "tier": "tier3_content_fingerprint",
                "key": content_key,
                "title": rec.title,
                "company": rec.company,
                "location": rec.location,
            })
            merged = True

        if not merged:
            unique_records.append(rec)
            if platform_key:
                by_platform_id[platform_key] = rec
            if norm_url:
                by_url[norm_url] = rec
            if content_key:
                by_content[content_key] = rec

    audit.unique_records_count = len(unique_records)
    return unique_records, audit

