# WS_OR_POLL_TIGHTENING packet — executor boot

Created: 2026-04-28
Author: executor-harness-fixes@zeus-harness-debate-2026-04-27 (LONG-LAST)
Judge: team-lead
Source dispatch: DISPATCH_WS_OR_POLL_TIGHTENING_PACKET (R3 §3 weeks 5-12 third leg)
Plan-evidence basis: docs/operations/task_2026-04-27_harness_debate/round3_verdict.md
Reuse note: same K1 read-only patterns + same canonical surface as
EDGE_OBSERVATION + ATTRIBUTION_DRIFT (just shipped). Same 3-batch +
critic-gate cadence.

## §0 Read summary

| Source | What I learned |
|---|---|
| AGENTS.md L114-126 | opening_inertia "alpha decay fastest (bot scanning)" — reaction time is the load-bearing signal for this strategy; shoulder_sell "moderate (competition narrows)" — slower but still time-sensitive |
| ULTIMATE_PLAN.md L312-314 | Packet definition: "reactive WS lets Zeus respond faster than competitors during opening-inertia and shoulder-bin entry windows. Edge-improving, not safety-closing." |
| R3 verdict §3 weeks 5-12 | EDGE-dominant phase; WS_OR_POLL parallel-with-AD timing |
| src/venue/ — only polymarket_v2_adapter.py + AGENTS.md (no separate ws_gap_guard.py); ws_gap_guard.py is at src/control/ws_gap_guard.py | M3 user-channel submit guard. In-memory only. Does NOT persist per-update WS-vs-poll provenance. ws_gap is a CHECK-constraint enum value in exchange_reconcile_findings.context (db.py:1110) — sweep-finding context, not per-tick attribution. |
| src/state/db.py L465 market_events | Market-METADATA table (slug/condition_id/token_id mapping). NOT a price-tick log. Schema has no source_timestamp / WS-vs-poll fields. |
| src/state/db.py L482-495 token_price_log | THE PRICE-TICK LOG. Has BOTH `source_timestamp` (venue clock) AND `timestamp` (Zeus persist clock). DELTA between these IS the latency signal. NO `update_source` field — does NOT distinguish WS vs poll at the row level. |
| src/state/edge_observation.py + attribution_drift.py | Same K1-compliant read-only patterns. Reuse: STRATEGY_KEYS, sample_quality boundaries (10/30/100), window-based aggregation, dataclass-based verdicts. Imports consolidated to top per LOW-CAVEAT-EO-2-1 lesson. |
| src/state/db.py grep ws_gap | The string "ws_gap" appears ONLY as a CHECK-constraint enum (sweep-finding context) and in cycle_runner.py imports of the in-memory guard. Zero per-tick WS-vs-poll persistence at HEAD. |

## §1 KEY OPEN QUESTION (the load-bearing finding)

**The canonical event log persists Zeus's REACTIONS (POSITION_OPEN_INTENT,
ENTRY_ORDER_POSTED, etc., in position_events with occurred_at) and the
upstream PRICE TICKS (token_price_log with source_timestamp + timestamp),
BUT it does NOT tag whether each tick arrived via WebSocket subscription
or via REST poll.**

Concretely:
- `token_price_log` has fields: token_id, city, target_date, price, volume,
  bid, ask, spread, **source_timestamp** (venue clock), **timestamp**
  (Zeus persist clock). Delta `timestamp - source_timestamp` IS the
  end-to-end ingest latency.
- There is NO `update_source TEXT CHECK (update_source IN ('ws','poll'))`
  column. There is no separate ws_tick_log / poll_tick_log table either.
- `src/control/ws_gap_guard.py` is in-memory only; it tracks the WS
  subscription state for fail-closed submit but does not stamp every tick.
- `position_events.MONITOR_REFRESHED` exists in the kernel schema but is
  not consistently emitted on every market update; it tracks position-level
  refresh, not market-level tick.

**Net consequence**: at HEAD, we can measure END-TO-END LATENCY (Zeus
persist time minus venue source time) but we CANNOT ATTRIBUTE it to WS-vs-
poll without either (a) extending token_price_log with an update_source
column (writer-side change; out of scope per dispatch "NOT-IN-SCOPE:
Modifying actual WS subscription / poll dispatch logic in src/venue/"), or
(b) inferring update_source heuristically from the latency distribution
itself (WS deliveries cluster at sub-100ms; poll deliveries cluster near
the poll-interval cadence — a bimodal distribution would suggest WS+poll
mix, unimodal would suggest one path dominant).

This is a Phase-2-lesson moment: dispatch wants ws_share/poll_share fields,
but the data substrate does not directly support them. Per audit-first
methodology, three honest paths forward (operator decides):

  PATH A — Latency-only measurement (PRECISION-FAVORED)
    Measure latency_p50_ms + latency_p95_ms + n_signals + sample_quality
    per strategy. Drop ws_share/poll_share from the BATCH 1 contract.
    Ship operator-actionable latency reports; ws-vs-poll attribution is
    deferred to a future packet that adds the upstream tag.

  PATH B — Heuristic inference (RECALL-FAVORED)
    Add a heuristic classifier: latency < 100ms → likely_ws; latency >=
    poll_interval - epsilon → likely_poll; else → likely_indeterminate.
    Surface ws_share/poll_share with explicit "heuristic" caveat.
    Documented limitation in module docstring + AGENTS.md.

  PATH C — Operator-decision: extend the writer
    Add a small upstream change to token_price_log writer to tag
    update_source. Out of scope per dispatch "NOT-IN-SCOPE" but flagged
    here as the proper structural fix.

**Default plan: PATH A** for BATCH 1 (honest precision-favored measurement;
mirrors AD's BATCH 1 framing); BATCH 2 builds gap detection on PATH-A
latency; BATCH 3 ships the runner. PATH B is documented in AGENTS.md as a
future enhancement; PATH C is flagged for operator decision (would unlock
true ws_share). Will await operator GO_BATCH_1 to confirm path before
implementing.

## §2 Per-batch design sketch (PATH A)

### BATCH 1 — `compute_reaction_latency_per_strategy` + tests (~6-10h)

**File**: `src/state/ws_poll_reaction.py` (NEW, ~150-200 LOC).

**Function signature**:
```python
def compute_reaction_latency_per_strategy(
    conn, window_days=7, end_date=None,
) -> dict[str, dict]:
    """K1-compliant read-only. Returns per-STRATEGY_KEY:
    {
        latency_p50_ms: float | None,
        latency_p95_ms: float | None,
        n_signals: int,                # token_price_log rows in window with valid timestamps
        n_with_action: int,            # of those, how many had a position_events action within X seconds
        sample_quality: 'insufficient' | 'low' | 'adequate' | 'high',
        window_start, window_end,
    }
    """
```

Joins `token_price_log` (price ticks with source_timestamp + Zeus
timestamp) with `position_current` (city/target_date/strategy_key
attribution) within the window. Computes latency = `timestamp -
source_timestamp` per tick; aggregates per strategy.

**ws_share / poll_share fields**: NOT included (PATH A); a comment in the
module docstring §"Known limitations" explains why + cites the dispatch
NOT-IN-SCOPE rule.

**Tests** (`tests/test_ws_poll_reaction.py`, NEW, ~150-200 LOC, ~7 tests):
1. per-strategy latency aggregation correctness (p50 + p95 math)
2. sample_quality boundaries (10/30/100 reusing edge_observation pattern)
3. empty_db safety
4. invalid-timestamp rows excluded (NULL source_timestamp or NULL timestamp)
5. window filter
6. unknown strategy quarantined (mirrors AD pattern)
7. negative-latency clipped/rejected (clock-skew defense)

**Mesh**: register in source_rationale.yaml + test_topology.yaml.

### BATCH 2 — `detect_reaction_gap` + tests (~4-6h)

**Function**: `detect_reaction_gap(latency_history, strategy_key, gap_threshold_multiplier=1.5)` → `ReactionGapVerdict` (gap_detected | within_normal | insufficient_data). Compares current p95 to trailing-N-window mean p95. Per-strategy threshold override (opening_inertia tighter than settlement_capture).

**Tests** (~6): synthetic gap patterns + threshold + insufficient.

### BATCH 3 — Weekly runner + e2e tests (~3-5h)

**File**: `scripts/ws_poll_reaction_weekly.py` (NEW, ~150 LOC). Mirror of `attribution_drift_weekly.py` shape. Output `docs/operations/ws_poll_reaction/weekly_<date>.json`. Exit 1 if any strategy gap_detected. New AGENTS.md for the dir.

## §3 Risk assessment per batch

| Batch | Risk | Mitigation |
|---|---|---|
| 1 | LOW (PATH A is straightforward latency stats; minimal new SQL JOIN) | Honest precision-favored shape; no false ws_share claims |
| 2 | LOW (pure-Python statistical detector over BATCH 1 outputs) | Reuse EO BATCH 2 ratio-test pattern; same insufficient_data graceful behavior |
| 3 | LOW-MEDIUM (CLI + JSON + new dir + script_manifest) | Direct mirror of attribution_drift_weekly.py shape |

**Cross-batch risk**: if operator chooses PATH B or PATH C in clarifications, design changes for BATCH 1; will not start BATCH 1 until confirmed.

## §4 Discipline pledges (carry-forward EO+AD lessons)

- ARCH_PLAN_EVIDENCE = `docs/operations/task_2026-04-27_harness_debate/round3_verdict.md` for every architecture/** edit
- Imports consolidated to top of file (LOW-CAVEAT-EO-2-1 lesson; cite in module docstring)
- Boundary tests for thresholds (LOW-CAVEAT-EO-2-2 lesson)
- Co-tenant safe staging — defensively unstage anything not mine (AD BATCH 1 INV-09 case)
- Operator-empathy AGENTS.md "known-limitations" section (AD pattern)
- Per BATCH: SendMessage `BATCH_X_DONE_WS_POLL files=<paths> tests=<X passed Y failed> baseline=<status> planning_lock=<receipt>`
- NO commits without critic-gate APPROVE

## §5 Out-of-scope (per dispatch — will NOT touch)

- Modifying actual WS subscription / poll dispatch logic in src/venue/ (measurement only; execution tightening is operator-decision)
- Extending token_price_log writer to tag update_source (PATH C; would resolve KEY OPEN QUESTION but is writer-side change → out of scope)
- LEARNING_LOOP packet (Week 21+; separate)
- CALIBRATION_HARDENING packet (Week 13; HIGH-risk; separate)
- Schema migrations
- Touching strategy_tracker.py beyond minimum

## §6 Open clarifications for team-lead (recommend defaults if no specific guidance)

1. **PATH choice for BATCH 1 ws_share/poll_share** (KEY): PATH A (latency-only, drop ws_share/poll_share fields) vs PATH B (heuristic classifier with explicit caveat) vs PATH C (operator-approved writer extension to add update_source column). **Default: PATH A** — honest, precision-favored, mirrors AD's "documented limitations" pattern. PATH B risks invented-data critique. PATH C is structurally correct but breaches dispatch NOT-IN-SCOPE.
2. **Latency unit**: report in milliseconds (latency_p50_ms / latency_p95_ms)? **Default: yes; weather-market reaction windows are 100ms-10s scale; ms is the right granularity.**
3. **Per-strategy threshold defaults for BATCH 2**: dispatch suggests opening_inertia gets tighter threshold than settlement_capture. **Default: gap_threshold_multiplier 1.5 default; operator-tunable per strategy via dict kwarg if needed (deferred to BATCH 2 design unless operator wants pre-tuned values now).**
4. **Action-window for `n_with_action`**: how many seconds after a price tick must Zeus take an action (POSITION_OPEN_INTENT etc.) to count as "acted on this signal"? **Default: 30 seconds** (covers full cycle decision time including evaluator + risk gates).

Will idle after BOOT_ACK_EXECUTOR_WS_POLL. Will execute BATCH 1 only after explicit GO_BATCH_1 from team-lead, with answers to §6 clarifications (or default-to-recommendation if no specific guidance).

End of boot.
