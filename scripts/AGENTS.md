File: scripts/AGENTS.md
Disposition: NEW
Authority basis: architecture/script_manifest.yaml; architecture/zones.yaml; docs/authority/zeus_current_delivery.md.
Why this file exists now: scripts can overreach DB truth or persist one-off probes unless their lifecycle is explicit.

# scripts AGENTS

Scripts are enforcement, audit, runtime support, ETL, repair, or operator tools.
The machine registry is `architecture/script_manifest.yaml`.

## Machine Registry

Use `architecture/script_manifest.yaml` for:

- script lifecycle: `long_lived`, `packet_ephemeral`, `promotion_candidate`, `deprecated_fail_closed`
- authority scope
- read/write targets
- dry-run/apply metadata
- target DB and danger classification
- reuse/disposal policy

Use `python scripts/topology_doctor.py --scripts --json` to check that top-level
scripts are registered and safe for their declared class.

## Core Rules

- Check the manifest before adding a top-level script; reuse or extend existing
  long-lived tools when possible.
- One-off scripts need `task_YYYY-MM-DD_<purpose>.py` naming plus `delete_by`.
- Repair/ETL writers must declare write targets and dry-run/apply behavior.
- Diagnostics and reports must not write canonical DB truth.
- Scripts are not hidden authority centers.

## Local Registry

Only list durable entry points here; use the manifest for the full catalog.

| Script | Purpose |
|--------|---------|
| `topology_doctor.py` | Compiled topology/digest/health checks |
| `check_daemon_heartbeat.py` | Daemon heartbeat staleness check |
| `backfill_tigge_snapshot_p_raw.py` | Replay-compatible TIGGE `p_raw_json` materialization |
