"""
LinkedIn Standalone Collector.

Features:
- Polite request pacing and detail fetch delay to avoid triggering rate limits
- Adaptive bounded exponential backoff on HTTP 429
- Structured error categorization (HTTP 429, HTTP 403, CAPTCHA, Server Error)
- Resilient circuit breaker (cooldown on transient blocks, graceful stop on persistent blocks)
- Atomic checkpointing and stateful search resumption
- 3-tier deduplication and full provenance preservation
- Granular data quality reporting
"""

from __future__ import annotations

import csv
import json
import logging
import random
import re
import time
from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from jobspy import scrape_jobs
from jobspy.linkedin import LinkedIn
from models import JobRecord, WorkMode
from linkedin_config import (
    CIRCUIT_BREAKER_CONSECUTIVE_FAILURES,
    CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    DEFAULT_REQUEST_INTERVAL_SECONDS,
    DELAY_BETWEEN_DETAILS_SECONDS,
    DELAY_BETWEEN_SEARCHES_SECONDS,
    FETCH_DESCRIPTIONS,
    INITIAL_BACKOFF_SECONDS,
    LINKEDIN_CHECKPOINT,
    LINKEDIN_ERRORS_JSON,
    LINKEDIN_JOBS_CSV,
    LINKEDIN_JOBS_JSON,
    LINKEDIN_QUALITY_REPORT,
    LINKEDIN_RUN_SUMMARY,
    MAX_BACKOFF_SECONDS,
    MAX_RETRIES,
    POSTED_WITHIN_HOURS,
    RESULTS_PER_SEARCH,
    build_search_plan,
)
from linkedin_dedup import deduplicate_linkedin_records
from linkedin_parser import DescriptionType, parse_linkedin_record
from request_pacer import (
    CircuitBreaker,
    RequestPacer,
    calculate_backoff,
    get_global_pacer,
    install_request_pacer,
    parse_retry_after,
)

logger = logging.getLogger("LinkedInCollector")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class ErrorCategory(str, Enum):
    HTTP_429_RATE_LIMITED = "http_429_rate_limited"
    HTTP_406_RECAPTCHA_CHALLENGE = "http_406_recaptcha_challenge"
    HTTP_403_ACCESS_BLOCKED = "http_403_access_blocked"
    HTTP_SERVER_ERROR = "http_server_error"
    NETWORK_OR_TIMEOUT = "network_or_timeout"
    CAPTCHA_CHALLENGE = "captcha_challenge"
    UNKNOWN_ERROR = "unknown_error"


def parse_error_details(error_str: str) -> tuple[int | None, ErrorCategory, str]:
    """
    Extract HTTP status code and ErrorCategory from error message.
    """
    if not error_str:
        return None, ErrorCategory.UNKNOWN_ERROR, ""

    s = str(error_str).strip()
    status_match = re.search(r"\b(?:status\s+code\s+|http\s+)(\d{3})\b", s, re.IGNORECASE)
    code = int(status_match.group(1)) if status_match else None

    s_lower = s.lower()
    if code == 429 or "429" in s_lower or "too many requests" in s_lower or "rate limit" in s_lower:
        return code or 429, ErrorCategory.HTTP_429_RATE_LIMITED, s
    if code == 406 or "406" in s_lower or "recaptcha" in s_lower:
        return code or 406, ErrorCategory.HTTP_406_RECAPTCHA_CHALLENGE, s
    if code == 403 or "403" in s_lower or "blocked: use a proxy" in s_lower or "forbidden" in s_lower:
        return code or 403, ErrorCategory.HTTP_403_ACCESS_BLOCKED, s
    if code and code >= 500:
        return code, ErrorCategory.HTTP_SERVER_ERROR, s
    if "timeout" in s_lower or "connection" in s_lower or "failed to establish" in s_lower:
        return code, ErrorCategory.NETWORK_OR_TIMEOUT, s
    if "captcha" in s_lower:
        return code, ErrorCategory.CAPTCHA_CHALLENGE, s

    return code, ErrorCategory.UNKNOWN_ERROR, s


class LinkedInCollector:
    def __init__(
        self,
        results_per_search: int = RESULTS_PER_SEARCH,
        hours_old: int = POSTED_WITHIN_HOURS,
        delays: tuple[float, float] = DELAY_BETWEEN_SEARCHES_SECONDS,
        detail_delays: tuple[float, float] = DELAY_BETWEEN_DETAILS_SECONDS,
        max_retries: int = MAX_RETRIES,
        checkpoint_path: Path = LINKEDIN_CHECKPOINT,
        jobs_json_path: Path = LINKEDIN_JOBS_JSON,
        jobs_csv_path: Path = LINKEDIN_JOBS_CSV,
        errors_path: Path = LINKEDIN_ERRORS_JSON,
        quality_path: Path = LINKEDIN_QUALITY_REPORT,
        summary_path: Path = LINKEDIN_RUN_SUMMARY,
    ) -> None:
        self.run_id = f"linkedin_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self.checkpoint_path = checkpoint_path
        self.jobs_json_path = jobs_json_path
        self.jobs_csv_path = jobs_csv_path
        self.errors_path = errors_path
        self.quality_path = quality_path
        self.summary_path = summary_path

        self.results_per_search = results_per_search
        self.hours_old = hours_old
        self.delays = delays
        self.detail_delays = detail_delays
        self.max_retries = max_retries

        self.completed_searches: set[tuple[str, str, bool]] = set()
        self.raw_records: list[dict[str, Any]] = []
        self.run_errors: list[dict[str, Any]] = []
        self.search_stats: list[dict[str, Any]] = []
        self.circuit_breaker_triggered = False
        self.resumed_from_checkpoint_count = 0
        self.current_run_raw_count = 0

        self.pacer = install_request_pacer()
        self.circuit_breaker = CircuitBreaker(
            source_name="linkedin",
            consecutive_failure_threshold=CIRCUIT_BREAKER_CONSECUTIVE_FAILURES,
            cooldown_seconds=CIRCUIT_BREAKER_COOLDOWN_SECONDS,
        )

        self._patch_jobspy_detail_pacing()

    def _patch_jobspy_detail_pacing(self) -> None:
        """
        Wrap LinkedIn._fetch_details to ensure every job detail request strictly obeys
        centralized request pacing (minimum 10.0s interval) before hitting /jobs/view/{job_id}.
        """
        orig_fetch = getattr(LinkedIn, "_fetch_details", None)
        if not orig_fetch or getattr(LinkedIn, "_detail_pacing_applied", False):
            return

        pacer = self.pacer

        def polite_fetch_details(scraper_self: Any, job_id: str) -> dict[str, object]:
            pacer.wait_before_request(f"{scraper_self.base_url}/jobs/view/{job_id}")
            return orig_fetch(scraper_self, job_id)

        LinkedIn._fetch_details = polite_fetch_details
        LinkedIn._detail_pacing_applied = True
        logger.info("Patched LinkedIn detail fetching with centralized RequestPacer (>= 10.0s interval).")

    def load_checkpoint(self) -> int:
        """
        Load completed searches and existing raw records from checkpoint.jsonl.
        """
        if not self.checkpoint_path.exists():
            logger.info(f"No existing checkpoint found at {self.checkpoint_path}")
            return 0

        count = 0
        try:
            with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        role = record.get("test_keyword")
                        loc = record.get("test_location")
                        is_remote = record.get("test_is_remote", False)
                        if role and loc:
                            self.completed_searches.add((role, loc, is_remote))
                        self.raw_records.append(record)
                        count += 1
                    except json.JSONDecodeError:
                        continue
            self.resumed_from_checkpoint_count = count
            logger.info(
                f"Loaded {count} records and {len(self.completed_searches)} completed searches from checkpoint"
            )
        except Exception as e:
            logger.warning(f"Error reading checkpoint: {e}")
        return count

    def append_checkpoint(self, raw_items: list[dict[str, Any]]) -> None:
        """
        Atomically append new records to checkpoint file.
        """
        if not raw_items:
            return
        with open(self.checkpoint_path, "a", encoding="utf-8") as f:
            for item in raw_items:
                f.write(json.dumps(item, default=str) + "\n")

    def execute_search_with_backoff(
        self,
        role: str,
        location: str,
        is_remote: bool,
    ) -> list[dict[str, Any]]:
        """
        Execute a single LinkedIn search with bounded backoff and error tracking.
        For remote searches, enhances query with 'remote' to ensure relevant listings.
        """
        search_term = f"{role} remote" if is_remote else role
        search_location = location

        records: list[dict[str, Any]] = []

        for attempt in range(self.max_retries + 1):
            retry_occurred = attempt > 0
            try:
                jobs = scrape_jobs(
                    site_name=["linkedin"],
                    search_term=search_term,
                    location=search_location,
                    results_wanted=self.results_per_search,
                    hours_old=self.hours_old,
                    fetch_description=FETCH_DESCRIPTIONS,
                    verbose=0,
                )

                errors = getattr(jobs, "attrs", {}).get("errors", {}) or {}
                linkedin_err = errors.get("linkedin")

                if linkedin_err:
                    status_code, category, msg = parse_error_details(str(linkedin_err))
                    final_outcome = "exhausted_retries" if attempt == self.max_retries else "retrying"

                    error_record = {
                        "run_id": self.run_id,
                        "source": "linkedin",
                        "role": role,
                        "location": location,
                        "is_remote": is_remote,
                        "search_term": search_term,
                        "status_code": status_code,
                        "category": category.value,
                        "error_message": msg,
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "retry_occurred": retry_occurred,
                        "retry_count": attempt,
                        "final_outcome": final_outcome,
                    }
                    self.run_errors.append(error_record)
                    logger.warning(
                        f"LinkedIn search error (attempt {attempt+1}/{self.max_retries+1}) for '{role}' in '{location}': {category.value} ({msg})"
                    )

                    # HTTP 403 Forbidden: Do not hammer
                    if status_code == 403 or category == ErrorCategory.HTTP_403_ACCESS_BLOCKED:
                        logger.warning("Permanent access block (403). Aborting retries for this query.")
                        error_record["final_outcome"] = "fatal_blocked_no_retry"
                        self.circuit_breaker.record_failure(category.value, status_code=403, is_retry=retry_occurred)
                        break

                    # HTTP 429: Rate limited. Apply Retry-After or bounded backoff with jitter
                    if attempt < self.max_retries:
                        # Extract Retry-After if present in message or error string
                        retry_after = None
                        retry_match = re.search(r"retry[-_]after[:\s]+(\d+)", msg, re.IGNORECASE)
                        if retry_match:
                            retry_after = float(retry_match.group(1))

                        sleep_time = retry_after if retry_after else calculate_backoff(
                            attempt + 1,
                            base_backoff=INITIAL_BACKOFF_SECONDS,
                            max_backoff=MAX_BACKOFF_SECONDS,
                        )
                        logger.info(f"Rate limited (429). Backing off for {sleep_time:.1f}s before retry...")
                        self.circuit_breaker.record_failure(category.value, status_code=429, is_retry=retry_occurred)
                        time.sleep(sleep_time)
                        continue
                    else:
                        self.circuit_breaker.record_failure(category.value, status_code=429, is_retry=retry_occurred)
                        break

                if not jobs.empty:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    for _, row in jobs.iterrows():
                        d = row.to_dict()
                        d["test_keyword"] = role
                        d["test_location"] = location
                        d["test_is_remote"] = is_remote
                        d["test_search_type"] = "remote_india" if is_remote else "city"
                        d["collected_at"] = now_iso
                        records.append(d)

                    if retry_occurred and self.run_errors:
                        self.run_errors[-1]["final_outcome"] = "succeeded_on_retry"

                    self.circuit_breaker.record_success()
                    logger.info(
                        f"Search succeeded: '{role}' in '{location}' (remote={is_remote}) -> {len(records)} records"
                    )
                    break
                else:
                    self.circuit_breaker.record_success()
                    logger.info(f"Search returned 0 jobs for '{role}' in '{location}' (remote={is_remote})")
                    break

            except Exception as e:
                status_code, category, msg = parse_error_details(str(e))
                final_outcome = "exhausted_retries" if attempt == self.max_retries else "retrying"
                self.run_errors.append({
                    "run_id": self.run_id,
                    "source": "linkedin",
                    "role": role,
                    "location": location,
                    "is_remote": is_remote,
                    "search_term": search_term,
                    "status_code": status_code,
                    "category": category.value,
                    "error_message": msg,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "retry_occurred": retry_occurred,
                    "retry_count": attempt,
                    "final_outcome": final_outcome,
                })
                logger.error(f"Exception during LinkedIn search for '{role}': {e}")
                self.circuit_breaker.record_failure(category.value, status_code=status_code, is_retry=retry_occurred)
                if attempt < self.max_retries:
                    sleep_time = calculate_backoff(attempt + 1, base_backoff=INITIAL_BACKOFF_SECONDS, max_backoff=MAX_BACKOFF_SECONDS)
                    time.sleep(sleep_time)
                else:
                    break

        return records

    def run(self, plan: list[dict[str, Any]], limit: int | None = None) -> list[JobRecord]:
        """
        Execute collection across planned searches with resumption and circuit breaker.
        """
        self.load_checkpoint()
        target_plan = plan[:limit] if limit else plan
        total = len(target_plan)

        logger.info(f"Starting LinkedIn standalone collection. Planned: {total} searches.")

        searches_completed_count = 0
        searches_failed_count = 0
        searches_skipped_count = 0

        for idx, item in enumerate(target_plan):
            role = item["role"]
            loc = item["location"]
            is_remote = item.get("is_remote", False)
            search_key = (role, loc, is_remote)

            if search_key in self.completed_searches:
                logger.info(f"[{idx+1}/{total}] Skipping already completed search: {role} | {loc} (remote={is_remote})")
                searches_skipped_count += 1
                continue

            if not self.circuit_breaker.can_proceed():
                logger.warning(
                    f"Circuit breaker is ACTIVE. Pausing remaining LinkedIn searches to respect platform limits."
                )
                self.circuit_breaker_triggered = True
                break

            logger.info(f"[{idx+1}/{total}] Executing search: {role} in {loc} (remote={is_remote})...")

            if idx > 0:
                delay = random.uniform(*self.delays)
                time.sleep(delay)

            t0 = time.time()
            records = self.execute_search_with_backoff(role, loc, is_remote)
            duration = round(time.time() - t0, 2)

            recent_err = [
                e for e in self.run_errors
                if e.get("role") == role and e.get("location") == loc and e.get("status_code") in (429, 403, 406)
            ]

            # Critical data integrity: Only mark search completed if clean success
            if records or not recent_err:
                self.completed_searches.add((role, loc, is_remote))
                self.raw_records.extend(records)
                self.current_run_raw_count += len(records)
                self.append_checkpoint(records)
                searches_completed_count += 1
                search_status = "SUCCESS" if records else "EMPTY_SUCCESS"
            else:
                searches_failed_count += 1
                is_429 = any(e.get("status_code") == 429 for e in recent_err)
                search_status = "RATE_LIMITED" if is_429 else "FAILED"
                logger.warning(f"Search for '{role}' in '{loc}' failed ({search_status}); NOT marked completed in checkpoint.")

            self.search_stats.append({
                "role": role,
                "location": loc,
                "is_remote": is_remote,
                "records_returned": len(records),
                "duration_seconds": duration,
                "status": search_status,
            })

            if self.circuit_breaker.is_open:
                logger.warning(
                    f"Circuit breaker tripped during search '{role}'. Stopping collection gracefully."
                )
                self.circuit_breaker_triggered = True
                break

        logger.info("Raw collection phase completed. Processing and deduplicating...")
        return self.process_and_save(
            searches_planned=total,
            searches_completed=searches_completed_count,
            searches_failed=searches_failed_count,
            searches_skipped=searches_skipped_count,
        )

    def process_and_save(
        self,
        searches_planned: int = 0,
        searches_completed: int = 0,
        searches_failed: int = 0,
        searches_skipped: int = 0,
    ) -> list[JobRecord]:
        """
        Transform raw records into canonical JobRecords, deduplicate, and export.
        """
        canonical_records: list[JobRecord] = []
        for raw in self.raw_records:
            rec = parse_linkedin_record(raw, self.run_id)
            if rec:
                canonical_records.append(rec)

        # Deduplicate
        unique_records, dedup_audit = deduplicate_linkedin_records(canonical_records)

        # Write Jobs JSON
        with open(self.jobs_json_path, "w", encoding="utf-8") as f:
            json.dump([r.model_dump(mode="json") for r in unique_records], f, indent=2, default=str)

        # Write Jobs CSV
        if unique_records:
            dict_rows = [r.model_dump(mode="json") for r in unique_records]
            for d in dict_rows:
                d.pop("quality_indicators", None)
                d["source_metadata"] = json.dumps(d.get("source_metadata", {}))
                d["field_origins"] = json.dumps(d.get("field_origins", {}))
                d["matched_keywords"] = json.dumps(d.get("matched_keywords", []))
                d["matched_locations"] = json.dumps(d.get("matched_locations", []))
                d["matched_search_types"] = json.dumps(d.get("matched_search_types", []))
                d["matched_run_ids"] = json.dumps(d.get("matched_run_ids", []))
            keys = list(dict_rows[0].keys())
            with open(self.jobs_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(dict_rows)

        # Write Errors JSON
        with open(self.errors_path, "w", encoding="utf-8") as f:
            json.dump(self.run_errors, f, indent=2)

        # Generate Quality Report
        quality_report = self.generate_quality_report(unique_records, dedup_audit)
        with open(self.quality_path, "w", encoding="utf-8") as f:
            json.dump(quality_report, f, indent=2)

        # Generate Run Summary
        run_summary = {
            "run_id": self.run_id,
            "collector": "LinkedInStandaloneCollector",
            "searches_planned": searches_planned or len(self.completed_searches),
            "searches_completed": searches_completed or len(self.completed_searches),
            "searches_failed": searches_failed,
            "searches_skipped": searches_skipped,
            "searches_resumed": len(self.completed_searches) if self.resumed_from_checkpoint_count else 0,
            "raw_records_collected": self.current_run_raw_count or len(self.raw_records),
            "raw_records_from_checkpoint": self.resumed_from_checkpoint_count,
            "raw_records_total": len(self.raw_records),
            "unique_records_preserved": len(unique_records),
            "circuit_breaker_triggered": self.circuit_breaker_triggered,
            "circuit_breaker_stats": self.circuit_breaker.to_dict(),
            "deduplication": dedup_audit.to_dict(),
            "issues_count": len(self.run_errors),
            "search_stats": self.search_stats,
            "output_files": {
                "jobs_json": str(self.jobs_json_path),
                "jobs_csv": str(self.jobs_csv_path),
                "checkpoint": str(self.checkpoint_path),
                "errors_json": str(self.errors_path),
                "quality_report": str(self.quality_path),
            },
        }
        with open(self.summary_path, "w", encoding="utf-8") as f:
            json.dump(run_summary, f, indent=2)

        logger.info(
            f"Phase 3 processing finished: {len(self.raw_records)} raw -> {len(unique_records)} unique jobs."
        )
        return unique_records

    def generate_quality_report(
        self,
        unique_records: list[JobRecord],
        dedup_audit: Any,
    ) -> dict[str, Any]:
        """
        Calculate detailed data quality indicators across unique records.
        """
        total = len(unique_records)
        if total == 0:
            return {
                "run_id": self.run_id,
                "collector": "LinkedInStandaloneCollector",
                "unique_records": 0,
                "circuit_breaker_triggered": self.circuit_breaker_triggered,
                "error_statistics": {"total_errors_logged": len(self.run_errors)},
            }

        def _count(fn: Any) -> int:
            return sum(1 for r in unique_records if fn(r))

        work_mode_counts: dict[str, int] = {}
        for r in unique_records:
            val = r.work_mode.value if hasattr(r.work_mode, "value") else str(r.work_mode)
            work_mode_counts[val] = work_mode_counts.get(val, 0) + 1

        remote_elig_counts: dict[str, int] = {}
        for r in unique_records:
            val = r.remote_eligibility if r.remote_eligibility else "not_applicable"
            remote_elig_counts[val] = remote_elig_counts.get(val, 0) + 1

        desc_types = [
            r.source_metadata.get("description_type", DescriptionType.MISSING.value)
            for r in unique_records
        ]
        type_counts = Counter(desc_types)
        non_empty_desc = _count(lambda r: bool(r.description and r.description.strip()))

        error_429_count = sum(1 for e in self.run_errors if e.get("status_code") == 429)
        error_403_count = sum(1 for e in self.run_errors if e.get("status_code") == 403)
        error_406_count = sum(1 for e in self.run_errors if e.get("status_code") == 406)
        recovered_via_retry = sum(1 for e in self.run_errors if e.get("final_outcome") == "succeeded_on_retry")
        exhausted_retries = sum(1 for e in self.run_errors if e.get("final_outcome") == "exhausted_retries")

        return {
            "run_id": self.run_id,
            "collector": "LinkedInStandaloneCollector",
            "unique_records": total,
            "circuit_breaker_triggered": self.circuit_breaker_triggered,
            "error_statistics": {
                "total_errors_logged": len(self.run_errors),
                "http_429_rate_limited_count": error_429_count,
                "http_403_access_blocked_count": error_403_count,
                "http_406_recaptcha_challenge_count": error_406_count,
                "recovered_via_retry_count": recovered_via_retry,
                "exhausted_retries_count": exhausted_retries,
            },
            "field_coverage": {
                "title": {"populated": _count(lambda r: bool(r.title)), "coverage_pct": round(_count(lambda r: bool(r.title)) / total * 100, 2)},
                "company": {"populated": _count(lambda r: bool(r.company)), "coverage_pct": round(_count(lambda r: bool(r.company)) / total * 100, 2)},
                "location": {"populated": _count(lambda r: bool(r.location)), "coverage_pct": round(_count(lambda r: bool(r.location)) / total * 100, 2)},
                "date_posted": {"populated": _count(lambda r: r.date_posted is not None), "coverage_pct": round(_count(lambda r: r.date_posted is not None) / total * 100, 2)},
                "job_url": {"populated": _count(lambda r: bool(r.job_url)), "coverage_pct": round(_count(lambda r: bool(r.job_url)) / total * 100, 2)},
                "application_url": {"populated": _count(lambda r: bool(r.application_url)), "coverage_pct": round(_count(lambda r: bool(r.application_url)) / total * 100, 2)},
                "min_experience": {"populated": _count(lambda r: r.min_experience is not None), "coverage_pct": round(_count(lambda r: r.min_experience is not None) / total * 100, 2)},
                "max_experience": {"populated": _count(lambda r: r.max_experience is not None), "coverage_pct": round(_count(lambda r: r.max_experience is not None) / total * 100, 2)},
                "work_mode": {"populated": _count(lambda r: r.work_mode.value != "unknown"), "coverage_pct": round(_count(lambda r: r.work_mode.value != "unknown") / total * 100, 2)},
                "remote_eligibility": {"populated": _count(lambda r: r.remote_eligibility is not None), "coverage_pct": round(_count(lambda r: r.remote_eligibility is not None) / total * 100, 2)},
                "salary": {"populated": _count(lambda r: bool(r.source_metadata.get("salary_min") or r.source_metadata.get("salary_text"))), "coverage_pct": round(_count(lambda r: bool(r.source_metadata.get("salary_min") or r.source_metadata.get("salary_text"))) / total * 100, 2)},
            },
            "description_quality": {
                "non_empty_description_coverage": {
                    "count": non_empty_desc,
                    "coverage_pct": round(non_empty_desc / total * 100, 2),
                },
                "full_description_coverage": {
                    "count": type_counts.get(DescriptionType.FULL_DESCRIPTION.value, 0),
                    "coverage_pct": round(type_counts.get(DescriptionType.FULL_DESCRIPTION.value, 0) / total * 100, 2),
                },
                "search_result_snippet_coverage": {
                    "count": type_counts.get(DescriptionType.SEARCH_RESULT_SNIPPET.value, 0),
                    "coverage_pct": round(type_counts.get(DescriptionType.SEARCH_RESULT_SNIPPET.value, 0) / total * 100, 2),
                },
                "partial_description_coverage": {
                    "count": type_counts.get(DescriptionType.PARTIAL_DESCRIPTION.value, 0),
                    "coverage_pct": round(type_counts.get(DescriptionType.PARTIAL_DESCRIPTION.value, 0) / total * 100, 2),
                },
                "missing_description_coverage": {
                    "count": type_counts.get(DescriptionType.MISSING.value, 0),
                    "coverage_pct": round(type_counts.get(DescriptionType.MISSING.value, 0) / total * 100, 2),
                },
            },
            "work_mode_distribution": work_mode_counts,
            "remote_eligibility_distribution": remote_elig_counts,
            "deduplication": dedup_audit.to_dict(),
        }

