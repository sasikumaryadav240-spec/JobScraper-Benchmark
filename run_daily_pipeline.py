"""
Master Orchestrator for Daily Job Collection, Deduplication, and Matching Pipeline.

Coordinates:
1. Indeed collection (fault-isolated)
2. Naukri collection (fault-isolated)
3. LinkedIn collection (fault-isolated)
4. ATS collection: Greenhouse, Lever, Ashby (fault-isolated)
5. Unified Normalization & Ingestion
6. Cross-Source Deduplication
7. Candidate Skill Matching & Eligibility Engine
8. Multi-format Output Generation (JSON, CSV, Excel)
9. Daily Run Telemetry, Quality Audits & Markdown Reporting
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
        self.run_mode = run_mode  # 'daily' | 'full' | 'skip_scraping' | 'dry_run'
        self.run_id = f"daily_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self.start_time = datetime.now(timezone.utc)
        self.source_telemetry: dict[str, dict[str, Any]] = {}
        self.run_errors: list[dict[str, Any]] = []

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
        """Execute Indeed collector with fault isolation."""
        logger.info("=== [1/4] INDEED COLLECTION STAGE ===")
        t0 = time.time()
        source_name = "indeed"
        collected_in_run = False
        status = "FAILED"
        rec_count = 0
        err_msg = None

        try:
            collector = IndeedCollector(checkpoint_path=self.base_dir / "indeed_checkpoint.jsonl")
            import indeed_config as cfg
            if self.run_mode == "full":
                searches = cfg.SEARCHES
            else:
                # Representative daily searches
                rep_roles = ["SOC Analyst", "Cybersecurity Analyst", "SIEM Analyst", "Cloud Security Analyst"]
                rep_locations = ["Bengaluru", "Hyderabad"]
                searches = cfg.generate_search_plan(roles=rep_roles, locations=rep_locations, include_remote=True)

            res = collector.run(searches=searches, clean=False)
            status = "SUCCESS" if res.get("unique_records") else "PARTIAL"
            rec_count = len(res.get("records", [])) or res.get("unique_records", 0)
            collected_in_run = True
            logger.info(f"Indeed collection completed: {rec_count} records.")
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
            "records_count": rec_count,
            "last_collection_time": timestamp,
            "error": err_msg,
        }

    def run_naukri(self) -> None:
        """Execute Naukri collector with fault isolation."""
        logger.info("=== [2/4] NAUKRI COLLECTION STAGE ===")
        t0 = time.time()
        source_name = "naukri"
        collected_in_run = False
        status = "FAILED"
        rec_count = 0
        err_msg = None

        try:
            collector = NaukriCollector(checkpoint_path=self.base_dir / "naukri_checkpoint.jsonl")
            import naukri_config as cfg
            if self.run_mode == "full":
                searches = cfg.build_search_plan()
            else:
                rep_roles = ["SOC Analyst", "Cybersecurity Analyst", "SIEM Analyst"]
                rep_locations = ["Bengaluru", "Hyderabad"]
                searches = cfg.build_search_plan(roles=rep_roles, cities=rep_locations, include_remote=True)

            res = collector.run(searches=searches, clean=False)
            rec_count = len(res)
            status = "SUCCESS" if rec_count else "PARTIAL"
            collected_in_run = True
            logger.info(f"Naukri collection completed: {rec_count} records.")
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
            "last_collection_time": timestamp,
            "error": err_msg,
        }

    def run_linkedin(self) -> None:
        """Execute LinkedIn collector with fault isolation."""
        logger.info("=== [3/4] LINKEDIN COLLECTION STAGE ===")
        t0 = time.time()
        source_name = "linkedin"
        collected_in_run = False
        status = "FAILED"
        rec_count = 0
        err_msg = None

        try:
            collector = LinkedInCollector(checkpoint_path=self.base_dir / "linkedin_checkpoint.jsonl")
            import linkedin_config as cfg
            if self.run_mode == "full":
                searches = cfg.build_search_plan()
            else:
                rep_roles = ["SOC Analyst", "Cybersecurity Analyst", "Cloud Security Analyst"]
                rep_locations = ["Bengaluru", "Hyderabad"]
                searches = cfg.build_search_plan(roles=rep_roles, cities=rep_locations, include_remote=True)

            res = collector.run(searches=searches, clean=False)
            rec_count = len(res)
            status = "SUCCESS" if rec_count else "PARTIAL"
            collected_in_run = True
            logger.info(f"LinkedIn collection completed: {rec_count} records.")
        except Exception as e:
            err_msg = str(e)
            logger.error(f"LinkedIn stage encountered an error: {e}", exc_info=True)
            self.run_errors.append({"source": source_name, "error": err_msg, "timestamp": datetime.now(timezone.utc).isoformat()})
            status = "RATE_LIMITED" if "429" in str(e) else "FAILED"

        duration = round(time.time() - t0, 2)
        freshness, timestamp = self._check_source_freshness(self.base_dir / "linkedin_jobs.json", collected_in_run)
        self.source_telemetry[source_name] = {
            "status": status,
            "freshness": freshness,
            "duration_seconds": duration,
            "records_count": rec_count,
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
        err_msg = None

        try:
            collector = ATSCollector()
            res = collector.run()
            rec_count = len(res)
            status = "SUCCESS" if rec_count else "PARTIAL"
            collected_in_run = True
            logger.info(f"ATS collection completed: {rec_count} records.")
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
                    "last_collection_time": timestamp,
                    "error": None,
                }

        # Execute Phase 5 Unified Pipeline
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
            "execution_mode": self.run_mode,
            "start_time_utc": self.start_time.isoformat(),
            "end_time_utc": end_time.isoformat(),
            "total_duration_seconds": total_duration,
            "source_telemetry": self.source_telemetry,
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

        logger.info(f"Daily pipeline finished successfully in {total_duration}s. Report saved to {report_path}")
        return 0

    def _build_markdown_report(self, summary: dict[str, Any]) -> str:
        """Create formatted markdown summary for humans and CI."""
        lines = []
        lines.append(f"# 🛡️ Daily Cybersecurity Job Collection Report")
        lines.append(f"**Run ID**: `{summary['run_id']}` | **Executed (UTC)**: `{summary['start_time_utc']}` | **Duration**: `{summary['total_duration_seconds']}s`\n")

        lines.append("## 1. Source Collection Status")
        lines.append("| Source | Status | Freshness | Records | Duration | Details |")
        lines.append("|---|---|---|---|---|---|")
        for src, t in summary["source_telemetry"].items():
            status_badge = "✅ SUCCESS" if t["status"] == "SUCCESS" else ("⚠️ " + t["status"])
            fresh_badge = "🟢 FRESH" if t["freshness"] == "FRESH" else ("🟡 " + t["freshness"])
            lines.append(
                f"| **{src.upper()}** | {status_badge} | {fresh_badge} | {t['records_count']} | {t['duration_seconds']}s | {t.get('error') or 'OK'} |"
            )

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
