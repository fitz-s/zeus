# Deep Alignment Audit — Run #4 Findings

**Run date:** 2026-05-16
**Worktree:** `.claude/worktrees/zeus-deep-alignment-audit-skill` @ `8114dcd0`
**Main HEAD compared:** `a924766c8a` (zero drift)
**Cascade target:** Karachi 2026-05-17 settlement, position `c30f28a5-d4e`
(buy_yes ≥37°C, shares=1.5873, exposure $0.59, T+~12h to Polymarket
endDate 2026-05-17T12:00Z).
**Mode:** READ-ONLY static + DB probe; no production writes.

---

## 1. Cascade Liveness Verdict

| # | Link | State | Evidence |
|---|------|-------|----------|
| L1 | ingest tick → `harvester_truth_writer.write_settlement_truth_for_open_markets` → `forecasts.settlements` | **GREEN** | `com.zeus.data-ingest` PID 34316 running, `ZEUS_HARVESTER_LIVE_ENABLED=1`, KeepAlive=true, writer signature confirmed at `src/ingest/harvester_truth_writer.py:637`. |
| L2 | `forecasts.settlements` → `harvester_pnl_resolver.resolve_pnl_for_settled_markets` | **GREEN** | Wired at `src/main.py:137-149` via APScheduler harvester job (hourly, UTC tz), reads `SELECT … FROM settlements WHERE authority='VERIFIED'` at `src/execution/harvester_pnl_resolver.py:78`. Refuses legacy `run_harvester` fallback (line 149). |
| L3 | `resolve_pnl_for_settled_markets` → `_settle_positions` → P&L log + `enqueue_redeem_command` | **GREEN** | `src/execution/harvester.py:2016` `_settle_positions` carries P6 idempotency via `position_current.phase ∈ _TERMINAL_PHASES`. Karachi position is `phase=active`, so settlement will fire once. `enqueue_redeem_command` (line 555) writes a `REDEEM_INTENT_CREATED` row via `settlement_commands.request_redeem`. |
| L4 | `enqueue_redeem_command` → `submit_redeem` → `adapter.redeem` (on-chain) | **RED — NO PRODUCTION DRIVER** | `grep -rn 'submit_redeem(' src/ scripts/` returns the definition only. All call sites are under `tests/**`. See F14. |
| L5 | `submit_redeem` confirmation → `reconcile_pending_redeems` → terminal state | **RED — NO PRODUCTION DRIVER** | Same as L4: only test imports. |

**Overall verdict: PARTIAL — cascade halts at L3.**
P&L will be booked correctly to the ledger when Karachi settles, and a
`REDEEM_INTENT_CREATED` row will be enqueued. The on-chain redeem will NOT
fire automatically — the $0.5873 cost (or $1.5873 payout if YES wins)
must be claimed manually via Polymarket UI or by hand-invoking
`submit_redeem` from a Python REPL. The runbook
`KARACHI_2026_05_17_MANUAL_FALLBACK.md` §1 calls L4 part of the
"auto-cascade" — this expectation is incorrect.

---

## 2. New Findings (F14, F15) + Re-Confirmations

Reconciled against `FINDINGS_REFERENCE.md`: 2 genuinely new
(F14, F15). F16 (WU_API_KEY in crontab) was already raised as Run #3
Finding #9 and operator-marked FALSE POSITIVE — NOT re-raised. F17
(heartbeat-sensor `KeepAlive`) duplicates Run #3 Finding #11 (STILL-OPEN) —
recorded here as re-verified, not new.

### F14 — `submit_redeem` has no production caller (SEV-0)

- **Symptom:** Repo-wide grep `submit_redeem(` returns only `tests/test_settlement_commands.py` (5 calls) and `tests/test_settlement_commands_gating.py` (1 call). `reconcile_pending_redeems` is identical (test-only).
- **Where:** `src/execution/settlement_commands.py:310` defines the function; nothing in `src/`, `scripts/`, launchd plists, or cron invokes it.
- **Why it matters:** The state machine `REDEEM_INTENT_CREATED → REDEEM_SUBMITTED → REDEEM_TX_HASHED → REDEEM_CONFIRMED` requires `submit_redeem` to drive the first transition. With no caller, every row enqueued by `enqueue_redeem_command` is write-only — the on-chain claim never happens. For Karachi 5/17, the $0.59 collateral will sit unclaimed until manual intervention.
- **Tier-0 reasoning:** "Blocks cascade" per SKILL §Tier-0 directives.
- **Antibody:** Add a scheduler job (APScheduler `interval` ~5 min) in `src/main.py` that selects `settlement_commands WHERE state='REDEEM_INTENT_CREATED'` and invokes `submit_redeem(command_id, adapter, ledger)`. Add a second job (~10 min) for `reconcile_pending_redeems` against `tx_hash IS NOT NULL AND state='REDEEM_TX_HASHED'`. Cover with a test that imports `src.main` and asserts the job IDs exist.
- **Recommended fix order:** Land before Karachi T+9h fallback window (`2026-05-18 04:00 UTC`).

### F15 — `forecasts.settlements` ↔ `forecasts.settlements_v2` drift (SEV-1)

- **Symptom:** `SELECT count(*) FROM settlements` → **5582**; `SELECT count(*) FROM settlements_v2` → **3999**. 1583-row asymmetry (28% missing in v2).
- **Where:** `state/zeus-forecasts.db`. Schemas differ: `settlements_v2` has explicit `temperature_metric NOT NULL` and `UNIQUE(city, target_date, temperature_metric)` constraint; `settlements` has nullable `temperature_metric` and no equivalent unique index.
- **Why it matters:** The production reader `harvester_pnl_resolver.py:78` uses `FROM settlements` — the legacy table. `settlements_v2` exists but is silently divergent and not read. Two possibilities, both bad: (a) writer half-migrated and `_v2` is dead shadow, (b) `_v2` is intended new authority but reader was never switched. Either way: a future "we switched the reader" change would silently lose 1583 historical settlements.
- **Probe (Cat-H asymmetric-row scan, Run #3 probe #3):** Confirmed via `sqlite3` row counts; both tables present, neither empty.
- **Antibody:** Either (a) drop `settlements_v2` and document `settlements` as canonical, or (b) backfill `settlements_v2` from `settlements` and migrate the reader. Add a CI assert `count(settlements) == count(settlements_v2)` to prevent re-drift.

### Re-confirm Finding #11 (heartbeat-sensor `KeepAlive` missing, STILL-OPEN)

Run #4 plutil sweep across all 6 `com.zeus.*.plist`:
- `data-ingest`, `forecast-live`, `live-trading`, `riskguard-live`: `KeepAlive=true` ✓
- `heartbeat-sensor`: no `KeepAlive` key (`RunAtLoad=true` only) — matches Run #3 #11
- `calibration-transfer-eval`: no `KeepAlive` (one-shot eval, legitimate)
No state change. Fix spec from Run #3 #11 still applies.

---

## 3. Re-Verification of Prior Findings

Not all prior findings were re-tested in this run (budget). Findings touched
by Run #4 probes:

| # | Status | Note |
|---|--------|------|
| F1 (settlement_truth_writer dry_run defaulting True) | Not re-tested | Out of probe scope |
| F4 (resolve_pnl reads wrong DB post-K1) | **PASS (still)** | `harvester_pnl_resolver.py:78` confirmed reading `forecasts_conn` → `forecasts.settlements`. K1 split honored. |
| F6 (P6 idempotency at _settle_positions) | **PASS (still)** | `position_current.phase ∈ _TERMINAL_PHASES` skip at `harvester.py:2090-2098`. Karachi position phase=`active`, sibling `7211b1c5-d3b` phase=`voided` — sibling will be skipped. |
| F8 (ZEUS_HARVESTER_LIVE_ENABLED gating) | **PASS** | `=1` in both relevant plists. |
| F9 (crontab secret false-positive in Run #3) | **CONFIRMED FP for OPENROUTER**; F16 is a separate genuine leak (WU_API_KEY) the FP-override comment-adjacency gate does NOT cover. |
| F11 (settlements ghost rows) | **Now subsumed by F15** with concrete row count gap (1583). |
| F13 (math drift retest) | Not re-tested |

Findings F2, F3, F5, F7, F10, F12 not re-probed this run.

---

## 4. Updated Executive Ranking (post-Run #4, top 7)

| Rank | Finding | SEV | Karachi-blocking? |
|------|---------|-----|-------------------|
| 1 | **F14 submit_redeem no driver** | SEV-0 | YES (auto-redeem blocked) |
| 2 | F1 truth_writer dry_run default (Run #1) | SEV-0 | re-verify next run |
| 3 | F15 settlements ↔ settlements_v2 drift | SEV-1 | no (read uses correct table) |
| 4 | F4 K1 read routing | SEV-1 (PASS) | no — confirmed correct |
| 5 | Run #3 #10 severity-channel disagreement | SEV-1 | no |
| 6 | Run #3 #11 heartbeat-sensor KeepAlive (re-verified) | SEV-2 | no |
| 7 | Run #3 #13 math drift | SEV-2 | no |

---

## 5. LEARNINGS / AUDIT_HISTORY deltas

- **New probe — `grep -rn '<function_name>(' src/ scripts/`** for any module
  whose docstring declares it the "sole entry point" of a state transition.
  Run #4 caught a state machine with a defined-but-unwired terminal step.
  Add to Cat-C (cross-module invariants) as **probe #9**.
- **Promote Cat-H** from MEDIUM → HIGH yield: in 2 of 4 runs it has surfaced
  a divergent shadow table (Run #3 #11, Run #4 F15).
- **Anti-heuristic refinement** for Cat-J: in addition to the comment-adjacency
  gate, add an `$(...)`-substitution gate — values that resolve via
  `$(keychain_resolver.py …)` should not flag, only literal `KEY=hex` should.
  Caught by Run #4 (would have prevented mistakenly re-raising operator-overridden
  Run #3 #9 had F16 been kept).
- **Cascade liveness as a category**: Run #4 spent most of its budget on a
  single cascade trace, surfacing one SEV-0 and one false expectation in the
  fallback runbook. Recommend adding **Cat-K: cascade liveness** with a
  fixed probe template — for each declared cascade chain in `docs/` runbooks,
  verify every link has both a definition AND a production caller.

---

## 6. Karachi 2026-05-17 Prep-Checklist Deltas

Update `KARACHI_2026_05_17_MANUAL_FALLBACK.md`:

- **§1 cascade description**: rewrite to `harvester_truth_writer → harvester_pnl_resolver → _settle_positions → enqueue_redeem_command`. **DO NOT** include `→ clob.redeem` until F14 lands. The on-chain step is manual until F14 fix ships.
- **§3 Timeline addition (T+1h)**: after observing `position_current.phase = settled` and the `decision_log` settlement row, operator must (a) check `settlement_commands` for a `REDEEM_INTENT_CREATED` row for Karachi's condition_id, (b) manually invoke `submit_redeem` or redeem via Polymarket UI.
- Existing §1 statement that auto-cascade includes `clob.redeem` is now known incorrect.

---

## 7. Anomalies / INVESTIGATE-FURTHER

- `forecasts.settlements_v2` has 3999 rows but no production reader. Need
  archaeology: when was `_v2` added, was a reader-switch PR drafted but
  never merged? Check `git log --all -- src/ingest/harvester_truth_writer.py`
  for `settlements_v2` writes.
- `KARACHI_2026_05_17_MANUAL_FALLBACK.md` was prepared in the same audit
  session that produced this report yet asserts an auto-cascade that
  doesn't fully exist. Suggests prior runs did not trace the cascade
  past `_settle_positions`.

## 8. Blocking

None. Audit completed read-only as scheduled. Karachi 5/17 settles in
~12h; F14 fix can land in parallel with operator-monitored fallback.
