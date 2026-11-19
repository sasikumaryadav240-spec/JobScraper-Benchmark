"""
Unit tests for indeed_parser.py module.
"""

import unittest
from indeed_parser import (
    extract_experience,
    extract_indeed_job_key,
    extract_salary_info,
    extract_work_mode,
)
from models import WorkMode


class TestIndeedParser(unittest.TestCase):

    def test_experience_range_extraction(self):
        desc = """
        Job Description:
        We are seeking a SOC Analyst.
        Requirements:
        - 3 to 5 years of relevant cybersecurity experience in a 24x7 SOC.
        - Experience with SIEM tools (Splunk, QRadar).
        """
        min_exp, max_exp, mand_text, pref_text, raw_text = extract_experience(desc)
        self.assertEqual(min_exp, 3.0)
        self.assertEqual(max_exp, 5.0)
        self.assertIn("3 to 5 years", mand_text)
        self.assertIsNone(pref_text)

    def test_experience_minimum_only(self):
        desc = """
        Qualifications:
        Minimum of 2+ years experience in vulnerability management and scanning tools.
        """
        min_exp, max_exp, mand_text, pref_text, raw_text = extract_experience(desc)
        self.assertEqual(min_exp, 2.0)
        self.assertIsNone(max_exp)
        self.assertIn("2+ years", mand_text)

    def test_experience_preferred_distinction(self):
        desc = """
        Requirements:
        - 2 years of IT security experience.
        Nice to have:
        - 5+ years of experience with cloud security (AWS/Azure).
        """
        min_exp, max_exp, mand_text, pref_text, raw_text = extract_experience(desc)
        self.assertEqual(min_exp, 2.0)
        self.assertIsNotNone(pref_text)
        self.assertIn("5+ years", pref_text)

    def test_company_history_noise_excluded(self):
        desc = """
        About Us:
        Our firm has been in business for 25 years delivering top-notch security solutions.
        Founded 30 years ago, we have a heritage of 100 years.
        Role: Entry level cybersecurity intern. No experience required.
        """
        min_exp, max_exp, mand_text, pref_text, raw_text = extract_experience(desc)
        # Should NOT capture 25, 30 or 100 years as candidate experience
        self.assertIsNone(min_exp)
        self.assertIsNone(max_exp)

    def test_unmentioned_experience_never_assumed_zero(self):
        desc = "Exciting security role. Familiarity with Linux and networking concepts."
        min_exp, max_exp, mand_text, pref_text, raw_text = extract_experience(desc)
        self.assertIsNone(min_exp)
        self.assertIsNone(max_exp)
        self.assertIsNone(raw_text)

    def test_work_mode_hybrid_vs_hybrid_cloud(self):
        # 1. Hybrid cloud infrastructure (should NOT be classified as hybrid work mode)
        desc_infra = "Manage security across hybrid and multi-cloud infrastructure environments (AWS/Azure)."
        wm_infra, _, _ = extract_work_mode("Bengaluru, Karnataka", desc_infra, is_remote_flag=False)
        self.assertEqual(wm_infra, WorkMode.ONSITE)

        # 2. Hybrid work location
        desc_work = "Workplace Type: Hybrid. Work Location: Hybrid remote in Bengaluru."
        wm_work, _, _ = extract_work_mode("Bengaluru, Karnataka", desc_work, is_remote_flag=False)
        self.assertEqual(wm_work, WorkMode.HYBRID)

    def test_remote_india_eligibility(self):
        desc = "Candidates must be based in India. 100% remote work."
        wm, rel, geo = extract_work_mode("India", desc, is_remote_flag=True)
        self.assertEqual(wm, WorkMode.REMOTE)
        self.assertEqual(rel, "india_only")
        self.assertEqual(geo, "India")

    def test_remote_foreign_restricted(self):
        desc = "Must be a US Citizen residing in the United States only. Remote within US."
        wm, rel, geo = extract_work_mode("Remote", desc, is_remote_flag=True)
        self.assertEqual(wm, WorkMode.REMOTE)
        self.assertEqual(rel, "country_restricted")
        self.assertIn("Non-India", geo)

    def test_extract_indeed_job_key(self):
        # From raw id
        self.assertEqual(extract_indeed_job_key("in-587eb9fc6b17e8d9", None), "587eb9fc6b17e8d9")
        # From viewjob URL
        url = "https://in.indeed.com/viewjob?jk=abcdef1234567890&utm_source=email"
        self.assertEqual(extract_indeed_job_key(None, url), "abcdef1234567890")
        # From vjk param
        url_vjk = "https://in.indeed.com/viewjob?vjk=1122334455667788"
        self.assertEqual(extract_indeed_job_key(None, url_vjk), "1122334455667788")

    def test_extract_salary_info(self):
        raw = {
            "min_amount": 800000.0,
            "max_amount": 1200000.0,
            "currency": "INR",
            "interval": "YEARLY",
        }
        res = extract_salary_info(raw)
        self.assertEqual(res["min_amount"], 800000.0)
        self.assertEqual(res["max_amount"], 1200000.0)
        self.assertEqual(res["currency"], "INR")
        self.assertEqual(res["interval"], "YEARLY")
        self.assertTrue(res["has_salary"])

    def test_en_dash_experience_range(self):
        desc = "Qualifications: 3–6 years of relevant experience in SOC, VM, or IAM operations."
        min_exp, max_exp, mand_text, pref_text, raw_text = extract_experience(desc)
        self.assertEqual(min_exp, 3.0)
        self.assertEqual(max_exp, 6.0)

    def test_word_number_experience(self):
        desc = "Candidate must have at least six years of experience in security operations."
        min_exp, max_exp, mand_text, pref_text, raw_text = extract_experience(desc)
        self.assertEqual(min_exp, 6.0)

    def test_preposition_experience(self):
        desc = "Requirements: 3 years as a Security Operations Analyst in an enterprise SOC."
        min_exp, max_exp, mand_text, pref_text, raw_text = extract_experience(desc)
        self.assertEqual(min_exp, 3.0)

    def test_degree_duration_not_captured(self):
        desc = "Education: Master's degree (2-3 years) in Information Technology. No prior experience required."
        min_exp, max_exp, mand_text, pref_text, raw_text = extract_experience(desc)
        self.assertIsNone(min_exp)

    def test_uber_hybrid_office_percentage(self):
        desc = "Unless approved for full remote work, employees must spend at least 50% of their time in-office."
        wm, _, _ = extract_work_mode("TS, India", desc, is_remote_flag=True)
        self.assertEqual(wm, WorkMode.HYBRID)


if __name__ == "__main__":
    unittest.main()

