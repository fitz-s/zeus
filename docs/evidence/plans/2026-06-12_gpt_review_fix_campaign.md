# Plan: GPT deep-review fix campaign (operator-ordered direct fixes)

Date: 2026-06-12
Authority: operator directive 2026-06-12 — "直接信gpt的review每一个文件都被reference了这些都是真问题直接修复"
Review source: /tmp/cgc_answer_REQ-20260612-164003-2259d5.txt (REQ-20260612-164003, ChatGPT Pro deep review of main@940c6ee2d4)
Task: #58

## Scope (T0 surfaces touched, by Top-10 expected-loss order)

1. src/execution/executor.py — CRITICAL-2: commit SUBMIT_TIMEOUT_UNKNOWN unconditionally
   (entry path had `if _own_conn` guard; exit path already unconditional). Duplicate-live-order
   fence durability. No behavior change on the happy path; one extra commit on the
   unknown-side-effect exception path.
2. src/engine/event_reactor_adapter.py — CRITICAL-3: final TAKER sizing must not sweep stale
   DB depth while the fresh witness carries no depth; clamp to fresh authority or typed fail.
3. src/data/replacement_forecast_materializer.py — CRITICAL-1: open-ended catchall cap must
   survive renormalization (constrained redistribution over uncapped bins).
4. src/engine/monitor_refresh.py — HIGH-5: replacement-managed positions must not fall back
   to legacy belief; BELIEF_AUTHORITY_FAULT + targeted rematerialization enqueue.
5. src/engine/event_reactor_adapter.py — HIGH-4: free-cash bound must not silently vanish
   under bankroll_usd_provider.
6. src/venue/polymarket_v2_adapter.py + src/execution/settlement_commands.py +
   src/execution/inventory_redeem_sweep.py — HIGH-9: delete residual autonomous redeem
   broadcast path (operator law: Zeus NEVER submits redeem).
7. src/engine/event_reactor_adapter.py — HIGH-8: settlement coverage shrink fail-CLOSED on
   structural exception in live mode; HIGH-7: NO-side probability independent of YES
   executability; HIGH-10: delete min-order 2% bankroll bump cap (NO-CAPS law).
8. src/data/replacement_cycle_advance_trigger.py — HIGH-6: per-family materializable cycle.
9. Obs coverage: METAR fast lane wiring + no-data-holes test (operator: 所有城市都不能缺少数据).

## Verification per fix

Each fix lands with its regression/antibody test; full money_path suite + touched-area suites
run before every commit; CI selected-relationship-tests must pass on the PR branch.

## Rollback

Each fix is an isolated commit on live/iteration-2026-06-13; revert by commit.
