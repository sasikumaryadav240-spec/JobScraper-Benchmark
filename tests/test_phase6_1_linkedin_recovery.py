"""
Tests for Phase 6.1 — LinkedIn Recovery, Rate Limiting, 500/429 Distinction & Checkpoint Resumption.

Verifies:
1. Distinct counting and handling of HTTP 500 (Server Error) vs HTTP 429 (Rate Limited).
2. Wire-level Retry-After header extraction and enforcement in RequestPacer.
3. Retention of existing checkpointed records (e.g. 30 records) without loss on failure.
4. Resumption of incomplete searches without repeating already completed searches.
5. Deduplication and merging of fresh records with checkpointed records (no duplicates).
6. Accurate raw record counting (no attribution of historical records to current run).
7. Circuit-breaker trip does not falsely mark unexecuted searches as completed.
8. HTTP 403 halts retries immediately without futile retry loops.
9. Orchestrator telemetry report formatting with distinct 5xx and 429 counts.
"""

from __future__ import annotations

import json
import mimetypes
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

from linkedin_collector import ErrorCategory, LinkedInCollector, parse_error_details
from request_pacer import (
    CircuitBreaker,
    RequestPacer,
    get_global_pacer,
    install_request_pacer,
    uninstall_request_pacer,
)
from run_daily_pipeline import DailyPipelineOrchestrator


class FakeClock:
    def __init__(self, start_time: float = 1000.0) -> None:
        self.current_time = start_time
        self.sleep_calls: list[float] = []

    def time(self) -> float:
        return self.current_time

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.current_time += seconds


class TestPhase61LinkedInRecovery(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.fake_clock = FakeClock(start_time=1000.0)
        self.pacer = RequestPacer(default_interval=10.0)
        self.pacer.set_clock(self.fake_clock.time, self.fake_clock.sleep)
        self.sleep_patcher = patch("linkedin_collector.time.sleep", side_effect=self.fake_clock.sleep)
        self.sleep_patcher.start()

    def tearDown(self) -> None:
        self.sleep_patcher.stop()
        uninstall_request_pacer()
        self.temp_dir.cleanup()

    def test_01_distinct_500_vs_429_circuit_breaker(self) -> None:
        """Verify that HTTP 500 and HTTP 429 are tracked separately and not conflated."""
        cb = CircuitBreaker("linkedin", consecutive_failure_threshold=5)

        # Record HTTP 500
        cb.record_failure(category=ErrorCategory.HTTP_SERVER_ERROR.value, status_code=500)
        self.assertEqual(cb.http_5xx_count, 1)
        self.assertEqual(cb.http_429_count, 0)
        self.assertEqual(cb.consecutive_failures, 1)

        # Record another HTTP 502
        cb.record_failure(category=ErrorCategory.HTTP_SERVER_ERROR.value, status_code=502)
        self.assertEqual(cb.http_5xx_count, 2)
        self.assertEqual(cb.http_429_count, 0)
        self.assertEqual(cb.consecutive_failures, 2)

        # Record HTTP 429
        cb.record_failure(category=ErrorCategory.HTTP_429_RATE_LIMITED.value, status_code=429)
        self.assertEqual(cb.http_5xx_count, 2)
        self.assertEqual(cb.http_429_count, 1)
        self.assertEqual(cb.consecutive_failures, 3)

        d = cb.to_dict()
        self.assertEqual(d["http_5xx_count"], 2)
        self.assertEqual(d["http_429_count"], 1)

    def test_02_wire_level_retry_after_interception(self) -> None:
        """Verify that RequestPacer records Retry-After from responses and enforces it."""
        pacer = RequestPacer(default_interval=10.0)
        pacer.set_clock(self.fake_clock.time, self.fake_clock.sleep)

        # Record Retry-After of 45 seconds for linkedin.com
        test_url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        pacer.record_retry_after(test_url, 45.0)

        # Check retrieval
        val = pacer.get_retry_after(test_url)
        self.assertEqual(val, 45.0)

        # Request pacing wait_before_request should enforce the 45s wait instead of just 10s
        waited = pacer.wait_before_request(test_url)
        self.assertAlmostEqual(waited, 45.0, places=1)

        # Advance clock by 10s so the standard interval has elapsed
        self.fake_clock.sleep(10.0)
        waited2 = pacer.wait_before_request(test_url)
        self.assertEqual(waited2, 0.0)

    def test_03_checkpoint_retention_and_resumption(self) -> None:
        """Verify that 30 existing records from a prior run are retained, completed searches skipped,

        and new records properly merged without duplicates.
        """
        output_dir = self.base_dir / "linkedin_out"
        output_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = output_dir / "linkedin_checkpoint.jsonl"
        jobs_json_path = output_dir / "linkedin_jobs.json"
        jobs_csv_path = output_dir / "linkedin_jobs.csv"
        errors_path = output_dir / "linkedin_errors.json"
        quality_path = output_dir / "linkedin_quality.json"
        summary_path = output_dir / "linkedin_summary.json"

        # Pre-populate checkpoint with 2 completed searches and 30 records (15 each)
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            for i in range(15):
                f.write(json.dumps({
                    "id": f"li_rec_{i:03d}",
                    "title": f"Security Analyst {i}",
                    "company": f"Corp {i}",
                    "location": "Bengaluru, Karnataka, India",
                    "job_url": f"https://www.linkedin.com/jobs/view/{1000+i}",
                    "description": f"Security Analyst description {i}",
                    "test_keyword": "Security Analyst",
                    "test_location": "Bengaluru",
                    "test_is_remote": False,
                }) + "\n")
            for i in range(15, 30):
                f.write(json.dumps({
                    "id": f"li_rec_{i:03d}",
                    "title": f"Security Analyst {i}",
                    "company": f"Corp {i}",
                    "location": "Hyderabad, Telangana, India",
                    "job_url": f"https://www.linkedin.com/jobs/view/{1000+i}",
                    "description": f"Security Analyst description {i}",
                    "test_keyword": "Security Analyst",
                    "test_location": "Hyderabad",
                    "test_is_remote": False,
                }) + "\n")

        # Initialize collector on same directory
        collector = LinkedInCollector(
            checkpoint_path=checkpoint_path,
            jobs_json_path=jobs_json_path,
            jobs_csv_path=jobs_csv_path,
            errors_path=errors_path,
            quality_path=quality_path,
            summary_path=summary_path,
            delays=(0.0, 0.0),
        )
        collector.pacer.set_clock(self.fake_clock.time, self.fake_clock.sleep)

        # Define 3 planned searches: 2 already in checkpoint, 1 new
        plan = [
            {"role": "Security Analyst", "location": "Bengaluru", "is_remote": False},
            {"role": "Security Analyst", "location": "Hyderabad", "is_remote": False},
            {"role": "SOC Analyst", "location": "Remote", "is_remote": True},
        ]

        # Mock search execution for the 3rd search to return 10 records (2 duplicate IDs, 8 new)
        def mock_search_jobs(**kwargs):
            import pandas as pd
            records = []
            # 2 duplicates of existing
            records.append({
                "id": "li_rec_000",
                "title": "Security Analyst 0",
                "company": "Corp 0",
                "location": "Remote",
                "job_url": "https://www.linkedin.com/jobs/view/1000",
                "description": "Already exists in Bengaluru",
            })
            records.append({
                "id": "li_rec_001",
                "title": "Security Analyst 1",
                "company": "Corp 1",
                "location": "Remote",
                "job_url": "https://www.linkedin.com/jobs/view/1001",
                "description": "Already exists in Bengaluru",
            })
            # 8 new unique
            for i in range(30, 38):
                records.append({
                    "id": f"li_rec_{i:03d}",
                    "title": f"SOC Analyst {i}",
                    "company": f"Corp {i}",
                    "location": "Remote",
                    "job_url": f"https://www.linkedin.com/jobs/view/{1000+i}",
                    "description": f"New remote SOC job {i}",
                })
            return pd.DataFrame(records)

        with patch("linkedin_collector.scrape_jobs", side_effect=mock_search_jobs):
            unique_records = collector.run(plan=plan)

        # 1. Checkpoint resumption verification
        self.assertEqual(len(collector.completed_searches), 3)
        self.assertIn(("Security Analyst", "Bengaluru", False), collector.completed_searches)
        self.assertIn(("Security Analyst", "Hyderabad", False), collector.completed_searches)
        self.assertIn(("SOC Analyst", "Remote", True), collector.completed_searches)

        # 2. Raw records collected in CURRENT RUN must be 10, NOT 40
        self.assertEqual(collector.current_run_raw_count, 10)

        # 3. Total unique records preserved: 30 pre-existing + 8 new = 38 unique
        self.assertEqual(len(unique_records), 38)

        # 4. Check summary json output
        with open(collector.summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)
        self.assertEqual(summary["raw_records_collected"], 10)
        self.assertEqual(summary["raw_records_from_checkpoint"], 30)
        self.assertEqual(summary["raw_records_total"], 40)
        self.assertEqual(summary["unique_records_preserved"], 38)
        self.assertEqual(summary["searches_resumed"], 2)

    def test_04_circuit_breaker_trip_does_not_mark_unexecuted_as_completed(self) -> None:
        """Verify that when circuit breaker trips, unexecuted or failed searches are NOT marked completed."""
        output_dir = self.base_dir / "linkedin_cb_test"
        output_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = output_dir / "linkedin_checkpoint.jsonl"
        jobs_json_path = output_dir / "linkedin_jobs.json"
        jobs_csv_path = output_dir / "linkedin_jobs.csv"
        errors_path = output_dir / "linkedin_errors.json"
        quality_path = output_dir / "linkedin_quality.json"
        summary_path = output_dir / "linkedin_summary.json"

        collector = LinkedInCollector(
            checkpoint_path=checkpoint_path,
            jobs_json_path=jobs_json_path,
            jobs_csv_path=jobs_csv_path,
            errors_path=errors_path,
            quality_path=quality_path,
            summary_path=summary_path,
            delays=(0.0, 0.0),
        )
        collector.circuit_breaker.threshold = 1  # Trip after 1 failure
        collector.pacer.set_clock(self.fake_clock.time, self.fake_clock.sleep)

        plan = [
            {"role": "Role 1", "location": "Bengaluru", "is_remote": False},
            {"role": "Role 2", "location": "Bengaluru", "is_remote": False},
            {"role": "Role 3", "location": "Bengaluru", "is_remote": False},
        ]

        call_idx = 0

        def mock_scrape(**kwargs):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                import pandas as pd
                return pd.DataFrame([{
                    "id": "job_01",
                    "title": "Role 1 Job",
                    "company": "Company A",
                    "location": "Bengaluru",
                    "job_url": "https://www.linkedin.com/jobs/view/1000",
                    "description": "Desc 1",
                }])
            elif call_idx == 2:
                raise Exception("HTTP 500: Internal Server Error")
            else:
                raise Exception("HTTP 429: Too Many Requests")

        with patch("linkedin_collector.scrape_jobs", side_effect=mock_scrape):
            unique_records = collector.run(plan=plan)

        # Only Search 1 should be marked completed
        self.assertEqual(len(collector.completed_searches), 1)
        self.assertIn(("Role 1", "Bengaluru", False), collector.completed_searches)
        self.assertNotIn(("Role 2", "Bengaluru", False), collector.completed_searches)
        self.assertNotIn(("Role 3", "Bengaluru", False), collector.completed_searches)

        # Verify circuit breaker triggered
        self.assertTrue(collector.circuit_breaker_triggered)

        # Verify unique record from search 1 was preserved (extracted source_job_id from url is 1000)
        self.assertEqual(len(unique_records), 1)
        self.assertEqual(unique_records[0].source_job_id, "1000")

        # Verify search stats recorded failures accurately
        stats = {f"{s['role']}|{s['location']}|remote={s['is_remote']}": s for s in collector.search_stats}
        self.assertEqual(stats["Role 1|Bengaluru|remote=False"]["status"], "SUCCESS")
        self.assertIn(stats["Role 2|Bengaluru|remote=False"]["status"], ("RATE_LIMITED", "SERVER_ERROR"))

    def test_05_http_403_halts_retries_immediately(self) -> None:
        """Verify that HTTP 403 halts retries immediately (no attempt 2 or 3)."""
        output_dir = self.base_dir / "linkedin_403_test"
        output_dir.mkdir(parents=True, exist_ok=True)
        collector = LinkedInCollector(
            checkpoint_path=output_dir / "checkpoint.jsonl",
            jobs_json_path=output_dir / "jobs.json",
            jobs_csv_path=output_dir / "jobs.csv",
            errors_path=output_dir / "errors.json",
            quality_path=output_dir / "quality.json",
            summary_path=output_dir / "summary.json",
            delays=(0.0, 0.0),
        )
        collector.pacer.set_clock(self.fake_clock.time, self.fake_clock.sleep)

        call_count = 0

        def mock_scrape_403(**kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception("HTTP 403: Forbidden - Access Denied")

        with patch("linkedin_collector.scrape_jobs", side_effect=mock_scrape_403):
            records = collector.execute_search_with_backoff(
                role="Security",
                location="Bengaluru",
                is_remote=False,
            )

        # Must have failed after exactly 1 call (no retries on 403)
        self.assertEqual(call_count, 1)
        self.assertEqual(records, [])
        self.assertEqual(collector.circuit_breaker.http_403_406_count, 1)

    def test_06_orchestrator_summary_report_with_5xx_telemetry(self) -> None:
        """Verify that the daily orchestrator builds report with 5xx column and distinct metrics."""
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir)

        summary = {
            "run_id": "test_run_123",
            "overall_run_status": "PARTIAL_EXECUTION",
            "total_duration_seconds": 325.0,
            "output_directory": str(self.base_dir),
            "source_status_breakdown": {
                "succeeded": ["indeed", "naukri", "ats"],
                "partial": ["linkedin"],
                "rate_limited": ["linkedin"],
                "failed": [],
                "cached_historical": [],
                "complete_collection_achieved": False,
            },
            "source_telemetry": {
                "indeed": {
                    "source": "indeed",
                    "status": "SUCCESS",
                    "freshness": "FRESH",
                    "planned": 18,
                    "completed": 18,
                    "failed": 0,
                    "skipped": 0,
                    "records_count": 73,
                    "raw_records_count": 125,
                    "http_429_count": 0,
                    "http_5xx_count": 0,
                    "http_403_406_count": 0,
                    "duration_seconds": 120.0,
                    "error": None,
                },
                "naukri": {
                    "source": "naukri",
                    "status": "SUCCESS",
                    "freshness": "FRESH",
                    "planned": 18,
                    "completed": 18,
                    "failed": 0,
                    "skipped": 0,
                    "records_count": 73,
                    "raw_records_count": 86,
                    "http_429_count": 0,
                    "http_5xx_count": 0,
                    "http_403_406_count": 0,
                    "duration_seconds": 110.0,
                    "error": None,
                },
                "linkedin": {
                    "source": "linkedin",
                    "status": "PARTIAL",
                    "freshness": "FRESH",
                    "planned": 9,
                    "completed": 2,
                    "failed": 7,
                    "skipped": 0,
                    "records_count": 30,
                    "raw_records_count": 30,
                    "http_429_count": 1,
                    "http_5xx_count": 1,
                    "http_403_406_count": 0,
                    "duration_seconds": 45.0,
                    "error": "Encountered HTTP 429 and HTTP 500",
                },
                "ats": {
                    "source": "ats",
                    "status": "SUCCESS",
                    "freshness": "FRESH",
                    "planned": 23,
                    "completed": 23,
                    "failed": 0,
                    "skipped": 0,
                    "records_count": 51,
                    "raw_records_count": 3067,
                    "http_429_count": 0,
                    "http_5xx_count": 0,
                    "http_403_406_count": 0,
                    "duration_seconds": 150.0,
                    "error": None,
                },
            },
            "unified_metrics": {
                "deduplicated_master_count": 226,
                "matched_jobs_count": 10,
                "supporting_it_count": 0,
                "internships_count": 2,
                "manual_review_count": 41,
                "excluded_count": 173,
                "source_breakdown": {"indeed": 73, "naukri": 73, "linkedin": 30, "ats": 50},
            },
        }

        report_md = orchestrator._build_markdown_report(summary)

        # Check table header contains 5xx
        self.assertIn("| Source | Status | Data Freshness | Planned | Completed | Failed | Skipped | Unique Records | Raw Records | 429s | 5xx | 403/406 | Duration | Details |", report_md)

        # Check LinkedIn row contains 30, 1 (for 429), 1 (for 5xx)
        self.assertIn("| **LINKEDIN** | ⚠️ PARTIAL | 🟢 FRESH | 9 | 2 | 7 | 0 | **30** | 30 | 1 | 1 | 0 | 45.0s | Encountered HTTP 429 and HTTP 500 |", report_md)

        # Check Source Integrity Breakdown classifies LinkedIn as Partial
        self.assertIn("Partial Sources (Retaining Usable Records)", report_md)
        self.assertIn("LINKEDIN", report_md)


if __name__ == "__main__":
    unittest.main()
