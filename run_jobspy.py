"""
India-focused JobSpy collector with:
- separate city and India-remote source lists
- Bayt excluded from primary collection
- graceful LinkedIn stop after HTTP 429 / block warnings
- exact-URL deduplication that preserves search provenance
- checkpoints, error logs, and data-quality report

Run from this directory:
    python run_jobspy.py
"""

import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
from jobspy import scrape_jobs

from config import (
    SITES,
    CITY_SITES,
    REMOTE_SITES,
    SEARCHES,
    RESULTS_PER_SEARCH,
    POSTED_WITHIN_HOURS,
    COUNTRY_INDEED,
    FETCH_DESCRIPTIONS,
    DELAY_BETWEEN_SEARCHES_SECONDS,
    STOP_LINKEDIN_AFTER_RATE_LIMIT,
    VERBOSE,
)

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "jobspy"
OUTPUT.mkdir(parents=True, exist_ok=True)

JSON_OUTPUT = OUTPUT / "jobspy_all_jobs.json"
CSV_OUTPUT = OUTPUT / "jobspy_all_jobs.csv"
ERROR_OUTPUT = OUTPUT / "jobspy_errors.json"
SUMMARY_OUTPUT = OUTPUT / "jobspy_run_summary.json"
QUALITY_OUTPUT = OUTPUT / "jobspy_quality_report.json"
CHECKPOINT_OUTPUT = OUTPUT / "checkpoint.jsonl"

run_started = datetime.now(timezone.utc)
run_id = run_started.strftime("%Y%m%d_%H%M%S")
raw_results = []
run_errors = []
search_stats = []
disabled_sites = set()


def write_json_atomic(path, data):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False, default=str)
    temp_path.replace(path)


def json_safe_rows(dataframe):
    # Convert pandas NaN/NaT values to JSON null before serialization.
    safe = dataframe.astype(object).where(pd.notna(dataframe), None)
    return safe.to_dict(orient="records")


def flatten_text(value):
    if isinstance(value, dict):
        return " ".join(flatten_text(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(flatten_text(v) for v in value)
    return str(value or "")


def record_issue(issue_type, search, details, site="unknown"):
    run_errors.append({
        "run_id": run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "site": str(site).lower(),
        "keyword": search["keyword"],
        "location": search["location"],
        "search_type": search.get("search_type", "unspecified"),
        "type": issue_type,
        "details": flatten_text(details)[:4000],
    })


def save_checkpoint():
    temp_path = CHECKPOINT_OUTPUT.with_suffix(".jsonl.tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        for row in raw_results:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    temp_path.replace(CHECKPOINT_OUTPUT)


def normalize_url(url):
    """Remove fragments and common tracking parameters, retain job identifiers."""
    if not url or not isinstance(url, str):
        return ""
    try:
        parts = urlsplit(url.strip())
        if not parts.netloc:
            return url.strip()
        host = parts.netloc.lower().removeprefix("www.")
        path = parts.path.rstrip("/")
        tracking = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term",
            "utm_content", "trk", "trackingid", "ref", "from",
            "source", "campaignid", "gclid", "fbclid",
        }
        query_items = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            key_lower = key.lower()
            if key_lower.startswith("utm_") or key_lower in tracking:
                continue
            query_items.append((key, value))
        query_items.sort()
        query = urlencode(query_items, doseq=True)
        return urlunsplit(("https", host, path, query, ""))
    except Exception:
        return url.strip()


def provenance_values(row, field):
    value = row.get(field)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value).strip()
    return [text] if text else []


def merge_records(rows):
    """
    Deduplicate by normalized URL only.
    URL-less records are retained separately to avoid false-positive merges.
    All search query provenance is aggregated into arrays.
    """
    merged = {}
    no_url_rows = []
    raw_with_url = 0

    provenance_fields = {
        "matched_keywords": "test_keyword",
        "matched_locations": "test_location",
        "matched_search_types": "test_search_type",
        "matched_run_ids": "run_id",
    }

    for index, source_row in enumerate(rows):
        row = dict(source_row)
        url = row.get("job_url") or row.get("job_url_direct") or ""
        key = normalize_url(str(url))

        if not key:
            row["deduplication_key"] = f"url_missing:{index}"
            for out_field, in_field in provenance_fields.items():
                row[out_field] = provenance_values(row, in_field)
            no_url_rows.append(row)
            continue

        raw_with_url += 1
        row["deduplication_key"] = key

        if key not in merged:
            for out_field, in_field in provenance_fields.items():
                row[out_field] = provenance_values(row, in_field)
            merged[key] = row
            continue

        existing = merged[key]

        # Preserve all query paths that discovered this listing.
        for out_field, in_field in provenance_fields.items():
            combined = existing.get(out_field, []) + provenance_values(row, in_field)
            existing[out_field] = list(dict.fromkeys(
                str(item) for item in combined if str(item).strip()
            ))

        # Fill missing fields and retain the longer description if both exist.
        for field, value in row.items():
            if field in provenance_fields.values() or field == "deduplication_key":
                continue
            old = existing.get(field)
            old_empty = old is None or (isinstance(old, float) and pd.isna(old)) or str(old).strip() in ("", "nan", "None")
            new_empty = value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() in ("", "nan", "None")
            if old_empty and not new_empty:
                existing[field] = value
            elif field == "description" and not new_empty and not old_empty:
                if len(str(value)) > len(str(old)):
                    existing[field] = value

    return list(merged.values()) + no_url_rows, raw_with_url, len(no_url_rows)


def missing_field_stats(records):
    fields = [
        "title", "company", "location", "job_url", "description",
        "job_type", "experience_range", "date_posted",
    ]
    total = len(records)
    result = {}
    for field in fields:
        missing = 0
        for row in records:
            value = row.get(field)
            if value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() in ("", "nan", "None"):
                missing += 1
        result[field] = {
            "missing": missing,
            "present": total - missing,
            "missing_percent": round((missing / total) * 100, 2) if total else 0,
        }
    return result


def detect_linkedin_rate_limit(errors, warnings):
    text = (flatten_text(errors) + " " + flatten_text(warnings)).lower()
    indicators = [
        "429", "too many requests", "blocked: use a proxy",
        "rate limit", "http 429",
    ]
    return any(item in text for item in indicators)


print("=" * 72)
print("JOBSPY COLLECTION — QUALITY-FIRST CONFIGURATION")
print("=" * 72)
print("Run ID:", run_id)
print("Configured sites:", ", ".join(SITES))
print("City sources:", ", ".join(CITY_SITES))
print("Remote sources:", ", ".join(REMOTE_SITES))
print("Search combinations:", len(SEARCHES))
print("Results per source/search:", RESULTS_PER_SEARCH)
print("Posting window (hours):", POSTED_WITHIN_HOURS)
print("Description fetching:", FETCH_DESCRIPTIONS)
print("Bayt: excluded")
print("=" * 72)

CHECKPOINT_OUTPUT.write_text("", encoding="utf-8")

for index, search in enumerate(SEARCHES, start=1):
    is_remote = bool(search.get("is_remote"))
    requested_sites = REMOTE_SITES if is_remote else CITY_SITES
    active_sites = [site for site in requested_sites if site not in disabled_sites]

    label = "REMOTE INDIA" if is_remote else "CITY"
    print(f"\n[{index}/{len(SEARCHES)}] {label}: {search['keyword']} | {search['location']}")

    if not active_sites:
        print("Skipped: no active sources remain for this search.")
        search_stats.append({
            **search, "sites_requested": requested_sites,
            "sites_run": [], "records_returned": 0,
            "records_by_site": {}, "status": "skipped_no_active_sources",
        })
        continue

    started = time.time()
    returned = 0
    site_counts = {}
    status = "completed"

    try:
        jobs = scrape_jobs(
            site_name=active_sites,
            search_term=search["keyword"],
            location=search["location"],
            is_remote=is_remote,
            results_wanted=RESULTS_PER_SEARCH,
            hours_old=POSTED_WITHIN_HOURS,
            country_indeed=COUNTRY_INDEED,
            fetch_description=FETCH_DESCRIPTIONS,
            verbose=VERBOSE,
        )

        errors = getattr(jobs, "attrs", {}).get("errors", {}) or {}
        warnings = getattr(jobs, "attrs", {}).get("warnings", {}) or {}

        for site, details in errors.items():
            record_issue("scraper_error", search, details, site)
        for site, details in warnings.items():
            record_issue("scraper_warning", search, details, site)

        if STOP_LINKEDIN_AFTER_RATE_LIMIT and "linkedin" in active_sites:
            linkedin_errors = errors.get("linkedin", "")
            linkedin_warnings = warnings.get("linkedin", "")
            if detect_linkedin_rate_limit(linkedin_errors, linkedin_warnings):
                disabled_sites.add("linkedin")
                record_issue(
                    "source_disabled_rate_limit",
                    search,
                    "LinkedIn returned a 429/rate-limit/block signal; "
                    "LinkedIn will be skipped for the rest of this run.",
                    "linkedin",
                )
                print("LinkedIn rate-limit/block signal detected. "
                      "LinkedIn disabled for the remainder of this run.")

        rows = json_safe_rows(jobs)
        collected_at = datetime.now(timezone.utc).isoformat()

        for row in rows:
            row["test_keyword"] = search["keyword"]
            row["test_location"] = search["location"]
            row["test_is_remote"] = is_remote
            row["test_search_type"] = search.get("search_type", "unspecified")
            row["benchmark_site"] = str(row.get("site") or "").lower()
            row["collected_at"] = collected_at
            row["run_id"] = run_id
            raw_results.append(row)

            site = str(row.get("site") or "unknown").lower()
            site_counts[site] = site_counts.get(site, 0) + 1

        returned = len(rows)
        if errors:
            status = "completed_with_errors"
        print("Returned:", returned, "| by source:", site_counts or "{}")
        if warnings:
            print("Warnings:", warnings)
        if errors:
            print("Errors:", errors)

    except Exception as exc:
        status = "exception"
        record_issue("search_exception", search, repr(exc))
        print("Search failed:", repr(exc))

    elapsed = round(time.time() - started, 2)
    search_stats.append({
        **search,
        "sites_requested": requested_sites,
        "sites_run": active_sites,
        "records_returned": returned,
        "records_by_site": site_counts,
        "elapsed_seconds": elapsed,
        "status": status,
    })

    save_checkpoint()
    write_json_atomic(ERROR_OUTPUT, run_errors)
    print(f"Completed in {elapsed}s")

    if index < len(SEARCHES):
        delay = random.uniform(*DELAY_BETWEEN_SEARCHES_SECONDS)
        print(f"Waiting {delay:.1f}s...")
        time.sleep(delay)

# Final deduplication and output.
deduped, raw_with_url, url_missing_count = merge_records(raw_results)
df = pd.DataFrame(deduped)

if not df.empty:
    df.to_csv(CSV_OUTPUT, index=False, encoding="utf-8-sig")
    df.to_json(
        JSON_OUTPUT, orient="records", indent=2,
        force_ascii=False, date_format="iso",
    )
else:
    pd.DataFrame().to_csv(CSV_OUTPUT, index=False, encoding="utf-8-sig")
    write_json_atomic(JSON_OUTPUT, [])

run_finished = datetime.now(timezone.utc)
site_counts_final = {}
if not df.empty and "site" in df.columns:
    site_counts_final = (
        df["site"].fillna("unknown").astype(str).str.lower()
        .value_counts().to_dict()
    )

quality = {
    "run_id": run_id,
    "unique_records": len(deduped),
    "field_completeness": missing_field_stats(deduped),
    "records_by_site_after_dedup": site_counts_final,
    "records_missing_job_url": url_missing_count,
    "remote_search_records": sum(
        1 for row in deduped
        if "remote_india" in row.get("matched_search_types", [])
    ),
    "notes": [
        "Exact normalized URL deduplication is performed within the collection.",
        "Cross-platform near-duplicate jobs are not automatically merged.",
        "Remote search membership is not proof that a role is India-eligible; "
        "verify location and work authorization before applying.",
        "Date-only posting values do not reveal the exact posting time.",
        "Description and experience completeness must be checked before matching.",
    ],
}

summary = {
    "run_id": run_id,
    "started_at_utc": run_started.isoformat(),
    "finished_at_utc": run_finished.isoformat(),
    "duration_seconds": round((run_finished - run_started).total_seconds(), 2),
    "configured_sites": SITES,
    "city_sites": CITY_SITES,
    "remote_sites": REMOTE_SITES,
    "disabled_sites_during_run": sorted(disabled_sites),
    "search_combinations_configured": len(SEARCHES),
    "search_combinations_recorded": len(search_stats),
    "raw_records_returned": len(raw_results),
    "raw_records_with_job_url": raw_with_url,
    "unique_records_after_normalized_url_dedup": len(deduped),
    "duplicate_url_occurrences_removed": max(0, raw_with_url - (len(deduped) - url_missing_count)),
    "records_without_job_url_retained": url_missing_count,
    "issues_recorded": len(run_errors),
    "records_by_site_after_dedup": site_counts_final,
    "search_stats": search_stats,
    "configuration": {
        "results_per_search": RESULTS_PER_SEARCH,
        "posted_within_hours": POSTED_WITHIN_HOURS,
        "country_indeed": COUNTRY_INDEED,
        "fetch_descriptions": FETCH_DESCRIPTIONS,
        "delay_seconds": list(DELAY_BETWEEN_SEARCHES_SECONDS),
    },
    "output_files": {
        "jobs_json": str(JSON_OUTPUT),
        "jobs_csv": str(CSV_OUTPUT),
        "errors_json": str(ERROR_OUTPUT),
        "quality_report_json": str(QUALITY_OUTPUT),
        "checkpoint_jsonl": str(CHECKPOINT_OUTPUT),
    },
}

write_json_atomic(ERROR_OUTPUT, run_errors)
write_json_atomic(QUALITY_OUTPUT, quality)
write_json_atomic(SUMMARY_OUTPUT, summary)

print("\n" + "=" * 72)
print("FINAL COLLECTION SUMMARY")
print("=" * 72)
print("Raw records:", len(raw_results))
print("Unique records:", len(deduped))
print("Duplicate URL occurrences removed:", summary["duplicate_url_occurrences_removed"])
print("URL-less records retained:", url_missing_count)
print("Issues:", len(run_errors))
print("Sources:", site_counts_final)
print("LinkedIn disabled during run:", "linkedin" in disabled_sites)
print("Jobs JSON:", JSON_OUTPUT)
print("Jobs CSV:", CSV_OUTPUT)
print("Errors:", ERROR_OUTPUT)
print("Quality report:", QUALITY_OUTPUT)
print("Run summary:", SUMMARY_OUTPUT)
print("Checkpoint:", CHECKPOINT_OUTPUT)
print("=" * 72)
