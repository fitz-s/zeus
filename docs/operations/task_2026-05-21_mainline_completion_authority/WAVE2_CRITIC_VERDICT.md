# Stochastic+Data-Gated+Capture Wave — Opus Wave-Critic Verdict (2026-05-22)

Branch `wave/stochastic-datagated-20260522` @ 9478443e96. VERDICT: **FIX_REQUIRED** (money-safe; the wave's PURPOSE is broken in live). No SEV-1. L-1 is safe to merge with flag OFF — but its ON-state is currently a no-op.

## L-1 MONEY-SAFETY — CLEAN (no fix)
The hook block (cycle_runtime.py:971-994) is inside one try/except Exception; the only non-exception exit falls through unchanged → the live decision return is structurally untouchable. Flag `shadow_candidate_capture_enabled` defaults OFF. The feared shared-conn commit coupling is never reached (the SELECT raises first). Safe.

## MAJOR-1 (root cause) — L-1 writes to the WRONG DB → captures zero rows in live (K1 ghost-split)
Live cycle passes the TRADE-DB conn (cycle_runner.py:68,639). Candidate writers route on `_is_world_db_conn(conn)` (decision_events.py:433, candidates/__init__.py:333) → False → SELECT decision_events/no_trade_events which live in WORLD, not the trade DB → OperationalError "no such table" → caught fail-open → 0 rows. With flag ON the promotion pipeline (L-2 + F3) receives nothing.
- FIX: the L-1 hook (the caller, per INV-37) must SUPPLY a world-DB connection. Open `get_world_connection()` inside dispatch_shadow_candidates (mirroring write_decision_event's conn=None self-open at cycle_runtime.py:5126) and pass it to candidate.evaluate(conn=world_conn). Add a COUNT(*)>0 smoke.

## MAJOR-2 — 5 calibrated candidates never dispatched
shadow_candidate_dispatch.py:96-106 `_build_candidate_list()` omits CenterBuyCalibratedShadow (S1), OpeningInertiaRelaxation (S2), ImminentOpenCapturePosteriorCollapse (S3), CenterSellModelNo (S4), ShoulderBuyEVT (S5) — all exported but unregistered. Even after MAJOR-1 they capture nothing.
- FIX: add the 5 to `_build_candidate_list()`. (Data-gated ones will log no_trade = valid n_no_trades evidence.)

## MAJOR-3 — F3 router key-namespace mismatch
promotion_proof_router.py:43-72 keys on base strategy keys (center_buy, opening_inertia, imminent_open_capture, center_sell, shoulder_buy), but candidates emit the new shadow keys (center_buy_calibrated_shadow, opening_inertia_relaxation, imminent_open_capture_posterior_collapse, center_sell_model_no, shoulder_buy_evt). Unknown → defaults to "B" (fail-safe, not dropped) — but routing-by-accident, no test.
- FIX: add the 5 shadow keys to `_PIPELINE_B_STRATEGY_KEYS` (calibrated → B) and add a router test asserting each of the 10 strategies → its intended pipeline.

## MAJOR-4 — L-1 tests miss the live-conn DB-effect path
tests/test_l1_shadow_capture_hook.py all use fresh in-memory world-shaped conns; the fail-open test's bomb candidate raises before any write. They'd pass green while MAJOR-1 ships.
- FIX: add a test with a trade-shaped conn (target table absent) asserting (a) no crash/fail-open AND (b) rows land in WORLD after the MAJOR-1 fix.

## MINORS (cheap, optional)
- no_trade_events_schema.py:202 `INSERT OR IGNORE` silently drops CHECK-failing legacy rows → use plain INSERT or a pre/post count assertion (harmless on current prod: 1485 rows, 0 NULL).
- no_trade_events_schema.py:221 CASE remap tops at 28 (ELSE 28) → extend keep-list to 29,30 (cosmetic provenance).
- datetime.utcnow() deprecation in test_weather_event_bayes_alert.py.

## CLEAN
Strategy math (S1/G1/G3 spot-checked: p⁻/p⁺ calibrated, Σ⁻¹e + δ* clipped); v28→29→30 migration antibodies pass; L-2 cron (INV-37 ATTACH+SAVEPOINT, idempotent, regret sum, POSITIVE=WIN); all 10 shadow/kelly 0, none in live_allowed_keys.
