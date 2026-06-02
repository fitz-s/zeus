# HANDOFF — EMOS-CI shadow / GOAL#36 — 2026-06-02

Created: 2026-06-02
Authority basis: this session's work on GOAL#36 (3 verified fills). Read root AGENTS.md + .claude/CLAUDE.md first.

## TL;DR
GOAL#36 (3 FILLED orders e2e + 120-min 守护) **NOT met**: 0 fills, **zero validated alpha**, arm OFF, **zero capital moved**. Daemon shadow, healthy. The session built the EMOS-CI honest-CI substrate, then found (via the operator's "check / show me the orders" pushes) that the EMOS shadow data was broken by a **degenerate-bin bug** — fix in flight. Operator issued 3 standing mandates that govern all further work (see below). DO NOT cherry-pick cities; DO NOT arm without explicit "arm it".

## OPERATOR'S 3 MANDATES (current law — supersede the per-city-license approach)
1. **Causality first**: the system must STOP using future data for current trades. (Audit in flight → `/tmp/causality_audit.md`.)
2. **Buy_yes/buy_no validation correct, NO exception.** (Audit DONE: LIVE path CORRECT — see below. Shadow EMOS NO-lcb is coarse, fix before live promotion.)
3. **UNIVERSAL** — every choice must work for ALL cities. "You do not fail 30 cities and 3 work and call that valid." The per-city-license / cherry-pick approach is REJECTED. The SYSTEM must be correct for all cities; trading then follows only where genuine edge exists.

## GIT / DAEMON STATE
- Branch `edli-correctness-recover-2026-06-02`, HEAD **61906ec2**, synced to origin, **13 ahead of main**. PR **#370** OPEN (base main) — title "feat(edli): Tier-0 correctness recovery + EMOS-CI shadow forward-proof harness". Carries the #369 recovery + the EMOS-CI shadow harness.
- Worktrees: **1** (main only). 3 siblings removed this session, all preserved on branches (work-b3@e167b0c2, stat-whole-refactor@f1de9f25, claude/wf_327845a4@297b724f80 = task #109 S1 work).
- Daemon **PID 80978**, booted ~12:36, running **a9a62e50** (metric-gate commit). SHADOW (`live_execution_mode=edli_shadow_no_submit`, `reactor_mode=live_no_submit`), arm `real_order_submit_enabled=false`. ZERO capital.
- **CRITICAL**: the running daemon (a9a62e50) has the degenerate-bin bug → it is recording **emos_q=0** for every interior bin. The shadow ledger (`state/emos_shadow_ledger.jsonl`, ~31.7k lines, flag `edli_emos_shadow_ledger_enabled=true`) is therefore **garbage for emos_q**. After the degenerate-bin fix lands + commit, the daemon MUST be restarted (git rev-parse HEAD first) to record correct emos_q.
- config/settings.json + .claude/settings.json are OPERATIONAL — NEVER commit. The shadow-ledger flag is ON there (operational).

## CRITICAL OPEN BUG — degenerate bin (fix in flight, agent aa3bb32a)
`emos_q == 0.000 for EVERY interior bin` because the EMOS path calls `bin_probability(mu,sigma, X, X)` on the DISPLAY point bin `[X,X]` (zero width → Φ−Φ=0). Settlement is `round→X` = interval **[X−0.5, X+0.5)**. The LIVE q (MC `p_raw_vector_from_maxes`) uses rounding membership (correct, e.g. q_live=0.512); the EMOS hook used the point → 0 → every buy_no fakes a (1−cost) edge. **ALL EMOS shadow analysis this session (509 "cold-tail-NO", 76 shadow orders, fillability, #24-pass licensing) is VOID — built on emos_q=0.** Fix = `bin_probability_settlement(mu,sigma,low,high)` that expands to the rounding interval (interior [X−0.5,X+0.5); shoulder [None,X]→(−inf,X+0.5); [X,None]→[X−0.5,+inf)), applied in BOTH `_write_emos_shadow_ledger` AND `_maybe_override_lcb_with_emos_ci`, + a MECE sum-to-1 antibody test. After fix: re-extract real shadow orders; verify emos bin-vec non-zero, peaks near round(μ), sums~1.

## SECOND OPEN CONCERN — EMOS μ shift ("huge temp diff = alpha−")
For one fresh SãoPaulo row, EMOS μ=22.62°C vs raw ensemble/live-q mode ≈21°C → EMOS shifting ~+1.6°C warm. After the bin fix, VERIFY per-city (universal) whether EMOS `a+b·x̄` makes honest small corrections or injects large warm/cold shifts. A large shift unvalidated by settlement = the bias the operator forbids. (Note: backward coverage showed SãoPaulo PIT 0.414 small-warm; the 1.6°C may be this row's specific x̄ — confirm.)

## AUDIT RESULTS
- **Buy_yes/buy_no (DONE, agent adf4ba16, /tmp/buynoyes_audit.md): LIVE path CORRECT universally.** q_no=1−q_yes (exact complement); NO lcb is INDEPENDENT bootstrap (`market_analysis._bootstrap_bin_no`), NOT 1−yes_lcb; cost_no = NO token's own ask (`executable_cost.py:161` book.no_asks) with fail-closed `assert_not_no_complement_cost` guards; buy_no → BUY on no_token (`execution.py:548`); unit-safe; open-shoulder handled. Live engine DOES clear genuine +edge buy_no (SãoPaulo 23°C: q_no 0.9992, cost 0.7688, +0.0096) — blocked only by an unrelated bankroll gate. **Shadow-only flag**: `_write_emos_shadow_ledger:3867` uses 1−emos_q for the NO *lcb* (coarse, harmless in shadow, feeds nothing live) — switch to independent NO bound BEFORE `edli_emos_ci_live_enabled` is ever turned on.
- **Causality (IN FLIGHT, agent a6cdc5db, /tmp/causality_audit.md)**: future-data-for-current-trades audit — READ ITS OUTPUT, fix any leak universally.

## WHAT WAS FIXED + COMMITTED THIS SESSION (on the branch / PR #370)
- a2013a98 EMOS serve + shadow dual-track ledger + forward scorer (flag OFF).
- 75ba7f6b shadow EMOS-CI recording (k_cov coverage-anchored CI) — critic-clean live-path (8/8), but carries the degenerate-bin bug (open).
- de57ad2c keystone: shadow hook reads members from snapshot.members_json (was empty getattr → all member stats nan).
- a9a62e50 gate shadow EMOS to HIGH metric + record metric (LOW excluded, needs #54).
- c78192a2 scorer: reject stale/inconsistent rows (forecast-consistency gate) + PIT per-(city,date) not per-bin.
- 61906ec2 test-isolation FIX A (ZEUS_EMOS_LEDGER_PATH env + conftest tmp) + FIX B (write-boundary stale-decision_time reject). PROOF: live ledger line count unchanged across a full pytest run.
- Ledger cleaned: 544 pytest-fixture rows removed (atomic filter).

## UNCOMMITTED ON DISK (from in-flight agents — review before commit)
- a75cba02 (DONE): EMOS-CI **live wiring** Option B — `_maybe_override_lcb_with_emos_ci` at event_reactor_adapter.py ~3159 (flag `edli_emos_ci_live_enabled` default OFF, per-city license file `state/emos_ci_license.json` absent) + `src/calibration/emos_ci_license.py` + boot-guard in main.py + `tests/engine/test_emos_ci_live_override.py`. Flag-OFF byte-identical proven. **HAS the degenerate-bin bug + the coarse-NO-lcb** — both must be fixed before this is ever armed.
- aa3bb32a (IN FLIGHT): degenerate-bin fix — modifying emos.py + event_reactor_adapter.py + score_emos_forward.py.

## KEY ARTIFACTS / FILES
- Design: `/tmp/design_emos_ci.md` (EMOS-CI architecture, §6 = live-flip seam). Critic: `/tmp/critic_emos_ci.md`.
- `state/emos_calibration.json` — EMOS fit (200 cells, 180 emos / 20 raw; mu=a+b·x̄, sigma2=exp(c+d·logS²+e·lead); fit 2024+2025, OOS-tested 2026).
- `scripts/validate_analytic_ci_coverage.py` — backward coverage (PIT/cov90/k_cov per city, EMOS variant). NOTE: uses the PREDICTIVE PIT (correct), NOT the broken bin emos_q.
- `scripts/score_emos_forward.py` — forward scorer (consistency-gated, per-date PIT, k_cov counterfactual). Currently n_scored=0 (no clean settled date).
- `src/calibration/emos.py` (serve), `emos_ci_shadow.py` (compute_robust_edge + solve_k_cov, k_cov≥1 clamp), `emos_ci_license.py` (license loader).

## ROOT FINDINGS (durable)
- **0 fills root**: `decision_events` table = 0 rows EVER → 100% upstream rejection, NOT the arm gate. Honest CI (EMOS k_cov) is the unblock — but bare-analytic CI under-covers 0/16 (false confidence); MC resample CI is artifact-wide BUT accidentally compensates for ensemble under-dispersion (realized err 1.4-2.2°C ≫ 0.28°C instrument). See memory feedback-mc-ci-compensates-ensemble-underdispersion.
- **NYC 935 CALIBRATION reject = metric='low'** (zero low:* Platt, LOW never fit; #54). HIGH trustworthy cities NOT calibration-blocked.
- **3 data-quality bugs this session, all found by operator "check/show me" pushes**: (1) scorer scored stale rows; (2) pytest contaminated the live ledger; (3) degenerate-bin emos_q=0. Pattern/lesson: verify the actual DECISION VALUES (emos_q, q), not just that the pipeline runs. (Memory: feedback-shadow-ledger-test-contamination.)

## NEXT SESSION — ORDERED
1. Land degenerate-bin fix (aa3bb32a). Verify emos_q non-zero + MECE sum~1. Commit. RESTART daemon (git rev-parse HEAD first) so the forward proof records correct emos_q.
2. Read `/tmp/causality_audit.md` → fix any future-data leak (universal). Mandate #1.
3. Re-extract the REAL shadow candidate orders (all cities) → give to operator. Verify buy_yes/buy_no directions, edge sanity, universality.
4. Verify EMOS μ shifts are honest per-city (no huge-temp-diff bias). Mandate #3 universal.
5. Switch the shadow NO-lcb to an independent NO bound (promotion-gate item from the buy/no audit) before any live wiring use.
6. Only after universal correctness (causality clean + buy/no proven + emos_q correct + μ honest): re-assess where genuine edge exists → operator-gated arm of a TIGHT canary → first FILL → e2e verify → 3 → 守护. Flood-cap #99 (max_orders_per_day 1000→~5) + bridge flag G29 (`edli_user_channel_reconcile_enabled`) + canary artifact are pre-arm prerequisites.

## HARD RULES (this session, preserve)
- SOLE committer/restarter; `git rev-parse HEAD` before any restart. Arm flag = operator's IRREVERSIBLE gate — NEVER flip without explicit "arm it". config/.claude settings OPERATIONAL — never commit. No AI trailers in commits. Bash stdout can be hijacked by the codegraph hook → write python→/tmp→Read. Verify recorded VALUES, not just that the pipeline ran.
