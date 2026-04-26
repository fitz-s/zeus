# Packet Scope Protocol

> Created: 2026-04-25
> Owner packet: `task_2026-04-25_p2_packet_runtime`
> Status: in effect

This document is the human-readable contract for the **Zeus Packet Runtime**
(`zpkt`) and its `scope.yaml` sidecar. It explains the *why* and the *workflow*;
the machine-readable contract is `architecture/scope_schema.json`. The CLI
itself is `scripts/zpkt.py`; the soft-warn pre-commit hook is
`.zeus-githooks/pre-commit`.

If you only have time to read one section, read **§3 Daily workflow**.

## 1. What problem this protocol solves

Five repeated friction patterns were measured during the topology-admission
hardening session (full evidence: `process_friction_audit.md`,
`bureaucracy_audit.md`):

1. **Pre-task discovery tax.** Five separate `topology_doctor` invocations
   (navigation, task-boot-profiles, planning-lock, map-maintenance,
   code-review-graph-status) at ~1.6 s cold-start each.
2. **Scope drift.** Packet scope existed only in prose; git could not see it,
   so unrelated edits silently slipped into commits.
3. **Manifest sideledger toil.** 14 hand-maintained YAML manifests had to be
   updated for any non-trivial src change.
4. **Closeout four-leaf bookkeeping.** Each landing touched ~9 files in 4
   directories.
5. **Multi-packet contention.** A single working tree forced `git stash`
   gymnastics whenever a second packet appeared (16 historical stash-pollution
   episodes).

The Packet Runtime collapses all five into one CLI surface and one sidecar
file. It is **soft-warn first by design** — nothing it does ever blocks a
commit on its own.

## 2. The contract

### 2.1 `scope.yaml`

Every independent package folder under `docs/operations/task_*_*/` may carry a
`scope.yaml` file. Multi-phase packages keep phase-local scopes under
`docs/operations/task_*_*/phases/task_*_*/scope.yaml` rather than creating
sibling top-level packages for each phase. Scope files conform to
`architecture/scope_schema.json`:

```yaml
$schema: ../../../architecture/scope_schema.json
schema_version: 1
packet: task_YYYY-MM-DD_<slug>
status: in_progress | landed | closed
branch: <branch-name>
worktree: <absolute path>

in_scope:                # files this packet may modify (globs allowed)
  - scripts/foo.py
  - tests/test_foo.py

allow_companions:        # registry/mesh updates that are permitted
  - architecture/script_manifest.yaml
  - architecture/test_topology.yaml

out_of_scope:            # explicit deny list (production DBs, etc.)
  - state/zeus-world.db
```

### 2.2 Bucket classification

When the soft-warn hook (or `zpkt status`) inspects a staged file, it routes
it into exactly one of four buckets:

| Bucket          | Meaning                                                   |
|-----------------|-----------------------------------------------------------|
| `in_scope`      | Matches an `in_scope` glob — silent, ideal case           |
| `companion`     | Matches `allow_companions` — silent, mesh maintenance     |
| `out_of_scope`  | Matches `out_of_scope` — **warn**                         |
| `unscoped`      | Matches none of the lists — **warn**                      |

Warnings print remediation guidance and exit with status **0** (soft-warn).

### 2.3 The bypass trailer

When the agent intentionally lands an out-of-scope or unscoped change, it
documents the decision with a Git commit trailer:

```
Pscb-Bypass: <one-line reason>
```

`zpkt audit-bypass` scans the commit log for these trailers, producing a
monthly digest. Trailers are advisory; nothing in the runtime requires them.
They exist so monthly bypass volume can be measured and the protocol tightened
later if needed.

## 3. Daily workflow

### 3.1 Starting a new packet

```
zpkt start <slug>
```

Effect:
1. Creates `docs/operations/task_<UTC-date>_<slug>/` containing `plan.md`,
   `scope.yaml`, and `work_log.md`.
2. **Creates a new git worktree as a sibling directory** (`zeus-<slug>`) on a
   fresh branch (`p2-<slug>`).
3. Writes `.zpkt-active` pointing at the new packet.

This worktree isolation is the single biggest win — two packets never share a
working tree, so concurrent agent work never produces stash pollution.

If you must reuse the current worktree (rare; consult plan first):
`zpkt start <slug> --inplace`.

### 3.2 During the packet

```
zpkt status                     # one-call digest, 5-min cached
zpkt scope add <files>          # widen in_scope as you discover deps
zpkt scope add <files> --kind allow_companions
zpkt commit -m "<msg>" [files]  # stage + commit with soft-warn
```

`zpkt status` collapses the legacy 5-doctor preamble. Running it a second time
within 5 minutes returns from `.zpkt-cache/status.json`, reducing cold-start
re-reads to near-zero.

### 3.3 Closing a packet

```
zpkt close
```

Effect:
1. Flips `scope.yaml` `status` from `in_progress` to `landed`.
2. Writes `receipt.json` summarising branch, head, scope, and commit log.
3. Idempotently appends a single line to
   `docs/operations/current_state.md` (marker comment dedupes re-runs).

The agent is *not* asked to manually compute a derived manifest fan-out;
`receipt.json` is the canonical evidence of landing.

### 3.4 Parking and resuming

When two packets must be alive in the same worktree (legacy edge case):

```
zpkt park   --packet <other> --message "<wip note>" [files]
zpkt unpark --packet <other>
```

These wrap `git stash push --include-untracked` with structured labels so
multiple parked packets coexist safely — replacing the historical bare-stash
pattern that lost work in 16 historical incidents.

## 4. Enforcement levels

The runtime is **soft-warn only** at this protocol's effective date.

| Level         | What hooks do                                          | When it activates |
|---------------|--------------------------------------------------------|-------------------|
| Soft-warn     | Print warning + remediation; exit 0                    | **Now (default)** |
| Opt-in block  | Repo flag enables hard block; agents may opt in early  | Future packet     |
| Hard block    | Out-of-scope commits rejected unless `Pscb-Bypass:`    | After ≥1 month of soft-warn telemetry shows clean adoption |

Promotion from soft-warn to hard-block requires a dedicated packet that:
- Reviews the `zpkt audit-bypass` log for the prior month
- Establishes a documented bypass-rate threshold
- Updates this protocol document accordingly

## 5. Installation

A one-time per-clone setup configures Git to use the in-repo hooks:

```
zpkt setup     # equivalent to: git config core.hooksPath .zeus-githooks
```

The hooks ship in `.zeus-githooks/`, are tracked in git, and review-friendly.
Do not symlink or copy them into `.git/hooks/` — that bypasses review.

## 6. Out of scope for this protocol

The following are intentionally **not** governed by `zpkt` and are tracked as
follow-up packets:

- Wholesale migration of historical packets to `scope.yaml`
- AGENTS.md de-bloat
- `topology_doctor` daemon mode (sub-100ms cold-start)
- Hard-block enforcement (see §4)

## 7. Related references

| Source                                       | Role                              |
|----------------------------------------------|-----------------------------------|
| `scripts/zpkt.py`                            | CLI implementation                |
| `scripts/_zpkt_scope.py`                     | scope load/match helpers          |
| `.zeus-githooks/pre-commit`                  | soft-warn enforcement hook        |
| `architecture/scope_schema.json`             | machine-readable schema           |
| `architecture/script_manifest.yaml`          | `zpkt.py` runtime classification  |
| `architecture/test_topology.yaml`            | test registration (tooling lane)  |
| `tests/test_zpkt.py`, `tests/test_pscb_hook.py` | behavioural contracts          |

## 8. Glossary

- **PSCB** — *Packet Soft-Commit Boundary*, the umbrella name for the
  scope-aware commit pipeline. The pre-commit hook is the PSCB hook.
- **Companion** — a registry/mesh file changed as a side-effect of an
  in-scope edit (e.g., `architecture/script_manifest.yaml` when a new script
  is added).
- **Bypass trailer** — `Pscb-Bypass: <reason>` line in a commit message,
  documenting that the agent intentionally landed out-of-scope work.
