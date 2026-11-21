"""
Master Orchestrator for Daily Job Collection, Deduplication, and Matching Pipeline.

Coordinates:
1. Indeed collection (fault-isolated, extracting unique and raw records accurately)
2. Naukri collection (fault-isolated, invoking supported collector.run(plan=plan) signature)
3. LinkedIn collection (fault-isolated, invoking supported collector.run(plan=plan) signature)
4. ATS collection: Greenhouse, Lever, Ashby (fault-isolated)
5. Unified Normalization & Ingestion with source-level provenance tracking
6. Cross-Source Deduplication & Pre-match Master Dataset Export
7. Candidate Skill Matching & Eligibility Engine (Authoritative Version 2.0, 15 Role Families)
8. Multi-format Output Generation (JSON, CSV, Excel)
9. Daily Run Telemetry, Quality Audits & Markdown Reporting distinguishing complete vs partial runs
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ats_collector import ATSCollector
from indeed_collector import IndeedCollector
from linkedin_collector import LinkedInCollector
from naukri_collector import NaukriCollector
from request_pacer import get_global_pacer, install_request_pacer
from unified_pipeline import UnifiedPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("DailyPipeline")


class DailyPipelineOrchestrator:
    def __init__(self, base_dir: str | Path = "jobspy", run_mode: str = "daily"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.run_mode = run_mode  # 'daily' | 'full' | 'skip_scraping'
        self.run_id = f"daily_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self.start_time = datetime.now(timezone.utc)
        self.source_telemetry: dict[str, dict[str, Any]] = {}
        self.run_errors: list[dict[str, Any]] = []
        self.pacer = install_request_pacer()

    def _check_source_freshness(self, file_path: Path, collected_in_run: bool) -> tuple[str, str]:
        """Return (freshness_status, timestamp_iso)."""
        if not file_path.exists():
            return "UNAVAILABLE", "none"
        mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
        mtime_iso = mtime.isoformat()
        if collected_in_run:
            return "FRESH", mtime_iso
        age_hours = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600.0
        if age_hours <= 24.0:
            return "CACHED_RECENT", mtime_iso
        return "STALE", mtime_iso

    def run_indeed(self) -> None:
        """Execute Indeed collector with fault isolation and accurate metric extraction."""
        logger.info("=== [1/4] INDEED COLLECTION STAGE ===")
        t0 = time.time()
        source_name = "indeed"
        collected_in_run = False
        status = "FAILED"
        raw_count = 0
        unique_count = 0
        duplicates_removed = 0
        err_msg = None
        planned = 0
        completed = 0
        failed = 0
        skipped = 0
        cnt_429 = 0
        cnt_5xx = 0
        cb_stats: dict[str, Any] = {}

        try:
            collector = IndeedCollector(checkpoint_path=self.base_dir / "indeed_checkpoint.jsonl")
            import indeed_config as cfg
            if self.run_mode == "full":
                searches = cfg.SEARCHES
            else:
                rep_roles = ["SOC Analyst", "Cybersecurity Analyst", "SIEM Analyst", "Cloud Security Analyst"]
                rep_locations = ["Bengaluru", "Hyderabad"]
                searches = cfg.generate_search_plan(roles=rep_roles, locations=rep_locations, include_remote=True)

            res = collector.run(searches=searches, clean=False)
            raw_count = res.get("raw_records_returned", 0)
            unique_count = res.get("unique_records_after_dedup", 0)
            duplicates_removed = res.get("duplicates_removed", 0)
            issues_recorded = res.get("issues_recorded", 0)
            planned = res.get("searches_planned", len(searches))
            completed = res.get("searches_completed", 0)
            failed = res.get("searches_failed", 0)
            skipped = res.get("searches_skipped", 0)
            cb_stats = res.get("circuit_breaker_stats", {})

            collected_in_run = True
            if failed > 0 or cb_stats.get("is_open"):
                if unique_count > 0:
                    status = "PARTIAL"
                    err_msg = f"{completed}/{planned} searches completed, {failed} failed/rate-limited"
                else:
                    status = "FAILED"
                    err_msg = f"All {planned} searches failed or blocked"
            elif unique_count > 0:
                status = "PARTIAL" if issues_recorded > 0 else "SUCCESS"
            else:
                status = "FAILED" if issues_recorded > 0 else "EMPTY_SUCCESS"

            logger.info(
                f"Indeed collection completed: {unique_count} unique records "
                f"(raw={raw_count}, duplicates_removed={duplicates_removed}, status={status})."
            )
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Indeed stage encountered an error: {e}", exc_info=True)
            self.run_errors.append({"source": source_name, "error": err_msg, "timestamp": datetime.now(timezone.utc).isoformat()})
            status = "FAILED"

        duration = round(time.time() - t0, 2)
        freshness, timestamp = self._check_source_freshness(self.base_dir / "indeed_jobs.json", collected_in_run)
        self.source_telemetry[source_name] = {
            "status": status,
            "freshness": freshness,
            "duration_seconds": duration,
            "records_count": unique_count,
            "raw_records_count": raw_count,
            "duplicates_removed": duplicates_removed,
            "planned": planned,
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "http_429_count": cb_stats.get("http_429_count", 0),
            "http_5xx_count": cb_stats.get("http_5xx_count", 0),
            "http_403_406_count": cb_stats.get("http_403_406_count", 0),
            "circuit_breaker_events": cb_stats.get("circuit_breaker_events", 0),
            "circuit_breaker_open": cb_stats.get("is_open", False),
            "last_collection_time": timestamp,
            "error": err_msg,
        }

    def run_naukri(self) -> None:
        """Execute Naukri collector with fault isolation using its supported run(plan=plan) signature."""
        logger.info("=== [2/4] NAUKRI COLLECTION STAGE ===")
        t0 = time.time()
        source_name = "naukri"
        collected_in_run = False
        status = "FAILED"
        rec_count = 0
        raw_count = 0
        err_msg = None
        planned = 0
        completed = 0
        failed = 0
        skipped = 0
        cnt_429 = 0
        cnt_5xx = 0
        cb_stats: dict[str, Any] = {}

        try:
            collector = NaukriCollector(checkpoint_path=self.base_dir / "naukri_checkpoint.jsonl")
            import naukri_config as cfg
            if self.run_mode == "full":
                plan = cfg.build_search_plan()
            else:
                rep_roles = ["SOC Analyst", "Cybersecurity Analyst", "SIEM Analyst"]
                rep_locations = ["Bengaluru", "Hyderabad"]
                plan = cfg.build_search_plan(roles=rep_roles, cities=rep_locations, include_remote=True)

            res = collector.run(plan=plan)
            rec_count = len(res)
            raw_count = len(getattr(collector, "raw_records", []))
            collected_in_run = True

            summary_file = self.base_dir / "naukri_run_summary.json"
            sdata: dict[str, Any] = {}
            if summary_file.exists():
                try:
                    with open(summary_file, "r", encoding="utf-8") as f:
                        sdata = json.load(f)
                except Exception:
                    pass

            planned = sdata.get("searches_planned", len(plan))
            completed = sdata.get("searches_completed", len(getattr(collector, "completed_searches", [])))
            failed = sdata.get("searches_failed", 0)
            skipped = sdata.get("searches_skipped", 0)
            cb_stats = sdata.get("circuit_breaker_stats", {})

            col_errors = getattr(collector, "run_errors", [])
            has_errors = bool(col_errors)
            has_406 = any(e.get("status_code") == 406 or "406" in str(e) for e in col_errors)

            if failed > 0 or cb_stats.get("is_open"):
                if rec_count > 0:
                    status = "PARTIAL"
                    err_msg = f"{completed}/{planned} searches completed, {failed} failed (reCAPTCHA: {cb_stats.get('http_403_406_count', 0)})"
                else:
                    status = "FAILED"
                    err_msg = "HTTP 406 reCAPTCHA challenge triggered / circuit breaker tripped"
            elif rec_count > 0:
                status = "PARTIAL" if has_errors else "SUCCESS"
            else:
                if has_406:
                    status = "FAILED"
                    err_msg = "HTTP 406 reCAPTCHA challenge triggered / circuit breaker tripped"
                elif has_errors:
                    status = "FAILED"
                    err_msg = str(col_errors[-1].get("error_message") or "Collection errors occurred") if col_errors else "Collection errors occurred"
                else:
                    status = "EMPTY_SUCCESS"

            logger.info(f"Naukri collection completed: {rec_count} unique records (raw={raw_count}, status={status}).")
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Naukri stage encountered an error: {e}", exc_info=True)
            self.run_errors.append({"source": source_name, "error": err_msg, "timestamp": datetime.now(timezone.utc).isoformat()})
            status = "FAILED"

        duration = round(time.time() - t0, 2)
        freshness, timestamp = self._check_source_freshness(self.base_dir / "naukri_jobs.json", collected_in_run)
        self.source_telemetry[source_name] = {
            "status": status,
            "freshness": freshness,
            "duration_seconds": duration,
            "records_count": rec_count,
            "raw_records_count": raw_count,
            "planned": planned,
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "http_429_count": cb_stats.get("http_429_count", 0),
            "http_5xx_count": cb_stats.get("http_5xx_count", 0),
            "http_403_406_count": cb_stats.get("http_403_406_count", 0),
            "circuit_breaker_events": cb_stats.get("circuit_breaker_events", 0),
            "circuit_breaker_open": cb_stats.get("is_open", False),
            "last_collection_time": timestamp,
            "error": err_msg,
        }

    def run_linkedin(self) -> None:
        """Execute LinkedIn collector with fault isolation using its supported run(plan=plan) signature."""
        logger.info("=== [3/4] LINKEDIN COLLECTION STAGE ===")
        t0 = time.time()
        source_name = "linkedin"
        collected_in_run = False
        status = "FAILED"
        rec_count = 0
        raw_count = 0
        err_msg = None
        planned = 0
        completed = 0
        failed = 0
        skipped = 0
        cnt_429 = 0
        cnt_5xx = 0
        cb_stats: dict[str, Any] = {}

        try:
            collector = LinkedInCollector(checkpoint_path=self.base_dir / "linkedin_checkpoint.jsonl")
            import linkedin_config as cfg
            if self.run_mode == "full":
                plan = cfg.build_search_plan()
            else:
                rep_roles = ["SOC Analyst", "Cybersecurity Analyst", "Cloud Security Analyst"]
                rep_locations = ["Bengaluru", "Hyderabad"]
                plan = cfg.build_search_plan(roles=rep_roles, cities=rep_locations, include_remote=True)

            res = collector.run(plan=plan)
            rec_count = len(res)
            raw_count = len(getattr(collector, "raw_records", []))
            collected_in_run = True

            summary_file = self.base_dir / "linkedin_run_summary.json"
            sdata: dict[str, Any] = {}
            if summary_file.exists():
                try:
                    with open(summary_file, "r", encoding="utf-8") as f:
                        sdata = json.load(f)
                except Exception:
                    pass

            planned = sdata.get("searches_planned", len(plan))
            completed = sdata.get("searches_completed", len(getattr(collector, "completed_searches", [])))
            failed = sdata.get("searches_failed", 0)
            skipped = sdata.get("searches_skipped", 0)
            cb_stats = sdata.get("circuit_breaker_stats", {})

            col_errors = getattr(collector, "run_errors", [])
            has_errors = bool(col_errors)
            has_429 = any(e.get("status_code") == 429 or "429" in str(e) for e in col_errors)
            has_5xx = any(e.get("status_code") and e.get("status_code") >= 500 for e in col_errors)
            cb_open = cb_stats.get("is_open") or getattr(collector, "circuit_breaker_triggered", False)

            cnt_429 = cb_stats.get("http_429_count", sum(1 for e in col_errors if e.get("status_code") == 429))
            cnt_5xx = cb_stats.get("http_5xx_count", sum(1 for e in col_errors if e.get("status_code") and e.get("status_code") >= 500))

            if failed > 0 or cb_open or has_429 or has_5xx:
                if rec_count > 0:
                    status = "PARTIAL"
                    err_msg = f"{completed}/{planned} searches completed, {failed} failed (HTTP 429: {cnt_429}, HTTP 5xx: {cnt_5xx}, circuit breaker: {'TRIPPED' if cb_open else 'ACTIVE'})"
                else:
                    status = "RATE_LIMITED" if has_429 else "FAILED"
                    err_msg = f"LinkedIn collection failed (HTTP 429: {cnt_429}, HTTP 5xx: {cnt_5xx}, circuit breaker tripped)"
            elif rec_count > 0:
                status = "PARTIAL" if has_errors else "SUCCESS"
            else:
                status = "EMPTY_SUCCESS"

            logger.info(f"LinkedIn collection completed: {rec_count} unique records (raw={raw_count}, status={status}).")
        except Exception as e:
            err_msg = str(e)
            logger.error(f"LinkedIn stage encountered an error: {e}", exc_info=True)
            self.run_errors.append({"source": source_name, "error": err_msg, "timestamp": datetime.now(timezone.utc).isoformat()})
            if "429" in str(e):
                status = "RATE_LIMITED"
                cnt_429 = max(cnt_429, 1)
            else:
                status = "FAILED"
            if "500" in str(e) or "502" in str(e) or "503" in str(e):
                cnt_5xx = max(cnt_5xx, 1)

        duration = round(time.time() - t0, 2)
        freshness, timestamp = self._check_source_freshness(self.base_dir / "linkedin_jobs.json", collected_in_run)
        self.source_telemetry[source_name] = {
            "status": status,
            "freshness": freshness,
            "duration_seconds": duration,
            "records_count": rec_count,
            "raw_records_count": raw_count,
            "planned": planned,
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "http_429_count": cnt_429,
            "http_5xx_count": cnt_5xx,
            "http_403_406_count": cb_stats.get("http_403_406_count", 0),
            "circuit_breaker_events": cb_stats.get("circuit_breaker_events", 0),
            "circuit_breaker_open": cb_stats.get("is_open", False),
            "last_collection_time": timestamp,
            "error": err_msg,
        }

    def run_ats(self) -> None:
        """Execute ATS collector across Greenhouse, Lever, and Ashby with fault isolation."""
        logger.info("=== [4/4] ATS COLLECTION STAGE (Greenhouse, Lever, Ashby) ===")
        t0 = time.time()
        source_name = "ats"
        collected_in_run = False
        status = "FAILED"
        rec_count = 0
        raw_count = 0
        err_msg = None
        planned = 0
        completed = 0
        failed = 0
        skipped = 0
        cnt_429 = 0
        cnt_5xx = 0
        cb_stats: dict[str, Any] = {}

        try:
            collector = ATSCollector()
            res = collector.run()
            rec_count = len(res)
            raw_count = len(getattr(collector, "raw_records", []))
            collected_in_run = True

            summary_file = self.base_dir / "ats_run_summary.json"
            sdata: dict[str, Any] = {}
            if summary_file.exists():
                try:
                    with open(summary_file, "r", encoding="utf-8") as f:
                        sdata = json.load(f)
                except Exception:
                    pass

            planned = sdata.get("boards_planned", 15)
            completed = sdata.get("boards_completed", len(getattr(collector, "completed_boards", [])))
            failed = sdata.get("boards_failed", 0)
            skipped = sdata.get("boards_skipped", 0)
            cb_stats = sdata.get("circuit_breaker_stats", {})

            col_errors = getattr(collector, "run_errors", [])
            has_errors = bool(col_errors)
            if failed > 0:
                if rec_count > 0:
                    status = "PARTIAL"
                    err_msg = f"{completed}/{planned} employer boards completed, {failed} failed"
                else:
                    status = "FAILED"
                    err_msg = f"All {planned} employer boards failed"
            elif rec_count > 0:
                status = "PARTIAL" if has_errors else "SUCCESS"
            else:
                status = "FAILED" if has_errors else "EMPTY_SUCCESS"

            logger.info(f"ATS collection completed: {rec_count} unique records (raw={raw_count}, status={status}).")
        except Exception as e:
            err_msg = str(e)
            logger.error(f"ATS stage encountered an error: {e}", exc_info=True)
            self.run_errors.append({"source": source_name, "error": err_msg, "timestamp": datetime.now(timezone.utc).isoformat()})
            status = "FAILED"

        duration = round(time.time() - t0, 2)
        freshness, timestamp = self._check_source_freshness(self.base_dir / "ats_jobs.json", collected_in_run)
        self.source_telemetry[source_name] = {
            "status": status,
            "freshness": freshness,
            "duration_seconds": duration,
            "records_count": rec_count,
            "raw_records_count": raw_count,
            "planned": planned,
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "http_429_count": cb_stats.get("http_429_count", 0),
            "http_5xx_count": cb_stats.get("http_5xx_count", 0),
            "http_403_406_count": cb_stats.get("http_403_406_count", 0),
            "circuit_breaker_events": cb_stats.get("circuit_breaker_events", 0),
            "circuit_breaker_open": cb_stats.get("is_open", False),
            "last_collection_time": timestamp,
            "error": err_msg,
        }

    def run_orchestration(self) -> int:
        """Run the full end-to-end daily pipeline."""
        logger.info(f"Starting Daily Job Collection Pipeline [Run ID: {self.run_id}, Mode: {self.run_mode}]")

        if self.run_mode != "skip_scraping":
            self.run_indeed()
            time.sleep(3.0)
            self.run_naukri()
            time.sleep(3.0)
            self.run_linkedin()
            time.sleep(3.0)
            self.run_ats()
        else:
            logger.info("Scraping skipped by user flag. Processing existing source files directly.")
            for src in ("indeed", "naukri", "linkedin", "ats"):
                f_path = self.base_dir / f"{src}_jobs.json"
                freshness, timestamp = self._check_source_freshness(f_path, collected_in_run=False)
                count = 0
                if f_path.exists():
                    try:
                        with open(f_path, "r", encoding="utf-8") as f:
                            d = json.load(f)
                            count = len(d) if isinstance(d, list) else 1
                    except Exception:
                        pass
                self.source_telemetry[src] = {
                    "status": "SKIPPED",
                    "freshness": freshness,
                    "duration_seconds": 0.0,
                    "records_count": count,
                    "raw_records_count": count,
                    "planned": 0,
                    "completed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "http_429_count": 0,
                    "http_5xx_count": 0,
                    "http_403_406_count": 0,
                    "last_collection_time": timestamp,
                    "error": None,
                }

        # Determine overall source collection outcome
        succeeded_sources = [s for s, t in self.source_telemetry.items() if t["status"] in ("SUCCESS", "EMPTY_SUCCESS")]
        partial_sources = [s for s, t in self.source_telemetry.items() if t["status"] in ("PARTIAL", "RATE_LIMITED")]
        failed_sources = [s for s, t in self.source_telemetry.items() if t["status"] == "FAILED"]
        cached_sources = [s for s, t in self.source_telemetry.items() if t.get("freshness") in ("CACHED_RECENT", "STALE")]

        if failed_sources or partial_sources:
            overall_run_status = "PARTIAL_EXECUTION"
        elif len(succeeded_sources) == len(self.source_telemetry):
            overall_run_status = "COMPLETE_SUCCESS"
        else:
            overall_run_status = "PARTIAL_EXECUTION"

        # Execute Unified Normalization, Deduplication, and Matching Pipeline
        logger.info("=== [UNIFIED PIPELINE STAGE] ===")
        t_pipe = time.time()
        pipeline = UnifiedPipeline(base_dir=self.base_dir)
        pipeline_summary = pipeline.run()
        pipe_duration = round(time.time() - t_pipe, 2)

        end_time = datetime.now(timezone.utc)
        total_duration = round((end_time - self.start_time).total_seconds(), 2)

        # Build Daily Run Summary JSON
        run_summary = {
            "run_id": self.run_id,
            "overall_run_status": overall_run_status,
            "execution_mode": self.run_mode,
            "start_time_utc": self.start_time.isoformat(),
            "end_time_utc": end_time.isoformat(),
            "total_duration_seconds": total_duration,
            "pipeline_stage_duration_seconds": pipe_duration,
            "source_telemetry": self.source_telemetry,
            "source_status_breakdown": {
                "succeeded": succeeded_sources,
                "partial": partial_sources,
                "failed": failed_sources,
                "cached_ingested": cached_sources,
                "complete_collection_achieved": (overall_run_status == "COMPLETE_SUCCESS"),
            },
            "pipeline_summary": pipeline_summary,
            "errors_count": len(self.run_errors),
            "errors": self.run_errors,
            "output_directory": str(self.base_dir.resolve()),
        }

        with open(self.base_dir / "daily_run_summary.json", "w", encoding="utf-8") as f:
            json.dump(run_summary, f, indent=2, default=str)

        with open(self.base_dir / "daily_run_errors.json", "w", encoding="utf-8") as f:
            json.dump(self.run_errors, f, indent=2, default=str)

        # Generate Human-Readable Markdown Report
        markdown_report = self._build_markdown_report(run_summary)
        report_path = self.base_dir / "daily_run_report.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(markdown_report)

        # GitHub Step Summary Support
        github_summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
        if github_summary_file:
            try:
                with open(github_summary_file, "a", encoding="utf-8") as gh_f:
                    gh_f.write("\n" + markdown_report + "\n")
                logger.info("Successfully published daily report to GITHUB_STEP_SUMMARY.")
            except Exception as e:
                logger.warning(f"Could not write to GITHUB_STEP_SUMMARY: {e}")

        logger.info(
            f"Daily pipeline finished [{overall_run_status}] in {total_duration}s. "
            f"Report saved to {report_path}"
        )
        return 0

    def _build_markdown_report(self, summary: dict[str, Any]) -> str:
        """Create formatted markdown summary for humans and CI with prominent completeness attribution."""
        lines = []
        overall_status = summary.get("overall_run_status", "UNKNOWN")
        status_banner = "🟢 COMPLETE SUCCESS" if overall_status == "COMPLETE_SUCCESS" else f"🟡 {overall_status}"

        lines.append(f"# 🛡️ Daily Cybersecurity Job Collection Report")
        lines.append(f"**Run ID**: `{summary['run_id']}` | **Status**: **{status_banner}** | **Duration**: `{summary['total_duration_seconds']}s`\n")

        lines.append("## 1. Source Collection Status")
        lines.append("| Source | Status | Data Freshness | Planned | Completed | Failed | Skipped | Unique Records | Raw Records | 429s | 5xx | 403/406 | Duration | Details |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for src, t in summary["source_telemetry"].items():
            st = t["status"]
            if st == "SUCCESS":
                status_badge = "✅ SUCCESS"
            elif st == "PARTIAL":
                status_badge = "⚠️ PARTIAL"
            elif st == "RATE_LIMITED":
                status_badge = "⏳ RATE_LIMITED"
            elif st == "EMPTY_SUCCESS":
                status_badge = "⚪ EMPTY_SUCCESS"
            elif st == "SKIPPED":
                status_badge = "⏭️ SKIPPED"
            else:
                status_badge = f"❌ {st}"

            fresh = t.get("freshness", t.get("data_freshness", "UNKNOWN"))
            if fresh == "FRESH":
                fresh_badge = "🟢 FRESH"
            elif fresh == "CACHED_RECENT":
                fresh_badge = "🟡 CACHED_RECENT"
            elif fresh == "STALE":
                fresh_badge = "🟠 STALE"
            else:
                fresh_badge = "🔴 UNAVAILABLE"

            raw_c = t.get("raw_records_count", t.get("records_count", 0))
            unique_c = t.get("records_count", 0)
            planned_c = t.get("planned", "-")
            completed_c = t.get("completed", "-")
            failed_c = t.get("failed", 0)
            skipped_c = t.get("skipped", 0)
            c_429 = t.get("http_429_count", 0)
            c_5xx = t.get("http_5xx_count", 0)
            c_403 = t.get("http_403_406_count", 0)
            err_text = t.get("error") or "OK"

            lines.append(
                f"| **{src.upper()}** | {status_badge} | {fresh_badge} | {planned_c} | {completed_c} | {failed_c} | {skipped_c} | **{unique_c}** | {raw_c} | {c_429} | {c_5xx} | {c_403} | {t['duration_seconds']}s | {err_text} |"
            )

        breakdown = summary.get("source_status_breakdown", {})
        lines.append("\n### Source Integrity Breakdown:")
        lines.append(f"- **Complete Collection Achieved**: `{'YES' if breakdown.get('complete_collection_achieved') else 'NO (Partial Run)'}`")
        if breakdown.get("succeeded"):
            lines.append(f"- **✅ Fully Completed Sources**: {', '.join(s.upper() for s in breakdown['succeeded'])}")
        if breakdown.get("partial"):
            partial_with_records = [s.upper() for s in breakdown["partial"] if summary["source_telemetry"].get(s, {}).get("records_count", 0) > 0]
            if partial_with_records:
                lines.append(f"- **⚠️ Partial Sources (Retaining Usable Records)**: {', '.join(partial_with_records)}")
            rl_sources = [s.upper() for s in breakdown["partial"] if summary["source_telemetry"].get(s, {}).get("status") == "RATE_LIMITED" or summary["source_telemetry"].get(s, {}).get("http_429_count", 0) > 0]
            if rl_sources:
                lines.append(f"- **⏳ Rate-Limited Sources**: {', '.join(rl_sources)}")
        if breakdown.get("failed"):
            lines.append(f"- **❌ Failed Sources (No Usable Records)**: {', '.join(s.upper() for s in breakdown['failed'])}")
        if breakdown.get("cached_ingested"):
            lines.append(f"- **🟡 Sources Using Cached/Historical Data**: {', '.join(s.upper() for s in breakdown['cached_ingested'])}")

        ps = summary.get("pipeline_summary", {})
        lines.append("\n## 2. Processing & Candidate Matching Summary")
        lines.append(f"- **Candidate Profile**: Authoritative Version 2.0 (9 November 2026) — 15 Role Families")
        lines.append(f"- **Scoring Model**: 35% Tech Skills | 25% Responsibilities | 15% Experience | 10% Tools | 5% Education & Certs | 10% Transferable")
        lines.append(f"- **Matching Threshold**: Strictly > 60.0%")
        lines.append(f"- **Total Ingested Raw**: {ps.get('total_ingested_raw', 0)}")
        lines.append(f"- **Master Deduplicated Jobs (Pre-Match)**: **{ps.get('deduplicated_master_count', 0)}** (`deduplicated_jobs.json/csv/xlsx`)")
        lines.append(f"- **Total Unique Canonical Jobs**: {ps.get('total_canonical_unique', 0)}")
        lines.append(f"- **Duplicates Merged**: {ps.get('duplicates_merged', 0)}")
        lines.append(f"- **🎯 Matched Opportunities (> 60% Score & PASS)**: **{ps.get('matched_jobs_count', 0)}** (`matched_jobs.json/csv/xlsx`)")
        lines.append(f"- **Supporting IT Roles**: {ps.get('supporting_it_count', 0)} (`supporting_it_jobs.json/csv`)")
        lines.append(f"- **Internships / Trainees**: {ps.get('internships_count', 0)} (`internships.json/csv`)")
        lines.append(f"- **Manual Verification Required**: {ps.get('manual_review_count', 0)} (`manual_verification_jobs.json/csv/xlsx`)")
        lines.append(f"- **Excluded Listings**: {ps.get('excluded_count', 0)} (`excluded_jobs.json`)")

        # Highlight top matched jobs if any
        matched_file = self.base_dir / "matched_jobs.json"
        if matched_file.exists():
            try:
                with open(matched_file, "r", encoding="utf-8") as f:
                    matched = json.load(f)
                if matched:
                    lines.append("\n## 3. 🎯 Top Matched Jobs")
                    lines.append("| Title | Company | Location | Score | Min Exp | Application URL |")
                    lines.append("|---|---|---|---|---|---|")
                    for m in matched[:5]:
                        url = m.get("application_url") or m.get("original_job_url") or "#"
                        exp_str = f"{m.get('mandatory_min_experience')} yrs" if m.get('mandatory_min_experience') is not None else "Entry"
                        lines.append(f"| **{m.get('job_title')}** | {m.get('company_name')} | {m.get('normalized_location')} | **{m.get('overall_match_score')}%** | {exp_str} | [Apply Here]({url}) |")
            except Exception:
                pass

        lines.append(f"\n*Outputs generated in `{summary['output_directory']}`*")
        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Master Daily Job Pipeline Orchestrator")
    parser.add_argument("--daily-run", action="store_true", help="Run representative daily collection and matching")
    parser.add_argument("--full-run", action="store_true", help="Run full search matrix across all sources")
    parser.add_argument("--skip-scraping", action="store_true", help="Skip scraping and run matching on existing source files")
    parser.add_argument("--clean", action="store_true", help="Clean previous output files before starting")
    parser.add_argument("--dir", default="jobspy", help="Base directory for source and output datasets")
    args = parser.parse_args()

    mode = "full" if args.full_run else ("skip_scraping" if args.skip_scraping else "daily")
    orchestrator = DailyPipelineOrchestrator(base_dir=args.dir, run_mode=mode)
    code = orchestrator.run_orchestration()
    sys.exit(code)


if __name__ == "__main__":
    main()
