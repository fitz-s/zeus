# src/observability AGENTS

Module book: `docs/reference/modules/observability.md`
Machine registry: `architecture/module_manifest.yaml`

Zone: K2_runtime — derived operator read model

## What this code does (and WHY)

Zeus is not a black box. Every cycle, `status_summary.py` writes a 5-section health snapshot that Venus/OpenClaw reads. This is a DERIVED surface — it summarizes DB truth for operator visibility but is NEVER promoted back to canonical truth (INV-03).

## Key files

| File | Purpose | Watch out for |
|------|---------|---------------|
| `status_summary.py` | Cycle health snapshot (positions, edges, risk, strategy gates, execution events) | Reads from DB and control plane — must not write to either |

## Domain rules

- Status summary is DERIVED, never canonical — do not read it back as truth
- Output is live-only derived status via `src/config.state_path()`
- K2_runtime evidence applies here: status-summary changes need runtime trace or targeted tests. This surface is derived/read-model only and must not become canonical truth.

## Common mistakes agents make here

- Treating status_summary.json as a data source for decisions (it's for operator display only)
- Importing internal state module functions instead of public query interfaces

## References
- Root rules: `../../AGENTS.md`
