"""
Unit tests for Greenhouse, Lever, and Ashby platform adapters.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ats_adapters import AshbyAdapter, GreenhouseAdapter, LeverAdapter, html_to_plain_text
from ats_registry import EmployerBoard
from models import WorkMode


class TestATSAdapters(unittest.TestCase):
    def setUp(self):
        self.gh_board = EmployerBoard(
            company_name="Netskope",
            ats_platform="greenhouse",
            board_identifier="netskope",
            board_url="https://boards-api.greenhouse.io/v1/boards/netskope/jobs?content=true",
            country_or_market="India",
            verification_timestamp="2026-11-19T00:00:00Z",
            discovery_source="test",
        )
        self.lever_board = EmployerBoard(
            company_name="Meesho",
            ats_platform="lever",
            board_identifier="meesho",
            board_url="https://api.lever.co/v0/postings/meesho?mode=json",
            country_or_market="India",
            verification_timestamp="2026-11-19T00:00:00Z",
            discovery_source="test",
        )
        self.ashby_board = EmployerBoard(
            company_name="Vanta",
            ats_platform="ashby",
            board_identifier="vanta",
            board_url="https://api.ashbyhq.com/posting-api/job-board/vanta",
            country_or_market="Global",
            verification_timestamp="2026-11-19T00:00:00Z",
            discovery_source="test",
        )

    def test_html_to_plain_text(self):
        raw_html = "<div class='job'><h2>About the Role</h2><p>Minimum 3 years of <b>SOC</b> experience.</p></div>"
        text = html_to_plain_text(raw_html)
        self.assertIn("About the Role", text)
        self.assertIn("Minimum 3 years of SOC experience.", text)
        self.assertNotIn("<h2>", text)

    def test_greenhouse_parse_record_success(self):
        adapter = GreenhouseAdapter()
        raw_job = {
            "id": 1234567,
            "title": "Information Security Analyst",
            "updated_at": "2026-11-18T12:00:00Z",
            "absolute_url": "https://boards.greenhouse.io/netskope/jobs/1234567",
            "location": {"name": "Bengaluru, Karnataka, India"},
            "content": "<p>Responsible for SIEM monitoring. Minimum 3-5 years of experience required. Hybrid work arrangement.</p>",
            "departments": [{"name": "Security Operations"}],
        }
        rec = adapter.parse_record(raw_job, self.gh_board, "run_test")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.title, "Information Security Analyst")
        self.assertEqual(rec.company, "Netskope")
        self.assertEqual(rec.source, "greenhouse")
        self.assertEqual(rec.source_job_id, "1234567")
        self.assertEqual(rec.min_experience, 3.0)
        self.assertEqual(rec.max_experience, 5.0)
        self.assertEqual(rec.work_mode, WorkMode.HYBRID)
        self.assertEqual(rec.job_url, "https://boards.greenhouse.io/netskope/jobs/1234567")

    def test_greenhouse_parse_record_non_security_filtered(self):
        adapter = GreenhouseAdapter()
        raw_job = {
            "id": 9999,
            "title": "Senior Financial Analyst",
            "location": {"name": "Bengaluru, India"},
            "content": "<p>Financial reporting and budget planning.</p>",
        }
        rec = adapter.parse_record(raw_job, self.gh_board, "run_test")
        self.assertIsNone(rec)

    def test_lever_parse_record_success(self):
        adapter = LeverAdapter()
        raw_job = {
            "id": "lever-uuid-456",
            "text": "Cybersecurity Analyst",
            "createdAt": 1731931200000,
            "hostedUrl": "https://jobs.lever.co/meesho/lever-uuid-456",
            "applyUrl": "https://jobs.lever.co/meesho/lever-uuid-456/apply",
            "categories": {
                "location": "Bengaluru, Karnataka",
                "commitment": "Full-time",
                "team": "Information Security",
            },
            "workplaceType": "hybrid",
            "descriptionPlain": "We are seeking a Cybersecurity Analyst. Requires minimum 4 years of experience.",
        }
        rec = adapter.parse_record(raw_job, self.lever_board, "run_test")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.title, "Cybersecurity Analyst")
        self.assertEqual(rec.company, "Meesho")
        self.assertEqual(rec.source, "lever")
        self.assertEqual(rec.source_job_id, "lever-uuid-456")
        self.assertEqual(rec.work_mode, WorkMode.HYBRID)
        self.assertEqual(rec.employment_type, "Full-time")
        self.assertEqual(rec.min_experience, 4.0)

    def test_ashby_parse_record_success(self):
        adapter = AshbyAdapter()
        raw_job = {
            "id": "ashby-uuid-789",
            "title": "Cloud Security Analyst",
            "publishedAt": "2026-11-18T10:30:00.000Z",
            "jobUrl": "https://jobs.ashbyhq.com/vanta/ashby-uuid-789",
            "applyUrl": "https://jobs.ashbyhq.com/vanta/ashby-uuid-789/application",
            "location": "India (Remote)",
            "isRemote": True,
            "employmentType": "FullTime",
            "descriptionPlain": "Join Vanta as a Cloud Security Analyst. Fully remote role within India. Requires 2 to 5 years experience.",
        }
        rec = adapter.parse_record(raw_job, self.ashby_board, "run_test")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.title, "Cloud Security Analyst")
        self.assertEqual(rec.company, "Vanta")
        self.assertEqual(rec.source, "ashby")
        self.assertEqual(rec.source_job_id, "ashby-uuid-789")
        self.assertEqual(rec.work_mode, WorkMode.REMOTE)
        self.assertEqual(rec.remote_eligibility, "india_only")
        self.assertEqual(rec.min_experience, 2.0)
        self.assertEqual(rec.max_experience, 5.0)

    @patch("requests.Session.get")
    def test_fetch_raw_board_http_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = Exception("404 Client Error: Not Found")
        mock_get.return_value = mock_resp

        adapter = GreenhouseAdapter()
        with self.assertRaises(Exception):
            adapter.fetch_raw_board(self.gh_board)


if __name__ == "__main__":
    unittest.main()

