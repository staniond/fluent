#!/usr/bin/env python3
"""
Fluent DB Update Script
Updates all 6 learning databases from a single JSON session report via stdin.

Usage:
    python3 .claude/hooks/update-db.py <<'EOF'
    { "session_id": "session-005", "date": "2026-04-24", ... }
    EOF

See docs/DB_SCRIPTS.md for the full input schema.

Exit codes: 0=success, 1=validation error, 2=blocking/data error
"""
import copy
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fluent_paths import ensure_data_dir, ensure_backups_dir, force_utf8_io  # noqa: E402
from sr_groups import infer_group  # noqa: E402

force_utf8_io()
DATA_DIR = ensure_data_dir()
BACKUP_DIR = ensure_backups_dir()

# --- Utility functions ---

def load_json(path: Path) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(path: Path, data: dict):
    tmp_path = path.with_suffix('.json.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp_path), str(path))  # atomic + overwrites (os.rename fails on Windows if dest exists)


def parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def date_str(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def tomorrow(today_str: str) -> str:
    return date_str(parse_date(today_str) + timedelta(days=1))


def yesterday(today_str: str) -> str:
    return date_str(parse_date(today_str) - timedelta(days=1))


def date_plus_days(today_str: str, days: int) -> str:
    return date_str(parse_date(today_str) + timedelta(days=days))


def get_week_start(today_str: str) -> str:
    d = parse_date(today_str)
    monday = d - timedelta(days=d.weekday())
    return date_str(monday)


def normalize_milestones(session: dict) -> list:
    """Validate + canonicalize session['milestones'] in place.

    Accepts each entry as either a bare string or an object
    {"milestone": <required non-empty str>, "date": <optional YYYY-MM-DD>}.
    Returns a list of canonical dicts and rewrites session['milestones'] to it.

    Decisions: a milestone's own date is honored (falling back to the session
    date if missing/blank/unparseable); the authoritative top-level session_id
    always wins (any nested session_id is ignored). Malformed entries exit 1
    (validation error) before any DB is touched.

    Each canonical dict carries a private '_achievement_id' key used by
    update_learner_profile; it is harmless because `session` is never persisted
    (only the 6 DBs are written).
    """
    raw = session.get("milestones", [])
    if not raw:
        session["milestones"] = []
        return []
    if not isinstance(raw, list):
        print(f"[Fluent] Error: 'milestones' must be a list, got {type(raw).__name__}", file=sys.stderr)
        sys.exit(1)

    outer_date = session["date"]
    outer_sid = session["session_id"]
    normalized = []

    for i, ms in enumerate(raw):
        if isinstance(ms, str):
            text = ms.strip()
            if not text:
                print(f"[Fluent] Error: milestone at index {i} is an empty string", file=sys.stderr)
                sys.exit(1)
            when = outer_date
        elif isinstance(ms, dict):
            text = ms.get("milestone")
            if not isinstance(text, str) or not text.strip():
                print(f"[Fluent] Error: milestone at index {i} must have a non-empty string 'milestone' field", file=sys.stderr)
                sys.exit(1)
            text = text.strip()
            when = outer_date
            d = ms.get("date")
            if isinstance(d, str) and d.strip():
                try:
                    parse_date(d.strip())
                    when = d.strip()
                except (ValueError, TypeError):
                    when = outer_date  # malformed date falls back to session date
        else:
            print(f"[Fluent] Error: milestone at index {i} must be a string or object, got {type(ms).__name__}", file=sys.stderr)
            sys.exit(1)

        slug = re.sub(r'[^a-z0-9]+', '_', text[:30].lower()).strip('_') or "milestone"
        normalized.append({
            "date": when,
            "milestone": text,
            "session_id": outer_sid,
            "_achievement_id": f"session_{outer_sid}_{i}_{slug}",
        })

    session["milestones"] = normalized
    return normalized


def validate_skill_scores(session: dict):
    """Reject skill_scores that would corrupt cumulative counters.

    Each entry needs integer `exercises` and `correct` with
    0 <= correct <= exercises. A missing `exercises` key used to be read as 0,
    which pushed correct_count above exercises_completed and drove
    incorrect_count negative. Exits 1 before any DB is touched.
    """
    scores = session.get("skill_scores", {})
    if not isinstance(scores, dict):
        print(f"[Fluent] Error: 'skill_scores' must be an object, got {type(scores).__name__}", file=sys.stderr)
        sys.exit(1)
    for skill, s in scores.items():
        if not isinstance(s, dict):
            print(f"[Fluent] Error: skill_scores.{skill} must be an object", file=sys.stderr)
            sys.exit(1)
        ex, cor = s.get("exercises"), s.get("correct")
        if not isinstance(ex, int) or not isinstance(cor, int) or isinstance(ex, bool) or isinstance(cor, bool):
            print(f"[Fluent] Error: skill_scores.{skill} needs integer 'exercises' and 'correct' (got {s})", file=sys.stderr)
            sys.exit(1)
        if not 0 <= cor <= ex:
            print(f"[Fluent] Error: skill_scores.{skill} must satisfy 0 <= correct <= exercises (got correct={cor}, exercises={ex})", file=sys.stderr)
            sys.exit(1)


def backup_all(tag: str):
    backup_path = BACKUP_DIR / tag
    backup_path.mkdir(parents=True, exist_ok=True)
    for f in DATA_DIR.glob("*.json"):
        shutil.copy2(f, backup_path / f.name)


# --- SM-2 Algorithm ---

def calculate_sm2(item: dict, quality: int) -> dict:
    """Classic SM-2. Uses ceil() for interval growth (standard)."""
    import math
    ef = item.get("easiness_factor", 2.5)
    interval = item.get("interval_days", 1)
    reps = item.get("repetitions", 0)

    if quality >= 3:
        if reps == 0:
            interval = 1
        elif reps == 1:
            interval = 6
        else:
            interval = int(math.ceil(interval * ef))
        reps += 1
    else:
        reps = 0
        interval = 1

    ef = ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    ef = max(1.3, ef)

    return {
        "easiness_factor": round(ef, 2),
        "interval_days": interval,
        "repetitions": reps,
    }


# Quality recorded when a known error pattern recurs outside a review.
ERROR_LAPSE_QUALITY = 2


def compute_priority(item: dict) -> str:
    """Priority from the item's own SM-2 state, so it can fall as well as rise.

    critical: failed twice or more in a row
    high:     failed last time, or a leech (easiness factor below 1.8)
    low:      established (mastery >= 3 or interval >= 21 days)
    medium:   everything else
    """
    if item.get("consecutive_incorrect", 0) >= 2:
        return "critical"
    if item.get("last_quality", 3) < 3 or item.get("easiness_factor", 2.5) < 1.8:
        return "high"
    if item.get("mastery_level", 0) >= 3 or item.get("interval_days", 1) >= 21:
        return "low"
    return "medium"


def apply_review(item: dict, quality: int, today: str, score=None, source=None):
    """Grade one recall of an existing SR item: SM-2, counters, mastery,
    priority and history."""
    result = calculate_sm2(item, quality)
    item["easiness_factor"] = result["easiness_factor"]
    item["interval_days"] = result["interval_days"]
    item["repetitions"] = result["repetitions"]
    item["due_date"] = date_plus_days(today, result["interval_days"])
    item["last_reviewed"] = today
    item["last_quality"] = quality
    item["total_reviews"] = item.get("total_reviews", 0) + 1
    if quality >= 3:
        item["consecutive_correct"] = item.get("consecutive_correct", 0) + 1
        item["consecutive_incorrect"] = 0
    else:
        item["consecutive_incorrect"] = item.get("consecutive_incorrect", 0) + 1
        item["consecutive_correct"] = 0
    # Mastery: rough map from repetitions and quality (clamped 0..5)
    current = item.get("mastery_level", 0)
    if item["repetitions"] >= 5 and item["consecutive_correct"] >= 3:
        item["mastery_level"] = min(5, max(current, 3))
    elif item["repetitions"] >= 2 and item["consecutive_correct"] >= 1 and quality >= 4:
        item["mastery_level"] = min(5, current + 1)
    item["priority"] = compute_priority(item)
    entry = {"date": today, "quality": quality}
    if score is not None:
        entry["score"] = score
    if source:
        entry["source"] = source
    item.setdefault("review_history", []).append(entry)


def set_group(item: dict, item_id: str, entry: dict):
    """Store the review group on a new SR item: the payload's `group` if given
    (null/"" = ask on its own), else the sr_groups rule match, if any."""
    group = entry["group"] if "group" in entry else infer_group(item_id, item)
    if "group" in entry or group:
        item["group"] = group


def validate_groups(session: dict):
    """`group` on errors[]/new_vocabulary[] entries must be a string or null.
    Exits 1 before any DB is touched."""
    for key in ("errors", "new_vocabulary"):
        for n, entry in enumerate(session.get(key, [])):
            if isinstance(entry, dict) and "group" in entry and not (
                    entry["group"] is None or isinstance(entry["group"], str)):
                print(f"[Fluent] Error: {key}[{n}].group must be a string or null (got {entry['group']!r})", file=sys.stderr)
                sys.exit(1)


def validate_review_results(session: dict):
    """Check review_results and fill `quality` from `score` when omitted.

    Each entry needs a string item_id and an integer quality 0-5 and/or an
    integer score 0-10 (quality = score // 2). Exits 1 before any DB is
    touched.
    """
    reviews = session.get("review_results", [])
    if not isinstance(reviews, list):
        print(f"[Fluent] Error: 'review_results' must be a list, got {type(reviews).__name__}", file=sys.stderr)
        sys.exit(1)

    def is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    for n, r in enumerate(reviews):
        if not isinstance(r, dict) or not isinstance(r.get("item_id"), str):
            print(f"[Fluent] Error: review_results[{n}] needs a string 'item_id' (got {r!r})", file=sys.stderr)
            sys.exit(1)
        score, quality = r.get("score"), r.get("quality")
        if score is not None and not (is_int(score) and 0 <= score <= 10):
            print(f"[Fluent] Error: review_results[{n}].score must be an integer 0-10 (got {score!r})", file=sys.stderr)
            sys.exit(1)
        if quality is None:
            if score is None:
                print(f"[Fluent] Error: review_results[{n}] ({r['item_id']}) needs 'quality' or 'score'", file=sys.stderr)
                sys.exit(1)
            r["quality"] = score // 2
        elif not (is_int(quality) and 0 <= quality <= 5):
            print(f"[Fluent] Error: review_results[{n}].quality must be an integer 0-5 (got {quality!r})", file=sys.stderr)
            sys.exit(1)


# --- Updater functions ---
# Each mutates in place. Confidence in learner-profile is 0-100 int.
# Session-log preserves the existing rich schema (skills_practiced array,
# score_breakdown, topics_covered, breakthroughs, focus_next_session,
# achievements_earned). Spaced-repetition preserves consecutive_*,
# mastery_level, total_reviews, priority, content, answer, category,
# difficulty fields on existing items.

def update_learner_profile(profile: dict, session: dict):
    today = session["date"]
    last = profile.get("last_updated", "")

    if last == today:
        pass
    elif last == yesterday(today):
        profile["current_streak_days"] = profile.get("current_streak_days", 0) + 1
    else:
        profile["current_streak_days"] = 1

    profile["last_updated"] = today
    profile["total_sessions"] = profile.get("total_sessions", 0) + 1
    profile["total_study_minutes"] = profile.get("total_study_minutes", 0) + session.get("duration_minutes", 0)

    for skill, scores in session.get("skill_scores", {}).items():
        skills = profile.setdefault("skills", {})
        s = skills.setdefault(skill, {
            "current_level": 0, "confidence": 0,
            "last_practiced": None, "total_practice_time": 0,
        })
        s["last_practiced"] = today
        s["total_practice_time"] = s.get("total_practice_time", 0) + scores.get("time_minutes", 0)
        if scores.get("exercises", 0) > 0:
            # confidence is 0-100 int; EWMA against session accuracy (0-100)
            new_acc_pct = (scores["correct"] / scores["exercises"]) * 100
            old_conf = s.get("confidence", 0)
            s["confidence"] = round(old_conf * 0.7 + new_acc_pct * 0.3)
        s["current_level"] = max(s.get("current_level", 0), 1)

    if session.get("focus_areas"):
        profile["focus_areas"] = session["focus_areas"]

    for m in session.get("milestones", []):
        profile.setdefault("achievements", []).append({
            "id": m["_achievement_id"],
            "name": m["milestone"],
            "earned_date": m["date"],
            "description": m["milestone"],
        })


def update_progress_db(progress: dict, session: dict):
    today = session["date"]
    skill_scores = session.get("skill_scores", {})
    total_ex = sum(s.get("exercises", 0) for s in skill_scores.values())
    total_cor = sum(s.get("correct", 0) for s in skill_scores.values())
    accuracy = round(total_cor / total_ex, 3) if total_ex > 0 else 0.0

    stats = progress.setdefault("overall_stats", {
        "total_sessions": 0, "total_exercises": 0, "total_correct": 0,
        "total_incorrect": 0, "accuracy_rate": 0.0,
        "total_study_minutes": 0, "average_session_duration": 0,
    })
    stats["total_sessions"] = stats.get("total_sessions", 0) + 1
    stats["total_exercises"] = stats.get("total_exercises", 0) + total_ex
    stats["total_correct"] = stats.get("total_correct", 0) + total_cor
    stats["total_incorrect"] = stats.get("total_incorrect", 0) + (total_ex - total_cor)
    stats["accuracy_rate"] = round(stats["total_correct"] / stats["total_exercises"], 3) if stats["total_exercises"] > 0 else 0.0
    stats["total_study_minutes"] = stats.get("total_study_minutes", 0) + session.get("duration_minutes", 0)
    stats["average_session_duration"] = round(stats["total_study_minutes"] / stats["total_sessions"])

    trend = progress.setdefault("accuracy_trend", [])
    # Dedup same-day entries: replace if present, else append
    existing = next((t for t in trend if t.get("date") == today), None)
    if existing is not None:
        existing["accuracy"] = accuracy
        existing["exercises"] = existing.get("exercises", 0) + total_ex
    else:
        trend.append({"date": today, "accuracy": accuracy, "exercises": total_ex})

    for skill, scores in skill_scores.items():
        sp = progress.setdefault("skill_progress", {}).setdefault(skill, {
            "sessions": 0, "accuracy": 0.0, "last_practiced": None,
            "exercises_completed": 0, "correct_count": 0, "incorrect_count": 0,
        })
        old_sessions = sp.get("sessions", 0)
        sp["sessions"] = old_sessions + 1
        new_acc = scores["correct"] / scores["exercises"] if scores.get("exercises", 0) > 0 else 0.0
        sp["accuracy"] = round(
            (sp.get("accuracy", 0.0) * old_sessions + new_acc) / sp["sessions"], 3
        )
        sp["last_practiced"] = today
        sp["exercises_completed"] = sp.get("exercises_completed", 0) + scores.get("exercises", 0)
        sp["correct_count"] = sp.get("correct_count", 0) + scores.get("correct", 0)
        sp["incorrect_count"] = sp.get("incorrect_count", 0) + (scores.get("exercises", 0) - scores.get("correct", 0))

    week_start = get_week_start(today)
    weekly = progress.setdefault("weekly_summary", [])
    week_entry = next((w for w in weekly if w.get("week_start") == week_start), None)
    if week_entry is None:
        week_entry = {"week_start": week_start, "sessions": 0, "total_minutes": 0, "accuracy": 0.0}
        weekly.append(week_entry)

    old_s = week_entry.get("sessions", 0)
    week_entry["sessions"] = old_s + 1
    week_entry["total_minutes"] = week_entry.get("total_minutes", 0) + session.get("duration_minutes", 0)
    week_entry["accuracy"] = round(
        (week_entry.get("accuracy", 0.0) * old_s + accuracy) / week_entry["sessions"], 3
    )

    progress.setdefault("metadata", {})["last_updated"] = today


def update_mistakes_db(mistakes: dict, session: dict):
    today = session["date"]
    patterns = mistakes.setdefault("error_patterns", {})

    for error in session.get("errors", []):
        pid = error["pattern_id"]

        if pid in patterns:
            pat = patterns[pid]
            pat["frequency"] = pat.get("frequency", 0) + 1
            pat["last_seen"] = today
            pat["last_occurred"] = today  # legacy alias kept
            pat["next_review"] = tomorrow(today)
            pat["consecutive_incorrect"] = pat.get("consecutive_incorrect", 0) + 1
            pat["consecutive_correct"] = 0
            pat.setdefault("examples", []).append({
                "incorrect": error.get("your_answer", ""),
                "correct": error.get("correct_answer", ""),
                "context": error.get("context", ""),
                "date": today,
            })
            pat["examples"] = pat["examples"][-5:]
            if error.get("notes"):
                pat["notes"] = error["notes"]
        else:
            patterns[pid] = {
                "category": error.get("category", "other"),
                "subcategory": error.get("subcategory", ""),
                "description": error.get("description", ""),
                "severity": error.get("severity", "minor"),
                "frequency": 1,
                "mastery_level": 0,
                "difficulty_score": error.get("difficulty_score", 0.5),
                "last_seen": today,
                "last_occurred": today,
                "next_review": tomorrow(today),
                "consecutive_correct": 0,
                "consecutive_incorrect": 1,
                "examples": [{
                    "incorrect": error.get("your_answer", ""),
                    "correct": error.get("correct_answer", ""),
                    "context": error.get("context", ""),
                    "date": today,
                }],
                "notes": error.get("notes", ""),
            }

    mistakes.setdefault("metadata", {})["last_updated"] = today
    mistakes["metadata"]["total_patterns_tracked"] = len(patterns)


def update_mastery_db(mastery: dict, session: dict, progress: dict):
    today = session["date"]

    for skill, scores in session.get("skill_scores", {}).items():
        s = mastery.setdefault("skills", {}).setdefault(skill, {
            "mastery_level": 0, "confidence_score": 0.0,
            "total_practice_time": 0, "last_practiced": None,
            "practice_count": 0, "avg_accuracy": 0.0,
        })
        s["last_practiced"] = today
        s["total_practice_time"] = s.get("total_practice_time", 0) + scores.get("time_minutes", 0)
        s["practice_count"] = s.get("practice_count", 0) + scores.get("exercises", 0)

        sp = progress.get("skill_progress", {}).get(skill, {})
        acc = sp.get("accuracy", 0)
        sessions = sp.get("sessions", 0)
        s["confidence_score"] = round(acc, 3)
        s["avg_accuracy"] = round(acc, 3)

        if sessions == 0:
            s["mastery_level"] = 0
        elif sessions < 3 or acc < 0.5:
            s["mastery_level"] = max(s.get("mastery_level", 0), 1)
        elif sessions < 5 or acc < 0.65:
            s["mastery_level"] = max(s.get("mastery_level", 0), 2)
        elif sessions < 10 or acc < 0.8:
            s["mastery_level"] = max(s.get("mastery_level", 0), 3)
        elif sessions < 20 or acc < 0.9:
            s["mastery_level"] = max(s.get("mastery_level", 0), 4)
        else:
            s["mastery_level"] = 5

    mastery.setdefault("metadata", {})["last_updated"] = today


def update_spaced_repetition(sr: dict, session: dict):
    today = session["date"]
    items = sr.setdefault("items", {})

    for review in session.get("review_results", []):
        item = items.get(review["item_id"])
        if item is not None:
            apply_review(item, review["quality"], today, review.get("score"))

    for vocab in session.get("new_vocabulary", []):
        item_id = vocab["item_id"]
        if item_id not in items:
            items[item_id] = {
                "id": item_id,
                "type": vocab.get("item_type", "vocabulary"),
                "content": vocab.get("content", ""),
                "answer": vocab.get("answer", ""),
                "category": vocab.get("category", ""),
                "difficulty": vocab.get("difficulty", ""),
                "created_date": today,
                "due_date": tomorrow(today),
                "interval_days": 1,
                "repetitions": 0,
                "easiness_factor": 2.5,
                "consecutive_correct": 0,
                "consecutive_incorrect": 0,
                "last_reviewed": today,
                "last_quality": vocab.get("initial_quality", 3),
                "mastery_level": 0,
                "total_reviews": 0,
                "priority": vocab.get("priority", "medium"),
            }
            set_group(items[item_id], item_id, vocab)

    # A repeat of a known error outside review is a failed recall: pull the
    # item back to tomorrow. Skip items already graded in review_results this
    # session (that grade wins, and counting both would double the lapse),
    # and lapse each item at most once per session.
    graded = {r["item_id"] for r in session.get("review_results", [])}
    lapsed = set()
    for error in session.get("errors", []):
        item_id = error["pattern_id"]
        if item_id in items:
            if "group" in error:
                items[item_id]["group"] = error["group"]
            if item_id not in graded and item_id not in lapsed:
                apply_review(items[item_id], ERROR_LAPSE_QUALITY, today, source="error")
                lapsed.add(item_id)
        else:
            items[item_id] = {
                "id": item_id,
                "type": "error_pattern",
                "content": error.get("your_answer", ""),
                "answer": error.get("correct_answer", ""),
                "category": error.get("category", ""),
                "difficulty": "",
                "created_date": today,
                "due_date": tomorrow(today),
                "interval_days": 1,
                "repetitions": 0,
                "easiness_factor": 2.5,
                "consecutive_correct": 0,
                "consecutive_incorrect": 1,
                "last_reviewed": today,
                "last_quality": 2,
                "mastery_level": 0,
                "total_reviews": 0,
                "priority": "high",
            }
            set_group(items[item_id], item_id, error)

    # Rebuild review queue
    sr["review_queue"] = {"today": [], "tomorrow": [], "this_week": [], "later": []}
    tom = tomorrow(today)
    week_end = date_plus_days(today, 7)
    for item_id, item in items.items():
        due = item.get("due_date", today)
        if due <= today:
            sr["review_queue"]["today"].append(item_id)
        elif due == tom:
            sr["review_queue"]["tomorrow"].append(item_id)
        elif due <= week_end:
            sr["review_queue"]["this_week"].append(item_id)
        else:
            sr["review_queue"]["later"].append(item_id)

    sr.setdefault("metadata", {})["last_updated"] = today
    sr["metadata"]["total_items_tracked"] = len(items)


def sync_mistakes_from_reviews(mistakes: dict, sr: dict, session: dict):
    """Mirror review outcomes of error-pattern items into mistakes-db.

    update_mistakes_db only sees `errors[]`, so a pattern reviewed correctly
    kept a stale next_review and consecutive_incorrect forever. Failed reviews
    are already counted via `errors[]` and are not double-counted here.
    Runs after update_spaced_repetition so due dates are final.
    """
    today = session["date"]
    patterns = mistakes.get("error_patterns", {})
    items = sr.get("items", {})
    errored = {e["pattern_id"] for e in session.get("errors", [])}

    for review in session.get("review_results", []):
        pid = review["item_id"]
        pat = patterns.get(pid)
        if pat is None or pid in errored or review["quality"] < 3:
            continue
        pat["consecutive_correct"] = pat.get("consecutive_correct", 0) + 1
        pat["consecutive_incorrect"] = 0
        pat["last_reviewed"] = today

    # Keep next_review aligned with the SM-2 schedule for every tracked pattern
    for pid, pat in patterns.items():
        due = items.get(pid, {}).get("due_date")
        if due:
            pat["next_review"] = due


def update_session_log(log: dict, session: dict, streak: int):
    """Matches existing schema: skills_practiced (array), score_breakdown,
    topics_covered, breakthroughs, focus_next_session, achievements_earned."""
    today = session["date"]
    skill_scores = session.get("skill_scores", {})
    total_ex = sum(s.get("exercises", 0) for s in skill_scores.values())
    total_cor = sum(s.get("correct", 0) for s in skill_scores.values())

    score_breakdown = {
        skill: round(s["correct"] / s["exercises"], 3) if s.get("exercises", 0) > 0 else 0.0
        for skill, s in skill_scores.items()
    }

    entry = {
        "session_id": session["session_id"],
        "date": today,
        "duration_minutes": session.get("duration_minutes", 0),
        "skills_practiced": session.get("skills_practiced", list(skill_scores.keys())),
        "command_used": session.get("command_used", "/fluent-learn"),
        "exercises_completed": total_ex,
        "accuracy": round(total_cor / total_ex, 3) if total_ex > 0 else 0.0,
        "score_breakdown": score_breakdown,
        "topics_covered": session.get("topics_covered", []),
        "breakthroughs": session.get("breakthroughs", []),
        "focus_next_session": session.get("focus_next_session", session.get("focus_areas", [])),
        "notes": session.get("session_notes", ""),
        "achievements_earned": session.get("achievements_earned", []),
        "streak_day": streak,
    }
    if session.get("exam_focus"):
        entry["exam_focus"] = session["exam_focus"]
    if session.get("critical_errors_identified"):
        entry["critical_errors_identified"] = session["critical_errors_identified"]

    log.setdefault("sessions", []).append(entry)

    for m in session.get("milestones", []):
        log.setdefault("milestones", []).append({
            "date": m["date"],
            "milestone": m["milestone"],
            "session_id": m["session_id"],
        })

    log.setdefault("metadata", {})["total_sessions"] = len(log["sessions"])


# --- Main ---

def main():
    try:
        session = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"[Fluent] Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    for field in ("session_id", "date"):
        if field not in session:
            print(f"[Fluent] Error: Missing required field '{field}'", file=sys.stderr)
            sys.exit(1)

    # Validate + canonicalize milestones before touching any DB (exits 1 on
    # malformed input, so disk stays untouched on a validation failure).
    normalize_milestones(session)
    validate_skill_scores(session)
    validate_review_results(session)
    validate_groups(session)

    session.setdefault("duration_minutes", 0)

    files = {
        "profile": DATA_DIR / "learner-profile.json",
        "progress": DATA_DIR / "progress-db.json",
        "mistakes": DATA_DIR / "mistakes-db.json",
        "mastery": DATA_DIR / "mastery-db.json",
        "sr": DATA_DIR / "spaced-repetition.json",
        "log": DATA_DIR / "session-log.json",
    }

    try:
        originals = {k: load_json(p) for k, p in files.items()}
    except Exception as e:
        print(f"[Fluent] Error loading databases: {e}", file=sys.stderr)
        sys.exit(2)

    # Work on deep copies so a mid-run exception leaves disk untouched.
    data = {k: copy.deepcopy(v) for k, v in originals.items()}

    try:
        update_learner_profile(data["profile"], session)
        update_progress_db(data["progress"], session)
        update_mistakes_db(data["mistakes"], session)
        update_mastery_db(data["mastery"], session, data["progress"])
        update_spaced_repetition(data["sr"], session)
        sync_mistakes_from_reviews(data["mistakes"], data["sr"], session)
        streak = data["profile"].get("current_streak_days", 0)
        update_session_log(data["log"], session, streak)
    except Exception as e:
        import traceback
        print(f"[Fluent] Error updating databases: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(2)

    # Backup originals BEFORE writing new state.
    backup_all(f"pre-update-{session['session_id']}")

    try:
        for k, p in files.items():
            save_json(p, data[k])
    except Exception as e:
        print(f"[Fluent] Error saving databases: {e}", file=sys.stderr)
        sys.exit(2)

    # Summary
    stats = data["progress"]["overall_stats"]
    sr_tomorrow = len(data["sr"]["review_queue"].get("tomorrow", []))
    skill_scores = session.get("skill_scores", {})
    total_ex = sum(s.get("exercises", 0) for s in skill_scores.values())
    total_cor = sum(s.get("correct", 0) for s in skill_scores.values())

    print(f"[Fluent] ✅ Updated 6 databases for session {session['session_id']}")
    print(f"[Fluent] 🔥 Streak: {streak} days | Sessions: {stats['total_sessions']} | Minutes: {stats['total_study_minutes']}")
    if total_ex > 0:
        print(f"[Fluent] 📊 This session: {total_cor}/{total_ex} correct ({round(total_cor/total_ex*100)}%)")
    else:
        print("[Fluent] 📊 No exercises recorded")
    print(f"[Fluent] 📈 Overall accuracy: {stats['accuracy_rate']*100:.0f}% ({stats['total_exercises']} exercises)")
    print(f"[Fluent] 🧠 SR: {data['sr']['metadata']['total_items_tracked']} items tracked, {sr_tomorrow} due tomorrow")
    print(f"[Fluent] 📝 Errors tracked: {data['mistakes']['metadata']['total_patterns_tracked']} patterns")

    sys.exit(0)


if __name__ == "__main__":
    main()
