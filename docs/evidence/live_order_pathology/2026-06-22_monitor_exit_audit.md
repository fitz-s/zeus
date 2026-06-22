# Monitor / Hold / Exit Re-Evaluation Audit — does Zeus continuously re-decide held positions?

- Created: 2026-06-22
- Last audited: 2026-06-22
- Authority basis: READ-ONLY evidence audit against live source (`zeus-live-main` deployed tree) + real-chain DBs (`state/zeus-world.db`, `state/zeus_trades.db`, `?mode=ro`). Mission: "system must constantly re-evaluate before-fill / holding / near-settle; react to reversal; fill-up / shift-bin / exit; sell before the market notices for a gain."
- Method: codegraph + direct Read of the cycle/monitor/exit spine; verbatim DB queries on the live state DBs (positions, position_events, opportunity_events, exit attribution). DB split per memory: `position_events` / `position_current` live in **zeus_trades.db**; the zeus-world copies are EMPTY shadows — do not read them.

## VERDICT (one line)

The system **does** continuously re-evaluate held positions — the holding-phase monitor refresh + the full reversal-exit decision lane fire on a real ~minute cadence and produced 421 genuine predictive EXIT_INTENTs (261 of them `CI_SEPARATED_REVERSAL`). It is **NOT** passive hold-to-settle at the decision layer. The break is at **execution** (5,897 `EXIT_ORDER_REJECTED` vs 19 `EXIT_ORDER_FILLED`, last fill 2026-06-18) and at two capabilities that are **wired but have never fired**: fill-up (D1) and shift-bin (D2), deployed only today.

---

## Architecture: two lanes, both live

In EDLI live mode `cycle_runner.run_cycle()` is **not** the held-position driver (`src/main.py:9249` comment: "In EDLI event-driven modes run_cycle() is never called"). Held-position re-evaluation runs on the APScheduler jobs in `src/main.py`:

| Phase | Live entry point (scheduler job) | Calls into |
|---|---|---|
| Holding / exit | `@_scheduler_job("exit_monitor")` `_exit_monitor_cycle` `src/main.py:9097`; sets `_held_position_monitor_active` 9124 | `_execute_monitoring_phase` (`cycle_runner.py:545` → `cycle_runtime.py:3671`) |
| Held-family redecision (fill-up/shift-bin trigger) | `@_scheduler_job("edli_continuous_redecision_screen")` `src/main.py:6249` | emits `EDLI_REDECISION_PENDING` → reactor |
| Reactor (entry + fill-up/shift-bin execution) | `@_scheduler_job("edli_event_reactor")` `src/main.py:5219`; `reactor.process_pending` 5694 | `event_reactor_adapter` fill-up/shift-bin |
| Before-fill / resting-order screen | `@_scheduler_job("maker_rest_escalation")` `src/main.py:5998` + `screen_resting_orders` 6337 | cancel/re-rest |

All four DEFER to the held-position monitor (`_defer_for_held_position_monitor`, `src/main.py:261`) so money-at-risk re-evaluation has priority.

---

## Q1 — PHASE COVERAGE (exists? scheduled? fires?)

### (a) Before-fill — resting ENTRY order re-evaluation
- **Exists / scheduled**: YES. `maker_rest_escalation` job (`src/main.py:5998`); the redecision screen also pulls rests whose belief decayed via `screen_resting_orders` (`src/main.py:6337`, `src/events/continuous_redecision.py`); stale-entry cancel in `cleanup_stale_entry_orders` (`cycle_runner.py:518`).
- **Fires**: YES. `position_current.order_status` real distribution: `canceled=263, filled=74, partial=15, backoff_exhausted=16, sell_filled=17, pending=1`. Resting orders are actively cancelled / escalated.
- **Gap**: a `PARTIALLY_MATCHED` remainder that the venue keeps resting can stall a family (known pathology, memory `partial-matched-order-blocks-family-fill-lane`). 15 `partial` rows present now.

### (b) Holding — monitor q refresh + exit-on-reversal
- **Exists / scheduled**: YES. `_execute_monitoring_phase` iterates every non-terminal position (`cycle_runtime.py:3731`), calls `refresh_position(conn, clob, pos)` (`cycle_runtime.py:4013`, `monitor_refresh.py`) for a fresh q/CI, then `pos.evaluate_exit(exit_context)` (`cycle_runtime.py:4108`, `state/portfolio.py:882`).
- **Fires**: YES, on a live minute cadence. `position_events.MONITOR_REFRESHED = 40,162`, latest **2026-06-22T19:51Z**; 38 positions updated today; fresh `last_monitor_prob` stamps within minutes. Current open book is tiny: `phase` active=2, day0_window=2 (5 non-terminal total), settled=60, voided=281.
- **Gap**: shift-bin redecision (see Q4) is not invoked from this loop — the holding loop's `_apply_family_monitor_overlay` (`cycle_runtime.py:2891`) is a **suppressor only** (can block a single-leg exit when family hold-value dominates; it never initiates fill-up or shift-bin).

### (c) Near-settle
- **Exists / scheduled / fires**: YES. Day0 window transition `should_enter_day0_window` (`cycle_runtime.py:3889`); day0 hard-fact lane `evaluate_hard_fact_exit` (`cycle_runtime.py:4026`, triggers `DAY0_HARD_FACT_BIN_DEAD` / `…STRUCTURAL_WIN_HOLD`); closed-market detection `_closed_non_accepting_market_info` (`cycle_runtime.py:3955`) with the 2026-06-20 FIX-2b split that gives a reversal one real shot at `place_sell_order` before stamping `MARKET_CLOSED_AWAITING_SETTLEMENT`.
- **Real evidence of firing**: `DAY0_HARD_FACT_BIN_DEAD` and `SETTLEMENT_IMMINENT` both appear as real EXIT_INTENT triggers (see Q3 table), latest 2026-06-21.

---

## Q2 — REAL-CHAIN EVIDENCE (are recent positions re-evaluated, or passively held?)

Re-evaluated, actively, with money-at-risk priority:
- `MONITOR_REFRESHED = 40,162` events (zeus_trades.db `position_events`), latest 2026-06-22T19:51Z — the monitor touches the held book continuously.
- `EDLI_REDECISION_PENDING` opportunity events = **3,193**, emitted continuously 2026-06-16 → latest 2026-06-22T19:38Z — held families are re-screened on price/belief movement between forecast cycles.
- `edli_live_order_events = 7,651`, latest 19:55Z today — the live order path is active.
- `EXIT_INTENT = 421` real exit decisions (below). NOT passive.

What ACTUALLY closed shares (`exit_timing_attribution`, 4 graded rows, today): all 4 closed via `M5_EXCHANGE_RECONCILE` (×3) or `EXIT_CHAIN_MISSING_REVIEW_REQUIRED` (×1) — i.e. reconcile/admin closes, 0 skillful. The predictive exits decided but did not close on chain (see Q3).

---

## Q3 — EXIT-ON-REVERSAL (does it sell before settlement when physics turns?)

**The decision layer DOES this — abundantly.** `position_events.EXIT_INTENT = 421`, driven by genuine predictive triggers (top rows):

| Trigger | EXIT_INTENT count | Note |
|---|---|---|
| `CI_SEPARATED_REVERSAL` (e.g. entry=0.568→current=0.231) | 261 | belief CI separated below entry → sell |
| `WHALE_TOXICITY` | 24 | toxic flow panic |
| `DAY0_OBSERVATION_REVERSAL` | 20+ | observation moved against held side |
| `SETTLEMENT_IMMINENT` | 11 | near-settle exit |
| `DAY0_HARD_FACT_BIN_DEAD` | ~20 (several variants) | settlement-grade bin death |
| `MODEL_DIVERGENCE_PANIC` (score 0.56–0.66) | ~10 | divergence panic |

The gate that owns this is the CI-separation belief-reversal gate `state/portfolio.py:1165-1259` (the "120-min 守護 guarantee": exit only when the belief CI has SEPARATED below entry, never on a bare price move). It is reachable, correct, and firing.

**Where it breaks: EXECUTION.** `EXIT_ORDER_REJECTED = 5,897` vs `EXIT_ORDER_FILLED = 19` (last fill **2026-06-18**, last EXIT_INTENT 2026-06-21). Rejection reasons (since 2026-06-15):

| Reject reason (verbatim) | count |
|---|---|
| `chain_balance_units=10000;chain_balance_shares=0.01` (dust — nothing real to sell) | 44 |
| `executable_snapshot_market_end` | 12 |
| `executable_snapshot_gate: SELL requires bid-side executable snapshot evidence` | 11 |
| `executable_snapshot_gate: venue command requires executable market snapshot_id` | 8 |
| `MARKET_CLOSED_AWAITING_SETTLEMENT` / `clob_market_info` | several |
| `unknown_side_effect_threshold`, `reconcile_finding_threshold`, `allocator_not_configured`, `ws_gap … m5_reconcile_required`, `collateral_snapshot_stale` | 1–6 each |

So a reversal is correctly DECIDED and an EXIT_INTENT recorded, but the sell is rejected by the executable-snapshot gate / market-already-closed / dust / collateral-stale. This is the same surface as open Task #4 (snapshot gate) and Task #6 (EXIT_ORDER_REJECTED dominates). The "sell before the market notices for a gain" capability exists at the decision boundary and dies at the venue-submit boundary.

---

## Q4 — GAPS (dead / never-fires / missing, with file:line)

1. **Fill-up (D1) — wired, never fired.** `plan_fill_up` (`fill_up_wiring.py:245`) invoked from `event_reactor_adapter.py:3897`, gated by `allow_same_family_monitor_owned = (event.event_type == "EDLI_REDECISION_PENDING")` (`event_reactor_adapter.py:2885`) AND `_recapture.may_submit` (`:3873`). Lease ledger `family_rebalance_intents` (zeus-world.db) = **0 rows**. Deployed only **today** (commit a8408e5e, 2026-06-22 07:11) — empty is partly expected, but it has not produced a single lease in the hours since.
2. **Shift-bin (D2) — wired, never fired, freshly de-inert-ed.** `plan_shift_bin` (`shift_bin_wiring.py:282`) invoked `event_reactor_adapter.py:4002`. Same `family_rebalance_intents`=0. Wired today (commit 89b75c50, 2026-06-22 07:42) and had an inert bug fixed today (`6052c15d fix(reactor): D2 old-leg exit seed must read orderbook_top_bid (was inert)`). No live evidence it fires yet.
3. **Holding loop cannot initiate fill-up/shift-bin.** The only active lifecycle decision inside `_execute_monitoring_phase` is exit-or-hold; fill-up/shift-bin live exclusively in the reactor lane reached via `EDLI_REDECISION_PENDING`. The monitor-side family overlay `_apply_family_monitor_overlay` (`cycle_runtime.py:2891-2990`) is suppress-only (`FAMILY_HOLD_DOMINATES_SINGLE_LEG_EXIT`), never additive. So a held family that should top-up or rotate bins depends entirely on the redecision screen emitting an event AND the reactor's `may_submit` recapture passing.
4. **Exit EXECUTION is the dominant suppressor (not the decision).** `cycle_runtime.py:4140-4255` (should_exit → `_exit_evidence_gate_allows_statistical_exit` → `execute_exit`) plus the executable-snapshot SELL gate produce 5,897 rejects / 19 fills. The reversal exit decision is healthy; the sell rarely actuates. (Open Task #4 / #6.)
5. **On-chain settlement attribution is near-empty** (`exit_timing_attribution` = 4, all reconcile/admin) because predictive exits don't reach a fill — so the system cannot yet self-measure exit skill on reversal exits. (Consistent with memory `verify-alpha-as-winrate-vs-price-not-qlcb`: edli profit-audit columns NULL; only settlement_attribution grades.)

---

## Deployment note (provenance)

Live daemon runs CODE from `/Users/leofitz/zeus-live-main` (launchd `com.zeus.live-trading`, `PYTHONPATH=/Users/leofitz/zeus-live-main`). That tree's git HEAD is `7e5c12bf` but its **working tree is dirty** — D1/D2 + `family_rebalance_intents_schema.py` were staged into it as uncommitted file edits today (mtimes 07:15/07:48). `merge-base 7e5c12bf a8408e5e == 7e5c12bf`, i.e. D1 builds on the live HEAD. So fill-up/shift-bin ARE deployed (files present, `plan_fill_up`/`plan_shift_bin` grep-confirmed in the live adapter), just hours old and not yet exercised. The "wired but never fires" prior finding is CORRECT for D1/D2 as never-fired, but the broader hold/exit re-evaluation it was generalized to is FALSE — that lane is alive.
