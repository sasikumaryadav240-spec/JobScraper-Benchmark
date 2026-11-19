"""
Unit tests for LinkedIn 3-tier deduplication engine.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from models import JobRecord, WorkMode
from linkedin_dedup import (
    build_linkedin_content_fingerprint,
    deduplicate_linkedin_records,
    merge_linkedin_records,
)


def _make_record(
    job_id: str,
    title: str,
    company: str,
    location: str,
    url: str,
    desc: str | None = None,
    min_exp: float | None = None,
    max_exp: float | None = None,
    app_url: str | None = None,
    work_mode: WorkMode = WorkMode.UNKNOWN,
    keyword: str = "SOC Analyst",
) -> JobRecord:
    now = datetime.now(timezone.utc)
    return JobRecord(
        job_id=job_id,
        source="linkedin",
        title=title,
        company=company,
        location=location,
        description=desc,
        job_url=url,
        application_url=app_url,
        min_experience=min_exp,
        max_experience=max_exp,
        work_mode=work_mode,
        collected_at=now,
        source_metadata={"source": "linkedin", "source_job_id": job_id},
        matched_keywords=[keyword],
        matched_locations=[location],
        matched_search_types=["city"],
        matched_run_ids=["run_1"],
    )


class TestLinkedInDedup(unittest.TestCase):
    def test_tier1_dedup_by_source_job_id(self):
        r1 = _make_record("4492063072", "SOC Analyst", "CGI", "Bengaluru", "https://www.linkedin.com/jobs/view/4492063072", keyword="SOC Analyst")
        r2 = _make_record("4492063072", "SOC Analyst", "CGI", "Bengaluru", "https://www.linkedin.com/jobs/view/4492063072?trk=other", keyword="SIEM Analyst")

        unique, audit = deduplicate_linkedin_records([r1, r2])
        self.assertEqual(len(unique), 1)
        self.assertEqual(audit.tier1_job_id_merges, 1)
        self.assertEqual(audit.duplicates_removed_total, 1)
        self.assertIn("SOC Analyst", unique[0].matched_keywords)
        self.assertIn("SIEM Analyst", unique[0].matched_keywords)

    def test_tier2_dedup_by_normalized_url(self):
        r1 = _make_record("id_1", "Security Engineer", "Infosys", "Hyderabad", "https://www.linkedin.com/jobs/view/999888777?trackingId=1")
        r2 = _make_record("id_2", "Security Engineer", "Infosys", "Hyderabad", "https://www.linkedin.com/jobs/view/999888777?refId=2")

        unique, audit = deduplicate_linkedin_records([r1, r2])
        self.assertEqual(len(unique), 1)
        self.assertEqual(audit.tier2_url_merges, 1)
        self.assertEqual(audit.duplicates_removed_total, 1)

    def test_tier3_dedup_by_content_fingerprint(self):
        # Different IDs and different tracking URLs, but same company, title, location
        r1 = _make_record("id_a", "Incident Response Analyst", "TCS", "Pune", "https://www.linkedin.com/jobs/view/111111")
        r2 = _make_record("id_b", "Incident Response Analyst", "TCS", "Pune", "https://www.linkedin.com/jobs/view/222222")

        # Give r1 and r2 no source_job_id so Tier 3 is exercised
        r1.source_metadata.pop("source_job_id", None)
        r2.source_metadata.pop("source_job_id", None)
        r1.job_url = "https://example.com/unique_a"
        r2.job_url = "https://example.com/unique_b"

        unique, audit = deduplicate_linkedin_records([r1, r2])
        self.assertEqual(len(unique), 1)
        self.assertEqual(audit.tier3_content_merges, 1)
        self.assertEqual(audit.duplicates_removed_total, 1)

    def test_distinct_jobs_never_merged(self):
        r1 = _make_record("4491001", "SOC Analyst L1", "EY", "Bengaluru", "https://www.linkedin.com/jobs/view/4491001")
        r2 = _make_record("4491002", "Senior SOC Lead", "EY", "Bengaluru", "https://www.linkedin.com/jobs/view/4491002")

        unique, audit = deduplicate_linkedin_records([r1, r2])
        self.assertEqual(len(unique), 2)
        self.assertEqual(audit.duplicates_removed_total, 0)

    def test_backfill_missing_fields_on_merge(self):
        r1 = _make_record("4493001", "Cloud Security Analyst", "Wipro", "Chennai", "https://www.linkedin.com/jobs/view/4493001", desc=None, min_exp=None)
        r2 = _make_record("4493001", "Cloud Security Analyst", "Wipro", "Chennai", "https://www.linkedin.com/jobs/view/4493001", desc="Full cloud security job description", min_exp=3.0, max_exp=6.0, app_url="https://wipro.com/apply/123", work_mode=WorkMode.HYBRID)

        unique, audit = deduplicate_linkedin_records([r1, r2])
        self.assertEqual(len(unique), 1)
        merged = unique[0]
        self.assertEqual(merged.description, "Full cloud security job description")
        self.assertEqual(merged.min_experience, 3.0)
        self.assertEqual(merged.max_experience, 6.0)
        self.assertEqual(merged.application_url, "https://wipro.com/apply/123")
        self.assertEqual(merged.work_mode, WorkMode.HYBRID)


if __name__ == "__main__":
    unittest.main()

