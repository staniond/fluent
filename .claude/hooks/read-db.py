#!/usr/bin/env python3
"""
Fluent DB Reader Script
Loads all 6 learning databases and outputs a single JSON object to stdout.

Usage:
    python3 .claude/hooks/read-db.py          # everything
    python3 .claude/hooks/read-db.py --due    # compact: today's reviews only

Exit codes: 0=success, 1=partial (some files missing), 2=critical error
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fluent_paths import data_dir, force_utf8_io  # noqa: E402
from sr_groups import build_rounds  # noqa: E402

force_utf8_io()
DATA_DIR = data_dir()

FILES = {
    "learner_profile": DATA_DIR / "learner-profile.json",
    "progress_db": DATA_DIR / "progress-db.json",
    "mistakes_db": DATA_DIR / "mistakes-db.json",
    "mastery_db": DATA_DIR / "mastery-db.json",
    "spaced_repetition": DATA_DIR / "spaced-repetition.json",
    "session_log": DATA_DIR / "session-log.json",
}


def load_json(path: Path):
    if not path.exists():
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def next_session_id(sessions: list) -> str:
    """Produce 'session-NNN' matching existing id convention.
    Falls back to 'session-001' on empty log or unparseable last id."""
    if not sessions:
        return "session-001"
    last_id = sessions[-1].get("session_id", "")
    m = re.search(r'(\d+)', last_id)
    if m:
        return f"session-{int(m.group(1)) + 1:03d}"
    return f"session-{len(sessions) + 1:03d}"


# Review-history entries / mistake examples kept per item in --due output.
DUE_HISTORY_KEEP = 3
DUE_EXAMPLES_KEEP = 2
# mistakes-db fields that duplicate the SR item (or are legacy aliases).
DUE_MISTAKE_DROP = ("category", "last_occurred", "next_review")


def due_view(databases: dict, computed: dict, queue: dict) -> dict:
    """Compact view for a review session: learner basics, today's review
    rounds (capped at the daily limit; a grouped round uses one slot), the
    items in them with their mistakes-db entries, and counts for the rest of
    the schedule. A few KB instead of the full dump."""
    profile = databases.get("learner_profile", {})
    sr = databases.get("spaced_repetition", {})
    items = sr.get("items", {})
    patterns = databases.get("mistakes_db", {}).get("error_patterns", {})
    limits = sr.get("daily_limits", {})
    rounds = computed["due_rounds"][:limits.get("review_items_per_day", 20)]
    shown = [iid for r in rounds for iid in r["items"]]

    due = {}
    for iid in shown:
        item = dict(items[iid])
        item["review_history"] = item.get("review_history", [])[-DUE_HISTORY_KEEP:]
        pat = patterns.get(iid)
        if pat is not None:
            pat = {k: v for k, v in pat.items() if k not in DUE_MISTAKE_DROP}
            pat["examples"] = pat.get("examples", [])[-DUE_EXAMPLES_KEEP:]
            item["mistake"] = pat
        due[iid] = item

    return {
        "computed": dict(computed, due_items_shown=len(shown)),
        "rounds": rounds,
        "learner": dict(
            profile.get("learner", {}),
            current_streak_days=profile.get("current_streak_days", 0),
            last_updated=profile.get("last_updated"),
            total_sessions=profile.get("total_sessions", 0),
        ),
        "daily_limits": limits,
        "due_items": due,
        "upcoming": {k: len(v) for k, v in queue.items() if k != "today"},
    }


def main():
    parser = argparse.ArgumentParser(description="Load the Fluent learner databases.")
    parser.add_argument("--due", action="store_true",
                        help="compact output with only today's due reviews")
    args = parser.parse_args()

    databases = {}
    missing = []

    for key, path in FILES.items():
        data = load_json(path)
        if data is None:
            missing.append(str(path))
            databases[key] = {}
        else:
            databases[key] = data

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    sr = databases.get("spaced_repetition", {})
    items = sr.get("items", {})
    # Most urgent first: priority, then most overdue (earliest due_date).
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    due_items = sorted(
        (iid for iid, item in items.items() if item.get("due_date", "") <= today),
        key=lambda iid: (rank.get(items[iid].get("priority"), 2), items[iid].get("due_date", "")),
    )

    # The stored review_queue is bucketed relative to the day update-db.py last
    # ran, so by the next morning "today" is empty. Re-bucket against the real
    # date (in the output only; disk is untouched).
    queue = {"today": [], "tomorrow": [], "this_week": [], "later": []}
    if items:
        tom = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        week_end = (now + timedelta(days=7)).strftime("%Y-%m-%d")
        for iid, item in items.items():
            due = item.get("due_date", today)
            if due <= today:
                queue["today"].append(iid)
            elif due == tom:
                queue["tomorrow"].append(iid)
            elif due <= week_end:
                queue["this_week"].append(iid)
            else:
                queue["later"].append(iid)
        queue["today"] = due_items
        sr["review_queue"] = queue

    log = databases.get("session_log", {})
    sessions = log.get("sessions", [])

    profile = databases.get("learner_profile", {})
    last_updated = profile.get("last_updated", "")
    streak_active = last_updated in (today, yesterday)
    try:
        days_since = (now - datetime.strptime(last_updated, "%Y-%m-%d")).days if last_updated else None
    except ValueError:
        days_since = None

    computed = {
        "today": today,
        "due_reviews_count": len(due_items),
        "due_review_items": due_items,
        "due_rounds": build_rounds(due_items, items),
        "next_session_id": next_session_id(sessions),
        "streak_active": streak_active,
        "days_since_last_session": days_since,
    }
    if args.due:
        result = due_view(databases, computed, queue)
    else:
        result = {"databases": databases, "computed": computed}

    if missing:
        result["_warnings"] = [f"Missing file: {m}" for m in missing]

    # --due is compact JSON: it exists to stay small.
    json.dump(result, sys.stdout, indent=None if args.due else 2, ensure_ascii=False)
    print()

    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
