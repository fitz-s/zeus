# PROBABILITY INTEGRITY AUDIT — buy_yes/buy_no + units + q-faithfulness

```
# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: Operator mandate #2 (buy_yes/buy_no validation correct, units correct, UNIVERSAL, never conclude on wrong data)
#                  Branch edli-correctness-recover-2026-06-02 @ d95f5e67. READ-ONLY audit. Daemon SHADOW, arm OFF.
```

## MANDATE-2 VERDICT (one line)

Direction logic and unit handling are **structurally correct and universal**; the **NO-direction CI is value-wrong** (estimator mismatch inflates the lower bound above the point estimate for 84.8% of buy_no rows, bypassing the designed CI haircut). All defects are **shadow-inert today** (`real_order_submit_enabled=False`, `live_execution_mode=edli_shadow_no_submit`). The NO-CI defect is **SEV1 to fix before arming**. Units, domain consistency, and direction sign are CORRECT.

## ARM-STATE (inertness basis)

`config/settings.json`: `edli_v1.real_order_submit_enabled = False`, `edli_v1.live_execution_mode = "edli_shadow_no_submit"`, `taker_fok_fak_live_enabled = False`, `day0_hard_fact_live_enabled = False`, `feature_flags.NATIVE_MULTIBIN_BUY_NO_SHADOW = False`. No live capital is exposed to any finding below. Every "SEV1" rating is **conditional on arming**.

---

## LEG 1 — NO-DIRECTION CI INTEGRITY — **BUG (SEV1 when armed)**

The buy_no lower bound `q_lcb_5pct` is computed by an **independent NO bootstrap** (correct per #106), but uses a **different probability estimator** than the point `q_live`, so for high-q_no (near-ceiling) bins the "lower bound" lands **above** the point. `min()` in the trade score then falls back to the point term, silently removing the CI haircut for the NO side.

### Mechanism (code-verified)
- `_bootstrap_bin_no` (`src/strategy/market_analysis.py:823-898`) independently resamples members (`_bootstrap_p_raw_all`, `:355` — `rng.choice(member_maxes, replace=True)` + noise + settle + bin), draws a **random historical Platt-param triple** each iteration (`:853 params = platt_params[rng.integers(len(platt_params))]`), computes `p_post_yes` (`:870`), forms `bootstrap_edges[i] = (1.0 - p_post_yes) - c_b` (`:890`), and takes `ci_lo = percentile(edges, 5)` (`:893`). This is fully independent of the YES bootstrap — **NOT `1 - yes_lcb`** (confirmed).
- The point `q_live` for buy_no = `1.0 - yes_q` (`src/engine/event_reactor_adapter.py:2892`), where `yes_q = p_posterior[index]` from `_compute_posterior(self.p_cal)` using the **current/MAP Platt fit** (`market_analysis.py:227`).
- Probability-space restore: `lcb_by_direction = ci_lo + cost` (`event_reactor_adapter.py:3138`), which algebraically cancels the `- c_b` in the edge bootstrap. The restore is correct (Leg 4); the **value the bootstrap produces** is the defect.
- **Dual-estimator mismatch**: point uses current MAP Platt on the full member set; LCB uses *historical* Platt params on *resampled* members. For high-q_no bins the historical-param distribution + member resampling push `percentile(q_no_boot, 5)` to the `[1e-6, 1-1e-6]` ceiling, producing `q_lcb = 1.0`.
- **Under-protection at the score**: `robust_trade_score` (`src/strategy/live_inference/trade_score.py:48-52`) = `min(q_5pct - c_95pct - λ_edge, q_posterior - c_stress - λ_stress)`. When `q_5pct (LCB) > q_posterior (point)`, the first (CI) term is *larger*, so `min()` binds on the **point term** — the CI haircut is bypassed. The score becomes `p_fill_lcb · (q_live - c - 0.01)` with zero CI discount.

### Universal evidence (full `no_trade_regret_events`, `state/zeus-world.db`)
- buy_no rows: **13,765**. `q_lcb_5pct > q_live`: **11,667 / 13,765 = 84.76%** (both-non-null base 13,765).
- Of inversions: **11,381 / 11,667 = 97.55%** have `q_lcb = 1.0` exactly (ceiling clip). Non-unity inversions cluster at `q_lcb = 50/51 = 0.9608` (167 rows) and `51/52 = 0.9804` (82 rows) — the bootstrap-`n` fingerprint (1 of ~500 samples below ceiling).
- **18 / 19 cities** exhibit the inversion. Panama City is the **only** exception (0/39) — its q_live≈0.88 sits below ceiling, so the bootstrap CI lands below the point as designed. Per-city inversion %: Tel Aviv 100 (31/31), London 100 (2/2), Seoul 99.1 (1722/1737), NYC 96.0 (824/858), Shenzhen 95.4, Wellington 94.7, Taipei 94.3, Wuhan 88.9, Seattle 87.4 (2684/3072), Toronto 83.3, Singapore 80.8, San Francisco 78.6, Warsaw 77.5, Sao Paulo 77.1, Tokyo 73.0, Shanghai 60.0, Paris 57.0, Qingdao 38.3, Panama City 0.0.
- Concentration by q_live bucket: extreme ≥0.99 → **91.2%** (10,619/11,646); 0.95–0.99 → 85.0%; 0.90–0.95 → 59.5%; 0.80–0.90 → 3.5%; 0.50–0.80 → 18.3%; <0.50 → **0%** (0/332). The effect is a near-ceiling artifact; mid/low-q_no NO trades are unaffected.
- **Materiality**: **694** buy_no rows have `trade_score > 0` AND inversion — i.e. would-be live NO entries scored with **no CI haircut**. Inspected exemplars: Wellington 17°C `q_live=0.9991 q_lcb=1.0 score=0.301`, Tel Aviv 31°C `q_live=0.9993 q_lcb=1.0 score=0.193`, Shanghai/Seoul `q_live=1.0 q_lcb=1.0`. These are NO bets on bins the ensemble says are near-impossible to win — directionally fine, but the score reports more edge than the CI design intends.

### Severity / fix
- **SEV1 when armed** (under-protected sizing on the NO leg, universal). **Inert today** (shadow / no-submit).
- **Structural fix (preferred, #105-class)**: compute `q_lcb` with the **same current/MAP Platt params** as `q_live` (resample members only, not Platt params) so point and LCB share one estimator and the LCB is guaranteed ≤ point. Fix surface: `_bootstrap_bin_no` member/Platt sampling at `market_analysis.py:849-870`, specifically the random-Platt draw `:853`. (Same defect class exists on the YES bootstrap `_bootstrap_bin` — audit `:755/:757/:839/:841` — but YES inversions are 0/499 in data because YES bins here are low-q.)
- **Guard (defense-in-depth)**: in the score, treat `q_lcb` as a *lower bound* explicitly — `q_lcb_eff = min(q_lcb, q_posterior)` *before* the cost subtraction, so an inflated bootstrap can never raise the CI term above the point term. This makes the under-protection unconstructable regardless of the bootstrap estimator.

**file:line** — `src/strategy/market_analysis.py:823-898`, `:849-870`, `:853`, `:890-893`, `:227`, `:355`; `src/engine/event_reactor_adapter.py:2892`, `:3125-3138`; `src/strategy/live_inference/trade_score.py:48-52`.

---

## LEG 2 — SETTLEMENT-UNIT CORRECTNESS (#100 / #101) — **CORRECT**

q is computed in each city's settlement unit; no cross-unit contamination in either direction; the 3-way unit identity is a fail-closed gate before q.

### Universal evidence
- **Unit census** (`ensemble_snapshots`, 54 cities): **11 F-cities** (Atlanta, Austin, Chicago, Dallas, Denver, Houston, Los Angeles, Miami, NYC, San Francisco, Seattle), **43 C-cities**. Config↔DB units agree 54/54.
- **`members_unit` coverage**: for ALL 11 F-cities, `members_unit='degF'` on **every** row (both the legacy block `settlement_unit=NULL` ~15,084 rows AND the newer `settlement_unit='F'` ~7,387 rows). Zero F-city row carries `degC`; zero C-city row carries `degF`. (Correction to prior note: the NULL-`settlement_unit` block is ~15k legacy rows, not "16–28"; the `members_unit` fallback covers them.)
- **Member values empirically in native unit** (latest snapshot per city): F-cities — SF 49.6, Seattle 51.7, NYC 58.3, LA 60.5, Atlanta 69.8, Dallas 74.9, Austin 75.7, Miami 76.5, Houston 77.1 (°F); C-cities — Shanghai 21.2, Singapore 27.0, Paris 13.3, Tokyo 19.1 (°C). All physically plausible; no °C value masquerading in an F-city.

### Code (fail-closed)
- Ingest converts Kelvin→native at extraction: `scripts/extract_tigge_mx2t6_localday_max.py:413-420` (`_kelvin_to_native` raises on any unit ∉ {C,F}; F path = `(K-273.15)·9/5+32`). Written to `members_json` at `scripts/ingest_grib_to_snapshots.py:658`. No post-ingest conversion exists or is needed.
- 3-way identity gate: `_assert_settlement_unit_identity` (`src/engine/event_reactor_adapter.py:3312-3337`) asserts `snapshot_unit == city.settlement_unit == every bin.unit`, raising `FORECAST_SETTLEMENT_UNIT_DIVERGENCE` on mismatch / mixed / empty bins. Called **unconditionally** at `:3368`, before p_raw/q at `:3381+`.
- Unit resolution `_snapshot_unit` (`:4247-4256`): prefers `settlement_unit`/`unit`, falls back `members_unit` degF→F / degC→C, else raises `FORECAST_UNIT_AUTHORITY_MISSING`. Bins are unit-tagged at canonical-grid construction (`src/contracts/calibration_bins.py`), dispatched F/C per city.

### Note (not a q-path defect)
`settlement_outcomes` is **empty (0 rows)** in the current forecasts DB, so its `settlement_unit` column is untested-by-data. This table is off the live q-compute path; flagged for awareness only.

**Verdict CORRECT.** Unit handling is universal and fail-closed.

---

## LEG 3 — q-FAITHFULNESS / MODAL-BIN +1 SHIFT (#105) — **UNCERTAIN (current code consistent; DB evidence stale)**

The DB shows a real +1-bin shift for Singapore, but the rows are **pre-branch stale**, and the **current code path does not reproduce it** when traced. No #105 fix commit exists, so it is unresolved whether the fix landed or the DB merely reflects a coincidental prior bug.

### DB evidence (stale)
- Singapore buy_yes `q_live(32°C, June 3) = 0.5650` on all rows, **created 2026-06-01T15:36–17:49Z**. The branch merge-base with main is **2026-06-02T00:00**, and HEAD is 2026-06-02T13:57 — so every Singapore q row predates this branch entirely. Most-recent Singapore rows are June 1 (none from June 2). The referenced June-3 snapshot is no longer present with p_raw in `ensemble_snapshots`.
- The shift signature: ensemble mode is ~30–31°C; q peaks one bin warmer (32°C). The DB `p_raw_json` is stored in **temperature-ascending support order** (e.g. snapshot 1130114: support_index 0..10 = 27°C..37°C, p_raw `[0,0.0002,0.087,0.532,0.372,0.0085,0,…]`, mode at index 3 = 30°C). This temperature-ordered vector is the *ingest representation*.

### Current code (consistent — no shift reproducible)
- Candidates are sorted by **condition_id**, not temperature: `src/events/candidate_binding.py:99-115` (`key=(condition_id, yes_token_id, no_token_id, bin.label)`).
- `bins = tuple(candidate.bin for candidate in candidates)` (`candidate_binding.py:126`) — bins are in the **same condition_id order** as candidates.
- p_raw is **recomputed from raw members**, not read from the stored temperature-ordered `p_raw_json`: `_snapshot_p_raw` (`event_reactor_adapter.py:3381`) → `p_raw_vector_from_maxes(members, city, semantics, bins)` (`:4114`), which returns shape `(n_bins,)` indexed by the **bins argument order** (`src/signal/ensemble_signal.py:173-214`, no internal temperature re-sort).
- q-assignment indexes the same condition_id order: `for index, candidate in enumerate(family.candidates): q_by_condition[condition_id] = p_posterior_vec[index]` (`event_reactor_adapter.py:3117-3124`).
- ⇒ bins, p_raw, p_posterior, and condition_id assignment are **all in condition_id order end-to-end**. Identity Platt and `calibrate_and_normalize` (`src/calibration/platt.py:342`) are element-wise and do not reorder. The stale temperature-ordered `p_raw_json` is never consumed on the live q path. A pre-fix code path that *sorted bins by temperature before passing to p_raw* would have produced exactly the observed shift — but that path is not present at HEAD.

### Why UNCERTAIN (not CORRECT)
1. No commit on this branch references a bin-shift / q-corruption / modal-mass fix (#105 marked completed in the task list, but no code change is locatable).
2. The daemon is in **shadow**; no new Singapore (or any fresh) regret rows exist post-branch to confirm current runtime output.
3. The conclusion "current code is consistent" rests on **static tracing**, not a reproduced live decision.

**Verdict UNCERTAIN.** Pre-arm action: drive one fresh COMPLETE family through the live reactor (e.g. Singapore) and assert the emitted per-bin q-vector equals `p_raw_vector_from_maxes(members, …, bins)` under condition_id order (relationship test, not just a green unit test). This is the only way to retire the uncertainty with evidence rather than inference.

**file:line** — `src/events/candidate_binding.py:99-115`, `:126`; `src/engine/event_reactor_adapter.py:3352`, `:3381-3384`, `:3117-3124`; `src/signal/ensemble_signal.py:173-214`; `src/calibration/platt.py:342`.

---

## LEG 4 — q_posterior vs q_lcb DOMAIN CONSISTENCY (#91) — **CORRECT**

The #91 domain fix holds: `q_lcb_5pct` is in probability space [0,1] for **both** directions; the buy_no-suppression root (un-normalized domain mismatch) is gone.

### Universal evidence (14,286 non-null q_lcb rows, `state/zeus-world.db`)
- `q_lcb_5pct < 0`: **0**. `q_lcb_5pct > 1`: **0**. `q_live < 0` or `> 1`: **0**. Domain is clean across the table.
- **buy_yes**: 499/499 rows have `q_lcb ≤ q_live` (LCB correctly below the point). Ranges: q_lcb [0.0, 0.451], q_live [~0, 0.645]. Zero YES inversion.
- **buy_no**: 84.8% have `q_lcb > q_live` — but this is the **value** defect of Leg 1 (estimator mismatch / ceiling), **not** a domain error. The values are in-domain; they are simply the wrong magnitude. Domain-correct, value-wrong.
- `c_cost_95pct` ∈ [0.0021, 0.9999] — probability units, consistent with `q − cost` being apples-to-apples in `robust_trade_score`.
- Conversion correctness: `q_lcb = ci_lower(edge) + cost` (`event_reactor_adapter.py:3138`) cancels the `-c_b` in `bootstrap_edges = (1-p_post_yes) - c_b` (`market_analysis.py:890`), restoring probability space exactly.

**Verdict CORRECT.** The #91 domain fix is intact on this branch. (Leg 1 is the residual *value* defect that #91's domain fix does not address.)

---

## SEVERITY-RANKED FIX LIST BEFORE ARMING

| # | Leg | Severity (armed) | Surface | Fix |
|---|-----|------------------|---------|-----|
| 1 | LEG 1 | **SEV1** — under-protected NO sizing, 18/19 cities, 694 positive-score rows | `src/strategy/market_analysis.py:849-870`, `:853`; guard at `src/strategy/live_inference/trade_score.py:48-52` | Ground `q_lcb` in the **same current/MAP Platt** as the point (resample members only); add `q_lcb_eff = min(q_lcb, q_posterior)` guard so an inflated LCB can never raise the CI score term above the point term. Audit the symmetric YES bootstrap `:755/:839`. |
| 2 | LEG 3 | **SEV1-pending-confirmation** — if a temperature-order shift exists at runtime it is wrong-bin/wrong-side | confirm at `event_reactor_adapter.py:3381/4114` + `candidate_binding.py:126` | Drive one fresh live family; assert emitted q-vector == `p_raw_vector_from_maxes(members, bins)` in condition_id order. Locate or author the #105 fix/commit. |
| — | LEG 2 | none | — | CORRECT. No action. (Awareness: `settlement_outcomes` empty — off q-path.) |
| — | LEG 4 | none | — | CORRECT. No action. |

**Shadow-inert vs armed-risk**: LEG 1 and LEG 3 are **shadow-inert today** — `real_order_submit_enabled=False`, `live_execution_mode=edli_shadow_no_submit`. Neither has touched live capital. Both must be resolved **before** `real_order_submit_enabled` is flipped. LEG 2 and LEG 4 are correct now and at arm time.

## METHOD / DATA PROVENANCE

READ-ONLY. All counts recomputed independently from `state/zeus-world.db` (`no_trade_regret_events`, full table 63,995 rows: 13,765 buy_no + 499 buy_yes + 49,629 direction-NULL screen rows) and `state/zeus-forecasts.db` (`ensemble_snapshots`). Code verified by direct Read at the cited file:line at HEAD d95f5e67. Scripts: `/tmp/audit_leg1.py`, `/tmp/audit_score.py`, `/tmp/audit_leg2.py`, `/tmp/audit_leg2b.py`, `/tmp/audit_leg3*.py`, `/tmp/audit_leg4.py`. Singapore q rows confirmed pre-branch by created_at (2026-06-01) vs merge-base (2026-06-02T00:00).
```
```
