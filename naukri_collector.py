"""
Naukri standalone cybersecurity job collector.

Features:
- Separate HTTP 406, 429, and 403 error classification with structured tracking
- Bounded exponential backoff for transient 406/429 rate challenges
- Immediate abortion of non-retriable 403 blocks
- Stateful checkpointing (resumable across runs)
- Canonical JobRecord transformation via naukri_parser
- 3-tier deduplication via naukri_dedup
- Structured error logging and quality metrics generation distinguishing description types
"""

from __future__ import annotations

import csv
import json
import logging
import random
import time
from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd
from jobspy import scrape_jobs
from jobspy.naukri import Naukri

# Hook into Naukri._process_job to preserve the native search jobDescription snippet
_orig_process_job = Naukri._process_job


def _process_job_preserve_snippet(self, raw: dict[str, Any]):
    post = _orig_process_job(self, raw)
    if post and not post.description:
        post.description = raw.get("jobDescription")
    return post


Naukri._process_job = _process_job_preserve_snippet

from models import JobRecord
from naukri_config import (
    DEFAULT_REQUEST_INTERVAL_SECONDS,
    DELAY_BETWEEN_SEARCHES_SECONDS,
    FETCH_DESCRIPTIONS,
    INITIAL_BACKOFF_SECONDS,
    MAX_BACKOFF_SECONDS,
    MAX_RETRIES,
    NAUKRI_CHECKPOINT,
    NAUKRI_ERRORS_JSON,
    NAUKRI_JOBS_CSV,
    NAUKRI_JOBS_JSON,
    NAUKRI_QUALITY_REPORT,
    NAUKRI_RUN_SUMMARY,
    POSTED_WITHIN_HOURS,
    RESULTS_PER_SEARCH,
    build_search_plan,
)
from naukri_dedup import deduplicate_naukri_records
from naukri_parser import DescriptionType, parse_naukri_record
from request_pacer import (
    CircuitBreaker,
    calculate_backoff,
    get_global_pacer,
    install_request_pacer,
    parse_retry_after,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("naukri_collector")


class ErrorCategory(str, Enum):
    HTTP_406_RECAPTCHA_CHALLENGE = "http_406_recaptcha_challenge"
    HTTP_429_RATE_LIMITED = "http_429_rate_limited"
    HTTP_403_ACCESS_BLOCKED = "http_403_access_blocked"
    HTTP_5XX_SERVER_ERROR = "http_5xx_server_error"
    NETWORK_ERROR = "network_error"
    PARSING_ERROR = "parsing_error"
    UNKNOWN_ERROR = "unknown_error"


def parse_error_details(err_str: str) -> tuple[int | None, ErrorCategory]:
    """
    Explicitly parse response status code and categorize HTTP errors.
    Separates HTTP 406 (Akamai reCAPTCHA), 429 (Rate Limit), and 403 (Access Blocked).
    """
    lower = err_str.lower()
    if "406" in lower or "recaptcha" in lower:
        return 406, ErrorCategory.HTTP_406_RECAPTCHA_CHALLENGE
    if "429" in lower or "too many requests" in lower:
        return 429, ErrorCategory.HTTP_429_RATE_LIMITED
    if "403" in lower or "forbidden" in lower or "blocked" in lower:
        return 403, ErrorCategory.HTTP_403_ACCESS_BLOCKED
    if any(code in lower for code in ("500", "502", "503", "504")):
        return 500, ErrorCategory.HTTP_5XX_SERVER_ERROR
    if any(term in lower for term in ("connection", "timeout", "dns")):
        return None, ErrorCategory.NETWORK_ERROR
    return None, ErrorCategory.UNKNOWN_ERROR


class NaukriCollector:
    def __init__(
        self,
        run_id: str | None = None,
        checkpoint_path: Path = NAUKRI_CHECKPOINT,
        jobs_json_path: Path = NAUKRI_JOBS_JSON,
        jobs_csv_path: Path = NAUKRI_JOBS_CSV,
        errors_path: Path = NAUKRI_ERRORS_JSON,
        summary_path: Path = NAUKRI_RUN_SUMMARY,
        quality_path: Path = NAUKRI_QUALITY_REPORT,
        results_per_search: int = RESULTS_PER_SEARCH,
        hours_old: int = POSTED_WITHIN_HOURS,
        delays: tuple[float, float] = DELAY_BETWEEN_SEARCHES_SECONDS,
        max_retries: int = MAX_RETRIES,
    ):
        self.run_id = run_id or datetime.now(timezone.utc).strftime("naukri_%Y%m%d_%H%M%S")
        self.checkpoint_path = checkpoint_path
        self.jobs_json_path = jobs_json_path
        self.jobs_csv_path = jobs_csv_path
        self.errors_path = errors_path
        self.summary_path = summary_path
        self.quality_path = quality_path

        self.results_per_search = results_per_search
        self.hours_old = hours_old
        self.delays = delays
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
            source_name="naukri",
            consecutive_failure_threshold=3,
            cooldown_seconds=45.0,
        )

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
        Execute a single Naukri search with bounded retries and exponential backoff
        explicitly separating HTTP 406, 429, and 403 errors.
        """
        attempt = 0
        backoff = INITIAL_BACKOFF_SECONDS
        retry_occurred = False

        while attempt <= self.max_retries:
            try:
                logger.info(
                    f"Searching Naukri for role='{role}' location='{location}' is_remote={is_remote} (attempt {attempt+1}/{self.max_retries+1})"
                )

                df = scrape_jobs(
                    site_name=["naukri"],
                    search_term=role,
                    location=location,
                    is_remote=is_remote,
                    results_wanted=self.results_per_search,
                    hours_old=self.hours_old,
                    fetch_description=FETCH_DESCRIPTIONS,
                )

                if df is not None and not df.empty:
                    records = df.to_dict(orient="records")
                    # Annotate search provenance
                    for rec in records:
                        rec["test_keyword"] = role
                        rec["test_location"] = location
                        rec["test_is_remote"] = is_remote
                        rec["run_id"] = self.run_id

                    if retry_occurred:
                        # Log successful recovery after retry
                        self.run_errors.append({
                            "status_code": 406 if attempt > 0 else 200,
                            "category": ErrorCategory.HTTP_406_RECAPTCHA_CHALLENGE.value if attempt > 0 else "success",
                            "role": role,
                            "location": location,
                            "is_remote": is_remote,
                            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                            "retry_occurred": True,
                            "retry_count": attempt,
                            "final_outcome": "succeeded_on_retry",
                            "error_message": "Search recovered after exponential backoff.",
                        })
                    return records
                return []

            except Exception as e:
                err_msg = str(e)
                status_code, category = parse_error_details(err_msg)
                logger.warning(
                    f"Search failed: [HTTP {status_code}] {err_msg} (Category: {category.value})"
                )

                # HTTP 406: Akamai reCAPTCHA rate challenge (Retriable with bounded backoff)
                if category == ErrorCategory.HTTP_406_RECAPTCHA_CHALLENGE:
                    attempt += 1
                    retry_occurred = True
                    if attempt <= self.max_retries:
                        sleep_time = min(backoff + random.uniform(1.0, 3.0), MAX_BACKOFF_SECONDS)
                        logger.info(f"HTTP 406 challenge. Backing off {sleep_time:.2f}s before retry {attempt}...")
                        time.sleep(sleep_time)
                        backoff *= 2.0
                        continue
                    else:
                        logger.error(f"HTTP 406 retries exhausted for {role} in {location}.")
                        self.run_errors.append({
                            "status_code": 406,
                            "category": category.value,
                            "role": role,
                            "location": location,
                            "is_remote": is_remote,
                            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                            "retry_occurred": True,
                            "retry_count": attempt,
                            "final_outcome": "exhausted_retries",
                            "error_message": err_msg,
                        })
                        return []

                # HTTP 429: Too Many Requests (Retriable with longer backoff)
                elif category == ErrorCategory.HTTP_429_RATE_LIMITED:
                    attempt += 1
                    retry_occurred = True
                    if attempt <= self.max_retries:
                        sleep_time = min(backoff * 1.5 + random.uniform(2.0, 4.0), MAX_BACKOFF_SECONDS)
                        logger.info(f"HTTP 429 rate limit. Backing off {sleep_time:.2f}s before retry {attempt}...")
                        time.sleep(sleep_time)
                        backoff *= 2.0
                        continue
                    else:
                        logger.error(f"HTTP 429 retries exhausted for {role} in {location}.")
                        self.run_errors.append({
                            "status_code": 429,
                            "category": category.value,
                            "role": role,
                            "location": location,
                            "is_remote": is_remote,
                            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                            "retry_occurred": True,
                            "retry_count": attempt,
                            "final_outcome": "exhausted_retries",
                            "error_message": err_msg,
                        })
                        return []

                # HTTP 403: Access Blocked / Forbidden (Non-retriable: Do NOT hammer blocked endpoints)
                elif category == ErrorCategory.HTTP_403_ACCESS_BLOCKED:
                    logger.error(f"HTTP 403 Access Blocked for {role} in {location}. Aborting retries immediately.")
                    self.run_errors.append({
                        "status_code": 403,
                        "category": category.value,
                        "role": role,
                        "location": location,
                        "is_remote": is_remote,
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "retry_occurred": False,
                        "retry_count": 0,
                        "final_outcome": "aborted_non_retriable",
                        "error_message": err_msg,
                    })
                    return []

                # Other errors (5xx, network, etc.)
                else:
                    self.run_errors.append({
                        "status_code": status_code,
                        "category": category.value,
                        "role": role,
                        "location": location,
                        "is_remote": is_remote,
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "retry_occurred": retry_occurred,
                        "retry_count": attempt,
                        "final_outcome": "aborted_non_retriable",
                        "error_message": err_msg,
                    })
                    return []

        return []

    def run(
        self,
        plan: list[dict] | None = None,
        max_searches: int | None = None,
    ) -> list[JobRecord]:
        """
        Execute collection across the specified search plan.
        """
        search_plan = plan or build_search_plan()
        if max_searches:
            search_plan = search_plan[:max_searches]

        logger.info(f"Starting Naukri collection: {len(search_plan)} planned searches (run_id={self.run_id})")
        self.load_checkpoint()

        total = len(search_plan)
        searches_completed_count = 0
        searches_failed_count = 0
        searches_skipped_count = 0

        for idx, query in enumerate(search_plan):
            role = query["role"]
            loc = query["location"]
            is_remote = query["is_remote"]
            search_key = (role, loc, is_remote)

            if search_key in self.completed_searches:
                logger.info(f"[{idx+1}/{total}] Skipping already completed search: {role} in {loc}")
                searches_skipped_count += 1
                continue

            if not self.circuit_breaker.can_proceed():
                logger.warning(
                    f"Naukri circuit breaker is ACTIVE after consecutive failures. Stopping collection gracefully."
                )
                self.circuit_breaker_triggered = True
                break

            # Respectful delay between queries
            if idx > 0:
                delay = random.uniform(*self.delays)
                time.sleep(delay)

            t0 = time.time()
            records = self.execute_search_with_backoff(role, loc, is_remote)
            duration = round(time.time() - t0, 2)

            recent_err = [
                e for e in self.run_errors
                if e.get("role") == role and e.get("location") == loc and e.get("status_code") in (406, 429, 403)
            ]

            if records or not recent_err:
                self.completed_searches.add(search_key)
                self.raw_records.extend(records)
                self.current_run_raw_count += len(records)
                self.append_checkpoint(records)
                self.circuit_breaker.record_success()
                searches_completed_count += 1
                search_status = "SUCCESS" if records else "EMPTY_SUCCESS"
            else:
                searches_failed_count += 1
                cat = recent_err[0].get("category", "unknown")
                code = recent_err[0].get("status_code")
                self.circuit_breaker.record_failure(cat, status_code=code)
                search_status = "RECAPTCHA_CHALLENGE" if code == 406 else "FAILED"
                logger.warning(f"Search for '{role}' in '{loc}' failed ({search_status}); NOT marked completed.")

            self.search_stats.append({
                "role": role,
                "location": loc,
                "is_remote": is_remote,
                "records_returned": len(records),
                "duration_seconds": duration,
                "status": search_status,
            })

            if self.circuit_breaker.is_open:
                logger.warning("Circuit breaker tripped: 3 consecutive challenges. Stopping collection gracefully.")
                self.circuit_breaker_triggered = True
                break

        logger.info("Raw collection complete. Processing and deduplicating...")
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
            rec = parse_naukri_record(raw, self.run_id)
            if rec:
                canonical_records.append(rec)

        # Deduplicate
        unique_records, dedup_audit = deduplicate_naukri_records(canonical_records)

        # Write Jobs JSON
        with open(self.jobs_json_path, "w", encoding="utf-8") as f:
            json.dump([r.model_dump(mode="json") for r in unique_records], f, indent=2, default=str)

        # Write Jobs CSV
        if unique_records:
            dict_rows = [r.model_dump(mode="json") for r in unique_records]
            # Flatten quality and metadata for CSV
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
            "collector": "NaukriStandaloneCollector",
            "searches_planned": searches_planned or len(self.completed_searches),
            "searches_completed": searches_completed or len(self.completed_searches),
            "searches_failed": searches_failed,
            "searches_skipped": searches_skipped,
            "searches_resumed": len(self.completed_searches) if self.resumed_from_checkpoint_count else 0,
            "raw_records_collected": self.current_run_raw_count,
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
            f"Phase 2 processing finished: {len(self.raw_records)} raw -> {len(unique_records)} unique jobs."
        )
        return unique_records

    def generate_quality_report(
        self,
        unique_records: list[JobRecord],
        dedup_audit: Any,
    ) -> dict[str, Any]:
        """
        Calculate detailed data quality indicators across unique records.
        Distinguishes explicit description types and separates HTTP 406/429/403 errors.
        """
        total = len(unique_records)
        if total == 0:
            return {"run_id": self.run_id, "collector": "NaukriStandaloneCollector", "unique_records": 0}

        def _count(fn) -> int:
            return sum(1 for r in unique_records if fn(r))

        work_mode_counts: dict[str, int] = {}
        for r in unique_records:
            val = r.work_mode.value if hasattr(r.work_mode, "value") else str(r.work_mode)
            work_mode_counts[val] = work_mode_counts.get(val, 0) + 1

        remote_elig_counts: dict[str, int] = {}
        for r in unique_records:
            val = r.remote_eligibility if r.remote_eligibility else "not_applicable"
            remote_elig_counts[val] = remote_elig_counts.get(val, 0) + 1

        # Description type breakdown
        desc_types = [
            r.source_metadata.get("description_type", DescriptionType.MISSING.value)
            for r in unique_records
        ]
        type_counts = Counter(desc_types)
        non_empty_desc = _count(lambda r: bool(r.description and r.description.strip()))

        # Error classification counts
        error_406_count = sum(1 for e in self.run_errors if e.get("status_code") == 406)
        error_429_count = sum(1 for e in self.run_errors if e.get("status_code") == 429)
        error_403_count = sum(1 for e in self.run_errors if e.get("status_code") == 403)
        recovered_via_retry = sum(1 for e in self.run_errors if e.get("final_outcome") == "succeeded_on_retry")
        exhausted_retries = sum(1 for e in self.run_errors if e.get("final_outcome") == "exhausted_retries")

        return {
            "run_id": self.run_id,
            "collector": "NaukriStandaloneCollector",
            "unique_records": total,
            "error_statistics": {
                "total_errors_logged": len(self.run_errors),
                "http_406_recaptcha_challenge_count": error_406_count,
                "http_429_rate_limited_count": error_429_count,
                "http_403_access_blocked_count": error_403_count,
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
                "salary_min": {"populated": _count(lambda r: r.source_metadata.get("salary_min") is not None), "coverage_pct": round(_count(lambda r: r.source_metadata.get("salary_min") is not None) / total * 100, 2)},
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
