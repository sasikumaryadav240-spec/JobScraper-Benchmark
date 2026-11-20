"""
Integration Tests for Phase 6 Collector Interfaces, Telemetry, and Fault Isolation.

Uses autospec=True against real collector classes to strictly verify:
1. Naukri orchestrator invokes the actual supported collector interface run(plan=...).
2. LinkedIn orchestrator invokes the actual supported collector interface run(plan=...).
3. Indeed unique record counts are reported accurately (distinguishing raw vs unique vs duplicates).
4. Naukri failure does not stop LinkedIn or ATS.
5. LinkedIn HTTP 429 does not stop other sources.
6. A failed source is not marked successful.
7. Missing output caused by a failure is not treated as an empty successful dataset.
8. Partial source data is retained.
9. The orchestrator reports incomplete source coverage (PARTIAL_EXECUTION).
10. Successful source data is still deduplicated and matched.
11. All expected output files are generated.
12. The final summary reconciles source counts and unique job counts.
"""

from __future__ import annotations

import json
import mimetypes
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Windows registry permission guard for openpyxl
try:
    mimetypes.init()
except PermissionError:
    mimetypes._winreg = None
    mimetypes.init = lambda *args, **kwargs: None

from ats_collector import ATSCollector
from indeed_collector import IndeedCollector
from linkedin_collector import LinkedInCollector
from models import JobRecord
from naukri_collector import NaukriCollector
from run_daily_pipeline import DailyPipelineOrchestrator


class TestPhase6CollectorIntegration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    # 1. Naukri orchestrator invokes the actual supported collector interface
    def test_01_naukri_invokes_actual_interface(self):
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="daily")
        with patch("run_daily_pipeline.NaukriCollector", autospec=True) as MockNaukri:
            instance = MockNaukri.return_value
            instance.raw_records = []
            instance.run_errors = []
            instance.run.return_value = []

            orchestrator.run_naukri()

            # Verify run was called
            self.assertTrue(instance.run.called)
            call_kwargs = instance.run.call_args.kwargs
            call_args = instance.run.call_args.args

            # Must use 'plan' parameter, NEVER 'searches' or 'clean'
            self.assertIn("plan", call_kwargs)
            self.assertNotIn("searches", call_kwargs)
            self.assertNotIn("clean", call_kwargs)
            self.assertIsInstance(call_kwargs["plan"], list)
            self.assertGreater(len(call_kwargs["plan"]), 0)

    # 2. LinkedIn orchestrator invokes the actual supported collector interface
    def test_02_linkedin_invokes_actual_interface(self):
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="daily")
        with patch("run_daily_pipeline.LinkedInCollector", autospec=True) as MockLinkedIn:
            instance = MockLinkedIn.return_value
            instance.raw_records = []
            instance.run_errors = []
            instance.run.return_value = []

            orchestrator.run_linkedin()

            self.assertTrue(instance.run.called)
            call_kwargs = instance.run.call_args.kwargs

            # Must use 'plan' parameter, NEVER 'searches' or 'clean'
            self.assertIn("plan", call_kwargs)
            self.assertNotIn("searches", call_kwargs)
            self.assertNotIn("clean", call_kwargs)
            self.assertIsInstance(call_kwargs["plan"], list)
            self.assertGreater(len(call_kwargs["plan"]), 0)

    # 3. Indeed unique record counts are reported correctly
    def test_03_indeed_counts_reported_correctly(self):
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="daily")
        with patch("run_daily_pipeline.IndeedCollector", autospec=True) as MockIndeed:
            instance = MockIndeed.return_value
            # Return real Indeed run summary format
            instance.run.return_value = {
                "raw_records_returned": 149,
                "unique_records_after_dedup": 87,
                "duplicates_removed": 62,
                "issues_recorded": 0,
            }

            # Create a mock indeed_jobs.json file to simulate successful save
            with open(self.base_dir / "indeed_jobs.json", "w", encoding="utf-8") as f:
                json.dump([{"title": "SOC Analyst", "company": "Co A"}] * 87, f)

            orchestrator.run_indeed()

            t = orchestrator.source_telemetry["indeed"]
            self.assertEqual(t["records_count"], 87)
            self.assertEqual(t["raw_records_count"], 149)
            self.assertEqual(t["duplicates_removed"], 62)
            self.assertEqual(t["status"], "SUCCESS")

    # 4. Naukri failure does not stop LinkedIn or ATS
    def test_04_naukri_failure_does_not_stop_linkedin_or_ats(self):
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="daily")

        with patch("run_daily_pipeline.IndeedCollector", autospec=True) as MockIndeed, \
             patch("run_daily_pipeline.NaukriCollector", autospec=True) as MockNaukri, \
             patch("run_daily_pipeline.LinkedInCollector", autospec=True) as MockLinkedIn, \
             patch("run_daily_pipeline.ATSCollector", autospec=True) as MockATS:

            # Indeed succeeds
            MockIndeed.return_value.run.return_value = {
                "raw_records_returned": 20, "unique_records_after_dedup": 15, "duplicates_removed": 5, "issues_recorded": 0
            }
            # Naukri crashes with HTTP 406
            MockNaukri.return_value.run.side_effect = RuntimeError("HTTP 406 Not Acceptable (Akamai reCAPTCHA)")
            # LinkedIn succeeds
            MockLinkedIn.return_value.run.return_value = []
            MockLinkedIn.return_value.raw_records = []
            MockLinkedIn.return_value.run_errors = []
            # ATS succeeds
            MockATS.return_value.run.return_value = []
            MockATS.return_value.raw_records = []
            MockATS.return_value.run_errors = []

            # Create output for indeed
            with open(self.base_dir / "indeed_jobs.json", "w", encoding="utf-8") as f:
                json.dump([{"title": "SOC Analyst", "company": "Co A", "source": "indeed", "source_job_id": "1"}], f)

            orchestrator.run_orchestration()

            # Verify LinkedIn and ATS ran despite Naukri failure
            self.assertTrue(MockLinkedIn.return_value.run.called)
            self.assertTrue(MockATS.return_value.run.called)
            self.assertEqual(orchestrator.source_telemetry["naukri"]["status"], "FAILED")

    # 5. LinkedIn HTTP 429 does not stop other sources
    def test_05_linkedin_http_429_does_not_stop_other_sources(self):
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="daily")

        with patch("run_daily_pipeline.IndeedCollector", autospec=True) as MockIndeed, \
             patch("run_daily_pipeline.NaukriCollector", autospec=True) as MockNaukri, \
             patch("run_daily_pipeline.LinkedInCollector", autospec=True) as MockLinkedIn, \
             patch("run_daily_pipeline.ATSCollector", autospec=True) as MockATS:

            MockIndeed.return_value.run.return_value = {"raw_records_returned": 5, "unique_records_after_dedup": 5}
            MockNaukri.return_value.run.return_value = []
            MockNaukri.return_value.raw_records = []
            MockNaukri.return_value.run_errors = []
            # LinkedIn hits 429
            MockLinkedIn.return_value.run.side_effect = RuntimeError("HTTP 429 Too Many Requests")
            # ATS succeeds
            MockATS.return_value.run.return_value = []
            MockATS.return_value.raw_records = []
            MockATS.return_value.run_errors = []

            orchestrator.run_orchestration()

            self.assertEqual(orchestrator.source_telemetry["linkedin"]["status"], "RATE_LIMITED")
            self.assertTrue(MockATS.return_value.run.called)

    # 6. A failed source is not marked successful
    def test_06_failed_source_not_marked_successful(self):
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="daily")
        with patch("run_daily_pipeline.NaukriCollector", autospec=True) as MockNaukri:
            MockNaukri.return_value.run.side_effect = ConnectionError("Network unreachable")
            orchestrator.run_naukri()
            self.assertEqual(orchestrator.source_telemetry["naukri"]["status"], "FAILED")
            self.assertNotEqual(orchestrator.source_telemetry["naukri"]["status"], "SUCCESS")

    # 7. Missing output caused by failure is not treated as an empty successful dataset
    def test_07_missing_output_not_treated_as_empty_success(self):
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="daily")
        with patch("run_daily_pipeline.NaukriCollector", autospec=True) as MockNaukri:
            MockNaukri.return_value.run.side_effect = RuntimeError("HTTP 406 Challenge")
            orchestrator.run_naukri()

            t = orchestrator.source_telemetry["naukri"]
            self.assertEqual(t["status"], "FAILED")
            self.assertEqual(t["freshness"], "UNAVAILABLE")
            self.assertEqual(t["records_count"], 0)

    # 8. Partial source data is retained
    def test_08_partial_source_data_retained(self):
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="daily")
        with patch("run_daily_pipeline.LinkedInCollector", autospec=True) as MockLinkedIn:
            # Succeeded partially with 10 records before rate limiting
            instance = MockLinkedIn.return_value
            mock_records = [MagicMock() for _ in range(10)]
            instance.run.return_value = mock_records
            instance.raw_records = [{}] * 15
            instance.run_errors = [{"status_code": 429, "error_message": "Rate limited on 3rd query"}]

            orchestrator.run_linkedin()

            t = orchestrator.source_telemetry["linkedin"]
            self.assertEqual(t["status"], "PARTIAL")
            self.assertEqual(t["records_count"], 10)
            self.assertEqual(t["raw_records_count"], 15)

    # 9. Orchestrator reports incomplete source coverage
    def test_09_orchestrator_reports_incomplete_source_coverage(self):
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="daily")
        with patch("run_daily_pipeline.IndeedCollector", autospec=True) as MockIndeed, \
             patch("run_daily_pipeline.NaukriCollector", autospec=True) as MockNaukri, \
             patch("run_daily_pipeline.LinkedInCollector", autospec=True) as MockLinkedIn, \
             patch("run_daily_pipeline.ATSCollector", autospec=True) as MockATS:

            MockIndeed.return_value.run.return_value = {"raw_records_returned": 10, "unique_records_after_dedup": 10}
            MockNaukri.return_value.run.side_effect = RuntimeError("HTTP 406")
            MockLinkedIn.return_value.run.side_effect = RuntimeError("HTTP 429")
            MockATS.return_value.run.return_value = []
            MockATS.return_value.raw_records = []
            MockATS.return_value.run_errors = []

            orchestrator.run_orchestration()

            with open(self.base_dir / "daily_run_summary.json", encoding="utf-8") as f:
                summary = json.load(f)

            self.assertEqual(summary["overall_run_status"], "PARTIAL_EXECUTION")
            self.assertFalse(summary["source_status_breakdown"]["complete_collection_achieved"])
            self.assertIn("naukri", summary["source_status_breakdown"]["failed"])
            self.assertIn("linkedin", summary["source_status_breakdown"]["partial"])

    # 10. Successful source data is still deduplicated and matched
    def test_10_successful_data_still_deduped_and_matched(self):
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="skip_scraping")
        # Put 2 Indeed records (1 duplicate) and 1 ATS record
        indeed_jobs = [
            {"title": "Junior SOC Analyst", "company": "Co A", "source": "indeed", "source_job_id": "1", "location": "Bengaluru", "job_url": "https://co.com/1", "description": "Splunk Sentinel 1 yr exp", "min_experience": 1.0},
            {"title": "Junior SOC Analyst", "company": "Co A", "source": "indeed", "source_job_id": "1", "location": "Bengaluru", "job_url": "https://co.com/1", "description": "Splunk Sentinel 1 yr exp", "min_experience": 1.0},
        ]
        ats_jobs = [
            {"title": "Platform Operations Analyst", "company": "Cyderes", "source": "ats", "source_job_id": "2", "location": "Bengaluru", "job_url": "https://co.com/2", "description": "Security operations analyst 1 yr exp", "min_experience": 1.0},
        ]
        with open(self.base_dir / "indeed_jobs.json", "w", encoding="utf-8") as f:
            json.dump(indeed_jobs, f)
        with open(self.base_dir / "ats_jobs.json", "w", encoding="utf-8") as f:
            json.dump(ats_jobs, f)

        orchestrator.run_orchestration()

        with open(self.base_dir / "deduplicated_jobs.json", encoding="utf-8") as f:
            dedup = json.load(f)
        # 3 raw records -> 2 unique canonical records
        self.assertEqual(len(dedup), 2)

    # 11. All expected output files are generated
    def test_11_all_expected_output_files_generated(self):
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="skip_scraping")
        jobs = [{"title": "SOC Analyst", "company": "Co A", "source": "indeed", "source_job_id": "1", "location": "Bengaluru", "job_url": "https://co.com/1", "min_experience": 1.0}]
        with open(self.base_dir / "indeed_jobs.json", "w", encoding="utf-8") as f:
            json.dump(jobs, f)

        orchestrator.run_orchestration()

        expected_files = [
            "deduplicated_jobs.json", "deduplicated_jobs.csv", "deduplicated_jobs.xlsx",
            "matched_jobs.json", "matched_jobs.csv", "matched_jobs.xlsx",
            "manual_verification_jobs.json", "manual_verification_jobs.csv", "manual_verification_jobs.xlsx",
            "supporting_it_jobs.json", "internships.json", "excluded_jobs.json",
            "unified_jobs.json", "unified_jobs.csv", "unified_jobs.xlsx",
            "duplicate_audit.json", "matching_report.json", "unified_quality_report.json",
            "daily_run_summary.json", "daily_run_report.md", "daily_run_errors.json",
        ]
        for ef in expected_files:
            self.assertTrue((self.base_dir / ef).exists(), f"Missing expected output file: {ef}")

    # 12. Final summary reconciles source counts and unique job counts
    def test_12_final_summary_reconciles_counts(self):
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="skip_scraping")
        jobs = [
            {"title": "SOC Analyst", "company": "Co A", "source": "indeed", "source_job_id": "1", "location": "Bengaluru", "job_url": "https://co.com/1", "description": "SOC analyst 1 yr exp", "min_experience": 1.0},
            {"title": "Desktop Support", "company": "Co B", "source": "ats", "source_job_id": "2", "location": "Chennai", "job_url": "https://co.com/2", "description": "Desktop support", "min_experience": 1.0},
        ]
        with open(self.base_dir / "indeed_jobs.json", "w", encoding="utf-8") as f:
            json.dump(jobs, f)

        orchestrator.run_orchestration()

        with open(self.base_dir / "daily_run_summary.json", encoding="utf-8") as f:
            summary = json.load(f)

        ps = summary["pipeline_summary"]
        total_unique = ps["total_canonical_unique"]
        category_sum = (
            ps["matched_jobs_count"]
            + ps["supporting_it_count"]
            + ps["internships_count"]
            + ps["manual_review_count"]
            + ps["excluded_count"]
        )
        self.assertEqual(total_unique, category_sum)
        self.assertEqual(total_unique, ps["deduplicated_master_count"])


if __name__ == "__main__":
    unittest.main()

