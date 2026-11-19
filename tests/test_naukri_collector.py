"""
Unit tests for Naukri collector error categorization, search plan, and resumption.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from naukri_collector import ErrorCategory, NaukriCollector, parse_error_details
from naukri_config import TARGET_CITIES, TARGET_ROLES, build_search_plan


class TestNaukriCollector(unittest.TestCase):
    def test_separate_http_error_classifications(self):
        # 1. HTTP 406: Akamai reCAPTCHA challenge
        e406 = "response status code 406 (recaptcha required): the address is rate limited"
        code, cat = parse_error_details(e406)
        self.assertEqual(code, 406)
        self.assertEqual(cat, ErrorCategory.HTTP_406_RECAPTCHA_CHALLENGE)

        # 2. HTTP 429: Too Many Requests
        e429 = "response status code 429: Too Many Requests"
        code, cat = parse_error_details(e429)
        self.assertEqual(code, 429)
        self.assertEqual(cat, ErrorCategory.HTTP_429_RATE_LIMITED)

        # 3. HTTP 403: Forbidden / Access Blocked
        e403 = "response status code 403 (blocked: use a proxy)"
        code, cat = parse_error_details(e403)
        self.assertEqual(code, 403)
        self.assertEqual(cat, ErrorCategory.HTTP_403_ACCESS_BLOCKED)

        # 4. HTTP 500: Server Error
        e500 = "response status code 500: Internal Server Error"
        code, cat = parse_error_details(e500)
        self.assertEqual(code, 500)
        self.assertEqual(cat, ErrorCategory.HTTP_5XX_SERVER_ERROR)

        # 5. Network Error
        e_net = "HTTPSConnectionPool: Connection timed out"
        code, cat = parse_error_details(e_net)
        self.assertIsNone(code)
        self.assertEqual(cat, ErrorCategory.NETWORK_ERROR)

    def test_search_plan_generation_counts(self):
        plan = build_search_plan()
        expected_city_searches = len(TARGET_ROLES) * len(TARGET_CITIES)  # 16 * 9 = 144
        expected_remote_searches = len(TARGET_ROLES)  # 16
        expected_total = expected_city_searches + expected_remote_searches  # 160

        self.assertEqual(len(plan), expected_total)
        self.assertEqual(sum(1 for q in plan if not q["is_remote"]), 144)
        self.assertEqual(sum(1 for q in plan if q["is_remote"]), 16)

    def test_checkpoint_io_and_resumption(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ckpt_path = Path(tmpdir) / "test_naukri_checkpoint.jsonl"
            collector = NaukriCollector(checkpoint_path=ckpt_path)

            # Initially empty
            self.assertEqual(collector.load_checkpoint(), 0)

            # Append items
            items = [
                {"id": "nk-1", "title": "SOC Analyst", "test_keyword": "SOC Analyst", "test_location": "Bengaluru", "test_is_remote": False},
                {"id": "nk-2", "title": "Cyber Analyst", "test_keyword": "Cybersecurity Analyst", "test_location": "Hyderabad", "test_is_remote": False},
            ]
            collector.append_checkpoint(items)

            # Reload collector
            collector2 = NaukriCollector(checkpoint_path=ckpt_path)
            loaded = collector2.load_checkpoint()
            self.assertEqual(loaded, 2)
            self.assertIn(("SOC Analyst", "Bengaluru", False), collector2.completed_searches)
            self.assertIn(("Cybersecurity Analyst", "Hyderabad", False), collector2.completed_searches)


if __name__ == "__main__":
    unittest.main()
