#!/usr/bin/env python3
"""
Smoke test for .claude/hooks/update-db.py.

Runs the script against a fresh fixture DB in a temp dir, feeds it a sample
session report, and asserts schema invariants on the output files.

Usage:
    python3 tests/test_update_db.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".claude" / "hooks" / "update-db.py"


def make_fixtures(data_dir: Path):
    (data_dir / "learner-profile.json").write_text(json.dumps({
        "learner": {"name": "Test", "target_language": "Dutch",
                    "current_level": "A1", "target_level": "A2"},
        "profile_created": "2026-04-20",
        "last_updated": "2026-04-23",
        "current_streak_days": 2,
        "total_sessions": 1,
        "total_study_minutes": 10,
        "skills": {
            "vocabulary": {"current_level": 1, "confidence": 60,
                           "last_practiced": "2026-04-23",
                           "total_practice_time": 10}
        },
        "focus_areas": [],
        "achievements": [],
        "preferences": {}
    }))
    (data_dir / "progress-db.json").write_text(json.dumps({
        "metadata": {"last_updated": "2026-04-23", "language": "Dutch",
                     "tracking_started": "2026-04-20"},
        "overall_stats": {"total_sessions": 1, "total_exercises": 4,
                          "total_correct": 3, "total_incorrect": 1,
                          "accuracy_rate": 0.75,
                          "total_study_minutes": 10,
                          "average_session_duration": 10},
        "accuracy_trend": [{"date": "2026-04-23", "accuracy": 0.75,
                            "exercises": 4}],
        "skill_progress": {
            "vocabulary": {"sessions": 1, "accuracy": 0.75,
                           "last_practiced": "2026-04-23",
                           "exercises_completed": 4, "correct_count": 3,
                           "incorrect_count": 1}
        },
        "weekly_summary": []
    }))
    (data_dir / "mistakes-db.json").write_text(json.dumps({
        "metadata": {"last_updated": "2026-04-23",
                     "total_patterns_tracked": 0, "language": "Dutch"},
        "error_patterns": {}
    }))
    (data_dir / "mastery-db.json").write_text(json.dumps({
        "metadata": {"last_updated": "2026-04-23", "language": "Dutch"},
        "skills": {
            "vocabulary": {"mastery_level": 1, "confidence_score": 0.75,
                           "total_practice_time": 10,
                           "last_practiced": "2026-04-23",
                           "practice_count": 4, "avg_accuracy": 0.75}
        },
        "patterns": {}
    }))
    (data_dir / "spaced-repetition.json").write_text(json.dumps({
        "metadata": {"algorithm": "SM-2", "last_updated": "2026-04-23",
                     "total_items_tracked": 1, "language": "Dutch"},
        "review_queue": {"today": [], "tomorrow": ["vocab_dag"],
                         "this_week": [], "later": []},
        "items": {
            "vocab_dag": {
                "id": "vocab_dag", "type": "vocabulary", "content": "dag",
                "answer": "day / hi-bye", "category": "greetings",
                "difficulty": "A1", "created_date": "2026-04-23",
                "due_date": "2026-04-24", "interval_days": 1,
                "repetitions": 1, "easiness_factor": 2.5,
                "consecutive_correct": 1, "consecutive_incorrect": 0,
                "last_reviewed": "2026-04-23", "last_quality": 4,
                "mastery_level": 1, "total_reviews": 1, "priority": "medium"
            }
        }
    }))
    (data_dir / "session-log.json").write_text(json.dumps({
        "metadata": {"language": "Dutch", "learner_name": "Test",
                     "total_sessions": 1},
        "sessions": [{
            "session_id": "session-001", "date": "2026-04-23",
            "duration_minutes": 10,
            "skills_practiced": ["vocabulary"],
            "exercises_completed": 4, "accuracy": 0.75,
            "score_breakdown": {"vocabulary": 0.75},
            "topics_covered": [], "breakthroughs": [],
            "focus_next_session": [], "notes": "",
            "achievements_earned": []
        }],
        "milestones": []
    }))


SESSION_PAYLOAD = {
    "session_id": "session-002",
    "date": "2026-04-24",
    "duration_minutes": 15,
    "command_used": "/fluent-learn",
    "skills_practiced": ["vocabulary"],
    "skill_scores": {
        "vocabulary": {"exercises": 5, "correct": 4, "time_minutes": 15}
    },
    "errors": [{
        "pattern_id": "verb_spreek",
        "category": "grammar",
        "subcategory": "verb_conjugation",
        "your_answer": "Hij spreek",
        "correct_answer": "Hij spreekt",
        "context": "3rd person",
        "severity": "critical",
        "difficulty_score": 0.7
    }],
    "new_vocabulary": [{
        "item_id": "het_huis",
        "item_type": "vocabulary",
        "content": "het huis",
        "answer": "the house",
        "category": "nouns",
        "difficulty": "A1",
        "initial_quality": 4
    }],
    "review_results": [{"item_id": "vocab_dag", "quality": 5}],
    "topics_covered": ["house_vocab"],
    "breakthroughs": ["Got 'het huis' on first try"],
    "focus_next_session": ["de/het drill"],
    "session_notes": "Good session.",
    "milestones": []
}


class UpdateDbSmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fluent-test-"))
        (self.tmp / "data").mkdir()
        make_fixtures(self.tmp / "data")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, payload: dict):
        proc = subprocess.run(
            ["python3", str(SCRIPT)],
            input=json.dumps(payload).encode(),
            cwd=str(self.tmp),
            capture_output=True,
        )
        return proc

    def test_happy_path(self):
        proc = self._run(SESSION_PAYLOAD)
        self.assertEqual(proc.returncode, 0,
                         msg=f"stdout={proc.stdout!r} stderr={proc.stderr!r}")

        with open(self.tmp / "data" / "session-log.json") as f:
            log = json.load(f)
        latest = log["sessions"][-1]
        self.assertEqual(latest["session_id"], "session-002")
        self.assertIn("skills_practiced", latest)
        self.assertIsInstance(latest["skills_practiced"], list)
        self.assertIn("score_breakdown", latest)
        self.assertIn("topics_covered", latest)
        self.assertIn("breakthroughs", latest)
        self.assertIn("focus_next_session", latest)
        self.assertIn("achievements_earned", latest)
        self.assertEqual(latest["streak_day"], 3)  # was 2, yesterday -> +1

        with open(self.tmp / "data" / "learner-profile.json") as f:
            profile = json.load(f)
        self.assertEqual(profile["current_streak_days"], 3)
        conf = profile["skills"]["vocabulary"]["confidence"]
        self.assertIsInstance(conf, int)
        self.assertGreaterEqual(conf, 0)
        self.assertLessEqual(conf, 100)

        with open(self.tmp / "data" / "spaced-repetition.json") as f:
            sr = json.load(f)
        dag = sr["items"]["vocab_dag"]
        # Schema preserved
        for k in ("consecutive_correct", "consecutive_incorrect",
                  "mastery_level", "total_reviews", "priority",
                  "content", "answer", "category", "difficulty"):
            self.assertIn(k, dag, f"lost field {k} on vocab_dag")
        self.assertEqual(dag["total_reviews"], 2)  # was 1, +1 review
        self.assertEqual(dag["last_quality"], 5)

        # New vocabulary item fully populated
        huis = sr["items"]["het_huis"]
        for k in ("id", "type", "content", "answer", "category",
                  "difficulty", "due_date", "interval_days", "repetitions",
                  "easiness_factor", "consecutive_correct",
                  "consecutive_incorrect", "mastery_level",
                  "total_reviews", "priority"):
            self.assertIn(k, huis, f"new item missing {k}")

        with open(self.tmp / "data" / "mistakes-db.json") as f:
            mistakes = json.load(f)
        self.assertIn("verb_spreek", mistakes["error_patterns"])
        pat = mistakes["error_patterns"]["verb_spreek"]
        self.assertEqual(pat["consecutive_incorrect"], 1)
        self.assertEqual(pat["examples"][-1]["incorrect"], "Hij spreek")
        self.assertEqual(pat["examples"][-1]["correct"], "Hij spreekt")

        # Backup directory exists (nested inside data/ to avoid collisions
        # with other plugins when the global fallback ~/.claude/fluent-data is used).
        backup = self.tmp / "data" / ".backups" / "pre-update-session-002"
        self.assertTrue(backup.exists(), "pre-update backup missing")

    def test_missing_required_field_exits_1(self):
        proc = self._run({"date": "2026-04-24"})  # no session_id
        self.assertEqual(proc.returncode, 1)

    def test_same_day_does_not_bump_streak(self):
        # Profile last_updated = 2026-04-23; send a session on 2026-04-23.
        payload = dict(SESSION_PAYLOAD)
        payload["session_id"] = "session-003"
        payload["date"] = "2026-04-23"
        proc = self._run(payload)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        with open(self.tmp / "data" / "learner-profile.json") as f:
            profile = json.load(f)
        self.assertEqual(profile["current_streak_days"], 2)

    # --- Milestones (issue #8) ---

    def _payload_with(self, session_id, milestones, date="2026-04-24"):
        payload = dict(SESSION_PAYLOAD)
        payload["session_id"] = session_id
        payload["date"] = date
        payload["milestones"] = milestones
        return payload

    def _load(self, name):
        with open(self.tmp / "data" / name, encoding="utf-8") as f:
            return json.load(f)

    # --- Encoding, skill_scores validation, mistakes sync ---

    def test_non_ascii_payload_survives_stdin(self):
        # Raw UTF-8 bytes on stdin (not \u escapes), as a shell heredoc sends them.
        payload = dict(SESSION_PAYLOAD)
        payload["new_vocabulary"] = [{
            "item_id": "vocab_een", "content": "één — café",
            "answer": "one", "category": "numbers"}]
        proc = subprocess.run(
            ["python3", str(SCRIPT)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            cwd=str(self.tmp), capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        item = self._load("spaced-repetition.json")["items"]["vocab_een"]
        self.assertEqual(item["content"], "één — café")

    def test_skill_scores_missing_exercises_exits_1_no_mutation(self):
        before = self._load("progress-db.json")
        payload = dict(SESSION_PAYLOAD)
        payload["skill_scores"] = {"vocabulary": {"correct": 4, "time_minutes": 5}}
        proc = self._run(payload)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self._load("progress-db.json"), before)

    def test_skill_scores_correct_above_exercises_exits_1(self):
        payload = dict(SESSION_PAYLOAD)
        payload["skill_scores"] = {"vocabulary": {"exercises": 2, "correct": 3}}
        self.assertEqual(self._run(payload).returncode, 1)

    def test_correct_review_of_error_pattern_syncs_mistakes_db(self):
        # First session logs the error; a later review answers it correctly.
        self.assertEqual(self._run(SESSION_PAYLOAD).returncode, 0)
        payload = dict(SESSION_PAYLOAD)
        payload.update(session_id="session-003", date="2026-04-25", errors=[],
                       new_vocabulary=[],
                       review_results=[{"item_id": "verb_spreek", "quality": 4}])
        proc = self._run(payload)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        pat = self._load("mistakes-db.json")["error_patterns"]["verb_spreek"]
        due = self._load("spaced-repetition.json")["items"]["verb_spreek"]["due_date"]
        self.assertEqual(pat["consecutive_correct"], 1)
        self.assertEqual(pat["consecutive_incorrect"], 0)
        self.assertEqual(pat["next_review"], due)
        self.assertEqual(pat["frequency"], 1)  # a correct review is not an error

    # --- SR tracking: error lapses, scores, priority ---

    def _second_session(self, **overrides):
        payload = dict(SESSION_PAYLOAD)
        payload.update(session_id="session-003", date="2026-04-25", errors=[],
                       new_vocabulary=[], review_results=[])
        payload.update(overrides)
        return payload

    def test_repeat_error_reschedules_existing_item(self):
        # vocab_dag is due 2026-04-24 with repetitions=1. Getting it wrong
        # again in a non-review session must pull it back to tomorrow.
        payload = self._second_session(review_results=[], errors=[{
            "pattern_id": "vocab_dag", "category": "vocabulary",
            "your_answer": "x", "correct_answer": "dag"}])
        proc = self._run(payload)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        dag = self._load("spaced-repetition.json")["items"]["vocab_dag"]
        self.assertEqual(dag["due_date"], "2026-04-26")
        self.assertEqual(dag["repetitions"], 0)
        self.assertEqual(dag["consecutive_incorrect"], 1)
        self.assertEqual(dag["consecutive_correct"], 0)
        self.assertEqual(dag["last_quality"], 2)
        self.assertEqual(dag["review_history"][-1]["source"], "error")

    def test_error_and_review_same_item_counts_once(self):
        err = {"pattern_id": "vocab_dag", "category": "vocabulary",
               "your_answer": "x", "correct_answer": "dag"}
        payload = self._second_session(
            errors=[err, err],
            review_results=[{"item_id": "vocab_dag", "quality": 1}])
        self.assertEqual(self._run(payload).returncode, 0)
        dag = self._load("spaced-repetition.json")["items"]["vocab_dag"]
        self.assertEqual(dag["consecutive_incorrect"], 1)
        self.assertEqual(dag["total_reviews"], 2)  # fixture 1 + this review only
        self.assertEqual(dag["last_quality"], 1)

    def test_repeated_errors_lapse_once_per_session(self):
        err = {"pattern_id": "vocab_dag", "category": "vocabulary"}
        self.assertEqual(self._run(self._second_session(errors=[err, err])).returncode, 0)
        dag = self._load("spaced-repetition.json")["items"]["vocab_dag"]
        self.assertEqual(dag["consecutive_incorrect"], 1)

    def test_review_score_recorded_and_quality_derived(self):
        payload = self._second_session(review_results=[{"item_id": "vocab_dag", "score": 7}])
        proc = self._run(payload)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        dag = self._load("spaced-repetition.json")["items"]["vocab_dag"]
        self.assertEqual(dag["last_quality"], 3)
        self.assertEqual(dag["review_history"][-1], {"date": "2026-04-25", "quality": 3, "score": 7})

    def test_review_without_score_omits_it(self):
        payload = self._second_session(review_results=[{"item_id": "vocab_dag", "quality": 4}])
        self.assertEqual(self._run(payload).returncode, 0)
        entry = self._load("spaced-repetition.json")["items"]["vocab_dag"]["review_history"][-1]
        self.assertNotIn("score", entry)

    def test_invalid_review_results_exit_1_no_mutation(self):
        before = self._load("spaced-repetition.json")
        for bad in ({"item_id": "vocab_dag"},
                    {"item_id": "vocab_dag", "quality": 6},
                    {"item_id": "vocab_dag", "score": 11},
                    {"item_id": "vocab_dag", "quality": True},
                    {"quality": 4}):
            with self.subTest(case=bad):
                proc = self._run(self._second_session(review_results=[bad]))
                self.assertEqual(proc.returncode, 1, msg=proc.stderr)
                self.assertEqual(self._load("spaced-repetition.json"), before)

    def test_priority_follows_performance(self):
        # Good review -> medium (fixture had "medium" hard-coded; start from high)
        sr = self._load("spaced-repetition.json")
        sr["items"]["vocab_dag"]["priority"] = "high"
        (self.tmp / "data" / "spaced-repetition.json").write_text(json.dumps(sr))
        self.assertEqual(self._run(self._second_session(
            review_results=[{"item_id": "vocab_dag", "quality": 5}])).returncode, 0)
        self.assertEqual(self._load("spaced-repetition.json")["items"]["vocab_dag"]["priority"], "medium")

        # Two misses in a row -> critical
        for n, date in enumerate(("2026-04-26", "2026-04-27")):
            self.assertEqual(self._run(self._second_session(
                session_id=f"session-01{n}", date=date,
                review_results=[{"item_id": "vocab_dag", "quality": 1}])).returncode, 0)
        self.assertEqual(self._load("spaced-repetition.json")["items"]["vocab_dag"]["priority"], "critical")

    def test_milestone_string_form(self):
        text = "Reached A2 vocabulary milestone"
        proc = self._run(self._payload_with("session-100", [text]))
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)

        log = self._load("session-log.json")
        m = log["milestones"][-1]
        self.assertEqual(m["milestone"], text)
        self.assertEqual(m["date"], "2026-04-24")
        self.assertEqual(m["session_id"], "session-100")

        profile = self._load("learner-profile.json")
        ach = profile["achievements"][-1]
        self.assertEqual(ach["name"], text)
        self.assertEqual(ach["description"], text)
        self.assertEqual(ach["earned_date"], "2026-04-24")
        self.assertTrue(ach["id"].startswith("session_session-100_"))

    def test_milestone_object_form(self):
        ms = {"milestone": "Wrote first paragraph", "date": "2026-04-24",
              "session_id": "session-101"}
        proc = self._run(self._payload_with("session-101", [ms]))
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)

        log = self._load("session-log.json")
        m = log["milestones"][-1]
        self.assertEqual(m["milestone"], "Wrote first paragraph")  # flat string
        self.assertEqual(m["date"], "2026-04-24")
        self.assertEqual(m["session_id"], "session-101")

        profile = self._load("learner-profile.json")
        self.assertEqual(profile["achievements"][-1]["name"], "Wrote first paragraph")

    def test_milestone_object_preserves_own_date(self):
        ms = {"milestone": "Backdated win", "date": "2026-04-20"}
        proc = self._run(self._payload_with("session-102", [ms], date="2026-04-24"))
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)

        log = self._load("session-log.json")
        self.assertEqual(log["milestones"][-1]["date"], "2026-04-20")
        profile = self._load("learner-profile.json")
        self.assertEqual(profile["achievements"][-1]["earned_date"], "2026-04-20")

    def test_milestone_bad_date_falls_back_to_session_date(self):
        ms = {"milestone": "Typo date", "date": "not-a-date"}
        proc = self._run(self._payload_with("session-103", [ms], date="2026-04-24"))
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        log = self._load("session-log.json")
        self.assertEqual(log["milestones"][-1]["date"], "2026-04-24")

    def test_milestone_malformed_exits_1_no_mutation(self):
        bad_cases = [
            {"date": "2026-04-24"},          # missing milestone
            {"milestone": None},
            {"milestone": ""},
            {"milestone": "   "},
            {"milestone": 5},
            42,                              # neither str nor dict
            "",                              # empty string form
        ]
        for n, bad in enumerate(bad_cases):
            with self.subTest(case=bad):
                proc = self._run(self._payload_with(f"session-2{n:02d}", [bad]))
                self.assertEqual(proc.returncode, 1,
                                 msg=f"case={bad!r} stderr={proc.stderr!r}")
                self.assertTrue(proc.stderr, "expected an error message on stderr")
                # No DB mutation: session-log still has its single original session.
                log = self._load("session-log.json")
                self.assertEqual(len(log["sessions"]), 1)
                self.assertEqual(log["milestones"], [])

    def test_milestone_nested_session_id_overridden(self):
        ms = {"milestone": "X", "session_id": "WRONG-999"}
        proc = self._run(self._payload_with("session-104", [ms]))
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        log = self._load("session-log.json")
        self.assertEqual(log["milestones"][-1]["session_id"], "session-104")

    def test_milestone_distinct_achievement_ids(self):
        # Two strings sharing the first 30 chars would slugify identically;
        # the index prefix must keep their IDs distinct.
        prefix = "Mastered the perfect tense fo"  # 29 chars
        ms = [prefix + "r regular verbs", prefix + "r irregular verbs"]
        proc = self._run(self._payload_with("session-105", ms))
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        profile = self._load("learner-profile.json")
        ids = [a["id"] for a in profile["achievements"][-2:]]
        self.assertEqual(len(set(ids)), 2, msg=f"colliding ids: {ids}")

    def test_milestone_non_latin_distinct_nonempty_ids(self):
        # All-non-Latin text slugifies to empty; fallback + index keep IDs valid.
        ms = ["مرحلة أولى", "مرحلة ثانية"]
        proc = self._run(self._payload_with("session-106", ms))
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        profile = self._load("learner-profile.json")
        ids = [a["id"] for a in profile["achievements"][-2:]]
        self.assertEqual(len(set(ids)), 2, msg=f"colliding ids: {ids}")
        for i in ids:
            self.assertFalse(i.endswith("_"), f"bare trailing underscore: {i}")

    def test_milestones_empty_and_omitted_are_noops(self):
        for n, payload in enumerate([
            self._payload_with("session-107", []),
            {k: v for k, v in self._payload_with("session-108", []).items()
             if k != "milestones"},
        ]):
            with self.subTest(n=n):
                before = len(self._load("learner-profile.json").get("achievements", []))
                proc = self._run(payload)
                self.assertEqual(proc.returncode, 0, msg=proc.stderr)
                after = len(self._load("learner-profile.json").get("achievements", []))
                self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
