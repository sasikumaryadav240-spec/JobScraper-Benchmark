"""
Unit tests for indeed_dedup.py deduplication module.
"""

import unittest
from indeed_dedup import (
    compute_description_hash,
    deduplicate_indeed_records,
    get_indeed_dedup_key,
    normalize_indeed_url,
)


class TestIndeedDedup(unittest.TestCase):

    def test_dedup_by_job_key_tier1(self):
        records = [
            {
                "id": "in-587eb9fc6b17e8d9",
                "title": "L1 SOC Analyst",
                "company": "CGI",
                "location": "Bengaluru",
                "job_url": "https://in.indeed.com/viewjob?jk=587eb9fc6b17e8d9&utm_source=email",
                "test_keyword": "SOC Analyst",
                "test_location": "Bengaluru",
                "description": "Short description",
            },
            {
                "id": "in-587eb9fc6b17e8d9",
                "title": "L1 SOC Analyst",
                "company": "CGI",
                "location": "Bengaluru",
                "job_url": "https://in.indeed.com/viewjob?jk=587eb9fc6b17e8d9",
                "test_keyword": "SIEM Analyst",
                "test_location": "Bengaluru",
                "description": "Longer and more detailed description of the role.",
            },
        ]
        deduped, raw_count, dup_count, audit = deduplicate_indeed_records(records)
        self.assertEqual(raw_count, 2)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(dup_count, 1)
        self.assertEqual(len(audit), 1)

        merged = deduped[0]
        self.assertEqual(merged["deduplication_key"], "INDEED_JK:587eb9fc6b17e8d9")
        self.assertEqual(merged["dedup_tier"], "tier1_job_key")
        self.assertEqual(merged["description"], "Longer and more detailed description of the role.")
        self.assertEqual(sorted(merged["matched_keywords"]), ["SIEM Analyst", "SOC Analyst"])

    def test_dedup_by_normalized_url_tier2(self):
        records = [
            {
                "id": None,
                "title": "Cloud Security Analyst",
                "company": "Wipro",
                "location": "Hyderabad",
                "job_url": "https://in.indeed.com/rc/clk?jk=12345678abcdef01&from=vj",
                "description": "Description text here for cloud security.",
                "test_keyword": "Cloud Security Analyst",
            },
            {
                "id": None,
                "title": "Cloud Security Analyst",
                "company": "Wipro",
                "location": "Hyderabad",
                "job_url": "https://in.indeed.com/rc/clk?jk=12345678abcdef01&utm_medium=feed",
                "description": "Description text here for cloud security.",
                "test_keyword": "Security Analyst",
            },
        ]
        deduped, raw_count, dup_count, audit = deduplicate_indeed_records(records)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(dup_count, 1)

    def test_dedup_by_content_fingerprint_tier3(self):
        desc = "Detailed job description for cybersecurity monitoring with splunk and edr."
        records = [
            {
                "id": None,
                "title": "MDR Analyst",
                "company": "SecureWorks",
                "location": "Pune",
                "job_url": None,
                "description": desc,
                "test_keyword": "MDR Analyst",
            },
            {
                "id": None,
                "title": "MDR Analyst",
                "company": "SecureWorks",
                "location": "Pune",
                "job_url": None,
                "description": desc,
                "test_keyword": "SOC Analyst",
            },
        ]
        deduped, raw_count, dup_count, audit = deduplicate_indeed_records(records)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(dup_count, 1)
        self.assertTrue(deduped[0]["deduplication_key"].startswith("FINGERPRINT:"))

    def test_distinct_jobs_never_merged(self):
        records = [
            {
                "id": "in-1111111111111111",
                "title": "SOC Analyst L1",
                "company": "CGI",
                "job_url": "https://in.indeed.com/viewjob?jk=1111111111111111",
                "description": "Job 1 description",
            },
            {
                "id": "in-2222222222222222",
                "title": "SOC Analyst L2",
                "company": "CGI",
                "job_url": "https://in.indeed.com/viewjob?jk=2222222222222222",
                "description": "Job 2 description",
            },
        ]
        deduped, raw_count, dup_count, audit = deduplicate_indeed_records(records)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(dup_count, 0)


if __name__ == "__main__":
    unittest.main()

