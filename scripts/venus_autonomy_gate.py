"""Venus Autonomy Gate — computes what Venus is allowed to do.

Output is JSON to stdout. Venus's session reads this as input.
This is a Python script, not a natural language rule. Venus cannot bypass it.

Autonomy levels:
  0 — read-only (3+ regressions in 7 days)
  1 — write xfail tests (2 regressions)
  2 — write code fix in branch (1 regression)
  3 — commit + run tests (0 regressions, <3 improvements)
  4 — create PR (0 regressions, 3+ improvements) [default healthy]
  5 — merge to main (Fitz explicit authorization only)
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

MEMORY_DIR = Path(__file__).parent.parent.parent / "memory"
OUTCOMES_PATH = MEMORY_DIR / "change_outcomes.json"

ALLOWED_ACTIONS = {
    0: ["read_state", "write_gaps", "write_daily_notes"],
    1: ["read_state", "write_gaps", "write_daily_notes", "write_xfail_tests"],
    2: ["read_state", "write_gaps", "write_daily_notes", "write_xfail_tests", "write_fix_branch"],
    3: ["read_state", "write_gaps", "write_daily_notes", "write_xfail_tests", "write_fix_branch",
        "commit_branch", "run_tests"],
    4: ["read_state", "write_gaps", "write_daily_notes", "write_xfail_tests", "write_fix_branch",
        "commit_branch", "run_tests", "create_pr"],
    5: ["read_state", "write_gaps", "write_daily_notes", "write_xfail_tests", "write_fix_branch",
        "commit_branch", "run_tests", "create_pr", "merge_to_main"],
}

BLOCKED_ALWAYS = ["modify_config", "modify_settings", "write_positions", "write_portfolio"]


def load_recent_outcomes(days: int = 7) -> list[dict]:
    if not OUTCOMES_PATH.exists():
        return []
    try:
        outcomes = json.loads(OUTCOMES_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [o for o in outcomes if o.get("date", "") >= cutoff]


def compute_level(outcomes: list[dict]) -> tuple[int, str]:
    regressions = sum(1 for o in outcomes if o.get("verdict") == "REGRESSION")
    improvements = sum(1 for o in outcomes if o.get("verdict") == "IMPROVEMENT")

    if regressions >= 3:
        return 0, f"{regressions} regressions in 7 days — read-only"
    if regressions == 2:
        return 1, f"{regressions} regressions — xfail tests only"
    if regressions == 1:
        return 2, f"1 regression — branch fixes only"
    if improvements < 3:
        return 3, f"{improvements} improvements, 0 regressions — commit allowed"
    return 4, f"{improvements} improvements, 0 regressions — full PR capability"


def main():
    outcomes = load_recent_outcomes(days=7)
    level, reason = compute_level(outcomes)

    result = {
        "level": level,
        "reason": reason,
        "allowed_actions": ALLOWED_ACTIONS[level],
        "blocked_actions": BLOCKED_ALWAYS,
        "outcomes_evaluated": len(outcomes),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }

    json.dump(result, sys.stdout, indent=2)
    print()  # trailing newline


if __name__ == "__main__":
    main()
