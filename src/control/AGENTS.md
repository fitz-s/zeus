# src/control AGENTS

Module book: `docs/reference/modules/control.md`
Machine registry: `architecture/module_manifest.yaml`

Zone: K1 — Protective (control plane)

## What this code does (and WHY)

The control plane allows Venus/OpenClaw to change Zeus's runtime behavior WITHOUT process restart. Commands like `pause_entries`, `tighten_risk`, and `set_strategy_gate` are read from `control_plane.json` each cycle. This is the ONLY write interface from the outside — Venus reads Zeus state through supervisor contracts, but writes ONLY through control plane commands.

## Key files

| File | Purpose | Watch out for |
|------|---------|---------------|
| `control_plane.py` | Command processing (6 supported commands), edge threshold multiplier, strategy gates | Adding new commands changes the external contract |
| `cutover_guard.py` | CLOB V2 cutover runtime state machine and live-side-effect gate | Must fail closed; live enablement remains operator-gated |
| `heartbeat_supervisor.py` | CLOB V2 venue-heartbeat health and resting-order submit gate | Must reuse `auto_pause_failclosed.tombstone`; no second tombstone source |
| `ws_gap_guard.py` | M3 user-channel WS gap submit gate and M5 reconcile-required marker | Must fail closed and must not implement M5 recovery/unblock semantics |
| `gate_decision.py` | `GateDecision` frozen dataclass + `ReasonCode` enum + `reason_refuted()` — machine-readable gate provenance | `reason_refuted()` returns False for all codes in Phase 1 (conservative); do not add per-code refutation logic without a Phase 2 plan |

## Domain rules

- Control plane commands are narrow-by-intent: each command does exactly one thing
- External systems (Venus/OpenClaw) NEVER write to Zeus state directly — only through control commands
- Control surfaces may tighten or pause — they may NOT silently rewrite truth
- Changes here require planning lock (K1 zone)

## Common mistakes agents make here

- Adding a command that mutates DB truth directly (must go through lifecycle manager)
- Coupling control plane logic to K3 math code (zone boundary violation)
- Making commands that are too broad ("do everything") instead of narrow-by-intent

## References
- Root rules: `../../AGENTS.md`
- Supervisor contracts: `../supervisor_api/contracts.py`
