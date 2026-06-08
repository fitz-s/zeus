# RESTART_RUNBOOK — correct live restart on the new (U0R) system

```
Created: 2026-06-08
Authority basis: this session's audit + builds (db-lock, zero-prior floor, U0R rails, Fault C evidence-gate,
                 retrospective validation). Operator directive: "做好一切live正确重启所需的一切" + "现在就处理好" (no forward wait).
Run scripts/preflight_restart_check.py --root <deploy> at every step — it tells you the ONE next switch.
```

## What is true now (proven, not asserted)
- **Forecast edge is REAL and LIVE-READY today** (retrospective, walk-forward, no-leak, VERIFIED settlement, through the live provider+`fuse_u0r_posterior`): HIGH **ΔBrier −0.0385** (0.8082→0.7697), CI[−0.0409,−0.0361], **win-rate 63.9%** (n=14,370), T2_BAYES on every point. LOW: only lead-1 robust. Matches the offline proof (−0.0415).
- **No 25-day forward wait.** The fixed-lead multi-model history already exists (B0, 50 cities × 189 days). Seeding it makes fusion T2_BAYES instantly. Forward capture only keeps it fresh.
- Infra fixed: "database is locked" category killed + RiskGuard fail-conservative (commit `bd13ca0232`). Zero-prior −inf veto killed (`86ad9380f6`).
- Authority is evidence-gated in the branch (Fault C): flags alone can NOT grant LIVE_AUTHORITY; the gate must pass. Live hazard closes on deploy.

## The restart, in order (each step: run preflight, flip the ONE flag it names)
1. **Deploy the branch** `thepath/audit-realign` to live. (Net live-money delta = tiny_live caps tighten + authority now evidence-gated; nothing loosens.)
2. **Seed the history (the backfill — replaces forward accrual):**
   `python3 scripts/backfill_u0r_history_from_b0.py --db <live>/state/zeus-forecasts.db`
   → ~470k SHADOW_ONLY previous_runs rows; ~113k (forecast,settlement) training pairs; idempotent.
3. **Flip capture** `replacement_0_1_u0r_multimodel_capture_enabled = TRUE` → forward multi-model download keeps history fresh (shadow; posterior byte-identical).
4. **Flip fusion** `replacement_0_1_u0r_fusion_enabled = TRUE` (+ `eb_bias_correction` + `member_vote_smoothing`) → posterior becomes T2_BAYES U0R. Verify `posterior_method=the_path_u0r_fusion` in a shadow cycle.
5. **q_lcb floor** `replacement_qlcb_settlement_sigma_floor_enabled = TRUE` once per-band coverage passes (only-lowers).
6. **Authority** `..._soft_anchor_{trade_authority,kelly_increase,direction_flip}_enabled = TRUE` — ONLY after the evidence gate passes (resolver enforces). See the operator decision below.
7. **ARM (final, operator)** `edli_live_operator_authorized = TRUE` — opens real money. Confirm shadow matches internal + mainstream forecast.

Keep ON throughout: `tiny_live_notional_cap_enabled`, `tiny_live_daily_order_cap_enabled`.

## The one operator decision (evidence gate)
The live `promotion_evidence.json` gate currently FAILS on 4 forward-live blockers (official_days 3<5, official_rows 28<250, q_lcb_coverage 0.409<0.95, nested_walk_forward_passed False). But the **retrospective backtest** just scored **14,370 VERIFIED-settled** points (win 63.9%) — settlement-grade, walk-forward, no-leak.
- **Decision:** regenerate `promotion_evidence.json` from the retrospective backtest (legitimate — same settlement truth, just historical not forward) → gate passes → step 6 unlocks today; **or** keep the conservative forward-live gate and accrue. The math does not require forward accrual; the gate is a policy choice. (Never hand-loosen the gate — regenerate it from real settlement.)

## Switch sprawl → one self-checked next action
Don't memorize 30 flags. `python3 scripts/preflight_restart_check.py --root <deploy>`:
- prints POSTURE (stage 0–4), EVIDENCE gate status + blockers, COHERENCE hazards (fusion-without-data, authority-without-evidence, arm-without-evidence, caps-off), and the **single NEXT flip**.
- exit 2 if any CRITICAL hazard combo is set.

## Residual (latent, not blocking — EMS currently healthy)
Complete the "database is locked" category-kill: `src/data/market_scanner.py:780/1084/3113` + `src/ingest_main.py:1039` are bare/hardcoded connects that can still strip the wait budget under executescript. Route through the factory or add busy_timeout + post-executescript re-apply. (main.py:4063 boot path already covered by init_schema re-apply.)

## Daily-flow check (what must keep downloading)
Continuous + healthy: ECMWF ENS, 54-city obs, intraday ask/depth. After step 3: the 8 U0R models (gfs/icon/gem/jma globals + icon_d2/arome regionals + **ecmwf_ifs anchor** — Fault-B anchor-capture fix) accrue forward daily, 180-day retention. Confirm per-day row growth in `raw_model_forecasts`, not today-only.
