# EDGE_OBSERVATION packet — executor boot

Created: 2026-04-28
Author: executor-harness-fixes@zeus-harness-debate-2026-04-27 (LONG-LAST)
Judge: team-lead
Source dispatch: DISPATCH_EDGE_OBSERVATION_PACKET (R3 verdict §1 #2 LOCKED FIRST edge packet)
Plan-evidence basis: docs/operations/task_2026-04-27_harness_debate/round3_verdict.md
Reuse note: this packet is the follow-up explicitly deferred by src/state/strategy_tracker.py:24-28 ("edge_compression_check ... wires it through the settlement_fact / decision_fact tables").

## §0 Read summary

| Source | What I learned |
|---|---|
| AGENTS.md L114-126 | 4 strategies + alpha-decay table: Settlement Capture (very slow), Shoulder Bin Sell (moderate), Center Bin Buy (fast), Opening Inertia (fastest). `strategy_key` = sole governance identity. |
| AGENTS.md L168-182 (cross-session merge protocol) | Co-tenant safety: any merge from another worktree needs critic-opus dispatch first; `pre-merge-contamination-check.sh` hook gates. Not directly load-bearing for this packet but informs my git hygiene. |
| src/state/strategy_tracker.py L1-50 | K1-COMPLIANT contract: NO write path; edge_compression_check returns []; deferred to canonical event-log time-series; STRATEGIES enum at L49 = ["settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"]. |
| ULTIMATE_PLAN.md L297-301 | Defines packet: alpha-decay tracker per strategy_key + weekly drift assertion. Apr26 §1.5 strategy-family table is the contract. |
| round3_verdict.md §1 #2/#4 + §3 timing | First edge packet; per-EDGE-packet substrate IS in-scope; Week 4-6 first ship. |
| src/state/db.py L3275-3470 | Canonical surface: `query_authoritative_settlement_rows(conn, limit, *, city, target_date, env, not_before)` returns normalized rows from `position_events` (K0 frozen kernel) with deduplication via `_normalize_position_settlement_event`. Row shape: `trade_id`, `strategy`, `target_date`, `outcome` (0/1), `pnl` (float), `p_posterior` (float, the entry probability), `won`, `direction`, `decision_snapshot_id`, `is_degraded`, `degraded_reason`, `settled_at` (timestamp). |

## §1 Path corrections from dispatch (load-bearing)

| Dispatch said | Actual on HEAD | Implication |
|---|---|---|
| "settlement_fact / decision_fact tables" (per strategy_tracker.py header) | NO tables literally named `settlement_fact` or `decision_fact` exist. The K0 canonical surface is `position_events` (event log). | I will use `query_authoritative_settlement_rows` (existing function, returns normalized event rows) as the source for BATCH 1. Same intent as dispatch; current naming. |
| "src/state/edge_observation.py" | DOES NOT EXIST | NEW file in BATCH 1 |
| "tests/test_edge_observation.py" | DOES NOT EXIST | NEW file in BATCH 1 |
| "scripts/edge_observation_weekly.py" | DOES NOT EXIST | NEW file in BATCH 3 |
| Existing tests: 90/22/0 baseline (BATCH A-D + Tier 2 phases) | Confirmed via .claude/hooks/pre-commit-invariant-test.sh BASELINE_PASSED=90 | Each BATCH bumps baseline as new tests land |

## §2 Per-batch design sketch

### BATCH 1 — `compute_realized_edge_per_strategy` + tests (~6-10h)

**File**: `src/state/edge_observation.py` (NEW, ~120-180 LOC).

**Function signature**:
```python
def compute_realized_edge_per_strategy(
    conn: sqlite3.Connection,
    window_days: int = 7,
    end_date: str | None = None,  # "YYYY-MM-DD"; defaults to today UTC
) -> dict[str, dict]:
    """K1-COMPLIANT read-only projection. Returns:
        {
            "settlement_capture": {
                "edge_realized": float,    # mean(outcome - p_posterior)
                "n_trades": int,
                "n_wins": int,
                "win_rate": float,
                "sample_quality": "insufficient" | "low" | "adequate" | "high",
                "window_start": "YYYY-MM-DD",
                "window_end": "YYYY-MM-DD",
            },
            ...same for shoulder_sell, center_buy, opening_inertia
        }
    """
```

**Realized edge formula**: `mean(outcome_i - p_posterior_i)` over rows where `strategy == strategy_key` AND `settled_at` falls in [end_date - window_days, end_date]. Skip rows with `is_degraded` (per K0-frozen rule that degraded rows must not enter learning).

**Sample-quality boundaries** (per dispatch):
- insufficient: `n_trades < 10`
- low: `10 <= n_trades < 30`
- adequate: `30 <= n_trades < 100`
- high: `n_trades >= 100`

**Tests** (`tests/test_edge_observation.py`, NEW, ~150-200 LOC, ~6-8 test fns):
1. `test_per_strategy_aggregation_correctness` — synthetic in-memory DB with known outcomes/p_posterior; verify mean-edge math
2. `test_sample_quality_boundaries` — exactly 10, 30, 100 trade boundaries
3. `test_empty_result_safety` — no rows → all 4 strategies return n_trades=0 + sample_quality=insufficient
4. `test_degraded_rows_excluded` — degraded row should not contribute to edge_realized
5. `test_window_filter` — settled_at outside window → excluded
6. `test_strategy_filter_only_4_known` — unknown strategy_key in data → not included in result (or quarantined)

**No schema changes.** Pure SQL + Python. No JSON persistence (per K1 rule).

**Mesh maintenance**:
- `architecture/source_rationale.yaml`: register `src/state/edge_observation.py` (planning-lock applies; ARCH_PLAN_EVIDENCE=round3_verdict.md)
- `architecture/test_topology.yaml`: register new test file (planning-lock applies)

### BATCH 2 — `detect_alpha_decay` + tests (~4-6h)

**File**: extends `src/state/edge_observation.py` (~50-80 LOC added).

**Function signature**:
```python
@dataclass
class DriftVerdict:
    kind: Literal["alpha_decay_detected", "within_normal_range", "insufficient_data"]
    strategy_key: str
    severity: Literal["info", "warn", "critical"] | None
    evidence: dict[str, Any]   # slope, current_edge, trailing_mean, n_windows, etc.

def detect_alpha_decay(
    edge_history: list[dict],   # list of weekly windows for ONE strategy_key
    strategy_key: str,
    *,
    decay_ratio_threshold: float = 0.5,    # current < 0.5 * trailing_mean → decay
    min_windows: int = 4,
) -> DriftVerdict:
```

**Algorithm**: ratio test (current window edge / trailing N-window mean) is simpler + more interpretable than linear regression on noisy weekly data. If `current_edge < decay_ratio_threshold * trailing_mean` AND trailing_mean > 0 → alpha_decay_detected (severity by ratio: 0.3-0.5 → warn, <0.3 → critical). Insufficient when n_windows < min_windows OR when most windows have insufficient sample_quality.

**Tests** (~6-8 test fns):
- synthetic decay pattern (steady + sudden + gradual) → expected verdict
- threshold boundary (exactly 0.5 ratio) → within_normal vs decay
- insufficient history graceful (3 windows when min=4) → insufficient_data
- per-strategy threshold override (configurable)
- negative trailing mean (fluky early data) → handled

### BATCH 3 — `edge_observation_weekly.py` runner + integration (~3-5h)

**File**: `scripts/edge_observation_weekly.py` (NEW, ~80-120 LOC).

**CLI**:
```
python3 scripts/edge_observation_weekly.py [--end-date YYYY-MM-DD] [--window-days 7] [--report-out PATH]
```

**Output**: structured JSON; each strategy with edge_realized + sample_quality + DriftVerdict. Default report path: `docs/operations/edge_observation/weekly_<YYYY-MM-DD>.json` (NEW dir; gitignored or registered).

**Integration**: documented for operator manual run + can be wired into existing scheduled job (operator decides; not in scope to wire automation).

**Tests**: end-to-end synthetic DB → JSON output validation (~3-4 test fns).

**Mesh maintenance**: register in `architecture/script_manifest.yaml` (planning-lock applies).

## §3 Risk assessment per batch

| Batch | Risk | Mitigation |
|---|---|---|
| 1 | LOW-MEDIUM (touches K0_frozen_kernel zone via read-only `position_events` query) | Pure read; no writes; uses existing `query_authoritative_settlement_rows` so the data path is already canonical-trusted. |
| 2 | LOW (pure-Python statistical detector; in-memory) | No DB writes; deterministic algorithm; small test surface. |
| 3 | LOW-MEDIUM (CLI + JSON output to new dir) | Output dir is ops/evidence, not authority; JSON is derived context not state. Will not introduce K1 violation. |

**Cross-batch risk: phantom-PnL trap.** strategy_tracker.py L8 documents that the deprecated JSON tracker drifted from canonical event-log → produced phantom PnL +$210.68 vs -$13.03 actual. My BATCH 1 must NOT replicate this — it reads `position_events` directly via `query_authoritative_settlement_rows` and dedupes by `trade_id`. The function I'm calling already implements the dedup; I will not introduce a parallel cache.

## §4 Discipline pledges

- ARCH_PLAN_EVIDENCE = `docs/operations/task_2026-04-27_harness_debate/round3_verdict.md` for every architecture/** edit
- Pytest baseline preserved per BATCH; new tests bump BASELINE_PASSED in the hook
- file:line citations grep-verified within 10 min before commit
- Disk-first: write before SendMessage
- Co-tenant git hygiene: stage SPECIFIC files; never `git add -A` (per memory `feedback_no_git_add_all_with_cotenant`)
- NO commits without explicit operator instruction
- Per BATCH: SendMessage `BATCH_X_DONE files=<count + path list> tests=<X passed Y failed> baseline=<preserved/regression> planning_lock=<receipt path>`

## §5 Out-of-scope (per dispatch — will NOT touch)

- ATTRIBUTION_DRIFT packet (separate; depends on this one shipping first)
- CALIBRATION_HARDENING packet (separate)
- Modifications to existing strategy_tracker.py beyond minimum (deprecated K0 ledger pattern stays deprecated; we BUILD AROUND it)
- Schema migrations to position_events / canonical tables (use existing schema only)

## §6 Open clarifications for team-lead (recommend defaults if no specific guidance)

1. **B1 source function**: confirm `query_authoritative_settlement_rows` is the canonical surface (not a separate `settlement_fact`/`decision_fact` table that I missed)? **Default: yes, use this — function already does the K0 dedup intended by the tracker header.**
2. **B1 row shape**: per `_normalize_position_settlement_event` the row has `outcome` (0/1) + `p_posterior` (float). Realized edge = mean(outcome - p_posterior) per strategy per window. **Default: yes; this is the standard prediction-market realized-edge formula.**
3. **B3 output dir**: `docs/operations/edge_observation/weekly_<date>.json` is a NEW dir under operations. Should it be (a) gitignored entirely, (b) tracked but with an AGENTS.md, or (c) under `docs/operations/task_2026-04-28_*/` instead? **Default: (b) tracked dir + small AGENTS.md describing it as evidence/derived-context, not authority.**
4. **B3 scheduling**: dispatch says "documented for operator manual run OR invoked by an existing scheduled job". **Default: documented for manual run + leave the scheduling decision (cron/launchd) to operator; do not modify cron/launchd in this packet.**

Will idle after BOOT_ACK_EXECUTOR_EDGE_OBSERVATION. Will execute BATCH 1 only after explicit GO_BATCH_1 from team-lead, with answers to §6 (or default-to-recommendation if no specific guidance).

End of boot.
