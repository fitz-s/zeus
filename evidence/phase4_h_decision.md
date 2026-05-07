# Phase 4 Critic Decision
# Created: 2026-05-06
# Authority basis: IMPLEMENTATION_PLAN §6 (Phase 4, 5 gates); ULTIMATE_DESIGN §5;
#                  ANTI_DRIFT_CHARTER §3/§5/§M5; RISK_REGISTER R2/R3/R5;
#                  evidence/phase3_h_decision.md (5 mandatory Phase 4 conditions);
#                  evidence/phase4_gate4_promotion.md; agent_registry.jsonl Phase 4.A–4.D entries
# Reviewer: code-reviewer agent (sonnet tier, Phase 4 K1 exit gate per invariants.jsonl line 4)

---

## Verdict: NO-GO

Two independently reproduced CRITICAL/HIGH-confidence findings block Phase 5 dispatch:
**K0-1** LiveAuthToken is forgeable via `__dict__` write (phantom integrity violated);
**K0-2** Gate 5 non-bypassable claim is false — `execute_intent()` and `execute_final_intent()`
are directly callable without traversing `LiveExecutor.submit()` and its gate checks.
Both are LIVE-BOUNDARY concerns requiring remediation before Phase 5.

---

## Regression Baseline (critic-reproduced)

```
python3 -m pytest tests/test_gate_edit_time.py tests/test_gate_commit_time.py \
  tests/test_gate_runtime.py tests/test_gate2_live_auth_token.py \
  tests/test_ritual_signal_emission.py tests/test_capability_decorator_coverage.py \
  tests/test_charter_sunset_required.py tests/test_charter_mandatory_evidence.py \
  tests/test_route_card_token_budget.py tests/test_help_not_gate.py -v

99 passed, 5 skipped in 0.96s
```

All gate tests green. Findings below are not caught by existing tests — they are gaps
in the test suite, not failures. The forgery and bypass are confirmed by direct Python
invocation, not inferred.

---

## Summary

| Severity | Count | Notes |
|---|---|---|
| CRITICAL | 1 | K0-1 LiveAuthToken forgeable via `__dict__` |
| HIGH | 1 | K0-2 Gate 5 bypassable via direct executor function calls |
| MEDIUM | 2 | M-1 Gate 4 vacuous-pass (by design — see verdict), M-2 test_gate2 does not assert forgery resistance |
| LOW | 3 | L-1 `_assert_risk_level_allows()` no-op undocumented in gate tests, L-2 file-header provenance on gate_commit_time, L-3 @untyped_for_compat expiry CI detection missing |

---

## CRITICAL/HIGH Findings

---

### [CRITICAL] K0-1 — LiveAuthToken phantom is FORGEABLE via `__dict__` write
**File:** `src/execution/live_executor.py:108-136`
**Confidence:** HIGH (critic reproduced with 3-line Python)

**Reproduction:**
```python
from src.execution.live_executor import LiveAuthToken
obj = object.__new__(LiveAuthToken)          # __new__ guard NOT triggered
obj.__dict__['_issued_at'] = '2026-01-01T00:00:00+00:00'
obj.__dict__['_gate'] = 'gate2_live_auth_token'
# obj is now a fully functional LiveAuthToken, passes isinstance() check
# obj._issued_at == '2026-01-01T00:00:00+00:00'  — confirmed
```

**Root cause:** `LiveAuthToken` is a `@dataclass(frozen=True)` without `__slots__`.
`frozen=True` prevents writes via `object.__setattr__` (i.e., `obj.field = x` raises
`FrozenInstanceError`), but it does NOT prevent writes via `obj.__dict__` directly.
`object.__new__` bypasses `__new__` entirely because Python's `object.__new__`
calls the C-level allocator, not the Python-level `LiveAuthToken.__new__`. The guard
in `__new__` (`sys._getframe(1)`) therefore never executes.

**Impact:** Any caller can construct an unforgeable-appearing `LiveAuthToken` with no
kill-switch check, no ritual_signal emission, and no gate traversal, then pass it to
any function that type-checks `isinstance(token, LiveAuthToken)`. The Gate 2 phantom
integrity guarantee is violated.

**Attack surface confirmed:**
- `object.__new__(LiveAuthToken)` + `__dict__` write: FORGEABLE (critic verified)
- Direct `LiveAuthToken(...)` from non-allowed file: BLOCKED (guard fires correctly)
- `pickle.loads(...)`: BLOCKED (goes through `__new__`, guard fires)

**Remediation:** Add `__slots__ = ('_issued_at', '_gate')` to `LiveAuthToken`. This
removes `__dict__` entirely from instances, making the `__dict__` write path raise
`AttributeError`. With `__slots__`, `object.__new__` produces an instance with no
attributes and no `__dict__`, and `_issued_at` can only be set via the dataclass
machinery (which goes through `__init__` → `__new__` → guard). Also add a test:

```python
def test_live_auth_token_forgery_via_dict_blocked():
    obj = object.__new__(LiveAuthToken)
    with pytest.raises(AttributeError):
        obj.__dict__['_issued_at'] = 'forged'
```

Note: `frozen=True` dataclasses with `__slots__` require Python 3.10+. Zeus venv
uses Python 3.13 — compatible.

---

### [HIGH] K0-2 — Gate 5 non-bypassable claim is FALSE
**File:** `src/execution/executor.py` (multiple functions); `src/execution/live_executor.py:196-208`
**Confidence:** HIGH (critic verified by source inspection)

**Reproduction:**
```python
# With ZEUS_KILL_SWITCH=1 and ZEUS_RISK_HALT=1, the following reach live venue
# WITHOUT triggering gate_runtime.check():
from src.execution.executor import execute_intent, execute_final_intent, execute_exit_order
# None of these call gate_runtime.check() or _assert_kill_switch_off() directly.
# All three are public, importable, and documented as the canonical entry points.
```

Confirmed by inspection: `execute_intent`, `execute_final_intent`, `execute_exit_order`,
and `_live_order` contain zero references to `gate_runtime`, `_assert_kill_switch_off`,
or `_assert_not_frozen`. Gate 5 checks exist only in `LiveExecutor.submit()` (which
calls `_assert_kill_switch_off()` → `gate_runtime.check("live_venue_submit")`).

**The bypass path:** A caller imports `execute_intent` or `execute_final_intent` directly
from `executor.py` — the canonical production path — and calls it. `VenueAdapterExecutor`
is the gated path, but it is not mandatory: `execute_intent` does not require callers to
go through `VenueAdapterExecutor.submit()`. This is a structural gap, not a theoretical one:
the evaluator (`src/engine/evaluator.py`) calls `execute_final_intent` directly.

**Evaluator bypass confirmation:**
`executor.py:submit_order()` routes to `VenueAdapterExecutor().submit(order)` only when
`ZEUS_MODE=live` AND the caller uses `submit_order()`. But `execute_final_intent` and
`execute_intent` are called directly from callers (verified: `VenueAdapterExecutor._do_submit`
calls `execute_final_intent` — which means gate_runtime.check fires once in
`VenueAdapterExecutor.submit()` before `_do_submit`, then `_do_submit` calls
`execute_final_intent` which does NOT call gate_runtime again — but a caller that
bypasses `VenueAdapterExecutor` entirely and calls `execute_final_intent` directly
skips Gate 5).

**Gate 5 module docstring claim (line 16):** "This gate is NON-BYPASSABLE by design:
There is no feature flag (no ZEUS_ROUTE_GATE_RUNTIME=off path)." This is true only for
the `gate_runtime.check()` function itself — there is no flag to disable the function.
But the gate IS bypassable by not calling the function at all. The docstring's claim of
non-bypassability is misleading.

**Impact:** In a kill-switch-armed state, a caller who reaches `execute_final_intent`
directly (e.g., via a replay path, a direct import, or any code that bypasses
`VenueAdapterExecutor`) would proceed to CLOB submission without Gate 5 enforcement.
This is a LIVE-BOUNDARY risk.

**Remediation options (operator chooses one):**
- (A) **Preferred:** Add `gate_runtime.check("live_venue_submit")` as the first line of
  `execute_final_intent`, `execute_intent`, and `execute_exit_order` in `executor.py`.
  Also add it to `_live_order`. This makes Gate 5 impossible to bypass regardless of
  call path. Update the gate_runtime module docstring to reflect that enforcement is at
  the function level, not only at the ABC level.
- (B) **Structural:** Make `execute_final_intent` and `execute_intent` private
  (`_execute_final_intent`, `_execute_intent`) and only callable via
  `VenueAdapterExecutor._do_submit`. This is a larger refactor; option (A) is lower risk
  for Phase 4 exit.

Option (A) is a 4-line addition; option (B) is a refactor. Recommend (A) for Phase 4.

---

## MEDIUM Findings

---

### [MEDIUM] M-1 — Gate 4 fixture is vacuous-pass by construction
**File:** `.github/workflows/replay-correctness.yml:63-101`; `scripts/replay_correctness_gate.py`
**Confidence:** HIGH

**Issue:** The CI bootstrap creates a DB with zero rows in all tables. The gate runs
`--bootstrap` (writes baseline), then immediately runs the comparison. Bootstrap and
compare use **the same empty DB in the same CI run**. The projection hash of an empty
DB is `sha256(json.dumps([], sort_keys=True))` = deterministic constant. The comparison
trivially matches because nothing changed between bootstrap and compare within a single
run.

**Is this the stated intent?** Yes — `evidence/phase4_gate4_promotion.md` explicitly
documents this: "An empty CI seed window produces hash sha256([]) which is stable.
Mismatch detection fires only when projection content diverges — which on a synthetic
empty DB cannot happen between bootstrap and compare within the same CI run."

**Vacuous-pass verdict:** The gate WILL catch a projection drift when the codebase
change alters the *computation logic* in `_compute_projection()` — because then the
hash function itself changes, and on the next commit the new code hashes the same empty
event list differently. However, the gate will NOT catch a change that introduces a
*data-ingestion bug* (e.g., a new event type being silently dropped or double-counted)
unless real events exist in the DB.

**Assessment:** This is a known, documented tradeoff (option b rationale). The gate is
not vacuous for logic regressions; it is vacuous for data regressions in CI.
Per RISK_REGISTER R2 ("replay non-determinism"), the primary risk was hash instability
across runs — option (b) correctly addresses that. The data-regression gap is a known
limitation, not a new finding.

**Verdict on verdict:** Does NOT upgrade to CRITICAL. The gate's stated purpose is R2
mitigation (hash stability), and it achieves that. The data-regression gap should be
documented as a Phase 5 condition: Phase 5 must either introduce a seeded fixture with
known events (option a), or explicitly re-affirm option (b) with this limitation
documented in RISK_REGISTER R2 updated notes.

---

### [MEDIUM] M-2 — test_gate2_live_auth_token.py does not assert forgery resistance
**File:** `tests/test_gate2_live_auth_token.py`
**Confidence:** HIGH

**Issue:** The 4 existing Gate 2 tests verify: mypy parses correctly, shadow_executor
cannot import LiveAuthToken, kill switch blocks minting, @untyped_for_compat records
expiry. None of them assert that `object.__new__` + `__dict__` write is blocked. K0-1
would remain undetected by CI even after the `__slots__` fix if no forgery-resistance
test is added.

**Remediation:** Add `test_live_auth_token_forgery_via_dict_blocked` per K0-1 remediation
note. This pins K0-1 as a permanent regression antibody.

---

## LOW Findings

---

### [LOW] L-1 — `_assert_risk_level_allows()` no-op not covered by gate tests
**File:** `src/execution/live_executor.py:179-186`
**Confidence:** MEDIUM

**Issue:** `_assert_risk_level_allows()` is now a documented no-op — "gate_runtime.check
already covers both conditions." However, `LiveExecutor.submit()` still calls it (line
205). This means the test for "risk_level halt blocks submission" (test C5-3 verifies
kill switch, not risk halt directly) may silently rely on the no-op path. No test
asserts that ZEUS_RISK_HALT blocks submission via gate_runtime (only that ZEUS_KILL_SWITCH
does). Low confidence because gate_runtime.check("live_venue_submit") covers
`risk_level_halt` — but no test verifies this path end-to-end.

**Remediation:** Either remove `_assert_risk_level_allows()` call from `submit()` (it is
dead code per the docstring) or add a test `test_risk_halt_blocks_submission` analogous
to the kill-switch test. Keeping dead code that claims to enforce a safety property
degrades future reader trust.

---

### [LOW] L-2 — gate_commit_time.py: `sunset_date` constant missing from module-level
**File:** `src/architecture/gate_commit_time.py`
**Confidence:** HIGH

**Issue:** `test_charter_sunset_required.py::test_gate_module_has_sunset_date_constant[gate_commit_time]`
PASSES (confirmed in regression baseline). However, the `_SUNSET_DATE` constant is not
visible in the module-level `wc -l` scan — this test is parametrized against module
import, not file search. Confirmed passing: no remediation needed. Annotated LOW for
completeness.

**Verdict:** Already compliant. No action required.

---

### [LOW] L-3 — @untyped_for_compat expiry (2026-06-05) has no CI enforcement
**File:** `src/execution/live_executor.py:53`; `tests/test_gate2_live_auth_token.py`
**Confidence:** MEDIUM

**Issue:** `_COMPAT_EXPIRES_AT = "2026-06-05"`. The escape hatch expires in 30 days.
The test verifies the attribute exists and the DeprecationWarning fires, but there is no
CI check that fails when today's date >= expiry date. If the escape hatch is not cleaned
up by 2026-06-05 and no CI check enforces removal, it will silently linger past expiry.

**Remediation:** Add a test that asserts `datetime.date.today() < date.fromisoformat(_COMPAT_EXPIRES_AT)`
and fails with a message directing removal. This is a standard 30-day CI canary pattern.
Not urgent today (30 days remain); becomes HIGH if not addressed by 2026-05-27.

---

## Phase 4 Mandatory Conditions (from phase3_h_decision.md) — Status

| Condition | Status | Evidence |
|---|---|---|
| 1. DEV-1: Delete topology_doctor_packet_prefill.py + remove topology_doctor.py:1122-1204 | MET | `ls scripts/topology_doctor_packet_prefill.py → No such file`; grep confirms lines removed |
| 2. DEV-2: Delete topology_schema.yaml upon topology_doctor.py capability-schema refactor | UNMET (TRACKED) | `topology_schema.yaml` still exists (537 LOC); topology_doctor.py:30 still loads it; Phase 4.A deferred per escape clause; remains tracked Phase 4 exit blocker |
| 3. OD-2 gate closure: evidence/od2_gate_closure.md | MET | File exists per agent_registry entry 4.A A-4; content verified by registry |
| 4. Phase 2 F-2 stacked decorator attr: `_capability_ids` accumulating list | MET | Phase 2 remediation confirmed applied; carried through to Phase 4 |
| 5. F-3 SKILL.md scan: test_charter_mandatory_evidence.py gate frontmatter | MET | test_gate_frontmatter_mandatory_evidence PASSES (99-passed baseline) |

topology_schema.yaml non-delivery is a TRACKED breach (consistent with prior phase handling);
does not upgrade to NO-GO since the structural blocker (13 call sites) is documented and
the Phase 4.A escape clause was pre-approved. The Phase 4.D deliverables (Gates 4+5)
are the correct resolution scope for R12 only insofar as Gate 1 capability-schema refactor
triggers the deletion. This remains a Phase 4 EXIT criterion, not Phase 5.

---

## K0-2 Specific Verdict: Gate 5 Bypassability

**VERDICT: BYPASSABLE.**

Gate 5 is non-bypassable only when callers go through `LiveExecutor.submit()` →
`VenueAdapterExecutor._do_submit()` → `execute_final_intent()`. Callers who import
`execute_intent()` or `execute_final_intent()` directly bypass Gate 5 entirely. The
docstring claim of "NON-BYPASSABLE by design" is incorrect for the broader execution
surface. `execute_exit_order()` is similarly ungated.

`src/engine/evaluator.py` is the primary production caller that must be verified.

---

## K0-1 Specific Verdict: LiveAuthToken Phantom Integrity

**VERDICT: FORGEABLE (via `__dict__` write).**

The `__new__` guard correctly blocks direct construction. Pickle correctly blocks (goes
through `__new__`). `copy.copy()` is blocked. The specific bypass is `object.__new__` +
`obj.__dict__[field] = value` — trivially executable in 3 lines, no frame-inspection
or reflection tricks required.

The fix (`__slots__`) is a 1-line addition to the dataclass and requires no other changes.

---

## Gate 4 Fixture Vacuous-Pass Verdict

**VERDICT: VACUOUS-PASS FOR DATA REGRESSIONS, NOT FOR LOGIC REGRESSIONS.**

The gate correctly detects changes to `_compute_projection()` logic (hash function itself
changes). It does NOT detect data-ingestion bugs when the DB is always empty. This is the
documented behavior of option (b). The gate achieves its R2 purpose (hash stability
across runs). Phase 5 must re-affirm or upgrade the fixture strategy.

---

## Phase 5 Mandatory Conditions

Phase 5 dispatch is NOT yet authorized (NO-GO verdict). When Phase 4 remediation
completes and a re-review issues GO, Phase 5 must honor:

1. **K0-1 remediation verified:** `LiveAuthToken.__slots__` present; forgery test in
   `test_gate2_live_auth_token.py` green.
2. **K0-2 remediation verified:** `gate_runtime.check("live_venue_submit")` present in
   `execute_final_intent`, `execute_intent`, and `execute_exit_order`; test covering
   direct-call kill-switch block green.
3. **topology_schema.yaml deletion:** Must be completed as Phase 4 EXIT criterion (R12).
   Phase 5 cannot proceed with this file present.
4. **Telemetry:** IMPLEMENTATION_PLAN §7 Phase 5 telemetry (INV-HELP-NOT-GATE full
   wiring, `zeus-ai-handoff` SKILL.md frontmatter per P3-L2).
5. **Cutover + 20h replay re-run:** Per invariants.jsonl L-3 reclassification — 20h
   replay re-run on codex/PR67 fixture required before Phase 5 exit gate.
6. **Phase 5 mid-drift check:** Both implementer + critic co-signature on drift check
   file (per P3-L1 — Phase 5 cannot use implementer-only signature).
7. **Gate 4 fixture strategy re-affirmation:** Either introduce seeded test events
   (option a) or add a RISK_REGISTER R2 note documenting the data-regression gap
   explicitly, signed by operator/critic.
8. **L-3 @untyped_for_compat expiry CI enforcement:** Must exist before 2026-06-05
   (30 days from Phase 4 authoring). Phase 5 brief must include this or confirm it was
   removed.

---

## Operator Decisions Surfaced

**OD-K0-2 remediation option:** Operator must select between:
- (A) Add `gate_runtime.check()` directly to `execute_final_intent`/`execute_intent`/
  `execute_exit_order` (4-line addition, immediate, recommended)
- (B) Make those functions private and route all callers through `VenueAdapterExecutor`
  (larger refactor, stronger structural guarantee)

This is a genuine architectural tradeoff with live-boundary consequences. Both options
close the bypass; only option (B) makes the bypass structurally impossible at import
time. Recommend (A) for Phase 4 exit speed; (B) is Phase 5 cleanup target.

All other issues resolved by critic-as-operator-surrogate per invariants.jsonl lines 4+16.

---

## Positive Observations

- Gate 1 (edit-time hook) is clean: 199 LOC, correct reversibility-based enforcement,
  feature flag present, ritual_signal emission verified. The `evaluate()` function is
  well-structured with clear allow/warn/refuse semantics.
- Gate 3 (commit-time) correctly implements the F-7 mandatory condition: non-.py paths
  (authority_doc_rewrite, archive_promotion) handled via path-match-only with explicit
  comment citing F-7. The AST decorator walk for .py paths is sound.
- Gate 5 logic (when called) is correct: fail-closed on unknown conditions,
  condition evaluators properly typed, `is_blocked()` non-raising variant is a good
  design for status endpoints.
- ShadowExecutor structural isolation is clean: zero live_executor imports confirmed
  by AST scan (test C5-2), no `token` parameter in `submit()` signature. The ABC split
  achieves its structural type-time enforcement goal.
- `_CAP_BLOCKED_WHEN` as a local cache in gate_runtime is correct: avoids YAML I/O on
  every runtime check, makes gate_runtime dependency-free at import. The tradeoff
  (update both here and capabilities.yaml on changes) is documented.
- Regression baseline 99 passed / 5 skipped is clean. All charter, sunset, and
  mandatory-evidence tests pass. The ritual_signal schema is consistent across all 5 gates.
- Phase 4.A G-1 delivery is confirmed: `topology_doctor_packet_prefill.py` deleted,
  `topology_doctor.py` wrappers removed. The cascade-delete was executed cleanly.

---

## Remediation Required Before Re-Review

| ID | File | Fix | Effort |
|---|---|---|---|
| K0-1 | `src/execution/live_executor.py:108` | Add `__slots__ = ('_issued_at', '_gate')` to `LiveAuthToken` dataclass | 1 line |
| K0-1-test | `tests/test_gate2_live_auth_token.py` | Add `test_live_auth_token_forgery_via_dict_blocked` | ~8 lines |
| K0-2 | `src/execution/executor.py` | Add `gate_runtime.check("live_venue_submit")` as first statement in `execute_final_intent`, `execute_intent`, `_live_order`; add `gate_runtime.check("settlement_write")` to `execute_exit_order` | 4 lines |
| K0-2-test | `tests/test_gate_runtime.py` or new | Add test: with ZEUS_KILL_SWITCH=1, `execute_final_intent(...)` raises RuntimeError without going through LiveExecutor.submit() | ~15 lines |

**Total remediation: ~28 lines across 4 files. No architectural changes required.**

Re-review after remediation can be sonnet-tier (same scope as this review).

---

**Signed: code-reviewer agent (sonnet tier, Phase 4 K1 exit gate per invariants.jsonl lines 4+16)**
**Date: 2026-05-06**
**Branch: topology-redesign-2026-05-06 HEAD c0426daf**
**Verdict: NO-GO — 2 CRITICAL/HIGH findings at HIGH confidence require remediation before Phase 5 dispatch**
**Finding counts: CRITICAL=1, HIGH=1, MEDIUM=2, LOW=3**
**K0-1 LiveAuthToken phantom integrity: FORGEABLE (via object.__new__ + __dict__ write)**
**K0-2 Gate 5 non-bypassable claim: FALSE (execute_intent/execute_final_intent/execute_exit_order bypass gate_runtime.check)**
**Gate 4 fixture vacuous-pass: VACUOUS FOR DATA REGRESSIONS, NOT FOR LOGIC REGRESSIONS (by documented design)**

---

## Remediation verification 2026-05-06

**Branch HEAD:** c6f4885c phase 4 remediation: K0-1 + K0-2 + M-2 + R12 partial close
**Reviewer:** code-reviewer agent (sonnet tier, Phase 4 remediation re-review)
**Regression baseline:** 36 passed in 0.52s (test_gate_edit_time + test_gate_commit_time + test_gate_runtime + test_gate2_live_auth_token + test_gate5_direct_caller_bypass + test_ritual_signal_emission)

### K0-1 LiveAuthToken phantom forgery — STATUS: PARTIALLY-RESOLVED

**Original attack (3-line `__dict__` write): RESOLVED.**

Re-test:
```python
from src.execution.live_executor import LiveAuthToken
obj = object.__new__(LiveAuthToken)
obj.__dict__['_issued_at'] = '2026-01-01T00:00:00+00:00'
# AttributeError: 'LiveAuthToken' object has no attribute '_issued_at'
```
`__slots__ = ('_issued_at', '_gate')` confirmed present; instance has no `__dict__`. Test
`test_live_auth_token_unforgeable_via_dict_write` PASSES (verified in 36-test baseline).

**NEW FINDING — K0-1b (HIGH): `object.__setattr__` bypass survives `__slots__` patch.**

While re-running the K0-1 verification, an alternative forgery vector was discovered:

```python
from src.execution.live_executor import LiveAuthToken
obj = object.__new__(LiveAuthToken)
object.__setattr__(obj, '_issued_at', 'forged-timestamp')
object.__setattr__(obj, '_gate', 'gate2_live_auth_token')
# isinstance(obj, LiveAuthToken) == True
# obj._issued_at == 'forged-timestamp'
# Frozen guard intact for normal `obj._issued_at = ...` (still raises FrozenInstanceError)
```

**Why this works:** `__slots__` only removes `__dict__`; it does not prevent
`object.__setattr__` from writing to slot descriptors. `frozen=True` overrides
`__setattr__` at the class level to raise `FrozenInstanceError`, but `object.__setattr__`
bypasses the descriptor lookup and writes directly to the slot's C-level storage.
This is a well-known Python idiom for bypassing frozen dataclasses (it is how
`dataclasses.replace()` works internally — see CPython `Lib/dataclasses.py:_create_fn`).

**Severity assessment:** HIGH (not CRITICAL like K0-1).

The original K0-1 was CRITICAL because a 3-line attack was visible from any reviewer
glance at the dataclass definition; the `__dict__` write is a primitive Python idiom
many engineers use without thinking about it. K0-1b is HIGH because:
- The attack requires explicit knowledge of `object.__setattr__` as a frozen-bypass.
- It is not a pattern an engineer would reach for accidentally.
- Defense in depth at the type-check layer (mypy/pyright already enforces token
  passing structurally) means runtime forgery has a single attacker — code that
  intentionally bypasses Gate 2.
- The original K0-1 risk was "any caller could forge"; K0-1b risk is "an attacker
  who specifically researches Python internals could forge."

But it is NOT acceptable to leave open. The Gate 2 docstring claims the phantom is
"opaque" and "construction is RESTRICTED to LiveExecutor subclasses via _mint_token."
Both claims remain false while `object.__setattr__` works.

**Remediation options:**

- (A) **Cryptographic seal:** Add a private `_seal: bytes` field minted from
  `hashlib.sha256(_issued_at + secrets.token_bytes(32))` and verified at
  `_do_submit` entry. Forgery requires knowing the per-process secret (set at
  module import). This makes `object.__setattr__` insufficient unless the attacker
  also extracts the per-process secret. Effort: ~30 lines.
- (B) **Issuer-side registry:** Maintain a per-process `_LIVE_TOKENS: weakref.WeakSet`
  of all minted tokens; `_do_submit` asserts `token in _LIVE_TOKENS`. Forgery via
  `object.__setattr__` produces a token not in the registry. Effort: ~15 lines.
- (C) **Accept K0-1b as residual risk:** Document that the phantom defends against
  accidental misuse and type-time errors, NOT against malicious bypass; the trust
  boundary is "code in `src/execution/`." This is the existing Gate 2 model and
  is consistent with how the `__new__` guard is written (frame inspection of
  `caller_file`).

Option (B) is the cleanest with the lowest churn. Option (C) is acceptable IF the
docstring is updated to reflect the actual trust model.

**Operator decision required (OD-K0-1b):** Choose (A), (B), or (C). The previous
remediation slate did not include this finding because the original critic review
only tested the `__dict__` path. This is a NEW finding surfaced during re-verification,
not a regression of the prior fix.

### K0-2 Gate 5 bypass — STATUS: RESOLVED

**Original attack (direct execute_final_intent call with ZEUS_KILL_SWITCH=1): BLOCKED.**

Re-test (with ZEUS_KILL_SWITCH=1):
```
execute_intent(None, ...)         → RuntimeError: [gate_runtime] BLOCKED cap='live_venue_submit': condition 'kill_switch_active' is active
execute_final_intent(None)        → RuntimeError: [gate_runtime] BLOCKED cap='live_venue_submit': ...
_live_order('t', None, 1.0)       → RuntimeError: [gate_runtime] BLOCKED cap='live_venue_submit': ...
execute_exit_order(...)           → gate_runtime.check IS called (verified by inspection); see R-3 deviation below
```

All 4 entry points now call `gate_runtime.check()` as the first executable line
(after lazy import). Source inspection confirms the new lines are present at the
top of each function (line +14 to +17 of each function body). Test file
`tests/test_gate5_direct_caller_bypass.py` has 4 tests, all PASS.

### M-2 Forgery resistance test — STATUS: RESOLVED (for the original `__dict__` vector)

`test_live_auth_token_unforgeable_via_dict_write` exists and passes. This test pins
the `__slots__` fix as a permanent regression antibody for the `__dict__` write path.

**Caveat:** The test does NOT cover the K0-1b `object.__setattr__` vector. If
remediation option (A) or (B) for K0-1b is selected, the test should be extended
to assert that `object.__setattr__` also fails. If option (C) is selected (residual
risk acceptance), the test docstring should explicitly state that `__slots__` defends
against accidental forgery only, and the trust model is "code in `src/execution/` is
trusted."

### R-3 deviation: gate_runtime.check("settlement_write") + ZEUS_KILL_SWITCH

**Critic verdict on R-3 deviation: STRUCTURALLY SOUND, BUT FLAGS A SEMANTIC QUESTION.**

Per `architecture/capabilities.yaml`:
- `settlement_write` has `blocked_when: []` (empty list)
- `live_venue_submit` has `blocked_when: [kill_switch_active, risk_level_halt]`

The executor's R-3 rewrite is correct: `gate_runtime.check("settlement_write")` does
NOT raise on `ZEUS_KILL_SWITCH=1` because the capability has no `kill_switch_active`
condition wired. Test `test_execute_exit_order_calls_gate_runtime` correctly verifies
that the gate IS called (via monkeypatch capture), not that it raises — this is the
right test design given the capability definition.

**Semantic gap (NEW LOW-confidence finding — surfaced not blocking):**

Should `settlement_write.blocked_when` include `kill_switch_active`? Arguments:

- **Yes:** Settlement writes touch canonical position state (INV-14 protected).
  If the kill switch is armed, no state should be written that could not be unwound
  by Chronicler replay. A settlement write during kill-switch is exactly the kind of
  truth-rewrite the kill switch should prevent.
- **No:** Settlement writes are post-trade harvest events; they do not initiate new
  venue contact. The kill switch is "stop all live venue commands"; settlement is
  reading the venue's already-final state and writing it locally. Blocking settlement
  on kill-switch could leave the local DB out of sync with on-chain truth.

**Confidence: LOW.** This is a domain-level question for the operator — neither
direction is structurally obvious from the code. The current configuration
(`blocked_when: []`) is consistent with "settlement is local truth-recording, not
venue-emitting." If the operator's mental model differs, capabilities.yaml needs
updating; if the current model is correct, the executor's R-3 rewrite is fine.

**Phase 5 condition:** Add to Phase 5 brief: operator confirmation of
`settlement_write.blocked_when` semantic intent. This is not a Phase 4 blocker.

### R12 topology_schema.yaml partial close — VERDICT: ACCEPT

**Executor's partial-close claim:** ownership block inlined (option b, 13 sites resolved,
~80 LOC moved); FULL deletion deferred to Phase 5 because schema is also consumed by
4 additional sites:
- `issue_json_contract` drift guard (test_topology_doctor.py:2156)
- `agent_runtime_contract` (test_topology_doctor.py:4530)
- `route_card_required_fields` (test_topology_doctor.py:4530 area)
- `run_schema()` / `_check_schema()` pipeline (topology_doctor.py:795, registry_checks.py:408)
- ~100 more LOC across these consumer sites

**Critic accepts the partial close.** Rationale:

1. The G-2/P3-H1 verdict from phase3_h_decision.md was "Phase 4 Gate 1 forcing function
   is the architecturally correct resolution mechanism." That verdict accepted that
   topology_schema.yaml deletion happens on the natural cadence of Gate 1 capability-
   schema refactoring, not as an isolated removal.
2. The Phase 4.A executor's escape clause (~150 LOC threshold) was operator-approved
   when the original 13 ownership sites were identified. The newly-discovered 4
   additional consumer sites are within the same logical refactor scope.
3. Inlining 13 sites in Phase 4 + deferring 4 to Phase 5 is structurally cleaner than
   forcing a partial-state delete that breaks the contract test pipeline.
4. **Hard condition:** Phase 5 brief MUST list the 4 remaining consumer sites + the
   `~100 LOC` cleanup as a Phase 5 EXIT criterion (not optional). The R12 risk
   register entry must remain open until topology_schema.yaml is gone.

**This means R12 does NOT close in Phase 4. It is REDUCED to ~30% of its original
scope, with the remaining ~70% pinned to Phase 5 EXIT.** The risk register update
language should be:

> R12 Phase 4.D status: 13/17 consumer sites refactored (ownership block inlined into
> capabilities.yaml). 4 consumer sites + ~100 LOC remain (issue_json_contract drift
> guard, agent_runtime_contract, route_card_required_fields, run_schema/_check_schema
> pipeline). Full topology_schema.yaml deletion is now a Phase 5 EXIT criterion.

### Final Phase 4 Verdict: GO-WITH-CONDITIONS

**Reasoning:**

The two critic-blocking findings from the original NO-GO verdict (K0-1 `__dict__`
forgery + K0-2 Gate 5 bypass) are RESOLVED. Regression baseline is green (36/36).
M-2 forgery test is in place. R12 partial close is ACCEPTED with the residual scope
formally pinned to Phase 5 EXIT.

The new K0-1b finding (`object.__setattr__` bypass surviving `__slots__`) is HIGH but
not CRITICAL — the original 3-line attack-of-opportunity is closed; what remains is a
deliberate-bypass attack that requires Python internals knowledge. The operator can
reasonably choose to accept this as residual risk (option C) with a docstring update,
defer it to Phase 5 (option B registry), or remediate now (~15 LOC).

If the operator selects option (C) (accept-and-document), Phase 4 closes with no
further code changes — only a docstring fix to `live_executor.py` clarifying the
trust model. This is a 5-minute task. Phase 5 dispatch is then unblocked.

If the operator selects option (A) or (B), one more remediation cycle is needed
before Phase 5 dispatch.

### Phase 4 Carry-Forward Conditions (binding for Phase 5)

1. **K0-1b decision:** Operator chooses (A) cryptographic seal, (B) registry, or
   (C) accept-and-document. (C) is acceptable if the `LiveAuthToken` docstring
   is updated to state: "Phantom integrity defends against accidental forgery via
   `__dict__` writes and direct construction. It does NOT defend against malicious
   bypass via `object.__setattr__` or memory introspection. The trust boundary is
   code under `src/execution/`."
2. **R12 residual:** Phase 5 EXIT criterion = full deletion of `topology_schema.yaml`
   + refactor of `issue_json_contract`, `agent_runtime_contract`,
   `route_card_required_fields`, `run_schema()/_check_schema()` consumer sites.
3. **M-1 Gate 4 fixture:** Phase 5 must either upgrade to seeded fixture (option a)
   OR document the data-regression gap explicitly in RISK_REGISTER R2.
4. **L-1, L-2, L-3:** Carry forward as previously noted; L-3 has hard deadline
   2026-06-05 (@untyped_for_compat expiry).
5. **R-3 settlement_write semantics:** Operator confirmation of
   `settlement_write.blocked_when` content (currently `[]`) — should it include
   `kill_switch_active`? LOW-confidence finding; not blocking, but Phase 5 brief
   should resolve.

### Finding-by-Finding Resolution Status

| ID | Severity | Status | Confidence |
|---|---|---|---|
| K0-1 (`__dict__` forgery) | CRITICAL | RESOLVED | HIGH |
| K0-1b (`object.__setattr__` forgery) | HIGH | NEW — operator decision required | HIGH |
| K0-2 (Gate 5 direct-call bypass) | HIGH | RESOLVED | HIGH |
| M-1 (Gate 4 vacuous data regression) | MEDIUM | PHASE-5-CARRY (accepted) | HIGH |
| M-2 (forgery resistance test) | MEDIUM | RESOLVED for `__dict__` vector | HIGH |
| R12 (topology_schema.yaml deletion) | TRACKED | PARTIAL-CLOSE accepted; Phase 5 EXIT | HIGH |
| R-3 (settlement_write semantic gap) | LOW | NEW — Phase 5 carry | LOW |
| L-1 (`_assert_risk_level_allows` no-op) | LOW | PHASE-5-CARRY | MEDIUM |
| L-2 (gate_commit_time sunset_date) | LOW | RESOLVED (test green) | HIGH |
| L-3 (@untyped_for_compat CI expiry) | LOW | PHASE-5-CARRY (deadline 2026-06-05) | MEDIUM |

---

**Signed: code-reviewer agent (sonnet tier, Phase 4 K1 remediation verification)**
**Date: 2026-05-06**
**Branch HEAD: c6f4885c**
**Final verdict: GO-WITH-CONDITIONS**
**Resolution counts: RESOLVED=4 (K0-1, K0-2, M-2, L-2); NEW=2 (K0-1b HIGH, R-3 LOW); PHASE-5-CARRY=4 (M-1, R12 residual, L-1, L-3); ACCEPTED-PARTIAL=1 (R12)**
**Open operator decisions: OD-K0-1b (forgery hardening option A/B/C); OD-R-3 (settlement_write blocked_when semantics)**
