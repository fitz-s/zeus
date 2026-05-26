# DO NOT STALL.

full_transport → live trading: autonomous execution ledger. Read top-to-bottom on wake, find the first non-FINISHED step, advance it, update state here. No-trade = defect. No stall, no excuse.

## CONTEXT FOR A FRESH READER (you, on wake, may have no prior memory)
**Goal:** ship the full_transport probability correction to live Polymarket weather trading. full_transport = location + scale + SNR-gate + F50→F25 transport, applied at p_raw generation. The math shape is correct & proven (#334/#336). The 3-day stall was: an evaluation refit was mistaken for a production artifact (in-RAM posteriors, scratch DB, no live wiring, no persistence). We are now building the real production instantiation.

**Probability chain:** 51-member ENS → ENS bias correction (the full_transport error model: bias b, λ SNR-gate, residual sd; src/calibration/ens_error_model.py + ens_bias_model.py) → daily-max extraction → 10k MC → p_raw → Platt OR identity calibrator → p_cal → α-fusion vs market → edge → Kelly. K1 DB split: state/zeus-world.db (platt_models_v2, traces, trades) + zeus-forecasts.db (calibration_pairs_v2, ensemble_snapshots_v2) + zeus_trades.db.

**Why the two fixes (the heart of it):**
- **Fix A:** the HIGH bias prior was contaminated — ens_bias_repo picked the *freshest* snapshot per date = the 12Z cycle, whose window is nighttime and MISSES the afternoon daily-HIGH → every HIGH prior read −3 to −4°C too cold. Fixed: metric-aware window selection (HIGH→0Z daytime, LOW→12Z night). HK HIGH prior −3.49→+0.67°C. DB-wide. (LOW was always correct → 12Z night IS the daily-min window.)
- **Fix B:** the F25→F50 transport term used single-date deltas (n_paired=1) which give var_d=0 → look maximally confident → SNR gate λ=1.0 → full wrong correction. 34/52 cities affected (Dallas −9.87, Busan +5.03). Fixed: MIN_PAIRED_N=5 gate → transport falls back to bias-only below threshold. HK HIGH effective_bias −2.10→+0.10°C.
- Net: HK ships at +0.10°C (was +6.3 warm then −2.1 cold). All 49 cities, NO carve-out.

**Why this is hard / the gotchas that bit us:** main lacks Fix A (still freshest-selection). Live p_raw is plain (no error model) → promoting ft Platt alone = train/serve mismatch (must wire monitor_refresh, gap 3.1). Evaluator blocks cal=None before edge → p_raw-direct not tradeable without an explicit identity calibrator (gap 3.3). promote scripts replace BY data_version → blanket promote orphans coverage; use additive insert keyed error_model_family. Empty calibration.pin → "newest VERIFIED wins" silent takeover → set pin explicitly. Daemon auto-pauses on stale code (deployment-freshness) — restart on the SHIP sha clears it.

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

- **a1d95fdd53c4676f5** — REBUILD REACTIVATED (the dozen-hour long pole). Full ft MC (HIGH+LOW, error_model=full_transport_v1, n_mc=10000, ~2 workers) writing ADDITIVE to the ACTUAL prod state/zeus-forecasts.db (operator: no scratch). Prod forecasts.db+world.db backed up first (state/backups/*_pre_ftrebuild_2026-05-25.db). Daemon left paused (not stopped). ETA ~dozen hours. When done → fit Platt/identity + posteriors + pin on prod world.db → restart on ship code.

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
