# Zeus Failure Modes Reference

Durable reference for failure classes with code-grounded examples, invariant
anchors, and the exact contracts that prevent recurrence.

Authority: executable source, tests, machine manifests, and authority docs win
on disagreement with this document.

---

## 1. Settlement & Rounding Failures

### 1.1 Rounding rule mismatch

**Failure**: Using Python `round()`, `numpy.round`, or `int()` for settlement
values produces incorrect integers. Python banker's rounding (`round(0.5) = 0`,
`round(1.5) = 2`) differs from WMO half-up (`floor(x + 0.5)`), producing
systematic settlement prediction errors at .5 boundaries.

**Invariant**: `SettlementSemantics.assert_settlement_value()` gates every DB
write. `apply_settlement_rounding()` is the shared dispatch for both
`MarketAnalysis._settle()` and `Day0Signal._settle()`.

**Code anchor**: `src/contracts/settlement_semantics.py:round_wmo_half_up_values()`

### 1.2 Oracle truncate applied to wrong city

**Failure**: `oracle_truncate` (floor) is correct for Hong Kong (HKO) where UMA
voters apply truncation bias ("28.7 → 28"). Applying it to WU cities produces
systematic -0.5°C bias on half the bins.

**Invariant**: `SettlementSemantics.for_city()` is the single entry point.
`oracle_truncate` routing is gated by `source_type == "hko"`. Empirically
verified: 14/14 match on HKO with floor vs 5/14 with wmo_half_up.

### 1.3 Shoulder bin treated as finite range

**Failure**: Assuming "58°F or higher" has `width=2` and using it for
width-normalized Platt calibration. Shoulder bins have `width=None` (unbounded).
Dividing by `None` crashes; dividing by a wrong finite width produces incorrect
per-degree density.

**Invariant**: `normalize_bin_probability_for_calibration()` passes through raw
probability when `bin_width is None or <= 0`. Width-normalized Platt
(`_bootstrap_bin`) raises `ValueError` if a bin with `width None or <= 0`
appears in WND input space.

### 1.4 °C/°F bin family mixing

**Failure**: Fitting a Platt model on calibration pairs from both °C and °F
cities produces meaningless parameters. A 2°F range bin and a 1°C point bin
have completely different probability scales.

**Invariant**: Platt models are bucketed by `cluster:season`. Cities are
assigned to clusters by geography. HIGH and LOW tracks have separate models.
`temperature_metric` is stamped on every calibration pair.

---

## 2. Probability Chain Failures

### 2.1 Naive member counting

**Failure**: Computing P_raw by directly testing bin membership per ENS member
without Monte Carlo. This ignores sensor noise and settlement rounding, producing
a distribution shape that diverges from the MC-generated P_raw space. Platt
models trained on MC-generated P_raw would not generalize to naive counts.

**Invariant**: `p_raw_vector_from_maxes()` is the single code path for both live
inference and offline calibration rebuilds. Training and inference must use this
function.

### 2.2 Bias correction training/inference mismatch

**Failure**: If live signals use ECMWF bias correction but calibration pairs were
trained without it, Platt maps from a corrected P_raw space to an uncorrected
outcome space. The calibrator is out-of-domain.

**Invariant**: `EnsembleSignal.__init__()` stores `self.bias_corrected`. Harvester
passes `bias_corrected` to `add_calibration_pair()`. Cross-module test
`test_calibration_pairs_use_same_bias_correction_as_live` enforces consistency.

### 2.3 UNVERIFIED calibration entering edge computation

**Failure**: UNVERIFIED calibration rows feeding Platt → alpha → posterior →
edge → execution. The calibration may be from a buggy rebuild, wrong source,
or incomplete data.

**Invariant**: Two gates:
1. `get_pairs_for_bucket(authority_filter='VERIFIED')` at query time
2. `compute_alpha(authority_verified=False)` → `AuthorityViolation` at
   market_fusion boundary

### 2.4 Monitor refresh double-inversion (buy_no)

**Failure**: Historical incident: monitor_refresh converted P(YES) → P(NO)
for buy_no positions, then exit_triggers inverted again. Double-inversion
caused 7/8 buy_no positions to false-exit in 30-90 minutes.

**Invariant**: `refresh_position()` converts once:
`if direction == "buy_no": p_cal_native = 1.0 - p_cal_yes`. Exit triggers
operate in native direction space — no further flipping.

---

## 3. Lifecycle & State Failures

### 3.1 Exit intent confused with economic close

**Failure**: Treating `pending_exit` as if the position is already closed.
`pending_exit` means an exit order has been placed — the position is still
live and the order may be cancelled.

**Invariant**: `LEGAL_LIFECYCLE_FOLDS` allows `pending_exit → active`
(cancel and restore). `release_pending_exit_runtime_state()` handles this
backward transition. Settlement dedup checks `position_current.phase`
(DB truth), not in-memory state.

### 3.2 Chain unknown treated as chain empty

**Failure**: When the Polymarket API fails, treating the empty response as
"all positions are gone" and voiding everything. This is the single most
dangerous failure mode — it can liquidate the entire portfolio on a
transient API error.

**Invariant**: `classify_chain_state()` returns `CHAIN_UNKNOWN` when API
fails. Reconciliation Rule 2 (void) only fires when
`chain_state != CHAIN_UNKNOWN` — unknown skips void entirely. Additional
stale guard: if any active position was verified within 6 hours, empty
API response is treated as unknown, not empty.

### 3.3 RED risk made advisory-only

**Failure**: RED risk level logged but not acted upon. INV-05 explicitly
forbids advisory-only risk.

**Invariant**: `get_current_level()` returns RED on: no risk_state row,
row older than 5 minutes, or any DB error. RED behavior: cancel all
pending orders, sweep all active positions. `force_exit_review` flag
written to risk_state for cycle_runner to read.

### 3.4 Stale in-memory portfolio causing duplicate settlement

**Failure**: In-memory portfolio loaded from JSON cache shows a position as
`active`, but the DB already settled it. Without dedup, the position gets
settled twice — double P&L, double calibration pairs.

**Invariant**: Three-layer settlement dedup:
1. DB-level: `_dual_write_canonical_settlement_if_available()` checks
   `position_current.phase` — terminal phases are skipped
2. Iterator-level: `_settle_positions()` queries `position_current` for
   all positions in the market before any computation
3. Runtime state: positions in terminal runtime states are skipped

---

## 4. Data Ingestion Failures

### 4.1 Partial harvest (Gamma API pagination)

**Failure**: First page of settled events succeeds, second page fails.
Returning the first page as "all settlements" misses settlements on page 2+.

**Invariant**: `_fetch_settled_events()` distinguishes first-page error
(warning + empty return, retries next cycle) from mid-pagination error
(offset > 0 → `RuntimeError`, refuses partial results).

### 4.2 Sentinel values in observation data

**Failure**: Open-Meteo occasionally returns sentinel values (99999, -9999)
that pass basic null checks but corrupt temperature records.

**Invariant**: `IngestionGuard.check_unit_consistency()` (Layer 1) applies
earth records bounds checking. `_validate_hourly_reading()` runs this check
on every row before INSERT.

### 4.3 DST spring-forward ghost observations

**Failure**: Spring-forward creates a local hour that does not exist (e.g.,
2:00 AM → 3:00 AM). An observation timestamped at 2:30 AM local is
physically impossible.

**Invariant**: `IngestionGuard.check_dst_boundary()` (Layer 5) rejects rows
where `_is_missing_local_hour()` returns True. Coverage tracking at daily
grain (not hourly) prevents DST spring-forward's 23-hour day from
false-positive as a coverage hole.

### 4.4 Cross-mode truth file collision

**Failure**: Live mode reading a truth file written by backtest mode, or
vice versa. The data is valid for the wrong mode.

**Invariant**: `read_mode_truth_json()` validates the file's `mode` tag
matches the caller's `mode` parameter. Mismatch → `ModeMismatchError`.
`mode=None` is explicitly rejected (not silently defaulted).

---

## 5. Execution Failures

### 5.1 Kelly oversizing from implied probability

**Failure**: Using `implied_probability` (market price) as Kelly entry cost
instead of fee-adjusted VWMP. Kelly interprets cost as "what I pay per
share" — implied probability is an estimate of true value, not a cost.
This systematically oversizes positions.

**Invariant**: `ExecutionPrice.assert_kelly_safe()` enforces three conditions:
`price_type ≠ "implied_probability"`, `fee_deducted = True`,
`currency = "probability_units"`. Any violation → raise.

### 5.2 Double taker fee application

**Failure**: Calling `with_taker_fee()` on an already fee-adjusted price
deducts fee twice, making positions appear more expensive than they are.

**Invariant**: `with_taker_fee()` checks `self.fee_deducted`. If already
True → `ExecutionPriceContractError`.

### 5.3 SELL rounding up → selling more shares than held

**Failure**: `math.ceil()` on SELL shares exceeds held position. If held
100.004 shares, ceiling to 100.01 tries to sell 0.006 unheld shares.

**Invariant**: BUY rounds UP (`math.ceil`), SELL rounds DOWN (`math.floor`)
with epsilon guards (1e-9). This prevents both under-buying (BUY) and
over-selling (SELL).

### 5.4 Unknown discovery mode timeout

**Failure**: An unrecognized discovery mode string defaults to some arbitrary
timeout, potentially holding orders open for hours.

**Invariant**: `create_execution_intent()` has no default case — unknown
discovery mode raises `ValueError` (fail-closed). Valid modes and their
timeouts are hard-coded: `opening_hunt=14400s`, `update_reaction=3600s`,
`day0_capture=900s`.

---

## 6. Invariant Crosswalk

| Code | Where enforced |
|------|---------------|
| INV-05 | Risk advisory forbidden — `get_current_level()` defaults to RED |
| INV-21 | `ExecutionPrice.assert_kelly_safe()` typed boundary |
| B077/SD-A | `ModeMismatchError` in truth file reads |
| B081 | Shared `apply_settlement_rounding()` dispatch |
| K4 | Authority hard gate in `compute_alpha()` |
| P6 | `_settle_positions()` DB-level dedup anchor |

---

## 7. Cross-References

- Settlement semantics: `src/contracts/settlement_semantics.py`
- Lifecycle folds: `src/state/lifecycle_manager.py`
- Chain reconciliation: `src/state/chain_reconciliation.py`
- Kelly contract: `src/contracts/execution_price.py`
- Risk level: `src/riskguard/risk_level.py`
- History lore: `architecture/history_lore.yaml`
- Negative constraints: `architecture/negative_constraints.yaml`
