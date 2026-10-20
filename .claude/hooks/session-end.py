#!/usr/bin/env python3
"""
Fluent Session End Hook
Creates daily backups and displays session summary
"""
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fluent_paths import data_dir, ensure_backups_dir, force_utf8_io  # noqa: E402

force_utf8_io()


def main():
    try:
        json.load(sys.stdin)
    except json.JSONDecodeError:
        pass

    backup_dir = ensure_backups_dir() / datetime.now().strftime("%Y%m%d")
    backup_dir.mkdir(parents=True, exist_ok=True)

    data = data_dir()
    if data.exists():
        backed_up = []
        for json_file in data.glob("*.json"):
            try:
                shutil.copy2(json_file, backup_dir / json_file.name)
                backed_up.append(json_file.name)
            except Exception as e:
                print(f"[Fluent] Warning: Could not backup {json_file}: {e}", file=sys.stderr)

        if backed_up:
            print(f"[Fluent] 📦 Session backup created: {backup_dir}/")
            print(f"[Fluent] 💾 Files backed up: {', '.join(backed_up)}")

    profile_path = data / "learner-profile.json"
    if profile_path.exists():
        try:
            with open(profile_path, 'r', encoding='utf-8') as f:
                profile = json.load(f)

            streak = profile.get("current_streak_days", 0)
            total_sessions = profile.get("total_sessions", 0)

            print(f"[Fluent] 🔥 Current streak: {streak} days")
            print(f"[Fluent] 📊 Total sessions: {total_sessions}")
            print(f"[Fluent] 👋 Great work today!")

        except Exception as e:
            print(f"[Fluent] Could not read stats: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
