"""
Unit tests for indeed_collector.py module.
"""

import tempfile
import unittest
from pathlib import Path
from indeed_collector import (
    IndeedCollector,
    append_indeed_checkpoint,
    categorize_error,
    load_indeed_checkpoint,
)
import indeed_config as config


class TestIndeedCollector(unittest.TestCase):

    def test_search_plan_generation(self):
        roles = ["SOC Analyst", "SIEM Analyst"]
        locations = ["Bengaluru", "Hyderabad"]
        searches = config.generate_search_plan(roles=roles, locations=locations, include_remote=True)
        # 2 roles x 2 locations + 2 remote = 6 searches
        self.assertEqual(len(searches), 6)
        city_searches = [s for s in searches if s["search_type"] == "city"]
        remote_searches = [s for s in searches if s["search_type"] == "remote_india"]
        self.assertEqual(len(city_searches), 4)
        self.assertEqual(len(remote_searches), 2)
        self.assertTrue(all(s["is_remote"] for s in remote_searches))
        self.assertFalse(any(s["is_remote"] for s in city_searches))

    def test_categorize_error(self):
        self.assertEqual(categorize_error("HTTP 429 Too Many Requests"), "rate_limited")
        self.assertEqual(categorize_error("response status code 403 (blocked: use a proxy)"), "blocked_or_access_denied")
        self.assertEqual(categorize_error("Connection timed out after 30 seconds"), "network_transient")
        self.assertEqual(categorize_error("JSONDecodeError: Expecting value"), "api_or_parse_error")

    def test_checkpoint_io(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            chk_path = Path(tmp_dir) / "indeed_test.jsonl"
            records = [
                {
                    "title": "SOC Analyst L1",
                    "company": "TCS",
                    "test_keyword": "SOC Analyst",
                    "test_location": "Bengaluru",
                    "test_is_remote": False,
                },
                {
                    "title": "Cloud Security",
                    "company": "Infosys",
                    "test_keyword": "Cloud Security",
                    "test_location": "India",
                    "test_is_remote": True,
                },
            ]
            append_indeed_checkpoint(records, chk_path)
            loaded, completed = load_indeed_checkpoint(chk_path)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(len(completed), 2)
            self.assertIn(("SOC Analyst", "Bengaluru", False), completed)
            self.assertIn(("Cloud Security", "India", True), completed)

    def test_transform_record_preserves_provenance(self):
        collector = IndeedCollector()
        raw = {
            "id": "in-587eb9fc6b17e8d9",
            "title": "L1/L2 SOC analyst",
            "company": "CGI",
            "location": "Bengaluru, India",
            "job_url": "https://in.indeed.com/viewjob?jk=587eb9fc6b17e8d9",
            "job_url_direct": "https://www.cgi.com/apply",
            "description": "Requirements: Minimum of 2 years of operational security experience.",
            "date_posted": "2026-11-18",
            "job_type": "fulltime",
            "min_amount": 600000.0,
            "currency": "INR",
            "interval": "YEARLY",
            "is_remote": False,
            "test_keyword": "SOC Analyst",
            "test_location": "Bengaluru",
            "test_search_type": "city",
        }
        res = collector.transform_record(raw, "test_run_1")
        self.assertEqual(res["source"], "indeed")
        self.assertEqual(res["indeed_job_key"], "587eb9fc6b17e8d9")
        self.assertEqual(res["min_experience"], 2.0)
        self.assertEqual(res["work_mode"], "onsite")
        self.assertEqual(res["salary_min"], 600000.0)
        self.assertEqual(res["salary_currency"], "INR")
        self.assertEqual(res["application_url"], "https://www.cgi.com/apply")
        self.assertEqual(res["field_origins"]["min_experience"], "extracted_from_description")
        self.assertEqual(res["field_origins"]["title"], "source_provided")


if __name__ == "__main__":
    unittest.main()

