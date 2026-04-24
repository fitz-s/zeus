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
| `gate_decision.py` | Resolved gate object used by runtime/evaluator. |

## 10. Relevant tests
- tests/test_b070_control_overrides_history_v2.py
- tests/test_authority_gate.py
- tests/test_bug100_k1_k2_structural.py

## 11. Invariants
- Control semantics must be durable, typed, and replayable.
- Control may change runtime behavior, but may not silently mutate law or schema.
- Control must not compete with `strategy_key` as a governance center.

## 12. Negative constraints
- No hidden in-memory override dicts as long-lived truth.
- No operator ingress that bypasses packeted authority for persistent semantics.

## 13. Known failure modes
- Control intent exists but is not durably recorded.
- Control state becomes stale/shadowed across tables or files.
- Gate decisions appear in status surfaces without actual runtime consumption.

## 14. Historical failures and lessons
- [Archive evidence] Control/history migration work in legacy packets showed why append-only override history is safer than mutable in-place control surfaces.

## 15. Code graph high-impact nodes
- `src/control/control_plane.py` — bridge between human/operator side and runtime internals.
- `src/control/gate_decision.py` — lightweight but semantically critical hub.

## 16. Likely modification routes
- New control command: specify storage, runtime consumer, absent-capability behavior, and rollback.
- Gate semantic change: review riskguard, engine, and state together.

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
