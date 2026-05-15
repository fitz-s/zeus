# Operations Packet Archival Rules

Status: SPEC
Companion: `PURGE_CATEGORIES.md` (Category 4 names the rule;
this file specifies the criteria, evidence, and exemption logic in detail)

## Why Archival Is Hard

A closed packet is not the same as a dead packet. The
`CLOSED_PACKET_STILL_LOAD_BEARING` friction pattern (see sibling audit packet)
shows that runtime, current authority docs, and even active hooks frequently
import or cite evidence from packets the human considers "closed." Naive
archival breaks the runtime silently and the agent later cites the missing
file as "this never existed."

This file specifies the criteria that distinguish a packet whose evidence is
truly post-load-bearing from one that is merely past its activity window.

## Archival Verdicts

Every packet directory under `docs/operations/task_*/` receives one verdict
on each maintenance run:

- `ACTIVE`: modified within last 30 days OR contains `Status: ACTIVE_LAW` /
  `Status: AUTHORITY` / `Status: IN_PROGRESS`
- `WINDING_DOWN`: not modified 30–60 days, no AUTHORITY status, but at least
  one current-authority file references a path inside it
- `ARCHIVE_CANDIDATE`: not modified 60+ days, no AUTHORITY status, and
  passes ALL exemption checks below
- `LOAD_BEARING_DESPITE_AGE`: would otherwise be ARCHIVE_CANDIDATE, but at
  least one exemption check failed; this packet stays in place AND a row is
  written to `LOAD_BEARING_REGISTRY.md` so the human knows it cannot be
  archived without first cutting the load-bearing link
- `ALREADY_ARCHIVED`: a `<name>.archived` stub exists at the original path

Only `ARCHIVE_CANDIDATE` packets move under any agent action. All others are
left in place.

## Exemption Checks (ALL must pass to archive)

For each candidate packet `P = docs/operations/task_<date>_<slug>`:

0. **Authority Status Registry check (priority — runs FIRST).** Look up
   the packet's canonical path in the active `artifact_authority_status`
   registry introduced by `01_topology_v_next/UNIVERSAL_TOPOLOGY_DESIGN.md`
   §13 and populated by `01_topology_v_next/ZEUS_BINDING_LAYER.md` §8.
   If the path appears in the registry AND the status is NOT one of
   `{ARCHIVED, CURRENT_HISTORICAL}` (or `CURRENT_HISTORICAL` only when the
   row also carries an explicit `archival_ok: true` flag), classify the
   packet as `LOAD_BEARING_DESPITE_AGE` immediately and SKIP checks 1–8.
   The registry is the authoritative source of truth for load-bearing status;
   the 8 heuristic checks below are a fallback for packets not yet registered.
   If the registry file is absent or unreadable, log a WARNING and continue
   to check 1 (do NOT silently treat absence as "not registered").

1. **Authority status check**: grep PLAN.md / README.md first 50 lines for
   `Status: AUTHORITY` | `Status: ACTIVE_LAW` | `Status: AUTHORITATIVE`. If
   present → `LOAD_BEARING_DESPITE_AGE`.
2. **Reference replacement check**: scan
   `architecture/reference_replacement.yaml` for any entry whose `source`
   path is under `P/`. If present → `LOAD_BEARING_DESPITE_AGE`.
3. **Docs registry check**: scan `architecture/docs_registry.yaml` for any
   entry whose `path` is under `P/`. If present → `LOAD_BEARING_DESPITE_AGE`.
4. **Code reference grep**: `git grep -l "task_<date>_<slug>"` across `src/`,
   `scripts/`, `tests/`, `architecture/`. Any non-self hit →
   `LOAD_BEARING_DESPITE_AGE`.
5. **Active packet citation**: grep all packets matching `task_*` modified in
   the last 30 days for references to `P/`. Any hit →
   `LOAD_BEARING_DESPITE_AGE`.
6. **Open PR check**: list open PRs (`gh pr list --state open --json
   files,number`); if any open PR touches a file inside `P/` →
   `LOAD_BEARING_DESPITE_AGE`.
7. **Hook / launchd citation**: grep `.claude/settings.json`,
   `.codex/hooks.json`, `~/Library/LaunchAgents/com.zeus.*.plist` for any
   path containing `task_<date>_<slug>`. Any hit → `LOAD_BEARING_DESPITE_AGE`.
8. **Worktree branch check**: list `git worktree list`; if any worktree's
   branch name contains `<slug>` → `LOAD_BEARING_DESPITE_AGE`.

A packet must pass all eight checks to become `ARCHIVE_CANDIDATE`. Any single
failure flips it to `LOAD_BEARING_DESPITE_AGE`.

## Archive Move Procedure

For an `ARCHIVE_CANDIDATE` packet:

1. Compute target path:
   `docs/operations/archive/<YYYY>-Q<1-4>/<original-name>/`
2. Verify target does not exist; if collision, append `.duplicate-<N>`.
3. `git mv` the directory tree under git so history is preserved.
4. Create stub at original path:
   ```
   docs/operations/<original-name>.archived  (single file, ~12 lines)
   ---
   archived_to: docs/operations/archive/<YYYY>-Q<1-4>/<original-name>/
   archived_at: <ISO date>
   archived_by: maintenance_agent
   last_modified_before_archive: <ISO date>
   exemption_checks_passed: 9/9  # check #0 (registry) + checks 1-8
   reference_grep_count: 0
   restore_command: git mv docs/operations/archive/.../<name>/ docs/operations/<name>/
   ---
   ```
5. Add a row to `02_daily_maintenance_agent/evidence_trail/<date>/archived.tsv`:
   `<original-name>\t<archived-to>\t<exemption-checks-passed>`
6. The `git mv` and stub-write happen in a single commit on a dedicated
   branch named `maintenance/archive-<YYYY-MM-DD>`. The commit message lists
   every archived packet. PR opened against main with `[maintenance]` tag.

The agent does NOT merge the PR. Human merges after spot-check.

## Restore Procedure

If a future search hits the `.archived` stub instead of content, the agent
(or human) follows the `restore_command`. Restore is reversible by re-running
the archive procedure on next tick once the load-bearing link is found and
either cut or documented.

## Quarterly Hard Sweep

A separate `quarterly_archive_sweep` task runs once per quarter, NOT daily.
It re-evaluates all packets older than 180 days and proposes a second-tier
move from `archive/<YYYY>-Q<N>/` to `archive/cold/<YYYY>/`. Cold archival
adds a `cold_archived: true` field to the stub but otherwise behaves
identically.

The agent never deletes packets. Deletion of `cold/` archives is an explicit
human action gated outside the agent.

## Special Case: Wave Packets

The wave-packet pattern (`task_*_wave[0-9]+`) is common in Zeus. Wave packets
share evidence within the same wave family. Treat them as an ATOMIC GROUP:
all wave packets in a family are archived together, OR none. The exemption
checks above run against the union of paths in all wave packets sharing the
same family slug.

## Acceptance For This Spec

A maintenance-agent run that proposes an archive without all nine exemption
checks (check #0 registry + checks 1–8) recorded in its evidence trail is a
regression. The DRY_RUN_PROTOCOL must surface the per-check outcome to the
human, not just the final verdict.
