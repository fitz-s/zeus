# Deep Alignment Audit — Run #5 Findings

**Run date:** 2026-05-16
**Worktree:** `.claude/worktrees/zeus-deep-alignment-audit-skill` @ `f0c4f48397`
**Main HEAD compared:** `a924766c8a` (zero drift)
**Cascade target:** Karachi 2026-05-17 settlement, position `c30f28a5-d4e`
(buy_yes ≥37°C, shares=1.5873, exposure $0.59, T+~23h to Polymarket
endDate 2026-05-17T12:00Z).
**Mode:** READ-ONLY static + DB probe; no production writes.
**Focus (per SKILL):** **Cat-K cascade liveness exhaustion** — enumerate every
state machine in the trade/forecast/data pipelines and find F14-siblings (defined
state transitions with NO production driver). Also: shadow-table drift sweep
across all 3 SQLite DBs.

---

## 1. State-Machine Inventory (Cat-K extension probe)

For every module with a multi-state lifecycle, classify the production-driver
status. **"Driver"** = some live caller (scheduler, cron, plist, daemon, or
production code-path leading from one) invokes the actor function that drives
the next state transition. **"Test-only"** = only `tests/` imports it.

| # | Module / state machine | Defined transitions | Production driver? | Verdict |
|---|---|---|---|---|
| 1 | `src/execution/settlement_commands.py` — REDEEM lifecycle (`REDEEM_INTENT_CREATED → SUBMITTED → TX_HASHED → CONFIRMED`) | enqueue (`request_redeem`), `submit_redeem`, `reconcile_pending_redeems` | enqueue=YES (harvester:575); submit=**NO**; reconcile=**NO** | **F14 (RE-CONFIRMED)** |
| 2 | `src/execution/wrap_unwrap_commands.py` — USDC.e ↔ pUSD wrap/unwrap (`PENDING → TX_HASHED → CONFIRMED`/`FAILED`) | `request_wrap`, `request_unwrap`, `mark_tx_hashed`, `confirm_command`, `fail_command`, `reconcile_pending_wraps_against_chain` | **NO production callers anywhere** (only `src/state/db.py:1892` schema + `AGENTS.md` doc mention) | **F16 (NEW SEV-0 latent)** |
| 3 | `src/state/venue_command_repo.py` — venue-command journal (insert/append_event/append_trade_fact) | many | YES — heavy: `executor.py` (10+ sites), `command_recovery.py`, `exit_safety.py`, `cycle_runner.py`, `polymarket_user_channel.py`, `fill_tracker.py`, `exchange_reconcile.py` | ALIVE |
| 4 | `src/execution/command_recovery.py` — `reconcile_unresolved_commands` | reconcile loop, `_review_required_command`, lookup-by-order-id | YES — `cycle_runner.py:674` invokes per cycle inside main engine | ALIVE |
| 5 | `src/execution/exchange_reconcile.py` — WS-gap reconcile + finding resolver | `run_ws_gap_reconcile_and_clear`, `resolve_finding` | YES — `src/main.py:284` + `command_recovery.py:659` | ALIVE |
| 6 | `src/state/uma_resolution_listener.py` — `poll_uma_resolutions` → `uma_resolution` table | `poll_uma_resolutions`, `init_uma_resolution_schema`, `lookup_resolution` | YES — `src/ingest_main.py:800` `ingest_uma_resolution_listener` 5-min APScheduler interval job (**default-OFF** when `settings.uma.polygon_rpc_url` unset — verify config) | ALIVE-CONDITIONAL |
| 7 | `src/calibration/manager.py` + `src/data/calibration_transfer_policy.py` — `validated_calibration_transfers` evidence table (LIVE_ELIGIBLE / TRANSFER_UNSAFE / INSUFFICIENT_SAMPLE / same_domain_no_transfer) | writer: `scripts/evaluate_calibration_transfer_oos.py` (one-shot script); reader: `evaluate_calibration_transfer_policy_with_evidence` (feature-flag gated) | writer=**MANUAL SCRIPT ONLY** (no scheduler); reader=gated-off → safe today; table is **EMPTY** | **F17 (NEW SEV-1 trapdoor)** |
| 8 | `src/ingest/harvester_truth_writer.py` — `forecasts.settlements_v2` writes | `write_settlement_truth_for_open_markets` | YES — `src/ingest_main.py` (post-K1 split, F4 RESOLVED) | ALIVE |
| 9 | `src/execution/harvester_pnl_resolver.py` — `_settle_positions` → ledger + `enqueue_redeem_command` | `resolve_pnl_for_settled_markets` | YES — `src/main.py:137-149` APScheduler hourly | ALIVE |
| 10 | APScheduler main loop (`src/main.py:961-997`) — 6 jobs only | opening_hunt, update_reaction_*, day0_capture, harvester, heartbeat (60s), venue_heartbeat | YES — `BlockingScheduler` UTC tz (P0 invariant honored) | ALIVE |
| 11 | APScheduler ingest loop (`src/ingest_main.py:1110-1291`) — ~25 jobs (k2_*, harvester_truth_writer, source_health_probe, uma_resolution_listener, market_scan, etc.) | many | YES | ALIVE |

**Cascade-liveness summary:** of 11 inventoried state machines, **2 are DEAD-on-arrival** (F14 redeem, F16 wrap/unwrap), **1 is half-dead** (F17 calibration transfer evidence), **8 are live**. Two of the three latent risks are in the trade-execution path; one is in calibration data flow.

---

## 2. New Findings (F16, F17, F18, F19, F20) + F14 correction

### F16 — `wrap_unwrap_commands.py` is a complete state machine with ZERO production callers (SEV-0 latent)

- **Symptom:** Repo-wide grep `wrap_unwrap_commands|request_wrap|request_unwrap` (excluding `tests/`, `__pycache__`, and the module file itself) returns only 3 hits, all schema/doc references:
  - `src/state/db.py:1892` — `CREATE TABLE IF NOT EXISTS wrap_unwrap_commands` (schema init)
  - `src/state/db.py:1907` — foreign-key reference in `wrap_unwrap_command_events`
  - `src/execution/AGENTS.md:21` — table-of-contents row marking the module as `HIGH — no live chain side effects in Z4`
- **Where:** `src/execution/wrap_unwrap_commands.py` lines 51-225 define 7 public functions:
  - `init_wrap_unwrap_schema` (L51)
  - `request_wrap(amount_micro, conn)` (L55)
  - `request_unwrap(amount_micro, conn)` (L59)
  - `mark_tx_hashed(...)` (L63)
  - `confirm_command(...)` (L79)
  - `fail_command(...)` (L96)
  - `get_command(...)` (L106)
  - `reconcile_pending_wraps_against_chain(web3)` (L117)
- **Table state:** `SELECT COUNT(*) FROM wrap_unwrap_commands` on `state/zeus_trades.db` → **0 rows**. The state machine has never been exercised in live data.
- **Why it matters (latency mode):** Unlike F14 (which is actively blocking Karachi auto-redeem because rows are being enqueued), F16 is *latent*. The risk surfaces the moment the first wrap/unwrap is enqueued — there is nowhere for the row to go and no reconcile loop polling for confirmation. The `AGENTS.md` annotation `"HIGH — no live chain side effects in Z4"` suggests the module was authored as preparation for a future deployment that never wired in. This is the classic shape of "complete state machine, missing operator." If a near-future feature ever calls `request_wrap` (e.g. for collateral conversion ahead of Polymarket pUSD migration), it will silently write a stuck `PENDING` row.
- **Tier-0 rationale (per SKILL):** Falls under "incomplete state machine" — same shape as F14. SEV-0 because risk surfaces silently on first use; SEV-0-latent rather than SEV-0-active because no enqueue path is currently wired either.
- **Antibody:**
  1. Decide intent: is wrap/unwrap a Z4-planned feature (delete the module + table for hygiene) or a Z5-imminent feature (wire it now: enqueue caller + scheduler poll for `PENDING` rows + reconcile job)?
  2. If keep: add a smoke test asserting `from src.main import scheduler; assert 'wrap_unwrap_reconcile' in {j.id for j in scheduler.get_jobs()}` (mirror the F14 antibody).
  3. If delete: drop both tables (`wrap_unwrap_commands`, `wrap_unwrap_command_events`), remove the module, update `AGENTS.md`.
- **Karachi 5/17 impact:** NONE direct (Karachi uses the redeem path, not wrap/unwrap).

### F17 — `validated_calibration_transfers` writer is a one-shot script with no scheduler (SEV-1 trapdoor)

- **Symptom:** Repo-wide grep `INSERT.*validated_calibration_transfers` returns exactly one hit: `scripts/evaluate_calibration_transfer_oos.py:381` — a manual/CLI tool. Reader `evaluate_calibration_transfer_policy_with_evidence` is gated by a feature flag (`when flag is off, delegates to legacy evaluate_calibration_transfer_policy`, per docstring at `src/data/calibration_transfer_policy.py:744`).
- **Where:** Writer `scripts/evaluate_calibration_transfer_oos.py:381`; readers `src/data/calibration_transfer_policy.py:724-849`; schema `src/state/schema/v2_schema.py:480`.
- **Table state:** `SELECT COUNT(*) FROM validated_calibration_transfers` on `zeus_trades.db` → **0 rows**.
- **Why it matters:** Today the feature flag is OFF and the policy delegates to legacy string-mapping — so the empty table does no harm. But the table is the operator-visible signal that a real evidence-based decision occurred. If anyone flips the flag ON without first running `scripts/evaluate_calibration_transfer_oos.py` (and that script being scheduled to re-run on each new platt model), every transfer query falls into the "no matching row" branch and either returns `same_domain_no_transfer` or refuses to evaluate. Silent calibration degradation across all city pairs.
- **Antibody:**
  1. Wire `scripts/evaluate_calibration_transfer_oos.py` (or a daemonized variant) into `src/ingest_main.py` APScheduler as a `cron`-style job running daily after `forecast_skill` ETL completes.
  2. Add a CI/preflight check: `if calibration_transfer_evidence_flag_on() and count(validated_calibration_transfers) == 0: raise RuntimeError("evidence-based transfer policy ON but evidence table empty")`.
- **Karachi 5/17 impact:** NONE (Karachi calibration runs through the existing legacy path; feature flag is off).

### F18 — `zeus-world.db` `observation_instants` legacy/v2 asymmetry (SEV-1)

- **Symptom:** `SELECT COUNT(*) FROM observation_instants` → **906,081**; `SELECT COUNT(*) FROM observation_instants_v2` → **1,835,645**. The v2 has roughly *2×* the legacy row count, with ~929k v2 rows absent from legacy.
- **Where:** `state/zeus-world.db`.
- **Why it matters:** This is the canonical "observation truth" table used as input for ensemble + calibration. The asymmetry shape is the inverse of F15 (settlements: legacy has more than v2) — here, v2 has *much* more. Two interpretations, both worth flagging: (a) `observation_instants_v2` is the post-migration authority and legacy is a frozen snapshot — fine, but the 906k legacy rows are dead weight; or (b) some reader (or scientist replay) still queries the legacy table and sees a half-truth historical view, causing reproducibility drift between live runtime and offline replay. Without a probe of every reader, the second possibility cannot be ruled out.
- **Antibody:** Grep `FROM observation_instants[^_v]` repo-wide; classify every reader as legacy-only or migrated. Drop legacy after readers migrate; add `assert_db_matches_registry` row-count parity check during the transition.
- **Karachi 5/17 impact:** NONE direct — Karachi is a settlement event, not a fresh-forecast event.

### F19 — Cross-DB `market_events_v2` asymmetry (SEV-2)

- **Symptom:** Row counts for the same logical table across 3 DBs:
  - `state/zeus-forecasts.db.market_events_v2` → **9,914** (canonical post-K1)
  - `state/zeus_trades.db.market_events_v2` → **7,326** (shadow, 2,588-row gap)
  - `state/zeus-world.db.market_events_v2` → **2,112** (F4 stranded residue, no longer growing)
- **Where:** All three DBs carry the `market_events_v2` table; only `forecasts.db` is the K1 authority.
- **Why it matters:** F4's resolution (PR #121) routed new writes to forecasts.db, but two shadow copies exist with stale row counts. The 2,588-row gap on `zeus_trades.db` is the most awkward — it sits inside the trade-lifecycle DB where the trade engine could mistakenly query it if a future read path forgets to use `get_forecasts_connection()`. Same pattern as F12 (ghost trade-lifecycle tables on world.db) but for a market-data table.
- **Antibody:** Drop the table on `zeus_trades.db` AND `zeus-world.db` after confirming no production reader on either path. Tighten `db_table_ownership.yaml` to assert `db:forecasts` tables have no schema on other DBs (extension of the F12/PR-F fix).
- **Karachi 5/17 impact:** NONE direct.

### F20 — `zeus-world.db.ensemble_snapshots` 116 dead legacy rows (SEV-2)

- **Symptom:** `SELECT COUNT(*) FROM ensemble_snapshots` on `zeus-world.db` → **116**; corresponding `ensemble_snapshots_v2` → **0**. Both 0 on the other two DBs. The canonical post-K1 location is `zeus-forecasts.db.ensemble_snapshots_v2` (which has **1,124,052** rows).
- **Where:** `state/zeus-world.db`.
- **Why it matters:** Same shape as F19 — orphaned legacy data on the wrong DB. 116 rows is small enough to be obviously dead, but indicates the migration sweep missed a table. Risk is that a future code path (`SELECT FROM ensemble_snapshots`) hits a 116-row dead table and silently runs against ancient data.
- **Antibody:** DROP TABLE on world.db. Same ownership-tightening as F19.
- **Karachi 5/17 impact:** NONE.

### F14 correction note

Run #4 §2 F14 stated `submit_redeem` lives on `forecasts.db`. **Actual location: `zeus_trades.db`** (`settlement_commands` table). Verified Run #5: `settlement_commands` row count = 0 on both `zeus_trades.db` and `zeus-world.db` (Karachi's `c30f28a5-d4e` position has not yet had its settlement event written, since the cascade hasn't fired — endDate is ~23h out at audit time). This does not change F14's verdict or fix spec, only the DB target for the wiring.

---

## 3. Stuck-Row / Sentinel Findings

| Probe | Result | Interpretation |
|---|---|---|
| `SELECT COUNT(*) FROM position_events WHERE occurred_at NOT GLOB '2*'` (zeus_trades.db) | 1 / 7 total | The sentinel row first noted in Run #2 Finding #8 (`occurred_at='unknown_entered_at'`) is still present. Affects Karachi position `c30f28a5-d4e` — its single `CHAIN_SYNCED` event carries the sentinel. No new sentinel rows since #8. Fix already specified in PR-G. |
| `SELECT COUNT(*) FROM exit_mutex_holdings` | 0 | No stuck exit mutex (all released). Cascade safe from mutex deadlock. |
| `SELECT COUNT(*) FROM collateral_reservations WHERE released_at IS NULL` (re-verified) | 0 | No stuck collateral; F2/F12 collateral integrity unaffected. |
| `SELECT * FROM uma_resolution LIMIT 5` | empty | UMA listener is active (5-min cron) but default-OFF without `settings.uma.polygon_rpc_url`. NOT a stuck row — just unused. |
| `SELECT COUNT(*) FROM settlement_commands` | 0 on trades.db; 0 on world.db | Karachi enqueue hasn't fired yet (expected — endDate +23h). Run a follow-up probe on **2026-05-17 ~13:00 UTC** (T+1h post-settlement) to confirm a `REDEEM_INTENT_CREATED` row appears — and stays stuck without F14 driver. |
| `SELECT COUNT(*) FROM wrap_unwrap_commands` | 0 | F16 module never exercised. |
| `SELECT COUNT(*) FROM validated_calibration_transfers` | 0 | F17 evidence table never populated. |

---

## 4. Cross-Cascade Asymmetry Summary

Aggregated DB row counts for canonical-vs-shadow tables across all 3 DBs (`forecasts.db` `F`, `zeus_trades.db` `T`, `zeus-world.db` `W`):

| Table | F.legacy | F.v2 | T.legacy | T.v2 | W.legacy | W.v2 | Finding |
|---|---|---|---|---|---|---|---|
| `calibration_pairs` | NA | 91,040,450 | 0 | 0 | 0 | 0 | clean |
| `ensemble_snapshots` | NA | 1,124,052 | 0 | 0 | **116** | 0 | **F20** |
| `historical_forecasts` | NA | 0 | NA | 0 | 0 | 22,644 | mixed (likely WIP table) |
| `market_events` | NA | 9,914 | 0 | **7,326** | 0 | **2,112** | **F19** (+ F4 residue) |
| `observation_instants` | NA | 0 | 0 | 0 | **906,081** | **1,835,645** | **F18** |
| `platt_models` | NA | 0 | 0 | 0 | 0 | 1,406 | inert (no reader probed) |
| `rescue_events` | NA | 0 | NA | **1** | NA | 0 | 1 orphan row — too small for new finding, log to LEARNINGS |
| `settlements` | 5,582 | 3,999 | 0 | 0 | 0 | 0 | **F15** (Run #4) |

---

## 5. Karachi 2026-05-17 Specific Status

Re-verified at audit time (2026-05-16 ~13:00 local):

- **Position `c30f28a5-d4e`**: phase=`active`, shares=1.5873, exposure $0.59 — unchanged since Run #4.
- **Sibling `7211b1c5-d3b`**: phase=`voided` — F6 idempotency will skip on settlement (re-confirmed).
- **No `settlement_commands` row yet** — expected; enqueue happens after harvester P&L fires post-resolution.
- **Single `position_events` row** for this position carries the F8 sentinel `occurred_at='unknown_entered_at'` — operator monitoring should ignore the timestamp.
- **Cascade verdict (refresh of Run #4 §1):** unchanged — auto-cascade halts at L4 (F14). Manual fallback per `KARACHI_2026_05_17_MANUAL_FALLBACK.md` §1 + §3 still required.
- **NEW probe scheduled**: `2026-05-17 13:00 UTC` (T+1h post-endDate) confirm `settlement_commands` row appears (proves L3 fired) and stays in `REDEEM_INTENT_CREATED` indefinitely (proves L4 dead — F14 evidence).
- **No new Karachi-blocking findings** in Run #5; F14 remains the sole direct blocker.

---

## 6. Updated Executive Ranking (post-Run #5, top 10)

| Rank | Finding | SEV | Karachi-blocking? | First seen |
|------|---------|-----|-------------------|-----------|
| 1 | **F14 `submit_redeem` no production driver** | SEV-0 active | YES (auto-redeem) | Run #4 |
| 2 | F1 truth_writer `dry_run` defaulting True | SEV-0 | re-verify next run | Run #1 |
| 3 | **F16 `wrap_unwrap_commands.py` no production driver** | SEV-0 latent | no | **Run #5** |
| 4 | F15 `settlements` ↔ `settlements_v2` 1,583-row drift | SEV-1 | no (read uses correct table) | Run #4 |
| 5 | F5 DB-lock storm in `CollateralLedger._connect` | SEV-1 live | INDIRECT (ingest slowness) | Run #1 |
| 6 | **F17 `validated_calibration_transfers` no scheduler** | SEV-1 trapdoor | no | **Run #5** |
| 7 | **F18 `observation_instants` legacy/v2 asymmetry on world.db** | SEV-1 | no | **Run #5** |
| 8 | Run #3 #10 severity-channel disagreement | SEV-1 | no | Run #3 |
| 9 | F2 `decision_id` 100% NULL lineage regression | SEV-1 | no (historical) | Run #1/Run #3 |
| 10 | **F19 cross-DB `market_events_v2` asymmetry** | SEV-2 | no | **Run #5** |

(F20 SEV-2 ranks #11; F8/F11/F12 SEV-2 unchanged.)

---

## 7. LEARNINGS.md suggested deltas

- **Cat-K (cascade liveness) graduates from "new" to "HIGH yield"**: in 2 runs (#4, #5) the category has surfaced 2 dead state machines (F14 active, F16 latent) and 1 half-dead writer (F17). The fixed probe template is now: for every module under `src/execution/`, `src/state/`, and `src/ingest/` that defines a multi-state lifecycle, run `grep -rn '<actor_fn>(' src/ scripts/ | grep -v test_ | grep -v __pycache__ | grep -v '<module>.py:'` for each declared actor function; flag any function with zero production callers as SEV-0 (if any enqueue path is wired) or SEV-0-latent (if even the enqueue path is unwired).
- **New probe #10 — "AGENTS.md as cascade map"**: per-directory `AGENTS.md` files annotate modules with descriptions like `"HIGH — no live chain side effects in Z4"`. These deferred-by-design tags are valuable cascade-liveness hints — grep for `"no live"` / `"deferred"` / `"future"` / `"unwired"` in `AGENTS.md` files and audit each tagged module for either (a) actual unwiring (then ensure it's deletion-safe) or (b) silent recent wiring (cross-check against grep).
- **New probe #11 — "shadow-table sweep across all DBs"**: for the 3 SQLite DBs, run `SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%_v2' OR '%_legacy' OR ...)` then row-count both halves of every `(table, table_v2)` pair. Cross-DB row-count matrix (as in §4 above) reveals shadow asymmetries that single-DB scans miss. This is the generalized form of Cat-H.
- **New probe #12 — "writer-is-a-script gate"**: when a table has a real reader but the only writer is a `scripts/*.py` (not imported by `src/main.py`, `src/ingest_main.py`, or any daemon), flag SEV-1 trapdoor. The script needs to be either deleted, daemonized, or scheduled.
- **Refinement to Cat-J (false-positive comment-adjacency gate)**: F17 reminds us that "writer is a script" is itself a legitimate design (one-shot evaluations); the gate should not auto-flag scripts as dead — only flag when there is *also* a live consumer expecting fresh writes. Pattern: `reader_calls > 0 AND writer_calls = 0 AND writer-in-scripts-only` → SEV-1.
- **Cat-K probe escape-hatch**: for an actor function with a docstring saying "should be wired via cron entry calling X" (UMA listener example), verify the cron/scheduler hookup separately. UMA passed (`ingest_main.py:800`); a similar docstring on a future module deserves the same probe before assuming default-OFF safety.

---

## 8. AUDIT_HISTORY.md append line

```
| 5 | 2026-05-16 | f0c4f48397 | Cat-K cascade-liveness exhaustion + shadow-table sweep | F16 (SEV-0 latent: wrap_unwrap_commands no driver), F17 (SEV-1: validated_calibration_transfers writer-is-script), F18 (SEV-1: observation_instants legacy/v2 asymmetry), F19 (SEV-2: cross-DB market_events_v2 asymmetry), F20 (SEV-2: 116 dead ensemble_snapshots on world.db); state-machine inventory (11 lifecycles, 8 ALIVE, 2 DEAD, 1 HALF-DEAD); F14 re-confirmed + DB location corrected to zeus_trades.db | none beyond Run #4 — F14 still sole Karachi blocker |
```

---

## 9. Probe-budget accounting

Run #5 spent ~60% budget on cascade-liveness enumeration (good — high yield, 3 findings), ~25% on shadow-table cross-DB sweep (good — 3 findings), ~15% on stuck-row sentinel re-verification (low — confirmed nothing new). Next run focus suggestion: F17 deep dive (confirm feature flag really OFF in prod configs + audit the legacy fallback for known calibration regressions) and Cat-J trapdoor sweep for *other* `scripts/*.py` writers that may share F17's shape.
