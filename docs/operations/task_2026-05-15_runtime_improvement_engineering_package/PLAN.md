# Runtime Improvement Engineering Package

Created: 2026-05-15
Status: PLAN-ONLY (this packet ships docs/specs only; implementation packets are
listed in `05_execution_packets/PACKET_INDEX.md` and ship separately)

## What This Is

A multi-track engineering package addressing four problems that compound on
each other in the current Zeus / OMC / OpenClaw runtime:

1. The topology subsystem has been redesigned five times in four days
   (`task_2026-05-05_topology_noise_repair`, `_2026-05-06_topology_redesign`,
   `_2026-05-07_navigation_topology_v2`, `_2026-05-08_topology_redesign_completion`,
   `_2026-05-09_post_s4_residuals_topology`) plus two hook-redesign rounds.
   Each round added surface; none of them eliminated the recurring failure
   pattern (lexical profile miss, scope-expansion on coherent unions, phrasing
   game tax). The hidden branches must be mined for what was tried, what stuck,
   what reverted, and what failure mode persists across iterations - then
   replaced with a project-agnostic v_next.
2. PR throughput has outpaced authority-doc maintenance. Files marked as
   authoritative (under `architecture/`, `docs/reference/`, project-root
   `AGENTS.md`) drift from current source. Agents cite stale authority and
   either repeat past mistakes or block themselves on requirements that no
   longer hold.
3. The workspace has accumulated one-off temp files, packet evidence that is
   no longer load-bearing, launchd plist `.bak`/`.replaced`/`.locked` shrapnel,
   abandoned worktrees, and uncommitted scratch. No autonomous hygiene loop
   exists; cleanup is manual and skipped under deadline pressure.
4. There is no scheduled autonomous maintenance agent. The OpenClaw cron layer
   (`~/.openclaw/cron/jobs.json`) and the launchd-managed Zeus daemons exist,
   but neither owns docs/operations/lore hygiene. Daily entropy goes
   un-counteracted.

## Tracks

This packet contains four parallel tracks plus an evidence floor and a
verification ceiling:

```
00_evidence/                          # Raw inventories from haiku probes
01_topology_v_next/                   # Universal (project-agnostic) topology design
02_daily_maintenance_agent/           # Scheduled hygiene agent design
03_authority_drift_remediation/       # Drift ledger + remediation plan
04_workspace_hygiene/                 # Purge/archive/lore-extract rules
05_execution_packets/                 # Ordered follow-up implementation packets
99_verification/                      # Probes & regression suite
```

Track dependencies:

```
00_evidence ─► 01_topology_v_next ─┐
            ─► 03_authority_drift ─┼─► 02_daily_maintenance_agent
            ─► 04_workspace_hygiene┘                          │
                                                              ▼
                                                  05_execution_packets
                                                              │
                                                              ▼
                                                       99_verification
```

`00_evidence` must be complete before any v_next claim. The maintenance agent
design depends on workspace-hygiene and authority-drift specs because those
specs define what the agent is allowed to act on.

## Reusable / Project-Agnostic Discipline

The topology v_next, the maintenance-agent design, the drift remediation
playbook, and the workspace-hygiene rules MUST be written so they apply to any
codebase, not just Zeus. Zeus-specific bindings live in the dedicated
`ZEUS_BINDING_LAYER.md` files, never in the universal core. The acceptance
test for "is this universal" is: a hypothetical second project (different
language, different domain) can adopt this with only its own binding layer.

## Non-Goals

- No source/state DB/launchd/credential mutation in this packet.
- No new topology_doctor.py code in this packet (spec only; implementation
  packet is listed under `05_execution_packets/`).
- No deletion of any existing packet evidence in this packet (the workspace
  hygiene track defines RULES; the daily agent applies them later under
  dry-run-first discipline).
- No collapse of "5 topology iterations" into "the previous designs were
  wrong." The v_next must absorb their wins, not negate them. A v_next that
  reverts a real-damage guard a previous iteration installed is a regression.
- No assumption that the maintenance agent gets full delete authority. It
  defaults to dry-run + propose-quarantine; destructive actions require an
  explicit allowlist updated by humans.

## Hidden Branches To Mine

(Filled by the sonnet `hidden-branches` task. Names are anchors for
synthesis output:)

- `task_2026-05-05_topology_noise_repair`
- `task_2026-05-06_topology_redesign`
- `task_2026-05-06_hook_redesign`
- `task_2026-05-07_navigation_topology_v2`
- `task_2026-05-07_hook_redesign_v2`
- `task_2026-05-08_topology_redesign_completion`
- `task_2026-05-09_post_s4_residuals_topology`

For each: what was the named problem, what was implemented, what got merged vs
reverted, what test/probe was added, what failure category is still observable
today, and what the iteration tells us about the underlying design constraint
that no incremental fix will resolve.

## Acceptance Criteria

- `00_evidence/` carries four populated inventories: hidden-branch packets,
  workspace mess sample, authority docs with last-touched dates, scheduled
  daemons/cron jobs.
- `01_topology_v_next/UNIVERSAL_TOPOLOGY_DESIGN.md` has zero Zeus-specific
  identifiers in the universal section. Zeus bindings live only in
  `ZEUS_BINDING_LAYER.md`.
- `01_topology_v_next/HIDDEN_BRANCH_LESSONS.md` produces one row per past
  iteration with `tried / shipped / reverted / persistent_failure_pattern /
  what_v_next_must_absorb`. No iteration is dismissed as "wrong."
- `02_daily_maintenance_agent/DESIGN.md` defines schedule, scope, authority,
  evidence trail, dry-run discipline, kill switch, and human-in-the-loop
  escalation. The design must be runnable under both launchd and the OpenClaw
  cron layer.
- `02_daily_maintenance_agent/SAFETY_CONTRACT.md` enumerates forbidden file
  paths, forbidden actions, and the explicit invariant that the agent may
  never modify `src/**`, `architecture/**`, `state/**`, hooks, plists, or
  credentials.
- `03_authority_drift_remediation/DRIFT_ASSESSMENT.md` lists every authority
  doc with last-modified date, last-reviewed date, current tier marking, and
  drift verdict (`CURRENT` / `MINOR_DRIFT` / `STALE_REWRITE_NEEDED` /
  `DEMOTE_AUTHORITY` / `QUARANTINE` / `DELETE`).
- `04_workspace_hygiene/PURGE_CATEGORIES.md` defines what classes of file get
  purged (e.g., `*.bak.*` plists older than N days, untracked top-level temp
  files matching specific patterns, abandoned worktrees with no commits in N
  days). Each rule cites a real example currently present.
- `05_execution_packets/PACKET_INDEX.md` lists at least four follow-up
  implementation packets with explicit ordering and dependency edges.
- `99_verification/REGRESSION_PROBE_SUITE.md` defines probes that v_next must
  pass and that the maintenance agent must pass (dry-run, allowlist boundary,
  rollback, etc.).
- A critic pass returns `APPROVE`. `REVISE` is not acceptance.

## Stop Conditions

- Stop if any track tries to ship implementation in this packet. Implementation
  belongs to packets enumerated under `05_execution_packets/`.
- Stop if `UNIVERSAL_TOPOLOGY_DESIGN.md` references Zeus pipeline stages,
  domain entities, or file paths in its universal section.
- Stop if `HIDDEN_BRANCH_LESSONS.md` declares any prior iteration "wrong"
  without naming what that iteration shipped that v_next must absorb.
- Stop if the maintenance agent design omits dry-run, kill switch, or
  evidence-trail logging.
- Stop if `DRIFT_ASSESSMENT.md` proposes a doc-update action that would
  silently change a runtime authority without companion test/probe coverage.
- Stop if the workspace-hygiene rules propose deletion of any closed packet
  whose evidence is still load-bearing per `00_evidence/`.

## Verification Plan

```bash
python3 scripts/topology_doctor.py --navigation \
  --task "operation planning packet: runtime improvement engineering" \
  --intent create_new --write-intent docs \
  --files docs/operations/task_2026-05-15_runtime_improvement_engineering_package/PLAN.md \
          docs/operations/AGENTS.md --json
python3 scripts/topology_doctor.py --planning-lock \
  --changed-files docs/operations/task_2026-05-15_runtime_improvement_engineering_package/PLAN.md \
                  docs/operations/AGENTS.md \
  --plan-evidence docs/operations/task_2026-05-15_runtime_improvement_engineering_package/PLAN.md
python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory \
  --changed-files docs/operations/task_2026-05-15_runtime_improvement_engineering_package/PLAN.md \
                  docs/operations/AGENTS.md
git diff --check
```

## Execution Handoff Shape

- Probe lane (haiku): produces `00_evidence/` (read-only enumeration).
- Hidden-branch synthesis lane (sonnet): produces `01_topology_v_next/`
  (HIDDEN_BRANCH_LESSONS, UNIVERSAL_TOPOLOGY_DESIGN, ZEUS_BINDING_LAYER,
  MIGRATION_PATH).
- Maintenance-agent design lane (orchestrator): produces
  `02_daily_maintenance_agent/` and `04_workspace_hygiene/`. Depends on probe
  output for concrete examples.
- Drift remediation lane (orchestrator + sonnet read-only crawl): produces
  `03_authority_drift_remediation/`.
- Packet planner lane (orchestrator): produces `05_execution_packets/` and
  `99_verification/`.
- Critic lane: attacks Zeus-leak in universal design, optimism on drift
  remediation, and any maintenance-agent rule that would touch
  `src/architecture/state/credentials/hooks/plists`.

The leader (orchestrator) owns synthesis and final acceptance verdict.
