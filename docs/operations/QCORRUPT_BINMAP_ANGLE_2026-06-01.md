# Q-Corruption Root-Cause — BIN↔PROBABILITY-INDEX Off-By-One Angle

- Created: 2026-06-01
- Last reused or audited: 2026-06-01
- Authority basis: read-only root-cause; HEAD 6fcd05a69f; angle = bin/label off-by-one as cause of Zeus q-vector warm shift
- Scope: READ-ONLY. No edits, no git, no DB writes.

## VERDICT: REFUTE

The bin↔probability-index off-by-one / boundary-misalignment hypothesis is **REFUTED**.
The bin labeled "31°C" correctly receives bin-31 probability mass. There is no +1 (or
any) label/index shift in bin definition, market-label parse, list ordering, or the
production mass-assignment code. The traded q's apparent placement on label "32" is
**downstream of p_raw** (calibration / bootstrap posterior), not a mapping bug.

## Decisive Test — Production Assignment Alignment Table

Live Singapore 2026-06-03 high, snapshot_id 1151951 (latest), 51 members, unit=degC,
rounding=wmo_half_up (floor(x+0.5)), precision=1.0. Bins parsed from live
`market_events` rows via the same fields `_bin_from_market_event` reads
(`range_low`/`range_high`/`range_label`), p_raw via production `p_raw_vector_from_maxes`.

Naive rounded histogram of live members (matches the EVIDENCE exactly):
P(30)=0.314, **P(31)=0.588**, P(32)=0.059.

| idx (value-order) | low | high | settlement_values | production p_raw | label temp |
|---|---|---|---|---|---|
| 0 | None | 26 | None (≤26 shoulder) | 0.0000 | 26 |
| 1 | 27 | 27 | [27] | 0.0000 | 27 |
| 2 | 28 | 28 | [28] | 0.0000 | 28 |
| 3 | 29 | 29 | [29] | 0.0346 | 29 |
| 4 | 30 | 30 | [30] | 0.3687 | 30 |
| 5 | 31 | 31 | [31] | **0.5192** | 31 |
| 6 | 32 | 32 | [32] | 0.0767 | 32 |
| 7 | 33 | 33 | [33] | 0.0007 | 33 |
| 8 | 34 | 34 | [34] | 0.0000 | 34 |
| 9 | 35 | 35 | [35] | 0.0000 | 35 |
| 10 | 36 | None | None (≥36 shoulder) | 0.0000 | 36 |

Sum = 1.0. The peak p_raw (0.519) lands on the bin **labeled 31** with sv=[31].
Bin "32" gets 0.077. (MC < naive histogram because production adds instrument sigma —
sensor-noise dispersion bleeds mass symmetrically 31→30/32; the peak does NOT move.)
If the off-by-one existed, label "32" would carry ≈0.519. It carries 0.077. REFUTED.

## Evidence by sub-claim

1. **Bin definition — point not interval.** `src/types/market.py:55-58,123-126` — °C
   point bin "31°C" → `low==high==31`, `width==1`, `settlement_values==[31]`. NOT
   `[31,32)` and NOT `[30.5,31.5)`. `Bin.contains` (`market.py:118-120`) =
   `low <= v <= high`, inclusive both ends.

2. **Mass assignment is content-addressed, not positional.**
   `bin_counts_from_array` (`src/types/market.py:243-247`) tests
   `(arr >= low) & (arr <= high)` per bin object. A member rounding to 31 is counted
   into the bin whose `(low,high)=(31,31)` — i.e. label "31". No ±1.
   Rounding preimage matches WMO floor(x+0.5): `analytic_p_raw_vector_from_maxes`
   `src/signal/ensemble_signal.py:286-287,347` — `wmo_half_up` preimage of t is
   `[t-0.5, t+0.5)`, consistent with the point bin {t}.

3. **Market-label parse is correct in the live DB.**
   `_bin_from_market_event` `src/engine/event_reactor_adapter.py:4043-4056` reads
   `range_low`/`range_high` directly. Live `market_events` (state/zeus-forecasts.db)
   stores "be 31°C" as `range_low=31.0, range_high=31.0` (verified, all 9 interior
   Singapore bins; 26 = ≤ shoulder high-only, 36 = ≥ shoulder low-only). Parse →
   `Bin(31,31,"C")`. No interval inflation, no +1.

4. **List ordering is internally coherent (no sort mismatch).**
   `bind_event_to_candidate_family` `src/events/candidate_binding.py:91-126` sorts
   candidates by key `(condition_id, yes_token_id, no_token_id, bin.label)` then sets
   `bins = tuple(candidate.bin for candidate in candidates)`. **bins[i] and
   candidates[i] derive from the SAME sorted sequence** → q-vector index i, bin[i],
   and market quote[i] always align. The condition_id-first key DOES scramble bins
   away from value order (live Singapore order: 26,33,34,30,35,27,28,29,36,31,32), but
   this is harmless: p_raw is content-addressed by each bin's own (low,high), and the
   MarketAnalysis quote loop `event_reactor_adapter.py:3338-3349` iterates the same
   `family.candidates`. Scramble is coherent across all three vectors.

5. **No reindex between p_raw and MarketAnalysis.** `bins = list(family.bins)` once
   (`event_reactor_adapter.py:3296`), passed unchanged to `_snapshot_p_raw`,
   `_snapshot_p_cal`, and `MarketAnalysis(bins=bins, ...)`
   (`event_reactor_adapter.py:3355-3358`). Same object order throughout.

## What this REFUTES, and where the warm shift actually lives

- p_raw itself is NOT warm-shifted to 32: peak is on 31 (0.519), bin 32 = 0.077.
- `_maybe_apply_edli_bias_correction` (`event_reactor_adapter.py:3493-3567`) runs
  BEFORE p_raw but is flag-gated OFF (`edli_v1.edli_bias_correction_enabled` default
  False) and fail-closed → did not warm the members here.
- Therefore q_YES(32)≈0.565 vs p_raw(31)=0.519 is a **continuous downstream
  transform** — calibration (`_snapshot_p_cal` / Platt, `event_reactor_adapter.py:3325`)
  or the bootstrap posterior in `MarketAnalysis` — NOT a bin/label off-by-one. The
  "striking match" q_YES(32)≈P(31) is coincidental magnitude, not index aliasing:
  if it were aliasing, p_raw(32) would already equal 0.519, which it does not.

## file:line index
- src/types/market.py:55-58,118-126,243-247 — point-bin semantics, contains, count
- src/signal/ensemble_signal.py:173,256-258,286-287,347 — MC assignment + WMO preimage
- src/engine/event_reactor_adapter.py:3296,3318,3338-3358,4043-4056 — bins order, p_raw,
  MarketAnalysis assembly, market-event bin parse
- src/events/candidate_binding.py:91-126 — single-sort bins↔candidates coherence
- state/zeus-forecasts.db market_events / ensemble_snapshots(1151951) — live data

## NEXT (outside this angle)
Investigate `_snapshot_p_cal` (Platt) and the MarketAnalysis bootstrap posterior for a
continuous warm transform; that is where q's mass moved off label 31. Not a mapping bug.
