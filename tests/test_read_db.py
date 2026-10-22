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
        (data / "learner-profile.json").write_text(json.dumps({"last_updated": "2020-01-01"}))
        (data / "spaced-repetition.json").write_text(json.dumps({"items": ITEMS}))
        for name in ("progress-db", "mistakes-db", "mastery-db", "session-log"):
            (data / f"{name}.json").write_text("{}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_due_items_sorted_by_priority_then_overdue(self):
        proc = subprocess.run(["python3", str(SCRIPT)], cwd=str(self.tmp), capture_output=True)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        out = json.loads(proc.stdout.decode("utf-8"))
        expected = ["critical", "high_old", "high_new", "no_priority", "low_old"]
        self.assertEqual(out["computed"]["due_review_items"], expected)
        self.assertEqual(out["databases"]["spaced_repetition"]["review_queue"]["today"], expected)


if __name__ == "__main__":
    unittest.main()
