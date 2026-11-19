"""
Unit tests for ATS 3-tier Deduplication Engine.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from ats_dedup import deduplicate_ats_records
from models import JobRecord, WorkMode


def _make_ats_record(
    source: str,
    job_id: str,
    title: str,
    company: str,
    location: str,
    url: str,
    desc: str | None = None,
    min_exp: float | None = None,
    app_url: str | None = None,
) -> JobRecord:
    now_iso = datetime.now(timezone.utc).isoformat()
    return JobRecord(
        source=source,
        source_job_id=job_id,
        collection_method=f"{source}_public_api",
        title=title,
        company=company,
        location=location,
        description=desc,
        job_url=url,
        application_url=app_url,
        min_experience=min_exp,
        work_mode=WorkMode.UNKNOWN,
        date_collected=now_iso,
        source_metadata={"source": source, "source_job_id": job_id},
        matched_keywords=[title],
        matched_locations=[location],
        matched_search_types=["official_ats_board"],
        matched_run_ids=["run_1"],
    )


class TestATSDedup(unittest.TestCase):
    def test_tier1_dedup_by_platform_job_id(self):
        r1 = _make_ats_record("greenhouse", "123", "SOC Analyst", "Netskope", "Bengaluru", "https://boards.greenhouse.io/netskope/jobs/123")
        r2 = _make_ats_record("greenhouse", "123", "SOC Analyst", "Netskope", "Bengaluru", "https://boards.greenhouse.io/netskope/jobs/123?gh_src=1")

        unique, audit = deduplicate_ats_records([r1, r2])
        self.assertEqual(len(unique), 1)
        self.assertEqual(audit.tier1_job_id_merges, 1)
        self.assertEqual(audit.duplicates_removed_total, 1)

    def test_tier2_dedup_by_normalized_url(self):
        r1 = _make_ats_record("lever", "id_a", "Security Engineer", "Meesho", "Bengaluru", "https://jobs.lever.co/meesho/uuid-1")
        r2 = _make_ats_record("lever", "id_b", "Security Engineer", "Meesho", "Bengaluru", "https://jobs.lever.co/meesho/uuid-1?lever-source=linkedin")

        unique, audit = deduplicate_ats_records([r1, r2])
        self.assertEqual(len(unique), 1)
        self.assertEqual(audit.tier2_url_merges, 1)
        self.assertEqual(audit.duplicates_removed_total, 1)

    def test_tier3_dedup_by_content_fingerprint(self):
        r1 = _make_ats_record("ashby", "id_1", "Cloud Security Analyst", "Vanta", "India (Remote)", "https://jobs.ashbyhq.com/vanta/1")
        r2 = _make_ats_record("ashby", "id_2", "Cloud Security Analyst", "Vanta", "India (Remote)", "https://jobs.ashbyhq.com/vanta/2")

        # Clear IDs so Tier 3 is exercised
        r1.source_job_id = None
        r2.source_job_id = None
        r1.source_metadata.pop("source_job_id", None)
        r2.source_metadata.pop("source_job_id", None)

        unique, audit = deduplicate_ats_records([r1, r2])
        self.assertEqual(len(unique), 1)
        self.assertEqual(audit.tier3_content_merges, 1)
        self.assertEqual(audit.duplicates_removed_total, 1)

    def test_distinct_jobs_never_merged(self):
        r1 = _make_ats_record("greenhouse", "101", "SOC Analyst L1", "Netskope", "Bengaluru", "https://boards.greenhouse.io/netskope/jobs/101")
        r2 = _make_ats_record("greenhouse", "102", "Principal Product Manager, AI Security", "Netskope", "Bengaluru", "https://boards.greenhouse.io/netskope/jobs/102")

        unique, audit = deduplicate_ats_records([r1, r2])
        self.assertEqual(len(unique), 2)
        self.assertEqual(audit.duplicates_removed_total, 0)

    def test_backfill_missing_fields_on_merge(self):
        r1 = _make_ats_record("lever", "uid-1", "Security Engineer", "Meesho", "Bengaluru", "https://jobs.lever.co/meesho/uid-1", desc=None, min_exp=None)
        r2 = _make_ats_record("lever", "uid-1", "Security Engineer", "Meesho", "Bengaluru", "https://jobs.lever.co/meesho/uid-1", desc="Complete security responsibilities", min_exp=4.0, app_url="https://jobs.lever.co/meesho/uid-1/apply")

        unique, audit = deduplicate_ats_records([r1, r2])
        self.assertEqual(len(unique), 1)
        merged = unique[0]
        self.assertEqual(merged.description, "Complete security responsibilities")
        self.assertEqual(merged.min_experience, 4.0)
        self.assertEqual(merged.application_url, "https://jobs.lever.co/meesho/uid-1/apply")


if __name__ == "__main__":
    unittest.main()

