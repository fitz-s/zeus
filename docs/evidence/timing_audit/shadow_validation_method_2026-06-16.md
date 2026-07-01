# Shadow-validation method for M1 / M2 / fusion-guard (2026-06-16)

Authority: investigation of existing shadow-comparison practice
(`docs/evidence/shadow_comparisons/2026-06-10 … 06-14`), shadow harness
(`src/analysis/retired_comparison_tool.py`), replay scripts
(`scripts/replay_exit_path_comparison.py`), and the three changed source
files (`src/execution/harvester.py`, `src/engine/monitor_refresh.py`,
`src/data/bayes_precision_fusion_capture.py`).

---

## Background: existing settlement-graded shadow practice

The standing shadow harness (`retired_comparison_tool.py`) works as follows:

- It reads `edli_no_submit_receipts` (world DB) plus
  `forecasts.settlement_outcomes` (forecasts DB ATTACHed to the world conn).
- A `ShadowCandidate` adapter yields `(shadow_q, live_q)` pairs per
  cohort cell `(city, metric, target_date, bin_label, direction)`.
- Cells without a VERIFIED settlement are dropped as `no_settlement`.
- Scoring: paired log-loss difference + bootstrap CI + sign test.
  Verdict: `PROMOTE_SUPPORTED` / `INSUFFICIENT_N` / `PROMOTE_NOT_SUPPORTED`.
- CLI: `python3 -m src.analysis.retired_comparison_tool [--since ISO] [--no-write]`
- Standing daily run: cron calls `run_retired_comparison_tool_job()`; output in
  `docs/evidence/shadow_comparisons/YYYY-MM-DD_shadow_comparison.md`.
- Current state (through 06-14): ALL days show `INSUFFICIENT_N: 0 paired cells`
  for the only registered candidate (`day0_remaining_day_q`) because the dual-persist
  field `q_remaining_day` was not yet written into receipts.

The exit-path replay analogue is `scripts/replay_exit_path_comparison.py`, which
reads `state/zeus_trades.db` and replays EV-gate decisions from
`position_events.event_type='MONITOR_REFRESHED'` payloads against the actual
`HoldValue` contract. Used for flag-flip decisions (prior use: `HOLD_VALUE_EXIT_COSTS`,
`CANONICAL_EXIT_PATH`). That script writes to `docs/evidence/exit_path_replay/`.

---

## M1 — harvester: settled_at = observation event time

### Can it be validated from existing historical data? NO

**Why not:**

`settled_at` is derived from `obs_row["observation_local_time"]` — the station-reported
observation instant fetched from the `observations` table (zeus-world.db) at harvest
time. The previous code substituted `datetime.now()` (cron wall-clock) as `settled_at`.

A settlement-graded shadow comparison requires two things: (a) a `shadow_q` and a
`live_q` computed under different rules, and (b) VERIFIED settled outcomes. M1 does
not change `q` at all — it changes only the `settled_at` timestamp written to
`settlement_outcomes`. Its effect is:

1. Rows that previously wrote `settled_at = now()` (cron clock, ~minutes after observation)
   now write `settled_at = observation_local_time` (the real station measurement time).
2. When `observation_local_time` is NULL, M1 forces `authority = QUARANTINED` rather
   than writing a VERIFIED settlement with a fabricated timestamp.

Neither of these effects produces a `(shadow_q, live_q)` pair. M1 changes *gradeability
of rows*, not the q values — so the retired_comparison_tool harness cannot score it.

Additionally, historical `settlement_outcomes` rows were written under the old clock,
and those timestamps cannot be retroactively compared against what the observation
table would have produced for the same event without re-running the harvester on each
historical observation row.

### Honest validation for M1

**Unit-level invariant tests (runnable now, no accumulated shadow data needed):**

**Test A — observation time is propagated honestly:**
```python
# Invoke the harvester settlement path with a mock obs_row that has
# observation_local_time = "2026-06-01T14:30:00", confirm:
#   1. settled_at in the written settlement_outcomes row = "2026-06-01T14:30:00"
#   2. recorded_at != settled_at  (two distinct variables)
#   3. authority = "VERIFIED" (when value is bin-contained)
python3 -m pytest tests/test_harvester_settlement_redeem.py -k "observation_local_time or settled_at" -v
```

**Test B — missing observation time forces QUARANTINED:**
```python
# Mock obs_row with observation_local_time = None (or obs_row = None).
# Confirm:
#   1. settled_at is NULL in the written row
#   2. authority = "QUARANTINED" even when the observation value was bin-contained
#   3. reason contains "no_observation_time"
# Key code path: harvester.py lines 1462-1523 (M1 block).
```

**Check existing test coverage now:**
```bash
cd /Users/leofitz/zeus/.claude/worktrees/timing-fixes
python3 -m pytest tests/test_harvester_settlement_redeem.py -v 2>&1 | head -40
grep -n "observation_local_time\|no_observation_time\|QUARANTINED" tests/test_harvester_settlement_redeem.py
```

If no test covers the `observation_local_time=None → QUARANTINED` path, write one
targeting `harvester.py:1517-1523`. The function to test is
`_write_settlement_outcome` (or the caller that sets `settled_at`).

**Live-fire verification (post-deploy, not now):**
```bash
# After deploy, confirm on next harvest cycle that settlement_outcomes rows
# have settled_at ≠ recorded_at for cities with real observation data:
sqlite3 state/zeus-forecasts.db \
  "SELECT city, target_date, settled_at, recorded_at FROM settlement_outcomes
   WHERE authority='VERIFIED' AND date(recorded_at) >= date('now','-1 day')
   ORDER BY recorded_at DESC LIMIT 10;"
# settled_at should be hours before recorded_at (observation time vs cron write time)
```

---

## M2 — monitor exit-age refuses when entered_at is missing/malformed

### Can it be validated from existing historical data? NO (with one partial exception)

**Why not:**

M2 consists of two sub-fixes:

**M2a (fill_tracker entered_at precedence):** Changes which timestamp `entered_at`
receives at fill-confirm time. Old behavior wrote `now.isoformat()` blindly; new behavior:
(1) venue WS match time → (2) preserve existing → (3) `now`. This changes the value stored
in `zeus_trades.db.positions.entered_at` for new fills. The effect on exit behavior is only
visible in subsequent monitor cycles for those positions. There is no historical corpus of
positions whose `entered_at` was set wrongly-vs-correctly in parallel.

**M2b (monitor exit-age refuses on NaN):** Changes what happens in `monitor_refresh.py`
when `position.entered_at` is missing or unparseable. Old behavior: `hours_since_open = 48.0`
(fabricated constant); new behavior: `hours_since_open = NaN` → explicit `entered_at_missing_alpha_refused`
refusal (sets `prob_is_fresh=False`). The corpus of `MONITOR_REFRESHED` payloads in
`position_events` holds the already-applied outcomes; the raw entered_at values that
triggered which branch are not stored in the payload.

There is no `shadow_q` vs `live_q` field in the monitor refresh payloads — the monitor does
not dual-persist alternative q values.

**Partial exception:** `replay_exit_path_comparison.py` proves the prior pattern for flag-flip
replay, but it requires `MONITOR_REFRESHED` payloads with `last_monitor_prob` and
`last_monitor_best_bid`. The M2b fix gates whether `last_monitor_prob_is_fresh` gets set to
False — which the payload does carry. It would be possible to **count** how many historical
MONITOR_REFRESHED events had `entered_at=None` or unparseable and therefore would be newly
refused. This is a corpus-count, not a settlement-graded score.

### Honest validation for M2

**M2b unit invariant — runnable now:**
```bash
cd /Users/leofitz/zeus/.claude/worktrees/timing-fixes
python3 -m pytest tests/test_monitor_refresh_ci_fallback.py \
                  tests/test_pr_monitor.py \
                  tests/test_monitor_refresh_nowcast_wiring.py \
    -k "entered_at" -v
# These are the tests the IMPLEMENTATION_DONE doc notes were fixed with
# realistic entered_at values (M2 regression fix).
```

**M2b directed unit test (write if absent):**
```python
# In any test that calls monitor_refresh_one() or _compute_monitor_refresh():
# 1. Set position.entered_at = None (or "" or "bad-timestamp")
# 2. Assert applied_validations contains "entered_at_missing_alpha_refused"
# 3. Assert last_monitor_prob_is_fresh == False
# Key code: monitor_refresh.py:1193-1238 (M2b block)
```

**M2a corpus count (runnable now, informational only):**
```bash
# Count how many MONITOR_REFRESHED events in the live DB had entered_at missing
# at the position-open time (informational — shows whether the old fabrication
# actually fired in practice):
sqlite3 state/zeus_trades.db \
  "SELECT COUNT(*) FROM position_events
   WHERE event_type='MONITOR_REFRESHED'" 
sqlite3 state/zeus_trades.db \
  "SELECT COUNT(*) FROM positions WHERE entered_at IS NULL OR entered_at=''"
```

**M2a fill_tracker entered_at — reconcile test (runnable now):**
```bash
# The one regression that was fixed (test_reconcile_pending_positions_sets_verified_entry...)
# confirms the precedence ladder works. Run it:
python3 -m pytest tests/ -k "reconcile_pending_positions_sets_verified_entry" -v
```

**Live-fire verification (post-deploy, not now):**
```bash
# After deploy, on each monitoring cycle, check for the new validation tag
# in position_events payloads to confirm M2b fires only when warranted:
sqlite3 state/zeus_trades.db \
  "SELECT COUNT(*), MAX(occurred_at) FROM position_events
   WHERE event_type='MONITOR_REFRESHED'
     AND json_extract(payload_json, '$.applied_validations') LIKE '%entered_at_missing_alpha_refused%'
   ORDER BY occurred_at DESC LIMIT 5;"
# Expect: 0 hits for positions opened post-fix (which now always have a real entered_at)
```

---

## Fusion pre-arrival guard — excludes source_available_at > decision time

### Can it be validated from existing historical data? PARTIALLY YES

**Why:**

The arrival guard in `bayes_precision_fusion_capture.py` (_available_after_decision)
is a **decision-time exclusion gate**: it drops extra models whose `source_available_at`
is after `decision_utc`. It does NOT change the q-building algorithm — it changes
which models enter the precision-weighted fusion. The guard comment
(`SHADOW-Q-STAGED: expected to exclude ~0 in current production`) states that in
current production all extras' `captured_at` lands hours before decisions, so the
guard is expected to be a no-op on live decisions.

This is the one change that produces a **measurable audit from existing data**:

**Can run now — production-impact audit:**
```bash
cd /Users/leofitz/zeus/.claude/worktrees/timing-fixes

# Count how many historical fusion calls would have excluded at least one model
# under the guard. This requires reading bayes_precision_fusion_capture calls
# from decision receipts where source_available_at and decision_time are both recorded.

# Check if model_available_at is persisted in any receipt table:
python3 -c "
import sqlite3, json
conn = sqlite3.connect('state/zeus-world.db')
rows = conn.execute(
  'SELECT receipt_json FROM edli_no_submit_receipts ORDER BY decision_time DESC LIMIT 50'
).fetchall()
for (rj,) in rows:
    r = json.loads(rj or '{}')
    if 'model_available_at' in r or 'source_available_at' in r:
        print(r.get('decision_time'), list(r.keys()))
        break
else:
    print('no model_available_at in receipts — guard effect not auditable from receipts alone')
conn.close()
"
```

If `model_available_at` is not in receipts, audit via `raw_model_forecasts` table:
```bash
# Check raw_model_forecasts for source_available_at vs known decision times:
python3 -c "
import sqlite3
from datetime import datetime, timezone
conn = sqlite3.connect('state/zeus-forecasts.db')
try:
    rows = conn.execute(
        '''SELECT model, source_available_at, captured_at
           FROM raw_model_forecasts
           ORDER BY captured_at DESC LIMIT 20'''
    ).fetchall()
    for r in rows:
        print(r)
except Exception as e:
    print('error:', e)
conn.close()
"
```

**Directed unit test (write if absent — this is the primary validation):**
```python
# test_bayes_precision_fusion_arrival_guard.py (new test)
from datetime import datetime, timezone
from src.data.bayes_precision_fusion_capture import _available_after_decision, capture_bayes_precision_fusion

dt_decision = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

# Case A: source available BEFORE decision — should be admitted (False)
assert _available_after_decision("2026-06-01T10:00:00+00:00", dt_decision) == False

# Case B: source available AFTER decision — should be excluded (True)
assert _available_after_decision("2026-06-01T14:00:00+00:00", dt_decision) == True

# Case C: None / empty — fail-open, admit (False)
assert _available_after_decision(None, dt_decision) == False
assert _available_after_decision("", dt_decision) == False

# Integration: call capture_bayes_precision_fusion with model_available_at set
# so that one extra is in the future; assert it is absent from the posterior.
```

```bash
# Run existing tests for the fusion module:
python3 -m pytest tests/test_bayes_precision_fusion_port_fidelity.py \
                  tests/test_bayes_precision_fusion_thin_anchor_retained.py \
    -v
```

**Settlement-graded shadow comparison (cannot run now, requires live data accumulation):**

The guard changes q only when `source_available_at > decision_utc` for at least one
extra model. Since the comment states this excludes ~0 models in current production,
the paired-cell delta between old-q and new-q is expected to be ≈0 for all historical
decisions. To confirm this at scale with settlement grading:

```bash
# Step 1: Add dual-persist field to bayes_precision_fusion receipts:
#   In the decision receipt, write q_fusion_with_guard (new) alongside q_live (old)
#   when the guard would have excluded at least one model.
# Step 2: Register a ShadowCandidate in retired_comparison_tool.py:
#   ShadowCandidate(name="fusion_arrival_guard",
#                   adapter=generic_two_provenance_field_adapter(shadow_field="q_fusion_with_guard"),
#                   description="fusion arrival guard: exclude future-avail models pre-T2")
# Step 3: Run for ≥14 days of settled markets, then:
python3 -m src.analysis.retired_comparison_tool --since 2026-06-16
# Check docs/evidence/shadow_comparisons/<date>_shadow_comparison.md for
# verdict on fusion_arrival_guard candidate.
```

This accumulation is NOT needed before go-live because (a) the guard is fail-open
(missing availability = admit), (b) the expected exclusion rate is ~0 today, and (c)
the unit-level test above confirms the exclusion logic is correct. The settlement-graded
comparison would only matter if the guard began excluding models at significant rate —
which would be detected in daily retired_comparison_tool output once dual-persist is added.

---

## Summary table

| Change | Settlement-graded shadow runnable now? | Honest validation |
|---|---|---|
| **M1** settled_at = obs event time | **NO** — changes gradeability of rows, not q | Unit tests: obs_local_time → settled_at propagates; NULL obs → QUARANTINED (not VERIFIED) |
| **M2a** fill_tracker entered_at precedence | **NO** — changes stored timestamp for new fills | Unit: reconcile test passes; venue-time → preserve → now precedence ladder |
| **M2b** monitor exit-age refuses on NaN | **NO** — changes prob_fresh flag, no shadow_q | Unit: `entered_at=None → entered_at_missing_alpha_refused` applied; `prob_is_fresh=False` |
| **fusion pre-arrival guard** | **PARTIAL** — can audit impact rate now; settlement shadow requires 14+ day accumulation | Unit: `_available_after_decision` logic; integration: future-avail model is excluded from posterior |

### Runnable verification commands (ordered by priority)

```bash
BASE=/Users/leofitz/zeus/.claude/worktrees/timing-fixes
cd $BASE

# 1. All touched-module tests (covers M2 regression suite + M1 harvester + fusion):
python3 -m pytest tests/test_harvester_settlement_redeem.py \
                  tests/test_monitor_refresh_ci_fallback.py \
                  tests/test_pr_monitor.py \
                  tests/test_monitor_refresh_nowcast_wiring.py \
                  tests/test_bayes_precision_fusion_port_fidelity.py \
                  tests/test_bayes_precision_fusion_thin_anchor_retained.py \
    -v 2>&1 | tail -20

# 2. Antibody bans (static correctness):
python3 -m pytest tests/test_timing_column_liveness.py \
                  tests/ci/test_no_date_today_ban.py \
    -v

# 3. Fusion arrival guard unit check (fast, no DB):
python3 -c "
from datetime import datetime, timezone
from src.data.bayes_precision_fusion_capture import _available_after_decision
d = datetime(2026,6,1,12,0,0,tzinfo=timezone.utc)
assert _available_after_decision('2026-06-01T10:00:00+00:00', d) == False
assert _available_after_decision('2026-06-01T14:00:00+00:00', d) == True
assert _available_after_decision(None, d) == False
print('arrival guard unit checks: PASS')
"

# 4. M2b enter-time refuse unit check (no DB):
python3 -c "
import sys; sys.path.insert(0,'.')
from tests.test_monitor_refresh_ci_fallback import *
" 2>&1 | head -5
# (or run the full pytest suite above)

# 5. Fusion impact audit on live DB (shows current exclusion rate = expected 0):
python3 -c "
import sqlite3, json
conn = sqlite3.connect('state/zeus-world.db')
sample = conn.execute(
  'SELECT receipt_json FROM edli_no_submit_receipts ORDER BY decision_time DESC LIMIT 100'
).fetchall()
with_avail = sum(1 for (r,) in sample if 'model_available_at' in (json.loads(r or '{}') or {}))
print(f'Receipts with model_available_at: {with_avail}/100')
conn.close()
"
```

### What "validated" means for each change going live

- **M1**: accepted when unit test confirms `settled_at = observation_local_time`
  (not `now()`) AND `NULL obs → QUARANTINED`. Live-verify first settlement cycle
  post-deploy by inspecting `settled_at` vs `recorded_at` gap in settlement_outcomes.

- **M2a/M2b**: accepted when the reconcile regression test passes AND a
  `entered_at=None → entered_at_missing_alpha_refused` unit test passes. The 48h
  fabrication was a wrong default; the NaN+refuse path is the structurally correct
  behavior (missing authority → honest refusal, not silent old-enough assumption).

- **Fusion guard**: accepted when `_available_after_decision` unit tests pass and
  the live DB audit shows 0 current exclusions (confirming the guard is a no-op
  in today's production before it matters). No settlement-graded accumulation
  is required as a gate because the guard is fail-open and excludes ~0 today.
