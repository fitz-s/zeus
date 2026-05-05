# Math defects 2.3 / 2.4 / 3.1 — unified design 2026-05-05

**Authority basis**: architect-opus 2026-05-05 unified plan, post Phase 0a migration + DDD INV-17 fix + calibration transfer scaffolding.

## TL;DR

| Issue | Verdict | Phase α scope (NOW) |
|---|---|---|
| 2.3 oracle LOW track | **Already fail-closed** via `oracle_penalty.py:472` METRIC_UNSUPPORTED → mult=0.0. LOW bridge blocked on upstream listener (separate roadmap). | docstring + invariant test |
| 2.4 bootstrap transfer uncertainty | Add `transfer_logit_sigma: float = 0.0` to MarketAnalysis, additive logit-space noise in bootstrap, helper `compute_transfer_logit_sigma(brier_diff, scale=4.0)`. Default 0.0 = byte-identical. | refactor + helper + config + 4 tests |
| 3.1 extractor period-step verification | Contract test + pure-Python helper `predicted_step_set_for_target`. Structural cleanup: unify `compute_required_max_step` ≡ `required_period_end_steps()[-1]`. | contract test + helper |

**Rollout**: 3 Phase α dispatch concurrently (no cross-deps). Phase β/γ collapse to one operator gate (`ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED` flip + `transfer_logit_sigma_scale` config).

---

## Issue 2.4 — Bootstrap transfer uncertainty

### Current state
- `src/strategy/market_analysis.py:3-7` docstring: 3 σ layers (ENS / instrument / Platt parameter).
- `_bootstrap_bin` 386-459 + `_bootstrap_bin_no` 461-521: same 3-layer loop.
- Platt sampling at line 415 uses `self._calibrator.bootstrap_params` only. **No domain awareness.**
- `validated_calibration_transfers` schema (`v2_schema.py:386-417`) carries `brier_source`/`brier_target`/`brier_diff` — exact moments needed.
- `MarketAnalysis` instantiated `evaluator.py:2736-2757`. Today no transfer-uncertainty parameter passed.

### Verdict: logit-space additive σ

Add `transfer_logit_sigma: float = 0.0` to `MarketAnalysis.__init__`. Bootstrap loop adds `rng.normal(0, transfer_logit_sigma)` to the `z` term at lines 444 + 506.

Why logit-additive (not multiplicative, not (A,B,C)-covariance inflation):
- Platt sigmoid IS logit-space; transfer uncertainty composes naturally with `(A,B,C)` parameter sampling instead of replacing it
- Multiplicative dampening discards directional information
- (A,B,C) covariance inflation requires synthesizing fake rows — no clean math

Mapping `brier_diff → σ`: `compute_transfer_logit_sigma(brier_diff, scale=4.0) = sqrt(max(0, brier_diff)) * scale`. Scale 4.0 ≈ logit slope at p=0.5. Operator-tunable in `config/settings.json::transfer_logit_sigma_scale`.

### Tradeoff matrix

| Option | Pro | Con |
|---|---|---|
| Logit additive (✅) | Composes with existing sampling; zero impact same-domain | Assumes Gaussian; bin-uniform |
| Inflate (A,B,C) covariance | More principled if cov available | Requires per-row covariance; not in schema |
| Multiplicative dampening | Trivial | Loses asymmetry |
| Per-bin variance | Range-label sensitive | 8× parameters, per-bin OOS rows |

### Evidence that flips verdict
- If post-X.2 OOS evaluator finds `brier_diff` distributions are bimodal/heavy-tailed → push to bootstrap from empirical residuals
- If `brier_diff` is bin-width sensitive → per-bin variance instead

### Phases
- **α (NOW)**: arg + helper + config; default 0.0 → behavior identical
- **β (post Phase 1 + X.2)**: evaluator reads row, computes σ, passes to MarketAnalysis
- **γ (operator)**: flip `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED=true` — same gate as 2.2 X.3

### Tests
- `test_transfer_sigma_zero_matches_legacy_3layer` — byte-identical at fixed RNG seed
- `test_transfer_sigma_widens_ci_monotonically`
- `test_transfer_sigma_preserves_p_value_sign_at_zero_edge`
- `test_helper_brier_diff_to_sigma_monotonic`

### Cross-deps
Phase β depends on Issue 2.2 X.2 OOS evaluator row coverage.

---

## Issue 2.3 — LOW track + missing-OK (SCOPE SHRUNK)

### Current state (already partially fixed — critic earlier verdict was literal-but-incomplete)
- `bridge_oracle_to_calibration.py:81` `AND temperature_metric='high'` ✓
- `oracle_penalty.py:472-478`: LOW → `METRIC_UNSUPPORTED` with `block_reason="LOW oracle bridge not yet shipped"`. `_BASE_MULTIPLIER[METRIC_UNSUPPORTED]=0.0` (line 105). **LOW Kelly mult = 0.0 = fail-closed.**
- Lines 455-498: 4-tier resolution (METRIC_UNSUPPORTED → MALFORMED → MISSING → record-classify). Missing→OK defect IS closed.

### Verdict: scope shrink — keep fail-closed, don't ship LOW bridge mirror now
- LOW bridge requires upstream `_snapshot_daily_low` capture (HKO CLMMINT etc.) — no listener writes it today. 3-stage upstream job, not a math fix.
- HKO `CLMMINT` semantics may differ from `CLMMAXT` rounding/timing — mirror-HIGH symmetry intuition is wrong without listener audit.
- Live LOW execution = 0 today (mult=0.0). Daemon LOCKED. Zero production loss waiting.

### Phase α (NOW): docstring + invariant
- `bridge_oracle_to_calibration.py:81` add docstring citing LOW listener gap + pointing at `oracle_penalty.py:472` as load-bearing fail-closed gate
- Test: `get_oracle_info(any_city, "low").penalty_multiplier == 0.0`

### Cross-deps: none

---

## Issue 3.1 — Extractor period-step verification

### Current state
- `src/data/forecast_target_contract.py:71-94` `required_period_end_steps()` correct (UTC-coerced, ceil-rounded).
- `scripts/_tigge_common.py:75-91` `compute_required_max_step()` is a **different function** with different semantics (single max int, fixed-offset tz).
- `scripts/extract_tigge_*_localday_*.py:85, 320` consumes only `compute_required_max_step` — never `required_period_end_steps`.
- **Two-implementation drift surface**, no shared invariant.

### Verdict: contract test + structural unification

K=1 structural decision (Fitz #1): `compute_required_max_step` should be `required_period_end_steps()[-1]` — make the drift category impossible.

Phase α: 
1. Pure-Python helper `predicted_step_set_for_target(issue_utc, target_date, city_tz, period_hours=6)` in `_tigge_common.py`
2. Contract test asserts `set(required_period_end_steps(...)) ⊆ extractor_step_set` for coverage matrix (cities × cycles × target offsets × seasons inc. DST cases)
3. Invariant test: `compute_required_max_step ≡ required_period_end_steps()[-1]`

### Tradeoff matrix

| Option | Pro | Con |
|---|---|---|
| (a) CI contract test (✅) | Catches drift early | Cloud-side untested unless shared imports |
| (b) Runtime assertion in extractor | Catches in production | Production block on edge cases |
| (c) Post-extraction read-back | Verifies actual emission | I/O dependent, slow, flaky |
| (d) Unify two functions (✅ as follow-up) | Fitz K=1 fix | Touches helpers, test imports |

Best path: (a) NOW + (d) follow-up.

### Cross-deps: none

---

## Unified rollout

**Parallelizable (Phase α):**
- 2.4 Phase α (sonnet) — refactor + helper + config + 4 tests
- 2.3 Phase α (haiku) — docstring + invariant test (~10 lines)
- 3.1 Phase α (sonnet) — contract test + helper

**Sequential (post Phase 1 + X.2):**
- 2.4 Phase β + 2.2 X.2 OOS evaluator land together (evaluator writes rows; bootstrap reads them)

**One operator gate covers γ for both 2.4 and 2.2:**
- Flip `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED=false → true`
- Pre-flip: `validated_calibration_transfers` row count ≥ 200 per live-targeted bucket; dry-run trade count delta acceptable

## Cross-cutting risk callouts (architect)

1. **Translation-loss `required_period_end_steps` vs `compute_required_max_step`** — Issue 3.1 contract test is the antibody. Without it, calibration_pairs may be polluted, contaminating the very `brier_diff` Issue 2.4 reads.
2. **Operator-gate stacking** — single flag flip activates BOTH 2.2 evidence-gating AND 2.4 σ-inflation. Present as ONE decision.
3. **`live_promotion_approved` deprecation** — when flag flips, callers passing `live_promotion_approved=True` silently lose effect. Need 1-line deprecation log or callsite audit before flip.
4. **σ × FDR interaction** — wider transfer σ → wider CI → fewer significant edges → fewer trades. Pre-flip dry run required to avoid trade-count surprise.
5. **LOW track HKO semantics** — future LOW bridge PR must audit CLMMINT vs CLMMAXT rounding direction. Mirror-symmetry is wrong.

## Single integrated operator decision delta

1. `config/settings.json::transfer_logit_sigma_scale` (default 4.0) — principled default; tune only if post-Phase-1 OOS empirics warrant
2. Single flag flip `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED` — activates BOTH evidence-gated transfer policy AND non-zero σ
3. LOW track activation — out of scope; revisit when LOW listener ships
