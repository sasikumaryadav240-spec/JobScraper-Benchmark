"""
Unit tests for LinkedIn collector, error classification, and stateful resumption.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from linkedin_collector import ErrorCategory, LinkedInCollector, parse_error_details
from linkedin_config import TARGET_CITIES, TARGET_ROLES, build_search_plan


class TestLinkedInCollector(unittest.TestCase):
    def test_parse_error_details(self):
        # HTTP 429
        code, cat, _ = parse_error_details("response status code 429 (Too Many Requests)")
        self.assertEqual(code, 429)
        self.assertEqual(cat, ErrorCategory.HTTP_429_RATE_LIMITED)

        # HTTP 403
        code, cat, _ = parse_error_details("response status code 403 (blocked: use a proxy)")
        self.assertEqual(code, 403)
        self.assertEqual(cat, ErrorCategory.HTTP_403_ACCESS_BLOCKED)

        # HTTP 406
        code, cat, _ = parse_error_details("response status code 406 (recaptcha required)")
        self.assertEqual(code, 406)
        self.assertEqual(cat, ErrorCategory.HTTP_406_RECAPTCHA_CHALLENGE)

        # Server error 502
        code, cat, _ = parse_error_details("HTTP 502 Bad Gateway")
        self.assertEqual(code, 502)
        self.assertEqual(cat, ErrorCategory.HTTP_SERVER_ERROR)

        # Network timeout
        code, cat, _ = parse_error_details("HTTPSConnectionPool: Read timed out")
        self.assertEqual(cat, ErrorCategory.NETWORK_OR_TIMEOUT)

    def test_search_plan_generation(self):
        plan = build_search_plan()
        expected_total = (len(TARGET_ROLES) * len(TARGET_CITIES)) + len(TARGET_ROLES)
        self.assertEqual(len(plan), expected_total)
        self.assertEqual(len(plan), 160)

        city_searches = [s for s in plan if not s["is_remote"]]
        remote_searches = [s for s in plan if s["is_remote"]]
        self.assertEqual(len(city_searches), 144)
        self.assertEqual(len(remote_searches), 16)
        for rs in remote_searches:
            self.assertEqual(rs["location"], "India")
            self.assertEqual(rs["search_type"], "remote_india")

    def test_checkpoint_io_and_resumption(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            chk_path = Path(tmpdir) / "test_linkedin_checkpoint.jsonl"
            collector = LinkedInCollector(checkpoint_path=chk_path)

            self.assertEqual(collector.load_checkpoint(), 0)

            # Append sample records
            sample_records = [
                {"test_keyword": "SOC Analyst", "test_location": "Bengaluru", "test_is_remote": False, "title": "Job 1"},
                {"test_keyword": "Cybersecurity Analyst", "test_location": "India", "test_is_remote": True, "title": "Job 2"},
            ]
            collector.append_checkpoint(sample_records)
            self.assertTrue(chk_path.exists())

            # Create fresh collector pointing to same checkpoint
            collector2 = LinkedInCollector(checkpoint_path=chk_path)
            count = collector2.load_checkpoint()
            self.assertEqual(count, 2)
            self.assertIn(("SOC Analyst", "Bengaluru", False), collector2.completed_searches)
            self.assertIn(("Cybersecurity Analyst", "India", True), collector2.completed_searches)


if __name__ == "__main__":
    unittest.main()

