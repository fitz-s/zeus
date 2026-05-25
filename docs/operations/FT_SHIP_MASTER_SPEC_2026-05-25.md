# full_transport → live: Master Ship Spec (operator-authored, 2026-05-25)

# Created: 2026-05-25
# Authority basis: operator specification 2026-05-25 (domain-identity doctrine) + investigation probes (HK provenance, sentinel, ft-posterior, live-wiring, ship-mechanics) on PR #340.

## Central principle
A live forecast signal MUST correspond to ONE complete, reproducible, explicitly-authorized **probability domain**:
```
domain = { p_raw generator code, error-model parameters, member_extrema source lineage,
           bins / rounding / settlement semantics, MC seed law, calibration family,
           Platt-or-identity-calibrator policy, live routing / pin }
```
A refit is a **production artifact** only if ALL hold: (1) p_raw generator on main; (2) error-model params persisted; (3) calibration_pairs tied to error_model_key; (4) Platt/identity coverage complete; (5) live p_raw wiring uses the same error_model_key; (6) explicit pin; (7) trace/replay evidence. Missing any → **research artifact**. The day's waste = treating a research artifact as production.

## Total-function rule (no implicit fallback)
For every live-eligible bucket, exactly one EXPLICIT route:
- **learned Platt** (maturity sufficient AND blocked-OOS improves), or
- **certified identity calibrator** — explicit row `calibration_method=identity_full_transport_v1`, `p_cal=p_raw`, `model_key` pinned, `authority=VERIFIED` (used when ft p_raw ECE already low; this is the authorized p_raw-direct route — NOT a missing-Platt fallback, which the evaluator blocks before edge/FDR), or
- **legacy** route, or
- **no-trade / shadow**.
No orphan buckets, no "newest VERIFIED wins", no implicit fallback.

## Phase order (the only correct path)

**Phase 0 — protect asset (no prod write).** Copy `/private/tmp/ens_refit/full.db` → durable `state/backups/ens_refit_full_2026-05-25.db`. Read-only; if tmp-cleaned, only then is a 20h rerun real.

**Phase 1 — code/schema first (zero prod write).**
1. Canonical error-model table (new `ens_error_model_v1` or extended `model_bias_ens_v2`) with FULL fields: error_model_key, error_model_family, city, metric, season, month/bucket, live_data_version, prior_data_version, transport_delta_policy, bias_c, bias_sd_c, residual_sd_c, heterogeneity_var_c2, correction_strength, effective_bias_c, total_residual_sd_c, n_live, n_prior, n_paired, paired_delta_c, training_cutoff, code_commit, fit_signature_hash, authority, recorded_at. (residual_sd_c + heterogeneity_var_c2 are required to reconstruct PredictiveErrorModel live.)
2. Fix writer bug (`onboard_cities.py` `.posterior.bias` → `.bias_c`).
3. Port posterior producer onto main: `fit_full_transport_error_models.py` (inputs: TIGGE prior residuals, OpenData live residuals, paired F25–F50 deltas, training_cutoff, bucket defs → ens_error_model_v1 rows). No throwaway sub-worktree dependency.
4. Wire live p_raw at `monitor_refresh.py:453` (+:471): load ens_error_model_v1 row → `p_raw_vector_with_error_model`, **behind flag `full_transport_live_enabled` default OFF (byte-identical when off)**.
5. Fix sentinel reader (`promote_platt_models_v2.py:226`: accept `data_version in wanted_dvs OR == "all"`); keep `pairs_complete` vs `platt_complete` distinct.
→ critic/verify → **OPERATOR CHECKPOINT before any production byte.**

**Phase 2 — canonical producer run on COPY + REPLAY-EQUIVALENCE PROOF (the rerun-decider).**
Run producer on a copy/staging DB → error-model rows. Then sample N snapshots across {HK HIGH, HK LOW, Miami HIGH/LOW, coastal, inland, F/C, lead buckets, seasons} and regenerate full_transport p_raw with main code + persisted error model + same members_json + same bins + same MC seed/n_mc; compare to full.db `calibration_pairs_v2.p_raw`.
- Accept: max_abs_diff ≤ tol, same argmax bin, Brier/LogLoss within tol on sampled cohorts → **10k-MC pairs reusable, NO 20h rerun.**
- Fail: pairs belong to an unreproducible domain → **regenerate pairs under the canonical persisted domain** (legitimate MC rerun, not waste).

**Phase 3 — additive (NON-destructive) pair migration.** Insert/upsert full_transport_v1 pairs under new `error_model_family`/`p_raw_domain`; pairs identity includes error_model_family, error_model_key, n_mc=10000, generator_commit, fit_signature_hash. **Do NOT delete none/legacy pairs or legacy Platt. Never use data_version-DELETE promote semantics.**

**Phase 4 — complete calibrator coverage** (every served bucket → explicit route A/B/C per total-function rule). ECE-gated: low-ECE ft buckets get identity calibrator rows; Platt only where blocked-OOS improves.

**Phase 5 — HK HIGH via GENERIC pathology rule (not city hack).**
```
if PIT extreme-decile mass > 30% OR ECE > 5× global OR (bias sign contradicts live residual with adequate n):
    no full_transport route for this bucket; use identity/legacy/no-trade;
    require posterior refit before serving full_transport.
```
HK HIGH currently triggers this (PIT 96.9% in bin0, +6.32°C over-warm from pooled cold prior on an already-unbiased cohort). **Fix = per-cohort bias shrinkage** so a data-rich unbiased cohort anchors to its own ~0 bias; then HK HIGH passes the rule and ships — all 49, no exclusion. HK LOW (genuinely cold) keeps its win.

**Phase 6 — explicit pin.** Write `calibration.pin.frozen_as_of` + `model_keys` (`metric:cluster:season:cycle → model_key`) for every served cohort. No reliance on "newest VERIFIED wins" (the empty-pin legacy default).

**Phase 7 — copy-prod rehearsal.** On cloned world/forecasts DB: schema migrate → insert error models → insert ft pairs → insert calibrators → pin → boot daemon against copy → generate p_raw traces → verify model_key/error_model_key/p_raw_domain. No live touch.

**Phase 8 — production write only after checkpoint.** Daemon stopped/write-frozen; absolute prod DB paths confirmed (`/Users/leofitz/.openclaw/workspace-venus/zeus/state/{zeus-world,zeus-forecasts}.db`); backups; copy rehearsal passed; operator approval. Then: schema migrate → insert ft pairs → error-model rows → calibrators → pin → restart → trace → decision audit → tiny live gate.

## Antibody — executable ship-readiness gate (CI/script, not a report)
```
full_transport_ship_readiness:
  pairs_complete == true
  error_models_persisted == true
  p_raw_replay_equivalence_pass == true
  platt_or_identity_coverage_complete == true
  hk_high_or_pathology_carveouts_declared == true
  sentinel_complete == true
  calibration_pin_complete == true
  live_wiring_flag_off_byte_identical == true
  live_trace_smoke_pass == true
```
No evaluation artifact may be called promotable unless all pass.

## On "move pairs to prod then rerun everything"
- ALLOWED: additive migration of complete ft pairs to prod forecasts.db **after** replay-equivalence proof passes + schema supports error_model_key + non-destructive + legacy retained.
- FORBIDDEN: push pairs → promote partial Platt → let live auto-select newest. Domain identity / coverage / routing authority not closed.
- "Everything rerun?" — only the MC pairs, and only IF replay-equivalence FAILS. Otherwise: producer + Platt/identity + replay, no 20h.
