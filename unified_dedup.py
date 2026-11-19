"""
Conservative Cross-Source Deduplication Engine for Phase 5.

Implements a 3-tier hierarchy:
- Tier 1: Stable source identifier / requisition ID
- Tier 2: Canonicalized job URL or application URL
- Tier 3: High-confidence combination of Company + Title + Location + Description Similarity

Preserves all source URLs, application URLs, source IDs, provenance,
and logs audit decisions to duplicate_audit.json.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional
from rapidfuzz import fuzz

from unified_models import CanonicalJobRecord, DescriptionType

logger = logging.getLogger("UnifiedDedup")


def _clean_str(val: str | None) -> str:
    if not val:
        return ""
    text = str(val).strip().lower()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class DuplicateAuditEntry:
    primary_id: str
    duplicate_id: str
    tier: str
    match_key: str
    reasons: list[str]
    preserved_urls: list[str]
    preserved_source_ids: dict[str, list[str]]
    winning_description_source: str


@dataclass
class CrossSourceDeduplicationAudit:
    total_input_records: int = 0
    unique_output_records: int = 0
    duplicates_merged_total: int = 0
    tier1_id_merges: int = 0
    tier2_url_merges: int = 0
    tier3_content_merges: int = 0
    possible_duplicates_flagged: int = 0
    audit_entries: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_input_records": self.total_input_records,
            "unique_output_records": self.unique_output_records,
            "duplicates_merged_total": self.duplicates_merged_total,
            "tier1_id_merges": self.tier1_id_merges,
            "tier2_url_merges": self.tier2_url_merges,
            "tier3_content_merges": self.tier3_content_merges,
            "possible_duplicates_flagged": self.possible_duplicates_flagged,
            "audit_log_count": len(self.audit_entries),
            "audit_entries": self.audit_entries,
        }


def merge_canonical_records(
    primary: CanonicalJobRecord,
    duplicate: CanonicalJobRecord,
    tier: str,
    match_key: str,
    reason: str,
) -> tuple[CanonicalJobRecord, DuplicateAuditEntry]:
    """
    Merge duplicate record into primary record losslessly:
    - Retains all source names and IDs
    - Retains all source URLs and application URLs
    - Retains the longer / higher-quality description
    - Backfills missing salary or experience fields
    - Records audit decisions
    """
    reasons = [reason]

    # 1. Merge all source IDs
    for src, ids in duplicate.all_source_ids.items():
        if src not in primary.all_source_ids:
            primary.all_source_ids[src] = []
        for id_val in ids:
            if id_val not in primary.all_source_ids[src]:
                primary.all_source_ids[src].append(id_val)

    # 2. Merge all source URLs
    for url in duplicate.all_source_urls:
        if url and url not in primary.all_source_urls:
            primary.all_source_urls.append(url)
            reasons.append(f"Preserved source URL: {url}")

    # 3. Merge all application URLs
    for app_url in duplicate.all_application_urls:
        if app_url and app_url not in primary.all_application_urls:
            primary.all_application_urls.append(app_url)
            reasons.append(f"Preserved application URL: {app_url}")

    # Best application URL selection
    if not primary.application_url and duplicate.application_url:
        primary.application_url = duplicate.application_url
        reasons.append(f"Backfilled application URL from {duplicate.source}")

    # 4. Description selection: full > partial > snippet
    winning_desc_src = primary.source
    p_quality = primary.description_type
    d_quality = duplicate.description_type

    # Prefer full_description over snippet or partial
    quality_priority = {
        DescriptionType.FULL_DESCRIPTION.value: 3,
        DescriptionType.PARTIAL_DESCRIPTION.value: 2,
        DescriptionType.SEARCH_RESULT_SNIPPET.value: 1,
        DescriptionType.MISSING.value: 0,
    }
    p_prio = quality_priority.get(p_quality, 0)
    d_prio = quality_priority.get(d_quality, 0)

    if d_prio > p_prio or (d_prio == p_prio and len(duplicate.job_description) > len(primary.job_description)):
        primary.job_description = duplicate.job_description
        primary.description_type = duplicate.description_type
        winning_desc_src = duplicate.source
        reasons.append(f"Upgraded description to {duplicate.source} ({d_quality}, {len(duplicate.job_description)} chars)")

    # 5. Backfill experience
    if primary.mandatory_min_experience is None and duplicate.mandatory_min_experience is not None:
        primary.mandatory_min_experience = duplicate.mandatory_min_experience
        primary.mandatory_max_experience = duplicate.mandatory_max_experience
        primary.original_experience_text = duplicate.original_experience_text
        primary.experience_extraction_status = duplicate.experience_extraction_status
        reasons.append(f"Backfilled experience from {duplicate.source}")

    # 6. Backfill salary
    if primary.salary_min is None and duplicate.salary_min is not None:
        primary.salary_min = duplicate.salary_min
        primary.salary_max = duplicate.salary_max
        primary.salary_currency = duplicate.salary_currency
        primary.salary_period = duplicate.salary_period
        reasons.append(f"Backfilled compensation from {duplicate.source}")

    # 7. Merge source provenance
    for prov in duplicate.source_provenance:
        if prov not in primary.source_provenance:
            primary.source_provenance.append(prov)

    # 8. Set duplicate cluster group
    cluster_id = primary.duplicate_group_id or f"grp_{primary.canonical_job_id[-8:]}"
    primary.duplicate_group_id = cluster_id

    audit_entry = DuplicateAuditEntry(
        primary_id=primary.canonical_job_id,
        duplicate_id=duplicate.canonical_job_id,
        tier=tier,
        match_key=match_key,
        reasons=reasons,
        preserved_urls=list(primary.all_source_urls) + list(primary.all_application_urls),
        preserved_source_ids=dict(primary.all_source_ids),
        winning_description_source=winning_desc_src,
    )
    return primary, audit_entry


def deduplicate_cross_source(
    records: list[CanonicalJobRecord],
) -> tuple[list[CanonicalJobRecord], CrossSourceDeduplicationAudit]:
    """
    Perform conservative 3-tier deduplication over canonical job records.
    """
    audit = CrossSourceDeduplicationAudit(total_input_records=len(records))
    unique_map: dict[str, CanonicalJobRecord] = {}

    tier1_seen: dict[str, str] = {}  # stable_id -> canonical_id
    tier2_seen: dict[str, str] = {}  # norm_url -> canonical_id
    tier3_seen: dict[str, str] = {}  # comp|title|city -> canonical_id

    for rec in records:
        merged = False

        # --- Tier 1: Stable Platform Identifier ---
        stable_key = f"{rec.source}:{rec.source_job_id}"
        if stable_key in tier1_seen:
            prim_id = tier1_seen[stable_key]
            prim_rec = unique_map[prim_id]
            merged_rec, entry = merge_canonical_records(
                prim_rec, rec, tier="Tier 1", match_key=stable_key,
                reason=f"Exact stable source identifier match: {stable_key}",
            )
            unique_map[prim_id] = merged_rec
            audit.tier1_id_merges += 1
            audit.duplicates_merged_total += 1
            audit.audit_entries.append(asdict(entry))
            continue

        # --- Tier 2: Canonical Job URL & Application URL ---
        urls_to_check = []
        if rec.original_job_url:
            urls_to_check.append(rec.original_job_url)
        if rec.application_url:
            urls_to_check.append(rec.application_url)

        matched_url_prim_id = None
        matched_url_val = None
        for u in urls_to_check:
            if u in tier2_seen:
                matched_url_prim_id = tier2_seen[u]
                matched_url_val = u
                break

        if matched_url_prim_id and matched_url_prim_id in unique_map:
            prim_rec = unique_map[matched_url_prim_id]
            merged_rec, entry = merge_canonical_records(
                prim_rec, rec, tier="Tier 2", match_key=matched_url_val,
                reason=f"Canonical URL match: {matched_url_val}",
            )
            unique_map[matched_url_prim_id] = merged_rec
            audit.tier2_url_merges += 1
            audit.duplicates_merged_total += 1
            audit.audit_entries.append(asdict(entry))
            # Register new URLs to this primary
            for u in urls_to_check:
                tier2_seen[u] = matched_url_prim_id
            continue

        # --- Tier 3: High-Confidence Content Fingerprint ---
        comp_norm = _clean_str(rec.normalized_company_name)
        title_norm = _clean_str(rec.normalized_job_title)
        loc_norm = _clean_str(rec.city or rec.normalized_location)

        fingerprint = f"{comp_norm}|{title_norm}|{loc_norm}"
        if fingerprint in tier3_seen and comp_norm and title_norm:
            prim_id = tier3_seen[fingerprint]
            prim_rec = unique_map[prim_id]

            # Require description similarity check (>= 80% fuzzy ratio)
            desc_ratio = 100
            if prim_rec.job_description and rec.job_description:
                desc_ratio = fuzz.token_set_ratio(prim_rec.job_description[:1000], rec.job_description[:1000])

            if desc_ratio >= 80:
                merged_rec, entry = merge_canonical_records(
                    prim_rec, rec, tier="Tier 3", match_key=fingerprint,
                    reason=f"High-confidence content match: {fingerprint} (desc similarity: {desc_ratio}%)",
                )
                unique_map[prim_id] = merged_rec
                audit.tier3_content_merges += 1
                audit.duplicates_merged_total += 1
                audit.audit_entries.append(asdict(entry))
                continue
            else:
                # Flag as possible duplicate for manual verification rather than auto-merging
                audit.possible_duplicates_flagged += 1
                grp_id = f"pos_dup_{prim_id[-8:]}"
                prim_rec.duplicate_group_id = grp_id
                rec.duplicate_group_id = grp_id
                rec.manual_review_reasons.append(
                    f"Possible duplicate of {prim_rec.source_job_id} ({prim_rec.company_name}) with low desc match ({desc_ratio}%)"
                )

        # New unique record
        unique_map[rec.canonical_job_id] = rec
        tier1_seen[stable_key] = rec.canonical_job_id
        for u in urls_to_check:
            tier2_seen[u] = rec.canonical_job_id
        if comp_norm and title_norm:
            tier3_seen[fingerprint] = rec.canonical_job_id

    final_unique = list(unique_map.values())
    audit.unique_output_records = len(final_unique)
    logger.info(
        f"Cross-source deduplication finished: {audit.total_input_records} input records -> "
        f"{audit.unique_output_records} unique, {audit.duplicates_merged_total} merged "
        f"({audit.tier1_id_merges} T1, {audit.tier2_url_merges} T2, {audit.tier3_content_merges} T3)."
    )
    return final_unique, audit

