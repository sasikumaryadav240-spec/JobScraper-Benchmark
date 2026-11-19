"""
Unit tests for LinkedIn parser and field extraction module.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from models import WorkMode
from linkedin_parser import (
    DescriptionType,
    classify_description_type,
    extract_experience_from_text,
    extract_linkedin_job_id,
    extract_remote_eligibility,
    extract_work_mode,
    normalize_linkedin_url,
    parse_linkedin_date,
    parse_linkedin_record,
)


class TestLinkedInParser(unittest.TestCase):
    def test_extract_linkedin_job_id(self):
        self.assertEqual(extract_linkedin_job_id("li-4492063072", None), "4492063072")
        self.assertEqual(extract_linkedin_job_id("4492063072", None), "4492063072")
        self.assertEqual(
            extract_linkedin_job_id(None, "https://www.linkedin.com/jobs/view/4492063072?trackingId=abc"),
            "4492063072",
        )
        self.assertEqual(
            extract_linkedin_job_id(None, "https://in.linkedin.com/jobs/view/soc-analyst-at-cgi-4492063072"),
            "4492063072",
        )
        self.assertEqual(
            extract_linkedin_job_id(None, "https://www.linkedin.com/jobs/search/?currentJobId=4492063072&geoId=123"),
            "4492063072",
        )
        self.assertIsNone(extract_linkedin_job_id(None, "https://www.linkedin.com/feed/"))

    def test_normalize_linkedin_url(self):
        raw = "https://www.linkedin.com/jobs/view/4492063072?refId=123&trackingId=xyz&position=1&pageNum=0&trk=public_jobs"
        expected = "https://www.linkedin.com/jobs/view/4492063072"
        self.assertEqual(normalize_linkedin_url(raw), expected)

    def test_classify_description_type(self):
        self.assertEqual(classify_description_type(None), DescriptionType.MISSING)
        self.assertEqual(classify_description_type(""), DescriptionType.MISSING)
        self.assertEqual(classify_description_type("Short search snippet"), DescriptionType.SEARCH_RESULT_SNIPPET)
        partial_text = "A" * 400
        self.assertEqual(classify_description_type(partial_text), DescriptionType.PARTIAL_DESCRIPTION)
        full_text = "B" * 600
        self.assertEqual(classify_description_type(full_text), DescriptionType.FULL_DESCRIPTION)

    def test_experience_extraction_range(self):
        text = "Candidates must have 3-5 years of experience in SIEM, EDR, and security operations."
        min_e, max_e, pref, wording = extract_experience_from_text(text)
        self.assertEqual(min_e, 3.0)
        self.assertEqual(max_e, 5.0)
        self.assertIn("3-5 years", wording)

    def test_experience_extraction_min_only(self):
        text = "Requires minimum 4 years of experience as a SOC Analyst."
        min_e, max_e, pref, wording = extract_experience_from_text(text)
        self.assertEqual(min_e, 4.0)
        self.assertIsNone(max_e)
        self.assertIn("4 years", wording)

    def test_experience_extraction_fresher(self):
        text = "This is an entry-level position. Freshers with a degree in Cybersecurity are encouraged to apply."
        min_e, max_e, pref, wording = extract_experience_from_text(text)
        self.assertEqual(min_e, 0.0)
        self.assertEqual(max_e, 0.0)

    def test_experience_unmentioned_never_assumed_zero(self):
        text = "Join our high-performing cybersecurity team. Knowledge of Linux, TCP/IP, and firewall concepts required."
        min_e, max_e, pref, wording = extract_experience_from_text(text)
        self.assertIsNone(min_e)
        self.assertIsNone(max_e)

    def test_experience_preferred_distinction(self):
        text = "Minimum 3 years of experience in incident response required; 5+ years preferred."
        min_e, max_e, pref, wording = extract_experience_from_text(text)
        self.assertEqual(min_e, 3.0)
        self.assertEqual(pref, 5.0)

    def test_experience_false_positives_excluded(self):
        # Degree duration
        text_degree = "Must possess a 4-year degree in Computer Science or Information Technology."
        min_e, max_e, _, _ = extract_experience_from_text(text_degree)
        self.assertIsNone(min_e)
        self.assertIsNone(max_e)

        # Company history
        text_company = "With over 25 years in business, our organization delivers enterprise security."
        min_e, max_e, _, _ = extract_experience_from_text(text_company)
        self.assertIsNone(min_e)

        # Tool version
        text_tool = "Hands-on experience with Python 3.8 and Windows 10 is desired."
        min_e, max_e, _, _ = extract_experience_from_text(text_tool)
        self.assertIsNone(min_e)

    def test_work_mode_city_without_evidence_is_unknown(self):
        mode, ev = extract_work_mode("SOC Analyst", "Bengaluru, Karnataka, India", "Analyze security alerts in a 24/7 SOC.")
        self.assertEqual(mode, WorkMode.UNKNOWN)
        self.assertIsNone(ev)

    def test_work_mode_explicit_onsite(self):
        mode, ev = extract_work_mode(
            "SOC Analyst", "Bengaluru, Karnataka, India", "This is an on-site role working from our Bengaluru security operations center."
        )
        self.assertEqual(mode, WorkMode.ONSITE)
        self.assertIsNotNone(ev)

    def test_work_mode_explicit_hybrid(self):
        mode, ev = extract_work_mode(
            "Cybersecurity Analyst", "Hyderabad, Telangana, India", "We operate a hybrid work model with 2 days in office and 3 days remote."
        )
        self.assertEqual(mode, WorkMode.HYBRID)
        self.assertIsNotNone(ev)

    def test_work_mode_explicit_remote(self):
        mode, ev = extract_work_mode(
            "SIEM Analyst", "India", "This is a 100% remote role with flexible working hours."
        )
        self.assertEqual(mode, WorkMode.REMOTE)
        self.assertIsNotNone(ev)

    def test_work_mode_false_positives_excluded(self):
        # Remote monitoring and remote access should not trigger WorkMode.REMOTE
        mode, ev = extract_work_mode(
            "SOC Analyst", "Bengaluru", "Perform remote monitoring of customer firewalls and manage remote access VPN logs."
        )
        self.assertEqual(mode, WorkMode.UNKNOWN)

        # Hybrid cloud should not trigger WorkMode.HYBRID
        mode_cloud, ev_cloud = extract_work_mode(
            "Cloud Security Engineer", "Pune", "Manage security across AWS and hybrid cloud infrastructure."
        )
        self.assertEqual(mode_cloud, WorkMode.UNKNOWN)

    def test_remote_eligibility_india_only(self):
        elig, ev = extract_remote_eligibility(
            "India", "This is a fully remote role within India. Must be an Indian citizen or have work rights.", WorkMode.REMOTE
        )
        self.assertEqual(elig, "india_only")

    def test_remote_eligibility_country_restricted(self):
        elig, ev = extract_remote_eligibility(
            "Remote", "Remote position, US-only. Must reside in the US.", WorkMode.REMOTE
        )
        self.assertEqual(elig, "country_restricted")

    def test_remote_eligibility_international(self):
        elig, ev = extract_remote_eligibility(
            "Worldwide", "Join our team from anywhere in the world. International remote.", WorkMode.REMOTE
        )
        self.assertEqual(elig, "international")

    def test_parse_linkedin_record_complete(self):
        raw = {
            "id": "li-4492063072",
            "title": "SOC Analyst L1",
            "company": "Trellix",
            "location": "Bengaluru, Karnataka, India",
            "date_posted": "2026-11-18",
            "job_url": "https://www.linkedin.com/jobs/view/4492063072?trk=abc",
            "job_url_direct": "https://careers.trellix.com/job/123",
            "job_type": "fulltime",
            "description": "About Trellix. We are looking for a SOC Analyst L1. Minimum 2 years of experience in SIEM required. This is a hybrid work arrangement.",
            "test_keyword": "SOC Analyst L1",
            "test_location": "Bengaluru",
            "test_is_remote": False,
        }
        rec = parse_linkedin_record(raw, "test_run_1")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.title, "SOC Analyst L1")
        self.assertEqual(rec.company, "Trellix")
        self.assertEqual(rec.job_url, "https://www.linkedin.com/jobs/view/4492063072")
        self.assertEqual(rec.application_url, "https://careers.trellix.com/job/123")
        self.assertEqual(rec.min_experience, 2.0)
        self.assertEqual(rec.work_mode, WorkMode.HYBRID)
        self.assertEqual(rec.source_metadata.get("source_job_id"), "4492063072")

    def test_nan_salary_never_stored_as_disclosed(self):
        raw = {
            "id": "li-123",
            "title": "Security Analyst",
            "company": "Capgemini",
            "location": "Mumbai",
            "min_amount": float("nan"),
            "max_amount": float("nan"),
            "currency": None,
        }
        rec = parse_linkedin_record(raw, "test_run_2")
        self.assertIsNotNone(rec)
        self.assertIsNone(rec.source_metadata.get("salary_min"))
        self.assertIsNone(rec.source_metadata.get("salary_max"))
        self.assertIsNone(rec.source_metadata.get("salary_text"))


if __name__ == "__main__":
    unittest.main()
