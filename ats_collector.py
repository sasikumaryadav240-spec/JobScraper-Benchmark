"""
ATS Standalone Collector.

Orchestrates official public job board collections across Greenhouse, Lever,
and Ashby platforms with fault-isolated adapters, checkpointing, and reporting.
"""

from __future__ import annotations

import csv
import json
import logging
import random
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ats_adapters import (
    AshbyAdapter,
    BaseATSAdapter,
    GreenhouseAdapter,
    LeverAdapter,
)
from ats_config import (
    ASHBY_JOBS_JSON,
    ATS_CHECKPOINT,
    ATS_ERRORS_JSON,
    ATS_JOBS_CSV,
    ATS_JOBS_JSON,
    ATS_QUALITY_REPORT,
    ATS_RUN_SUMMARY,
    DELAY_BETWEEN_BOARDS_SECONDS,
    GREENHOUSE_JOBS_JSON,
    INITIAL_BACKOFF_SECONDS,
    LEVER_JOBS_JSON,
    MAX_BACKOFF_SECONDS,
    MAX_RETRIES,
)
from ats_dedup import deduplicate_ats_records
from ats_registry import EmployerBoard, get_verified_registry
from linkedin_parser import DescriptionType
from models import JobRecord

logger = logging.getLogger("ATSCollector")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class ATSCollector:
    def __init__(
        self,
        checkpoint_path: Path = ATS_CHECKPOINT,
        jobs_json_path: Path = ATS_JOBS_JSON,
        jobs_csv_path: Path = ATS_JOBS_CSV,
        greenhouse_json_path: Path = GREENHOUSE_JOBS_JSON,
        lever_json_path: Path = LEVER_JOBS_JSON,
        ashby_json_path: Path = ASHBY_JOBS_JSON,
        errors_path: Path = ATS_ERRORS_JSON,
        quality_path: Path = ATS_QUALITY_REPORT,
        summary_path: Path = ATS_RUN_SUMMARY,
    ) -> None:
        self.run_id = f"ats_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self.checkpoint_path = checkpoint_path
        self.jobs_json_path = jobs_json_path
        self.jobs_csv_path = jobs_csv_path
        self.greenhouse_json_path = greenhouse_json_path
        self.lever_json_path = lever_json_path
        self.ashby_json_path = ashby_json_path
        self.errors_path = errors_path
        self.quality_path = quality_path
        self.summary_path = summary_path

        self.adapters: dict[str, BaseATSAdapter] = {
            "greenhouse": GreenhouseAdapter(),
            "lever": LeverAdapter(),
            "ashby": AshbyAdapter(),
        }

        self.completed_boards: set[tuple[str, str]] = set()  # (platform, board_id)
        self.raw_records: list[dict[str, Any]] = []
        self.run_errors: list[dict[str, Any]] = []
        self.board_stats: list[dict[str, Any]] = []

    def load_checkpoint(self) -> int:
        """Load completed boards and raw records from checkpoint."""
        if not self.checkpoint_path.exists():
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
                        plat = record.get("ats_platform")
                        bid = record.get("board_identifier")
                        if plat and bid:
                            self.completed_boards.add((plat, bid))
                        self.raw_records.append(record)
                        count += 1
                    except json.JSONDecodeError:
                        continue
            logger.info(f"Loaded {count} records across {len(self.completed_boards)} boards from checkpoint")
        except Exception as e:
            logger.warning(f"Error reading checkpoint: {e}")
        return count

    def append_checkpoint(self, raw_items: list[dict[str, Any]]) -> None:
        """Atomically append new records to checkpoint file."""
        if not raw_items:
            return
        with open(self.checkpoint_path, "a", encoding="utf-8") as f:
            for item in raw_items:
                f.write(json.dumps(item, default=str) + "\n")

    def fetch_board_with_backoff(self, board: EmployerBoard) -> list[dict[str, Any]]:
        """Fetch raw listings from an employer board with bounded backoff."""
        adapter = self.adapters.get(board.ats_platform)
        if not adapter:
            raise ValueError(f"Unsupported ATS platform: {board.ats_platform}")

        backoff = INITIAL_BACKOFF_SECONDS
        raw_items: list[dict[str, Any]] = []

        for attempt in range(MAX_RETRIES + 1):
            try:
                raw_items = adapter.fetch_raw_board(board)
                logger.info(f"Successfully fetched {board.company_name} ({board.ats_platform}:{board.board_identifier}) -> {len(raw_items)} listings")
                break
            except Exception as e:
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                err_msg = str(e)
                final_outcome = "exhausted_retries" if attempt == MAX_RETRIES else "retrying"

                err_entry = {
                    "run_id": self.run_id,
                    "platform": board.ats_platform,
                    "company_name": board.company_name,
                    "board_identifier": board.board_identifier,
                    "status_code": status_code,
                    "error_message": err_msg,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "retry_count": attempt,
                    "final_outcome": final_outcome,
                }
                self.run_errors.append(err_entry)
                logger.warning(f"Error fetching {board.company_name} ({board.ats_platform}): {err_msg} (attempt {attempt+1}/{MAX_RETRIES+1})")

                if status_code in (404, 403):
                    # 404 Not Found or 403 Forbidden: Do not retry
                    err_entry["final_outcome"] = "fatal_non_retryable"
                    break

                if attempt < MAX_RETRIES:
                    sleep_time = min(backoff + random.uniform(1.0, 3.0), MAX_BACKOFF_SECONDS)
                    time.sleep(sleep_time)
                    backoff *= 2.0
                else:
                    break

        return raw_items

    def run(self, boards: list[EmployerBoard] | None = None) -> list[JobRecord]:
        """
        Execute collection across verified employer boards.
        """
        target_boards = boards or get_verified_registry(active_only=True)
        self.load_checkpoint()
        total = len(target_boards)
        logger.info(f"Starting ATS collection across {total} verified employer boards...")

        for idx, board in enumerate(target_boards, 1):
            board_key = (board.ats_platform, board.board_identifier)
            if board_key in self.completed_boards:
                logger.info(f"[{idx}/{total}] Skipping already completed board: {board.company_name} ({board.ats_platform})")
                continue

            logger.info(f"[{idx}/{total}] Processing board: {board.company_name} ({board.ats_platform}:{board.board_identifier})...")

            if idx > 1:
                delay = random.uniform(*DELAY_BETWEEN_BOARDS_SECONDS)
                time.sleep(delay)

            t0 = time.time()
            raw_listings = self.fetch_board_with_backoff(board)
            duration = round(time.time() - t0, 2)

            # Store board identifier in raw records for checkpointing
            board_records_for_chk = []
            for item in raw_listings:
                item_copy = dict(item) if isinstance(item, dict) else {"raw": item}
                item_copy["ats_platform"] = board.ats_platform
                item_copy["board_identifier"] = board.board_identifier
                item_copy["company_name"] = board.company_name
                board_records_for_chk.append(item_copy)

            self.completed_boards.add(board_key)
            self.raw_records.extend(board_records_for_chk)
            self.append_checkpoint(board_records_for_chk)

            self.board_stats.append({
                "company_name": board.company_name,
                "platform": board.ats_platform,
                "board_identifier": board.board_identifier,
                "total_listings_fetched": len(raw_listings),
                "duration_seconds": duration,
                "status": "success" if raw_listings else "empty_or_failed",
            })

        logger.info("Raw ATS collection phase finished. Parsing and deduplicating cybersecurity roles...")
        return self.process_and_save()

    def process_and_save(self) -> list[JobRecord]:
        """
        Transform raw listings into canonical cybersecurity JobRecords, deduplicate, and export.
        """
        canonical_jobs: list[JobRecord] = []
        registry_map = {
            (b.ats_platform, b.board_identifier): b
            for b in get_verified_registry(active_only=False)
        }

        # Filter & parse
        for raw in self.raw_records:
            plat = raw.get("ats_platform")
            bid = raw.get("board_identifier")
            adapter = self.adapters.get(plat)
            board = registry_map.get((plat, bid))
            if not adapter or not board:
                # Create a minimal fallback board if missing
                board = EmployerBoard(
                    company_name=raw.get("company_name", "Unknown"),
                    ats_platform=plat or "unknown",
                    board_identifier=bid or "unknown",
                    board_url="",
                    country_or_market="India",
                    verification_timestamp="",
                    discovery_source="checkpoint",
                )

            rec = adapter.parse_record(raw, board, self.run_id)
            if rec:
                canonical_jobs.append(rec)

        # Deduplicate
        unique_jobs, dedup_audit = deduplicate_ats_records(canonical_jobs)

        # Write unified ATS Jobs JSON
        with open(self.jobs_json_path, "w", encoding="utf-8") as f:
            json.dump([r.model_dump(mode="json") for r in unique_jobs], f, indent=2, default=str)

        # Write per-platform JSON files
        gh_jobs = [r for r in unique_jobs if r.source == "greenhouse"]
        lever_jobs = [r for r in unique_jobs if r.source == "lever"]
        ashby_jobs = [r for r in unique_jobs if r.source == "ashby"]

        with open(self.greenhouse_json_path, "w", encoding="utf-8") as f:
            json.dump([r.model_dump(mode="json") for r in gh_jobs], f, indent=2, default=str)

        with open(self.lever_json_path, "w", encoding="utf-8") as f:
            json.dump([r.model_dump(mode="json") for r in lever_jobs], f, indent=2, default=str)

        with open(self.ashby_json_path, "w", encoding="utf-8") as f:
            json.dump([r.model_dump(mode="json") for r in ashby_jobs], f, indent=2, default=str)

        # Write unified ATS Jobs CSV
        if unique_jobs:
            dict_rows = [r.model_dump(mode="json") for r in unique_jobs]
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
        quality_report = self.generate_quality_report(unique_jobs, dedup_audit, gh_jobs, lever_jobs, ashby_jobs)
        with open(self.quality_path, "w", encoding="utf-8") as f:
            json.dump(quality_report, f, indent=2)

        # Generate Run Summary
        run_summary = {
            "run_id": self.run_id,
            "collector": "ATSStandaloneCollector",
            "boards_configured": len(get_verified_registry(active_only=True)),
            "boards_completed": len(self.completed_boards),
            "raw_listings_fetched_total": len(self.raw_records),
            "cybersecurity_jobs_filtered": len(canonical_jobs),
            "unique_jobs_preserved": len(unique_jobs),
            "platform_breakdown": {
                "greenhouse": len(gh_jobs),
                "lever": len(lever_jobs),
                "ashby": len(ashby_jobs),
            },
            "deduplication": dedup_audit.to_dict(),
            "issues_count": len(self.run_errors),
            "board_stats": self.board_stats,
            "output_files": {
                "ats_jobs_json": str(self.jobs_json_path),
                "ats_jobs_csv": str(self.jobs_csv_path),
                "greenhouse_jobs_json": str(self.greenhouse_json_path),
                "lever_jobs_json": str(self.lever_json_path),
                "ashby_jobs_json": str(self.ashby_json_path),
                "checkpoint": str(self.checkpoint_path),
                "errors_json": str(self.errors_path),
                "quality_report": str(self.quality_path),
            },
        }
        with open(self.summary_path, "w", encoding="utf-8") as f:
            json.dump(run_summary, f, indent=2)

        logger.info(
            f"Phase 4 processing finished: {len(self.raw_records)} raw listings -> {len(canonical_jobs)} cybersecurity jobs -> {len(unique_jobs)} unique jobs."
        )
        return unique_jobs

    def generate_quality_report(
        self,
        unique_jobs: list[JobRecord],
        dedup_audit: Any,
        gh_jobs: list[JobRecord],
        lever_jobs: list[JobRecord],
        ashby_jobs: list[JobRecord],
    ) -> dict[str, Any]:
        """
        Calculate comprehensive quality indicators across unified and per-platform datasets.
        """
        total = len(unique_jobs)

        def _count(records: list[JobRecord], fn: Any) -> int:
            return sum(1 for r in records if fn(r))

        def _metrics_for(subset: list[JobRecord]) -> dict[str, Any]:
            sub_total = len(subset)
            if sub_total == 0:
                return {"count": 0}

            desc_types = [
                r.source_metadata.get("description_type", DescriptionType.MISSING.value)
                for r in subset
            ]
            type_counts = Counter(desc_types)
            non_empty_desc = _count(subset, lambda r: bool(r.description and r.description.strip()))

            work_mode_counts: dict[str, int] = {}
            for r in subset:
                val = r.work_mode.value if hasattr(r.work_mode, "value") else str(r.work_mode)
                work_mode_counts[val] = work_mode_counts.get(val, 0) + 1

            return {
                "unique_records": sub_total,
                "field_coverage": {
                    "title": {"populated": _count(subset, lambda r: bool(r.title)), "coverage_pct": round(_count(subset, lambda r: bool(r.title)) / sub_total * 100, 2)},
                    "company": {"populated": _count(subset, lambda r: bool(r.company)), "coverage_pct": round(_count(subset, lambda r: bool(r.company)) / sub_total * 100, 2)},
                    "location": {"populated": _count(subset, lambda r: bool(r.location)), "coverage_pct": round(_count(subset, lambda r: bool(r.location)) / sub_total * 100, 2)},
                    "date_posted": {"populated": _count(subset, lambda r: r.date_posted is not None), "coverage_pct": round(_count(subset, lambda r: r.date_posted is not None) / sub_total * 100, 2)},
                    "job_url": {"populated": _count(subset, lambda r: bool(r.job_url)), "coverage_pct": round(_count(subset, lambda r: bool(r.job_url)) / sub_total * 100, 2)},
                    "application_url": {"populated": _count(subset, lambda r: bool(r.application_url)), "coverage_pct": round(_count(subset, lambda r: bool(r.application_url)) / sub_total * 100, 2)},
                    "min_experience": {"populated": _count(subset, lambda r: r.min_experience is not None), "coverage_pct": round(_count(subset, lambda r: r.min_experience is not None) / sub_total * 100, 2)},
                    "max_experience": {"populated": _count(subset, lambda r: r.max_experience is not None), "coverage_pct": round(_count(subset, lambda r: r.max_experience is not None) / sub_total * 100, 2)},
                    "work_mode": {"populated": _count(subset, lambda r: r.work_mode.value != "unknown"), "coverage_pct": round(_count(subset, lambda r: r.work_mode.value != "unknown") / sub_total * 100, 2)},
                    "remote_eligibility": {"populated": _count(subset, lambda r: r.remote_eligibility is not None), "coverage_pct": round(_count(subset, lambda r: r.remote_eligibility is not None) / sub_total * 100, 2)},
                },
                "description_quality": {
                    "non_empty_description_coverage": {
                        "count": non_empty_desc,
                        "coverage_pct": round(non_empty_desc / sub_total * 100, 2),
                    },
                    "full_description_coverage": {
                        "count": type_counts.get(DescriptionType.FULL_DESCRIPTION.value, 0),
                        "coverage_pct": round(type_counts.get(DescriptionType.FULL_DESCRIPTION.value, 0) / sub_total * 100, 2),
                    },
                    "search_result_snippet_coverage": {
                        "count": type_counts.get(DescriptionType.SEARCH_RESULT_SNIPPET.value, 0),
                        "coverage_pct": round(type_counts.get(DescriptionType.SEARCH_RESULT_SNIPPET.value, 0) / sub_total * 100, 2),
                    },
                    "missing_description_coverage": {
                        "count": type_counts.get(DescriptionType.MISSING.value, 0),
                        "coverage_pct": round(type_counts.get(DescriptionType.MISSING.value, 0) / sub_total * 100, 2),
                    },
                },
                "work_mode_distribution": work_mode_counts,
            }

        return {
            "run_id": self.run_id,
            "collector": "ATSStandaloneCollector",
            "unique_records_total": total,
            "platform_breakdown_counts": {
                "greenhouse": len(gh_jobs),
                "lever": len(lever_jobs),
                "ashby": len(ashby_jobs),
            },
            "unified_metrics": _metrics_for(unique_jobs),
            "greenhouse_metrics": _metrics_for(gh_jobs),
            "lever_metrics": _metrics_for(lever_jobs),
            "ashby_metrics": _metrics_for(ashby_jobs),
            "deduplication": dedup_audit.to_dict(),
            "error_statistics": {
                "total_errors_logged": len(self.run_errors),
                "fatal_errors": sum(1 for e in self.run_errors if e.get("final_outcome") == "fatal_non_retryable"),
                "exhausted_retries": sum(1 for e in self.run_errors if e.get("final_outcome") == "exhausted_retries"),
            },
        }

