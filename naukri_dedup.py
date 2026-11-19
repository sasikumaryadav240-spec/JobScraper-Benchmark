"""
Naukri deduplication engine.

Implements 3-tier duplicate detection:
- Tier 1: Stable source job ID ('jobId', e.g. '171126013925')
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
from naukri_parser import clean_text_token, normalize_naukri_url


def _normalize_str(val: str | None) -> str:
    if not val:
        return ""
    text = clean_text_token(val).lower()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def build_naukri_content_fingerprint(record: JobRecord) -> str:
    """
    Build content fingerprint: company | title | location.
    Requires all three to prevent false positive merging across distinct openings.
    """
    comp = _normalize_str(record.company)
    title = _normalize_str(record.title)
    loc = _normalize_str(record.location)
    return f"{comp}|{title}|{loc}"


@dataclass
class NaukriDeduplicationAudit:
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


def merge_naukri_records(primary: JobRecord, duplicate: JobRecord) -> JobRecord:
    """
    Merge duplicate into primary by backfilling missing fields and merging provenance.
    """
    # Backfill description if missing
    if not primary.description and duplicate.description:
        primary.description = duplicate.description
        primary.field_origins["description"] = duplicate.field_origins.get("description", primary.field_origins.get("description"))

    # Backfill experience
    if primary.min_experience is None and duplicate.min_experience is not None:
        primary.min_experience = duplicate.min_experience
        primary.max_experience = duplicate.max_experience
        primary.mandatory_experience = duplicate.mandatory_experience
        primary.field_origins["experience"] = duplicate.field_origins.get("experience", primary.field_origins.get("experience"))

    # Backfill application URL
    if not primary.application_url and duplicate.application_url:
        primary.application_url = duplicate.application_url

    # Merge search provenance
    for kw in duplicate.matched_keywords:
        if kw not in primary.matched_keywords:
            primary.matched_keywords.append(kw)

    for loc in duplicate.matched_locations:
        if loc not in primary.matched_locations:
            primary.matched_locations.append(loc)

    for run in duplicate.matched_run_ids:
        if run not in primary.matched_run_ids:
            primary.matched_run_ids.append(run)

    primary.source_metadata["duplicate_count"] = primary.source_metadata.get("duplicate_count", 0) + 1

    # Recalculate quality
    primary.quality_indicators = primary.compute_quality_indicators()
    return primary


def deduplicate_naukri_records(
    records: list[JobRecord],
) -> tuple[list[JobRecord], NaukriDeduplicationAudit]:
    """
    Deduplicate a list of JobRecord instances using the 3-tier hierarchy.
    """
    audit = NaukriDeduplicationAudit(raw_records_count=len(records))

    unique_by_id: dict[str, JobRecord] = {}
    unique_by_url: dict[str, JobRecord] = {}
    unique_by_fingerprint: dict[str, JobRecord] = {}

    final_unique: list[JobRecord] = []

    for rec in records:
        matched_primary: JobRecord | None = None
        merge_tier: str | None = None

        # Tier 1: Source Job ID
        if rec.source_job_id and rec.source_job_id in unique_by_id:
            matched_primary = unique_by_id[rec.source_job_id]
            merge_tier = "tier1_job_id"
            audit.tier1_job_id_merges += 1

        # Tier 2: Normalized Job URL
        elif rec.job_url:
            norm_url = normalize_naukri_url(rec.job_url)
            if norm_url and norm_url in unique_by_url:
                matched_primary = unique_by_url[norm_url]
                merge_tier = "tier2_url"
                audit.tier2_url_merges += 1

        # Tier 3: Content Fingerprint
        if not matched_primary:
            fp = build_naukri_content_fingerprint(rec)
            if fp and fp in unique_by_fingerprint:
                matched_primary = unique_by_fingerprint[fp]
                merge_tier = "tier3_content"
                audit.tier3_content_merges += 1

        if matched_primary:
            # Merge duplicate into existing primary
            audit.duplicates_removed_total += 1
            audit.duplicate_records_log.append({
                "tier": merge_tier,
                "primary_id": matched_primary.source_job_id,
                "duplicate_id": rec.source_job_id,
                "title": rec.title,
                "company": rec.company,
                "url": rec.job_url,
            })
            merge_naukri_records(matched_primary, rec)
        else:
            # First time seeing this job
            final_unique.append(rec)
            if rec.source_job_id:
                unique_by_id[rec.source_job_id] = rec
            if rec.job_url:
                norm_url = normalize_naukri_url(rec.job_url)
                if norm_url:
                    unique_by_url[norm_url] = rec
            fp = build_naukri_content_fingerprint(rec)
            if fp:
                unique_by_fingerprint[fp] = rec

    audit.unique_records_count = len(final_unique)
    return final_unique, audit
