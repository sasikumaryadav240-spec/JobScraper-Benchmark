"""
Tests for Phase 6.3 — Workflow Runtime and Data Recovery Fixes.

Covers all 9 required verification scenarios:
1. ATS incremental persistence after the first successful board.
2. ATS exports contain records from two completed boards when board three fails.
3. Failed boards are not marked completed.
4. ATS checkpoint resumption does not lose or duplicate records.
5. Interrupted ATS execution still allows unified processing when source data is available.
6. Partial LinkedIn plus partial ATS still produces unified outputs.
7. Existing source failures remain correctly reflected in final pipeline status.
8. Artifact configuration includes JSONL checkpoint files.
9. Atomic export writes preserve the previous valid file if writing fails.
"""

from __future__ import annotations

import csv
import json
import mimetypes
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Windows registry permission guard for openpyxl / mimetypes
try:
    mimetypes.init()
except PermissionError:
    mimetypes._winreg = None
    mimetypes.init = lambda *args, **kwargs: None

from ats_collector import ATSCollector, _write_csv_atomic, _write_json_atomic
from ats_registry import EmployerBoard
from models import JobRecord
from run_daily_pipeline import DailyPipelineOrchestrator
from unified_pipeline import UnifiedPipeline


class TestPhase63RuntimeRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        # Patch sleep so tests run swiftly
        self.sleep_patcher = patch("time.sleep", return_value=None)
        self.sleep_patcher.start()

    def tearDown(self) -> None:
        self.sleep_patcher.stop()
        self.temp_dir.cleanup()

    def _create_board(self, name: str, platform: str, identifier: str) -> EmployerBoard:
        return EmployerBoard(
            company_name=name,
            ats_platform=platform,
            board_identifier=identifier,
            board_url=f"https://jobs.{platform}.com/{identifier}",
            country_or_market="India",
            verification_timestamp="2026-11-21T00:00:00Z",
            discovery_source="verified_registry",
        )

    # 1. ATS incremental persistence after the first successful board
    def test_01_ats_incremental_persistence_first_board(self):
        b1 = self._create_board("Cyderes", "lever", "cyderes")
        b2 = self._create_board("Elastic", "greenhouse", "elastic")

        collector = ATSCollector(
            checkpoint_path=self.base_dir / "ats_checkpoint.jsonl",
            jobs_json_path=self.base_dir / "ats_jobs.json",
            jobs_csv_path=self.base_dir / "ats_jobs.csv",
            greenhouse_json_path=self.base_dir / "greenhouse_jobs.json",
            lever_json_path=self.base_dir / "lever_jobs.json",
            ashby_json_path=self.base_dir / "ashby_jobs.json",
            errors_path=self.base_dir / "ats_errors.json",
            quality_path=self.base_dir / "ats_quality_report.json",
            summary_path=self.base_dir / "ats_run_summary.json",
        )

        b1_raw = [{
            "id": "cyd-001",
            "text": "SOC Analyst",
            "description": "Splunk Sentinel SIEM analyst with 2 years experience in Bangalore",
            "categories": {"location": "Bengaluru", "commitment": "Full-time"},
            "hostedUrl": "https://jobs.lever.co/cyderes/cyd-001",
            "applyUrl": "https://jobs.lever.co/cyderes/cyd-001/apply",
        }]

        board2_checked = False

        def fake_fetch(board):
            nonlocal board2_checked
            if board.board_identifier == "cyderes":
                return b1_raw
            if board.board_identifier == "elastic":
                # When board 2 is about to run, board 1 must ALREADY be persisted!
                self.assertTrue((self.base_dir / "ats_jobs.json").exists(), "ats_jobs.json must exist before board 2")
                with open(self.base_dir / "ats_jobs.json", "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    self.assertEqual(len(saved), 1, "ats_jobs.json must contain board 1 data incrementally")
                    self.assertEqual(saved[0]["title"], "SOC Analyst")
                board2_checked = True
                return []
            return []

        with patch.object(collector, "fetch_board_with_backoff", side_effect=fake_fetch):
            collector.run(boards=[b1, b2])

        self.assertTrue(board2_checked, "Board 2 should have executed and verified incremental persistence")
        self.assertTrue((self.base_dir / "ats_checkpoint.jsonl").exists())
        self.assertTrue((self.base_dir / "ats_run_summary.json").exists())

    # 2. ATS exports contain records from two completed boards when board three fails
    def test_02_ats_exports_preserve_two_boards_when_third_fails(self):
        b1 = self._create_board("Cyderes", "lever", "cyderes")
        b2 = self._create_board("Elastic", "greenhouse", "elastic")
        b3 = self._create_board("FailCo", "ashby", "failco")

        collector = ATSCollector(
            checkpoint_path=self.base_dir / "ats_checkpoint.jsonl",
            jobs_json_path=self.base_dir / "ats_jobs.json",
            jobs_csv_path=self.base_dir / "ats_jobs.csv",
            greenhouse_json_path=self.base_dir / "greenhouse_jobs.json",
            lever_json_path=self.base_dir / "lever_jobs.json",
            ashby_json_path=self.base_dir / "ashby_jobs.json",
            errors_path=self.base_dir / "ats_errors.json",
            quality_path=self.base_dir / "ats_quality_report.json",
            summary_path=self.base_dir / "ats_run_summary.json",
        )

        b1_raw = [{
            "id": "cyd-002",
            "text": "Cybersecurity Engineer",
            "description": "SOC SIEM analyst 1 year experience in Bengaluru",
            "categories": {"location": "Bengaluru", "commitment": "Full-time"},
            "hostedUrl": "https://jobs.lever.co/cyderes/cyd-002",
            "applyUrl": "https://jobs.lever.co/cyderes/cyd-002/apply",
        }]
        b2_raw = [{
            "id": 1002,
            "title": "Information Security Analyst",
            "content": "Incident response and SIEM monitoring 2 years experience in Bangalore",
            "location": {"name": "Bengaluru"},
            "absolute_url": "https://boards.greenhouse.io/elastic/jobs/1002",
        }]

        def fake_fetch(board):
            if board.board_identifier == "cyderes":
                return b1_raw
            if board.board_identifier == "elastic":
                return b2_raw
            if board.board_identifier == "failco":
                collector.run_errors.append({
                    "board_identifier": "failco",
                    "final_outcome": "exhausted_retries",
                    "error": "HTTP 500 Internal Server Error",
                })
                return []
            return []

        with patch.object(collector, "fetch_board_with_backoff", side_effect=fake_fetch):
            collector.run(boards=[b1, b2, b3])

        # Verify ats_jobs.json exists and has records from both Cyderes and Elastic
        self.assertTrue((self.base_dir / "ats_jobs.json").exists())
        with open(self.base_dir / "ats_jobs.json", "r", encoding="utf-8") as f:
            jobs = json.load(f)
        self.assertEqual(len(jobs), 2, "Both completed boards must be preserved when third fails")
        companies = {j["company"] for j in jobs}
        self.assertIn("Cyderes", companies)
        self.assertIn("Elastic", companies)

        # Verify summary reflects 2 completed, 1 failed
        with open(self.base_dir / "ats_run_summary.json", "r", encoding="utf-8") as f:
            summary = json.load(f)
        self.assertEqual(summary["boards_completed"], 2)
        self.assertEqual(summary["boards_failed"], 1)

    # 3. Failed boards are not marked completed
    def test_03_failed_boards_not_marked_completed(self):
        b1 = self._create_board("Cyderes", "lever", "cyderes")
        b2 = self._create_board("FailCo", "ashby", "failco")

        collector = ATSCollector(
            checkpoint_path=self.base_dir / "ats_checkpoint.jsonl",
            jobs_json_path=self.base_dir / "ats_jobs.json",
            jobs_csv_path=self.base_dir / "ats_jobs.csv",
            greenhouse_json_path=self.base_dir / "greenhouse_jobs.json",
            lever_json_path=self.base_dir / "lever_jobs.json",
            ashby_json_path=self.base_dir / "ashby_jobs.json",
            errors_path=self.base_dir / "ats_errors.json",
            quality_path=self.base_dir / "ats_quality_report.json",
            summary_path=self.base_dir / "ats_run_summary.json",
        )

        def fake_fetch(board):
            if board.board_identifier == "cyderes":
                return [{
                    "id": "cyd-003",
                    "text": "Security Operations Specialist",
                    "description": "SOC SIEM analyst 1 year experience Bangalore",
                    "categories": {"location": "Bengaluru", "commitment": "Full-time"},
                    "hostedUrl": "https://jobs.lever.co/cyderes/cyd-003",
                    "applyUrl": "https://jobs.lever.co/cyderes/cyd-003/apply",
                }]
            collector.run_errors.append({
                "board_identifier": "failco",
                "final_outcome": "exhausted_retries",
                "error": "Connection timeout",
            })
            return []

        with patch.object(collector, "fetch_board_with_backoff", side_effect=fake_fetch):
            collector.run(boards=[b1, b2])

        self.assertIn(("lever", "cyderes"), collector.completed_boards)
        self.assertNotIn(("ashby", "failco"), collector.completed_boards)

        # Inspect checkpoint file
        with open(self.base_dir / "ats_checkpoint.jsonl", "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertTrue(all(item.get("board_identifier") != "failco" for item in lines))

    # 4. ATS checkpoint resumption does not lose or duplicate records
    def test_04_ats_checkpoint_resumption_no_duplication(self):
        b1 = self._create_board("Cyderes", "lever", "cyderes")
        b2 = self._create_board("Elastic", "greenhouse", "elastic")

        # Run 1: Cyderes succeeds, Elastic fails
        collector1 = ATSCollector(
            checkpoint_path=self.base_dir / "ats_checkpoint.jsonl",
            jobs_json_path=self.base_dir / "ats_jobs.json",
            jobs_csv_path=self.base_dir / "ats_jobs.csv",
            greenhouse_json_path=self.base_dir / "greenhouse_jobs.json",
            lever_json_path=self.base_dir / "lever_jobs.json",
            ashby_json_path=self.base_dir / "ashby_jobs.json",
            errors_path=self.base_dir / "ats_errors.json",
            quality_path=self.base_dir / "ats_quality_report.json",
            summary_path=self.base_dir / "ats_run_summary.json",
        )

        b1_raw = [{
            "id": "cyd-004",
            "text": "SOC Analyst",
            "description": "SOC SIEM analyst 1 year experience Bangalore",
            "categories": {"location": "Bengaluru", "commitment": "Full-time"},
            "hostedUrl": "https://jobs.lever.co/cyderes/cyd-004",
            "applyUrl": "https://jobs.lever.co/cyderes/cyd-004/apply",
        }]
        b2_raw = [{
            "id": 1004,
            "title": "Cloud Security Engineer",
            "content": "Cloud security and incident response 2 years experience Bangalore",
            "location": {"name": "Bengaluru"},
            "absolute_url": "https://boards.greenhouse.io/elastic/jobs/1004",
        }]

        with patch.object(collector1, "fetch_board_with_backoff") as mock_fetch1:
            mock_fetch1.side_effect = lambda b: b1_raw if b.board_identifier == "cyderes" else (
                collector1.run_errors.append({"board_identifier": "elastic", "final_outcome": "exhausted_retries"}), []
            )[1]
            collector1.run(boards=[b1, b2])

        self.assertEqual(len(collector1.completed_boards), 1)

        # Run 2: Resume with both boards; Cyderes should be skipped, Elastic succeeds
        collector2 = ATSCollector(
            checkpoint_path=self.base_dir / "ats_checkpoint.jsonl",
            jobs_json_path=self.base_dir / "ats_jobs.json",
            jobs_csv_path=self.base_dir / "ats_jobs.csv",
            greenhouse_json_path=self.base_dir / "greenhouse_jobs.json",
            lever_json_path=self.base_dir / "lever_jobs.json",
            ashby_json_path=self.base_dir / "ashby_jobs.json",
            errors_path=self.base_dir / "ats_errors.json",
            quality_path=self.base_dir / "ats_quality_report.json",
            summary_path=self.base_dir / "ats_run_summary.json",
        )

        fetch_calls = []

        def track_fetch(b):
            fetch_calls.append(b.board_identifier)
            return b2_raw

        with patch.object(collector2, "fetch_board_with_backoff", side_effect=track_fetch):
            collector2.run(boards=[b1, b2])

        self.assertNotIn("cyderes", fetch_calls, "Already completed board Cyderes must be skipped")
        self.assertIn("elastic", fetch_calls, "Incomplete board Elastic must be fetched")

        # Verify no duplicate jobs in ats_jobs.json
        with open(self.base_dir / "ats_jobs.json", "r", encoding="utf-8") as f:
            final_jobs = json.load(f)
        self.assertEqual(len(final_jobs), 2, "There must be exactly 2 unique jobs without duplication")

        with open(self.base_dir / "ats_run_summary.json", "r", encoding="utf-8") as f:
            summary = json.load(f)
        self.assertEqual(summary["boards_resumed"], 1)
        self.assertEqual(summary["boards_completed"], 2)
        self.assertEqual(summary["raw_listings_from_checkpoint"], 1)
        self.assertEqual(summary["raw_listings_fetched_current_run"], 1)

    # 5. Interrupted ATS execution still allows unified processing when source data is available
    def test_05_interrupted_ats_allows_unified_processing(self):
        # Create valid ATS jobs JSON as if written incrementally before crash
        ats_jobs = [{
            "id": "ats_01",
            "source": "ats",
            "source_job_id": "cyd-10",
            "title": "Junior Cybersecurity Analyst",
            "company": "Cyderes",
            "location": "Bengaluru",
            "country": "India",
            "job_url": "https://jobs.lever.co/cyderes/cyd-10",
            "application_url": "https://jobs.lever.co/cyderes/cyd-10/apply",
            "description": "SOC SIEM analyst Splunk Sentinel 1 year experience in Bengaluru",
            "min_experience": 1.0,
            "work_mode": "Hybrid",
            "description_type": "full",
            "verification_status": "verified",
        }]
        with open(self.base_dir / "ats_jobs.json", "w", encoding="utf-8") as f:
            json.dump(ats_jobs, f)

        # Mock ATS collector raising an unhandled exception midway through run_ats()
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="full")

        with patch("run_daily_pipeline.IndeedCollector") as MockIndeed, \
             patch("run_daily_pipeline.NaukriCollector") as MockNaukri, \
             patch("run_daily_pipeline.LinkedInCollector") as MockLinkedIn, \
             patch("run_daily_pipeline.ATSCollector") as MockATS:

            MockIndeed.return_value.run.return_value = {"unique_records_after_dedup": 0, "searches_planned": 1, "searches_completed": 1}
            MockNaukri.return_value.run.return_value = []
            MockLinkedIn.return_value.run.return_value = []
            MockATS.return_value.run.side_effect = RuntimeError("Runner network interrupted")

            exit_code = orchestrator.run_orchestration()

        self.assertEqual(exit_code, 0)
        # Unified processing must have run and produced master outputs
        self.assertTrue((self.base_dir / "deduplicated_jobs.json").exists())
        self.assertTrue((self.base_dir / "deduplicated_jobs.csv").exists())
        self.assertTrue((self.base_dir / "deduplicated_jobs.xlsx").exists())
        self.assertTrue((self.base_dir / "daily_run_report.md").exists())

        with open(self.base_dir / "deduplicated_jobs.json", "r", encoding="utf-8") as f:
            dedup = json.load(f)
        self.assertEqual(len(dedup), 1, "The incrementally saved ATS job must be ingested")
        self.assertEqual(dedup[0]["job_title"], "Junior Cybersecurity Analyst")

        # Telemetry should capture that ATS was PARTIAL with saved records
        self.assertEqual(orchestrator.source_telemetry["ats"]["status"], "PARTIAL")
        self.assertEqual(orchestrator.source_telemetry["ats"]["records_count"], 1)

    # 6. Partial LinkedIn plus partial ATS still produces unified outputs
    def test_06_partial_linkedin_and_partial_ats_produce_unified_outputs(self):
        # Create LinkedIn partial jobs
        linkedin_jobs = [
            {
                "id": "li_01",
                "source": "linkedin",
                "source_job_id": "li-101",
                "title": "Junior SOC Analyst",
                "company": "Infosys",
                "location": "Bengaluru",
                "country": "India",
                "job_url": "https://linkedin.com/jobs/view/li-101",
                "application_url": "https://infosys.com/apply",
                "description": "Junior SOC Analyst Splunk Sentinel monitoring 1 year exp. Engineering accepted.",
                "min_experience": 1.0,
                "work_mode": "Hybrid",
                "description_type": "full",
                "verification_status": "verified",
            },
            {
                "id": "li_02",
                "source": "linkedin",
                "source_job_id": "li-102",
                "title": "Desktop Support Engineer",
                "company": "Wipro",
                "location": "Bengaluru",
                "country": "India",
                "job_url": "https://linkedin.com/jobs/view/li-102",
                "application_url": "https://wipro.com/apply",
                "description": "General desktop hardware support 1 year exp.",
                "min_experience": 1.0,
                "work_mode": "On-site",
                "description_type": "full",
                "verification_status": "verified",
            },
        ]
        # Create ATS partial jobs
        ats_jobs = [
            {
                "id": "ats_01",
                "source": "ats",
                "source_job_id": "cyd-201",
                "title": "Information Security Engineer",
                "company": "Cyderes",
                "location": "Bengaluru",
                "country": "India",
                "job_url": "https://jobs.lever.co/cyderes/cyd-201",
                "application_url": "https://jobs.lever.co/cyderes/cyd-201/apply",
                "description": "Incident response SOC SIEM Sentinel 1 year experience Bangalore. Engineering degree.",
                "min_experience": 1.0,
                "work_mode": "Remote",
                "description_type": "full",
                "verification_status": "verified",
            },
            {
                "id": "ats_02",
                "source": "ats",
                "source_job_id": "ela-202",
                "title": "Cybersecurity Intern",
                "company": "Elastic",
                "location": "Bengaluru",
                "country": "India",
                "job_url": "https://boards.greenhouse.io/elastic/jobs/202",
                "application_url": "https://boards.greenhouse.io/elastic/jobs/202/apply",
                "description": "Cybersecurity internship for students in SOC",
                "min_experience": 0.0,
                "work_mode": "Remote",
                "description_type": "full",
                "verification_status": "verified",
            },
        ]

        with open(self.base_dir / "linkedin_jobs.json", "w", encoding="utf-8") as f:
            json.dump(linkedin_jobs, f)
        with open(self.base_dir / "ats_jobs.json", "w", encoding="utf-8") as f:
            json.dump(ats_jobs, f)

        # Run orchestrator in skip_scraping mode to process existing partial source files
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="skip_scraping")
        orchestrator.run_orchestration()

        # Check deduplicated outputs
        with open(self.base_dir / "deduplicated_jobs.json", "r", encoding="utf-8") as f:
            master_jobs = json.load(f)
        self.assertEqual(len(master_jobs), 4, "All 4 records across partial sources must be in master deduplicated dataset")

        # Check matched jobs exists and has the cybersecurity match
        with open(self.base_dir / "matched_jobs.json", "r", encoding="utf-8") as f:
            matched = json.load(f)
        self.assertGreater(len(matched), 0, "Cybersecurity roles meeting criteria should be classified in matched jobs")

        # Check Excel export
        self.assertTrue((self.base_dir / "deduplicated_jobs.xlsx").exists())
        self.assertTrue((self.base_dir / "matched_jobs.xlsx").exists())

    # 7. Existing source failures remain correctly reflected in final pipeline status
    def test_07_source_failures_accurately_reflected_in_final_status(self):
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="full")

        with patch("run_daily_pipeline.IndeedCollector") as MockIndeed, \
             patch("run_daily_pipeline.NaukriCollector") as MockNaukri, \
             patch("run_daily_pipeline.LinkedInCollector") as MockLinkedIn, \
             patch("run_daily_pipeline.ATSCollector") as MockATS:

            # Indeed: SUCCESS
            MockIndeed.return_value.run.return_value = {
                "unique_records_after_dedup": 1,
                "raw_records_returned": 1,
                "duplicates_removed": 0,
                "issues_recorded": 0,
                "searches_planned": 1,
                "searches_completed": 1,
                "searches_failed": 0,
                "searches_skipped": 0,
                "circuit_breaker_stats": {},
            }
            MockIndeed.return_value.raw_records = [{"raw": 1}]
            MockIndeed.return_value.run_errors = []

            # Naukri: FAILED
            MockNaukri.return_value.run.side_effect = RuntimeError("HTTP 406 CAPTCHA triggered")

            # LinkedIn: RATE_LIMITED
            MockLinkedIn.return_value.run.side_effect = RuntimeError("HTTP 429 Too Many Requests")

            # ATS: SUCCESS
            MockATS.return_value.run.return_value = [
                JobRecord(id="ats_1", title="Security Analyst", company="Cyderes", source="ats", source_job_id="2", location="Bengaluru", job_url="https://cyd.com/2", description="Security analyst", min_experience=1.0)
            ]
            MockATS.return_value.raw_records = [{"raw": 2}]
            MockATS.return_value.run_errors = []
            MockATS.return_value.completed_boards = set(range(15))

            orchestrator.run_orchestration()

        with open(self.base_dir / "daily_run_summary.json", "r", encoding="utf-8") as f:
            summary = json.load(f)

        self.assertEqual(summary["overall_run_status"], "PARTIAL_EXECUTION")
        self.assertFalse(summary["source_status_breakdown"]["complete_collection_achieved"])
        self.assertIn("naukri", summary["source_status_breakdown"]["failed"])
        self.assertIn("linkedin", summary["source_status_breakdown"]["partial"])
        self.assertIn("indeed", summary["source_status_breakdown"]["succeeded"])
        self.assertIn("ats", summary["source_status_breakdown"]["succeeded"])

    # 8. Artifact configuration includes JSONL checkpoint files
    def test_08_workflow_artifact_config_includes_jsonl(self):
        workflow_path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "daily-job-scraper.yml"
        self.assertTrue(workflow_path.exists(), f"Workflow file must exist at {workflow_path}")

        with open(workflow_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Verify timeout-minutes: 90
        self.assertIn("timeout-minutes: 90", content, "Job timeout must be increased to 90 minutes")

        # Verify jobspy/*.jsonl is included in artifact upload
        self.assertIn("jobspy/*.jsonl", content, "jobspy/*.jsonl must be present in artifact paths")

        # Verify if: always() is preserved
        self.assertIn("if: always()", content, "if: always() must be present for artifact upload step")

    # 9. Atomic export writes preserve the previous valid file if writing fails
    def test_09_atomic_writes_preserve_valid_file_on_write_failure(self):
        json_target = self.base_dir / "valid_data.json"
        initial_data = [{"job_id": "valid_1", "status": "intact"}]

        # Write valid initial file
        _write_json_atomic(json_target, initial_data)
        self.assertTrue(json_target.exists())

        # Attempt to write circular structure that fails in json.dump
        circular_data: list = []
        circular_data.append(circular_data)

        with self.assertRaises(ValueError):
            _write_json_atomic(json_target, circular_data)

        # Confirm target file is untouched and remains the valid initial data
        with open(json_target, "r", encoding="utf-8") as f:
            preserved_data = json.load(f)
        self.assertEqual(preserved_data, initial_data, "Original file must be preserved on write failure")

        # Confirm no temporary files leaked
        temp_files = list(self.base_dir.glob("valid_data.json.tmp_*"))
        self.assertEqual(len(temp_files), 0, "Temporary files should be removed on failure")

        # Test _write_csv_atomic failure preservation
        csv_target = self.base_dir / "valid_data.csv"
        initial_rows = [{"col1": "val1", "col2": "val2"}]
        _write_csv_atomic(csv_target, initial_rows, ["col1", "col2"])
        self.assertTrue(csv_target.exists())

        # Create generator that fails mid-iteration during writing
        def bad_generator():
            yield {"col1": "first", "col2": "row"}
            raise RuntimeError("Disk write failure simulation")

        with self.assertRaises(RuntimeError):
            _write_csv_atomic(csv_target, bad_generator(), ["col1", "col2"])

        # Confirm target CSV file is untouched and remains the valid initial data
        with open(csv_target, "r", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
        self.assertEqual(reader, initial_rows, "Original CSV file must be preserved on write failure")

        csv_temp_files = list(self.base_dir.glob("valid_data.csv.tmp_*"))
        self.assertEqual(len(csv_temp_files), 0, "Temporary CSV files should be removed on failure")


if __name__ == "__main__":
    unittest.main()
