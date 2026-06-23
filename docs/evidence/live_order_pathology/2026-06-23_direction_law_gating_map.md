# Direction-Law Gating Map — Live Candidate Admission
# Created: 2026-06-23
# Authority basis: direct code read of ERA, family_decision_engine, direction_law.py, qkernel_spine_bridge

---

## Q1 — Order of LAW A and LAW B in the candidate admission pipeline

### LAW A (σ-band) — applied FIRST, at proof-generation time

`src/engine/event_reactor_adapter.py:10038-10049` — inside `_build_candidate_proofs()`:

```python
direction_law_reason = _direction_law_reason_for_candidate(
    candidate=candidate,
    direction=direction,
    mu=direction_law_mu,
    predictive_sigma=direction_law_sigma,
    mu_settled=direction_law_mu_settled,
    settle_value=direction_law_settle_value,
)
if direction_law_reason is not None:
    score = 0.0
    if missing_reason is None:
        missing_reason = direction_law_reason
```

Inputs resolved once per family at lines 9840–9861. The call dispatches to
`src/strategy/live_inference/direction_law.py::direction_law_rejection_reason`
via `src/engine/event_reactor_adapter.py:10564`.

The rejection reason is stamped onto the proof's `missing_reason` and `score=0.0`
**before** any selection ranking occurs.

### LAW B (q-floor) — applied SECOND, inside the qkernel-spine FamilyDecisionEngine

`src/decision/family_decision_engine.py:720-724` — inside `FamilyDecisionEngine.decide()`:

```python
direction_law_ok=direction_law_ok(
    d.route,
    forecast_bin=forecast_bin,
    point_q=float(joint_q.q_by_bin_id.get(d.route.bin_id, 0.0)),
),
```

Stamped on every `CandidateDecision`; enforcement happens in `_select()` at lines
1160–1170 (filter: `_direction_admitted`).

**Order: LAW A runs first (proof generation, ERA ~line 10038), LAW B runs second
(spine candidate scoring, FDE ~line 720).**

---

## Q2 — Is each law ENFORCED (hard drop) or only LOGGED?

### LAW A: ENFORCED on the legacy path; PARTIALLY bypassed on the spine path

On the **legacy** path (`honor_admission_rejections=True`, default):
`src/engine/event_reactor_adapter.py:9267-9276` — `_selection_scoped_proofs` strips
any proof where `missing_reason is not None`. A proof with a
`DIRECTION_LAW_BIN_FORECAST_MISMATCH` reason is silently excluded from the ΔU
ranking set and cannot be selected.

On the **spine** path (`honor_admission_rejections=False`, line 3194):
`src/engine/event_reactor_adapter.py:9278-9286` — proofs rejected by LAW A are
**re-admitted** to the spine if their `missing_reason` starts with
`"DIRECTION_LAW_BIN_FORECAST_MISMATCH"`. This is intentional: the spine's own
`direction_law_ok` (LAW B) replaces LAW A's scalar rejection for forecast families.

```python
def _qkernel_may_rescore_rejected_proof(missing_reason: str | None) -> bool:
    text = str(missing_reason or "").strip()
    if not text:
        return True
    return text.startswith((
        "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV",
        "ADMISSION_CAPITAL_EFFICIENCY",
        "DIRECTION_LAW_BIN_FORECAST_MISMATCH",  # <-- LAW A rejections re-admitted for spine
    ))
```
`src/engine/event_reactor_adapter.py:9245-9265`

### LAW B: ENFORCED as a hard structural filter inside _select()

`src/decision/family_decision_engine.py:1160-1170`:

```python
def _direction_admitted(d):
    return d.direction_law_ok or (
        d.economics.edge_lcb > 0.0
        and d.economics.optimal_delta_u > 0.0
        and d.q_lcb_guard_basis == "OOF_WILSON_95"
        and not d.q_lcb_guard_abstained
    )

after_direction = [d for d in after_executable if _direction_admitted(d)]
if not after_direction:
    return None, NO_TRADE_NO_DIRECTION_LAW
```

When `after_direction` is empty the engine returns `selected=None` (no-trade).
The bridge returns `SpineDecisionResult(selected_proof=None, ...)` and the reactor
emits a no-trade receipt. This is a hard structural drop.

**The OOF_WILSON_95 empirical-license override** (`_direction_admitted`) can admit a
candidate with `direction_law_ok=False` if the q_lcb reliability guard cell is active
and non-abstained. This is the only bypass.

---

## Q3 — Does LAW A actually block a far buy_yes, or is its rejection only recorded while LAW B binds?

**On the qkernel-spine path (live for forecast families): LAW A rejection is BYPASSED;
LAW B is the binding gate.**

The sequence:
1. `_build_candidate_proofs()` stamps `missing_reason = "DIRECTION_LAW_BIN_FORECAST_MISMATCH"` on the far buy_yes proof (LAW A runs, score=0.0, passed_prefilter=False).
2. `_selection_scoped_proofs(..., honor_admission_rejections=False)` at line 3189–3194 re-admits that proof because its reason starts with `"DIRECTION_LAW_BIN_FORECAST_MISMATCH"`.
3. `decide_family_via_spine` is called with that proof present in `_spine_entry_proofs`.
4. Inside `FamilyDecisionEngine.decide()`, `direction_law_ok(route, forecast_bin, point_q)` is computed. For buy_yes on a non-modal bin, this is `True` when `point_q >= 0.05` (CALIBRATED_NONMODAL_Q_FLOOR). For far-tail (q < 0.05) it is `False`.
5. In `_select()`, `_direction_admitted(d)` admits the candidate if `direction_law_ok=True` (i.e., q ≥ 0.05) OR if `OOF_WILSON_95` basis is active.
6. A far buy_yes with q < 0.05 and no OOF license is dropped at step 5 (NO_TRADE_NO_DIRECTION_LAW).
7. A buy_yes with q ≥ 0.05 (non-modal but calibrated domain) IS admitted by LAW B.

**Conclusion: LAW A σ-band is NOT the binding gate on the live (spine) path. LAW B
(q-floor ≥ 0.05) is the binding gate. A buy_yes far from μ* but with point_q ≥ 0.05
is admitted by the spine even though LAW A would reject it. Both laws must agree for a
candidate to be blocked on the spine path.**

---

## Q4 — Where does qkernel_spine_bridge call direction_law_ok? Which path produces the live ActionableTradeCertificate?

`qkernel_spine_bridge.py` does NOT call `direction_law_ok` directly. It calls
`FamilyDecisionEngine.decide()` at line ~1000 (via `decide_family_via_spine` at
`src/engine/qkernel_spine_bridge.py:903–1052`), and it is FDE that calls
`direction_law_ok` at line 720.

The bridge maps `FamilyDecision.selected` back to a reactor `_CandidateProof` via
`_overlay_spine_economics_onto_proof` (line 1328), setting
`selection_authority_applied="qkernel_spine"` at line 1393. This stamped proof is
returned as `SpineDecisionResult.selected_proof`.

The reactor at line 3213 reads `proof = _spine_result.selected_proof`. Downstream cert
construction (claim type `ActionableTradeCertificate`, defined in
`src/decision_kernel/claims.py:40`) is built from this proof.

**The spine path (`qkernel_spine_enabled=True`, `_FORECAST_DECISION_EVENT_TYPES`)
produces the live `ActionableTradeCertificate` for forecast families.** The legacy path
at line 3216 only fires for day0 events or when the spine flag is off.

The selection authority is confirmed at `src/engine/event_reactor_adapter.py:8834`:

```python
if str(getattr(proof, "selection_authority_applied", "") or "") == "qkernel_spine":
    return True
```

---

## Q5 — Precise seam to make buy_yes admissible ONLY on the forecast/modal bin

Two seams must both change.

### Seam 1 (LAW B — the binding live gate)
`src/decision/family_decision_engine.py:499-503`:

```python
if route.side == "YES":
    return (
        route.bin_id == forecast_bin
        or point_q >= CALIBRATED_NONMODAL_Q_FLOOR  # <-- REMOVE THIS CLAUSE
    )
```

To enforce modal-only YES, change to:
```python
if route.side == "YES":
    return route.bin_id == forecast_bin
```

This is the binding structural gate on the spine path. All non-modal YES regardless of
q would be dropped at `_select()` step (NO_TRADE_NO_DIRECTION_LAW) unless OOF_WILSON_95
is active (see below).

### Seam 2 (OOF empirical override in _select — must also close for full modal-only)
`src/decision/family_decision_engine.py:1160-1166`:

```python
def _direction_admitted(d):
    return d.direction_law_ok or (          # <-- the OOF bypass
        d.economics.edge_lcb > 0.0
        and d.economics.optimal_delta_u > 0.0
        and d.q_lcb_guard_basis == "OOF_WILSON_95"
        and not d.q_lcb_guard_abstained
    )
```

If modal-only YES is the target, the OOF bypass should be constrained to NOT admit
non-modal YES (it was designed for NO-on-modal harvesting). To make YES strictly
modal-only, add a side check:
```python
def _direction_admitted(d):
    return d.direction_law_ok or (
        d.route.side == "NO"   # <-- restrict OOF bypass to NO direction only
        and d.economics.edge_lcb > 0.0
        and d.economics.optimal_delta_u > 0.0
        and d.q_lcb_guard_basis == "OOF_WILSON_95"
        and not d.q_lcb_guard_abstained
    )
```

LAW A (`direction_law.py::direction_law_rejection_reason`) is NOT on the live
binding path for the spine (it is bypassed by `honor_admission_rejections=False`),
so changing it alone does nothing for live decisions. Changing Seam 1 (and optionally
Seam 2) is sufficient.

---

## Q6 — Tests pinning the current non-modal-YES behavior

These must be updated if modal-only YES is enforced:

### `tests/decision/test_family_decision_engine.py`

- **Line 489–499**: `test_direction_law_ok_basic_modal_and_nonmodal_yes`
  - Line 493: asserts `direction_law_ok(yes_b27, ..., point_q=CALIBRATED_NONMODAL_Q_FLOOR-0.01) is False`
  - Line 497: asserts `direction_law_ok(yes_b27, ..., point_q=CALIBRATED_NONMODAL_Q_FLOOR) is True`
  - The `is True` assertion at line 497 becomes False under modal-only.

- **Line 502–540**: `test_nonmodal_yes_in_calibrated_domain_is_admitted_far_tail_still_modal_only`
  - Lines 529-533: asserts non-modal YES at q=0.22, q=0.10, q=FLOOR are `True` — all become False.
  - Lines 537-538: far-tail assertions (`is False`) survive.
  - Line 540: modal bin YES with q=0.0 (`is True`) survives.

- **Line 725–732**: test asserting `yes_b24[0].direction_law_ok is True` when `q >= CALIBRATED_NONMODAL_Q_FLOOR` — fails under modal-only.

- **Lines 1182–1207**: `test_oof_wilson_95_license_bypasses_direction_law_for_yes_nonmodal`
  - Lines 1202–1207: asserts `licensed_yes_on_non_modal_cand` is selected with `direction_law_ok=False` — fails if the OOF bypass is also constrained (Seam 2 change).

### `tests/strategy/live_inference/test_direction_law.py`

- No test pins non-modal-YES admission (LAW A only rejects far-tail YES on distance, which modal-only would also reject — these tests survive).

---

## Summary

| Item | Answer |
|---|---|
| Law order | LAW A (σ-band) stamps proof at ERA:10038; LAW B (q-floor) gates inside FDE._select() later |
| LAW A enforcement on spine path | BYPASSED — LAW A rejections re-admitted to spine at ERA:9263 |
| LAW B enforcement | HARD DROP — FDE._select() returns NO_TRADE_NO_DIRECTION_LAW when after_direction is empty |
| Binding gate for live YES | LAW B: `route.bin_id == forecast_bin OR point_q >= 0.05` at FDE:499-503 |
| OOF bypass | `_direction_admitted` at FDE:1160 can admit direction_law_ok=False candidates with OOF_WILSON_95 |
| Live cert path | qkernel-spine (selection_authority_applied="qkernel_spine") for all forecast families |
| Seam for modal-only YES | Primary: `FDE:499-503` remove `or point_q >= CALIBRATED_NONMODAL_Q_FLOOR`; Secondary: `FDE:1160-1166` add `d.route.side == "NO"` guard on OOF bypass |
| Tests to update | `test_family_decision_engine.py` lines 497, 529-533, 725-732, 1202-1207 |
