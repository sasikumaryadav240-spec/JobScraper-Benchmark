"""
Unit tests for URL normalization and tracking parameter removal.
"""

import unittest
from run_jobspy import normalize_url


class TestUrlNormalizer(unittest.TestCase):

    def test_strip_utm_parameters(self):
        url = "https://www.linkedin.com/jobs/view/123456?utm_source=feed&utm_medium=jobboard&utm_campaign=cyber"
        expected = "https://linkedin.com/jobs/view/123456"
        self.assertEqual(normalize_url(url), expected)

    def test_strip_tracking_parameters(self):
        url = "https://in.indeed.com/viewjob?jk=abcdef123456&trk=org&ref=email&fbclid=XYZ"
        expected = "https://in.indeed.com/viewjob?jk=abcdef123456"
        self.assertEqual(normalize_url(url), expected)

    def test_preserve_job_identifying_queries(self):
        url = "https://glassdoor.co.in/job-listing/j?jl=987654321&gclid=123"
        expected = "https://glassdoor.co.in/job-listing/j?jl=987654321"
        self.assertEqual(normalize_url(url), expected)

    def test_remove_trailing_slash_and_www(self):
        url = "https://www.naukri.com/job-listings-soc-analyst-123456/"
        expected = "https://naukri.com/job-listings-soc-analyst-123456"
        self.assertEqual(normalize_url(url), expected)

    def test_empty_and_invalid_urls(self):
        self.assertEqual(normalize_url(""), "")
        self.assertEqual(normalize_url(None), "")
        self.assertEqual(normalize_url("   "), "")
        self.assertEqual(normalize_url("not-a-valid-url"), "not-a-valid-url")


if __name__ == "__main__":
    unittest.main()

