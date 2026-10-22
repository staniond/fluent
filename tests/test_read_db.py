#!/usr/bin/env python3
"""
Tests for .claude/hooks/read-db.py.

Usage:
    python3 tests/test_read_db.py
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".claude" / "hooks" / "read-db.py"

# All in the past so they are due regardless of when the test runs.
ITEMS = {
    "low_old":       {"priority": "low",      "due_date": "2020-01-01"},
    "high_new":      {"priority": "high",     "due_date": "2020-03-01"},
    "high_old":      {"priority": "high",     "due_date": "2020-02-01"},
    "critical":      {"priority": "critical", "due_date": "2020-03-01"},
    "no_priority":   {"due_date": "2020-01-01"},
    "not_due":       {"priority": "critical", "due_date": "2999-01-01"},
}


class ReadDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fluent-test-"))
        data = self.tmp / "data"
        data.mkdir()
        (data / "learner-profile.json").write_text(json.dumps({
            "learner": {"name": "Test"}, "last_updated": "2020-01-01",
            "current_streak_days": 4}))
        items = json.loads(json.dumps(ITEMS))
        items["critical"]["review_history"] = [{"date": f"2020-0{n}-01", "quality": 2} for n in range(1, 6)]
        (data / "spaced-repetition.json").write_text(json.dumps({
            "items": items, "daily_limits": {"review_items_per_day": 3}}))
        (data / "mistakes-db.json").write_text(json.dumps({"error_patterns": {
            "critical": {"category": "grammar", "frequency": 4, "next_review": "x",
                         "examples": [{"incorrect": str(n)} for n in range(5)]}}}))
        for name in ("progress-db", "mastery-db", "session-log"):
            (data / f"{name}.json").write_text("{}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        proc = subprocess.run(["python3", str(SCRIPT), *args], cwd=str(self.tmp), capture_output=True)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        return json.loads(proc.stdout.decode("utf-8"))

    def test_due_items_sorted_by_priority_then_overdue(self):
        out = self._run()
        expected = ["critical", "high_old", "high_new", "no_priority", "low_old"]
        self.assertEqual(out["computed"]["due_review_items"], expected)
        self.assertEqual(out["databases"]["spaced_repetition"]["review_queue"]["today"], expected)

    def test_due_mode_is_compact_and_capped(self):
        out = self._run("--due")
        self.assertNotIn("databases", out)
        self.assertEqual(out["computed"]["due_reviews_count"], 5)
        self.assertEqual(out["computed"]["due_items_shown"], 3)  # daily limit
        self.assertEqual(list(out["due_items"]), ["critical", "high_old", "high_new"])
        self.assertEqual(out["learner"]["name"], "Test")
        self.assertEqual(out["learner"]["current_streak_days"], 4)
        self.assertEqual(out["upcoming"], {"tomorrow": 0, "this_week": 0, "later": 1})

        crit = out["due_items"]["critical"]
        self.assertEqual(len(crit["review_history"]), 3)
        self.assertEqual(crit["review_history"][-1]["date"], "2020-05-01")
        self.assertEqual([e["incorrect"] for e in crit["mistake"]["examples"]], ["3", "4"])
        self.assertNotIn("next_review", crit["mistake"])
        self.assertEqual(crit["mistake"]["frequency"], 4)
        self.assertNotIn("mistake", out["due_items"]["high_old"])


if __name__ == "__main__":
    unittest.main()
