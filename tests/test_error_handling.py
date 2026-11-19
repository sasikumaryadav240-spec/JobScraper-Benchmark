"""
Unit tests for error detection, rate limiting, and atomic file I/O in run_jobspy.py.
"""

import json
import tempfile
import unittest
from pathlib import Path
from run_jobspy import detect_linkedin_rate_limit, record_issue, write_json_atomic


class TestErrorHandling(unittest.TestCase):

    def test_detect_linkedin_rate_limit(self):
        # Explicit 429
        self.assertTrue(detect_linkedin_rate_limit("response status code 429 (blocked: use a proxy)", ""))
        # Case insensitive
        self.assertTrue(detect_linkedin_rate_limit("", "TOO MANY REQUESTS from server"))
        # Rate limit phrase
        self.assertTrue(detect_linkedin_rate_limit("rate limit exceeded", ""))
        # Normal message
        self.assertFalse(detect_linkedin_rate_limit("no job data returned", "empty results"))
        self.assertFalse(detect_linkedin_rate_limit("", ""))

    def test_record_issue_structure(self):
        errors = []
        search = {
            "keyword": "SOC Analyst",
            "location": "Bengaluru",
            "search_type": "city",
        }
        record_issue(
            run_errors=errors,
            run_id="run_123",
            issue_type="scraper_error",
            search=search,
            details="HTTP 403 Forbidden",
            site="google",
        )
        self.assertEqual(len(errors), 1)
        entry = errors[0]
        self.assertEqual(entry["run_id"], "run_123")
        self.assertEqual(entry["site"], "google")
        self.assertEqual(entry["keyword"], "SOC Analyst")
        self.assertEqual(entry["location"], "Bengaluru")
        self.assertEqual(entry["type"], "scraper_error")
        self.assertEqual(entry["details"], "HTTP 403 Forbidden")
        self.assertTrue("timestamp_utc" in entry)

    def test_write_json_atomic(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_path = Path(tmp_dir) / "output.json"
            data = {"run_id": "test_atomic", "count": 42}

            write_json_atomic(target_path, data)
            self.assertTrue(target_path.exists())

            with open(target_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(loaded, data)

            # Ensure no leftover temporary file
            temp_path = target_path.with_suffix(target_path.suffix + ".tmp")
            self.assertFalse(temp_path.exists())


if __name__ == "__main__":
    unittest.main()

