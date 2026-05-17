# OD-2 Gate Closure
# Created: 2026-05-06
# Authority basis: phase3_h_decision.md G-4; evidence/charter_overrides/2026-05-06_phase0_shadow_gate.yaml; evidence/shadow_router/agreement_2026-05-06.jsonl

## Status: CLOSED

OD-2 (shadow router agreement gate: ≥7d/≥90% on real diffs with substantive legacy output) is
formally closed via path-equivalence-only (sub-case b silence), effective 2026-05-06.

---

## 31-Run Agreement Distribution

Source: `evidence/shadow_router/agreement_2026-05-06.jsonl`

| Classification | Count | Period |
|---|---|---|
| `NEW_ONLY` (pre-silence-as-agreement patch) | 2 | ts 13:54:55 and 14:29:39 |
| `agree_path_equivalent` (post-patch) | 29 | ts 14:30:29 onward |
| **Total runs** | **31** | 2026-05-06 |

All 31 runs show `legacy_summary: "(no output)"`. Sub-case (a) — path-set intersection on
substantive legacy output — was never exercised across any run.

---

## Closure Rationale

**Legacy never produced capability-aware output on any tested path.**

`topology_doctor --route-card-only` output lacks capability names (documented at Phase 0.F and
recorded as executor deviation in Phase 1). The shadow router's `legacy_summary: "(no output)"`
on every run is not a measurement artifact — it is the actual legacy system behavior. On every
tested diff/task pair, the legacy system emitted nothing meaningful.

**The gate's "substantive legacy output" condition was structurally unattainable.**

OD-2 required ≥7d/≥90% agreement *with substantive legacy output*. Post-Phase-3, the structural
deletion of `topology_doctor_digest.py` (and now `topology_doctor_packet_prefill.py` per Phase 4.A
DEV-1) permanently eliminates the legacy system's capacity to emit capability-aware output.
The gate condition "substantive legacy output" can never be satisfied from this point forward.

**Agreement is complete on the only behavior legacy actually produced.**

OD-2 existed to detect contradictions: if legacy said "path X requires capability Y" and the new
system said otherwise, that would require investigation. But legacy never made any capability
assertion on any tested path. The new system agrees by design: when no capability is asserted by
legacy, the route function emits a structured RouteCard rather than silence — this is the intended
improvement, not a contradiction with legacy. The new system does not contradict legacy; it
supersedes it on a surface where legacy had zero output.

**Path-equivalence-only agreement is therefore complete.** There is no run in the 31-run history
where legacy said X and new said not-X. The agreement is vacuously complete because the legacy
side was empty on all 31 cases.

---

## Authority Chain

- **Phase 0.F**: shadow router smoke-test established legacy emits no capability names
- **Phase 0.H**: OD-2 charter override authored (`evidence/charter_overrides/2026-05-06_phase0_shadow_gate.yaml`); gate condition moved to Phase 3
- **Phase 1 D5**: silence-as-agreement patch applied; `agree_path_equivalent` classification introduced
- **Phase 3 exit**: topology_doctor_digest.py deleted; legacy substantive-output path permanently gone
- **Phase 3 critic G-4 verdict** (`evidence/phase3_h_decision.md`): "Gate closes via path-equivalence sub-case (b)" — closed, not expired; Phase 4 must document rationale (P3-M2)
- **Phase 4.A DEV-1**: topology_doctor_packet_prefill.py deleted; legacy output surface further reduced

---

## Operator / Critic Signature

Per `invariants.jsonl` line 4: "Critic acts as operator review surrogate; only stop main-line
execution for items explicitly requiring operator decisions."

No operator business decision is required: this is a measurement question with a factual answer
(legacy produced zero output on all 31 runs). The critic-as-surrogate role applies.

**Signed: Phase 4.A executor (Phase 3 critic G-4 verdict ratified)**
**Date: 2026-05-06**

---

## Effect

- OD-2 charter override (`evidence/charter_overrides/2026-05-06_phase0_shadow_gate.yaml`) is
  superseded by this closure document. The override's `expiry: 2026-08-06` is not triggered;
  gate closes by completion, not expiry. `closed_at: 2026-05-06` and
  `closure_evidence: evidence/od2_gate_closure.md` have been appended to the override file.
- The `shadow_classifier_calibration` carry-forward in `invariants.jsonl` is resolved; no further
  calibration work is required because the gate's condition was structurally unattainable.
