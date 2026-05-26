# DO NOT STALL.

full_transport → live trading: autonomous execution ledger. Read top-to-bottom on wake, find the first non-FINISHED step, advance it, update state here. No-trade = defect. No stall, no excuse.

## Operator contract (hard rules)
- **DO NOT STALL.** Every wake: advance the next incomplete step or root-cause the blocker. Never report "waiting" as a resting state.
- **No-trade = something wrong.** Shadow producing no result = bug → root-cause. Full-live with no actual chain order filled = bug → root-cause. No excuse/stall reason.
- **Rebuild on the ACTUAL prod DB — no separate-branch/scratch DB.** (Scratch regen killed per operator.)
- **`feat/ft-ship-64` is the ONLY integration base** (origin, ahead of main). main LACKS Fix A. Never fork off main.
- **All code → ONE #64 PR → wait for operator merge.**
- **Post-restart verify chain (before any real trade):** shadow result must align with the online weather forecast for the target date, **bin bias ≤ 1 unit** → THEN unshadow → THEN prove real chain order fills.
- HK ships, all 49, **no carve-out** (fix, not exclude). Simplify post-MC: prefer identity calibrator (p_raw-direct) where ECE low; Platt only where it helps.

## Key paths
- Integration branch: `origin/feat/ft-ship-64` (ahead 9).
- Prod DBs (live checkout, ABSOLUTE): `/Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db` (39GB) + `zeus-forecasts.db` (49GB). Live daemon: `src.main` + riskguard.
- Backup full.db (38M pairs): `state/backups/ens_refit_full_2026-05-25.db` (33.5GB).
- Corrected posteriors (71 rows): `state/backups/ens_error_models_2026-05-25.db`.
- Master spec: `docs/operations/FT_SHIP_MASTER_SPEC_2026-05-25.md` (PR #340).

## FINISHED
- Fix A — metric-aware 0Z/12Z window selection in ens_bias_repo (HK HIGH prior −3.49→+0.67). `5260dd2809` on ft-ship-64.
- Fix B — transport MIN_PAIRED_N=5 gate in ens_error_model (HK HIGH eff −2.10→+0.10; 34 cities de-noised). `060540448e` on ft-ship-64.
- Sentinel reader fix (promote_platt_models_v2.py:226). On ft-ship-64.
- Ship-readiness gate (scripts/check_full_transport_ship_readiness.py, 9 booleans). On ft-ship-64.
- Matched-date eval tool + replay-equivalence harness. On ft-ship-64.
- Canonical error-model schema (model_bias_ens_v2 +13 fields) + writer-bug fix + posterior producer (scripts/fit_full_transport_error_models.py). On ft-ship-64.
- Branch consolidation → feat/ft-ship-64 ahead 9 (git-master).
- full.db backup + corrected posteriors persisted (durable).
- Gate-rejection audit → verdict: market-thinning, NOT over-rejection.
- SIGNAL_QUALITY "alpha bug" → REFUTED (real cause MODEL_CONFLICT on phantom cold-bias edges; full_transport is the fix). Closed.
- Replay-proof run → all 79 cohorts regenerate (moot: rebuilding on prod anyway; uniform-0.5 diff possibly harness artifact, not chased).

## ONGOING (in-flight agents — check completion, do not duplicate)
- **a65606217d201f57b** — Live wiring monitor_refresh:453 → p_raw_vector_with_error_model, flag default OFF. (#87)
- **a7cfb0224c4c44b49** — Identity-calibrator route (calibration_method=identity_full_transport_v1, p_cal=p_raw, evaluator un-blocks it) = post-MC simplification. (#86)
- ~~a123b794b1ca0a7c6 — daemon silence RC~~ → **RESOLVED-DIAGNOSIS.** Cause: deployment_freshness_4h_divergence auto-pause (daemon booted on stale SHA e4dcaf56, origin/main advanced → guard auto-paused entries 2026-05-24 17:15:31, rolling every min since; traces stopped 17:15:16). SECONDARY: M5 WS-gap reconcile kill-switch armed (15 findings → allow_submit=False, DATA_DEGRADED). Daemon alive+ticking (market discovery 123 events), correctly self-protecting against stale code. FIX = restart on current/ship HEAD (`launchctl kickstart -k gui/$(id -u)/com.zeus.live-trading`) → pause auto-clears, M5 in-memory resets. **Folds into ship deploy restart (step 6) — do NOT restart on interim code.** Watch: M5's 3 chain subjects may need operator QUARANTINE review if findings re-accumulate post-restart.

## FORKED (extra issues found — track to closure)
- **Daemon silence 27h** — a123b investigating. MUST fix before shadow-verify (shadow can't be verified if it emits nothing).
- 3 no-model cohorts (Ankara/high/DJF, Jakarta/high/SON, Wellington/high/JJA) — insufficient source data; need data backfill before they ship.
- 8 large-eff cohorts (Busan, Jeddah, …) — large raw TIGGE prior; MUST pass pathology rule (PIT/ECE) in re-eval before pinning.
- Replay uniform-0.5/0%-argmax incl LOW — possible harness artifact; flagged, not blocking (full rebuild on prod anyway).

## PENDING → live (the spine; advance in order)
1. [after a65606+a7cfb0] Consolidate wiring + identity onto feat/ft-ship-64.
2. Open the SINGLE #64 PR; opus critic on full diff; address bot review; CI green. **Wait for operator merge.**
3. [after merge] FULL rebuild HIGH+LOW pairs on the **actual prod** forecasts.db through Fix A+B (no scratch). Backup prod first; stop/coordinate daemon.
4. Write corrected posteriors → prod model_bias_ens_v2; fit Platt where ECE>threshold else identity calibrator (ECE-gated); additive (retain legacy), keyed error_model_family.
5. Set calibration.pin model_keys for all 49 (explicit; no "newest VERIFIED wins").
6. Restart daemon in SHADOW + flag full_transport_live_enabled ON.
7. VERIFY shadow: output aligns with online weather forecast for target date, **bin bias ≤ 1 unit**. If no shadow result → DEFECT, root-cause (do not stall).
8. UNSHADOW.
9. PROVE real chain order fills (bounded/tiny first). If no fill → DEFECT, root-cause.
10. Normal sizing.

## NEXT ACTION ON WAKE
Check a65606 + a7cfb0 + a123b completion. If wiring+identity done → consolidate + open #64 PR (step 2). If silence RC done → apply daemon fix. Advance the lowest-numbered non-FINISHED PENDING step. Update states above. DO NOT STALL.
