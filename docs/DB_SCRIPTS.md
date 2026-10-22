# Database Helper Scripts

Two Python scripts under `.claude/hooks/` manage the six learner databases:

| Script | Purpose |
|--------|---------|
| `read-db.py` | Load all 6 databases (+ computed fields) in one call; `--due` for today's reviews only |
| `update-db.py` | Apply a session report to all 6 databases atomically |

Both scripts resolve the data directory internally (see `fluent_paths.data_dir()`)
and produce identical output regardless of CWD. Always invoke them with the
`${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-.}}` prefix so the script path
itself resolves whether Fluent is installed as a plugin (CWD is the data
directory) or cloned (CWD is the repo root).

## Reading

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-.}}/.claude/hooks/read-db.py"
```

Outputs a single JSON object:

```json
{
  "databases": {
    "learner_profile": { ... },
    "progress_db": { ... },
    "mistakes_db": { ... },
    "mastery_db": { ... },
    "spaced_repetition": { ... },
    "session_log": { ... }
  },
  "computed": {
    "today": "2026-04-24",
    "due_reviews_count": 3,
    "due_review_items": ["vocab_dag", ...],
    "next_session_id": "session-005",
    "streak_active": true,
    "days_since_last_session": 1
  }
}
```

Exit codes: `0` OK, `1` one or more files missing (partial result with
`_warnings`), `2` critical error.

### `--due`: today's reviews only

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-.}}/.claude/hooks/read-db.py" --due
```

The full output grows with every session (150KB+ after ~20 sessions). `--due`
prints compact JSON with just what a review session needs:

```json
{
  "computed": { "...same as above...": "", "due_items_shown": 14 },
  "learner": { "name": "...", "target_language": "...", "current_streak_days": 1, "last_updated": "...", "total_sessions": 21 },
  "daily_limits": { "review_items_per_day": 20, "new_items_per_day": 10 },
  "due_items": {
    "<item_id>": { "...SR item fields...": "", "review_history": ["last 3"],
                   "mistake": { "...mistakes-db entry...": "", "examples": ["last 2"] } }
  },
  "upcoming": { "tomorrow": 14, "this_week": 18, "later": 23 }
}
```

`due_items` is ordered most urgent first (priority, then oldest `due_date`) and
capped at `review_items_per_day`; `computed.due_review_items` still lists every
due id. `mistake` is present only for items tracked in mistakes-db, without the
fields that duplicate the SR item (`category`, `next_review`, `last_occurred`).

## Writing

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-.}}/.claude/hooks/update-db.py" <<'EOF'
{
  "session_id": "session-005",
  "date": "2026-04-24",
  "duration_minutes": 20,
  "command_used": "/fluent-learn",
  "skills_practiced": ["vocabulary", "writing"],
  "skill_scores": {
    "vocabulary": { "exercises": 5, "correct": 4, "time_minutes": 10 },
    "writing":    { "exercises": 3, "correct": 3, "time_minutes": 10 }
  },
  "errors": [
    {
      "pattern_id": "verb_conjugation_3rd_person",
      "category": "grammar",
      "subcategory": "verb_conjugation",
      "your_answer": "Hij spreek",
      "correct_answer": "Hij spreekt",
      "context": "3rd person singular",
      "difficulty_score": 0.7,
      "severity": "critical",
      "notes": "optional free text"
    }
  ],
  "new_vocabulary": [
    {
      "item_id": "het_huis",
      "item_type": "vocabulary",
      "content": "het huis",
      "answer": "the house",
      "category": "essential_nouns",
      "difficulty": "A1",
      "initial_quality": 4,
      "priority": "medium"
    }
  ],
  "review_results": [
    { "item_id": "vocab_dag", "score": 9 }
  ],
  "topics_covered": ["articles", "house_vocabulary"],
  "breakthroughs": ["First correct use of 'het' vs 'de'"],
  "focus_next_session": ["Drill de/het article gender"],
  "session_notes": "Strong session. Article gender still tricky.",
  "achievements_earned": [],
  "milestones": [
    { "milestone": "Reached 100 words learned", "date": "2026-04-24" },
    "Completed first full conversation"
  ]
}
EOF
```

### Required fields
- `session_id` (string, conventionally `session-NNN`)
- `date` (YYYY-MM-DD)

Everything else is optional; omitted fields do not update.

### Side effects
- Backs up `data/*.json` to `.backups/pre-update-<session_id>/` *before*
  writing.
- Writes each JSON file via a `.tmp` + `fsync` + `rename` pattern so a crash
  mid-write cannot leave a half-written file.
- Rebuilds `spaced-repetition.review_queue` from scratch each run — any manual
  edits there will be overwritten.
- `review_results[]` entries take `score` (0–10, `quality = score // 2` when
  `quality` is omitted) and/or `quality` (0–5). The score is kept in the
  item's `review_history`. A missing or out-of-range value exits `1`.
- An `errors[]` entry whose `pattern_id` is an existing SR item not graded in
  `review_results[]` this session counts as a failed recall (quality 2): the
  item is due tomorrow again. Repeats within one session lapse it once.
- Each graded item's `priority` is recomputed: `critical` after 2+ misses in
  a row, `high` after a miss or with easiness factor < 1.8, `low` once
  mastery ≥ 3 or interval ≥ 21 days, otherwise `medium`. `read-db.py` lists
  due items by priority, then most overdue first.

### Exit codes
- `0` success
- `1` validation error (bad/missing JSON, missing required field)
- `2` I/O or logic error (full traceback on stderr; no files were modified)

## Data model notes

- `learner-profile.json` stores `confidence` per skill as an integer 0–100.
- `progress-db.json` stores `accuracy` values as floats 0.0–1.0.
- `session-log.json` sessions use `skills_practiced` (array), `score_breakdown`
  (per-skill float accuracy), `topics_covered`, `breakthroughs`,
  `focus_next_session`, `achievements_earned`. Session IDs are
  `session-NNN`.
- `spaced-repetition.json` items preserve `consecutive_correct/incorrect`,
  `mastery_level`, `total_reviews`, `priority`, `content`, `answer`,
  `category`, `difficulty` — supply these in `new_vocabulary` payloads so new
  items are fully populated.
- `milestones[]` accepts **either** a bare string **or** an object
  `{ "milestone": <required non-empty string>, "date": <optional YYYY-MM-DD,
  defaults to the session date> }`. A nested `session_id` is ignored — the
  script always stamps each milestone with the authoritative top-level
  `session_id`. An entry that is neither a string nor an object, or an object
  whose `milestone` is missing/empty/non-string, is a validation error (exit
  `1`, no files written). A present-but-unparseable `date` silently falls back
  to the session date.
