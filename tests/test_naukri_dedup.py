"""
Unit tests for Naukri deduplication engine.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from models import FieldOrigin, JobRecord, QualityIndicators, VerificationStatus, WorkMode
from naukri_dedup import deduplicate_naukri_records


def _make_dummy_record(
    job_id: str | None,
    title: str = "SOC Analyst",
    company: str = "CyberCorp",
    location: str = "Bengaluru",
    url: str | None = "https://www.naukri.com/job-listings-soc-analyst-123",
    description: str | None = None,
    min_exp: float | None = None,
) -> JobRecord:
    now_str = datetime.now(timezone.utc).isoformat()
    field_origins = {
        "title": FieldOrigin.SOURCE_PROVIDED,
        "company": FieldOrigin.SOURCE_PROVIDED,
        "location": FieldOrigin.SOURCE_PROVIDED,
        "description": FieldOrigin.SOURCE_PROVIDED if description else FieldOrigin.UNKNOWN,
        "experience": FieldOrigin.SOURCE_PROVIDED if min_exp is not None else FieldOrigin.UNKNOWN,
    }
    rec = JobRecord(
        source="naukri",
        source_job_id=job_id,
        title=title,
        company=company,
        location=location,
        country="India",
        work_mode=WorkMode.ONSITE,
        description=description,
        min_experience=min_exp,
        date_posted=now_str,
        date_collected=now_str,
        job_url=url,
        verification_status=VerificationStatus.UNVERIFIED,
        collection_method="test",
        source_metadata={"test": True},
        field_origins=field_origins,
        deduplication_key=url or f"{company}:{title}:{location}",
        matched_keywords=[title],
        matched_locations=[location],
        matched_run_ids=["test_run"],
    )
    rec.quality_indicators = rec.compute_quality_indicators()
    return rec


class TestNaukriDedup(unittest.TestCase):
    def test_tier1_dedup_by_source_job_id(self):
        rec1 = _make_dummy_record("1001", url="https://www.naukri.com/job-listings-1?src=srp")
        rec2 = _make_dummy_record("1001", url="https://www.naukri.com/job-listings-1?src=discovery")
        unique, audit = deduplicate_naukri_records([rec1, rec2])

        self.assertEqual(len(unique), 1)
        self.assertEqual(audit.tier1_job_id_merges, 1)
        self.assertEqual(audit.duplicates_removed_total, 1)

    def test_tier2_dedup_by_normalized_url(self):
        # Two records with different IDs but exact same normalized job URL
        rec1 = _make_dummy_record("1001", url="https://www.naukri.com/job-listings-soc-analyst-1001?utm_source=feed")
        rec2 = _make_dummy_record(None, url="https://www.naukri.com/job-listings-soc-analyst-1001?tracking=abc")
        unique, audit = deduplicate_naukri_records([rec1, rec2])

        self.assertEqual(len(unique), 1)
        self.assertEqual(audit.tier2_url_merges, 1)

    def test_tier3_dedup_by_content_fingerprint(self):
        # Two records without URLs or IDs, matching normalized company, title, location
        rec1 = _make_dummy_record(None, title="Senior Security Analyst", company="Acme Infosec", location="Bengaluru", url=None)
        rec2 = _make_dummy_record(None, title="Senior Security Analyst", company="Acme Infosec", location="Bengaluru", url=None)
        unique, audit = deduplicate_naukri_records([rec1, rec2])

        self.assertEqual(len(unique), 1)
        self.assertEqual(audit.tier3_content_merges, 1)

    def test_distinct_jobs_never_merged(self):
        # Same company & title but different locations must NOT be merged
        rec1 = _make_dummy_record("1001", title="SOC Analyst", company="Wipro", location="Bengaluru", url="https://www.naukri.com/job-1001")
        rec2 = _make_dummy_record("1002", title="SOC Analyst", company="Wipro", location="Hyderabad", url="https://www.naukri.com/job-1002")
        unique, audit = deduplicate_naukri_records([rec1, rec2])

        self.assertEqual(len(unique), 2)
        self.assertEqual(audit.duplicates_removed_total, 0)

    def test_backfill_missing_fields_on_merge(self):
        rec1 = _make_dummy_record("1001", description=None, min_exp=None)
        rec2 = _make_dummy_record("1001", description="Detailed SOC Analyst duties", min_exp=3.0)
        unique, audit = deduplicate_naukri_records([rec1, rec2])

        self.assertEqual(len(unique), 1)
        merged = unique[0]
        self.assertEqual(merged.description, "Detailed SOC Analyst duties")
        self.assertEqual(merged.min_experience, 3.0)


if __name__ == "__main__":
    unittest.main()
