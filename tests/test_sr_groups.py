#!/usr/bin/env python3
"""
Unit tests for .claude/hooks/sr_groups.py.

Usage:
    python3 tests/test_sr_groups.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".claude" / "hooks"))
from sr_groups import MAX_ROUND_SIZE, build_rounds, infer_group  # noqa: E402


class InferGroupTest(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(infer_group("article_de_het_huis", {"type": "error_pattern"}), "article_gender")
        self.assertEqual(infer_group("spelling_s_z", {"category": "spelling"}), "spelling")
        self.assertEqual(infer_group("vocab_aardig", {"type": "vocabulary"}), "vocabulary")
        self.assertIsNone(infer_group("modal_infinitive_order", {"type": "error_pattern", "category": "grammar"}))

    def test_stored_group_wins_and_can_opt_out(self):
        self.assertEqual(infer_group("x", {"group": "custom"}), "custom")
        self.assertIsNone(infer_group("article_de_het_huis", {"group": None}))
        self.assertIsNone(infer_group("article_de_het_huis", {"group": ""}))


class BuildRoundsTest(unittest.TestCase):
    def test_round_sits_at_most_urgent_member(self):
        items = {
            "a1": {"group": "g"}, "solo": {}, "a2": {"group": "g"}, "b1": {"group": "h"},
        }
        rounds = build_rounds(["a1", "solo", "a2", "b1"], items)
        self.assertEqual(rounds, [
            {"group": "g", "items": ["a1", "a2"]},
            {"group": None, "items": ["solo"]},
            {"group": "h", "items": ["b1"]},
        ])

    def test_full_round_starts_a_new_one(self):
        ids = [f"n{i}" for i in range(MAX_ROUND_SIZE + 2)]
        rounds = build_rounds(ids, {i: {"group": "g"} for i in ids})
        self.assertEqual([len(r["items"]) for r in rounds], [MAX_ROUND_SIZE, 2])

    def test_empty(self):
        self.assertEqual(build_rounds([], {}), [])


if __name__ == "__main__":
    unittest.main()
