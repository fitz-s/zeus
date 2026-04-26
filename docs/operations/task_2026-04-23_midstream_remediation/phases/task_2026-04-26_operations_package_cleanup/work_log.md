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

## 2026-04-26 -- runtime projection untracking

- Removed `state/daemon-heartbeat.json` and `state/status_summary.json` from
  the Git index with `git rm --cached`, leaving both local files present for
  the running daemon. `.gitignore` already ignores `/state/*`, so future daemon
  refreshes no longer dirty ordinary source/docs/test packets.
- This fixes the process failure that repeatedly forced heartbeat-only commits,
  stash churn, and receipt noise. It does not change runtime read/write code or
  operator semantics.

## Bureaucracy assessment

### What was excessive

- Re-reading root guidance before every small phase was too frequent. The right
  trigger is compaction, route/topology change, cross-zone work, or reviewer
  evidence of drift.
- Per-phase `plan.md`, `work_log.md`, `scope.yaml`, and `receipt.json` for
  tiny docs/review-fix slices created more evidence than decisions. Small,
  reversible docs/tooling packages should default to `plan + receipt`; use a
  separate work log only for multi-day, high-risk, or cross-module changes.
- Running critic/review after every tiny edit slowed throughput without adding
  proportional safety. Batch low-risk adjacent work into one review wave.
- Treating tracked runtime projections as normal source diffs was a process
  error. Runtime projection churn belongs outside ordinary packet receipts.

### What must stay

- One scoped topology/navigation check at packet start.
- Focused tests or static checks that prove the changed behavior.
- At least one substantive reviewer/critic pass for source, schema, runtime,
  DB, risk, replay, or settlement-impacting work.
- Planning-lock, map-maintenance, and receipt checks for high-risk or
  cross-zone changes.

### Operating rule going forward

Use the lightest evidence that proves the claim. Do not add ceremony that only
proves an agent followed ceremony.
