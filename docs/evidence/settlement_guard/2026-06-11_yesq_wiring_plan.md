# Plan evidence: same_bin_yes_posterior receipt-wiring — buy_no admission gate input loss

Created: 2026-06-11. Authority basis: production defect — today's FIRST positive
trade-score candidate (Shanghai|2026-06-12|high, bin "32°C", direction buy_no,
trade_score +0.0448, q_lcb_5pct 0.7699) rejected 2026-06-11T13:18:38Z with
`ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING`
(zeus-world.db no_trade_regret_events).

## Incident (re-verified against live DBs, read-only)
Live row: direction=buy_no, q_live (NO posterior)=0.86521, q_lcb_5pct=0.76996,
c_fee_adjusted=0.67122, trade_score=+0.04483, rejection_stage=TRADE_SCORE,
rejection_reason=ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING.
q_live=0.86521 ⇒ the 32°C YES posterior = 1 − 0.86521 = 0.1348 (matches the
verified serving q_json q_yes=0.1348). The trade_score is POSITIVE, so the proof
was scored with a real yes_q — the YES posterior existed and was material-floor
admitted at scoring time.

## Root cause (exact divergence, reproduced offline with the REAL gate + values)
`live_buy_no_conservative_evidence_rejection_reason` is enforced at TWO sites with
DIFFERENT inputs:

- (A) ADAPTER, event_reactor_adapter.py:6188 — passes `same_bin_yes_posterior=yes_q`
  (0.1348). Offline replay with the real row's values → returns **None** (admits;
  yes_q 0.1348 < 0.20 material floor, so no native-NO-source requirement).
- (B) RECEIPT, reactor.py:1131 `_receipt_money_path_blocker` — calls the SAME gate
  but CANNOT pass the posterior because `EventSubmissionReceipt` has **no
  `same_bin_yes_posterior` field**. It defaults to `None`, so the gate's first
  branch `if same_bin_yes_posterior is None: return
  "ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING"` fires UNCONDITIONALLY for
  every buy_no that reaches it.

Offline proof (real values): (A) → None (admit); (B) → the live row's exact reason
string. This is data-semantic loss at the proof→receipt module boundary: the proof
carries `same_bin_yes_posterior` (set at adapter:6269), but the receipt contract
has no field to carry it, so the re-enforcement gate is starved and rejects every
buy_no. NOT a forecast/topology/bin-binding defect — the q surface was correct and
present; only the receipt-boundary lost the gate input.

## Design failure → K-decision (make the category impossible)
The receipt must carry the SAME gate input the adapter gate had. One structural
field on the receipt + plumb it through the success-receipt path + feed it to the
receipt-level gate. After this, a buy_no whose posterior carried the bin can never
be rejected for a MISSING posterior at the receipt boundary.

## Change set (scope = adapter q-wiring + live_admission caller path + tests)
1. `src/events/reactor.py` — add `same_bin_yes_posterior: float | None = None`
   field to `EventSubmissionReceipt`; `_receipt_money_path_blocker` passes
   `same_bin_yes_posterior=receipt.same_bin_yes_posterior` to the gate.
2. `src/engine/event_reactor_adapter.py` — success-receipt `raw_receipt.update`
   sets `"same_bin_yes_posterior": proof.same_bin_yes_posterior`; the dict→dataclass
   deserializer (`_event_bound_submission_receipt_from_raw`) maps it.
3. `src/events/no_submit_receipts.py` `_receipt_json` — omit-when-None for the new
   field (receipt_hash byte-stability for legacy/canonical receipts that never
   carried it; mirrors alpha_gap / q_source / posterior_id pattern).

## What is preserved (NOT weakened)
- Complement-arithmetic ban: the value flows from `proof.same_bin_yes_posterior`
  (== yes_q from the materialized q-vector), NEVER `1 − price` or `1 − q_no`. The
  receipt-level gate now receives the same independently-materialized YES posterior.
- The gate logic itself is untouched: material-YES (≥0.20) buy_no still requires an
  allowed native-NO calibration source; only the input-loss is fixed.
- receipt_hash byte-stability via omit-when-None.

## Antibody (relationship test, real-shaped fixtures)
tests/engine/ — a bundle with full q_map + matching bin_topology and family
candidates built from market_events-style rows ⇒ every candidate's proof carries a
float `same_bin_yes_posterior`; that float survives the proof→receipt projection;
`_receipt_money_path_blocker` receives a float (never None) and does NOT emit
ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING when the posterior carries the
bin. The cross-module invariant: the YES posterior the ADAPTER gate saw == the YES
posterior the RECEIPT gate sees.
