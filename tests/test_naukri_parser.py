"""
Unit tests for Naukri parser and field extraction with regression coverage
for work-mode false positives, city-only unknown work modes, and description types.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from models import FieldOrigin, WorkMode
from naukri_parser import (
    DescriptionType,
    classify_description_type,
    extract_naukri_job_id,
    parse_naukri_date,
    parse_naukri_experience,
    parse_naukri_record,
    parse_naukri_work_mode,
)


class TestNaukriParser(unittest.TestCase):
    # --- ID Extraction ---
    def test_extract_naukri_job_id(self):
        self.assertEqual(extract_naukri_job_id("nk-171126013925", None), "171126013925")
        self.assertEqual(extract_naukri_job_id("171126013925", None), "171126013925")
        url = "https://www.naukri.com/job-listings-soc-analyst-i-berkadia-services-india-pvt-ltd-hyderabad-4-to-7-years-171126013925"
        self.assertEqual(extract_naukri_job_id(None, url), "171126013925")
        self.assertIsNone(extract_naukri_job_id(None, None))
        self.assertIsNone(extract_naukri_job_id("invalid-id", "https://example.com/no-id"))

    # --- Experience Parsing ---
    def test_parse_experience_native_range(self):
        min_e, max_e, text, origin = parse_naukri_experience("4-7 Yrs")
        self.assertEqual(min_e, 4.0)
        self.assertEqual(max_e, 7.0)
        self.assertEqual(origin, FieldOrigin.SOURCE_PROVIDED)

    def test_parse_experience_en_dash_range(self):
        min_e, max_e, text, origin = parse_naukri_experience("2–5 Yrs")
        self.assertEqual(min_e, 2.0)
        self.assertEqual(max_e, 5.0)
        self.assertEqual(origin, FieldOrigin.SOURCE_PROVIDED)

    def test_parse_experience_fresher(self):
        min_e, max_e, text, origin = parse_naukri_experience("Fresher")
        self.assertEqual(min_e, 0.0)
        self.assertEqual(max_e, 0.0)
        self.assertEqual(origin, FieldOrigin.SOURCE_PROVIDED)

    def test_parse_experience_min_only(self):
        min_e, max_e, text, origin = parse_naukri_experience("8+ Yrs")
        self.assertEqual(min_e, 8.0)
        self.assertIsNone(max_e)
        self.assertEqual(origin, FieldOrigin.SOURCE_PROVIDED)

    def test_unmentioned_experience_never_assumed_zero(self):
        min_e, max_e, text, origin = parse_naukri_experience(None, snippet="General job with no exp requirement")
        self.assertIsNone(min_e)
        self.assertIsNone(max_e)
        self.assertEqual(origin, FieldOrigin.UNKNOWN)

    def test_parse_experience_snippet_fallback(self):
        snippet = "Must have at least 3 to 6 years of experience in SIEM and security incident triage."
        min_e, max_e, text, origin = parse_naukri_experience(None, snippet=snippet)
        self.assertEqual(min_e, 3.0)
        self.assertEqual(max_e, 6.0)
        self.assertEqual(origin, FieldOrigin.EXTRACTED_FROM_DESCRIPTION)

    # --- Work Mode Classification & False-Positive Tests ---
    def test_city_without_work_mode_evidence_is_unknown(self):
        # NON-NEGOTIABLE: Bengaluru, Hyderabad, etc. must NOT be assumed onsite!
        mode, elig, ev = parse_naukri_work_mode(None, "Bengaluru, India")
        self.assertEqual(mode, WorkMode.UNKNOWN)
        self.assertIsNone(elig)

        mode, elig, ev = parse_naukri_work_mode(None, "Hyderabad")
        self.assertEqual(mode, WorkMode.UNKNOWN)
        self.assertIsNone(elig)

        mode, elig, ev = parse_naukri_work_mode(None, "Chennai, Pune, Mumbai")
        self.assertEqual(mode, WorkMode.UNKNOWN)
        self.assertIsNone(elig)

    def test_explicit_onsite_evidence(self):
        # Explicit walk-in
        mode, elig, ev = parse_naukri_work_mode(None, "Bengaluru", snippet="Walk-in drive for SOC Analyst at office premises.")
        self.assertEqual(mode, WorkMode.ONSITE)

        # Explicit work from office in snippet
        mode, elig, ev = parse_naukri_work_mode(None, "Hyderabad", snippet="Mandatory 100% onsite role, work from office daily.")
        self.assertEqual(mode, WorkMode.ONSITE)

        # Explicit location onsite
        mode, elig, ev = parse_naukri_work_mode(None, "Onsite - Bengaluru")
        self.assertEqual(mode, WorkMode.ONSITE)

    def test_explicit_hybrid_evidence(self):
        # Native Hybrid
        mode, elig, ev = parse_naukri_work_mode("Hybrid", "Bengaluru")
        self.assertEqual(mode, WorkMode.HYBRID)
        self.assertIsNone(elig)

        # Title explicit hybrid
        mode, elig, ev = parse_naukri_work_mode(None, "Bengaluru", title="Security Analyst/Sr. Security Analyst--Hybrid")
        self.assertEqual(mode, WorkMode.HYBRID)

        # Location explicit hybrid
        mode, elig, ev = parse_naukri_work_mode(None, "Hybrid - Pune, Bengaluru")
        self.assertEqual(mode, WorkMode.HYBRID)

        # Snippet explicit hybrid
        mode, elig, ev = parse_naukri_work_mode(None, "Chennai", snippet="This position offers a hybrid work model with 2 days from home.")
        self.assertEqual(mode, WorkMode.HYBRID)

    def test_explicit_remote_evidence(self):
        # Native Remote
        mode, elig, ev = parse_naukri_work_mode("Remote", "India")
        self.assertEqual(mode, WorkMode.REMOTE)
        self.assertEqual(elig, "india_only")

        # Location Remote
        mode, elig, ev = parse_naukri_work_mode(None, "Remote")
        self.assertEqual(mode, WorkMode.REMOTE)
        self.assertEqual(elig, "india_only")

        # Title explicit remote
        mode, elig, ev = parse_naukri_work_mode(None, "India", title="Senior SOC Analyst - 100% Remote")
        self.assertEqual(mode, WorkMode.REMOTE)
        self.assertEqual(elig, "india_only")

        # Snippet explicit remote
        mode, elig, ev = parse_naukri_work_mode(None, "Bengaluru", snippet="This is a fully remote role open to candidates across India.")
        self.assertEqual(mode, WorkMode.REMOTE)
        self.assertEqual(elig, "india_only")

    def test_work_mode_false_positives_excluded(self):
        # 'hybrid cloud' must NOT trigger hybrid work mode
        snippet_hc = "Responsible for managing hybrid cloud security across AWS and on-premise."
        mode, elig, ev = parse_naukri_work_mode(None, "Bengaluru", snippet=snippet_hc)
        self.assertEqual(mode, WorkMode.UNKNOWN)

        # 'hybrid infrastructure' must NOT trigger hybrid work mode
        snippet_hi = "Design and deploy security controls in hybrid infrastructure."
        mode, elig, ev = parse_naukri_work_mode(None, "Hyderabad", snippet=snippet_hi)
        self.assertEqual(mode, WorkMode.UNKNOWN)

        # 'remote access' must NOT trigger remote work mode
        snippet_ra = "Troubleshoot VPN and remote access security issues."
        mode, elig, ev = parse_naukri_work_mode(None, "Bengaluru", snippet=snippet_ra)
        self.assertEqual(mode, WorkMode.UNKNOWN)

        # 'remote monitoring' must NOT trigger remote work mode
        snippet_rm = "Perform 24x7 remote monitoring of SIEM alerts."
        mode, elig, ev = parse_naukri_work_mode(None, "Pune", snippet=snippet_rm)
        self.assertEqual(mode, WorkMode.UNKNOWN)

        # 'office location' or 'work location' must NOT trigger onsite work mode
        snippet_wl = "Primary office location: Bengaluru, Karnataka."
        mode, elig, ev = parse_naukri_work_mode(None, "Bengaluru", snippet=snippet_wl)
        self.assertEqual(mode, WorkMode.UNKNOWN)

    # --- Description Type Classification Tests ---
    def test_description_type_classification(self):
        # Missing
        self.assertEqual(classify_description_type(None), DescriptionType.MISSING)
        self.assertEqual(classify_description_type(""), DescriptionType.MISSING)
        self.assertEqual(classify_description_type("   "), DescriptionType.MISSING)

        # Search snippet
        snippet_text = "Accredited certifications in Incident Handling such as CEH, GCIH, or ECIH."
        self.assertEqual(classify_description_type(snippet_text, is_source_snippet=True), DescriptionType.SEARCH_RESULT_SNIPPET)

        # Full description (>= 500 chars, not snippet)
        full_text = "Responsibilities:\n" + ("Analyze security alerts and perform forensic triage.\n" * 20)
        self.assertEqual(classify_description_type(full_text, is_source_snippet=False), DescriptionType.FULL_DESCRIPTION)

        # Partial description (truncated ending with ...)
        partial_text = full_text + "..."
        self.assertEqual(classify_description_type(partial_text, is_source_snippet=False), DescriptionType.PARTIAL_DESCRIPTION)

    # --- Date and Record Parsing Tests ---
    def test_parse_date_epoch_ms(self):
        dt_str = parse_naukri_date(1794894170070)
        self.assertIsNotNone(dt_str)
        self.assertTrue(dt_str.startswith("2026-"))

    def test_parse_date_string(self):
        dt_str = parse_naukri_date("2026-11-18 10:30:00")
        self.assertIsNotNone(dt_str)
        self.assertTrue(dt_str.startswith("2026-11-18"))

    def test_parse_naukri_record_complete(self):
        raw = {
            "id": "nk-171126013925",
            "title": "SOC Analyst I",
            "company": "Berkadia",
            "location": "Hyderabad, India",
            "job_url": "https://www.naukri.com/job-listings-soc-analyst-i-berkadia-171126013925",
            "job_url_direct": "https://berkadia.wd1.myworkdayjobs.com/en-US/Careers/job/123",
            "experience_range": "4-7 Yrs",
            "work_from_home_type": "Hybrid",
            "createdDate": 1794894170070,
            "jobDescription": "Incident handling and SIEM log monitoring.",
            "min_amount": 800000,
            "max_amount": 1400000,
            "currency": "INR",
        }
        rec = parse_naukri_record(raw, "test_run_1")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.source, "naukri")
        self.assertEqual(rec.source_job_id, "171126013925")
        self.assertEqual(rec.title, "SOC Analyst I")
        self.assertEqual(rec.company, "Berkadia")
        self.assertEqual(rec.min_experience, 4.0)
        self.assertEqual(rec.max_experience, 7.0)
        self.assertEqual(rec.work_mode, WorkMode.HYBRID)
        self.assertEqual(rec.source_metadata["salary_min"], 800000.0)
        self.assertEqual(rec.source_metadata["salary_max"], 1400000.0)
        self.assertEqual(rec.source_metadata["description_type"], DescriptionType.SEARCH_RESULT_SNIPPET.value)
        self.assertEqual(rec.field_origins["title"], FieldOrigin.SOURCE_PROVIDED)
        self.assertEqual(rec.field_origins["experience"], FieldOrigin.SOURCE_PROVIDED)

    def test_nan_salary_never_stored_as_disclosed(self):
        raw = {
            "title": "Cloud Security Analyst",
            "company": "Infosys",
            "min_amount": float("nan"),
            "max_amount": float("nan"),
        }
        rec = parse_naukri_record(raw, "test_run_2")
        self.assertIsNotNone(rec)
        self.assertIsNone(rec.source_metadata["salary_min"])
        self.assertIsNone(rec.source_metadata["salary_max"])

    def test_reject_empty_title_or_company(self):
        self.assertIsNone(parse_naukri_record({"title": "", "company": "Berkadia"}, "test"))
        self.assertIsNone(parse_naukri_record({"title": "SOC Analyst", "company": None}, "test"))


if __name__ == "__main__":
    unittest.main()
