"""
Unit tests for ATS Cybersecurity Role and Location Filtering Engine.
"""

from __future__ import annotations

import unittest

from ats_filter import is_cybersecurity_role, is_india_or_remote_eligible


class TestATSFilter(unittest.TestCase):
    def test_canonical_roles(self):
        for role in ["SOC Analyst", "SOC Analyst L1", "Cybersecurity Analyst", "Cloud Security Analyst", "SIEM Analyst"]:
            is_sec, matched = is_cybersecurity_role(role)
            self.assertTrue(is_sec, f"Failed for {role}")
            self.assertIsNotNone(matched)

    def test_extended_roles(self):
        titles = [
            "Senior Application Security Engineer",
            "Security Architect - AI Cloud",
            "Detection & Response Engineer",
            "Threat Intelligence Analyst",
            "Infosec Compliance Manager",
            "Lead Cloud Security Engineer",
        ]
        for t in titles:
            is_sec, matched = is_cybersecurity_role(t)
            self.assertTrue(is_sec, f"Failed for {t}")

    def test_negative_exclusions(self):
        non_sec_titles = [
            "Senior Financial Analyst",
            "Equity Research Analyst",
            "Security Guard - Night Shift",
            "Physical Security Supervisor",
            "Loss Prevention Associate",
            "Business Analyst, Marketing",
            "Securities Lending Operations",
        ]
        for t in non_sec_titles:
            is_sec, _ = is_cybersecurity_role(t)
            self.assertFalse(is_sec, f"Expected False for {t}")

    def test_description_fallback_for_ambiguous_titles(self):
        is_sec, _ = is_cybersecurity_role(
            "Associate Analyst",
            "Responsible for SIEM alert triage, EDR telemetry analysis, and security incident response.",
        )
        self.assertTrue(is_sec)

    def test_indian_locations_match(self):
        locs = [
            "Bengaluru, Karnataka, India",
            "Bangalore",
            "Hyderabad, Telangana",
            "Pune, Maharashtra",
            "Gurugram, Haryana",
            "Mumbai",
            "India",
        ]
        for l in locs:
            eligible, std_loc, _ = is_india_or_remote_eligible(l)
            self.assertTrue(eligible, f"Expected eligible for {l}")

    def test_foreign_restricted_remote_excluded(self):
        foreign_locs = [
            ("Remote (US)", "Must reside in the US. US-only.", True),
            ("Remote - Canada", "Eligible to work in Canada.", True),
            ("London, UK", "Onsite role", False),
            ("Remote - EMEA Only", "Must be located in EMEA timezone.", True),
        ]
        for loc, desc, is_rem in foreign_locs:
            eligible, _, _ = is_india_or_remote_eligible(loc, desc, is_remote_flag=is_rem)
            self.assertFalse(eligible, f"Expected not eligible for {loc}")

    def test_india_remote_and_worldwide(self):
        # India remote
        eligible, std_loc, elig = is_india_or_remote_eligible("Remote - India", is_remote_flag=True)
        self.assertTrue(eligible)
        self.assertEqual(elig, "india_only")

        # Worldwide remote
        eligible_ww, _, elig_ww = is_india_or_remote_eligible(
            "Remote", "Join our distributed team from anywhere in the world. Globally remote.", is_remote_flag=True
        )
        self.assertTrue(eligible_ww)
        self.assertEqual(elig_ww, "international")


if __name__ == "__main__":
    unittest.main()

