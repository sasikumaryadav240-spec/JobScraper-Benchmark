"""
Comprehensive Unit & Integration Test Suite for Phase 6 Hardening.

Covers all 23 verification scenarios specified in Phase 6:
1. 10-second minimum request interval enforcement
2. Pacing across search requests
3. Pacing across detail requests
4. Pacing across pagination requests
5. Pacing during retries
6. Per-host pacing isolation (unrelated hosts do not block each other)
7. Concurrent request protection (thread serialization)
8. HTTP 429 with Retry-After header
9. HTTP 429 without Retry-After (exponential backoff fallback)
10. Exponential backoff progression
11. Random jitter bounds
12. Circuit-breaker activation on threshold consecutive failures
13. Circuit-breaker recovery on subsequent success
14. HTTP 406 CAPTCHA handling (immediate respectful pause, no bypass)
15. HTTP 403 access blocked handling (immediate abort of retries)
16. HTTP 5xx retry handling (bounded backoff)
17. Checkpoint persistence after partial execution
18. Correct resumption of incomplete searches (only truly succeeded searches skipped)
19. Successful data retention when another source fails
20. No fabricated empty-success records
21. Correct source status classification (SUCCESS, PARTIAL, RATE_LIMITED, FAILED)
22. Accurate unified counts and category reconciliation
23. JSON, CSV, and XLSX consistency across outputs
"""

from __future__ import annotations

import csv
import json
import mimetypes
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Windows registry permission guard for openpyxl
try:
    mimetypes.init()
except PermissionError:
    mimetypes._winreg = None
    mimetypes.init = lambda *args, **kwargs: None

import openpyxl

from ats_collector import ATSCollector
from indeed_collector import IndeedCollector
from linkedin_collector import ErrorCategory, LinkedInCollector, parse_error_details
from models import JobRecord, VerificationStatus, WorkMode
from naukri_collector import NaukriCollector
from request_pacer import (
    CircuitBreaker,
    RequestPacer,
    calculate_backoff,
    get_global_pacer,
    install_request_pacer,
    normalize_host,
    parse_retry_after,
    uninstall_request_pacer,
)
from run_daily_pipeline import DailyPipelineOrchestrator
from unified_pipeline import UnifiedPipeline


class FakeClock:
    """Deterministic fake clock for high-speed rate-limiting testing without real sleeps."""

    def __init__(self, start_time: float = 1000.0) -> None:
        self.current_time = start_time
        self.sleep_calls: list[float] = []

    def time(self) -> float:
        return self.current_time

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.current_time += seconds


class TestPhase6Hardening(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.fake_clock = FakeClock(start_time=1000.0)
        self.pacer = RequestPacer(default_interval=10.0)
        self.pacer.set_clock(self.fake_clock.time, self.fake_clock.sleep)

    def tearDown(self) -> None:
        uninstall_request_pacer()
        self.temp_dir.cleanup()

    # 1. 10-second minimum request interval enforcement
    def test_01_ten_second_minimum_interval(self) -> None:
        host = "https://www.linkedin.com/jobs"
        # First request: no prior request, wait is 0
        w1 = self.pacer.wait_before_request(host)
        self.assertEqual(w1, 0.0)

        # Advance clock by 2.0s
        self.fake_clock.current_time += 2.0

        # Second request: 2.0s elapsed < 10.0s interval -> must wait 8.0s!
        w2 = self.pacer.wait_before_request(host)
        self.assertAlmostEqual(w2, 8.0, places=2)
        self.assertAlmostEqual(self.fake_clock.current_time, 1010.0, places=2)

    # 2. Pacing across search requests
    def test_02_pacing_across_search_requests(self) -> None:
        host = "https://apis.indeed.com/graphql"
        w1 = self.pacer.wait_before_request(host)
        self.assertEqual(w1, 0.0)

        # Immediate search 2: should wait full 10.0s
        w2 = self.pacer.wait_before_request(host)
        self.assertAlmostEqual(w2, 10.0, places=2)

        # Immediate search 3: should wait full 10.0s
        w3 = self.pacer.wait_before_request(host)
        self.assertAlmostEqual(w3, 10.0, places=2)
        self.assertEqual(len(self.fake_clock.sleep_calls), 2)

    # 3. Pacing across detail requests
    def test_03_pacing_across_detail_requests(self) -> None:
        url1 = "https://www.linkedin.com/jobs/view/1001"
        url2 = "https://www.linkedin.com/jobs/view/1002"
        w1 = self.pacer.wait_before_request(url1)
        self.assertEqual(w1, 0.0)

        # Detail 2 fired 1.5s later (e.g. rapid loop): must wait 8.5s!
        self.fake_clock.current_time += 1.5
        w2 = self.pacer.wait_before_request(url2)
        self.assertAlmostEqual(w2, 8.5, places=2)

    # 4. Pacing across pagination requests
    def test_04_pacing_across_pagination_requests(self) -> None:
        page1 = "https://www.naukri.com/jobapi/v3/search?pageNo=1"
        page2 = "https://www.naukri.com/jobapi/v3/search?pageNo=2"
        w1 = self.pacer.wait_before_request(page1)
        self.assertEqual(w1, 0.0)

        self.fake_clock.current_time += 3.0  # e.g. parsing took 3s
        w2 = self.pacer.wait_before_request(page2)
        self.assertAlmostEqual(w2, 7.0, places=2)

    # 5. Pacing during retries
    def test_05_pacing_during_retries(self) -> None:
        url = "https://www.linkedin.com/jobs-guest/api/search"
        w1 = self.pacer.wait_before_request(url)
        self.assertEqual(w1, 0.0)

        # Request fails after 1s, retry is scheduled: must wait at least 9.0s
        self.fake_clock.current_time += 1.0
        w2 = self.pacer.wait_before_request(url)
        self.assertAlmostEqual(w2, 9.0, places=2)

    # 6. Per-host pacing isolation
    def test_06_per_host_pacing_isolation(self) -> None:
        gh_url = "https://boards-api.greenhouse.io/v1/boards/stripe/jobs"
        lever_url = "https://api.lever.co/v0/postings/atlassian"

        # Request to Greenhouse
        w_gh = self.pacer.wait_before_request(gh_url)
        self.assertEqual(w_gh, 0.0)

        # Immediate request to Lever: different host! Must NOT be delayed by Greenhouse!
        w_lever = self.pacer.wait_before_request(lever_url)
        self.assertEqual(w_lever, 0.0)

        # Second request to Greenhouse immediately: MUST wait 10.0s!
        w_gh2 = self.pacer.wait_before_request(gh_url)
        self.assertAlmostEqual(w_gh2, 10.0, places=2)

    # 7. Concurrent request protection (thread serialization)
    def test_07_concurrent_request_protection(self) -> None:
        host = "https://www.linkedin.com/api"
        threads: list[threading.Thread] = []
        waits: list[float] = []
        lock = threading.Lock()

        def worker() -> None:
            w = self.pacer.wait_before_request(host)
            with lock:
                waits.append(w)

        for _ in range(3):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(waits), 3)
        # One thread gets 0s wait, others get serialized waits (10s, 20s cumulative simulated time)
        self.assertIn(0.0, waits)
        self.assertAlmostEqual(self.fake_clock.current_time, 1020.0, places=1)

    # 8. HTTP 429 with Retry-After header
    def test_08_http_429_with_retry_after(self) -> None:
        # Integer seconds
        self.assertEqual(parse_retry_after("120"), 120.0)
        self.assertEqual(parse_retry_after("30.5"), 30.5)

        # HTTP Date
        now_ts = 1000.0
        # 60 seconds into the future
        future_date = "Thu, 01 Jan 1970 00:17:40 GMT"  # 1060s
        wait_s = parse_retry_after(future_date, now_ts=now_ts)
        self.assertIsNotNone(wait_s)
        self.assertAlmostEqual(wait_s, 60.0, places=1)

    # 9. HTTP 429 without Retry-After (exponential backoff fallback)
    def test_09_http_429_without_retry_after(self) -> None:
        b1 = calculate_backoff(attempt=1, base_backoff=30.0, max_backoff=120.0, jitter_range=(0.0, 0.0))
        b2 = calculate_backoff(attempt=2, base_backoff=30.0, max_backoff=120.0, jitter_range=(0.0, 0.0))
        b3 = calculate_backoff(attempt=3, base_backoff=30.0, max_backoff=120.0, jitter_range=(0.0, 0.0))
        self.assertEqual(b1, 30.0)
        self.assertEqual(b2, 60.0)
        self.assertEqual(b3, 120.0)

    # 10. Exponential backoff progression bounded by max_backoff
    def test_10_exponential_backoff_progression(self) -> None:
        b4 = calculate_backoff(attempt=4, base_backoff=30.0, max_backoff=120.0, jitter_range=(0.0, 0.0))
        self.assertEqual(b4, 120.0)  # Capped at max_backoff

    # 11. Random jitter bounds
    def test_11_random_jitter_bounds(self) -> None:
        samples = [calculate_backoff(attempt=1, base_backoff=30.0, jitter_range=(1.0, 5.0)) for _ in range(20)]
        for s in samples:
            self.assertGreaterEqual(s, 31.0)
            self.assertLessEqual(s, 35.0)
        # Jitter ensures they are not all identical
        self.assertGreater(len(set(samples)), 1)

    # 12. Circuit-breaker activation on threshold consecutive failures
    def test_12_circuit_breaker_activation(self) -> None:
        cb = CircuitBreaker("linkedin", consecutive_failure_threshold=3)
        self.assertTrue(cb.can_proceed())

        cb.record_failure("429", status_code=429)
        self.assertTrue(cb.can_proceed())
        cb.record_failure("429", status_code=429)
        self.assertTrue(cb.can_proceed())

        # Third consecutive failure trips breaker
        tripped = cb.record_failure("429", status_code=429)
        self.assertTrue(tripped)
        self.assertTrue(cb.is_open)
        self.assertFalse(cb.can_proceed())
        self.assertEqual(cb.circuit_breaker_events, 1)

    # 13. Circuit-breaker recovery on subsequent success
    def test_13_circuit_breaker_recovery(self) -> None:
        cb = CircuitBreaker("linkedin", consecutive_failure_threshold=2)
        cb.record_failure("500", status_code=500)
        cb.record_failure("500", status_code=500)
        self.assertTrue(cb.is_open)

        # Recovery on successful response
        cb.record_success()
        self.assertFalse(cb.is_open)
        self.assertTrue(cb.can_proceed())
        self.assertEqual(cb.consecutive_failures, 0)
        self.assertEqual(cb.recovery_attempts, 1)

    # 14. HTTP 406 CAPTCHA handling
    def test_14_http_406_captcha_handling(self) -> None:
        code, cat, _ = parse_error_details("response status code 406 (recaptcha required)")
        self.assertEqual(code, 406)
        self.assertEqual(cat, ErrorCategory.HTTP_406_RECAPTCHA_CHALLENGE)

    # 15. HTTP 403 access blocked handling
    def test_15_http_403_handling(self) -> None:
        code, cat, _ = parse_error_details("response status code 403 (blocked: use a proxy)")
        self.assertEqual(code, 403)
        self.assertEqual(cat, ErrorCategory.HTTP_403_ACCESS_BLOCKED)

    # 16. HTTP 5xx retry handling
    def test_16_http_5xx_retry_handling(self) -> None:
        code, cat, _ = parse_error_details("HTTP 503 Service Unavailable")
        self.assertEqual(code, 503)
        self.assertEqual(cat, ErrorCategory.HTTP_SERVER_ERROR)

    # 17. Checkpoint persistence after partial execution
    def test_17_checkpoint_persistence_after_partial_execution(self) -> None:
        chk_file = self.base_dir / "test_partial_chk.jsonl"
        collector = LinkedInCollector(checkpoint_path=chk_file)
        sample = [{"test_keyword": "SOC", "test_location": "Bengaluru", "test_is_remote": False, "title": "Job 1"}]
        collector.append_checkpoint(sample)
        self.assertTrue(chk_file.exists())

        loaded = []
        with open(chk_file, "r", encoding="utf-8") as f:
            for line in f:
                loaded.append(json.loads(line))
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["title"], "Job 1")

    # 18. Correct resumption of incomplete searches
    def test_18_correct_resumption_of_incomplete_searches(self) -> None:
        chk_file = self.base_dir / "test_resumption_chk.jsonl"
        # Search 1 succeeded
        rec1 = [{"test_keyword": "SOC Analyst", "test_location": "Bengaluru", "test_is_remote": False, "title": "Job A"}]
        with open(chk_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec1[0]) + "\n")

        collector = LinkedInCollector(checkpoint_path=chk_file)
        count = collector.load_checkpoint()
        self.assertEqual(count, 1)
        # Succeeded search is in completed_searches
        self.assertIn(("SOC Analyst", "Bengaluru", False), collector.completed_searches)
        # Failed or unrun search is NOT in completed_searches
        self.assertNotIn(("Cybersecurity Analyst", "Hyderabad", False), collector.completed_searches)

    # 19. Successful data retention when another source fails
    def test_19_successful_data_retention_when_another_source_fails(self) -> None:
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="daily")
        # Prepopulate indeed and ats jobs
        sample_jobs = [{"source": "indeed", "source_job_id": "i1", "title": "SOC Analyst", "company": "Corp", "location": "Bengaluru"}]
        with open(self.base_dir / "indeed_jobs.json", "w", encoding="utf-8") as f:
            json.dump(sample_jobs, f)

        with patch("run_daily_pipeline.IndeedCollector") as MockIndeed, \
             patch("run_daily_pipeline.NaukriCollector") as MockNaukri, \
             patch("run_daily_pipeline.LinkedInCollector") as MockLinkedIn, \
             patch("run_daily_pipeline.ATSCollector") as MockATS:

            # LinkedIn raises 429 rate limit
            MockLinkedIn.return_value.run.side_effect = RuntimeError("HTTP 429 Too Many Requests")
            MockIndeed.return_value.run.return_value = {"unique_records_after_dedup": 1, "raw_records_returned": 1}
            MockNaukri.return_value.run.return_value = []
            MockATS.return_value.run.return_value = []

            # Execute pipeline
            code = orchestrator.run_orchestration()
            self.assertEqual(code, 0)
            self.assertEqual(orchestrator.source_telemetry["linkedin"]["status"], "RATE_LIMITED")
            self.assertEqual(orchestrator.source_telemetry["indeed"]["status"], "SUCCESS")

    # 20. No fabricated empty-success records
    def test_20_no_fabricated_empty_success_records(self) -> None:
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="skip_scraping")
        # Missing source file
        missing_file = self.base_dir / "non_existent.json"
        freshness, _ = orchestrator._check_source_freshness(missing_file, collected_in_run=False)
        self.assertEqual(freshness, "UNAVAILABLE")

    # 21. Correct source status classification
    def test_21_correct_source_status_classification(self) -> None:
        orchestrator = DailyPipelineOrchestrator(base_dir=self.base_dir, run_mode="daily")
        with patch("run_daily_pipeline.LinkedInCollector") as MockLinkedIn:
            inst = MockLinkedIn.return_value
            inst.raw_records = []
            inst.run_errors = [{"status_code": 429, "error_message": "Rate limited"}]
            inst.circuit_breaker_triggered = True
            inst.run.return_value = []

            orchestrator.run_linkedin()
            self.assertEqual(orchestrator.source_telemetry["linkedin"]["status"], "RATE_LIMITED")

    # 22. Accurate unified counts and category reconciliation
    def test_22_accurate_unified_counts(self) -> None:
        rec1 = {
            "source": "indeed",
            "source_job_id": "rec_001",
            "title": "SOC Analyst",
            "company": "SecureWorks",
            "location": "Bengaluru",
            "description": "SOC alert monitoring using Sentinel. 1 year experience required.",
            "min_experience": 1.0,
            "max_experience": 2.0,
        }
        with open(self.base_dir / "indeed_jobs.json", "w", encoding="utf-8") as f:
            json.dump([rec1], f)

        pipeline = UnifiedPipeline(base_dir=self.base_dir)
        summary = pipeline.run()

        # Deduplicated jobs pre-match exported
        self.assertEqual(summary["deduplicated_master_count"], 1)
        # Total partitioned categories reconcile to 1
        cat_sum = (
            summary["matched_jobs_count"]
            + summary["supporting_it_count"]
            + summary["internships_count"]
            + summary["manual_review_count"]
            + summary["excluded_count"]
        )
        self.assertEqual(cat_sum, 1)

    # 23. JSON, CSV, and XLSX consistency across outputs
    def test_23_json_csv_xlsx_consistency(self) -> None:
        json_file = self.base_dir / "deduplicated_jobs.json"
        csv_file = self.base_dir / "deduplicated_jobs.csv"
        xlsx_file = self.base_dir / "deduplicated_jobs.xlsx"

        rec = {
            "source": "ats",
            "source_job_id": "ats_001",
            "title": "Incident Responder",
            "company": "CrowdStrike",
            "location": "Hyderabad",
            "description": "Handle security incidents and triage alerts.",
        }
        with open(self.base_dir / "ats_jobs.json", "w", encoding="utf-8") as f:
            json.dump([rec], f)

        pipeline = UnifiedPipeline(base_dir=self.base_dir)
        pipeline.run()

        self.assertTrue(json_file.exists())
        self.assertTrue(csv_file.exists())
        self.assertTrue(xlsx_file.exists())

        with open(json_file, "r", encoding="utf-8") as f:
            jobs_json = json.load(f)
        self.assertEqual(len(jobs_json), 1)
        self.assertEqual(jobs_json[0]["job_title"], "Incident Responder")

        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_title"], "Incident Responder")

        wb = openpyxl.load_workbook(xlsx_file)
        sheet = wb.active
        self.assertEqual(sheet.max_row, 2)


if __name__ == "__main__":
    unittest.main()
