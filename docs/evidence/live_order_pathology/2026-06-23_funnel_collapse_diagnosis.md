# Live order-generation funnel — where 15,804 markets collapse to ~2 orders

Created: 2026-06-23
Last audited: 2026-06-23
Authority basis: standing mission (thousands-market continuous discovery + re-evaluation); real-chain live evidence only.

## Question
Operator: still only 1–2 orders, not the thousands-market continuous discovery the design expects; no re-evaluation visible. Diagnose where the funnel collapses.

## Evidence (live DBs at /Users/leofitz/zeus/state, 2026-06-23 ~07:10Z)

| Stage | Measure | Value |
|---|---|---|
| Substrate scale | distinct conditions snapshotted / hr | **1,254** (7.7M total) |
| Substrate freshness | conditions fresh now (≤180s, both sides) | **894 / 1,254 = 71%** |
| Discovery head (`enqueue_live_redecisions`) | entry_candidates per tick | **bursts to 7126 / 5330 / 103** on a new forecast cycle, **0** between cycles |
| Emit (good tick 06-22 05:53) | candidates→spine→families→**emitted** | 103 → 103 → 30 → **29** |
| Emit (typical tick now) | reason | **`no_screened_families`** / `entry_scope=0` |
| Fills (venue_commands, last 3h) | CANCELLED / FILLED / EXPIRED | **30 / 2 / 2** |
| Holdings | active / pending | 2 active buy_no (Wellington 17°C, Warsaw 28°C, both Jun24), 1 pending_entry, 1 pending_exit |
| buy_yes | all phases | **VOIDED** (312 voided incl. Lucknow/Moscow/Ankara/Shenzhen YES) — modal-only fix holding |

## Mechanism (read from src/main.py + src/events/continuous_redecision.py)

1. **Discovery works, is bursty BY DESIGN.** `enqueue_live_redecisions` scans every belief × bin × {buy_yes,buy_no}, requires a FRESH price quote (stale→skip, R7), scores edge, and dedupes via in-memory `acted_state` (re-fires only when score improves ≥ IMPROVE_DELTA). So a new forecast cycle → burst of candidates → emit a batch → `acted_state` suppresses unchanged families until price moves or the next cycle. `entry_candidates=0` mid-cycle is EXPECTED, not broken.

2. **Forecast cadence is per-cycle bursty, not stalled.** forecast_posteriors newest 04:30Z from source cycle 2026-06-22T12Z; written in bursts (71 rows at 01–04Z), quiet since. History self-recovered a 13h gap. Daemon (pid 12260) alive, polling 5-min; will burst again on the next ECMWF cycle.

3. **THE SCALE THROTTLE — emit confirmation gate (src/main.py:6376–6406, 6486).** Even on a burst, `family_keys &= confirmed_entry_scope`. `confirmed_entry_scope` = families whose EVERY bin-condition has fresh both-sided executable substrate (`_edli_families_with_fresh_executable_substrate` → `_condition_buy_sides_fresh`, 180s window). The per-tick refresh that makes families fresh is **budget-limited (~19s, `_edli_refresh_continuous_money_path_families`) and prioritizes held + open-rest families**. With held positions consuming the budget, NEW entry families rarely get confirmed → `entry_scope=0` → tick returns with no emit. This is the recurring `confirmation refresh partial but no screened family has complete fresh substrate; skipping emit`.

4. **Fill yield throttle — maker-only churn.** Re-evaluation IS happening (30 place→cancel cycles / 3h = rest re-pricing), but maker rests rarely fill before price moves → 2 fills / 3h. The "no re-evaluation" the operator sees is really "re-pricing churns but rarely fills, and currently 0 rests are open."

## Verdict
- Mechanically sound: discovery scans the full universe, modal-only buy_yes holds (all YES voided), re-pricing runs.
- Low yield from THREE compounding throttles: (a) discovery bursts gated to forecast cycles; (b) **emit-confirmation budget starves new-entry families because held/rest families are prioritized**; (c) maker-only fills churn.

## Highest-leverage fixes (math-grounded, no caps) — PENDING consult review of session changes (PR #418)
1. **Over-strict family freshness gate.** To trade ONE bin you need THAT bin's both-sided substrate fresh, not ALL bins of the family. `_edli_families_with_fresh_executable_substrate` requires `all(condition fresh)` → a single stale sibling bin drops the whole family. Relax to per-bin (per-condition) admission of the actually-traded bin.
2. **Confirmation budget shared between monitoring and new entries.** Held/rest families eat the ~19s budget; new entries starve. Decouple, or size the budget to cover the screened entry set within the 180s freshness window.

Both touch the live candidate funnel → do NOT deploy until the PR #418 consult review clears (operator-ordered) and a TDD failing-test reproduces the throttle.
