# Work Log -- task_2026-04-26_operations_package_cleanup

## 2026-04-26 -- cleanup and review

- Identified the bad guidance that encouraged sibling phase packages:
  `docs/operations/AGENTS.md`, `docs/README.md`,
  `architecture/naming_conventions.yaml`, `architecture/docs_registry.yaml`,
  `architecture/topology.yaml`, and `scripts/zpkt.py`.
- Moved midstream remediation phase folders under the main package
  `phases/` directory.
- Left independent workstreams, including Packet Runtime and graph/rendering
  integration, as separate top-level packages.
- Updated `zpkt start --package` so future phase evidence can be created under
  an existing package.
- Process note: the earlier workflow over-read `AGENTS.md` on ordinary
  subphases, wrote too many per-phase closeout artifacts, and used too many
  tiny reviewer cycles. Future midstream work should reread root guidance after
  compaction or material topology changes, batch simple related fixes, and keep
  logs to phase-level decisions plus verification.
- Runtime note: `state/daemon-heartbeat.json` and `state/status_summary.json`
  are live projections. They should not drive ordinary source/docs packet
  receipts or review scope; only a runtime-governance packet should change that
  tracking policy.
