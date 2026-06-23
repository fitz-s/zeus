# Zeus Archival And Evidence Isolation Rules

Status: active durable archival authority  
Scope: packet/archive/evidence/report demotion, quarantine, stubs, and default-read isolation  
Freshness model: durable rule. Individual current facts and packet status remain expiry-bound elsewhere.

---

## 1. Purpose

Archival in Zeus is not cosmetic. A closed packet, dated consult, PR review, rebuild diary, or evidence report can still contain useful history, but it must not sit in the authority/reference/default-boot path where a zero-context agent can restore an obsolete worldview.

This file defines when historical material is moved, stubbed, indexed, or quarantined. It does not authorize deletion of evidence and it does not define live system behavior.

---

## 2. Layer Verdicts

Every doc-like file that can influence agent cognition must be classed as one of:

- `durable_authority_law`: active law under `docs/authority/**`.
- `canonical_reference`: durable reference under `docs/reference/**`.
- `current_fact_pointer`: expiry-bound current fact under `docs/operations/current_state.md`, `current_data_state.md`, or `current_source_validity.md`.
- `runbook`: procedural operation under `docs/runbooks/**`.
- `machine_manifest`: machine-checkable yaml/registry under `architecture/**`.
- `evidence`: raw evidence, receipt, audit, measurement, or diagnostic artifact.
- `report`: interpreted historical report, review, closeout, or audit summary.
- `packet`: active or closed work packet.
- `archive`: historical material retained for discoverability.
- `quarantined_pollution`: a file demoted because its old path/class made agents likely to treat stale claims as authority.
- `obsolete_dead`: retained only to explain that an old branch/feature/path is dead.

Only the first five classes may appear in default boot, and only when the router says so. `current_fact_pointer` is default-readable only as a pointer, not as durable law.

---

## 3. What Must Leave Authority Or Reference

Move or demote these out of active authority/reference unless the durable rule has been promoted into current law/reference:

- dated strategy-of-record documents;
- consult raw transcripts;
- PR reviews or critic/verifier reports;
- packet plans, closeouts, work logs, task diaries;
- one-off statistical/audit snapshots;
- evidence dumps;
- current operational facts without freshness/expiry;
- historical deploy status such as loaded SHA, PID, bankroll, active positions, transient reject counts;
- docs that present legacy ENS/Platt/market_fusion, AIFS hard dependency, q_lcb_5pct, submit-disabled, shadow-only, old lifecycle strings, old bankroll/caps, or packet freeze claims as current law.

If a demoted file contains a still-valid rule, promote the rule first. The source remains evidence/history and must not be default-read.

---

## 4. Packet Archival Verdicts

For packet directories under `docs/operations/task_*` or equivalent current-work homes:

- `ACTIVE`: current open packet, explicitly named by `docs/operations/current_state.md`, with freshness/evidence.
- `CURRENT_POINTER_ONLY`: the current-state pointer names the packet, but agents may not default-read the whole directory.
- `WINDING_DOWN`: recently closed or partially referenced; not default-read.
- `LOAD_BEARING_HISTORY`: code/manifests/tests/reference still cite it. Keep discoverable, but cut default routes and promote any durable rule.
- `ARCHIVE_CANDIDATE`: not active, not load-bearing after checks, safe to move to archive/report/evidence.
- `QUARANTINE_CANDIDATE`: old class/path makes false authority likely; move or reindex immediately even if raw content remains useful.
- `ALREADY_ARCHIVED`: original path is a stub pointing to archive/report/evidence.

Closed packet evidence must not be read during default boot merely because it remains load-bearing history.

---

## 5. Exemption Checks Before Moving A Packet

Before moving a packet directory, check:

1. Is it named by a fresh current-state pointer?
2. Is it referenced by `architecture/docs_registry.yaml`, `reference_replacement.yaml`, `module_manifest.yaml`, `task_boot_profiles.yaml`, `fatal_misreads.yaml`, or scoped AGENTS?
3. Is it cited by active authority/reference docs?
4. Is it imported or opened by source, scripts, tests, hooks, launchd, or CI?
5. Does it contain status markers `AUTHORITY`, `ACTIVE_LAW`, `IN_PROGRESS`, or similar?
6. Does it contain surviving durable law that has not yet been promoted?
7. Does an open PR or branch actively modify it?
8. Would moving it break a current runbook, operator receipt, or validation script?
9. Is there an archive/report/evidence index row or stub plan for discoverability?

If any check blocks a move, cut or update the load-bearing link first. Do not leave the file in default boot as a workaround.

---

## 6. Move And Stub Procedure

Preferred move forms:

- closed packet -> `docs/archive/<YYYY>-Q<N>/<original-name>/`;
- interpreted historical report -> `docs/reports/<topic>/<name>.md`;
- raw measurement/audit evidence -> `docs/evidence/<topic>/<name>`;
- authority/reference pollution -> `docs/reports/authority_history/<name>.md` or `docs/archive/<YYYY>-Q<N>/authority_pollution/<name>.md`.

Use `git mv` where possible. When only a contents API is available, create the target file, delete the old path, and record the demotion in `docs/archive_registry.md` or the active archive/report index.

A stub is allowed only when repo policy or link preservation requires it. Stubs must be short and must state:

- old path;
- new path;
- demoted class;
- reason it is not authority;
- date of demotion;
- active replacement authority/reference.

A stub under an active route must itself say `archive-only` and must not restate obsolete present-tense claims.

---

## 7. Archive Registry Requirements

Every demotion must record:

- old path;
- new path or deletion/discoverability note;
- old claimed class;
- actual class after inspection;
- default-read after demotion (`false` unless a pointer stub is required);
- reason for demotion;
- active replacement law/reference, if any;
- evidence date / demotion date.

The registry is an index, not authority. It helps agents find history without default-reading it.

---

## 8. Deletion Rule

Agents do not delete historical evidence for convenience. Deletion is allowed only for duplicate generated artifacts, empty stubs, or files whose content is preserved elsewhere and whose deletion is explicitly recorded. Human/operator review is required for irreversible purge of evidence archives.

---

## 9. Acceptance Criteria

An archival/demotion patch is accepted only if:

1. no demoted evidence/report/archive path remains in default AGENTS/README/registry route;
2. active authority/reference still contains the surviving durable truth;
3. current facts have freshness/expiry semantics or are marked unknown;
4. archive/report/evidence remains discoverable through registry/index;
5. validation or search confirms stale terms are not present-tense law in default-read authority/reference/router files.
