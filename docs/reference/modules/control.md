# Control Module Authority Book

**Recommended repo path:** `docs/reference/modules/control.md`
**Current code path:** `src/control`
**Authority status:** Dense module reference for durable operator control semantics and gate decisions.

## 1. Module purpose
Provide the narrow, durable, operator-facing control surface that can change runtime behavior without violating Zeus's authority grammar.

## 2. What this module is not
- Not a free-form runtime command bus.
- Not a replacement for riskguard or state truth.
- Not a place where in-memory toggles outrank durable control semantics.

## 3. Domain model
- Control plane commands and resolved control state.
- Gate decisions that decide what runtime paths are permitted.
- Durability and replay of operator-issued overrides.

## 4. Runtime role
Bridges operator intent into safe, typed, durable runtime behavior changes.

## 5. Authority role
K1 governance surface. Control is allowed to change runtime behavior, but only through explicit grammar, durable storage, and accountable evidence. The constitution and delivery law both treat this zone as planning-lock territory.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `src/control/control_plane.py` and `gate_decision.py`
- `src/control/heartbeat_supervisor.py` for R3 venue-heartbeat fail-closed gating
- `src/control/ws_gap_guard.py` for R3 M3 authenticated user-channel gap submit gating
- `docs/authority/zeus_change_control_constitution.md` K1 governance rules
- `docs/authority/zeus_current_delivery.md` planning-lock and always-human-gated sections for control semantics

### Non-authority surfaces
- Shell environment variables used as hidden long-lived control
- Operator chat memory or host-level instructions
- One-off scripts that mutate control semantics without packet approval

## 7. Public interfaces
- Control-plane command ingestion and persistence
- Gate decision objects consumed by runtime

## 8. Internal seams
- Operator ingress vs durable storage
- Control decision vs riskguard policy vs engine gating

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `control_plane.py` | Main durable control surface. Must stay narrow and typed. |
| `cutover_guard.py` | Runtime cutover state machine that blocks venue side effects until operator-enabled. |
| `heartbeat_supervisor.py` | Venue heartbeat state observer and fail-closed resting-order gate. Reuses `auto_pause_failclosed.tombstone`; it is not a second control truth surface. |
| `ws_gap_guard.py` | User-channel WebSocket gap state observer. Blocks new submit and records that future M5 reconciliation evidence is required before full unblock. |
| `gate_decision.py` | Resolved gate object used by runtime/evaluator. |

## 10. Relevant tests
- tests/test_b070_control_overrides_history_v2.py
- tests/test_authority_gate.py
- tests/test_cutover_guard.py
- tests/test_heartbeat_supervisor.py
- tests/test_user_channel_ingest.py
- tests/test_bug100_k1_k2_structural.py

## 11. Invariants
- Control semantics must be durable, typed, and replayable.
- Control may change runtime behavior, but may not silently mutate law or schema.
- Control must not compete with `strategy_key` as a governance center.
- GTC/GTD live resting orders must not submit unless the venue heartbeat is HEALTHY; FOK/FAK immediate-only orders are the only heartbeat-exempt order types.
- New venue submits must not proceed when the authenticated user-channel WS is disconnected/stale/auth-failed/mismatched; M3 may mark M5 reconcile required but must not implement M5 recovery.

## 12. Negative constraints
- No hidden in-memory override dicts as long-lived truth.
- No operator ingress that bypasses packeted authority for persistent semantics.

## 13. Known failure modes
- Control intent exists but is not durably recorded.
- Control state becomes stale/shadowed across tables or files.
- Gate decisions appear in status surfaces without actual runtime consumption.
- A heartbeat failure writes a separate tombstone or only logs an alert instead of activating the fail-closed auto-pause path.

## 14. Historical failures and lessons
- [Archive evidence] Control/history migration work in legacy packets showed why append-only override history is safer than mutable in-place control surfaces.

## 15. Code graph high-impact nodes
- `src/control/control_plane.py` — bridge between human/operator side and runtime internals.
- `src/control/heartbeat_supervisor.py` — bridge between venue-heartbeat health and execution gating.
- `src/control/gate_decision.py` — lightweight but semantically critical hub.

## 16. Likely modification routes
- New control command: specify storage, runtime consumer, absent-capability behavior, and rollback.
- Gate semantic change: review riskguard, engine, and state together.
- Heartbeat gate change: review execution submit seams, `src/main.py` scheduler wiring, and the single-tombstone invariant together.

## 17. Planning-lock triggers
- Any control-plane semantic change or gate-decision grammar change.

## 18. Common false assumptions
- Control is just operator convenience and can be stringly-typed.
- Temporary overrides don't need durability.

## 19. Do-not-change-without-checking list
- Control command grammar
- Durable control storage semantics
- Gate decision type shape without downstream audit

## 20. Verification commands
```bash
pytest -q tests/test_b070_control_overrides_history_v2.py tests/test_authority_gate.py tests/test_bug100_k1_k2_structural.py
python -m py_compile src/control/*.py
```

## 21. Rollback strategy
Rollback control-plane packets with their persistence model; never leave partial command grammar active.

## 22. Open questions
- Should control-plane command vocabulary be separately machine-registered to prevent drift?

## 23. Future expansion notes
- Expose control command grammar in module manifest and supervisor docs.

## 24. Rehydration judgement
This book is the dense reference layer for control. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
