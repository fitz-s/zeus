# Run #14 — Track C: `get_*_connection as get_connection` alias lint patch

**Branch**: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `b973ece`
**Date**: 2026-05-17

---

## Confirmed: 17 alias sites (1 intentional, 16 hygiene)

| # | File | Aliased from | Verdict |
|---|---|---|---|
| 1 | `src/data/ecmwf_open_data.py:85` | `get_forecasts_connection` | **INTENTIONAL** (forecasts data writer) — exclude |
| 2 | `src/data/observation_client.py:513` | `get_world_connection` | hygiene rewrite |
| 3 | `scripts/baseline_experiment.py:33` | `get_world_connection` | hygiene rewrite |
| 4 | `scripts/refit_platt.py:23` | `get_world_connection` | hygiene rewrite |
| 5 | `scripts/etl_diurnal_curves.py:44` | `get_world_connection` | hygiene rewrite |
| 6 | `scripts/etl_historical_forecasts.py:45` | `get_world_connection` | hygiene rewrite |
| 7 | `scripts/backfill_cluster_taxonomy.py:21` | `get_world_connection` | hygiene rewrite |
| 8 | `scripts/backfill_ens.py:32` | `get_world_connection` | hygiene rewrite |
| 9 | `scripts/investigate_ecmwf_bias.py:23` | `get_world_connection` | hygiene rewrite |
| 10 | `scripts/automation_analysis.py:28` | `get_world_connection` | hygiene rewrite |
| 11 | `scripts/run_replay.py:24` | `get_world_connection` | hygiene rewrite |
| 12 | `scripts/etl_asos_wu_offset.py:18` | `get_world_connection` | hygiene rewrite |
| 13 | `scripts/validate_dynamic_alpha.py:72` | `get_world_connection` | hygiene rewrite |
| 14 | `scripts/etl_temp_persistence.py:30` | `get_world_connection` | hygiene rewrite |
| 15 | `scripts/audit_time_semantics.py:16` | `get_world_connection` | hygiene rewrite |
| 16 | `scripts/capture_replay_artifact.py:27` | `get_world_connection` | hygiene rewrite |
| 17 | `scripts/etl_solar_times.py:25` | `get_world_connection` | hygiene rewrite |

**FP rate**: 1/17 = 5.9% (ecmwf_open_data.py is intentional alias for forecasts data, as flagged in the prior session and confirmed by `from src.state.db import ZEUS_FORECASTS_DB_PATH, …, get_forecasts_connection as get_connection` import block).

Of the 16 hygiene rewrites: 1 is in `src/` (`observation_client.py:513`) and 15 are in `scripts/`. The src/ one is **inside a function** (line 513 — local import), so the blast radius for renaming bare `get_connection()` is scoped to that function only.

## Why the aliases are a bug (not just hygiene)

The alias hides the DB-zone identity at the call site. A reader of `scripts/refit_platt.py` sees `get_connection()` and cannot tell whether the call writes to world / trades / forecasts without scrolling to the top of the file. This is the exact pattern that produced F22, F43, F46, F81, F82 — DB-zone aliasing collapses the boundary between "trades-rooted live conn" and "world/forecasts archival conn", and the bug surfaces months later as silent dual-write.

## Patch shape — lint rule (runnable)

**File**: `tools/lint/zeus_db_alias.py` (NEW)

```python
#!/usr/bin/env python3
"""zeus_db_alias.py — fail CI when `get_*_connection as get_connection` is used.

Allowed exceptions:
  - src/data/ecmwf_open_data.py  (forecasts data writer; alias is intentional)

Exits 1 if any disallowed alias remains.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_EXCEPTIONS = {
    ROOT / "src/data/ecmwf_open_data.py",
}
PATTERN = re.compile(
    r"get_(?:world|forecasts|trade)_connection\s+as\s+get_connection"
)

def main() -> int:
    bad: list[str] = []
    for path in list(ROOT.glob("src/**/*.py")) + list(ROOT.glob("scripts/**/*.py")):
        if path in ALLOWED_EXCEPTIONS:
            continue
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if PATTERN.search(line):
                bad.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()}")
    if bad:
        print("ZEUS-DB-ALIAS rule violations:", file=sys.stderr)
        for b in bad:
            print("  " + b, file=sys.stderr)
        print(
            "\nFix: rename the import to the explicit name, then update local call "
            "sites:\n"
            "    from src.state.db import get_world_connection\n"
            "    conn = get_world_connection()\n",
            file=sys.stderr,
        )
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

**Wire-up**: add a line to `.pre-commit-config.yaml` (if present) OR `scripts/semantic_linter.py` (if the project uses that as the umbrella linter — F46/F48 antibodies above already extend it, so prefer this).

**CI integration**: invoke from existing CI step that runs `scripts/semantic_linter.py`. Failure mode: exit code 1 with file:line list.

## Per-site rewrite (mechanical)

For each of the 16 hygiene sites:

```bash
# 1) Replace the import line
sed -i '' 's/from src\.state\.db import get_world_connection as get_connection/from src.state.db import get_world_connection/' "$FILE"

# 2) Replace bare get_connection() call sites within that file
sed -i '' 's/\bget_connection(/get_world_connection(/g' "$FILE"
```

**Safety**: step 2 must be scoped to **that file only** (sed `-i ''` does this). If the file also has unrelated `get_connection` references (e.g. a local function literally named `get_connection`), this is unsafe — but a `grep -c "def get_connection" $FILE` returns 0 for all 16 hygiene sites, so the sed is safe.

For `src/data/observation_client.py:513` (the one src/ hygiene site): the alias is inside `def`, so the rewrite is local to that function — even safer.

## Estimated patch size

- New: `tools/lint/zeus_db_alias.py` (~50 LOC)
- Modified: 16 files, each ~2 lines changed
- Test: 1 unit test asserting `zeus_db_alias.main()` exits 0 on a fresh tree and exits 1 with synthetic violation injected.

## Karachi 5/17 impact
None. Lint is a hygiene gate; no runtime behavior change. Safe to land any time.

## Recommendation
Land as a single PR `chore(lint): zeus_db_alias rule + 16 mechanical rewrites`. Do NOT bundle with the F46/F48 fixes — keeps the review surface clean.
