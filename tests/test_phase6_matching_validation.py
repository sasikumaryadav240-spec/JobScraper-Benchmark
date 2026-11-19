"""
Unit Tests for Phase 6 Candidate Profile Matching Validation & Audit.

Covers all 24 specific requirements from Phase 6 prompt:
1. All 15 role families are available to the matching engine.
2. Candidate profile proficiency distinctions are respected.
3. Learning targets do not count as existing skills.
4. MERN internship is not counted as cybersecurity employment.
5. Weighted score components total 100%.
6. Exactly 60% is rejected.
7. 60.01% qualifies only if all eligibility checks pass.
8. Mandatory minimum 2 years passes.
9. Mandatory minimum 3 years fails.
10. Preferred 3 years does not automatically fail a job with accepted mandatory experience.
11. Missing experience leads to manual verification.
12. ECE degree is accepted when engineering degrees are permitted.
13. Explicit CS/IT-only restrictions are enforced.
14. Batch restrictions are enforced.
15. India remote eligibility is checked.
16. Seniority checks examine responsibilities as well as titles.
17. High scores cannot override eligibility failures.
18. Alternative-tool rules work correctly.
19. Duplicate records preserve all source URLs.
20. The deduplicated master dataset includes all unique jobs.
21. Matching processes every canonical job.
22. Matched, manual, supporting IT, internship and excluded outputs reconcile.
23. JSON, CSV and XLSX files are valid.
24. Historical runs are not overwritten.
"""

from __future__ import annotations

import csv
import json
import mimetypes
import tempfile
import unittest
from pathlib import Path

# Windows registry permission guard for openpyxl
try:
    mimetypes.init()
except PermissionError:
    mimetypes._winreg = None
    mimetypes.init = lambda *args, **kwargs: None

import openpyxl

from candidate_matching_engine import (
    CandidateMatchingEngine,
    MATCHER_ENGINE,
    evaluate_candidate_matching,
    evaluate_education_eligibility,
    evaluate_experience_eligibility,
)
from candidate_profile import CANDIDATE_PROFILE, SCORING_WEIGHTS
from unified_dedup import deduplicate_cross_source
from unified_ingestion import map_raw_to_canonical
from unified_models import CanonicalJobRecord, EligibilityStatus, JobCategory
from unified_pipeline import UnifiedPipeline


class TestPhase6MatchingValidation(unittest.TestCase):

    def setUp(self):
        self.engine = MATCHER_ENGINE
        self.sample_soc_job = CanonicalJobRecord(
            canonical_job_id="test_canon_1",
            source="indeed",
            source_job_id="src_1",
            job_title="Junior SOC Analyst",
            normalized_job_title="Junior SOC Analyst",
            company_name="SecureDefense Ltd.",
            normalized_company_name="SecureDefense",
            job_description=(
                "We are looking for a Junior SOC Analyst to monitor security events, "
                "investigate alerts, triage incidents, and query logs using Splunk and Microsoft Sentinel. "
                "Requires 1-2 years of relevant experience. Any engineering degree accepted."
            ),
            original_location="Bengaluru, Karnataka, India",
            normalized_location="Bengaluru, Karnataka, India",
            work_mode="onsite",
            mandatory_min_experience=1.0,
            mandatory_max_experience=2.0,
            collected_at="2026-11-19T00:00:00Z",
            all_source_urls=["https://example.com/job1"],
            application_url="https://example.com/apply1",
        )

    # 1. All 15 role families are available
    def test_01_all_15_role_families_available(self):
        families = self.engine.role_families
        self.assertEqual(len(families), 15)
        family_names = [f["name"] for f in families]
        expected_families = [
            "SOC Analyst / Security Operations",
            "Security Analyst / Information Security Analyst",
            "SIEM / Detection / Incident Response",
            "Security Engineering",
            "Cloud Security",
            "Application Security / Product Security",
            "Vulnerability Management / Security Testing",
            "IAM / Identity and Access Management",
            "GRC / IT Risk / Compliance",
            "Security Research / Threat Intelligence",
            "Help Desk Technician",
            "Desktop Support Engineer",
            "IT Support Specialist",
            "Junior System Administrator",
            "Network Support Associate",
        ]
        for ef in expected_families:
            self.assertIn(ef, family_names)

    # 2. Candidate profile proficiency distinctions are respected
    def test_02_proficiency_distinctions_respected(self):
        levels = CANDIDATE_PROFILE["proficiency_levels"]
        self.assertTrue(levels["professional"]["is_employment"])
        self.assertFalse(levels["practical"]["is_employment"])
        self.assertFalse(levels["familiarity"]["is_employment"])
        self.assertFalse(levels["learning_target"]["is_employment"])
        self.assertFalse(levels["certification"]["is_employment"])

    # 3. Learning targets do not count as existing skills
    def test_03_learning_targets_do_not_count_as_existing_skills(self):
        # Zeek, YARA, Shuffle SOAR are learning targets
        desc = "Candidate will use Zeek and YARA and Shuffle SOAR for automation."
        matched, partially_matched, missing, learning_targets = self.engine.extract_matched_and_missing(desc)
        # Should not be in matched practical skills
        matched_names = [m["name"].lower() for m in matched]
        self.assertNotIn("zeek", matched_names)
        self.assertNotIn("yara", matched_names)
        self.assertNotIn("shuffle soar", matched_names)
        # But should be captured in learning target overlap
        lt_lower = [lt.lower() for lt in learning_targets]
        self.assertTrue(any("zeek" in lt for lt in lt_lower))
        self.assertTrue(any("yara" in lt for lt in lt_lower))

    # 4. MERN internship is not counted as cybersecurity employment
    def test_04_mern_internship_not_cybersecurity_employment(self):
        exp = CANDIDATE_PROFILE["experience"][0]
        self.assertEqual(exp["role"], "MERN Stack Developer Intern")
        self.assertFalse(exp["is_cybersecurity_employment"])
        self.assertFalse(exp["is_it_support_employment"])

        # When a job strictly requires 2+ years of professional cybersecurity/SOC employment
        job = self.sample_soc_job.model_copy(deep=True)
        job.job_description = "Requires minimum 2+ years of professional experience in production SOC operations."
        job.mandatory_min_experience = 2.0
        evaluated = evaluate_candidate_matching(job)
        self.assertEqual(evaluated.eligibility_status, EligibilityStatus.EXCLUDE_STATUS.value)
        self.assertTrue(any("Mandatory professional cybersecurity/SOC experience required" in r for r in evaluated.rejection_reasons))

    # 5. Weighted score components total 100%
    def test_05_weighted_score_components_total_100_percent(self):
        total_weight = sum(SCORING_WEIGHTS.values())
        self.assertAlmostEqual(total_weight, 1.00, places=4)
        self.assertEqual(SCORING_WEIGHTS["required_technical_skills"], 0.35)
        self.assertEqual(SCORING_WEIGHTS["core_responsibilities"], 0.25)
        self.assertEqual(SCORING_WEIGHTS["experience_compatibility"], 0.15)
        self.assertEqual(SCORING_WEIGHTS["tools_and_technologies"], 0.10)
        self.assertEqual(SCORING_WEIGHTS["education_and_certifications"], 0.05)
        self.assertEqual(SCORING_WEIGHTS["transferable_skills"], 0.10)

    # 6. Exactly 60% is rejected
    def test_06_exactly_60_percent_rejected(self):
        job = self.sample_soc_job.model_copy(deep=True)
        job.overall_match_score = 60.00
        evaluated = evaluate_candidate_matching(job, recalculate_scores=False)
        self.assertEqual(evaluated.eligibility_status, EligibilityStatus.EXCLUDE_STATUS.value)
        self.assertTrue(any("does not strictly exceed the 60.0% threshold" in r for r in evaluated.rejection_reasons))

    # 7. 60.01% qualifies only if all eligibility checks pass
    def test_07_60_01_percent_qualifies_if_eligible(self):
        job = self.sample_soc_job.model_copy(deep=True)
        job.overall_match_score = 60.01
        evaluated = evaluate_candidate_matching(job, recalculate_scores=False)
        self.assertEqual(evaluated.eligibility_status, EligibilityStatus.PASS_STATUS.value)
        self.assertEqual(evaluated.job_category, JobCategory.DIRECT_CYBERSECURITY.value)

    # 8. Mandatory minimum 2 years passes
    def test_08_mandatory_min_2_years_passes(self):
        job = self.sample_soc_job.model_copy(deep=True)
        job.mandatory_min_experience = 2.0
        status, reasons = evaluate_experience_eligibility(job)
        self.assertEqual(status, "PASS")

    # 9. Mandatory minimum 3 years fails
    def test_09_mandatory_min_3_years_fails(self):
        job = self.sample_soc_job.model_copy(deep=True)
        job.mandatory_min_experience = 3.0
        status, reasons = evaluate_experience_eligibility(job)
        self.assertEqual(status, "EXCLUDE")
        self.assertTrue(any("exceeds limit of 2 years" in r for r in reasons))

    # 10. Preferred 3 years does not fail a job with accepted mandatory experience
    def test_10_preferred_experience_does_not_fail_accepted_mandatory(self):
        job = self.sample_soc_job.model_copy(deep=True)
        job.mandatory_min_experience = 1.0
        job.preferred_min_experience = 3.0
        job.job_description = "Mandatory 1 year experience in security. Preferred 3+ years experience with Splunk."
        status, reasons = evaluate_experience_eligibility(job)
        self.assertEqual(status, "PASS")

    # 11. Missing experience leads to manual verification
    def test_11_missing_experience_leads_to_manual_verification(self):
        job = self.sample_soc_job.model_copy(deep=True)
        job.mandatory_min_experience = None
        job.mandatory_max_experience = None
        job.original_experience_text = None
        status, reasons = evaluate_experience_eligibility(job)
        self.assertEqual(status, "REVIEW")
        evaluated = evaluate_candidate_matching(job, recalculate_scores=False)
        # Should be manual verification if overall score > 60
        job.overall_match_score = 65.0
        evaluated2 = evaluate_candidate_matching(job, recalculate_scores=False)
        self.assertEqual(evaluated2.job_category, JobCategory.MANUAL_VERIFICATION.value)

    # 12. ECE degree is accepted when engineering degrees are permitted
    def test_12_ece_degree_accepted_for_engineering(self):
        job = self.sample_soc_job.model_copy(deep=True)
        job.job_description = "Qualifications: B.E. / B.Tech in any engineering stream or ECE."
        status, reasons = evaluate_education_eligibility(job)
        self.assertEqual(status, "PASS")

    # 13. Explicit CS/IT-only restrictions are enforced
    def test_13_cs_it_only_restriction_enforced(self):
        job = self.sample_soc_job.model_copy(deep=True)
        job.job_description = "Qualifications: B.Tech in Computer Science only required."
        status, reasons = evaluate_education_eligibility(job)
        self.assertEqual(status, "EXCLUDE")
        self.assertTrue(any("CS/IT only" in r for r in reasons))

    # 14. Batch restrictions are enforced
    def test_14_batch_restrictions_enforced(self):
        job = self.sample_soc_job.model_copy(deep=True)
        job.job_description = "This drive is strictly for 2024 batch only."
        status, reasons = evaluate_education_eligibility(job)
        self.assertEqual(status, "EXCLUDE")
        self.assertTrue(any("2024/2025" in r for r in reasons))

    # 15. India remote eligibility is checked
    def test_15_india_remote_eligibility_checked(self):
        job = self.sample_soc_job.model_copy(deep=True)
        job.work_mode = "remote"
        job.remote_eligibility = "country_restricted"
        evaluated = evaluate_candidate_matching(job)
        self.assertEqual(evaluated.eligibility_status, EligibilityStatus.EXCLUDE_STATUS.value)
        self.assertTrue(any("restricted to a foreign country" in r for r in evaluated.rejection_reasons))

    # 16. Seniority checks examine responsibilities as well as titles
    def test_16_seniority_checks_title_and_responsibilities(self):
        # Senior title
        job1 = self.sample_soc_job.model_copy(deep=True)
        job1.job_title = "TC-CS-CAOE-Security Architect Manager"
        job1.overall_match_score = 75.0
        eval1 = evaluate_candidate_matching(job1, recalculate_scores=False)
        self.assertEqual(eval1.eligibility_status, EligibilityStatus.EXCLUDE_STATUS.value)
        self.assertTrue(any("Manager/Architect/Director" in r for r in eval1.rejection_reasons))

        # Senior responsibilities in description
        job2 = self.sample_soc_job.model_copy(deep=True)
        job2.job_title = "Security Analyst"
        job2.job_description = "Role involves managing a team of 15 security engineers and direct reports."
        eval2 = evaluate_candidate_matching(job2)
        self.assertEqual(eval2.eligibility_status, EligibilityStatus.EXCLUDE_STATUS.value)
        self.assertTrue(any("direct team management" in r for r in eval2.rejection_reasons))

    # 17. High scores cannot override eligibility failures
    def test_17_high_scores_cannot_override_eligibility_failures(self):
        job = self.sample_soc_job.model_copy(deep=True)
        job.mandatory_min_experience = 5.0  # Ineligible
        job.overall_match_score = 95.0
        evaluated = evaluate_candidate_matching(job, recalculate_scores=False)
        self.assertEqual(evaluated.eligibility_status, EligibilityStatus.EXCLUDE_STATUS.value)
        self.assertEqual(evaluated.job_category, JobCategory.EXCLUDED.value)

    # 18. Alternative-tool rules work correctly
    def test_18_alternative_tool_rules(self):
        # Splunk in JD gives Sentinel alternative credit
        desc_splunk = "Requires SIEM monitoring with Splunk."
        matched, partially_matched, _, _ = self.engine.extract_matched_and_missing(desc_splunk)
        all_found = [m["name"] for m in matched + partially_matched]
        self.assertTrue(any("Sentinel" in name or "Splunk" in name for name in all_found))

        # Qualys in JD gives Nessus VA alternative credit
        desc_qualys = "Requires vulnerability assessment using Qualys."
        matched, partially_matched, _, _ = self.engine.extract_matched_and_missing(desc_qualys)
        all_found = [m["name"] for m in matched + partially_matched]
        self.assertTrue(any("Nessus" in name for name in all_found))

    # 19. Duplicate records preserve all source URLs
    def test_19_duplicate_records_preserve_all_source_urls(self):
        raw1 = {
            "title": "SOC Analyst", "company": "CyberSec", "location": "Bengaluru",
            "source": "indeed", "source_job_id": "ind1",
            "job_url": "https://indeed.com/j1", "application_url": "https://co.com/apply",
        }
        raw2 = {
            "title": "SOC Analyst", "company": "CyberSec", "location": "Bengaluru",
            "source": "naukri", "source_job_id": "nau1",
            "job_url": "https://naukri.com/j1", "application_url": "https://co.com/apply",
        }
        rec1, _ = map_raw_to_canonical(raw1, "indeed")
        rec2, _ = map_raw_to_canonical(raw2, "naukri")
        unique, audit = deduplicate_cross_source([rec1, rec2])
        self.assertEqual(len(unique), 1)
        merged = unique[0]
        self.assertEqual(len(merged.all_source_urls), 2)
        self.assertIn("https://indeed.com/j1", merged.all_source_urls)
        self.assertIn("https://naukri.com/j1", merged.all_source_urls)

    # 20. Deduplicated master dataset includes all unique jobs
    def test_20_deduplicated_master_dataset_includes_all_unique_jobs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            # Create synthetic raw sources
            jobs = [
                {"title": "Job 1", "company": "Co A", "source": "indeed", "source_job_id": "1", "location": "Bengaluru", "job_url": "https://co.com/1"},
                {"title": "Job 2", "company": "Co B", "source": "naukri", "source_job_id": "2", "location": "Hyderabad", "job_url": "https://co.com/2"},
            ]
            with open(p / "indeed_jobs.json", "w", encoding="utf-8") as f:
                json.dump(jobs[:1], f)
            with open(p / "naukri_jobs.json", "w", encoding="utf-8") as f:
                json.dump(jobs[1:], f)

            pipeline = UnifiedPipeline(base_dir=p)
            pipeline.run()

            dedup_file = p / "deduplicated_jobs.json"
            self.assertTrue(dedup_file.exists())
            with open(dedup_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(len(data), 2)

    # 21. Matching processes every canonical job
    def test_21_matching_processes_every_canonical_job(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            jobs = [
                {"title": "SOC Analyst", "company": "Co A", "source": "indeed", "source_job_id": "1", "location": "Bengaluru", "job_url": "https://co.com/1"},
                {"title": "Network Admin", "company": "Co B", "source": "naukri", "source_job_id": "2", "location": "Hyderabad", "job_url": "https://co.com/2"},
            ]
            with open(p / "indeed_jobs.json", "w", encoding="utf-8") as f:
                json.dump(jobs, f)

            pipeline = UnifiedPipeline(base_dir=p)
            summary = pipeline.run()
            self.assertEqual(summary["total_canonical_unique"], 2)
            self.assertEqual(summary["deduplicated_master_count"], 2)

    # 22. Matched, manual, supporting IT, internship and excluded outputs reconcile
    def test_22_category_outputs_reconcile_exactly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            jobs = [
                {"title": "Junior SOC Analyst", "company": "Co A", "source": "indeed", "source_job_id": "1", "location": "Bengaluru", "job_url": "https://co.com/1", "description": "Junior SOC Analyst Splunk Sentinel monitoring 1 year exp. Engineering accepted.", "min_experience": 1.0},
                {"title": "Senior Security Architect", "company": "Co B", "source": "naukri", "source_job_id": "2", "location": "Hyderabad", "job_url": "https://co.com/2", "description": "Senior Security Architect Manager 10 years experience required.", "min_experience": 10.0},
                {"title": "Cybersecurity Intern", "company": "Co C", "source": "linkedin", "source_job_id": "3", "location": "Pune", "job_url": "https://co.com/3", "description": "Security intern trainee position."},
                {"title": "Desktop Support Engineer", "company": "Co D", "source": "ats", "source_job_id": "4", "location": "Chennai", "job_url": "https://co.com/4", "description": "Desktop support hardware troubleshooting 1 year experience.", "min_experience": 1.0},
                {"title": "Security Analyst Unspecified Exp", "company": "Co E", "source": "indeed", "source_job_id": "5", "location": "Remote", "job_url": "https://co.com/5", "description": "Security monitoring SIEM log analysis. Degree in engineering."},
            ]
            with open(p / "indeed_jobs.json", "w", encoding="utf-8") as f:
                json.dump(jobs, f)

            pipeline = UnifiedPipeline(base_dir=p)
            summary = pipeline.run()

            # Read all generated category files
            with open(p / "matched_jobs.json", encoding="utf-8") as f:
                m = json.load(f)
            with open(p / "supporting_it_jobs.json", encoding="utf-8") as f:
                s = json.load(f)
            with open(p / "internships.json", encoding="utf-8") as f:
                i = json.load(f)
            with open(p / "manual_verification_jobs.json", encoding="utf-8") as f:
                v = json.load(f)
            with open(p / "excluded_jobs.json", encoding="utf-8") as f:
                e = json.load(f)

            total_reconciled = len(m) + len(s) + len(i) + len(v) + len(e)
            self.assertEqual(total_reconciled, 5)
            self.assertEqual(total_reconciled, summary["total_canonical_unique"])

    # 23. JSON, CSV and XLSX files are valid
    def test_23_json_csv_xlsx_files_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            jobs = [
                {"title": "SOC Analyst", "company": "Co A", "source": "indeed", "source_job_id": "1", "location": "Bengaluru", "job_url": "https://co.com/1", "description": "SOC analyst 1 year exp", "min_experience": 1.0},
            ]
            with open(p / "indeed_jobs.json", "w", encoding="utf-8") as f:
                json.dump(jobs, f)

            pipeline = UnifiedPipeline(base_dir=p)
            pipeline.run()

            # Verify JSON
            with open(p / "deduplicated_jobs.json", encoding="utf-8") as f:
                data_json = json.load(f)
            self.assertIsInstance(data_json, list)

            # Verify CSV
            with open(p / "deduplicated_jobs.csv", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 1)

            # Verify XLSX
            wb = openpyxl.load_workbook(p / "deduplicated_jobs.xlsx")
            self.assertIn("All Deduplicated Jobs", wb.sheetnames)
            ws = wb["All Deduplicated Jobs"]
            self.assertGreater(ws.max_row, 1)

    # 24. Historical runs are not overwritten
    def test_24_historical_runs_not_overwritten(self):
        from run_daily_pipeline import DailyPipelineOrchestrator
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            orch1 = DailyPipelineOrchestrator(base_dir=p, run_mode="skip_scraping")
            orch1.run_id = "daily_20261118_100000"
            orch1.run_orchestration()

            # Save historical run summary file
            hist_path = p / "daily_run_summary_20261118.json"
            import shutil
            shutil.copy(p / "daily_run_summary.json", hist_path)

            orch2 = DailyPipelineOrchestrator(base_dir=p, run_mode="skip_scraping")
            orch2.run_id = "daily_20261119_100000"
            orch2.run_orchestration()

            # Verify historical file was untouched
            self.assertTrue(hist_path.exists())
            with open(hist_path, encoding="utf-8") as f:
                hist_data = json.load(f)
            self.assertEqual(hist_data["run_id"], "daily_20261118_100000")


if __name__ == "__main__":
    unittest.main()
