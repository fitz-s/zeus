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

## ONGOING (in-flight agents — these ARE dominoes D1a/D1b/D6 below)
- **a65606217d201f57b** = **D1a** — wiring committed to a branch off ft-ship-64; running final test suite (verify-only, work durable). (#87)
- **a7cfb0224c4c44b49** = **D1b** — ✅ DONE. Identity-calibrator route committed **`2f3df914b5`** on feat/ft-ship-64. IdentityCalibrator in platt.py; get_calibrator returns (cal, level=1) bypassing maturity_level(0)=4 gate; schema **36→37** (calibration_method TEXT col, ALTER idempotent); 13 relationship tests + 116/116 suite. EMITS `IDENTITY_DONE`. (#86)
  - ⚠️ schema 36→37 ALTER → needs world.db migration on D8 restart (daemon checks, does NOT self-migrate).
- ~~a123b794b1ca0a7c6 — daemon silence RC~~ → **RESOLVED-DIAGNOSIS.** Cause: deployment_freshness_4h_divergence auto-pause (daemon booted on stale SHA e4dcaf56, origin/main advanced → guard auto-paused entries 2026-05-24 17:15:31, rolling every min since; traces stopped 17:15:16). SECONDARY: M5 WS-gap reconcile kill-switch armed (15 findings → allow_submit=False, DATA_DEGRADED). Daemon alive+ticking (market discovery 123 events), correctly self-protecting against stale code. FIX = restart on current/ship HEAD (`launchctl kickstart -k gui/$(id -u)/com.zeus.live-trading`) → pause auto-clears, M5 in-memory resets. **Folds into ship deploy restart (step 6) — do NOT restart on interim code.** Watch: M5's 3 chain subjects may need operator QUARANTINE review if findings re-accumulate post-restart.

- **a1d95fdd53c4676f5** = **D6** — REBUILD GREEN-LIT (the dozen-hour long pole). Full ft MC (HIGH+LOW, error_model=full_transport_v1, n_mc=10000, ~2 workers) ADDITIVE to prod state/zeus-forecasts.db (operator: no scratch). world.db NOT touched (rebuild writes forecasts.db only) → no world backup needed. NO 46GB forecasts backup either — instead PROVE additivity via dry-run (delete-scope = bin_source=canonical_v2 AND family=full_transport_v1, which is absent in prod → must report 0 deletions; ANY legacy deletion = STOP). Daemon stays paused. Awaiting a1d95f's dry-run count + launch PID + log path + ETA.

## FORKED (extra issues found — track to closure)
- **DISK EMERGENCY 2026-05-25 ~21:00 — RESOLVED.** State volume hit 100% / 1.5GB free → live-daemon-crash risk. ROOT CAUSE = self-replicating `replay_equivalence_full_transport.py` pileup (4 live + 3 FD-pinning procs, recurred from the earlier 2-h stall) + a1d95f's interrupted 36GB partial backup. FIX: pkill -9 all replay (verdict moot), rm invalid partial backup (+36GB), rm unheld dead scratch regen_high_fixab/sf* (+33GB) → 121GB free. KEPT: state/backups/ens_refit_full (durable 38M-pair asset) + ens_error_models (holds the 71 corrected posteriors AND a 54M-pair copy). REMAINING pinned: /private/tmp/ens_refit/{full,subset}.db held by omc-bridge FDs (~37GB, not reclaimed — left alone, 121GB is enough). ANTIBODY TODO: replay harness must self-limit to one instance + clean up; never spawn on the 31GB DB unsupervised.
- **Daemon silence 27h** — RC done (deployment_freshness auto-pause on stale SHA). No-trade right now is EXPECTED (pre-ship, gated), NOT the "no-trade=defect" case — that rule applies only AFTER D10 unshadow. Clears on D8 restart on ship sha.
- 3 no-model cohorts (Ankara/high/DJF, Jakarta/high/SON, Wellington/high/JJA) — insufficient source data; need data backfill before they ship.
- 8 large-eff cohorts (Busan, Jeddah, …) — large raw TIGGE prior; MUST pass pathology rule (PIT/ECE) in re-eval before pinning.
- Replay uniform-0.5/0%-argmax incl LOW — possible harness artifact; flagged, not blocking (full rebuild on prod anyway).

## DOMINO CHAIN → live (each EMIT tips the next TRIGGER — no manual gaps)
Two parallel tracks (CODE, DATA) run independently, converge at D8 (shadow restart).
On wake: find the last EMIT that fired, tip the domino whose TRIGGER it is. AUTO = advance without asking. GATE = surface to operator with evidence, then stop.

**CODE track** (worktree → PR → merge):
- **D1a** [IN FLIGHT a65606] wiring monitor_refresh flag-OFF → EMITS `WIRING_DONE`. AUTO.
- **D1b** [IN FLIGHT a7cfb0] identity-calibrator route + evaluator un-block → EMITS `IDENTITY_DONE`. AUTO.
- **D2** TRIGGER `WIRING_DONE && IDENTITY_DONE` → consolidate both onto feat/ft-ship-64 (git-master) → EMITS `CONSOLIDATED`. AUTO.
- **D3** TRIGGER `CONSOLIDATED` → open the SINGLE #64 PR + dispatch opus critic on full diff → EMITS `PR_OPEN`. AUTO.
- **D4** TRIGGER `PR_OPEN` → address bot review + critic findings, CI green → EMITS `PR_GREEN`. AUTO.
- **D5** TRIGGER `PR_GREEN` → **operator merge**. → EMITS `MERGED`. **GATE (operator).**

**DATA track** (prod DBs, no PR — runs in parallel with CODE track):
- **D6** [IN FLIGHT a1d95f, long pole ~dozen h] rebuild ft MC HIGH+LOW additive on prod forecasts.db → EMITS `PAIRS_REBUILT`. AUTO.
- **D7** TRIGGER `PAIRS_REBUILT` → write corrected posteriors→prod model_bias_ens_v2; fit Platt where ECE>thr else identity (ECE-gated); additive keyed error_model_family; set calibration.pin model_keys for all 49 (explicit, no "newest VERIFIED wins") → EMITS `CAL_READY`. AUTO.

**CONVERGENCE** (needs BOTH tracks):
- **D8** TRIGGER `MERGED && CAL_READY` → restart daemon SHADOW on ship sha + flag `full_transport_live_enabled` ON (this restart also clears the deployment-freshness auto-pause + resets M5 in-memory) → EMITS `SHADOW_LIVE`. AUTO.
- **D9** TRIGGER `SHADOW_LIVE` → verify shadow: p_raw bins align with online weather forecast for target date, **bin bias ≤ 1 unit**. No shadow result = DEFECT → root-cause (do not stall). → EMITS `SHADOW_VERIFIED`. AUTO.
- **D10** TRIGGER `SHADOW_VERIFIED` → **operator unshadow** (irreversible). → EMITS `UNSHADOWED`. **GATE (operator).**
- **D11** TRIGGER `UNSHADOWED` → prove real chain order fills (tiny/bounded first). No fill = DEFECT → root-cause. → EMITS `FILL_PROVEN`. AUTO.
- **D12** TRIGGER `FILL_PROVEN` → normal sizing. DONE.

## NEXT ACTION ON WAKE
Identify last EMIT fired → tip its successor. Right now: D1a/D1b/D6 IN FLIGHT.
- D1a && D1b done → tip **D2** (consolidate) → D3 (open #64 PR). [CODE track auto to D5 gate.]
- D6 done → tip **D7** (fit cal + pin). [DATA track auto to CAL_READY.]
- Both `MERGED` (D5 operator) && `CAL_READY` (D7) → tip **D8** (shadow restart).
Update domino states above on every advance. DO NOT STALL.
