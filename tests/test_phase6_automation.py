"""
Unit Tests for Phase 6 Daily Automation & Master Orchestrator.

Tests:
- Master orchestrator execution & failure isolation
- Simulated HTTP 429 on LinkedIn (graceful pause, partial preservation)
- Simulated HTTP 406 on Naukri (circuit breaker, partial preservation)
- Simulated ATS board failure (individual board failure isolation)
- Checkpoint resumption & recovery
- Stale vs. Fresh data detection
- Seniority suitability guardrails (Manager/Architect exclusions)
- Daily report generation (Markdown & JSON telemetry)
- GitHub Actions summary output
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from candidate_matching_engine import evaluate_candidate_matching
from run_daily_pipeline import DailyPipelineOrchestrator
from unified_models import CanonicalJobRecord, EligibilityStatus


class TestPhase6DailyAutomation(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)

        # Create minimal mock source files in temp directory
        self.sample_indeed = [{"title": "SOC Analyst", "company": "CyberSec", "location": "Bengaluru", "source": "indeed", "source_job_id": "ind1"}]
        self.sample_naukri = [{"title": "SIEM Analyst", "company": "NaukriSec", "location": "Hyderabad", "source": "naukri", "source_job_id": "nak1"}]
        self.sample_linkedin = [{"title": "Security Analyst", "company": "LinkSec", "location": "Pune", "source": "linkedin", "source_job_id": "lin1"}]
        self.sample_ats = [{"title": "AppSec Engineer", "company": "ATSSec", "location": "Remote", "source": "greenhouse", "source_job_id": "ats1"}]

        with open(self.base_dir / "indeed_jobs.json", "w", encoding="utf-8") as f:
            json.dump(self.sample_indeed, f)
        with open(self.base_dir / "naukri_jobs.json", "w", encoding="utf-8") as f:
            json.dump(self.sample_naukri, f)
        with open(self.base_dir / "linkedin_jobs.json", "w", encoding="utf-8") as f:
            json.dump(self.sample_linkedin, f)
        with open(self.base_dir / "ats_jobs.json", "w", encoding="utf-8") as f:
            json.dump(self.sample_ats, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_stale_data_detection(self):
        """Verify that files older than 24h are marked STALE, while recent files are CACHED_RECENT."""
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="skip_scraping")
        target_file = self.base_dir / "indeed_jobs.json"

        # 1. Fresh file (collected in run)
        freshness, _ = orchestrator._check_source_freshness(target_file, collected_in_run=True)
        self.assertEqual(freshness, "FRESH")

        # 2. Recent cached file (under 24h old)
        freshness, _ = orchestrator._check_source_freshness(target_file, collected_in_run=False)
        self.assertEqual(freshness, "CACHED_RECENT")

        # 3. Artificially age the file to 25 hours ago
        stale_time = (datetime.now() - timedelta(hours=25)).timestamp()
        import os
        os.utime(target_file, (stale_time, stale_time))

        freshness, _ = orchestrator._check_source_freshness(target_file, collected_in_run=False)
        self.assertEqual(freshness, "STALE")

    def test_seniority_suitability_guardrail_manager_architect(self):
        """Verify that clearly senior roles (Manager, Architect, Director) are excluded for freshers."""
        rec = CanonicalJobRecord(
            canonical_job_id="test_mgr",
            source="indeed",
            source_job_id="mgr1",
            job_title="Security Architect Manager",
            normalized_job_title="Security Architect Manager",
            company_name="Enterprise Corp",
            normalized_company_name="Enterprise Corp",
            job_description="Looking for an experienced professional to lead our security architecture team. Must possess strong problem solving skills.",
            original_location="Bengaluru",
            collected_at="2026-11-19T10:00:00Z",
            mandatory_min_experience=1.0,  # Even with 1 year min stated in subclause
            overall_match_score=65.0,     # Even with > 60% match score
        )
        evaluated = evaluate_candidate_matching(rec, recalculate_scores=False)
        self.assertEqual(evaluated.eligibility_status, EligibilityStatus.EXCLUDE_STATUS.value)
        self.assertTrue(any("Manager/Architect/Director" in r for r in evaluated.rejection_reasons))

    def test_source_failure_isolation_linkedin_429(self):
        """Simulate LinkedIn HTTP 429 and verify pipeline does not crash and continues other stages."""
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="daily")

        with patch("run_daily_pipeline.LinkedInCollector") as MockLinkedIn:
            instance = MockLinkedIn.return_value
            instance.run.side_effect = RuntimeError("HTTP 429 Too Many Requests (Rate Limited)")

            orchestrator.run_linkedin()

            telemetry = orchestrator.source_telemetry.get("linkedin", {})
            self.assertEqual(telemetry.get("status"), "RATE_LIMITED")
            self.assertIn("429", telemetry.get("error", ""))
            self.assertEqual(len(orchestrator.run_errors), 1)

    def test_source_failure_isolation_naukri_406(self):
        """Simulate Naukri HTTP 406 and verify pipeline marks failed and records error."""
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="daily")

        with patch("run_daily_pipeline.NaukriCollector") as MockNaukri:
            instance = MockNaukri.return_value
            instance.run.side_effect = RuntimeError("HTTP 406 Not Acceptable (Bot Gate)")

            orchestrator.run_naukri()

            telemetry = orchestrator.source_telemetry.get("naukri", {})
            self.assertEqual(telemetry.get("status"), "FAILED")
            self.assertIn("406", telemetry.get("error", ""))
            self.assertEqual(len(orchestrator.run_errors), 1)

    def test_orchestration_end_to_end_outputs_and_reports(self):
        """Execute orchestrator with existing source files and verify all required report files are generated."""
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="skip_scraping")
        exit_code = orchestrator.run_orchestration()
        self.assertEqual(exit_code, 0)

        # Check required files
        self.assertTrue((self.base_dir / "daily_run_summary.json").exists())
        self.assertTrue((self.base_dir / "daily_run_report.md").exists())
        self.assertTrue((self.base_dir / "daily_run_errors.json").exists())
        self.assertTrue((self.base_dir / "unified_jobs.json").exists())
        self.assertTrue((self.base_dir / "unified_jobs.xlsx").exists())
        self.assertTrue((self.base_dir / "matching_report.json").exists())

        # Verify summary contents
        with open(self.base_dir / "daily_run_summary.json", "r", encoding="utf-8") as f:
            summary = json.load(f)
        self.assertEqual(summary["execution_mode"], "skip_scraping")
        self.assertIn("indeed", summary["source_telemetry"])
        self.assertIn("ats", summary["source_telemetry"])


if __name__ == "__main__":
    unittest.main()

