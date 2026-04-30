File: scripts/AGENTS.md
Disposition: NEW
Authority basis: architecture/script_manifest.yaml; architecture/zones.yaml; docs/authority/zeus_current_delivery.md.
Why this file exists now: scripts can overreach DB truth or persist one-off probes unless their lifecycle is explicit.

# scripts AGENTS

Scripts are enforcement, audit, runtime support, ETL, repair, or operator tools.
The machine registry is `architecture/script_manifest.yaml`.

Module book: `docs/reference/modules/scripts.md`

## Machine Registry

Use `architecture/script_manifest.yaml` for:

- script lifecycle: `long_lived`, `packet_ephemeral`, `promotion_candidate`, `deprecated_fail_closed`
- authority scope
- read/write targets
- dry-run/apply metadata
- target DB and danger classification
- reuse/disposal policy

Use `python3 scripts/topology_doctor.py --scripts --json` to check that top-level
scripts are registered and safe for their declared class.

## Class Quick Guide

- `enforcement`: repeatable topology, policy, lint, or contract gate.
- `diagnostic`: read-only investigation; stdout only; no live authorization.
- `diagnostic_report_writer`: repeatable non-authority report artifact writer.
- `runtime_support`: operator health/watch/resume/post-run support wrapper.
- `etl_writer`: repeatable external/world-data ingestion or backfill writer.
- `repair`: packet-approved repair with explicit dry-run/apply boundary.
- `config_writer`: repeatable config artifact generator.
- `stale_deprecated`: retained only to fail closed; `DO_NOT_RUN`.

## Core Rules

- Check the manifest before adding a top-level script; reuse or extend existing
  long-lived tools when possible.
- Canonical file/function naming and freshness rules live in
  `architecture/naming_conventions.yaml`; do not redefine them here.
- Touched, newly created, or evidence-reused scripts must satisfy the freshness
  header contract in `architecture/naming_conventions.yaml`.
- Old or unknown scripts must be inspected against this file and
  `architecture/script_manifest.yaml` before execution.
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
| `venus_sensing_report.py` | Venus sensing report, including daemon-independent source-contract watch |
| `backfill_tigge_snapshot_p_raw.py` | Replay-compatible TIGGE `p_raw_json` materialization |
| `watch_source_contract.py` | Polymarket settlement source-contract monitor, city quarantine writer, and conversion-history reporter |
| `source_contract_auto_convert.py` | Cron-safe source-contract transition controller, deterministic date-scope planner, apply/evidence runner, quarantine release gate, receipt writer, and Discord reporter |
