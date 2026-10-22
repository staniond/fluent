#!/usr/bin/env python3
"""
Fluent Session Start Hook
Displays welcome message with learner stats and due reviews
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fluent_paths import data_dir, force_utf8_io  # noqa: E402

force_utf8_io()


def main():
    # Read hook input from stdin (optional for SessionStart)
    try:
        json.load(sys.stdin)
    except Exception:
        pass

    data = data_dir()
    profile_path = data / "learner-profile.json"

    if not profile_path.exists():
        print("[Fluent] 🌍 Welcome to Fluent - The AI Language Learning Kit!")
        print("[Fluent] 📝 Run /fluent-setup to create your personalized learning profile")
        sys.exit(0)

    try:
        with open(profile_path, 'r', encoding='utf-8') as f:
            profile = json.load(f)

        learner = profile.get("learner", {})
        name = learner.get("name", "Learner")
        target_lang = learner.get("target_language", "your target language")
        current_level = learner.get("current_level", "...")
        target_level = learner.get("target_level", "...")
        streak = profile.get("current_streak_days", 0)

        # The stored streak is only refreshed by update-db.py, so it goes stale
        # once a day is missed. Same rule as read-db.py's streak_active.
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        if profile.get("last_updated", "") not in (today, yesterday):
            streak = 0

        print(f"[Fluent] 🌍 Welcome back, {name}!")
        print(f"[Fluent] 📚 Learning: {target_lang}")
        print(f"[Fluent] 🎯 Level: {current_level} → {target_level}")
        print(f"[Fluent] 🔥 Streak: {streak} {'day' if streak == 1 else 'days'}")

        sr_path = data / "spaced-repetition.json"
        if sr_path.exists():
            try:
                with open(sr_path, 'r', encoding='utf-8') as f:
                    sr_data = json.load(f)

                due_count = 0

                items = sr_data.get("items", {})
                iterable = items.values() if isinstance(items, dict) else items
                for item in iterable:
                    due = item.get("due_date") or item.get("next_review_date", "")
                    if due and due <= today:
                        due_count += 1

                if due_count > 0:
                    print(f"[Fluent] 📅 {due_count} items due for review today - Run /fluent-review!")

            except Exception:
                pass

    except Exception as e:
        print(f"[Fluent] Error loading profile: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
