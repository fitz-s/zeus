# Dropped-Context Seam Ledger — EDLI reactor → shared engines

```
# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: DESIGN_VS_LIVE_PROBABILITY_2026-06-01.md; KELLY_PORTFOLIO_GAP_2026-06-01.md;
#   EDLI_BIAS_CORRECTION_FULL_SPEC_2026-06-01.md; src/engine/evaluator.py (design-faithful reference);
#   src/engine/cycle_runner.py; HEAD 6fcd05a69f
# Mode: READ-ONLY enumeration. No edits / git / DB writes. Every seam grep-verified at HEAD.
```

## The K=1 structural decision (Fitz #1/#2)

The EDLI reactor (`src/engine/event_reactor_adapter.py`) reaches the SAME shared engines the
broad cycle uses (`MarketAnalysis`, `dynamic_kelly_mult`, `kelly_size`, `robust_trade_score`,
the Platt bias path), but it constructs its inputs from a **flat scalar receipt payload**
(`EventSubmissionReceipt` + `_CandidateProof`) instead of threading the **live decision object**
that `evaluator.py` carries across the same boundary. Each shared engine has a permissive
default for the missing context (`calibrator=None`, `lead_days=3.0`, `kelly_mult=0.25`,
`portfolio_heat=0`, no oracle, no phase, no risk level), so the absence **degrades silently** —
the engine returns a number, never an error. The number is then persisted into receipts and (once
armed) drives a live order. This is ONE dropped-context seam repeated N times, not N independent
bugs. The evaluator threads a rich object; the reactor threads scalars; every place that object is
thinner is a seam.

---

## The ledger (each row grep-verified at HEAD 6fcd05a69f)

For each: **seam (caller)** → **dropped context** → **engine's silent degradation** → **live-q/sizing impact** → **design-faithful fix**.

### SEAM 1 — `MarketAnalysis` built without `calibrator=` / `lead_days=` → bootstrap CI on RAW surface (HIGH)
- **Seam**: `event_reactor_adapter.py:3356-3377` (`_market_analysis_from_event_snapshot` constructs `MarketAnalysis(...)`).
- **Dropped**: the fitted `ExtendedPlattCalibrator` (`calibrator=`) and the real `lead_days=`. Evaluator passes both (`evaluator.py:5182-5183`).
- **Silent degradation**: defaults `calibrator=None`, `lead_days=3.0` (`market_analysis.py:174-175`). Inside the shared `_bootstrap_bin` (`market_analysis.py:752-788`) → `has_platt=False` → every bootstrap iteration sets `p_cal_boot = p_raw_all` (RAW Monte-Carlo, uncalibrated) and omits the §8.2 σ_parameter layer (A,B,C resampling).
- **Live-q impact**: the POINT q_posterior is correctly calibrated (the point `_snapshot_p_cal` DOES apply A·logit+B·lead_days+C, `:3689`), but the **CI around it is drawn in raw-probability space and is missing Platt uncertainty** → `q_lcb_5pct`, FDR p_value, prefilter (`ci_lower>0`), trade_score gate are wrong → suppresses/admits the wrong candidates. Always-on (no flag).
- **Fix**: pass `calibrator=cal` + `lead_days=lead_days` into the EDLI `MarketAnalysis(...)` so the bootstrap perturbs the calibrated surface with parameter sampling. Restores §8.2 without touching the (correct) point serve.
- **Source**: `DESIGN_VS_LIVE_PROBABILITY_2026-06-01.md` (this seam is the index case).

### SEAM 2 — Kelly sized without portfolio-heat / held-position context → unbounded book (HIGH)
- **Seam**: `event_reactor_adapter.py:810-828` (per-candidate sizing block) → `money_path_adapters.evaluate_kelly:81-100` → `kelly.py:30-58 kelly_size(p, price, bankroll, kelly_mult)`.
- **Dropped**: `current_heat` (cumulative held exposure), per-city held exposure, held-position count. Evaluator computes `current_heat = portfolio_heat_for_bankroll(...)` and threads it into both `dynamic_kelly_mult(portfolio_heat=current_heat)` (`evaluator.py:6001`) AND a post-Kelly `would_breach(...)` hard gate (`risk_limits.py:24-60`).
- **Silent degradation**: `kelly_size` sizes against the FULL `bankroll` argument with no N, no heat term; the only bound is a flat per-ORDER clamp `tiny_live_max_notional_usd` (`event_reactor_adapter.py:1615-1616`, `settings.json:122`). `would_breach`/`portfolio_heat`/`max_single_position` return **ZERO** matches in the reactor (grep-verified).
- **Live-sizing impact**: `$43.01 = f*0.93 × 0.25 × $185` single bin = 23% of bankroll, above `max_single_position_pct=0.10`; total book = N × $43 bounded by **nothing**. Per-position cap, portfolio-heat ceiling (0.50), per-city cap (0.20) all ABSENT.
- **Fix**: load live `PortfolioState`, compute `current_heat` once per cycle, thread into sizing; replace flat 0.25 with heat-aware mult; add `would_breach(...)` hard gate before live-cap reservation. Corrected max single bid $18.50, book ceiling $92.50.
- **Source**: `KELLY_PORTFOLIO_GAP_2026-06-01.md` (PART 2-4).

### SEAM 3 — flat `0.25` instead of `dynamic_kelly_mult(...)` → drops 6 sizing signals (HIGH)
- **Seam**: `event_reactor_adapter.py:814-815` `kelly_multiplier = _runtime_kelly_multiplier()` → `settings["sizing"]["kelly_multiplier"]` = constant **0.25** (`:4360-4366`). The reactor NEVER calls `dynamic_kelly_mult` (grep: **0 matches**).
- **Dropped**: every arg `dynamic_kelly_mult` consumes (`kelly.py:433-445`): `ci_width`, `lead_days`, `rolling_win_rate_20`, `portfolio_heat`, `drawdown_pct`/`max_drawdown`, `strategy_key`, `city`. Evaluator passes ci_width, lead_days, portfolio_heat, city (`evaluator.py:5997-6013`).
- **Silent degradation**: the raw 0.25 has none of the haircuts. CI-width haircut (×0.7/×0.5), lead-time decay (×0.6/×0.8), losing-streak throttle (×0.5/×0.7), portfolio-heat throttle (`×max(0.1,1−heat)`), drawdown proportional reduction (`kelly.py:466-505`) — ALL bypassed. (Note: the bias-decay haircut at `:815-820` is the ONLY haircut the reactor applies, and only per-city bias.)
- **Live-sizing impact**: over-sizes on wide CI, long lead, losing streaks, high heat, and drawdown simultaneously — the union of every sizing-discipline signal is dropped. #103.
- **Fix**: replace `_runtime_kelly_multiplier()` with `dynamic_kelly_mult(base=0.25, ci_width=edge.ci_upper−edge.ci_lower, lead_days=lead_days, rolling_win_rate_20=…, portfolio_heat=current_heat, drawdown_pct=…, city=family.city)`.
- **Source**: `KELLY_PORTFOLIO_GAP_2026-06-01.md` (PART 3 row "Dynamic multiplier"); #103.

### SEAM 4 — bias served without OOS-gate provenance; forced-identity Platt on corrected domain (LOW, currently OFF)
- **Seam**: `event_reactor_adapter.py:3487-3567` (`_maybe_apply_edli_bias_correction`, shift at `:3552`) + `:3624-3629` (identity-Platt when `_edli_bias_corrected`).
- **Dropped**: the fitter's OWN settled-OOS gate context. `ens_bias_model.build_candidate_biases:351-391` says "raw is served — by design" until the OOS-gate (LCB + BH-FDR) clears; the reactor reads `model_bias_ens` rows **without** requiring a preserved, current-regime, independently-verified settled-OOS receipt. And when applied, it FORCES identity Platt (drops the fitted A/B/C) because the Platt was trained on uncorrected p_raw.
- **Silent degradation**: a row promoted without clearing the gate (the live −1.58°C Singapore row was) shifts live q by its full magnitude (Singapore 31→32 modal, wrong for the settled day); the Platt stage is bypassed.
- **Live-q impact**: warps the traded q modal bin → buys NO on the raw-modal bin. Currently INERT — `edli_bias_correction_enabled` set FALSE 2026-06-01 (q reverts to raw). Latent: re-flips the instant the flag is set without the gate.
- **Fix**: P3 correction-provenance gate — apply a row ONLY if it carries a pointer to a preserved, in-repo, current-regime, independently-signed settled-OOS receipt; else fail-closed to raw per city. (Mirrors data-provenance `authority=UNVERIFIED` law.)
- **Source**: `EDLI_BIAS_CORRECTION_FULL_SPEC_2026-06-01.md` (§6 P3, §8.1, §10); #58.

### SEAM 5 — α not computed per-decision (LOW, inert under model-only)
- **Seam**: `event_reactor_adapter.py:3363` hardcodes `alpha = settings["edge"]["base_alpha"]["level1"]`.
- **Dropped**: `compute_alpha(calibration_level, ensemble_spread, model_agreement, lead_days, hours_since_open, authority_verified)`. Evaluator computes it (`evaluator.py:4970-4977`) and passes the result (`:5179`).
- **Silent degradation**: `MarketAnalysis` accepts the scalar; under `MODEL_ONLY_POSTERIOR_MODE` the posterior ignores α entirely (`market_fusion.py:294-301`), so it is inert TODAY.
- **Live-q impact**: none currently (model-only). Latent if market fusion is ever enabled — the reactor would fuse at a static α blind to calibration maturity / spread / agreement / lead.
- **Fix**: call `compute_alpha(...)` from the snapshot's live context if/when fusion is enabled; until then, document the inert scalar.
- **Source**: `DESIGN_VS_LIVE_PROBABILITY_2026-06-01.md` (stage 4b).

### SEAM 6 — RiskLevel consumed as a binary GREEN gate, never threaded into sizing (MEDIUM)
- **Seam**: `event_reactor_adapter.py:213-219` `riskguard_allows_new_entries` returns `level == RiskLevel.GREEN`; `evaluate_riskguard(level)` (`:887-892`) passes/fails. RiskLevel reaches the reactor ONLY as this binary admit gate.
- **Dropped**: the graded YELLOW/ORANGE/RED **sizing de-rating** and the regime/heat-saturation throttle the evaluator applies. Evaluator: `risk_throttle` (regime variance ×0.5 at `evaluator.py:5985`, global-heat ×0.5 at `:5986-5988`) plus the elevated-risk-state reduction folded into `dynamic_kelly_mult` (math spec §10.2 / `zeus_math_spec.md:458-461`).
- **Silent degradation**: under YELLOW/ORANGE the reactor either fully admits at flat 0.25 (if GREEN gate is widened) or fully blocks (if not GREEN) — there is no in-between graded de-rate. The reactor has no `risk_throttle`, no regime-variance term (grep: `risk_state`/`drawdown`/`rolling_win` = 0).
- **Live-sizing impact**: a YELLOW/ORANGE regime that the design would size DOWN (not block) is mis-handled — binary on/off vs the design's continuous posture de-rating.
- **Fix**: thread the graded `RiskLevel` (+ regime variance + global heat) into the sizing multiplier (the same `dynamic_kelly_mult` / `risk_throttle` path SEAM 3 restores), not just the binary entry gate.
- **Source**: this audit (new); cross-ref `zeus_math_spec.md:458-461`, `evaluator.py:5985-5988`.

### SEAM 7 — DDD oracle-discount not evaluated (MEDIUM)
- **Seam**: the reactor sizing/score path; `evaluate_ddd`/`get_oracle_info`/`oracle_penalty` return **ZERO** matches in `event_reactor_adapter.py` (grep-verified).
- **Dropped**: the Dynamic-Discount-on-Disagreement / oracle-status discount the evaluator threads. Evaluator: `oracle = get_oracle_info(city, metric)` (`evaluator.py:5807`); `ddd_result = evaluate_ddd_for_decision(...)` (`:5844`) with `HALT` action (`:5874`) and `final_discount_pre_mismatch` (`:5902`); `oracle_penalty` (METRIC_UNSUPPORTED → 0) is a hard veto in `dynamic_kelly_mult`'s consumers.
- **Silent degradation**: without DDD/oracle the reactor never HALTs on settlement-source disagreement and never discounts sizing for an unsupported/contested oracle metric — it sizes as if the oracle were fully trusted.
- **Live-sizing impact**: a metric with no supported oracle (or a DDD-HALT condition) is sized at full confidence in the reactor; the evaluator would zero or discount it.
- **Fix**: evaluate `get_oracle_info` + `evaluate_ddd_for_decision` per candidate; apply HALT as a fail-closed reject and the discount as a multiplier in the sizing path.
- **Source**: this audit (new); cross-ref `evaluator.py:5807-5902`, `src/engine/ddd_wiring.py`, `src/strategy/oracle_penalty.py`.

### SEAM 8 — per-strategy live-quality policy + source-quality haircut not applied (MEDIUM)
- **Seam**: the reactor uses a single hardcoded `strategy_key="entry_forecast"` for the forecast READER scope only (`event_reactor_adapter.py:4612,4717`); it applies no per-strategy sizing/quality POLICY. `_strategy_live_quality_policy`, `source_quality_kelly_haircut`, `probability_edge_bin_sanity` = **ZERO** matches (grep-verified).
- **Dropped**: the per-strategy policy object the evaluator resolves per edge: `policy.gated/exit_only` (`evaluator.py:6030-6048`), `_strategy_live_quality_policy` (entry-price floor, fill-prob floor), `_source_quality_kelly_haircut(strategy_key, ens_result)` (`:5590`), `probability_edge_bin_sanity` (`:5600-5610`), `phase_aware_kelly_multiplier(strategy_key, phase, …)` (`:6089`).
- **Silent degradation**: the reactor treats every candidate as one generic strategy at flat sizing — no exit-only/gated strategy honoring, no partial-source haircut, no per-strategy entry-price/fill floors, no phase-aware multiplier.
- **Live-sizing impact**: a strategy the registry marks `exit_only`/`gated`, or an edge built on partial source data, or a wrong-phase market is sized identically to a clean entry_forecast edge.
- **Fix**: resolve the per-candidate `strategy_key` → `StrategyPolicy` and thread its gates + haircuts + `phase_aware_kelly_multiplier` into the reactor sizing path (the same live decision object).
- **Source**: this audit (new); cross-ref `evaluator.py:1232-1517,5569-5610,6030-6097`.

---

## Severity-ranked summary

| # | Seam (caller) | Dropped context | Degradation default | Severity | Status |
|---|---|---|---|---|---|
| 1 | `MarketAnalysis(...)` `:3356-3377` | `calibrator`, `lead_days` | `None` / `3.0` → bootstrap on RAW p_raw, no σ_parameter | **HIGH** | always-on |
| 2 | sizing `:810-828`→`kelly_size` | `current_heat`, held positions | full-bankroll, only per-order clamp | **HIGH** | pre-live (shadow) |
| 3 | `_runtime_kelly_multiplier` `:814` | ci_width/lead/win-rate/heat/drawdown/strategy/city | flat 0.25, all haircuts bypassed | **HIGH** | always-on |
| 6 | `evaluate_riskguard` `:887` | graded YELLOW/ORANGE/RED + regime/heat throttle | binary GREEN gate only | **MEDIUM** | always-on |
| 7 | sizing path (no DDD/oracle) | DDD discount, oracle status, HALT | no discount, no halt | **MEDIUM** | always-on |
| 8 | hardcoded `entry_forecast` `:4612` | per-strategy policy + source/phase haircuts | one generic strategy, flat | **MEDIUM** | always-on |
| 4 | `_maybe_apply_edli_bias_correction` `:3487` | settled-OOS gate provenance; identity-Platt | shifts q full magnitude, Platt bypassed | **LOW** | OFF (2026-06-01) |
| 5 | `alpha=base_alpha.level1` `:3363` | `compute_alpha(...)` inputs | static α, inert under model-only | **LOW** | inert |

**Seam count: 8** (3 HIGH, 3 MEDIUM, 2 LOW). All 8 are the SAME failure shape: reactor hands a stripped scalar where `evaluator.py` threads the live decision object; the shared engine's permissive default absorbs the gap silently.

---

## Unifying-fix verdict

**ONE structural decision closes all eight, not N independent wirings.** Every seam is the
reactor failing to assemble — and thread — a single **`LiveDecisionContext`**: the live decision
object that `evaluator.py` already carries (fitted calibrator + lead_days, portfolio heat + held
positions, graded RiskLevel + regime throttle, oracle/DDD status, per-strategy policy, phase
evidence). The eight engines (`MarketAnalysis`, `dynamic_kelly_mult`, `kelly_size` + `would_breach`,
the bias path, `compute_alpha`, the risk de-rate, DDD/oracle, the strategy policy) each already
accept this context; they only degrade because the reactor passes the permissive defaults.

So the fix is structurally ONE refactor with two parts:
1. **Build the context once per reactor cycle/event** — load `PortfolioState`/`current_heat`, the
   graded `RiskLevel` + regime variance, oracle/DDD status, the per-candidate `strategy_key`→policy,
   the fitted calibrator + lead_days (already in scope at `_snapshot_p_cal`), and phase evidence
   (already computed at `:626` for the admit gate, then discarded for sizing).
2. **Thread it through every shared-engine call** — pass `calibrator`/`lead_days` to `MarketAnalysis`,
   replace flat-0.25 with `dynamic_kelly_mult(context)`, add the post-Kelly `would_breach(context)`
   gate, fold the graded RiskLevel + DDD/oracle + strategy policy into the same multiplier chain,
   and gate the bias row on its OOS-provenance.

This is NOT 8 unrelated patches (that would be the whack-a-mole Fitz #1 warns against — each patch
boundary spawns the next bug). It is the single unexecuted decision "**route every reactor decision
through the same live-decision context the broad cycle uses**", executed once in `evaluator.py` and
never in the reactor. The antibody (Fitz #2/#3): a relationship test asserting *"for an identical
(snapshot, family, portfolio, risk, oracle) input, the reactor's q, q_lcb, and size equal the broad
evaluator's"* — RED until the context is threaded, GREEN after, and it makes the entire seam category
unconstructable rather than fixing 8 instances.

## References
- `DESIGN_VS_LIVE_PROBABILITY_2026-06-01.md` — SEAM 1, 5.
- `KELLY_PORTFOLIO_GAP_2026-06-01.md` — SEAM 2, 3.
- `EDLI_BIAS_CORRECTION_FULL_SPEC_2026-06-01.md` — SEAM 4.
- `src/engine/evaluator.py:4970-6097` — the reference live-decision-object threading (all seams' design-faithful target).
- `src/engine/event_reactor_adapter.py:810-828,3356-3377,3363,3487-3567,4612` — the reactor seam sites.
- `src/strategy/kelly.py:30-58,433-505` — `kelly_size` / `dynamic_kelly_mult`.
- `src/strategy/market_analysis.py:166-198,752-788` — `MarketAnalysis` ctor defaults + shared bootstrap.
- `src/strategy/risk_limits.py:24-60` — `would_breach`. `src/engine/ddd_wiring.py`, `src/strategy/oracle_penalty.py` — DDD/oracle.
- Grep-verified absences at HEAD 6fcd05a69f: `dynamic_kelly_mult`, `phase_aware_kelly`, `evaluate_ddd`, `get_oracle_info`, `oracle_penalty`, `EffectiveKellyContext`, `source_quality_kelly_haircut`, `probability_edge_bin_sanity`, `portfolio_heat_for_bankroll`, `would_breach`, `_strategy_live_quality_policy`, `rolling_win_rate`, `drawdown` → **all 0 in `event_reactor_adapter.py`**.
