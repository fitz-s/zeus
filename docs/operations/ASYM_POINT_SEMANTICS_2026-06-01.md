# ASYM_POINT_SEMANTICS — Is q_NO a structural reverse of q_YES, or computed per bin-settlement-region?

- Created: 2026-06-01
- Last reused/audited: 2026-06-01
- Authority basis: Operator read-only investigation (NO must derive independently per bin settlement
  semantics, never as arithmetic `1 - yes_q`). Repo HEAD 6fcd05a69f.
- Scope: read-only. No edits / git / DB writes.

## Verdict (one line)

**CONFIRM — `1 - yes_q` is the CORRECT per-token NO for every bin type (point / finite_range /
open_shoulder), because `yes_q` is the calibrated per-bin region probability `P(round ∈ bin_i)` built
directly from the member distribution over a COMPLETE partition. NO is NOT a flip of a point.**

## Why the `1 - yes` reverse is structurally sound here (the key relationship)

Each Polymarket token is a **binary YES/NO on its OWN bin**. For token on bin_i:
`q_NO_i = P(round ∉ bin_i) = 1 - P(round ∈ bin_i) = 1 - q_YES_i`. This identity is exact **provided
q_YES_i is itself the probability of bin_i's full settlement region** — which the live system computes,
not a point.

The settlement region per bin is owned by `Bin.contains()` (`src/types/market.py:117-120`,
`_norm_low/_norm_high` send shoulders to ±inf):

| Bin type | Encoding (`src/types/market.py`) | YES region computed | q_NO = 1−YES means |
|---|---|---|---|
| POINT (°C) | `low==high` (`is_point`, :122-125) | `P(round == t)` via `contains` | `P(round ≠ t)` ✓ |
| FINITE_RANGE (°F) | `low<high`, width=2 (:128-143) | `P(round ∈ {a..b})` via `contains` | `P(round ∉ {a..b})` ✓ |
| OPEN_SHOULDER | `low=None`→`-inf` or `high=None`→`+inf` (`is_shoulder`, :113-115) | `P(round ≥ X)` / `P(round ≤ X)` via `contains` (full tail) | `P(round < X)` / `P(round > X)` ✓ — a DIFFERENT region, correctly the partition remainder |

YES vector provenance — `p_raw_vector_from_maxes` (`src/signal/ensemble_signal.py:173-265`):
`p += bin_counts_from_array(measured, bins)` over the MC settlement chain, then normalize so the vector
sums to 1.0. Every bin (incl. shoulders) gets its region mass **directly**; shoulders are first-class
partition members, never a `1 − point`. The analytic twin (`analytic_p_raw_vector_from_maxes`, :268+)
integrates Φ over each bin's rounding preimage identically. Platt keeps shoulders in raw probability
space and does NOT width-decompose them into points (`src/calibration/platt.py:80-86`), so the calibrated
shoulder YES stays a region probability.

Therefore `1 - yes_q` inherits the partition-remainder semantics automatically; it is algebraically
identical to summing the YES mass of all OTHER bins. The operator's structural worry — "shoulder NO is a
different region than a flipped point" — is satisfied because the YES it complements is already the
shoulder region, not a point.

## Decisive numerical proof (live MC path, Tokyo-like °C partition)

Partition `{≤26, 27, 28, 29(point), 30, ≥31(shoulder)}`, 20k MC iters, instrument σ=0.5, via the real
`SettlementSemantics.round_values` + `bin_counts_from_array`:

| Bin | q_YES | q_NO direct `P(round∉bin)` | q_NO `1−q_YES` | agree |
|---|---|---|---|---|
| 26°C or below (shoulder) | 0.0021 | 0.9979 | 0.9979 | ✓ |
| 28°C (point) | 0.2008 | 0.7992 | 0.7992 | ✓ |
| 29°C (point) | 0.2683 | 0.7317 | 0.7317 | ✓ |
| 31°C or higher (shoulder) | 0.2158 | 0.7842 | 0.7842 | ✓ |

Agreement is **exact for all bin types** (atol 1e-9). Counterfactual confirms the ONLY way `1−YES`
breaks: if the shoulder YES were mistakenly computed point-wise (`P(round==31)=0.1746` instead of the
real `P(round≥31)=0.2158`), then `1−YES=0.8254` would diverge from the true shoulder NO
`P(round<31)=0.7842`. The live system does NOT do this — it uses the shoulder bin's full `≥X` region.

## Every naive-reverse site (file:line) — all DEFENSIBLE per-bin complements

| file:line | expression | context | verdict |
|---|---|---|---|
| `src/engine/event_reactor_adapter.py:2878` | `(no_token_id, "buy_no", 1.0 - yes_q, no_lcb)` | `yes_q = q_by_condition[condition_id]` = per-bin region YES (:2870) | CONFIRM |
| `src/engine/event_reactor_adapter.py:3132` | `q_point = ... (1.0 - yes_posterior)` | neutral fill when NO side non-executable; `yes_posterior=p_posterior_vec[index]` per bin (:3109) | CONFIRM |
| `src/engine/event_reactor_adapter.py:3864` | `min(no_lcb, 1.0 - q_value)` | day0-masked LCB; `q_value` per-bin live prob | CONFIRM |
| `src/engine/event_reactor_adapter.py:3864`→`masked` | `masked_lcb_by_direction[... "buy_no"]` | mirrors main path | CONFIRM |
| `src/strategy/market_analysis.py:572` | `p_model_no = 1.0 - p_cal[i]` | per-bin calibrated YES → per-bin NO | CONFIRM |
| `src/strategy/market_analysis.py:574` | `p_post_no = 1.0 - p_posterior[i]` | per-bin posterior YES → per-bin NO; gated by native NO quote (`supports_buy_no_edges`) | CONFIRM |
| `src/strategy/market_analysis.py:698` | `p_posterior=1.0 - p_posterior[i]` | trace-only (NO side non-executable branch) | CONFIRM |
| `src/strategy/market_analysis.py:890` | `(1.0 - p_post_yes) - c_b` | bootstrap NO edge, per-bin | CONFIRM |
| `src/strategy/market_analysis.py:399` | `1.0 - p_market[bin_idx]` | `buy_no_complement_diagnostic_price`, **binary-only, explicitly non-executable diagnostic** (:391-398, raises if >2 bins) | CONFIRM (not entry authority) |

Note on EXECUTION price (separate from probability): NO-side *entry price* is NOT a complement — it comes
from the **native NO-token book VWMP** (`buy_no_market_price`, market_analysis.py:383-389; INV-38 comment
:615-617). `1−p_market` as a price is gated to a binary-only diagnostic. So the `1−x` reverse appears ONLY
on the model-probability axis (where it is exact), never on the executable cost axis.

## #91 follow-up note (q_posterior normalized vs q_lcb un-normalized)

The "domain mismatch in robust_trade_score" concern relates to Platt `calibrate_and_normalize`
(`src/engine/evaluator.py:39`) acting on the **YES** vector before the complement, and to restoring q_lcb
into probability space (`event_reactor_adapter.py:3111-3124`: `q_lcb = edge_lcb + c_b`). That fix
normalizes/aligns the YES-side domains; it does NOT introduce or remove any NO=1−YES asymmetry, because
NO is and remains a strict per-bin complement of the (now domain-correct) per-bin YES. Commit
9e944bc2d8 (#91) is a bundled PR-comment-fix commit; the structural NO derivation is unchanged by it and
remains correct.

## Conclusion

No DEFECT. The system never computes q_NO as a family-level or point-wise flip that would mishandle
shoulder/range structure. q_NO = 1 − q_YES is mathematically identical to "P(round in the complement
region of bin_i)" because q_YES is the per-bin region probability over a complete MECE partition, with
shoulders carried as full tails (not points) end-to-end through MC/analytic p_raw → per-bin Platt →
posterior. Any YES corruption would be inherited by NO — but that is a YES-side concern, not a NO-side
semantics defect.
