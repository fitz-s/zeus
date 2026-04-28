# INV-29 amendment planning-lock receipt — 2026-04-27

Date: 2026-04-27
Branch: plan-pre5
Task: R3 M1 INV-29 closed-law amendment for grammar-additive CommandState / CommandEventType values
Changed files: `architecture/invariants.yaml`, `docs/operations/task_2026-04-26_ultimate_plan/r3/operator_decisions/INDEX.md`, `tests/test_command_grammar_amendment.py`, `architecture/topology.yaml`, `tests/test_digest_profile_matching.py`, R3 state/evidence surfaces, and the packet receipt.
Summary: Incorporates the already-implemented and reviewed M1 command-side grammar expansion into the authoritative `INV-29` invariant. This closes the M1 governance gate narrowly so M1 can move from `COMPLETE_AT_GATE` to `COMPLETE` and M2 can be frozen next after M1 closeout/review. It does not authorize M2 unknown-side-effect runtime semantics, live venue submission, CLOB cutover, or any operator go-live gate.
Verification: Pending final M1 gate closeout, critic, and verifier after this receipt is wired.
Next: Run M1 focused tests, topology navigation, drift, planning-lock, closeout, then pre-close and post-close critic+verifier before unfreezing M2.

## Authority basis

- User directive in this session grants broad but cautious autonomous improvisation to complete the Zeus upgrade, with objective function bounded by safety and evidence.
- `docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_at_gate_edge_M1.md` records that M1 engineering already reached the gate edge and passed pre-close critic/verifier for `COMPLETE_AT_GATE`.
- `docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M1.yaml` requires `INV-29 amendment + planning-lock receipt` before M1 can become fully complete.
- `docs/authority/zeus_current_delivery.md` treats `architecture/**` and lifecycle grammar as planning-lock/governance work.
- `architecture/invariants.yaml` is the machine law surface for INV-29.

## Incorporated law

`architecture/invariants.yaml` now records amendment `R3-M1-INV-29-2026-04-27` with explicit `CommandState` and `CommandEventType` value lists matching `src/execution/command_bus.py`.

The amendment scope is deliberately narrow:

- Allows M1's tested grammar-additive command-side values.
- Preserves closed enum enforcement.
- Keeps `RESTING`, `MATCHED`, `MINED`, `CONFIRMED`, and venue order/trade finality outside `CommandState`.
- Does not implement or authorize M2 runtime resolution semantics.
- Does not authorize live venue submission, CLOB v2 cutover, TIGGE activation, or calibration retrain go-live.

## Rollback

If review finds the amendment over-broad, revert only the `INV-29` amendment block and operator decision status back to OPEN; M1 remains `COMPLETE_AT_GATE` and M2 stays frozen. No runtime/source code change is required for that rollback.
