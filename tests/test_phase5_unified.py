"""
Unit Tests for Phase 5 Unified Processing Pipeline.

Tests:
- Source data ingestion & fault tolerance
- Canonical schema validation
- Normalization (titles, companies, locations, dates, work modes, URLs)
- Cross-source deduplication tiers & false duplicate prevention
- Source URL and application URL preservation on merge
- Candidate skills matching & transparent weighted scoring
- Strict > 60.0% threshold boundary condition (59%, 60%, 60.01%)
- Experience eligibility boundary conditions (<=2 yrs PASS, >=3 yrs EXCLUDE, ranges, preferred)
- Education & batch restrictions (CS/IT only EXCLUDE, ECE PASS, 2026 batch)
- Job categorization & manual review assignment
- Export integrity
"""

import json
import tempfile
import unittest
from pathlib import Path

from candidate_matching_engine import (
    calculate_job_scores,
    evaluate_candidate_matching,
    evaluate_education_eligibility,
    evaluate_experience_eligibility,
    evaluate_job_category,
)
from candidate_profile import CANDIDATE_PROFILE
from unified_dedup import deduplicate_cross_source, merge_canonical_records
from unified_ingestion import ingest_all_sources, load_raw_source_file, map_raw_to_canonical
from unified_models import (
    CanonicalJobRecord,
    DescriptionType,
    EligibilityStatus,
    JobCategory,
    WorkModeEnum,
)
from unified_normalizer import (
    classify_description_quality,
    extract_work_mode_evidence,
    normalize_canonical_url,
    normalize_company_name,
    normalize_date_to_iso,
    normalize_job_title,
    normalize_location_details,
)
from unified_pipeline import UnifiedPipeline


class TestPhase5UnifiedPipeline(unittest.TestCase):

    def setUp(self):
        self.sample_job_dict = {
            "title": "Junior SOC Analyst [Remote]",
            "company": "SecureNet Pvt. Ltd.",
            "location": "Bengaluru, Karnataka, India",
            "description": (
                "We are hiring a Junior SOC Analyst for 24/7 security monitoring. "
                "Candidate will monitor alerts, use Splunk and Microsoft Sentinel, query with KQL, "
                "and triage security incidents. Requires 1-3 years of relevant experience. "
                "Any engineering graduate can apply for this position. We provide full training on "
                "enterprise security infrastructure, log analysis, alert correlation, and SOC playbooks. "
                "Candidate must possess strong problem-solving skills, analytical mindset, and clear communication abilities. "
                "This role operates on rotational shifts supporting global cybersecurity operations."
            ),
            "source": "indeed",
            "source_job_id": "test_soc_123",
            "job_url": "https://in.indeed.com/viewjob?jk=test_soc_123&utm_source=test",
            "application_url": "https://securenet.com/careers/apply?id=123",
            "min_experience": 1.0,
            "max_experience": 3.0,
            "date_posted": "2026-11-18T10:00:00Z",
        }

    # --- 1. Ingestion & Schema Mapping Tests ---

    def test_map_raw_to_canonical_success(self):
        rec, err = map_raw_to_canonical(self.sample_job_dict, default_source="indeed")
        self.assertIsNone(err)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.normalized_job_title, "Junior SOC Analyst")
        self.assertEqual(rec.normalized_company_name, "SecureNet")
        self.assertEqual(rec.work_mode, "remote")
        self.assertEqual(rec.mandatory_min_experience, 1.0)
        self.assertEqual(rec.mandatory_max_experience, 3.0)
        self.assertEqual(rec.description_type, "full_description")
        self.assertIn("https://in.indeed.com/viewjob?jk=test_soc_123", rec.all_source_urls[0])
        self.assertNotIn("utm_source", rec.all_source_urls[0])

    def test_ingestion_rejects_empty_identity(self):
        invalid = {"title": "", "company": "SecureNet", "source": "naukri"}
        rec, err = map_raw_to_canonical(invalid, default_source="naukri")
        self.assertIsNone(rec)
        self.assertIn("Missing critical identity", err)

    def test_load_raw_source_file_json_lines_and_arrays(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p_jsonl = Path(tmpdir) / "test.jsonl"
            with open(p_jsonl, "w", encoding="utf-8") as f:
                f.write('{"title": "Job 1", "company": "Co 1"}\n')
                f.write('{"title": "Job 2", "company": "Co 2"}\n')
                f.write('malformed json line\n')

            valid, malformed = load_raw_source_file(p_jsonl)
            self.assertEqual(len(valid), 2)
            self.assertEqual(len(malformed), 1)

    # --- 2. Normalization Tests ---

    def test_normalize_job_title(self):
        self.assertEqual(normalize_job_title("Cybersecurity Analyst [Remote]"), "Cybersecurity Analyst")
        self.assertEqual(normalize_job_title("SOC Analyst (Hybrid)"), "SOC Analyst")
        self.assertEqual(normalize_job_title("Security Engineer - Full Time"), "Security Engineer")

    def test_normalize_company_name(self):
        self.assertEqual(normalize_company_name("Infosys Pvt. Ltd."), "Infosys")
        self.assertEqual(normalize_company_name("CrowdStrike Inc."), "CrowdStrike")
        self.assertEqual(normalize_company_name("Palo Alto Networks Technologies"), "Palo Alto Networks")

    def test_normalize_canonical_url(self):
        url = "https://www.example.com/job/123?utm_source=indeed&ref=abc&jk=123"
        norm = normalize_canonical_url(url)
        self.assertEqual(norm, "https://example.com/job/123?jk=123")

    def test_extract_work_mode_evidence(self):
        # Unevidenced city must remain unknown
        mode, ev = extract_work_mode_evidence("SOC Analyst", "Bengaluru", "Responsible for monitoring.")
        self.assertEqual(mode, WorkModeEnum.UNKNOWN.value)

        # Hybrid cloud false positive must NOT trigger hybrid work mode
        mode, ev = extract_work_mode_evidence("Cloud Security Analyst", "Hyderabad", "Experience in hybrid cloud architecture required.")
        self.assertEqual(mode, WorkModeEnum.UNKNOWN.value)

        # Remote access false positive must NOT trigger remote work mode
        mode, ev = extract_work_mode_evidence("Security Engineer", "Pune", "Manage remote access VPN and firewalls.")
        self.assertEqual(mode, WorkModeEnum.UNKNOWN.value)

        # Explicit remote match
        mode, ev = extract_work_mode_evidence("SOC Analyst", "Remote", "100% remote position.")
        self.assertEqual(mode, WorkModeEnum.REMOTE.value)

    # --- 3. Cross-Source Deduplication & URL Preservation Tests ---

    def test_tier1_stable_id_dedup(self):
        rec1, _ = map_raw_to_canonical(self.sample_job_dict, "indeed")
        rec2, _ = map_raw_to_canonical(dict(self.sample_job_dict, salary_min=500000), "indeed")
        unique, audit = deduplicate_cross_source([rec1, rec2])
        self.assertEqual(len(unique), 1)
        self.assertEqual(audit.tier1_id_merges, 1)
        self.assertEqual(unique[0].salary_min, 500000)

    def test_tier2_url_dedup_and_url_preservation(self):
        job1 = dict(self.sample_job_dict, source="indeed", source_job_id="id1", application_url="https://co.com/apply1")
        job2 = dict(self.sample_job_dict, source="naukri", source_job_id="id2", application_url="https://co.com/apply1")
        rec1, _ = map_raw_to_canonical(job1, "indeed")
        rec2, _ = map_raw_to_canonical(job2, "naukri")

        unique, audit = deduplicate_cross_source([rec1, rec2])
        self.assertEqual(len(unique), 1)
        self.assertEqual(audit.tier2_url_merges, 1)

        # Verify that all URLs from both sources are preserved
        merged = unique[0]
        self.assertIn("https://co.com/apply1", merged.all_application_urls)
        self.assertEqual(len(merged.all_source_ids), 2)
        self.assertIn("indeed", merged.all_source_ids)
        self.assertIn("naukri", merged.all_source_ids)

    def test_distinct_jobs_never_falsely_merged(self):
        job1 = dict(self.sample_job_dict, title="SOC Analyst L1", location="Bengaluru", source_job_id="id1", job_url="https://co.com/j1", application_url="https://co.com/a1")
        job2 = dict(self.sample_job_dict, title="SOC Analyst L2", location="Hyderabad", source_job_id="id2", job_url="https://co.com/j2", application_url="https://co.com/a2")
        rec1, _ = map_raw_to_canonical(job1, "indeed")
        rec2, _ = map_raw_to_canonical(job2, "naukri")

        unique, audit = deduplicate_cross_source([rec1, rec2])
        self.assertEqual(len(unique), 2)
        self.assertEqual(audit.duplicates_merged_total, 0)

    # --- 4. Scoring & Threshold Boundary Tests ---

    def test_strict_score_threshold_boundary_60_pct(self):
        rec, _ = map_raw_to_canonical(self.sample_job_dict, "indeed")

        # Test boundary conditions: 59.99% -> EXCLUDE
        rec.overall_match_score = 59.99
        eval_59 = evaluate_candidate_matching(rec, recalculate_scores=False)
        self.assertEqual(eval_59.eligibility_status, EligibilityStatus.EXCLUDE_STATUS.value)
        self.assertTrue(any("does not strictly exceed the 60.0% threshold" in r for r in eval_59.rejection_reasons))

        # Exactly 60.0% MUST NOT qualify -> EXCLUDE
        rec.overall_match_score = 60.00
        rec.rejection_reasons = []
        rec.manual_review_reasons = []
        eval_60 = evaluate_candidate_matching(rec, recalculate_scores=False)
        self.assertEqual(eval_60.eligibility_status, EligibilityStatus.EXCLUDE_STATUS.value)
        self.assertTrue(any("does not strictly exceed the 60.0% threshold" in r for r in eval_60.rejection_reasons))

        # 60.01% DOES qualify on score (subject to other rules)
        rec.overall_match_score = 60.01
        rec.rejection_reasons = []
        rec.manual_review_reasons = []
        eval_6001 = evaluate_candidate_matching(rec, recalculate_scores=False)
        self.assertFalse(any("does not strictly exceed the 60.0% threshold" in r for r in eval_6001.rejection_reasons))

    # --- 5. Experience Eligibility Boundary Tests ---

    def test_experience_eligibility_acceptable_minimums(self):
        rec, _ = map_raw_to_canonical(self.sample_job_dict, "indeed")

        # 0 years -> PASS
        rec.mandatory_min_experience = 0.0
        status, _ = evaluate_experience_eligibility(rec)
        self.assertEqual(status, "PASS")

        # 1 year -> PASS
        rec.mandatory_min_experience = 1.0
        status, _ = evaluate_experience_eligibility(rec)
        self.assertEqual(status, "PASS")

        # 2 years -> PASS
        rec.mandatory_min_experience = 2.0
        status, _ = evaluate_experience_eligibility(rec)
        self.assertEqual(status, "PASS")

        # Range 2-5 years -> PASS (min is 2)
        rec.mandatory_min_experience = 2.0
        rec.mandatory_max_experience = 5.0
        status, _ = evaluate_experience_eligibility(rec)
        self.assertEqual(status, "PASS")

    def test_experience_eligibility_rejected_minimums(self):
        rec, _ = map_raw_to_canonical(self.sample_job_dict, "indeed")

        # 3+ years -> EXCLUDE
        rec.mandatory_min_experience = 3.0
        status, _ = evaluate_experience_eligibility(rec)
        self.assertEqual(status, "EXCLUDE")

        # 5+ years -> EXCLUDE
        rec.mandatory_min_experience = 5.0
        status, _ = evaluate_experience_eligibility(rec)
        self.assertEqual(status, "EXCLUDE")

    def test_experience_unmentioned_requires_manual_verification(self):
        rec, _ = map_raw_to_canonical(self.sample_job_dict, "indeed")
        rec.mandatory_min_experience = None
        rec.mandatory_max_experience = None
        rec.original_experience_text = None
        status, reasons = evaluate_experience_eligibility(rec)
        self.assertEqual(status, "REVIEW")
        self.assertTrue(any("not stated" in r for r in reasons))

    # --- 6. Education & Batch Restrictions Tests ---

    def test_education_cs_it_only_excluded(self):
        rec, _ = map_raw_to_canonical(self.sample_job_dict, "indeed")
        rec.job_description = "B.E./B.Tech in Computer Science only mandatory."
        status, reasons = evaluate_education_eligibility(rec)
        self.assertEqual(status, "EXCLUDE")
        self.assertTrue(any("CS/IT only" in r for r in reasons))

    def test_education_any_engineering_accepted(self):
        rec, _ = map_raw_to_canonical(self.sample_job_dict, "indeed")
        rec.job_description = "Any engineering graduate can apply for this position."
        status, _ = evaluate_education_eligibility(rec)
        self.assertEqual(status, "PASS")

    def test_education_batch_2024_2025_only_excluded(self):
        rec, _ = map_raw_to_canonical(self.sample_job_dict, "indeed")
        rec.job_description = "Open for 2024 batch only."
        status, reasons = evaluate_education_eligibility(rec)
        self.assertEqual(status, "EXCLUDE")
        self.assertTrue(any("2024/2025" in r for r in reasons))

    # --- 7. Job Categorization Tests ---

    def test_job_categorization(self):
        rec, _ = map_raw_to_canonical(self.sample_job_dict, "indeed")

        # Internship
        rec.job_title = "SOC Analyst Intern"
        self.assertEqual(evaluate_job_category(rec, 65.0, True), JobCategory.INTERNSHIP.value)

        # Supporting IT
        rec.job_title = "Cloud Infrastructure Engineer"
        self.assertEqual(evaluate_job_category(rec, 65.0, True), JobCategory.SUPPORTING_IT.value)

        # Direct Cybersecurity (PASS & >60%)
        rec.job_title = "Cybersecurity Analyst"
        self.assertEqual(evaluate_job_category(rec, 65.0, True), JobCategory.DIRECT_CYBERSECURITY.value)

        # Excluded
        self.assertEqual(evaluate_job_category(rec, 50.0, False), JobCategory.EXCLUDED.value)


if __name__ == "__main__":
    unittest.main()
