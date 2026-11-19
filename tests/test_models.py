"""
Unit tests for models.py data models.
"""

import unittest
from models import FieldOrigin, JobRecord, QualityIndicators, VerificationStatus, WorkMode


class TestJobRecordModels(unittest.TestCase):

    def test_valid_minimal_record(self):
        rec = JobRecord(
            source="naukri",
            title="SOC Analyst",
            company="Infosys",
            location="Bengaluru",
        )
        self.assertEqual(rec.source, "naukri")
        self.assertEqual(rec.title, "SOC Analyst")
        self.assertEqual(rec.company, "Infosys")
        self.assertEqual(rec.location, "Bengaluru")
        self.assertIsNone(rec.description)
        self.assertIsNone(rec.min_experience)
        self.assertIsNone(rec.max_experience)
        self.assertEqual(rec.work_mode, WorkMode.UNKNOWN)
        self.assertEqual(rec.verification_status, VerificationStatus.UNVERIFIED)

    def test_reject_empty_title_or_company(self):
        with self.assertRaises(ValueError):
            JobRecord(source="indeed", title="", company="Wipro")
        with self.assertRaises(ValueError):
            JobRecord(source="indeed", title="Analyst", company="   ")
        with self.assertRaises(ValueError):
            JobRecord(source="indeed", title="Analyst", company="null")

    def test_quality_indicators_calculation(self):
        rec = JobRecord(
            source="glassdoor",
            title="Security Engineer",
            company="Cisco",
            location="Bengaluru",
            job_url="https://glassdoor.co.in/job/123",
            description="We are hiring a security engineer...",
            employment_type="fulltime",
            date_posted="2026-11-18",
            min_experience=3.0,
            max_experience=5.0,
        )
        qi = rec.compute_quality_indicators()
        self.assertTrue(qi.has_title)
        self.assertTrue(qi.has_company)
        self.assertTrue(qi.has_location)
        self.assertTrue(qi.has_description)
        self.assertTrue(qi.has_job_url)
        self.assertTrue(qi.has_experience_range)
        self.assertTrue(qi.has_date_posted)
        self.assertTrue(qi.has_employment_type)
        self.assertEqual(qi.missing_critical_fields, [])
        self.assertEqual(qi.completeness_score, 1.0)

    def test_missing_fields_never_fabricated(self):
        rec = JobRecord(
            source="linkedin",
            title="Junior Pentester",
            company="TCS",
        )
        qi = rec.compute_quality_indicators()
        self.assertFalse(qi.has_description)
        self.assertFalse(qi.has_job_url)
        self.assertFalse(qi.has_experience_range)
        self.assertIn("url", qi.missing_critical_fields)
        self.assertIn("location", qi.missing_critical_fields)
        self.assertIsNone(rec.description)
        self.assertIsNone(rec.min_experience)
        self.assertIsNone(rec.max_experience)
        self.assertIsNone(rec.country)
        self.assertIsNone(rec.state)
        self.assertIsNone(rec.city)

    def test_from_raw_jobspy_conversion(self):
        raw = {
            "id": "nk-181126030019",
            "site": "naukri",
            "title": "Security Analyst",
            "company": "Achieve Cybersecurity",
            "location": "Bengaluru, India",
            "job_url": "https://www.naukri.com/job-181126030019",
            "job_url_direct": None,
            "description": None,
            "date_posted": "2026-11-18",
            "job_type": None,
            "experience_range": "2-7 Yrs",
            "is_remote": False,
        }
        rec = JobRecord.from_raw_jobspy(
            raw,
            run_id="20261119_002214",
            keyword="SOC Analyst",
            search_location="Bengaluru",
            search_type="city",
        )
        self.assertEqual(rec.source, "naukri")
        self.assertEqual(rec.source_job_id, "nk-181126030019")
        self.assertEqual(rec.min_experience, 2.0)
        self.assertEqual(rec.max_experience, 7.0)
        self.assertEqual(rec.work_mode, WorkMode.ONSITE)
        self.assertIsNone(rec.description)
        self.assertEqual(rec.field_origins["min_experience"], FieldOrigin.SOURCE_PROVIDED)
        self.assertEqual(rec.field_origins["description"], FieldOrigin.UNKNOWN)
        self.assertIn("SOC Analyst", rec.matched_keywords)
        self.assertIn("Bengaluru", rec.matched_locations)

    def test_from_raw_jobspy_remote_detection(self):
        raw = {
            "site": "indeed",
            "title": "Cloud Security Engineer",
            "company": "RemoteCorp",
            "is_remote": True,
            "work_from_home_type": "Remote in India",
            "experience_range": "5+ years",
        }
        rec = JobRecord.from_raw_jobspy(raw)
        self.assertEqual(rec.work_mode, WorkMode.REMOTE)
        self.assertEqual(rec.min_experience, 5.0)
        self.assertIsNone(rec.max_experience)
        self.assertEqual(rec.field_origins["work_mode"], FieldOrigin.SOURCE_PROVIDED)


if __name__ == "__main__":
    unittest.main()

