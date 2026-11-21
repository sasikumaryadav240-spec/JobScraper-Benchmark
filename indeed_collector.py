"""
Standalone Indeed Collector Core.

Manages resilient Indeed scraping with:
- Bounded retries and exponential backoff
- Rate-limit detection and categorization
- Stateful checkpointing and resumption
- Comprehensive field extraction (experience, work mode, salary, URLs)
- Duplicate detection and quality reporting
"""

import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
from jobspy import scrape_jobs

import indeed_config as config
from indeed_dedup import deduplicate_indeed_records
from indeed_parser import (
    extract_experience,
    extract_indeed_job_key,
    extract_salary_info,
    extract_work_mode,
)
from models import FieldOrigin, JobRecord, QualityIndicators, VerificationStatus, WorkMode
from request_pacer import (
    CircuitBreaker,
    calculate_backoff,
    get_global_pacer,
    install_request_pacer,
    parse_retry_after,
)


def load_indeed_checkpoint(path: Path) -> Tuple[List[Dict[str, Any]], Set[Tuple[str, str, bool]]]:
    """Load existing Indeed checkpoint and return (records, completed_search_keys)."""
    records = []
    completed_searches = set()
    if not path.exists():
        return records, completed_searches

    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line_str = line.strip()
                if not line_str:
                    continue
                row = json.loads(line_str)
                records.append(row)
                kw = row.get("test_keyword")
                loc = row.get("test_location")
                is_rem = bool(row.get("test_is_remote", False))
                if kw and loc:
                    completed_searches.add((kw, loc, is_rem))
    except Exception as exc:
        print(f"Warning: Failed reading Indeed checkpoint at {path}: {exc}")

    return records, completed_searches


def append_indeed_checkpoint(records: List[Dict[str, Any]], path: Path) -> None:
    """Append newly collected records to checkpoint file."""
    with open(path, "a", encoding="utf-8") as handle:
        for row in records:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        handle.flush()


def categorize_error(error_str: str) -> str:
    """Classify error into actionable category."""
    lower = str(error_str).lower()
    if any(k in lower for k in ["429", "too many requests", "rate limit", "rate-limit"]):
        return "rate_limited"
    if any(k in lower for k in ["403", "blocked", "captcha", "forbidden"]):
        return "blocked_or_access_denied"
    if any(k in lower for k in ["timeout", "timed out", "connection error", "reset by peer"]):
        return "network_transient"
    return "api_or_parse_error"


class IndeedCollector:
    """
    Production-grade Indeed cybersecurity job collector.
    """

    def __init__(
        self,
        results_per_search: int = config.RESULTS_PER_SEARCH,
        posted_within_hours: int = config.POSTED_WITHIN_HOURS,
        country: str = config.INDEED_COUNTRY,
        delay_range: Tuple[float, float] = config.DELAY_BETWEEN_SEARCHES_SECONDS,
        max_retries: int = config.MAX_RETRIES,
        backoff_factor: float = config.RETRY_BACKOFF_FACTOR,
        checkpoint_path: Path = config.CHECKPOINT_OUTPUT,
    ):
        self.results_per_search = results_per_search
        self.posted_within_hours = posted_within_hours
        self.country = country
        self.delay_range = delay_range
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.checkpoint_path = checkpoint_path

        self.pacer = install_request_pacer()
        self.circuit_breaker = CircuitBreaker("indeed", consecutive_failure_threshold=3, cooldown_seconds=60.0)
        self.resumed_from_checkpoint_count = 0
        self.current_run_raw_count = 0

    def scrape_single_search(
        self,
        search: Dict[str, Any],
        run_id: str
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
        """
        Execute a single search combination on Indeed with bounded retries.
        Returns: (raw_records, error_records, status)
        """
        keyword = search["keyword"]
        location = search["location"]
        is_remote = bool(search.get("is_remote", False))
        search_type = search.get("search_type", "city")

        records: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        status = "completed"

        for attempt in range(1, self.max_retries + 1):
            try:
                jobs_df = scrape_jobs(
                    site_name="indeed",
                    search_term=keyword,
                    location=location,
                    is_remote=is_remote,
                    results_wanted=self.results_per_search,
                    hours_old=self.posted_within_hours,
                    country_indeed=self.country,
                    fetch_description=True,
                    verbose=0,
                )

                scraper_errors = getattr(jobs_df, "attrs", {}).get("errors", {}) or {}
                if "indeed" in scraper_errors:
                    err_msg = scraper_errors["indeed"]
                    cat = categorize_error(err_msg)
                    errors.append({
                        "run_id": run_id,
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "keyword": keyword,
                        "location": location,
                        "search_type": search_type,
                        "attempt": attempt,
                        "error_category": cat,
                        "details": str(err_msg),
                    })
                    if cat in ("rate_limited", "blocked_or_access_denied"):
                        status = "blocked_or_rate_limited"
                        break

                if jobs_df is not None and not jobs_df.empty:
                    # Convert pandas NaNs to None
                    safe_rows = jobs_df.astype(object).where(pd.notna(jobs_df), None).to_dict(orient="records")
                    collected_at = datetime.now(timezone.utc).isoformat()
                    for r in safe_rows:
                        r["test_keyword"] = keyword
                        r["test_location"] = location
                        r["test_is_remote"] = is_remote
                        r["test_search_type"] = search_type
                        r["benchmark_site"] = "indeed"
                        r["collected_at"] = collected_at
                        r["run_id"] = run_id
                        records.append(r)
                return records, errors, status

            except Exception as exc:
                cat = categorize_error(str(exc))
                errors.append({
                    "run_id": run_id,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "keyword": keyword,
                    "location": location,
                    "search_type": search_type,
                    "attempt": attempt,
                    "error_category": cat,
                    "details": repr(exc),
                })
                if attempt < self.max_retries and cat == "network_transient":
                    backoff = self.backoff_factor ** attempt
                    print(f"Transient error on {keyword} | {location}. Retrying in {backoff:.1f}s...")
                    time.sleep(backoff)
                else:
                    status = "failed"
                    break

        return records, errors, status

    def transform_record(self, raw: Dict[str, Any], run_id: str) -> Dict[str, Any]:
        """
        Apply comprehensive parsing and extraction to raw Indeed record.
        """
        title = str(raw.get("title") or "").strip()
        company = str(raw.get("company") or "").strip()
        loc = str(raw.get("location") or "").strip()
        desc = raw.get("description")
        is_remote_flag = bool(raw.get("is_remote", False))
        raw_id = raw.get("id")
        job_url = raw.get("job_url")
        direct_apply_url = raw.get("job_url_direct")

        # 1. Experience extraction
        min_exp, max_exp, mandatory_exp, pref_exp, raw_exp_text = extract_experience(desc)

        # 2. Work mode & remote eligibility extraction
        work_mode, remote_eligibility, geo_restrictions = extract_work_mode(loc, desc, is_remote_flag)

        # 3. Salary extraction
        salary = extract_salary_info(raw)

        # 4. Indeed Job Key
        jk = extract_indeed_job_key(raw_id, job_url)

        # 5. Field origin indicators
        field_origins = {
            "title": FieldOrigin.SOURCE_PROVIDED if title else FieldOrigin.UNKNOWN,
            "company": FieldOrigin.SOURCE_PROVIDED if company else FieldOrigin.UNKNOWN,
            "location": FieldOrigin.SOURCE_PROVIDED if loc else FieldOrigin.UNKNOWN,
            "description": FieldOrigin.SOURCE_PROVIDED if desc else FieldOrigin.UNKNOWN,
            "date_posted": FieldOrigin.SOURCE_PROVIDED if raw.get("date_posted") else FieldOrigin.UNKNOWN,
            "job_url": FieldOrigin.SOURCE_PROVIDED if job_url else FieldOrigin.UNKNOWN,
            "application_url": FieldOrigin.SOURCE_PROVIDED if direct_apply_url else FieldOrigin.UNKNOWN,
            "salary": FieldOrigin.SOURCE_PROVIDED if salary["has_salary"] else FieldOrigin.UNKNOWN,
            "min_experience": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if min_exp is not None else FieldOrigin.UNKNOWN,
            "max_experience": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if max_exp is not None else FieldOrigin.UNKNOWN,
            "experience_range": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if (min_exp is not None or max_exp is not None) else FieldOrigin.UNKNOWN,
            "work_mode": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if work_mode != WorkMode.UNKNOWN else FieldOrigin.UNKNOWN,
            "remote_eligibility": FieldOrigin.EXTRACTED_FROM_DESCRIPTION if remote_eligibility else FieldOrigin.UNKNOWN,
        }

        # 6. Assemble standardized dict
        enriched = {
            "source": "indeed",
            "source_job_id": jk or raw_id,
            "indeed_job_key": jk,
            "title": title or "Untitled Job",
            "company": company or "Unknown Company",
            "location": loc or None,
            "work_mode": work_mode.value if hasattr(work_mode, "value") else str(work_mode),
            "remote_eligibility": remote_eligibility,
            "geographic_restrictions": geo_restrictions,
            "employment_type": raw.get("job_type"),
            "description": desc,
            "min_experience": min_exp,
            "max_experience": max_exp,
            "mandatory_experience": mandatory_exp,
            "preferred_experience": pref_exp,
            "experience_raw_text": raw_exp_text,
            "date_posted": raw.get("date_posted"),
            "date_collected": raw.get("collected_at") or datetime.now(timezone.utc).isoformat(),
            "job_url": job_url,
            "application_url": direct_apply_url,
            "url_verification_status": "unverified",
            "salary_min": salary["min_amount"],
            "salary_max": salary["max_amount"],
            "salary_currency": salary["currency"],
            "salary_interval": salary["interval"],
            "field_origins": {k: v.value if hasattr(v, "value") else str(v) for k, v in field_origins.items()},
            "test_keyword": raw.get("test_keyword"),
            "test_location": raw.get("test_location"),
            "test_is_remote": is_remote_flag,
            "test_search_type": raw.get("test_search_type"),
            "benchmark_site": "indeed",
            "run_id": run_id,
            "matched_keywords": raw.get("matched_keywords", []),
            "matched_locations": raw.get("matched_locations", []),
            "matched_search_types": raw.get("matched_search_types", []),
            "matched_run_ids": raw.get("matched_run_ids", []),
        }

        return enriched

    def run(
        self,
        searches: List[Dict[str, Any]] = config.SEARCHES,
        resume: bool = True,
        clean: bool = False,
        limit_searches: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute collection across configured search plan.
        """
        run_started = datetime.now(timezone.utc)
        run_id = f"indeed_{run_started.strftime('%Y%m%d_%H%M%S')}"
        raw_results: List[Dict[str, Any]] = []
        run_errors: List[Dict[str, Any]] = []
        search_stats: List[Dict[str, Any]] = []

        target_searches = searches[:limit_searches] if limit_searches else searches
        completed_searches: Set[Tuple[str, str, bool]] = set()

        if clean:
            print("Clean run requested: clearing Indeed checkpoint.")
            self.checkpoint_path.write_text("", encoding="utf-8")
        elif resume and self.checkpoint_path.exists():
            raw_results, completed_searches = load_indeed_checkpoint(self.checkpoint_path)
            if raw_results:
                print(f"Resuming Indeed run: loaded {len(raw_results)} records across {len(completed_searches)} prior search keys.")

        print("=" * 72)
        print("INDEED STANDALONE CYBERSECURITY JOB COLLECTOR")
        print("=" * 72)
        print(f"Run ID: {run_id}")
        print(f"Search combinations configured: {len(target_searches)}")
        print(f"Results per search: {self.results_per_search}")
        print(f"Posting window (hours): {self.posted_within_hours}")
        print(f"Delay range (seconds): {self.delay_range}")
        print(f"Checkpoint path: {self.checkpoint_path}")
        print(f"Dry run: {dry_run}")
        print("=" * 72)

        if dry_run:
            print("Dry run complete: No searches executed.")
            return {
                "run_id": run_id,
                "dry_run": True,
                "target_searches": len(target_searches),
                "loaded_from_checkpoint": len(raw_results),
            }

        consecutive_blocks = 0
        searches_completed_count = 0
        searches_failed_count = 0
        searches_skipped_count = 0

        for index, search in enumerate(target_searches, start=1):
            kw = search["keyword"]
            loc = search["location"]
            is_rem = bool(search.get("is_remote", False))
            search_key = (kw, loc, is_rem)
            label = "REMOTE INDIA" if is_rem else "CITY"

            print(f"\n[{index}/{len(target_searches)}] {label}: {kw} | {loc}")

            if search_key in completed_searches:
                print("Skipped: already completed in prior checkpoint run.")
                searches_skipped_count += 1
                search_stats.append({
                    **search,
                    "records_returned": 0,
                    "status": "skipped_already_completed",
                })
                continue

            if not self.circuit_breaker.can_proceed():
                print("WARNING: Indeed circuit breaker is ACTIVE. Pausing search loop to protect access.")
                run_errors.append({
                    "run_id": run_id,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "type": "collection_paused_rate_limit",
                    "details": "Collector paused after consecutive rate-limit / block signals from Indeed.",
                })
                break

            started = time.time()
            records, errors, status = self.scrape_single_search(search, run_id)
            elapsed = round(time.time() - started, 2)

            run_errors.extend(errors)

            if status == "blocked_or_rate_limited":
                consecutive_blocks += 1
                searches_failed_count += 1
                self.circuit_breaker.record_failure("rate_limited_or_blocked", status_code=429)
                print(f"Returned: {len(records)} | Status: {status} (NOT added to completed searches) | Elapsed: {elapsed}s")
            elif status == "failed":
                searches_failed_count += 1
                self.circuit_breaker.record_failure("network_or_api_failure")
                print(f"Returned: {len(records)} | Status: {status} (NOT added to completed searches) | Elapsed: {elapsed}s")
            else:
                consecutive_blocks = 0
                searches_completed_count += 1
                self.circuit_breaker.record_success()
                completed_searches.add(search_key)
                print(f"Returned: {len(records)} | Status: {status} | Elapsed: {elapsed}s")

            if records:
                append_indeed_checkpoint(records, self.checkpoint_path)
                raw_results.extend(records)
                self.current_run_raw_count += len(records)

            search_stats.append({
                **search,
                "records_returned": len(records),
                "elapsed_seconds": elapsed,
                "status": status,
            })

            if self.circuit_breaker.is_open:
                print("WARNING: Indeed circuit breaker tripped. Stopping further searches.")
                break

            if index < len(target_searches):
                delay = random.uniform(*self.delay_range)
                print(f"Delay: {delay:.1f}s...")
                time.sleep(delay)

        # ---------------------------------------------------------------------
        # Deduplication & Transformation
        # ---------------------------------------------------------------------
        print("\n" + "=" * 72)
        print("PROCESSING & DEDUPLICATING INDEED RECORDS")
        print("=" * 72)

        deduped_raw, raw_count, dup_count, audit_log = deduplicate_indeed_records(raw_results)
        print(f"Raw records: {raw_count} | Unique records: {len(deduped_raw)} | Duplicates removed: {dup_count}")

        # Enrich and validate records
        enriched_jobs = [self.transform_record(r, run_id) for r in deduped_raw]

        # Calculate field completeness metrics
        total = len(enriched_jobs)
        field_stats = {}
        tracked_fields = [
            "title", "company", "location", "description", "date_posted",
            "employment_type", "job_url", "application_url",
            "min_experience", "work_mode", "remote_eligibility", "salary_min"
        ]
        for f in tracked_fields:
            if f in ("min_experience", "salary_min"):
                pop = sum(1 for j in enriched_jobs if j.get(f) is not None)
            elif f == "work_mode":
                pop = sum(1 for j in enriched_jobs if j.get(f) and j.get(f) != "unknown")
            else:
                pop = sum(1 for j in enriched_jobs if j.get(f) and str(j.get(f)).strip() not in ("", "None", "nan"))
            field_stats[f] = {
                "populated": pop,
                "missing": total - pop,
                "coverage_pct": round((pop / total * 100), 2) if total else 0.0,
            }

        # Work mode breakdown
        work_mode_counts = {}
        for j in enriched_jobs:
            wm = j.get("work_mode", "unknown")
            work_mode_counts[wm] = work_mode_counts.get(wm, 0) + 1

        # Remote eligibility breakdown
        remote_eligibility_counts = {}
        for j in enriched_jobs:
            re_val = j.get("remote_eligibility") or "not_applicable"
            remote_eligibility_counts[re_val] = remote_eligibility_counts.get(re_val, 0) + 1

        # Save files
        df = pd.DataFrame(enriched_jobs)
        if not df.empty:
            df.to_csv(config.CSV_OUTPUT, index=False, encoding="utf-8-sig")
            with open(config.JSON_OUTPUT, "w", encoding="utf-8") as f:
                json.dump(enriched_jobs, f, indent=2, ensure_ascii=False, default=str)
        else:
            pd.DataFrame().to_csv(config.CSV_OUTPUT, index=False, encoding="utf-8-sig")
            with open(config.JSON_OUTPUT, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2)

        run_finished = datetime.now(timezone.utc)
        quality_report = {
            "run_id": run_id,
            "collector": "IndeedStandaloneCollector",
            "unique_records": total,
            "field_coverage": field_stats,
            "work_mode_distribution": work_mode_counts,
            "remote_eligibility_distribution": remote_eligibility_counts,
            "notes": [
                "Experience extraction parses mandatory minimums and maximums from descriptions.",
                "Unmentioned experience is recorded as None (not assumed zero).",
                "Work mode distinguishes hybrid work arrangements from hybrid cloud infrastructure.",
                "Remote eligibility requires explicit India supporting evidence to be marked india_only.",
                "Job URLs are direct Indeed listing URLs; application URLs point to employer career portals where provided.",
            ],
        }

        run_summary = {
            "run_id": run_id,
            "started_at_utc": run_started.isoformat(),
            "finished_at_utc": run_finished.isoformat(),
            "duration_seconds": round((run_finished - run_started).total_seconds(), 2),
            "search_combinations_configured": len(target_searches),
            "search_combinations_recorded": len(search_stats),
            "searches_planned": len(target_searches),
            "searches_completed": searches_completed_count,
            "searches_failed": searches_failed_count,
            "searches_skipped": searches_skipped_count,
            "searches_resumed": len(completed_searches),
            "raw_records_returned": raw_count,
            "raw_records_collected": self.current_run_raw_count,
            "raw_records_from_checkpoint": len(raw_results) - self.current_run_raw_count,
            "unique_records_after_dedup": total,
            "duplicates_removed": dup_count,
            "issues_recorded": len(run_errors),
            "circuit_breaker_stats": self.circuit_breaker.to_dict(),
            "field_coverage_summary": {k: v["coverage_pct"] for k, v in field_stats.items()},
            "work_mode_distribution": work_mode_counts,
            "remote_eligibility_distribution": remote_eligibility_counts,
            "search_stats": search_stats,
            "output_files": {
                "json": str(config.JSON_OUTPUT),
                "csv": str(config.CSV_OUTPUT),
                "errors": str(config.ERROR_OUTPUT),
                "quality_report": str(config.QUALITY_OUTPUT),
                "summary": str(config.SUMMARY_OUTPUT),
                "checkpoint": str(self.checkpoint_path),
            },
        }

        with open(config.QUALITY_OUTPUT, "w", encoding="utf-8") as f:
            json.dump(quality_report, f, indent=2, ensure_ascii=False)

        with open(config.SUMMARY_OUTPUT, "w", encoding="utf-8") as f:
            json.dump(run_summary, f, indent=2, ensure_ascii=False)

        with open(config.ERROR_OUTPUT, "w", encoding="utf-8") as f:
            json.dump(run_errors, f, indent=2, ensure_ascii=False)

        print("\n" + "=" * 72)
        print("INDEED COLLECTION COMPLETE")
        print("=" * 72)
        print(f"Raw records: {raw_count}")
        print(f"Unique records: {total}")
        print(f"Duplicates removed: {dup_count}")
        print(f"Descriptions populated: {field_stats['description']['coverage_pct']}%")
        print(f"Experience extracted: {field_stats['min_experience']['coverage_pct']}%")
        print(f"Direct Application URLs: {field_stats['application_url']['coverage_pct']}%")
        print(f"Work modes: {work_mode_counts}")
        print(f"Remote eligibility: {remote_eligibility_counts}")
        print(f"Errors/issues: {len(run_errors)}")
        print(f"Output JSON: {config.JSON_OUTPUT}")
        print(f"Output CSV: {config.CSV_OUTPUT}")
        print("=" * 72)

        return run_summary
