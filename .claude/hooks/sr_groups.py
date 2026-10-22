#!/usr/bin/env python3
"""
Review groups for spaced repetition.

Items in the same group share one exercise format, so a review session can
ask several of them as a single round ("de or het? werk / boter / huis") that
uses one daily review slot. Each item keeps its own SM-2 schedule and is
graded on its own; grouping only changes how due items are presented.

An item's group is its stored `group` field when present (null or "" opts
out), otherwise inferred from GROUP_RULES. Only add a rule for items whose
exercise is identical in shape; word-order patterns each need their own
sentence and stay ungrouped.
"""

# Largest number of items asked together in one round.
MAX_ROUND_SIZE = 6

# (group, predicate(item_id, item)) — first match wins.
GROUP_RULES = [
    ("article_gender", lambda iid, item: iid.startswith("article_de_het_")),
    ("irregular_participles", lambda iid, item: iid.startswith("irregular_participle_")),
    ("spelling", lambda iid, item: item.get("category") == "spelling"),
    ("vocabulary", lambda iid, item: item.get("type") == "vocabulary"),
]


def infer_group(item_id: str, item: dict):
    """Return the item's review group, or None if it is asked on its own."""
    if "group" in item:
        return item["group"] or None
    for group, matches in GROUP_RULES:
        if matches(item_id, item):
            return group
    return None


def build_rounds(ordered_ids: list, items: dict) -> list:
    """Bundle due items into review rounds, keeping urgency order.

    A round sits where its most urgent member would have been; later members
    of the same group join it until it holds MAX_ROUND_SIZE items, then a new
    round of that group starts. Returns [{"group": str|None, "items": [ids]}].
    """
    rounds = []
    open_round = {}
    for iid in ordered_ids:
        group = infer_group(iid, items[iid])
        if group is None:
            rounds.append({"group": None, "items": [iid]})
            continue
        r = open_round.get(group)
        if r is None or len(r["items"]) >= MAX_ROUND_SIZE:
            r = {"group": group, "items": []}
            rounds.append(r)
            open_round[group] = r
        r["items"].append(iid)
    return rounds
