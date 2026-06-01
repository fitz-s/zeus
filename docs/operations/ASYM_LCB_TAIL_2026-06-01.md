# Asymmetric LCB Tail-Direction Audit — buy_no Risk Floor

- Created: 2026-06-01
- Last reused or audited: 2026-06-01
- Authority basis: OPERATOR LAW (q_NO = 1 − q_YES point; quantiles FLIP TAILS — the 5th pct of NO = 1 − 95th pct of YES). Read-only forensic audit. HEAD `6fcd05a69f`.

## Mandate

Prove, per site, whether the NO confidence/percentile (LCB) quantity is:
- **CORRECT** = correct tail-flipped mirror (`1 − q_YES_95pct`), OR an independent NO-direction bootstrap on the correct (lower) tail;
- **NAIVE-FLIP** = a same-direction `1 − q_YES_5pct` (BUG — silently inverts the NO trade's risk floor);
- **INDEPENDENT** = NO bin's own bootstrap distribution.

## Math being enforced

For complementary tokens the POINT reverses (`q_NO = 1 − q_YES`) but quantiles flip tails. Since `g(x) = 1 − x` is strictly **decreasing**, for any random `p_YES`:

```
percentile_5( 1 − p_YES )  =  1 − percentile_95( p_YES )
```

So the correct NO LCB (5th pct of q_NO) equals `1 − q_YES_95pct`, **NOT** `1 − q_YES_5pct`. A same-direction flip of the YES LCB would silently raise the NO risk floor (often ABOVE the NO point — a degenerate, non-conservative floor).

## Per-site table

| Site | NO-quantity | Source | Tail-direction | Verdict |
|---|---|---|---|---|
| `event_reactor_adapter.py:2872,2878` | `no_lcb` (5pct) for buy_no proof | `q_lcb_by_direction[(cond,"buy_no")]` — separately keyed, NOT `1−yes_lcb` | n/a (pass-through) | **CORRECT** |
| `event_reactor_adapter.py:2878` | `1.0 − yes_q` | NO **point** estimate only (operator-law point reverse) | point reverse (allowed) | **CORRECT (point, not a CI)** |
| `event_reactor_adapter.py:3120-3124` | `lcb_by_direction[(cond,"buy_no")] = hyp.ci_lower + p_market_no` | NO hypothesis `ci_lower` from `_bootstrap_bin_no`; cost restored from NO market price | lower tail of NO-edge dist | **CORRECT** |
| `event_reactor_adapter.py:3132-3134` | fallback `q_point = 1−yes_posterior`, `lcb = q_point` | non-executable direction → neutral, `p_value=1.0`, prefilter False (rejected downstream) | point (no CI used) | **CORRECT (inert fallback)** |
| `event_reactor_adapter.py:3861-3864` | `masked_lcb buy_no = min(no_lcb, 1−q_value)` | `no_lcb` = NO-keyed bootstrap LCB; `1−q_value` = NO point as a CEILING clamp | lower tail, clamped by point ceiling | **CORRECT (clamp, not flip)** |
| `market_analysis_family_scan.py:107-115` | `ci_lo_no` via `analysis._bootstrap_bin_no(idx,n)` | dedicated NO bootstrap method (NOT `_bootstrap_bin`) | lower tail of NO-edge | **INDEPENDENT/CORRECT** |
| `market_analysis.py:823-894` `_bootstrap_bin_no` | `bootstrap_edges[i] = (1−p_post_yes) − c_b; ci_lo = percentile(.,5)` | per-sample NO transform of the SHARED posterior stream, THEN 5th pct | **correct tail-flip by construction** | **CORRECT** |
| `market_analysis.py:572-574,613` (`find_edges`) | `p_post_no = 1−p_posterior` (point); `ci_lo` via `_bootstrap_bin_no` | point = naive complement (allowed); CI = NO bootstrap | point reverse + correct-tail CI | **CORRECT** |
| `market_analysis.py:391-399` `buy_no_complement_diagnostic_price` | `1 − p_market` | explicitly a **non-executable PRICE diagnostic**, binary-only; never feeds the NO LCB | price complement | **CORRECT (not in LCB path)** |
| `trade_score.py:42-52,68-71` | `q_5pct` (= NO LCB for buy_no), `q_posterior` (= NO point) | `min(q_5pct−c_95−λ, q_posterior−c_stress−λ)·p_fill` | consumes NO-grounded LCB | **CORRECT** |
| `kelly.py:31-63` `kelly_size` | `f* = (q_NO − price_no)/(1 − price_no)` | uses NO **point** + NO entry price; LCB lives in trade_score gate, not Kelly | point (Kelly does not use a CI) | **CORRECT** |

**No `1.0 − yes_lcb` / `1.0 − yes_ucb` same-direction-flip site exists anywhere in the NO LCB path** (grep-verified across `src/strategy/` + `event_reactor_adapter.py`). The only `1.0 − yes_q` is the NO point at line 2878.

## Decisive numeric proof (production formula vs naive flip)

Reproduced `_bootstrap_bin_no` at the live `edge_n_bootstrap()=500` on a representative high-q_NO bin (q_NO≈0.876, matching the Singapore NO-31 target). Same `self._rng` posterior stream feeds both directions; the only difference is the final transform.

```
q_YES point        = 0.1264
q_YES 5pct         = 0.0282      q_YES 95pct = 0.2658
q_NO point         = 0.8736  (= 1 − q_YES point)               [matches live q_NO=0.876]
q_NO_lcb PRODUCTION = 0.7342  (_bootstrap_bin_no: 1−p_post_yes, 5th pct)
CORRECT  1 − q_YES_95pct = 0.7342   →  PRODUCTION == CORRECT   (True, atol 1e-9)
NAIVE    1 − q_YES_5pct  = 0.9718   →  PRODUCTION == NAIVE      (False)
gap(CORRECT, NAIVE)      = 0.2376
```

The production LCB equals the **correct tail-flip** to machine precision, and is provably **not** the naive flip. The naive value (0.9718) would sit ABOVE the NO point (0.8736) — an impossible "lower" bound; the correct value (0.7342) is a genuine downside floor below the point, consistent with the live Singapore NO row (q_NO=0.876, q_NO_lcb=0.804: LCB correctly below point).

## Live data cross-check (`state/zeus-world.db`, `no_trade_regret_events`, 6 777 buy_no rows)

- Clean mid-range rows show the LCB correctly **below** the point (real floor): Paris q_NO=0.6214, q_NO_lcb=0.4706 (gap 0.151). A naive flip would have produced ~0.75 (above the point). → CORRECT.
- Singapore 06-01/02 buy_no rows: q_live 0.996 → q_lcb 0.9804 (floor below point). → CORRECT.
- 4 406 buy_no rows have `q_lcb ≤ q_live` (valid floor); 2 371 show `q_lcb > q_live`.

### The 2 371 "inverted" rows are a BASIS MISMATCH, not the naive tail-flip

- 99.6% (2 358/2 371) sit in the q_live ≥ 0.9 **saturation** regime, with `q_lcb` pinned to exactly 1.0 (the legitimate probability ceiling) while the live-remapped point q_live is a hair below 1.0.
- Root: the persisted `q_live` (NO point) is `1 − live_state.probabilities` — the **inference-engine-remapped** YES (`event_reactor_adapter.py:3146-3149 / 3853`), whereas `q_lcb_5pct` is `1 − p_post_yes` from the bootstrap on the **raw** posterior. When the live remap nudges the YES point up (NO point down) without moving the bootstrap LCB, the recorded NO LCB can exceed the recorded NO point.
- This is a point-vs-LCB **provenance divergence at persist time**, structurally distinct from a same-direction tail flip of the bootstrap. The bootstrap LCB itself (the audited code) is correctly tail-flipped.
- Safety impact is contained: the robust trade_score is `min(q_5pct − c95 − λ, q_posterior − c_stress − λ)`. When the LCB branch is inverted-high it becomes NON-binding and the **point branch dominates the min()**, so an inverted LCB cannot inflate the executable score via the floor. Of the 2 371 inverted rows, 2 126 were rejected `TRADE_SCORE_NON_POSITIVE`; the 248 positive-score rows passed on the point branch, not on an inflated floor.

## Verdict

**CONFIRM** — no NO risk-floor is inverted by a naive same-direction tail flip. Every NO LCB along the executable path is built from `_bootstrap_bin_no`, which transforms each posterior sample to `(1 − p_post_yes) − c_b` BEFORE taking the 5th percentile — the mathematically correct tail-flip (`q_NO_lcb = 1 − q_YES_95pct`), proven to machine precision and against the WRONG naive value. The NO point is the operator-law `1 − q_YES`; Kelly uses the point, trade_score uses the correct-tail LCB.

**One DEFECT-adjacent observation (not a tail-flip bug, not a risk-floor inversion):** at persist time the NO point (`q_live`, live-engine remapped) and the NO LCB (`q_lcb_5pct`, raw-posterior bootstrap) come from different bases, producing `q_lcb > q_live` in the q→1 saturation regime (2 371 rows, 99.6% saturation). It is self-protected by the `min()` in robust_trade_score and never inflates an executable floor, but the recorded NO LCB is not a valid lower bound in those rows — a provenance-consistency cleanup item (align the LCB basis to the live-remapped point, or persist the pre-remap point alongside), out of scope for this read-only audit.
