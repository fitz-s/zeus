# P2 — W-SUBMIT: The Submit-Path Blocker (file-level implementation plan)

**Date:** 2026-06-14
**Mode:** PLAN-MAKING (no production edits, no deploy, no live touch). DBs opened `?mode=ro`, `timeout 25` per sqlite3.
**Role:** P2 workstream owner for the diagnosis's **#2 blocker** (`diagnosis_confirmation.md:130`): admitted candidates (`proof_accepted=1`) that die at SUBMIT via `real_order_submit_disabled` / `event_bound_final_intent_no_submit` / `SUBMIT_ABORTED_MODE_FLIPPED`. This is **Thrust 5** of `P1_strategy_of_record.md:97-101` ("make the submit stage a re-decision, not a death").
**Authority spine:** `diagnosis_confirmation.md`, `P1_strategy_of_record.md`, live `config/settings.json`, `state/zeus-world.db` (`edli_no_submit_receipts`), source (`src/main.py`, `src/engine/event_reactor_adapter.py`, `src/strategy/redecision.py`, `src/strategy/live_inference/mode_consistent_ev.py`).

---

## 0. THE QUESTION, ANSWERED FIRST (flag/arm-state or code gate?)

The operator's framing — *"Is it a flag (arm-state) or a code gate? File-level fix or confirm it is the intended arm-state."* — has a **three-part answer**, because the single label "submit-path blocker" is actually **three distinct mechanisms** with different verdicts. I separated them at source and at the receipt level (query+counts below). Conflating them is exactly the error the diagnosis warns against (`diagnosis_confirmation.md:25` — "both the receipt table AND the live cycle log must be read").

| Sub-blocker | Receipt reason | Recent count (≥06-10) | All-time | Flag or code? | Verdict |
|---|---|---|---|---|---|
| **W-S1** Arm not effective this cycle | `real_order_submit_disabled` + `NO_SUBMIT_ADAPTER_LANE:*` (default-reason `event_bound_final_intent_no_submit`) | 34 + 6 | 62,671 + 147 | **Both** — a per-cycle DEGRADE, not the master arm | **Confirm it is intended arm-state** (master arm IS on; the per-cycle degrades are honest fail-closed). Fix = OBSERVABILITY only. |
| **W-S2** Mode-flip terminal abort | `SUBMIT_ABORTED_MODE_FLIPPED:...` | ~50 | ~62 | **Code gate** (a typed exception) | **File-level fix** — convert terminal-abort → single-criterion re-decision (Thrust 5). |
| **W-S3** Locked-opportunity / price-improvement re-quote | `EDLI_LOCKED_OPPORTUNITY_NO_PRICE_IMPROVEMENT:...` | 0 recent | 5 | **Code gate** with a covert `0.02` constant | **File-level fix** — fold the `improve_delta` constant into the honest criterion (mirrors P1 Thrust 5's flat-0.01 finding). LOW priority. |

**The headline for the operator:** the master submit arm is **ON and correct** — `config/settings.json` has `real_order_submit_enabled: true`, `reactor_mode: "live"`, `edli_live_operator_authorized: true`, `durable_submit_outbox_enabled: true`, `edli_live_scope: "forecast_plus_day0"` (verified this session). The submit lane is **not** held dark by a master flag. The recent submit-stage deaths are (a) per-cycle fail-closed **degrades** that are *correctly* refusing to submit when the allocator / portfolio-state / operator-arm token is momentarily unavailable (W-S1 — intended arm-state, keep), and (b) a real **code gate**, `SUBMIT_ABORTED_MODE_FLIPPED`, that terminally discards an admitted candidate on a transient maker/taker book tick (W-S2 — the one real fix). **W-S2 is the only sub-blocker that wastes a genuine admission**, and even it is quantitatively small *today* because the admitted population is dominated by settlement-dead cheap-tail (P1 §7) — so W-SUBMIT's value is **conditional on Thrusts 3–4 first producing a real ring-bin candidate** (P1 dependency, §6 below). This is a plumbing-correctness fix, not an alpha source: it stops a *future* good candidate dying to a tick, it does not manufacture edge (operator law 8).

---

## 1. EVIDENCE: WHAT ACTUALLY KILLS AN ADMITTED CANDIDATE AT SUBMIT

### 1.1 The receipt-level distribution (state/zeus-world.db, read-only)

`edli_no_submit_receipts` is by CHECK constraint `side_effect_status='NO_SUBMIT'` (every row is a non-submit) and `reason`/`proof_accepted` live inside `receipt_json`. Among **`proof_accepted=1`** rows (the admitted candidates — the only ones that reach submit):

```
event_bound_final_intent_no_submit ................. 62,671   (default no-submit reason; NOT a death — see §1.2)
real_order_submit_disabled .........................    147
SUBMIT_ABORTED_MODE_FLIPPED:* (all variants) .......    ~62
EDLI_LOCKED_OPPORTUNITY_NO_PRICE_IMPROVEMENT:* .....      5
```
(query: `SELECT json_extract(receipt_json,'$.reason'), COUNT(*) FROM edli_no_submit_receipts WHERE json_extract(receipt_json,'$.proof_accepted')=1 GROUP BY 1 ORDER BY 2 DESC`)

Recent window (`created_at >= '2026-06-10'`): `event_bound_final_intent_no_submit=34`, `SUBMIT_ABORTED_MODE_FLIPPED=~50` (split across MAKER→TAKER and TAKER→MAKER at fresh_bid/ask spanning 0.01–0.78), `real_order_submit_disabled=6`.

**Decisive observation on the MODE_FLIPPED variants** — they fire on BOTH cheap bins (`fresh_bid=0.011:fresh_ask=0.012`, `0.1/0.12`, `0.14/0.15`) AND favorite bins (`0.64/0.77`, `0.71/0.78`). The flip is symmetric: 11× `MAKER→TAKER` at 0.011/0.012 and 11× `TAKER→MAKER` at 0.14/0.15 in the recent window. This is the classic 1-tick book-wobble-at-submit pattern the hysteresis margin (`mode_consistent_ev.py:358-363`) was built to suppress but does not fully kill.

### 1.2 `event_bound_final_intent_no_submit` is NOT a death — it is the default no-submit reason

`_DEFAULT_NO_SUBMIT_REASON = "event_bound_final_intent_no_submit"` (`event_reactor_adapter.py:1232`). It is the literal a receipt carries when **no specific gate** named the non-submission. On the **live adapter** this default is never returned for a full-pass — `_stamp_live_adapter_lane` (`:1274-1294`) stamps `LIVE`/`SUBMIT_DISABLED`, and on the **no-submit adapter** `_stamp_no_submit_adapter_lane` (`:1235-1271`) **rewrites** a full-pass default to `NO_SUBMIT_ADAPTER_LANE:<degrade_cause>` (`:1269`). So the 62,671 `event_bound_final_intent_no_submit` rows are the **historical shadow-era backlog** (written before the lane-stamp antibody #54 and before the arm flipped on), plus any receipt-write path that bypasses the stamp. They are NOT live submit-stage deaths. The recent 34 carry **`submit_lane = NULL`** (verified: `SELECT json_extract(receipt_json,'$.submit_lane') ...` returns empty for them) — meaning the stamp did not run on the persist path that wrote them. **This is a real but secondary observability hole** (see W-S1 fix).

### 1.3 The arm-state is genuinely ON — the degrades are honest fail-closed

`real_order_submit_disabled` is emitted ONLY from the `else` branch at `event_reactor_adapter.py:1791-1806`, reached when the `real_order_submit_enabled` argument **passed into the adapter** is False. That argument is `real_submit_effective` (`main.py:5912`), computed at `main.py:5781`:
```python
real_submit_effective = real_order_submit_enabled if reactor_mode == "live" else False
```
With `reactor_mode == "live"` and `real_order_submit_enabled == True` (config), `real_submit_effective` starts True. It is then **degraded to False mid-cycle**, fail-closed, by three honest guards (single source of truth `_live_lane_degrade_cause`):
- `main.py:5799-5808` — live-bridge allocator did not configure (`allocator_not_configured`).
- `main.py:5828-5833` — `portfolio_state_unavailable` (real submit must never fall back to single-asset Kelly).
- `main.py:5867-5868` — `operator_arm is None` (the capability token could not be minted).

And the adapter is only the **live** adapter when `live_submit_effective and operator_arm is not None` (`main.py:5947`); otherwise the **no-submit** adapter is selected with `degrade_cause` threaded onto every receipt (`main.py:5967`). The `operator_arm` token is minted by `require_operator_arm(edli_cfg)` (`main.py:5859` → `:627-650`) IFF `edli_live_operator_authorized is True` (config: True).

**Verdict on W-S1:** these are not a flag held off — they are the system **correctly refusing to submit** on a cycle where the allocator/portfolio/arm-token is momentarily unavailable, exactly as designed (antibody #54, #48). The live-state tracker (`live_state_tracker.md:5`) shows the dominant recent degrade was the **M5 submit latch** (now self-cleared 06-14T01:06, `diagnosis_confirmation.md:97`) — which is the B1/M5 absorber that the keep-list explicitly preserves (P1 §2). **There is no code gate to remove here; W-S1 is intended arm-state.** The only defect is that the named-degrade telemetry (`NO_SUBMIT_ADAPTER_LANE:<cause>` / `submit_lane`) is not always populated on the persisted receipt, so the operator cannot see *which* degrade fired without reading the log.

---

## 2. W-S2 — THE ONE REAL FIX: MODE-FLIP, TERMINAL ABORT → SINGLE-CRITERION RE-DECISION

### 2.1 The current path (file:line)

The mode is decided ONCE at submit by a **validator**, not a re-selector:
- `event_reactor_adapter.py:4007-4014` — `_fresh_rest_then_cross_mode(...)` re-derives the fresh-book mode from the SAME K4.0 rest-then-cross policy as the proof (`:3754-3822`).
- `event_reactor_adapter.py:4015-4020` — `_validate_final_order_mode_or_abort(proof_mode=..., fresh_mode=...)` (`:3825-3861`):
  - `proof_mode == fresh_mode` → return proof_mode (proceed).
  - `proof_mode != fresh_mode` (EITHER direction) → **raise `_SubmitAbortedModeFlipped`** (`:3856-3860`).
  - `proof_mode` missing/unknown → fail-closed, **raise** (`:3849-3855`).
- A second, legacy-only tripwire at `:4320-4342` raises `_SubmitAbortedModeFlipped` when a proven-MAKER post_only intent's fresh-book EV now favors crossing (only when no rest-then-cross policy is present).
- The raise propagates to `:1814-1831`, which returns a `NO_SUBMIT` receipt with `proof_accepted=True` and reason `SUBMIT_ABORTED_MODE_FLIPPED:...`. The candidate is **discarded**; the next cycle does a full re-rank (`redecision.py:144`, `ReversalReason.MODE_FLIPPED:215-221`).

The hysteresis margin (`mode_consistent_ev.py:358-363`, `taker_over_maker_margin`) already biases toward MAKER on a knife-edge to kill "the 93% MODE_FLIPPED waste" — but the recent receipts prove ~50 flips still occur, because the margin only protects the knife-edge, not a genuine 1–3 tick move between proof-time and submit-time on a thin cheap book.

### 2.2 Why this is a defect under Thrust 5 (and why it was RIGHT when written)

This abort was a **deliberate operator decision** (task #7 completed; "P0 mode-authority, operator review 2026-06-10"). Its rationale (`:3739-3746`): the proof's mode was PROVEN through submit recapture under that mode's economics (MAKER rests with zero taker fee + skips PRICE_MOVED; TAKER pays full fee under the bounded ceiling). Submitting under a *different* mode than was proven means submitting under **unproven economics** — so it fails closed.

That rationale is sound, and W-S2 must **not** discard it. The defect is narrower: **the abort treats a mode-flip as terminal when it should be a re-decision under the SAME honest admission criterion**. If, on the fresh book, the *other* mode independently re-clears `capital_efficiency` (`q_lcb > price` after that mode's own cost), then submitting in the fresh mode is NOT "unproven economics" — it is the same honest inequality re-evaluated on fresh inputs, which is exactly what K=1 (`#39`, "final fresh snapshot = the ONLY money-decision authority") already mandates for price. Thrust 5's insight: **the mode should obey the same K=1 freshness law as the price** — re-price the chosen mode against the fresh book, and if it still clears the K-spine, submit; abort only if it fails.

### 2.3 The change (before → after), with weighed alternatives

**Current (`event_reactor_adapter.py:4015-4020`):**
```python
order_mode = _validate_final_order_mode_or_abort(
    proof_mode=str(actionable.payload.get("proof_execution_mode_intent") or "") or None,
    fresh_mode=_fresh_mode,
    fresh_best_bid=fresh_best_bid,
    fresh_best_ask=fresh_best_ask,
)
```

#### Alternative A — **Re-admit the fresh mode under `capital_efficiency` (PICK).**
Replace `_validate_final_order_mode_or_abort` with a `_resolve_final_order_mode_or_abort` that, on `proof_mode != fresh_mode`, **re-evaluates the fresh mode's own EV against the SAME admission criterion** (`mode_consistent_ev.select_mode_consistent_ev` already returns `chosen_ev` per mode; `:334-389`). Decision rule:
- `proof_mode == fresh_mode` → proceed in proof_mode (unchanged).
- `proof_mode != fresh_mode` → compute the fresh mode's `chosen_ev` on the fresh book; if it is **admissible** (`q_lcb - cost_in_that_mode > 0`, the K-spine inequality, identical to `capital_efficiency`) AND `chosen_ev > 0`, submit in the **fresh mode** (the proof's edge survives in the executable mode). Else abort `SUBMIT_ABORTED_MODE_FLIPPED` (genuinely no executable edge).
- `proof_mode` missing/unknown → fail-closed abort (unchanged — never default a taker submit).

This makes mode-flip a **re-decision under the single admission authority** (`capital_efficiency`), exactly as Thrust 5 specifies, and exactly mirrors how K=1 (`#39`) already treats the price as re-decidable against the fresh book. It collapses the validator's separate "mode-equality" vocabulary into the one honest gate (a SIMPLIFY, net gate count −1).
- **Pro:** stops discarding a candidate whose edge genuinely survives in the executable mode; single criterion; no new gate/flag; directly implements Thrust 5.
- **Con:** the fresh mode was not "proven through submit recapture" in the original sense. Mitigation: re-evaluating `q_lcb - cost` on the **fresh JIT book** (the pre-submit witness, `:3976-3977`) under that mode's own cost law IS the proof for that mode — there is no weaker evidence in the fresh-mode case than in the proof-mode case once K=1 makes the fresh book the sole price authority. The recapture's *extra* protection (PRICE_MOVED ceiling for taker, rest-feasibility for maker) is preserved by keeping the downstream maker-book-agreement wall (`:4053-4060`) and the taker spread guard (`:4323-4327`) — those still fire on the fresh mode.

#### Alternative B — Widen the hysteresis margin only (`taker_over_maker_margin`) — REJECT.
Tune `TAKER_OVER_MAKER_MARGIN` up so fewer knife-edge ticks flip. Pro: one-line, low-risk. **Con:** it is a **tuning knob on a throttle**, not a fix of the mechanism (operator law 3 — "never add a cap/throttle; default SIMPLIFY"). It would suppress *some* flips by making taker strictly harder, which silently biases the executable mode and re-introduces exactly the artificial-throttle the no-caps memory forbids (`no-caps-no-overengineering-2026-06-12.md`). It also does nothing for the TAKER→MAKER direction. Reject.

#### Alternative C — Submit a MAKER fallback whenever the proof mode is non-executable — REJECT.
On any flip, rest a MAKER order at the admitted limit. Pro: maximizes fill attempts. **Con:** this is the *exact* defect P0 mode-authority killed (`:4293-4299` — "a maker that never cleared TAKER recapture entered the taker submit path", inverted). It submits under a mode whose economics were never checked against the fresh book at all — a blind flip. Reject; it re-opens the wrong-trade class (#19 Paris).

**PICK: Alternative A.** It is the only option that (i) implements Thrust 5 literally (re-decision, not death), (ii) preserves the P0 fail-closed intent (no unproven-economics submit — the fresh mode must independently clear `capital_efficiency` + the maker/taker downstream walls), (iii) is a SIMPLIFY (collapses mode-equality into the one admission gate). The legacy tripwire at `:4320-4342` is folded into the same resolver (a proven-MAKER whose fresh EV favors cross is just the `proof_mode != fresh_mode` case — re-admit the taker mode under `capital_efficiency` instead of a blind abort).

### 2.4 Exact edit specification

1. **New function** `_resolve_final_order_mode_or_abort` in `event_reactor_adapter.py` (replacing `_validate_final_order_mode_or_abort:3825-3861`), signature additionally taking the fresh book + the actionable payload (q_lcb, c_fee_adjusted, tick_size) so it can call `select_mode_consistent_ev` for the fresh mode's `chosen_ev`. Returns the **executable mode** (proof_mode if equal; fresh_mode if the flip re-clears; raise otherwise).
2. **Call site `:4015-4020`** — swap to the new resolver, passing `fresh_best_bid/ask`, `actionable.payload`, `tick_size`. The returned `order_mode` then drives the existing maker-context / maker-book-agreement / size-to-depth path unchanged (`:4035-4060`), so all downstream walls still fire on the resolved mode.
3. **Legacy tripwire `:4320-4342`** — delete the standalone raise; the resolver now owns the proven-MAKER-wants-cross case (re-admit taker under `capital_efficiency`, or abort if it fails). Keep the taker spread guard (`:4323-4327`) as a downstream admissibility check inside the resolver.
4. **Receipt reason** — when the resolver re-admits a fresh mode, stamp the receipt's audit with `MODE_REDECIDED:proof=<x>:fresh=<y>:readmitted_ev=<ev>` (telemetry, not a gate) so the operator sees a re-decision distinct from an abort. When it aborts, keep the existing `SUBMIT_ABORTED_MODE_FLIPPED:...` reason (now meaning "the fresh mode also failed `capital_efficiency`" — a genuine no-edge, not a transient tick).
5. **`redecision.py`** — `SUBMIT_ABORTED_MODE_FLIPPED` lifecycle state (`:144`) and `ReversalReason.MODE_FLIPPED` (`:220`) stay (still the terminal state when the re-decision *fails*); add no new state. Optionally annotate the docstring that the abort now fires only after the fresh-mode re-admission failed.

### 2.5 Self-check — systematically correct, or a 1-order hack?

**Systematically correct.** The fix does not target one stuck order; it changes the *category*: a mode-flip stops being a terminal discard and becomes a re-evaluation under the system's single honest admission criterion (`capital_efficiency`, the K-spine the whole strategy is built to keep). It composes with K=1 (#39, fresh book = sole price authority) — mode now obeys the same freshness law as price. It removes a vocabulary (mode-equality) rather than adding one. It does NOT loosen `capital_efficiency` (the fresh mode must clear the identical inequality). It cannot re-enable a wrong-mode blind submit (downstream maker/taker walls preserved). The honest test of "not a hack": after the fix, a candidate is discarded at submit **iff neither mode clears `q_lcb > price` on the fresh book** — which is the correct, settlement-honest reason to not trade.

---

## 3. W-S3 — LOCKED-OPPORTUNITY PRICE-IMPROVEMENT (covert `0.02` constant; LOW priority)

### 3.1 Current path
`_locked_live_opportunity_no_price_improvement_reason` (`event_reactor_adapter.py:4725-4803`), called at `:4343-4352`, raises `_LiveOpportunityAlreadyLocked` when a condition/token/direction already has a locked SubmitPlan and the new limit does not improve by a hardcoded `improve_delta: float = 0.02` (`:4733`). This is the correct *intent* (do not re-emit an identical will-trade chain for an already-locked opportunity — a de-dup, not a throttle) but carries a **covert constant** identical in spirit to P1 Thrust 5's flat-0.01 finding (`P1_strategy_of_record.md:101`).

### 3.2 The change (weighed)
- **Alt A (PICK):** keep the de-dup (it is honest — prevents duplicate chains on the same locked token), but replace the flat `0.02` with the **tick size** of the specific market (already available as `provisional_final_intent.payload["tick_size"]`). Re-quote is allowed when the new limit improves by ≥1 tick — the smallest economically meaningful improvement, market-specific, not a global constant. Pro: removes the constant, market-correct, tiny diff. Con: requires threading tick_size into the reason function (mechanical).
- **Alt B:** delete the gate entirely. Reject — it is a real de-dup antibody (prevents the 5 observed duplicate-chain emissions); deleting it re-opens duplicate locked-order emission.
- **Alt C:** leave `0.02` as-is. Reject on principle (operator constant-elimination mandate, task #64; version-suffix/constant-elimination memory) — but acceptable to DEFER since count=5 all-time, 0 recent.

**Priority: LOW.** 5 receipts all-time, 0 recent. Schedule AFTER W-S2, or fold into the task #64 constant-elimination program. Listed here for completeness because it is a submit-stage gate, but it is not a binding blocker.

---

## 4. W-S1 — OBSERVABILITY FIX (confirm intended arm-state; make the degrade visible)

W-S1 is **not a code-gate to remove** — it is intended fail-closed arm-state (§1.3). The only defect is that the persisted receipt does not always name *which* degrade fired (recent receipts carry `submit_lane=NULL` and the default reason). This is the SAME observability gap P1 Thrust 1 targets (`P1_strategy_of_record.md:65-67`), at the submit lane.

### 4.1 The change
- **Confirm at runtime** (verification, not edit): on a cycle where `real_submit_effective` degrades to False, the receipt SHOULD carry `reason=NO_SUBMIT_ADAPTER_LANE:<cause>` (`:1269`) and `submit_lane=NO_SUBMIT_ADAPTER` (`:1268`). The recent NULL-lane receipts prove a persist path bypasses `_stamp_no_submit_adapter_lane`. **Trace which `process_pending` write path persists a receipt without routing through the stamp** (the stamp is applied in the adapter return at `:1949-1951` for the live lane and must be applied symmetrically on the no-submit lane via `degrade_cause=_no_submit_degrade_cause`, `main.py:5967`). The fix is to ensure EVERY persisted receipt passes through one of the two lane stamps — make a lane-less persisted receipt structurally impossible (assert `submit_lane is not None` at the persist boundary, fail-closed).
- This is pure telemetry; it changes no decision, gate, or submit semantics (mirrors the antibody #54 invariant). It is **pre-work** so that, once W-S2 lands, the operator can distinguish "lane was dark (degrade)" from "mode-flipped (now re-decided)" from "honest no-edge" on the receipt alone.

### 4.2 Self-check
Not a hack — it closes a blind spot (a persisted receipt that cannot name why it did not submit), exactly the "honest rejection labels" discipline of #33/#54. No behavior change.

---

## 5. RED-ON-REVERT TESTS (each fix must have a test that FAILS if the fix is reverted)

All tests carry the provenance header (Created / Last audited / Authority basis) per the global file-header rule.

### 5.1 W-S2 (the load-bearing test)
- **`tests/test_submit_mode_redecision.py::test_mode_flip_readmits_when_fresh_mode_clears_capital_efficiency`** — construct an admitted candidate proven MAKER; present a fresh book where the policy re-derives TAKER and the taker mode's `q_lcb - taker_all_in > 0`. **Assert the receipt is SUBMITTED (or reaches the executor) in TAKER mode**, NOT `SUBMIT_ABORTED_MODE_FLIPPED`. RED on revert (the old validator aborts).
- **`::test_mode_flip_aborts_when_neither_mode_clears`** — fresh book where the flipped mode's `q_lcb - cost <= 0`. **Assert `SUBMIT_ABORTED_MODE_FLIPPED`** (genuine no-edge; the abort is still correct). Guards against over-loosening.
- **`::test_missing_proof_mode_still_fails_closed`** — proof_mode missing/unknown → abort (never a default taker submit). Preserves the P0 fail-closed invariant.
- **`::test_proven_maker_fresh_ev_favors_cross_readmits_taker_under_capital_efficiency`** — the legacy `:4320-4342` case: proven-MAKER, fresh EV favors cross, taker clears `capital_efficiency` → submit TAKER; if taker fails the spread guard → abort. RED on revert of the tripwire deletion.
- **Downstream-wall preservation:** `::test_readmitted_maker_still_requires_book_agreement` and `::test_readmitted_taker_still_obeys_spread_guard` — assert the maker-book-agreement wall (`:4053-4060`) and taker spread guard still fire on the *resolved* mode.

### 5.2 W-S1
- **`tests/test_no_submit_receipt_lane_stamp.py::test_every_persisted_receipt_names_its_lane`** — every persisted `edli_no_submit_receipts` row has non-null `submit_lane`; a full-pass on the no-submit lane carries `NO_SUBMIT_ADAPTER_LANE:<cause>`, never the bare default. RED on revert of the persist-boundary assert.

### 5.3 W-S3
- **`tests/test_locked_opportunity_tick_improvement.py::test_requote_allowed_at_one_tick_improvement`** and `::test_requote_blocked_below_one_tick` — assert the de-dup uses the market tick, not a flat 0.02. RED on revert.

---

## 6. DEPENDENCY ON OTHER WORKSTREAMS (the sequencing law)

W-SUBMIT is **downstream of, and gated by, the admission fix (P1 Thrusts 3–4 / W-q_lcb).** The hard dependency (P1 §3, `:99`):

> "This is relevant only AFTER Thrusts 3–4 produce a ring-bin candidate (the cheap-tail class that dominated the 454 mode-flip receipts is settlement-dead, so the quantitative unblock is small)."

Concretely:
- **Do NOT ship W-S2 as a standalone "unblock submission" change.** With today's admitted population (settlement-dead cheap-tail, P1 §7; C5 confirmed `diagnosis_confirmation.md:122`), W-S2 would let more *base-rate / dead* candidates reach the venue — which is a FAILURE under operator law 2 ("a fix just to fill one order = FAILURE") and law 4 (base-rate buy_no is not alpha). W-S2's value is **latent**: it ensures that once a *real ring-bin candidate* exists (post-Thrust-3/4), it does not die to a transient tick.
- **Ordering:** W-S1 (observability) may ship anytime (zero behavior change, helps everything). **W-S2 ships AFTER the q_lcb/σ-fit admission fix is at least in shadow** and producing genuine ring-bin admissions, so its first live effect is on a candidate that *should* trade. W-S3 folds into task #64 (constant elimination) whenever convenient.
- **Submit-latch (B1/M5) interaction:** W-SUBMIT does NOT touch the M5/B1 latch — that absorber is on the keep-list (P1 §2, `diagnosis_confirmation.md:97`, #31) and self-heals. W-S1's degrade telemetry will simply *name* the latch as the degrade cause on cycles where it is closed, which is correct and desirable.
- **Grading:** every W-SUBMIT promotion is graded by the same T6 settlement harness (P1 §103-113) — a submitted order only "counts" when it settles and the event-level walk-forward monitor scores it vs-market. A fill is never the success criterion (operator contract).

---

## 7. MIGRATION / DATA STEPS

- **W-S2:** none (pure code path; no schema change). The `SUBMIT_ABORTED_MODE_FLIPPED` lifecycle state and receipt reason are retained, so no historical-receipt migration. New telemetry field `MODE_REDECIDED:*` is additive in the receipt audit JSON (no column).
- **W-S1:** add a persist-boundary assertion that `submit_lane is not None`. Backfill is NOT required (historical NULL-lane receipts are shadow-era and read-only evidence); the assert governs only go-forward writes. If a strict backfill is desired, a one-time read-only audit query can label historical NULLs as `LEGACY_PRE_LANE_STAMP` — optional, low value.
- **W-S3:** none (constant → tick_size is a code change; the function already receives the market identity to look up tick_size).

---

## 8. VERIFICATION GATE (prefer settlement-graded)

- **Immediate (unit/integration):** the RED-on-revert tests in §5 must pass; the full submit-path suite (`tests/` touching `event_reactor_adapter` submit boundary) green.
- **Shadow runtime gate (the real gate):** after W-S2 + the admission fix are both in shadow, the T6 harness (P1 §6) must show that on cycles where a ring-bin candidate is admitted, the **mode-flip discard rate drops to ~0 for candidates that re-clear `capital_efficiency` in the fresh mode**, while the abort rate for candidates that genuinely fail both modes is unchanged (no over-loosening). Measured from `edli_no_submit_receipts` reason distribution before/after.
- **Settlement-graded promotion gate (DONE-adjacent):** W-SUBMIT contributes to the operator-contract DONE bar only through settled fills — the live ring-bin fills it enables must clear **>51% after-cost at n≥30 forward fills, model-Brier < market-Brier**, event-level walk-forward (P1 §5 step 6). W-SUBMIT alone proves nothing; it removes a friction so the *admission* fix's edge can be measured at settlement.

---

## 9. RISK + ROLLBACK

| Risk | Likelihood | Mitigation | Rollback |
|---|---|---|---|
| W-S2 re-admits a mode whose economics weren't truly proven → a wrong-mode fill | LOW | Fresh mode must clear `capital_efficiency` on the fresh JIT book + pass the maker-book-agreement / taker-spread downstream walls (preserved). Tests §5.1 assert both. | Single-function revert of `_resolve_…` → `_validate_…`; receipt reason and lifecycle state unchanged, so no data cleanup. |
| W-S2 ships before admission fix → more dead cheap-tail reaches venue | MEDIUM if mis-sequenced | §6 sequencing law: ship W-S2 only after Thrust 3/4 in shadow. | Revert; or gate W-S2 behind the same shadow-promotion the admission fix uses. |
| W-S1 persist-assert fails-closed on a legitimate path that legitimately has no lane | LOW | The two lane stamps cover every adapter return; the assert surfaces any uncovered path as a loud failure (desired) rather than a silent NULL. | Downgrade the assert to a WARN + counter for one cycle to locate the uncovered path, then re-arm. |
| W-S3 tick lookup unavailable for a market → re-quote de-dup can't compute | LOW | Fail-closed to the prior `0.02` behavior when tick is unknown (never block a re-quote on a missing tick). | Revert constant. |

**No rollback touches live data or the latch.** All three fixes are code-path-local; none alters the q point, `capital_efficiency`, the M5/B1 latch, or any DB schema.

---

## 10. THE OVERALL SELF-CHECK (W-SUBMIT as a whole)

**Is W-SUBMIT systematically correct or a 1-order hack?**

Systematically correct, AND correctly scoped as secondary. The honest accounting:
- The master submit arm is **already on and correct** — W-SUBMIT does not "unblock submission" by flipping a flag (that would be the failure the operator contract names: "A plan ending at 'submission unblocked' is a FAILURE").
- The single real code fix (W-S2) changes a *category* — mode-flip from terminal discard to re-decision under the one honest admission gate — composing with the existing K=1 freshness law, removing a vocabulary, adding none. It is a SIMPLIFY (laws 3/8).
- Its value is **explicitly conditional** on the admission fix (Thrusts 3–4) first producing a real ring-bin candidate; shipped alone it would merely speed dead base-rate trades to the venue, which W-SUBMIT's own sequencing law (§6) forbids.
- The success proof is **settlement**, never a fill (§8). W-SUBMIT removes a friction; it manufactures no edge (law 8 — q_lcb / mode / gates are downstream relay; a wrong-bin belief is not rescued by smoother submission).

**The one-sentence verdict for the operator:** the submit path is **not** held dark by a flag — the arm is on; the only real defect is `SUBMIT_ABORTED_MODE_FLIPPED` terminally discarding an admitted candidate on a transient maker/taker book tick, and the fix is to make mode-flip a re-decision under the same `capital_efficiency` criterion that governs admission, sequenced to land only after the upstream q_lcb fix gives it a real candidate to protect.

*End of P2 W-SUBMIT plan. Read-only planning; no production code or daemon changed. Every empirical claim cited to file:line or query+counts.*
