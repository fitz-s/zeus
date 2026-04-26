# Operations Archive Deferrals — 2026-04-24

Created: 2026-04-24
Last audited: 2026-04-26
Authority basis: `docs/operations/` packet audit 2026-04-24 (23 packets
triaged; Sonnet audit report routed through team-lead).
Status: operational evidence / task queue (NOT authority)

## 2026-04-26 status update

| ID | Status as of 2026-04-26 | Source |
|---|---|---|
| **D1** TIGGE GRIB ingest | OPEN — operator decision still pending | unchanged from 2026-04-24 |
| **D2** Source-attestation | OPEN — operator decision still pending | unchanged from 2026-04-24 |
| **D3** venue_commands spine | **ABSORBED by `zeus-pr18-fix-plan-20260426`** — P1 packet open; P1.S1 schema landed at `0a7845f`, closeout at `7ebed4e` | live worktree HEAD verified 2026-04-26 |
| **D4** Archive cold-storage | **CLOSED** — `docs/archives/{packets,bundles,...}/` + `docs/archive_registry.md` both present | `ls docs/archives/` + `ls docs/archive_registry.md` |
| **D5** current_state.md trim | **NEAR-CLOSED** — `current_state.md "Other operations surfaces"` is already minimal (2 redirect lines); folded into next hygiene pass | direct read |

Companion audit: `docs/operations/task_2026-04-26_live_readiness_completion/evidence/audit_2026-04-26.md`.

Only **D1 + D2** remain awaiting explicit operator ruling. D3/D4/D5 are no longer load-bearing on this workbook.

## Purpose

This file captures every NEEDS_OPERATOR_DECISION and
pending-implementation item surfaced during the `docs/operations/`
packet audit that cannot be archived without explicit operator ruling
or additional engineering work. Companion to
`zeus_midstream_fix_plan_2026-04-23.md §"Out-of-plan deferrals"` —
this file covers items NOT in the midstream workbook.

**This is a task queue, not durable law. Do not promote items here
to authority by reference. When an item closes, record its closure
in the owning packet / receipt / lore card.**

---

## D1 — TIGGE GRIB ingest (#52 + #53)

**Source**: `docs/operations/task_2026-04-13_remaining_repair_backlog.md`
items #52 / #53 (DB/Rebuild/Calibration Dependent section).

**Scope**: The TIGGE ensemble forecast GRIB extraction + ingest pipeline
is declared in the backlog but was never implemented. Gate F data
backfill (`task_2026-04-21_gate_f_data_backfill`) closed conditionally
without this work. The `ensemble_snapshots_v2` table remains
substantively empty for TIGGE-sourced ensembles, blocking Phase-7 v2
substrate rebuild.

**Blocker**: implementation gap, not external dependency. No live
TIGGE GRIB data pipeline exists. Manual one-off cloud-pull harness
was built during data-readiness tail but not promoted to scheduled
ingest.

**Operator decision**: (a) forward to a dedicated new packet
`task_2026-04-XX_tigge_grib_ingest/` with plan + receipt; OR (b)
formally retire as OBSOLETE if Zeus is pivoting to a different
ensemble source; OR (c) defer indefinitely with explicit tombstone.

**Estimated size**: 12-20h for option (a) — GRIB library choice +
scheduled harness + provenance wiring + v2 snapshot backfill.

**Unblock**: operator ruling.

**Cross-reference**:
- `docs/artifacts/tigge_cloud_wiring_snapshot_2026-04-19.md`
- `docs/artifacts/tigge_data_training_handoff_2026-04-23.md` (new,
  created 2026-04-23; not yet routed into a packet)

---

## D2 — Source-attestation package

**Source**: `docs/operations/task_2026-04-13_remaining_repair_backlog.md`
"Source attestation section".

**Scope**: Monitor helpers + harvester closed-event polling paths
would benefit from explicit source-attestation guards (schema-drift
detection on Gamma payload shapes, UMA-vote conformance, resolution-
timestamp provenance). Backlog notes this was flagged as needing a
dedicated package "if they must defend against malformed Gamma
payloads".

**Partial precedent**: DR-33-A `_find_winning_bin` UMA-vote gate
(commit in `task_2026-04-23_live_harvester_enablement_dr33`) is the
first structural antibody in this family. It proves the pattern but
covers only one call site.

**Blocker**: scoping decision. A source-attestation package would
audit every external-API-consuming site (monitor refresh, harvester,
Gamma fetch, CLOB orderbook reads) and add schema-drift gates.

**Operator decision**: (a) open a dedicated `task_2026-04-XX_source_
attestation/` packet to extend the DR-33-A pattern to all external-API
consumers; OR (b) add source-attestation hardening as an inline
responsibility of each feature slice that touches external APIs (no
dedicated packet).

**Estimated size**: 6-10h per external-API consumer × ~5 consumers = 30-50h for option (a).

**Unblock**: operator ruling + scoping decision.

---

## D3 — Execution-state truth upgrade (venue_commands spine)

**Source**: `docs/operations/task_2026-04-19_execution_state_truth_upgrade/`
(76K of planning documents — project_brief, prd, decisions,
implementation_plan, verification_plan, architecture_note, not_now).

**Scope**: 4-phase roadmap to turn Zeus execution from "sometimes infers
what happened" → "proves what it knows, admits what it does not know,
survives restart/recovery without inventing truth":
- **P0**: harden current live behavior (absorbed by midstream remediation
  — position authority, chain reconciliation, RED sweep)
- **P1**: durable execution command truth — `venue_commands` +
  `venue_command_events` schema with submit-before-side-effect
  discipline. **NOT IMPLEMENTED**.
- **P2**: close the semantic loop — commands → events → position state
  transitions with no orphan paths. **NOT IMPLEMENTED**.
- **P3**: outer decision law refactor — may follow P2. **NOT IMPLEMENTED**.

**Status**: The packet never exited planning-lock. Files present:
architecture_note.md, decisions.md, implementation_plan.md,
not_now.md, prd.md, project_brief.md, verification_plan.md. No
work_log.md, no receipt.json.

**What absorbed P0**: midstream remediation (T1–T6) + data-readiness
remediation (P-A through P-H) together cover the P0 "harden current
live behavior" scope. T4 family (DecisionEvidence symmetric contract)
is adjacent to P1 but does NOT create the `venue_commands` spine.

**What is still open**: P1 (venue_commands schema + submit-before-
side-effect discipline) + P2 (semantic loop closure).

**Operator decision**: (a) forward to a new
`task_2026-04-XX_venue_commands_spine/` packet with fresh plan,
adopting the existing planning docs as inputs; OR (b) formally
tombstone the packet with an explicit "absorbed / superseded by
midstream+data-readiness" note, accepting that P1/P2 are indefinitely
deferred; OR (c) keep the current packet in place with a "PLANNING
ONLY — NEVER IMPLEMENTED" banner and defer until operational priority
shifts.

**Estimated size**: Option (a) full P1+P2 implementation = 40-80h
(large architectural slice; affects order lifecycle, chain
reconciliation, exit authority); option (b) tombstone = 0.5h;
option (c) banner = 0.5h.

**Unblock**: operator ruling.

**Durable lore already extracted**: The 4-phase project_brief
framing ("harden → command truth → semantic loop → outer law") is a
reusable methodology for hardening asynchronous execution systems.
Extracted as lore card `EXECUTION_STATE_TRUTH_FOUR_PHASE_PROGRESSION`
(see `architecture/history_lore.yaml`).

---

## D4 — Archive-body cold-storage protocol

**Source**: `docs/operations/` packet audit 2026-04-24 — 21 packets
classified ARCHIVE_NOW (15) or ARCHIVE_AFTER_LORE_EXTRACT (6), total
~5.5M of closed-but-on-disk content.

**Scope**: Per AGENTS.md `§History`, archived packets move to "local
historical cold storage, not default-read, not peer authority".
Concrete implementation options:
- (a) **Move to `docs/archives/packets/YYYY-MM-DD_<name>/`**: retain
  full bodies under a known archive path; `docs/archive_registry.md`
  indexes them. Lowest data-loss risk; uses ~5.5M tracked disk but
  keeps content browsable for forensic work.
- (b) **Tarball to `docs/archives/bundles/` + delete originals**:
  compress each archived packet into a single `.tar.gz`, retain the
  bundle file, delete the original directory. Saves disk; trades
  browsability for compression.
- (c) **Out-of-tree cold storage**: move archive bodies outside the
  git repo entirely, keep only the lore cards + archive_registry
  metadata in-tree. Maximum repo hygiene; requires operator to
  maintain separate archive disk/drive.

**Recommendation**: (a) for Batch 1 (15 low-lore ARCHIVE_NOW packets)
is safe + trivial; consider (b) or (c) for Batch 2 (6 high-lore
packets totaling ~5.1M) after lore cards are extracted.

**Operator decision**: pick (a), (b), or (c); OR blend by batch.

**Unblock**: operator ruling.

**Estimated size**: Option (a) = 2h (move + update registry);
option (b) = 3h (tar + move + update); option (c) = 4h (cold-storage
path setup + move + registry + git-ignore conventions).

---

## D5 — Completed-packet-visible-in-current_state trim

**Source**: `docs/operations/current_state.md §"Other operations
surfaces"` "Visible non-default packet evidence" section — lists 17
packets that are all CLOSED per audit, plus 2 ACTIVE packets.

**Scope**: Once Batch 1 + Batch 2 archive complete, trim
`current_state.md §"Visible non-default packet evidence"` to only
list ACTIVE packets (`task_2026-04-23_midstream_remediation` and
`task_2026-04-23_graph_rendering_integration`) + point to
`docs/archive_registry.md` for historical packets.

**Blocker**: depends on D4 archive path decision.

**Estimated size**: 0.5h (mechanical trim + reference redirection).

**Unblock**: D4 resolved.

---

## Meta: pending operator decisions summary

| Item | Decision type | Blocker | Estimated size |
|---|---|---|---|
| D1 TIGGE GRIB ingest | forward vs retire vs defer | scoping + external dependency on ensemble pipeline | 12-20h (a) / 0 (b,c) |
| D2 Source-attestation | dedicated packet vs inline | scoping | 30-50h (a) / 0 (b) |
| D3 venue_commands spine | packet vs tombstone vs banner | scoping + effort | 40-80h (a) / 0.5h (b,c) |
| D4 Archive cold-storage | path choice (a/b/c) | preference | 2-4h |
| D5 current_state trim | mechanical post-D4 | depends on D4 | 0.5h |

All five items await explicit operator ruling. None is code-locked;
each resolves with a scoping decision + follow-up execution work sized
per the decision.

## Cross-references

- Audit source: Sonnet-driven triage report (in-session, 2026-04-24).
- Parent task queue: `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md`
  §"Out-of-plan deferrals surfaced during execution" — covers midstream-
  adjacent deferrals; this file covers non-midstream operations
  deferrals.
- Authority for closure criteria: `docs/operations/AGENTS.md §History`
  — defines the archive-bodies / history_lore / archive_registry triad.
- Lore extraction: `architecture/history_lore.yaml` — 13 new cards
  added 2026-04-24 from the audit.
