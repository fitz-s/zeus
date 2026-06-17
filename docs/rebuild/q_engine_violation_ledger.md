All three load-bearing claims verified byte-exact. The audits are accurate. Producing the authority document.

# Zeus q Engine vs Arrow-Debreu Neg-Risk Theory — Authoritative Rebuild Specification

**Date:** 2026-06-14 · **Authority basis:** Operator Arrow-Debreu neg-risk theory (INV-Q1..Q8), 8 read-only audits, live 06-15 `no_trade_regret_events` evidence, 3 byte-verified code pointers · **Status:** AUTHORITATIVE for the q-engine rebuild

---

## 1. EXECUTIVE VERDICT

**Is the q engine fundamentally broken vs the theory? — YES, but not where the live symptom first points.** The q POINT vector is structurally correct: outcome space is a complete MECE partition (INV-Q1 ✅), and all three q paths renormalize the point to Σq=1 (INV-Q2 ✅ for the point). The break is in **everything downstream of the point**: the uncertainty band (q_lcb), the NO leg, the edge, the sizing, the arbitrage routes, the exit, and the HK settlement preimage.

**Single biggest root cause (one sentence):** The conservative band `q_lcb`/`q_ucb` is computed as **independent per-bin percentiles of a raw, non-renormalized bin-mass sample** (`np.percentile(probs, 5.0/95.0, axis=0)` at `replacement_forecast_materializer.py:1425-1426`, and the histogram-count variant at `market.py:303`), so a high-belief narrow bin's lower bound collapses to 0.01-0.09 from Monte-Carlo count/center granularity rather than from any defensible settlement uncertainty (INV-Q6 VIOLATION) — and that same incoherent band is the *sole source* of the NO leg (`q_lcb_no = 1 − q_ucb_yes`, `event_reactor_adapter.py:9955`), poisoning INV-Q3.

**Is the live no-trade fully explained by q being wrong? — NO, only ~half of it.** The collapse of `q_lcb` to ~0 explains why near-center buy_no families show `q_live ≤ cost` and get starved (the conservative leg evaporates). But the **headline live symptom — Tokyo buy_yes 26C q=0.469 vs market ask=0.001, edge=+0.47, REJECTED** — is NOT a q-normalization failure. The Tokyo 0.469 is the verified, correctly-normalized modal mass of ONE Normal (scipy: N(μ=26.0, σ=0.8) over the half-up preimage [25.5, 26.5) = 0.468; q_live=0.469 matches to 3 decimals). The Tokyo trade is killed by an **unrelated gate** (`REPLACEMENT_FORECAST_LIVE_DIRECTION_PROOF_MISSING`, `direction_law_verdict=null`) *before* the arbitrage layer ever runs — and the engine has **no coherence test** that would flag "a q=0.47 bin priced by a deep book at 0.001 is incoherent with the family." So the no-trade is co-caused: (a) `q_lcb` collapse starves buy_no, (b) a direction-proof gate starves the over-confident buy_yes, (c) the missing arb/coherence layer means neither path is sanity-checked against Σq=1 or against the market. q being wrong is necessary but **not sufficient** to explain the no-trade; the missing arbitrage/route layer (INV-Q7/Q8 entirely absent) and the direction-proof gate are independent contributors.

---

## 2. VIOLATION LEDGER

Deduped across all 8 dimensions, sorted by severity. **Root-defect clusters are tagged `[R#]` and cross-referenced** — five dimensions converge on two physical defects.

### Root-defect clusters

- **`[R1]` THE BAND GENERATOR** — per-bin non-renormalized percentile / histogram-count sampling. Surfaces in: q_normalization (V4), qlcb_uncertainty (V1, V2, V3), no_semantics (V5), executable_price (anchor input V9). **This is the single highest-leverage fix.**
- **`[R2]` NO-AS-COMPLEMENT-NOT-BASKET** — NO modeled as a scalar UI complement, never as the convertible payoff basket Σ_{j≠i}e_j. Surfaces in: no_semantics (V5, V6), negrisk_routes (V10, V11, V12, V13), edge_ev (V8).

---

### CRITICAL

**V1 — `q_lcb` is the 5th-percentile of a per-bin raw bin-mass sample; narrow high-belief bins collapse to ~0 `[R1]`**
- **file:line:** `src/data/replacement_forecast_materializer.py:1425-1426` (fused/replacement path, byte-verified); `src/types/market.py:298-303` + `src/strategy/market_analysis.py:445-454` + `src/strategy/probability_uncertainty.py:306-311` (canonical histogram-count path)
- **Current:** `q_lcb_vec = np.percentile(probs, 5.0, axis=0)` where `probs` is `(N_draws, M_bins)` per-draw per-bin mass (`ndtr(z_high)−ndtr(z_low)`, *not* renormalized across bins within a draw — verified: no row-sum divide between line 1423 and 1425). Each bin's 5th percentile comes from a *different* center draw; the resulting vector does not sum to 1 and is not a sub-distribution. For a sharp predictive over a 1°C bin, a single center/noise draw shoves mass across the bin edge, swinging the raw mass ~0→0.9; its 5th percentile is near-0 even when the mean is 0.47. **Live:** q_lcb 0.01-0.09 while q_live 0.39-0.95, books deep (p_fill~0.99, not liquidity).
- **Violates:** INV-Q6 (conservative bound must be COHERENT with the joint distribution and use a DEFENSIBLE width model; explicitly forbids collapsing a high-belief bin to ~0) and INV-Q2 (band is not a normalized joint).
- **Required:** `q_lcb_i` = lower 5% quantile of the *i*-th marginal of ONE joint credible region. Renormalize each draw before taking percentiles, OR sample predictive parameters (μ,σ) ~ EMOS posterior and integrate the analytic bin mass per sample then renormalize. Width must reflect forecast-parameter uncertainty, not count/center granularity.

**V2 — `q_lcb` computed independently per bin with NO joint simplex constraint `[R1]`**
- **file:line:** `src/engine/event_reactor_adapter.py:10460-10463, 10319-10321`; `src/strategy/probability_uncertainty.py:256-315`
- **Current:** Per bin index the seam draws *that bin's* YES samples alone (`bin_yes_probability_samples(index, ...)`) and reads its 5th percentile (`_side_q_lcb_from_yes_samples`). The only cross-bin coupling is the per-sample renorm *inside* `compute_posterior` (`market_fusion.py:298-300`); after that each bin's LCB is read off its own marginal with no constraint that the family LCBs be jointly realizable. All bins can be simultaneously crushed toward 0 (their LCBs can sum to ≪1).
- **Violates:** INV-Q6 (incoherent per-bin band is exactly what the invariant names).
- **Required:** Bound the JOINT (Dirichlet/credible region on q with Σq=1), read each bin's conservative mass as a projection so lowering one LCB raises siblings'. Fix the generator (V1) first — coherent marginals of an incoherent generator are still wrong.

**V3 — HK point-q drops the per-city `rounding_rule`; `build_emos_q` always integrates WMO half-up, never `oracle_truncate`**
- **file:line:** `src/calibration/emos_q_builder.py:132-136` (byte-verified: `bin_probability_settlement(mu_native, sigma_native, lo, hi)` — no `rounding_rule` kwarg → defaults to `wmo_half_up` per `emos.py:587`); live caller `event_reactor_adapter.py:10972-10978` passes only `city.name`, never `settlement_source_type`/rule
- **Current:** Hong Kong settles by UMA truncation (`for_city` → `rounding_rule='oracle_truncate'`, empirically 14/14 match vs 5/14 for WMO; `settlement_semantics.py:271-283`), but the EMOS seam integrates HK against the WMO `(-0.5,+0.5)` preimage instead of truncation `(0.0,+1.0)`. q is byte-different from how the market resolves.
- **Violates:** INV-Q1 (bin settlement preimage must be byte-identical to on-chain settlement; resolution semantics must be versioned).
- **Required:** Add `rounding_rule` param to `build_emos_q`/`build_honest_raw_q`, derive via `SettlementSemantics.for_city(city).rounding_rule` (the seed builder already does this at `replacement_forecast_materialization_seed_builder.py:197`), forward into every `bin_probability_settlement(..., rounding_rule=...)`. Fail-closed if the rule can't be resolved.

**V4 — HK `q_lcb` bootstrap also rounds WMO (`MarketAnalysis(round_fn=None)`)**
- **file:line:** `src/engine/event_reactor_adapter.py:11206` (`round_fn=None`); fallback chain `market_analysis.py:860-864` → `settlement_semantics.py:147-149` (None → `round_wmo_half_up_values`)
- **Current:** The EMOS lcb sampler calls `analysis._settle(draws)`; with `round_fn=None` HK settles by WMO too, so the band is incoherent with even a *fixed* point-q.
- **Violates:** INV-Q1 + INV-Q6 (q_lcb must be coherent with the SAME settlement-resolved distribution as the point).
- **Required:** Pass `round_fn=SettlementSemantics.for_city(city).round_values` (the reactor already has this exact lambda at `event_reactor_adapter.py:8175-8181`).

**V5 — None of the four INV-Q7 size-aware arbitrage checks exist in live code `[R2]`**
- **file:line:** `src/strategy/live_inference/executable_cost.py:158-167` (only a single-direction native book walk); whole-repo grep empty for all four
- **Current:** `_levels_for_direction` returns exactly ONE ladder per direction; no (A) `ask(YES_i)+ask(NO_i)<1` pair parity, no (B) `Σ ask(YES_i)<1` basket, no (C) `ask(NO_i)+friction < Σ_{j≠i} bid(YES_j)` conversion arb, no (D) `not_i_cost = min(ask(NO_i), Σ_{j≠i} ask(YES_j))` route dominance. Every `min(...ask...)` in the engine is a maker-limit clamp, not a cross-route compare.
- **Violates:** INV-Q7 (all four, size-aware on executable bid/ask).
- **Required:** A family-level executable basket-cost primitive that walks all sibling YES ask ladders to depth and compares Σ-walk vs direct NO walk (D) and vs 1.0 (A/B); gated on the per-market negRisk flag.

### HIGH

**V6 — Native-NO robust lower bound is `1 − q_ucb_yes`, a per-bin band edge, not a calibrated NO probability (Mistake 2) `[R1][R2]`**
- **file:line:** `src/engine/event_reactor_adapter.py:9955` (`q_lcb_no = 1.0 − q_ucb_yes`), `:7471-7474`; band source `replacement_forecast_materializer.py:1425-1426`
- **Current:** `q_ucb_yes` is the per-bin non-renormalized 95th percentile (V1). The wiki validates byte-exact that `q_lcb_no` is therefore **FLAT ~0.78 across ALL interior bins** (mode AND neighbors) — "ZERO favorite-longshot discrimination… a band EDGE, not a NO belief." This flat value is `q_lcb` for every buy_no score (`:7574`), FDR p-value (`:10160`), and conservative-evidence gate (`:7611`).
- **Violates:** INV-Q3 + INV-Q6 (NO lower bound must be a conservative bound coherent with the one joint distribution; must vary mode~0.78 vs far~0.99, not collapse flat).
- **Required:** `q_no_samples = 1 − q_yes_samples_renormalized` per draw (the `no_side_samples` helper at `probability_uncertainty.py:105/111` already does this canonically), then `lower_quantile(q_no_samples, alpha)`. **Lands jointly with R1.**

**V7 — Family TOTAL size is scalar binary Kelly; the R^n ΔU vector stake is computed then discarded `[R2]`**
- **file:line:** `src/engine/event_reactor_adapter.py:8625-8632` (byte-verified: `f_star = (_q_lcb_sel − _cost) / (1.0 − _cost)`; `family_total = bankroll_usd * mult * f_star`)
- **Current:** The in-code comment (`:8608-8616`, verified) is explicit: "Before this fix the kernel used `score.optimal_stake_usd * mult` where optimal_stake_usd is the ΔU argmax on the full family payoff matrix." The vector optimum (`utility_ranker.FamilyPayoffMatrix`) is overwritten by a single-bin binary fraction. ΔU is used only for *shape* across simultaneous legs (=1.0 for the live single-leg default, so it does nothing).
- **Violates:** INV-Q5 (sizing must be the vector argmax `s* = argmax_s Σ_y π_y log(A_y+R_y(s))`).
- **Required:** Size from `RobustCandidateScore.optimal_stake_usd × mult`; do not substitute binary Kelly. *Note:* this is the operator's 2026-06-10 single-Kelly directive — the rebuild must reconcile "Kelly basis = total portfolio equity, applied once" with the vector argmax; likely the directive wants a *scalar global haircut on the vector stake*, not replacement of it. **Operator confirmation required (see §5).**

**V8 — `forward_edge`/EV is the per-bin scalar `q_i − price_i`, used for selection, economic floor, and telemetry `[R2]`**
- **file:line:** `src/contracts/semantic_types.py:237-245` (`compute_forward_edge` → `prob_value − price_value`); `src/engine/evaluator.py:1321-1338` (`edge_per_share = p_posterior − price`); live edge `src/strategy/live_inference/trade_score.py:48-52`
- **Current:** A single scalar `q_i − price_i` ranks which bin survives (`family_exclusive_dedup`) and gates the economic floor. Cannot express NO_i = Σ_{j≠i}e_j basket economics. Directly explains Tokyo: `edge = q_model − ask = 0.47 − 0.001` treated as an isolated bin with no Σq=1 sanity check.
- **Violates:** INV-Q5 (Edge = q·Δh − cost − frictions, a dot product against the R^n payoff vector).
- **Required:** Edge = ΔU (or its dollar-equivalent q·Δh) from `utility_ranker` for both selection and floor; retain `q_i − price_i` as a logged diagnostic only.

**V9 — negRisk flag read per market but never gates a route or arbitrage direction (dead metadata) `[R2]`**
- **file:line:** `src/strategy/live_inference/executable_cost.py:154,161-162`; `src/engine/event_reactor_adapter.py:13788,13808`
- **Current:** Flag threaded into the quote book (`neg_risk=snapshot.neg_risk`) but every downstream conditional grep returns ZERO branch sites — assignments only. buy_no unconditionally walks the native NO ladder (`if direction == "buy_no": return book.no_asks`).
- **Violates:** INV-Q7 (flag must be read AND consumed to enable convertibility + route selection).
- **Required:** A `negRisk=True` branch that computes synthetic-basket alternatives and feeds both routes into selection.

**V10 — `VectorEdgeDecision` / `neg_risk_basket` strategy and the family exit optimizer are DEAD (no live producer/caller) `[R2]`**
- **file:line:** `src/contracts/deterministic_edge.py:73-105`; `src/analysis/market_analysis_vnext.py:114-128` ("consumer… removed with the shadow-candidate framework 2026-06-14"); `src/strategy/exit_family_optimizer.py:11-14` ("no in-tree caller imports it yet"); reason-28 enum `no_trade_events_schema.py:39` never emitted
- **Current:** The only code that ever expressed the basket payoff vector `(K-1)·q*` survives only on the analysis/report path; live producer deleted. Orphaned contracts imply basket economics exist when they don't.
- **Violates:** INV-Q5/INV-Q7 (NO=Σ_{j≠i}e_j basket identity in payoff-vector space).
- **Required:** Either resurrect a live producer wiring the four INV-Q7 checks, or delete the orphans honestly.

**V11 — Exit value is `bid(current_token)` via `place_sell_order`, not `liquidation_value(position_vector)` `[R2]`**
- **file:line:** `src/execution/exit_lifecycle.py:1242-1243,1332-1342`; dead family-aware path `src/strategy/exit_family_optimizer.py:165-180`
- **Current:** Live exit submits a direct single-token sell at `best_bid`. Grep for `convert`/basket-sell/`liquidation`/hold-to-redeem in exit modules returns nothing. A NO leg with a thin direct bid but a rich sibling-YES basket is liquidated at the worse price.
- **Violates:** INV-Q8 (exit = max(direct bid, convert NO→basket then sell, hold-to-redeem)).
- **Required:** Compute all three routes, take the max before choosing the sell.

**V12 — Live edge uses per-bin q − direct cost with no basket/conversion term `[R2]`**
- **file:line:** `src/strategy/live_inference/trade_score.py:48-52,68-71`
- **Current:** `robust_edge = min(q_5pct − c_95pct − λ_edge, q_posterior − c_stress − λ_stress)`. No Σ_{j≠i}q_j NO-basket term, no conversion-route cost. The +0.47 Tokyo edge is never reconciled against the family sum or the market.
- **Violates:** INV-Q4/INV-Q7 (`EV_buy_no_i = Σ_{j≠i}q_j − ask_no_i`; route choice size-aware).
- **Required:** `EV_buy_no_i` computes the basket sum and compares `ask_no_i` against both direct NO ask and synthetic Σ ask(YES_{j≠i}); EV consistent with a normalized q.

### MEDIUM

**V13 — NO never modeled as a convertible basket; `NegRiskBasket` unimported on live path `[R2]`** — `src/strategy/candidates/neg_risk_basket.py` (unimported); buy_no prices ONLY the own NO ask ladder (`_native_side_candidate_from_proof`, `event_reactor_adapter.py:6818-6822`). No conversion route ever computed. Violates INV-Q3/Q7(C/D). (Same root as V5/V9/V10 — folded into R2 fix.)

**V14 — Market-anchor cap uses the fee-loaded NO ASK as "market-implied NO probability" (price-as-probability)** — `event_reactor_adapter.py:7540` + `market_anchor.py:25-26,200-212`. `execution_price.value` (depth-walked ASK + taker fee) blended as a probability `q_anchor_no = a·q_pt + (1−a)·mkt`. Violates INV-Q4 (price≠probability; ASK sits strictly above true NO belief, biasing the haircut HIGH). **Severity low-live because flag-gated OFF** (`:9774` default False). Required: pass de-frictioned mid (or `1 − Σ_{j≠i} mid(YES_j)` on neg-risk); type input `price_type='implied_probability'`.

**V15 — No market-price reconciliation of belief on the live path (market-anchor flag OFF)** — `event_reactor_adapter.py:7531-7534,9774`. The anchor block is the ONLY site where market price touches q, entered only if `replacement_q_market_anchor_enabled` (default False). EMOS q carries no market term. So q_live/q_lcb are pure model output; a q=0.47 bin vs market 0.001 survives as "edge" with no coherence gate. Violates INV-Q4 reconciliation. Required: expose market-implied belief as a typed first-class input for a coherence/shrink gate.

**V16 — buy_no posterior is `1 − q_yes` binary complement at the sizing boundary `[R2]`** — `event_reactor_adapter.py:7471,9949`, sized at `:8627`. Numerically equals the basket sum ONLY when q is one normalized distribution; used as a standalone scalar detached from the family and sized by binary Kelly. Violates INV-Q3/Q5. (Folded into V6+V7 fix.)

**V17 — `kelly_size` and the generic sizing seam are strictly single-asset binary Kelly** — `src/strategy/kelly.py:61-62`; `money_path_adapters.py:217-222`; `evaluator.py:1658-1663`. One posterior, one price, no vector of competing bins. Violates INV-Q5. Required: route weather families through the vector optimizer; reserve scalar `kelly_size` for genuinely single-outcome instruments.

**V18 — Point-q normalization correct but UNGUARDED by any Σ=1 invariant on the live path** — `replacement_forecast_materializer.py:1688-1691` + `state.py:25-29`. Correctness rests on three independent divide-by-sum sites staying in agreement; the code documents a #176 `q_lcb>q_point` inversion that arose because "the two modules normalise differently." Violates INV-Q2 (Σ=1 must be an enforced invariant, not emergent). Required: single Σ=1 post-condition assert (`abs(sum−1)≤1e-9`) at the fused-q return and `evaluate_live_bins` output; collapse the three sites toward one authority.

**V19 — `finalization_time` hardcoded `'12:00:00Z'` for every city; `resolution_source` embeds `None` for non-WU stations** — `settlement_semantics.py:238,253,282,287,291`. All four constructors set `12:00:00Z` unconditionally; HK `wu_station=None` yields `resolution_source` containing literal "None". No station ICAO, settlement tz/local-day, or version field. Violates INV-Q1 (resolution semantics must be versioned and encode local-day/tz/station). Required: add station, settlement_timezone, semantics_version; derive finalization from city tz; fail-closed when station id is None.

**V20 — C3 N_eff width correction is shadow-only; never reaches live q_lcb** — `probability_uncertainty.py:325-346,281-297`; live call passes no `n_eff_override`. Docstring: "main q_lcb field is ALWAYS the raw value… flag-OFF byte-identical." Note the `sqrt(N/N_eff)` ratio WIDENS the interval, so even if wired it doesn't fix the collapse direction. Violates INV-Q6 (no live owned width model). Required: wire a real N_eff AND switch to the analytic-parameter generator (V1), or delete the dead shadow.

---

## 3. THE CORRECT q ENGINE (target architecture)

A from-scratch design satisfying INV-Q1..Q8. **Layered, each layer a typed contract; economics live in R^n payoff-vector space, token labels are implementation.**

### Layer 0 — Event / Outcome Space (INV-Q1)

```
EventResolution:                         # versioned settlement identity
    station_id: str                      # ICAO/HKO id (fail-closed if None)
    settlement_timezone: str             # local-day boundary
    finalization_local_time: time        # derived from tz, NOT hardcoded 12:00Z
    rounding_rule: Literal["wmo_half_up","oracle_truncate","floor"]
    tie_break, source_revision, semantics_version: str

Omega:                                    # complete MECE partition
    bins: tuple[Bin, ...]                 # leftmost.low == -inf, rightmost.high == +inf
                                          # interior: isclose(prev.high+1, next.low), no overlap
    residual_shoulders: (low_shoulder, high_shoulder)   # open-ended, absorb tail mass
    resolution: EventResolution
    # validate_bin_topology() at binding; fail-closed (no synthetic-Other repair — operator-confirmed §5)
```

One Ω per (city, target_date, metric), sourced from `market_events` (NOT the fresh-executable subset). Non-tradeable tail bins KEPT in the family (`executable_mask=False`), so q/FDR run over the complete partition. **Both q paths consume the SAME Ω** (close the dual-validator gap — `validate_bin_topology` and `_validate_full_family_bins` must agree).

### Layer 1 — ONE Normalized Joint Distribution (INV-Q2)

```
JointQ:
    q: np.ndarray                        # (M,), q_i >= 0, INVARIANT: sum == 1 (+/- 1e-9)
    omega: Omega
    predictive: PredictiveParams         # (mu, sigma) posterior — the SOURCE of width
    q_source: str                        # single authority enum
```

Construction: `q_i = bin_probability_settlement(mu, sigma, lo_i, hi_i, rounding_rule=omega.resolution.rounding_rule)` then `q = q / q.sum()`. **One renormalization authority** — collapse the three current divide-by-sum sites; assert Σ=1 post-condition at the single return (fixes V3, V4, V18). `rounding_rule` threaded from `EventResolution`, never defaulted.

### Layer 2 — Coherent Uncertainty Band (INV-Q6) — *the highest-leverage layer*

```
JointQBand:
    samples: np.ndarray                  # (N, M) per-draw q-vectors, EACH renormalized to sum 1
    q_lcb: np.ndarray                    # (M,) i-th = lower-alpha quantile of i-th MARGINAL of the joint
    q_ucb: np.ndarray                    # (M,) symmetric
    # width driven by PARAMETER uncertainty: (mu_i, sigma_i) ~ EMOS predictive posterior,
    # NOT histogram-count granularity, NOT per-bin raw-mass percentile
```

Per sample *k*: draw `(mu_k, sigma_k)` from the EMOS predictive posterior (μ MAP ± SE; σ with its lead-aware floor uncertainty), integrate analytic bin mass, **renormalize the row** (`samples[k] /= samples[k].sum()`), THEN take per-bin percentiles. A sharp high-belief bin now keeps a high lower bound because its mass is a smooth function of (μ,σ); the band reflects forecast uncertainty, not count noise. **This single change kills V1, V2, V6, V16, V20 and de-flattens the NO band.** The EMOS-analytic `k_cov` path (`event_reactor_adapter.py:12221-12224`, `min(emos_q, q(mu, k_cov·sigma))`) is the right shape — promote it from shadow to primary.

### Layer 3 — NO as Complement Basket (INV-Q3)

```
NoLeg:
    point: q_no_i = 1 - q_yes_i          # = Σ_{j!=i} q_j (valid ONLY because Layer-1 q sums to 1)
    band:  q_no_samples = 1 - q_yes_samples_renormalized   # per-draw, NOT 1 - q_ucb_yes band edge
    q_lcb_no = lower_quantile(q_no_samples, alpha)
    payoff_vector: R^n = sum_{j!=i} e_j  # wins on every other outcome incl. OUTSIDE
```

NO lower bound is the lower tail of the per-draw complement of the renormalized joint (fixes V6). NO is a payoff vector, not a scalar (fixes V13, V16).

### Layer 4 — Executable Quote / Depth Pricing (INV-Q4)

```
ExecutableCost (KEEP — already INV-Q4-correct):
    buy -> depth-walked ASK ladder + taker fee; sell -> BID. Size/depth aware.
    assert_not_midpoint_cost / assert_not_last_trade_cost  (mid/last structurally banned)
MarketImpliedBelief (NEW, typed price_type='implied_probability'):
    de-frictioned mid (or 1 - mid(YES_i); on neg-risk 1 - Σ_{j!=i} mid(YES_j))
    -> feeds the coherence gate (Layer 7), NEVER reusable as cost (assert_kelly_safe forbids)
```

The cost path is correct and **must not change** (V4-clean). The new piece is exposing a de-frictioned market belief as a first-class typed input (fixes V14, V15).

### Layer 5 — Payoff-Vector EV / Edge (INV-Q5)

```
PayoffMatrix (from utility_ranker.FamilyPayoffMatrix):
    YES_i: R = s(1-c)/c if y==i else -s
    NO_i:  R = -s if y==i else s(1-c)/c        # basket
EV_terminal = q . h
Edge = q . delta_h - executable_cost - frictions
DeltaU_j(s) = Σ_y pi_y^rob [ log(A_y + R_{y,j}(s)) - log(A_y) ]
```

ΔU (or its dollar-equiv q·Δh) is THE edge for selection AND the economic floor (fixes V8). `q_i − price_i` becomes a logged diagnostic only.

### Layer 6 — Neg-Risk Arbitrage + Route Selection (INV-Q7)

```
FamilyBook:                              # NEW — assembles ALL sibling YES/NO ladders for one event
    yes_asks[j], yes_bids[j], no_asks[i], no_bids[i]   # size-aware, depth-walked
RouteSet (per market, gated on negRisk flag):
    (A) pair:        ask(YES_i)+ask(NO_i) < 1 ; bid sum > 1
    (B) basket:      Σ ask(YES_i) < 1
    (C) conversion:  ask(NO_i) + friction < Σ_{j!=i} bid(YES_j)
    (D) dominance:   not_i_cost = min(ask(NO_i), Σ_{j!=i} ask(YES_j))   -> choose cheaper
```

The missing core (fixes V5, V9, V10, V12, V13). Requires the `FamilyBook` aggregator (currently `family_book_snapshot` is documented "always None in live"). Prerequisite: verify the venue `NegRiskAdapter` convert/merge/split primitives are wired (only `wcol()` found) — see §5.

### Layer 7 — Sizing + Coherence Gate

```
size = optimal_stake_usd (DeltaU vector argmax)  x  fractional_kelly_mult
       # NOT scalar (q_lcb - cost)/(1 - cost)
       # operator single-Kelly directive applied as a GLOBAL HAIRCUT on the vector stake
CoherenceGate (NEW):
    flag any bin where |q_model_i - market_implied_i| exceeds tolerance on a DEEP book
    -> James-Stein shrink toward market OR block (catches Tokyo 0.47-vs-0.001 BEFORE direction-proof)
```

Fixes V7, V17; adds the missing Tokyo guard (V15).

### Layer 8 — Liquidation-Value Exit (INV-Q8)

```
exit_value = liquidation_value(position_vector) = max(
    sell_direct_bid,
    convert(NO_i -> {YES_j}) then Σ sell at YES bids - conversion_friction,
    hold_to_redeem_expected_value )
```

Fixes V11. Resurrect or replace the dead `exit_family_optimizer`.

---

## 4. STAGED REBUILD PLAN

Ordered highest-leverage-correctness-first. **Every stage: shadow before live, ARM gate, RED-on-revert relationship test.** Live stays safe — new q surfaces compute in shadow and are diffed against current before any flag flips.

### Stage 0 — Σ=1 invariant + dual-path Ω reconciliation (guardrail, no behavior change)
- **Goal:** Make INV-Q2 a structural invariant before changing the band, so the rebuild can't silently regress.
- **Files:** `replacement_forecast_materializer.py:1688-1691`, `live_inference/state.py:25-29`, `emos_q_builder.py:137-140`, both validators (`types/market.py`, AIFS `_validate_full_family_bins`).
- **RED-on-revert test:** Assert `abs(sum(q)−1) ≤ 1e-9` at every q return; assert both validators accept/reject the SAME bin set. Revert the assert → test goes RED.
- **Live signal:** Zero change to live decisions (assert-only); CI green confirms three normalization sites agree (kills the #176 inversion class).

### Stage 1 — Coherent q_lcb band generator `[R1]` (THE fix; SHADOW)
- **Goal:** Replace per-bin raw-mass percentile with per-draw-renormalized analytic-parameter sampling. Fixes V1, V2, V6, V16, V20.
- **Files:** `replacement_forecast_materializer.py:1419-1441` (`_build_fused_q_bounds`), `probability_uncertainty.py:256-346`, promote `event_reactor_adapter.py:12221-12224` `k_cov` path. Shadow-ledger the new band beside the old.
- **RED-on-revert test:** (1) Each draw row sums to 1 (`abs(samples[k].sum()−1)≤1e-9`). (2) For a sharp predictive (σ=0.6) over a 1°C modal bin with q_point≈0.6, `q_lcb(modal) ≥ 0.45` (NOT ≤0.09). (3) `q_lcb` tracks joint belief monotonically. Revert to `np.percentile(probs, 5.0, axis=0)` on raw mass → modal q_lcb collapses to ~0.05 → test RED.
- **Live signal:** Shadow diff: near-center buy_no families where `q_live ≤ cost` today should show `q_lcb` rise from 0.01-0.09 into a defensible band; Guangzhou-class (q 0.95) buy_no should clear cost. ARM only after the shadow band is reviewed against ≥1 week of `no_trade_regret_events`.

### Stage 2 — HK settlement preimage (correctness; SHADOW then live)
- **Goal:** Thread `rounding_rule` into the EMOS seam. Fixes V3, V4.
- **Files:** `emos_q_builder.py:132-136` (add `rounding_rule` param), `event_reactor_adapter.py:10972-10978` (pass `for_city(city).rounding_rule`), `:11206` (`round_fn=for_city(city).round_values`).
- **RED-on-revert test:** Call `build_emos_q` for an HKO city; assert interior-bin mass == `oracle_truncate` preimage AND differs from WMO (the byte-difference `test_hk_truncation_shifts_mass_down_relative_to_wmo` proves at the leaf, now through the live seam). Revert to no-kwarg → HK integrates WMO → test RED. (Closes the gap that the existing `test_hk_settlement_preimage_contract.py` only tests the leaf, never `build_emos_q`.)
- **Live signal:** Shadow diff HK q point + band vs current; expect mass shift down by the truncation offset. Live only for HK families once diff matches the seed-builder path (`:197`).

### Stage 3 — Payoff-vector edge + vector sizing `[R2]` (SHADOW)
- **Goal:** ΔU edge for selection/floor; size from `optimal_stake_usd × mult`. Fixes V7, V8, V17.
- **Files:** `event_reactor_adapter.py:8625-8632` (replace binary `f_star` with vector stake + global haircut), `contracts/semantic_types.py` + `evaluator.py:1321-1338` (ΔU edge; demote `q_i−price_i` to diagnostic), reconcile the two competing payoff-vector impls (`utility_ranker` ΔU vs `family_exclusive_dedup.optimize_exclusive_outcome_portfolio` — pick `utility_ranker` as authoritative, delete the weaker `0.01·expected_net_profit` proxy).
- **RED-on-revert test:** For a family with correlated existing exposure, assert `family_total == optimal_stake_usd × mult` (NOT the binary `(q_lcb−cost)/(1−cost)`), and that the two stakes differ by the documented 5-10×. Resolve the stale `utility_ranker` header ("DEFAULT-OFF/SHADOW… NOT wired into live") vs its live call at `:8370` — the header is stale for SELECTION; make it true for SIZING.
- **Live signal:** Shadow stake diff vs binary Kelly; **operator gate on the single-Kelly reconciliation (§5) before ARM.**

### Stage 4 — Neg-risk arbitrage + route selection `[R2]` (SHADOW)
- **Goal:** `FamilyBook` aggregator + four INV-Q7 checks + route dominance, gated on negRisk flag. Fixes V5, V9, V10, V12, V13.
- **Files:** new family-book primitive (replace `family_book_snapshot=None`), `executable_cost.py:158-167`, resurrect/replace `VectorEdgeDecision` producer, wire `NegRiskBasket`.
- **RED-on-revert test:** Construct a synthetic family where `Σ ask(YES_{j≠i}) < ask(NO_i)`; assert route dominance picks the basket and `EV_buy_no_i = Σ_{j≠i}q_j − basket_cost`. Revert → engine prices only direct NO ask → misses the cheaper route → test RED.
- **Live signal:** Shadow-emit reason-28 `NEGRISK_NO_PROFITABLE_BASKET` and route-choice telemetry; compare basket vs direct cost on live neg-risk markets. **Prerequisite: confirm venue convert primitives wired (§5).**

### Stage 5 — Liquidation-value exit (INV-Q8; SHADOW)
- **Goal:** 3-route max exit. Fixes V11.
- **Files:** `exit_lifecycle.py:1242-1342`, resurrect `exit_family_optimizer`.
- **RED-on-revert test:** For a NO position with thin direct bid + rich sibling-YES basket, assert exit chooses `convert→basket→sell`. Revert → direct bid only → test RED.
- **Live signal:** Shadow exit-route ledger; live after confirming convert is executable on-chain.

### Stage 6 — Market coherence gate + de-frictioned anchor (INV-Q4; SHADOW)
- **Goal:** Typed `MarketImpliedBelief`; coherence gate catches q-vs-market incoherence BEFORE direction-proof. Fixes V14, V15.
- **Files:** `executable_cost.py` (expose de-frictioned mid, typed), new `CoherenceGate`, fix `market_anchor.py:25-26` to use de-frictioned belief.
- **RED-on-revert test:** Feed the Tokyo case (q=0.47, deep book ask=0.001); assert the gate flags incoherence. Revert → trade passes coherence silently → test RED.
- **Live signal:** Shadow-flag count of incoherent bins; the Tokyo-class 0.47-vs-0.001 must be caught here, not by an unrelated direction-proof null.

### Stage 7 — Versioned `EventResolution` (INV-Q1 hardening)
- **Goal:** station/tz/local-day/version on the settlement contract. Fixes V19.
- **Files:** `settlement_semantics.py:238-291`.
- **Test:** Assert no `resolution_source` contains literal "None"; finalization derives from city tz; fail-closed on None station.

### MUST NOT CHANGE (honest gates — preserve exactly)
- **Direction law / direction-proof** — `_replacement_live_authority_proof_for_direction`. Keep; the Tokyo fix is a NEW coherence gate, NOT loosening direction proof.
- **Real taker fee + min_tick** — `executable_cost.py:78` `with_taker_fee`, `event_reactor_adapter.py:13663` `+ min_tick`. The cost path is INV-Q4-correct; do not touch.
- **Settlement truth** — once Stage 2 lands, `oracle_truncate` for HK is the truth; never regress to WMO.
- **Mid/last cost ban** — `assert_not_midpoint_cost` / `assert_not_last_trade_cost`. Keep structural.
- **MECE fail-closed** — `validate_bin_topology` rejecting incomplete families. Keep (no silent synthetic-Other — operator-confirm §5).
- **No caps / no shadow-default-OFF accretion** (operator laws 06-12/06-13) — go LIVE direct after ARM, do not leave new gates as permanent shadow flags; collapse, don't accrete.

---

## 5. BIGGEST RISK + WHAT THE AUDITS MIGHT HAVE MISSED

**Biggest risk: the band fix (R1/Stage 1) changes the live trade distribution materially, and there is no labeled ground-truth for "correct" q_lcb width.** Today's near-zero `q_lcb` makes the engine ultra-conservative on buy_no (it trades almost nothing — the live no-trade). A coherent band will RAISE `q_lcb` into a defensible range and the engine will START taking near-center buy_no families it currently rejects. If the new width is too tight, the engine over-trades favorites at base-rate (the "96% cost>0.5 near-center favorite-buying, not alpha" pattern the live evidence already flags). The width model is the difference between "fixed the collapse" and "removed the only thing stopping base-rate favorite-buying." **Mitigation:** Stage 1 must ARM only after the shadow band is validated against realized settlement on ≥1 week of families, with an explicit calibration check (does q_lcb coverage ≈ 95%?), not just "q_lcb no longer collapses."

**Open operator decisions the rebuild is blocked on (must resolve before the named stages ARM):**
1. **Single-Kelly vs vector argmax (V7/Stage 3):** The operator's 2026-06-10 directive pinned the family total to binary Kelly explicitly because the ΔU argmax was re-applying risk-aversion. INV-Q5 demands the vector stake. The reconciliation is almost certainly "vector stake × single global fractional-Kelly haircut" — but this contradicts the *letter* of the committed directive (commit `57c441049d`). **Do not silently revert the directive; confirm intent.**
2. **Reject-incomplete vs synthesize-residual (INV-Q1):** Current code fails an incomplete venue family CLOSED. INV-Q1's "Other can shrink as placeholders clarify" *could* be read as license to synthesize a residual shoulder. Confirm "reject" is intended.
3. **Venue convert primitives (Stage 4/5 prerequisite):** Only `wcol()` wrapped-collateral was found in `polymarket_v2_adapter.py`. If the on-chain `NegRiskAdapter` convert/merge/split is NOT wired, INV-Q7(C)/INV-Q8 conversion routes are unexecutable even with correct math. **Verify before building the arb layer** — otherwise Stage 4/5 ships dead code (the exact orphan pattern V10 condemns).

**What the audits may have missed:**
- **Cross-path q divergence (flagged, unresolved):** Two q-construction paths (EMOS `build_emos_q` and fused/AIFS) renormalize over their own bin sets via DIFFERENT validators. The audits confirm both enforce completeness but did NOT confirm they produce *identical* q over an identical Ω for the same live family. If they diverge, INV-Q2 cross-path coherence is silently broken and Stage 0 must catch it.
- **Live config values unread:** `settings['edge']['n_bootstrap']`, `settings['ensemble']['primary_members']`, and crucially **which `q_source` actually serves live HK today** (`emos`/`raw_honest` WMO-buggy vs `replacement_0_1` truncation-correct). The HK violation (V3/V4) is only LIVE if `edli_emos_sole_calibrator_enabled` is ON for HK. The audits located the accessors but read no live TOML — **the severity of V3/V4 is conditional on a config value nobody confirmed.**
- **Soft-anchor fallback path unaudited:** `q_shape='aifs_member_votes_soft_anchor'` (`replacement_forecast_materializer.py:1511`) is the live q when the fused flag is OFF; its `posterior.probabilities` renormalization was never read. If the fused path is not the live default, the entire band analysis may be auditing the wrong code path.
- **The Tokyo symptom is a RED HERRING for the q-engine break.** Every audit that chased Tokyo concluded it is NOT a normalization failure (q=0.469 is correct). The real engine break (R1 band collapse) is masked by the fact that the *headline* live example is a different, gate-level problem. A rebuild that "fixes Tokyo" by loosening the direction-proof gate would ship the over-confident-σ trade while leaving the actual R1/R2 defects untouched — the inverse of correct.

---

**Authority note:** Three load-bearing pointers byte-verified against live source this session (`event_reactor_adapter.py:8625-8632` binary-Kelly substitution; `replacement_forecast_materializer.py:1425-1426` per-bin percentile band; `emos_q_builder.py:132-140` renorm + rounding_rule omission). All match the audits. Key files: `/Users/leofitz/zeus/src/data/replacement_forecast_materializer.py`, `/Users/leofitz/zeus/src/engine/event_reactor_adapter.py`, `/Users/leofitz/zeus/src/calibration/emos_q_builder.py`, `/Users/leofitz/zeus/src/strategy/probability_uncertainty.py`, `/Users/leofitz/zeus/src/strategy/live_inference/{trade_score,executable_cost,market_anchor}.py`, `/Users/leofitz/zeus/src/contracts/settlement_semantics.py`, `/Users/leofitz/zeus/src/strategy/utility_ranker.py`, `/Users/leofitz/zeus/src/execution/exit_lifecycle.py`.