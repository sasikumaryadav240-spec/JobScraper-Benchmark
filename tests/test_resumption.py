"""
Unit tests for checkpoint loading and stateful resumption in run_jobspy.py.
"""

import json
import tempfile
import unittest
from pathlib import Path
from run_jobspy import append_checkpoint_records, load_checkpoint


class TestResumption(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.checkpoint_path = Path(self.temp_dir.name) / "test_checkpoint.jsonl"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_non_existent_checkpoint(self):
        records, completed = load_checkpoint(self.checkpoint_path)
        self.assertEqual(records, [])
        self.assertEqual(completed, set())

    def test_load_valid_checkpoint_with_searches(self):
        test_records = [
            {
                "site": "naukri",
                "title": "SOC Analyst",
                "company": "Infosys",
                "test_keyword": "SOC Analyst",
                "test_location": "Bengaluru",
                "test_is_remote": False,
            },
            {
                "site": "indeed",
                "title": "Security Analyst",
                "company": "Wipro",
                "test_keyword": "SOC Analyst",
                "test_location": "Hyderabad",
                "test_is_remote": False,
            },
            {
                "site": "glassdoor",
                "title": "Cloud Security",
                "company": "RemoteCorp",
                "test_keyword": "Cloud Security Engineer",
                "test_location": "India",
                "test_is_remote": True,
            },
        ]
        with open(self.checkpoint_path, "w", encoding="utf-8") as f:
            for r in test_records:
                f.write(json.dumps(r) + "\n")

        records, completed = load_checkpoint(self.checkpoint_path)
        self.assertEqual(len(records), 3)
        self.assertEqual(len(completed), 3)
        self.assertIn(("SOC Analyst", "Bengaluru", False), completed)
        self.assertIn(("SOC Analyst", "Hyderabad", False), completed)
        self.assertIn(("Cloud Security Engineer", "India", True), completed)

    def test_graceful_handling_of_malformed_checkpoint_lines(self):
        with open(self.checkpoint_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"test_keyword": "SIEM", "test_location": "Pune", "test_is_remote": False}) + "\n")
            f.write("\n")  # Empty line
            f.write("NOT_VALID_JSON\n")  # Malformed line

        # Should load without raising an unhandled exception
        records, completed = load_checkpoint(self.checkpoint_path)
        # The parser catches corrupted files and logs a warning
        self.assertTrue(isinstance(records, list))

    def test_append_checkpoint_records(self):
        new_records = [
            {"title": "Job 1", "site": "indeed"},
            {"title": "Job 2", "site": "glassdoor"},
        ]
        append_checkpoint_records(new_records, self.checkpoint_path)
        self.assertTrue(self.checkpoint_path.exists())

        loaded, _ = load_checkpoint(self.checkpoint_path)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["title"], "Job 1")
        self.assertEqual(loaded[1]["title"], "Job 2")


if __name__ == "__main__":
    unittest.main()

