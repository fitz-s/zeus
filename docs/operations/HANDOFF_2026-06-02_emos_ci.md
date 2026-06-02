# HANDOFF — EMOS-CI shadow / GOAL#36 — 2026-06-02

Created: 2026-06-02
Authority basis: this session's work on GOAL#36 (3 verified fills). Read root AGENTS.md + .claude/CLAUDE.md first.

## TL;DR
GOAL#36 (3 FILLED orders e2e + 120-min 守护) **NOT met**: 0 fills, **zero validated alpha**, arm OFF, **zero capital moved**. Daemon shadow, healthy. The session built the EMOS-CI honest-CI substrate, found (via the operator's "check / show me the orders" pushes) the **degenerate-bin bug**, and this turn LANDED + verified the fix and restarted the daemon on it. With the now-correct shadow ledger, the next blocker is fully characterized: **EMOS μ applies a large (+2 to +3.5°C) warm correction vs the raw ensemble** — fitted+OOS-gated-through-2025, but 2026-unvalidated; INERT on live (flag-OFF). Operator's 3 standing mandates govern all further work. DO NOT cherry-pick cities; DO NOT arm without explicit "arm it".

## STATUS DELTA THIS TURN (2026-06-02 ~19:05 UTC)
- **Degenerate-bin fix LANDED + verified + live.** Committed `d95f5e67` (pushed to PR #370). `bin_probability_settlement` wired into shadow hook + live override + scorer. Daemon RESTARTED via `launchctl kickstart -k com.zeus.live-trading` → **new PID 21616** on d95f5e67, boot clean (on-chain $185.15, world v43 / forecasts v7 current, CLOB V2 live, arm OFF). DECISIVE verification: 468 post-restart interior EMOS bins, **0 surviving-bug** (no near-μ bin is zero); per-family peak sits at μ. Tests 80 pass + my independent MECE sum=1.000000 harness.
- **Causality audit DONE** (`/tmp/causality_audit.md`): no active future-data leak. One latent, down-only Kelly-haircut gap (bias `training_cutoff` not asserted `> target_date`) — bounded (can only shrink a position), hardening noted.
- **EMOS warm-shift characterized** (the "huge temp diff" concern): see updated §SECOND OPEN CONCERN — it's the #1 EMOS-CI promotion blocker, inert on live.

## OPERATOR'S 3 MANDATES (current law — supersede the per-city-license approach)
1. **Causality first**: the system must STOP using future data for current trades. (Audit in flight → `/tmp/causality_audit.md`.)
2. **Buy_yes/buy_no validation correct, NO exception.** (Audit DONE: LIVE path CORRECT — see below. Shadow EMOS NO-lcb is coarse, fix before live promotion.)
3. **UNIVERSAL** — every choice must work for ALL cities. "You do not fail 30 cities and 3 work and call that valid." The per-city-license / cherry-pick approach is REJECTED. The SYSTEM must be correct for all cities; trading then follows only where genuine edge exists.

## GIT / DAEMON STATE
- Branch `edli-correctness-recover-2026-06-02`, HEAD **d95f5e67** (was 61906ec2), synced to origin, **14 ahead of main**. PR **#370** OPEN (base main) — title "feat(edli): Tier-0 correctness recovery + EMOS-CI shadow forward-proof harness". Carries the #369 recovery + the EMOS-CI shadow harness + the degenerate-bin fix.
- Worktrees: **1** (main only). 3 siblings removed this session, all preserved on branches (work-b3@e167b0c2, stat-whole-refactor@f1de9f25, claude/wf_327845a4@297b724f80 = task #109 S1 work).
- Daemon **PID 21616** (restarted this turn from 80978), launchd label **`com.zeus.live-trading`**, cwd = THIS checkout @ d95f5e67. SHADOW (`live_execution_mode=edli_shadow_no_submit`, `reactor_mode=live_no_submit`), arm `real_order_submit_enabled=false`. ZERO capital. Restart cmd: `launchctl kickstart -k gui/$(id -u)/com.zeus.live-trading` (KeepAlive; never leaves it down). Sibling daemons: riskguard `com.zeus.riskguard-live` (PID 81524), forecast `com.zeus.forecast-live` (PID 59903).
- **Ledger now CORRECT**: `state/emos_shadow_ledger.jsonl` (~34.7k lines). Rows BEFORE 2026-06-02 ~18:59 UTC have emos_q=0 (old code) — IGNORE them. Rows AFTER that are valid (verified nonzero, peaks-at-μ). The forward scorer must filter to post-restart UTC.
- config/settings.json + .claude/settings.json are OPERATIONAL — NEVER commit. The shadow-ledger flag is ON there (operational).
- **TZ trap (cost me a wrong conclusion this turn)**: `zeus-live.log` stamps are LOCAL (CDT, UTC−5); the ledger `ts` is UTC. The 13:59 local restart = 18:59 UTC. Always compare ledger ts against a UTC cutoff.

## RESOLVED THIS TURN — degenerate bin (fix d95f5e67, verified live)
WAS: `emos_q == 0.000 for EVERY interior bin` because the EMOS path priced the DISPLAY point bin `[X,X]` (zero width → Φ−Φ=0). Settlement is `round→X` = interval **[X−0.5, X+0.5)**. FIX: `bin_probability_settlement(mu,sigma,low,high)` (rounding preimage; interior [X−0.5,X+0.5); open-low (−inf,X+0.5); open-high [X−0.5,+inf)), wired into `_write_emos_shadow_ledger` + `_maybe_override_lcb_with_emos_ci` + scorer; MECE sum-to-1 antibody added. VERIFIED in the running daemon (post-restart): 0 near-μ zeros, peaks at μ, sums~1. **All pre-fix EMOS shadow analysis (509 "cold-tail-NO", 76 shadow orders, #24-pass licensing) remains VOID — do not reuse; only post-19:00-UTC-2026-06-02 ledger rows are valid.**

## SECOND OPEN CONCERN — EMOS μ warm shift ("huge temp diff = alpha−") — #1 EMOS-CI promotion blocker
With the corrected ledger, measured UNIVERSALLY (post-restart, 13 families): **EMOS μ runs warmer than the raw ensemble (emos_mu − raw_mu) by +1.6 to +3.5°C for 11/13 families** (Tel Aviv −0.4 and Wuhan +0.65 the only honest cells). The live MC q faithfully tracks raw_mu (raw_mu ≈ qlive_mode ±0.5°C) — so q is NOT corrupted; the gap is purely the EMOS correction.
- Mechanism (from `state/emos_calibration.json`, μ=a+b·x̄, n=1472/cell): Shanghai a=−5.87 **b=1.331** (+3.5 via slope); Toronto **a=+4.73** b=0.907 (+2.0 via intercept); Seoul a=+2.08; Tokyo b=1.099; Wuhan b=1.115; Tel Aviv a=0.60 b=0.960 ≈ identity.
- It is a REAL fitted correction, OOS-gated THROUGH 2025 (`do_no_harm: fit2024→gate2025; raw served where no gain`; served=emos means it beat raw on 2025 held-out). The ensemble genuinely runs cold for these cities.
- BUT: (1) magnitude is large (+3.5°C ≈ 3-4 bins of mass shift); (2) **b>1 cities (Shanghai 1.33, Wuhan, Tokyo) extrapolate aggressively at summer highs beyond the 2024-25 training envelope**; (3) **2026 (the live year) is NOT yet validated** — the fit gate stopped at 2025.
- **INERT on live**: EMOS-CI override is flag-OFF; live trades raw q. So this is NOT causing wrong trades today. It is the gate on EVER promoting EMOS-CI.
- **The validation is now running**: the corrected forward ledger records emos_mu + raw_mu + q per family; as 2026 targets settle (1-3 days) the forward scorer (`scripts/score_emos_forward.py`) measures whether emos_mu lands closer to 2026 settlement than raw_mu, per-city, universal. Promotion = operator decision gated on that 2026 forward agreement — NOT on the 2025 gate alone.

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
1. ~~Degenerate-bin fix + restart~~ DONE this turn (d95f5e67, PID 21616, verified). Forward ledger now records correct emos_q.
2. **EMOS μ warm-shift validation (the gate)**: let the forward ledger accumulate 1-3 days of 2026 settlement, then run `scripts/score_emos_forward.py` → per-city, does emos_mu land closer to settlement than raw_mu? UNIVERSAL verdict (mandate #3). Pay special attention to b>1 cities (Shanghai/Wuhan/Tokyo) at summer highs (extrapolation). If EMOS does NOT beat raw on 2026 settlement for a city, that city stays raw — no promotion. This is THE blocker before EMOS-CI can ever help fills.
3. Re-extract REAL shadow candidate orders (all cities, post-restart rows only) → operator. Verify buy_yes/buy_no directions, edge sanity, universality (mandate #2 — live path already audited CORRECT).
4. Switch the shadow NO-lcb to an independent NO bound (buy/no promotion-gate item) before any live EMOS-CI wiring use.
5. (Hardening, low) Add `family.target_date < row.training_cutoff` assertion to `_maybe_bias_decay_kelly_haircut` (causality audit's latent gap) + any future bias/grid path before flip-ON.
6. Only after universal correctness (causality clean ✓ + buy/no proven ✓ + emos_q correct ✓ + EMOS μ validated on 2026 settlement ⟵ pending): re-assess where genuine edge exists → operator-gated arm of a TIGHT canary → first FILL → e2e verify → 3 → 守护. Flood-cap #99 (max_orders_per_day 1000→~5) + bridge flag G29 (`edli_user_channel_reconcile_enabled`) + canary artifact are pre-arm prerequisites.

## HARD RULES (this session, preserve)
- SOLE committer/restarter; `git rev-parse HEAD` before any restart. Arm flag = operator's IRREVERSIBLE gate — NEVER flip without explicit "arm it". config/.claude settings OPERATIONAL — never commit. No AI trailers in commits. Bash stdout can be hijacked by the codegraph hook → write python→/tmp→Read. Verify recorded VALUES, not just that the pipeline ran.
