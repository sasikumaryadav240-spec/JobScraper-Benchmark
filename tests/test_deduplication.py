"""
Unit tests for deduplication and provenance merging in run_jobspy.py.
"""

import unittest
from run_jobspy import merge_records


class TestDeduplication(unittest.TestCase):

    def test_merge_exact_duplicate_urls(self):
        rows = [
            {
                "site": "naukri",
                "title": "SOC Analyst",
                "company": "Infosys",
                "job_url": "https://www.naukri.com/job-101?utm_source=email",
                "test_keyword": "SOC Analyst",
                "test_location": "Bengaluru",
                "test_search_type": "city",
                "run_id": "run_1",
                "description": "Short description",
            },
            {
                "site": "naukri",
                "title": "SOC Analyst",
                "company": "Infosys",
                "job_url": "https://naukri.com/job-101",
                "test_keyword": "SIEM Analyst",
                "test_location": "Bengaluru",
                "test_search_type": "city",
                "run_id": "run_1",
                "description": "Longer and more comprehensive description",
            },
        ]
        deduped, raw_with_url, url_missing = merge_records(rows)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(raw_with_url, 2)
        self.assertEqual(url_missing, 0)

        merged = deduped[0]
        # Verify longer description kept
        self.assertEqual(merged["description"], "Longer and more comprehensive description")
        # Verify provenance accumulated
        self.assertEqual(sorted(merged["matched_keywords"]), ["SIEM Analyst", "SOC Analyst"])
        self.assertEqual(merged["matched_locations"], ["Bengaluru"])

    def test_retain_url_less_records(self):
        rows = [
            {
                "site": "indeed",
                "title": "Incident Responder",
                "company": "Company A",
                "job_url": None,
                "test_keyword": "SOC Analyst",
                "test_location": "Hyderabad",
            },
            {
                "site": "indeed",
                "title": "Incident Responder",
                "company": "Company B",
                "job_url": "",
                "test_keyword": "SOC Analyst",
                "test_location": "Pune",
            },
        ]
        deduped, raw_with_url, url_missing = merge_records(rows)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(raw_with_url, 0)
        self.assertEqual(url_missing, 2)
        self.assertEqual(deduped[0]["deduplication_key"], "url_missing:0")
        self.assertEqual(deduped[1]["deduplication_key"], "url_missing:1")

    def test_backfill_missing_fields_on_merge(self):
        rows = [
            {
                "site": "glassdoor",
                "title": "Security Analyst",
                "company": "TCS",
                "job_url": "https://glassdoor.co.in/job/555",
                "date_posted": None,
                "job_type": None,
                "test_keyword": "SOC Analyst",
                "test_location": "Chennai",
            },
            {
                "site": "glassdoor",
                "title": "Security Analyst",
                "company": "TCS",
                "job_url": "https://glassdoor.co.in/job/555",
                "date_posted": "2026-11-18",
                "job_type": "fulltime",
                "test_keyword": "Security Analyst",
                "test_location": "Chennai",
            },
        ]
        deduped, raw_with_url, url_missing = merge_records(rows)
        self.assertEqual(len(deduped), 1)
        merged = deduped[0]
        self.assertEqual(merged["date_posted"], "2026-11-18")
        self.assertEqual(merged["job_type"], "fulltime")


if __name__ == "__main__":
    unittest.main()

