# POLARITY / q-side audit — Singapore 2026-06-03 high

- Created: 2026-06-01
- Last reused/audited: 2026-06-01
- Authority basis: live q-corruption incident (Singapore 06-03 raw P(high=31)=0.588 modal,
  traded q_YES(31)=0.124, q peaks on bin 32). Read-only audit at HEAD 6fcd05a69f.
  Angle: PROBABILITY / q polarity — YES↔NO inversion vs continuous warm shift.

## VERDICT (q-side polarity)

**REFUTE a YES↔NO polarity inversion.** The q-side wiring is direction-correct end to
end; q_NO is the same-bin complement of q_YES, paired with the same-bin NO cost; no token
swap, no cross-bin/cross-side leak. The Singapore 06-03 corruption is a **continuous
one-bin WARM SHIFT introduced by the calibration (p_cal) step**, NOT a polarity/index swap.
p_raw is correctly modal at bin 31 (0.519 via the production extractor); the posterior q
peaks at bin 32 (0.565) with bin 31 collapsed to 0.124 — a monotone re-weighting, the
exact fingerprint of a warm-bias calibration transform, not an inversion.

## DECISIVE REPRODUCTION (production code + live DBs)

Raw ensemble (snapshot_id=1151951, ecmwf_open_data, fetch 06-01T08:27, lead 48h,
state/zeus-forecasts.db; 51 members, mean 30.63°C):

| bin | raw rounded P | production p_raw_vector_from_maxes |
|----:|--------------:|-----------------------------------:|
| 29  | 0.0392        | 0.0343 |
| 30  | 0.3137        | 0.3688 |
| 31  | **0.5882**    | **0.5192** (mode) |
| 32  | 0.0588        | 0.0768 |
| 33  | 0.0000        | 0.0009 |

p_raw is modal at 31 — YES↔bin mapping is NOT inverted (a polarity/index swap would put
the mode on the cold tail or reverse the vector). Confirmed via
`src/signal/ensemble_signal.py::p_raw_vector_from_maxes`.

Production-stored q per bin (`no_trade_regret_events`, state/zeus-world.db, decision_time
2026-06-01T17:40–18:42; q_live is the per-direction probability):

| bin | direction | stored q_live(dir) | implied q_YES | market YES ask |
|----:|-----------|-------------------:|--------------:|---------------:|
| 29  | buy_no    | 1.0000             | ~0.0000       | — |
| 30  | buy_no    | 0.9988             | 0.0012        | — |
| 31  | buy_no    | 0.8762             | **0.1238**    | 0.41 |
| 32  | buy_yes   | 0.5650             | **0.5650** (q mode) | 0.41 |
| 35  | buy_no    | 1.0000             | ~0.0000       | 0.008 |

Implied q_YES(31)=0.1238 reproduces the operator's reported q_YES(31)=0.124 to 3 dp, and
the q mode sits on bin 32. The mode moved 31→32 (one bin warm) and the whole vector
re-weighted monotonically toward warmer bins — continuous shift, not a swap. (Full p_cal
re-execution in-process raised `CALIBRATION_AUTHORITY_MISSING` because the calibration
store loader binds the canonical world connection / cluster-keyed Platt model, not a
file-path conn; the stored production q vector above is the authoritative artifact and
already encodes the live p_cal output.)

## q-side wiring trace (every YES/NO assignment site)

All sites verified direction-correct; NO is grounded as the same-bin complement + same-bin
NO cost, never a naive 1−YES against the wrong bin/side.

- `src/engine/event_reactor_adapter.py:2876-2879` — per-candidate loop. buy_yes gets
  `(yes_token_id, yes_q, yes_lcb)`; buy_no gets `(no_token_id, 1.0 - yes_q, no_lcb)`.
  `yes_q = q_by_condition[condition_id]` is the SAME-bin YES posterior. CORRECT.
  `1.0 - yes_q` is a complement, but it is the complement of the SAME bin's YES posterior
  and is paired with the SAME bin's no_token + NO cost → correct NO q. GROUNDED, not naive.
- `event_reactor_adapter.py:2890-2894` (`_execution_price_from_snapshot`, def ~2150) —
  cost is fetched for `selected_token_id=token_id` (= no_token_id for buy_no) via that
  token's own snapshot row's ask. NO cost is the NO token's own ask, not 1−YES-ask. CORRECT.
- `event_reactor_adapter.py:3116-3134` (`_canonical_probability_and_fdr_proof`) —
  `cost_by_direction = {buy_yes: p_market_yes[i], buy_no: p_market_no[i]}` same index i;
  neutral fallback `q_point = yes_posterior if buy_yes else (1.0 - yes_posterior)` — same-bin
  complement. CORRECT.
- `event_reactor_adapter.py:3146-3149` — `q_by_condition[condition]` is OVERWRITTEN with
  `evaluate_live_bins(prior).probabilities[index]`. `evaluate_live_bins`
  (`src/strategy/live_inference/inference_engine.py:20-36`) with no likelihoods/mask is just
  `normalize(prior)`. So stored q_YES = p_posterior / Σp_posterior. This is the ONLY place
  q magnitude is re-scaled post-calibration; it is an index-preserving normalization (the
  `{str(index): value}` map and `family.candidates[index]` share one ordering). NO index
  reversal. The 0.588→0.124 magnitude drop is upstream (p_cal), not here.
- `src/strategy/market_analysis.py:569-675` (`scan` buy_no branch) —
  `p_post_no = 1.0 - p_posterior[i]`, `p_market_no = buy_no_market_price(i)`; both same
  index i. CORRECT same-bin complement.
- `src/strategy/market_analysis.py:386-399` (`buy_no_market_price`, `buy_yes_market_price`)
  — `p_market_no[bin_idx]` and `1.0 - p_market[bin_idx]` are same-bin. CORRECT.
- `event_reactor_adapter.py:928` — `outcome_label = "NO" if token==no_token_id else "YES"`.
  Label matches token identity. CORRECT.
- `src/strategy/live_inference/trade_score.py:48-52,68-70` — robust edge =
  `min(q_5pct - c_95 - λ, q_posterior - c_stress - λ)`; for buy_no the adapter passes
  q_no + NO cost, so edge = q_no − cost_no. Direction-correct (matches the verified contract
  in `tests/engine/test_trade_score_direction_semantics.py`). CORRECT.
- Venue token polarity: all 11 June-3 conditions have `labels_swapped=0`,
  `token_map_valid=1`, outcomes `["Yes","No"]` (state/zeus_trades.db token_map_json).
  No venue-level YES/NO swap.

## Naive-complement sites (1−YES) — inventory and grounding status

Every `1−YES` below is a complement against the SAME bin's YES posterior and is paired with
that bin's NO token/cost. None is a naive complement against a different bin or a different
market's YES. All GROUNDED:

- `event_reactor_adapter.py:2878` `1.0 - yes_q` (buy_no q) — GROUNDED (same-bin + no_token).
- `event_reactor_adapter.py:3132` `1.0 - yes_posterior` (neutral fallback) — GROUNDED.
- `market_analysis.py:574` `p_post_no = 1.0 - p_posterior[i]` — GROUNDED (same i).
- `market_analysis.py:397` `1.0 - p_market[bin_idx]` (yes-from-no price) — GROUNDED.

## Where the corruption actually is (out of q-polarity scope, flagged for routing)

The warm shift is a **calibration / bias-correction** defect, consistent with the standing
A4 cold-bias note and `_maybe_apply_edli_bias_correction` (`event_reactor_adapter.py:3313`)
+ the Platt p_cal transform (`_snapshot_p_cal`, manager `get_calibrator`). The model marks
31 (raw mode, market-favored at 0.41) as a buy_no and pushes belief one bin warm to 32. The
trade-side consequence (q_YES(31)=0.124 → buy_no on the market's own favored bin) is
real risk, but it originates in the temperature/calibration domain, not a q YES↔NO polarity
inversion. Route to the calibration / bias-correction lane.

## Confidence

HIGH. q_YES(31)=0.1238 reproduced to 3 dp from production-stored rows; p_raw mode (31)
reproduced from the production extractor; every YES/NO assignment site read at HEAD and
found same-bin grounded; venue token maps unswapped.
