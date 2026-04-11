# src/control AGENTS

Zone: K1 — Protective (control plane)

## What this code does (and WHY)

The control plane allows Venus/OpenClaw to change Zeus's runtime behavior WITHOUT process restart. Commands like `pause_entries`, `tighten_risk`, and `set_strategy_gate` are read from `control_plane.json` each cycle. This is the ONLY write interface from the outside — Venus reads Zeus state through supervisor contracts, but writes ONLY through control plane commands.

## Key files

| File | Purpose | Watch out for |
|------|---------|---------------|
| `control_plane.py` | Command processing (6 supported commands), edge threshold multiplier, strategy gates | Adding new commands changes the external contract |

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
