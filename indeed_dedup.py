"""
Indeed Deduplication Module.

Implements multi-tier, safe deduplication for Indeed job records:
Tier 1: Explicit Indeed Job Key (jk) e.g. '587eb9fc6b17e8d9'
Tier 2: Normalized Canonical Job URL
Tier 3: Content Fingerprint (clean company + clean title + clean location + description hash)

Preserves duplicate counts, raw records, and merges query provenance.
"""

import hashlib
import html
import re
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from indeed_parser import extract_indeed_job_key


def clean_text_token(value: Any) -> str:
    """Normalize text for identity comparison."""
    if not value:
        return ""
    val = html.unescape(str(value))
    val = re.sub(r"<[^>]+>", " ", val)
    val = val.lower().strip()
    val = re.sub(r"[^a-z0-9]+", " ", val)
    return re.sub(r"\s+", " ", val).strip()


def compute_description_hash(desc: Any) -> str:
    """Compute SHA-256 hash of cleaned description text."""
    cleaned = clean_text_token(desc)
    if not cleaned or len(cleaned) < 20:
        return ""
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def normalize_indeed_url(url: Any) -> str:
    """Normalize an Indeed job URL."""
    if not url or not isinstance(url, str):
        return ""
    try:
        parts = urlsplit(url.strip())
        if not parts.netloc:
            return url.strip()
        host = parts.netloc.lower().removeprefix("www.")
        path = parts.path.rstrip("/")
        # Extract jk if present in query
        tracking = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term",
            "utm_content", "from", "ref", "trk", "fbclid", "gclid",
        }
        query_items = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() not in tracking:
                query_items.append((key, value))
        query_items.sort()
        query = urlencode(query_items, doseq=True)
        return urlunsplit(("https", host, path, query, ""))
    except Exception:
        return url.strip()


def get_indeed_dedup_key(record: Dict[str, Any]) -> Tuple[str, str]:
    """
    Generate unique deduplication key and tier.
    Returns: (key, tier)
    """
    raw_id = record.get("id") or record.get("source_job_id")
    job_url = record.get("job_url")

    # Tier 1: Indeed Job Key (jk)
    jk = extract_indeed_job_key(raw_id, job_url)
    if jk:
        return f"INDEED_JK:{jk}", "tier1_job_key"

    # Tier 2: Normalized Canonical URL
    norm_url = normalize_indeed_url(job_url)
    if norm_url:
        return f"URL:{norm_url}", "tier2_normalized_url"

    # Tier 3: Content Fingerprint
    company_token = clean_text_token(record.get("company"))
    title_token = clean_text_token(record.get("title"))
    loc_token = clean_text_token(record.get("location"))
    desc_hash = compute_description_hash(record.get("description"))

    if company_token and title_token and desc_hash:
        return f"FINGERPRINT:{company_token}|{title_token}|{loc_token}|{desc_hash}", "tier3_content_fingerprint"

    # Fallback: No reliable identifier available, retain record safely
    return "", "unkeyed"


def deduplicate_indeed_records(
    records: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], int, int, List[Dict[str, Any]]]:
    """
    Deduplicate Indeed records while aggregating query provenance and maintaining audit logs.
    Returns:
        (deduplicated_records, raw_count, duplicate_count, duplicate_audit_log)
    """
    unique_map: Dict[str, Dict[str, Any]] = {}
    unkeyed_records: List[Dict[str, Any]] = []
    duplicate_audit_log: List[Dict[str, Any]] = []
    raw_count = len(records)
    duplicate_count = 0

    provenance_keys = {
        "matched_keywords": "test_keyword",
        "matched_locations": "test_location",
        "matched_search_types": "test_search_type",
        "matched_run_ids": "run_id",
    }

    for index, raw_item in enumerate(records):
        row = dict(raw_item)
        key, tier = get_indeed_dedup_key(row)

        if not key:
            row["deduplication_key"] = f"indeed_unkeyed:{index}"
            row["dedup_tier"] = "unkeyed"
            for out_k, in_k in provenance_keys.items():
                val = str(row.get(in_k) or "").strip()
                row[out_k] = [val] if val else []
            unkeyed_records.append(row)
            continue

        row["deduplication_key"] = key
        row["dedup_tier"] = tier

        if key not in unique_map:
            for out_k, in_k in provenance_keys.items():
                val = str(row.get(in_k) or "").strip()
                row[out_k] = [val] if val else []
            unique_map[key] = row
            continue

        # Duplicate detected
        duplicate_count += 1
        existing = unique_map[key]

        # Preserve duplicate details in audit log
        duplicate_audit_log.append({
            "dedup_key": key,
            "dedup_tier": tier,
            "kept_title": existing.get("title"),
            "kept_company": existing.get("company"),
            "kept_url": existing.get("job_url"),
            "removed_title": row.get("title"),
            "removed_company": row.get("company"),
            "removed_url": row.get("job_url"),
            "duplicate_source_location": row.get("test_location"),
            "duplicate_source_keyword": row.get("test_keyword"),
        })

        # Merge search provenance
        for out_k, in_k in provenance_keys.items():
            existing_arr = existing.get(out_k, [])
            new_val = str(row.get(in_k) or "").strip()
            if new_val and new_val not in existing_arr:
                existing_arr.append(new_val)
            existing[out_k] = existing_arr

        # Preserve richer fields (longer description, direct application URL, salary info)
        old_desc = str(existing.get("description") or "")
        new_desc = str(row.get("description") or "")
        if len(new_desc) > len(old_desc):
            existing["description"] = row["description"]

        if not existing.get("job_url_direct") and row.get("job_url_direct"):
            existing["job_url_direct"] = row["job_url_direct"]

        if not existing.get("min_amount") and row.get("min_amount"):
            for sal_k in ["min_amount", "max_amount", "currency", "interval"]:
                existing[sal_k] = row.get(sal_k)

    deduped_records = list(unique_map.values()) + unkeyed_records
    return deduped_records, raw_count, duplicate_count, duplicate_audit_log

