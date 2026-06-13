# No-Order Diagnosis: Liquid-Bin Edge Split (Honest-σ vs Structural)

- Created: 2026-06-13
- Authority basis: operator no-order investigation (Zeus 0 orders in 12.5h); RULE 1
  (no orders = OUR defect). Read-only DB forensics on live `state/zeus-world.db`
  (45GB) + `state/zeus_trades.db` (16GB) + `state/zeus-forecasts.db`.
- Method: reconstruct per-(family,bin,direction) candidate economics from
  `no_trade_regret_events.envelope_json` (rich 11-12KB DecisionProvenanceEnvelope
  carrying the full 22-candidate `candidate_book`), cross with the live
  `executable_market_snapshots` orderbook ladders and the `forecast_posteriors`
  q/q_lcb/q_ucb blobs. 26 families (one per city), 572 candidate legs, 286 bins
  with both legs present.

## TL;DR verdict

**The killer is NOT honest-no-edge and NOT a book-binding bug. It is a buy_no
robust-lower-bound (`q_lcb_no`) that is UNIVERSALLY ZERO** — `q_lcb_no = 0.0` in
**286 / 286** bins examined, across every city, regardless of how confident the
model is on the NO side. Because the admission gate is `q_lcb − cost > 0` and
`q_lcb_no ≡ 0`, **no buy_no candidate can EVER clear**, so the favorite-longshot
NO harvest (Zeus's strategy of record) is structurally extinguished. The
favorite-longshot bins that DO carry model NO mass are exactly the far-tail bins
whose NO ask is `clob_no_ask_illiquid` (priced only by the complement-synthesized
maker lane) or whose NO ask is real but the zeroed `q_lcb_no` denies the edge.

This is **structural (H2-class)**, but it is a probability-construction seam, not a
book/candidate-binding seam. The books bind correctly; the YES leg prices
correctly; the NO robust bound is the single broken input.

## The decisive datum (orchestrator's buyable-and-has-edge count)

Across 26 families / 478 priced legs, using the system's OWN per-leg q_lcb vs its
OWN execution price:

| class | count |
|---|---|
| priced (buyable) legs | 478 |
| buyable AND q_lcb > price (has edge) | **13** |
| — of those, `buy_no` | **0** |
| — of those, `buy_yes` | 13 (ALL) |
| buyable, edge ≤ 0 | 465 |

All 13 edge-bearing legs are `buy_yes` longshots, every one rejected by
`DIRECTION_LAW_BIN_FORECAST_MISMATCH` or `COVERAGE_UNLICENSED_TAIL` (DESIGNED_GATEs,
not honest market). Zero `buy_no` legs have edge — because every `buy_no` q_lcb = 0.

## Evidence chain (verified, not re-derived)

1. Window (`created_at >= 2026-06-13T00`, `no_trade_regret_events`): 128 rows, 97
   `buy_no` + 31 blank-direction. The 97 carry `trade_score=0`, `p_fill_lcb=0`,
   `native_quote_available=0`. Dominant rejection (102 rows):
   `EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=16..20
   direction_law=1..3 coverage_unlicensed_tail=0..2 other=...`. (19 rows are honest
   `LIVE_INFERENCE_INPUTS_MISSING` day0/forecast-readiness — a separate self-resolving
   HONEST_DATA class, not the no-order root.)

2. Rejection labels are observability-only (`event_reactor_adapter.py`
   `_family_all_candidates_rejected_reason`, `_classify_rejection_missing_reason`):
   they read proofs, decide nothing. The REAL gate verdict lives in each leg's
   `missing_reason`. The dominant verdict is
   `ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:ev_per_dollar=-1.000000:q_lcb=0.000000:price=...`
   on `buy_no` legs — i.e. q_lcb=0, so edge = 0 − price = −price → ev_per_dollar=−1.

3. **buy_no q_lcb is universally 0** (`/tmp/analyze_lcb.py` over 286 bins):
   - `buy_no q_lcb > 0`: **0 / 286**
   - `buy_yes q_lcb > 0 but buy_no q_lcb == 0`: 62
   - both legs q_lcb == 0: 224
   The YES robust bound IS computed (62 nonzero); the NO robust bound never is.

4. The model's NO confidence is real and strong. The candidate_book's own per-bin
   `q_posterior` for `buy_no` is the proper `1 − q_yes` MECE complement (buy_yes
   q_post sums to 1.000 across Beijing's 11 bins; buy_no q_post = 1 − that). Far
   bins carry NO point mass 0.78–1.00 (e.g. Beijing-28°C buy_no q_post=0.977,
   NO ask 0.67 → +30¢ by point; Tel Aviv-29°C q_post=1.000, NO ask 0.47). The
   stored `forecast_posteriors` q_ucb_json (cycle 2026-06-12T06) gives per-bin
   `no_lcb = 1 − q_ucb_yes` of **0.20–0.95** (zero bins saturate q_ucb_yes ≥ 0.999).
   So a NON-zero robust NO bound EXISTS in the model; it is lost before the gate.

## Price provenance (operator's "价格必须从市场获取" axis)

Classified all 478 priced legs by mode (maker lane is `buy_no`-ONLY,
`event_reactor_adapter.py:13490` `if direction != "buy_no": return None`):

| class | count |
|---|---|
| `buy_yes` priced via real taker own-ask (TAKER/MAKER walk of `yes_asks`) | 286 |
| `buy_no` priced via maker-lane complement-synthesis (`MAKER`, p_fill=0) | **185** |
| `buy_no` priced via real NO taker ask | 7 |

`_maker_quote_execution_price_from_snapshot` (`event_reactor_adapter.py:13451`)
emits `quote = tick_round_down(1 − comp_best_bid − tick)` — a price SYNTHESIZED from
the complementary YES bid, `price_type="bid"`, and **`p_fill_lcb = 0.0` hardcoded**
(line 13522, "a maker rest fills only when the book comes to it"). So 185/192
priced `buy_no` legs are complement-derived maker quotes that, by construction,
report p_fill=0. This is NOT a misrepresentation of edge (the function explicitly
does not claim edge; the belief cap is applied later), and it is correct doctrine
("我们的系统本质上是maker") — BUT it means: even if the NO q_lcb were fixed, these
185 legs are resting maker quotes that only fill passively (p_fill_lcb=0), so
`trade_score = p_fill_lcb · edge = 0` REGARDLESS of edge, on the visible-depth
taker fill metric. The maker EV is supposed to be rescored by the mode-consistent
EV seam (`_mode_consistent_ev_for_proof`, line 7538) which uses a resting fill
prior — but with q_lcb_no=0 that seam also yields ≤0.

The orchestrator's example "Beijing 34°C buy_yes price=0.0010 ev=+38" is NOT a
maker synthesis: the YES-34°C snapshot (`ems2-195da2704...`) carries a REAL ask
ladder whose cheapest level is `0.001 × 30.7 shares` (verified against
`orderbook_depth_json`). It is a genuine +EV taker longshot, killed by the
direction law (34°C is 2.5°C from μ=31.45, threshold 1.0). Real edge, deliberate
gate — not a phantom.

## Competing hypotheses

**H1 (honest σ-root / calibration is the path).** On liquid bins the model has no
real edge because the calibration q is too flat. — PARTIALLY TRUE but NOT the
binding constraint. Evidence FOR: the buy_yes q_lcb/q_point band ratio is
pathologically wide (median 0.246 — the robust YES bound is ~25% of the point), a
genuine σ-width symptom; and many NO legs (Helsinki-15, Jeddah-38) are honestly
−EV even at the point estimate. Evidence AGAINST: the stored posterior carries
`no_lcb = 1 − q_ucb_yes` of 0.78–0.95 on the far bins (NOT 0), and the buy_no
q_lcb reaching the gate is **0**, not 0.78 — so the binding zero is NOT the model's
honest σ; it is a value loss between the posterior and the proof.

**H2 (structural defect).** A real robust NO bound exists but is zeroed before the
gate. — CONFIRMED as the binding constraint. `q_lcb_no = 0` in 286/286 bins; the
YES leg's bound survives while the NO leg's does not; the model's own posterior
proves a non-zero NO bound exists.

## Where the zero comes from (two seams, same observable)

Both live probability paths can emit `q_lcb_no = 0` and are indistinguishable from
the persisted observable alone:

- **Canonical path** (`_canonical_probability_and_fdr_proof`,
  `event_reactor_adapter.py:10520`): for a bin whose YES side has no executable
  market (`yes_executable=False`, line 10605), the `else` branch (line 10668)
  forces `q_lcb_no = q_point = 0.0` (line 10669, comment line 10666: "no samples =>
  no native NO authority"). The favorite-longshot NO bins are EXACTLY the bins with
  illiquid YES asks → this branch zeros them by design. For the LIQUID near-center
  bins (`yes_executable=True`, line 10610), q_lcb_no = `lower_quantile(1 −
  yes_samples)` clamped to `[0, 1−q_yes]` (`_side_q_lcb_from_yes_samples`, line
  10464-10469). This is 0 only if the 95th-percentile YES sample reaches ≥1.0 — the
  extreme-σ saturation.

- **Replacement path** (`_replacement_authority_probability_and_fdr_proof`, line
  10079): `no_lcb = _replacement_no_lcb_for_bin(bundle, bin_id, q_yes)` (line
  10245). That helper (line 10048) returns `0.0` (fail-closed) when the bundle's
  `q_ucb` map lacks `bin_id` (line 10069/10076) — a bin_id keying miss zeros EVERY
  NO leg even with q_ucb present.

Path discriminator (not fully conclusive): all 26 envelopes carry
`q_mode = "UNAVAILABLE: bundle not provided"` and `posterior_id = "UNAVAILABLE"`,
and all 489 live `2026-06-13` posteriors are `trade_authority_status = SHADOW_ONLY`
/ `posterior_method = openmeteo_..._soft_anchor` — NOT the `FUSED_NORMAL_FULL/PARTIAL`
the replacement q-mode gate admits (line 10152, raises
`REPLACEMENT_Q_MODE_NOT_LIVE_ELIGIBLE`). A replacement-gate raise becomes
`LIVE_INFERENCE_INPUTS_MISSING` — but only 19 window rows are that; the 102 dominant
rows are full priced books. That pattern is most consistent with the **canonical
path** running (the soft-anchor posterior is not replacement-live-eligible), in
which case the universal NO zero is the canonical `yes_executable`/extreme-σ
branch, leaning the residual back toward H1's σ-width on the liquid bins while the
illiquid-YES bins are zeroed by the explicit `else` branch. The
`q_mode=UNAVAILABLE` provenance field may itself be an unwired observability gap, so
this discriminator is suggestive, not proof.

## Counterfactual (is the zero load-bearing?)

YES. Replacing the zeroed buy_no q_lcb with the stored posterior's
`1 − q_ucb_yes` and testing vs the leg's NO price: **24 / 82 matched buy_no legs
would clear** their NO price (edges +0.005 .. +0.31; e.g. Beijing-28 +0.24,
Tel Aviv-29 +0.31, Warsaw-21 +0.22, Milan-31 +0.25). Caveat: this used the
SHADOW_ONLY stored posterior's q_ucb; the candidate's OWN per-bin NO point is even
higher (0.977 for Beijing-28). Either way, a non-trivial favorite-longshot NO book
exists and is suppressed entirely by the q_lcb_no=0.

## Recommendation

This is a fix-NOW structural defect, NOT a wait-for-calibration. The σ-width
(C1 era-EB / C3 JS-toward-market) is a real secondary problem that would tighten the
YES band, but it does not unblock orders while `q_lcb_no ≡ 0` denies every NO leg
unconditionally. Two precise probes before patching:

1. **Confirm the path** by instrumenting one live decision (Beijing/Singapore
   06-13): log which of `_canonical_probability_and_fdr_proof` vs
   `_replacement_authority_probability_and_fdr_proof` produced the family, and the
   raw `q_lcb_no_raw` (pre-clamp) on a liquid near-center bin (e.g. Singapore-32,
   q_point=0.596). If `q_lcb_no_raw ≤ 0` there, it is genuine σ-saturation (the
   95th-pct YES sample hits 1.0) → H1 surface, fix = sample-width / N_eff. If
   `q_lcb_no_raw > 0` but the persisted value is 0, it is a transport/clamp/keying
   defect → H2 surface, fix at the seam.
2. **Confirm the maker-lane interaction**: even with q_lcb_no fixed, verify
   `_mode_consistent_ev_for_proof` rescoring lets a complement-synthesized maker
   buy_no with a strong belief produce a positive chosen_ev (the p_fill=0 must not
   zero the maker score). Otherwise the maker lane needs the resting-fill prior, not
   the taker p_fill, on the score.

## Discriminating probe (single highest-value)

Log `q_lcb_no_raw` (pre-clamp) and the path tag for ONE live Singapore-32°C /
Beijing-28°C decision. That one number splits H1-σ (raw ≤ 0) from H2-transport
(raw > 0, persisted 0) definitively, which is the only remaining uncertainty.

## File:line references

- `src/engine/event_reactor_adapter.py:7430-7454` — proof reads
  `q_lcb_by_direction[(cond,"buy_no")]`, transports as `q_lcb`.
- `:7574-7582` — `_capital_efficiency_untradeable_reason` fires with the q_lcb it
  was handed (the 0).
- `:10048-10076` — `_replacement_no_lcb_for_bin`, fail-closed `return 0.0` when
  q_ucb lacks bin_id.
- `:10405-10483` — `_side_q_lcb_from_yes_samples` (canonical), q_lcb_no =
  lower_quantile(1−yes_samples), clamp `[0, 1−q_yes]`.
- `:10605-10677` — canonical `yes_executable` gate; `else` branch zeros NO on
  illiquid-YES bins (line 10669).
- `:10152` — replacement q-mode gate (admits only FUSED_NORMAL_FULL/PARTIAL).
- `:13451-13525` — `_maker_quote_execution_price_from_snapshot`: complement-derived
  price, `p_fill_lcb=0.0` hardcoded, buy_no-only.
- `:13528-13633` — `_execution_price_from_snapshot`: routes empty-own-ask
  (`clob_no_ask_illiquid`) buy_no to the maker lane.
- `src/contracts/rejection_reasons.py` — taxonomy; `EVENT_BOUND_ALL_CANDIDATES_REJECTED`
  is HONEST_MARKET (mislabels this structural zero as efficient-market normal).
- Live config: `openmeteo_..._soft_anchor_trade_authority_enabled=True` but all
  489 `2026-06-13` posteriors `trade_authority_status=SHADOW_ONLY`.
