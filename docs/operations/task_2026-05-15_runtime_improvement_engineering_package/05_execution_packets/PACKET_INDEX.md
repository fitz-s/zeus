# Execution Packet Index

Status: PLAN
Purpose: ordered, dependency-tagged list of follow-up implementation packets
that ship the work specified by tracks 01–04. This packet does NOT execute
any of them; it specifies the order, dependencies, and entry/exit criteria
so future packets can be planned and admitted without re-relitigating scope.

## Packet Naming Convention

`task_<YYYY-MM-DD>_<slug>` per repo convention. Slugs below are forward-
looking; the actual creation date is the day the implementation packet
opens.

## Dependency Graph

```
P1 (topology_v_next_phase1_additive) ──┐
  [additive parallel route]            ├─► P3 (topology_v_next_phase2_shadow) ──► P4 (topology_v_next_phase3_cutover_pilot)
P2 (companion_required_mechanism) ─────┘                                                      │
  [runs alongside P1; both                                                                    ▼
   exercised together in P3]      P5 (maintenance_worker_core) ──► P6 (maintenance_worker_zeus_binding) ──► P7 (lore_indexer_and_proposal_promoter)
                                                                              │
                                                                              ▼
                                              P8 (authority_drift_3_blocking_remediation) ──► P9 (authority_inventory_v2)

P10 (topology_doctor_module_consolidation) — after P4 cutover ships first;
                                              consolidating against a moving
                                              target is regression risk.
```

## Packets

### P1 — `topology_v_next_phase1_additive`

**Goal**: implement v_next as a parallel route layer; current admission
remains authoritative; capture per-call divergence.

**Inputs**:
- `01_topology_v_next/UNIVERSAL_TOPOLOGY_DESIGN.md` §3 (admission unit), §4
  (matching algorithm), §11 (output normalization)
- `01_topology_v_next/ZEUS_BINDING_LAYER.md` §1–§4 (project identity,
  intent extensions, hard safety kernel, coverage map)
- `01_topology_v_next/MIGRATION_PATH.md` Phase 1 entry/exit criteria

**Scope**:
- New CLI flag `--v-next-shadow` on `topology_doctor.py` that runs both old
  and new admission, returns OLD result, logs both to a divergence file.
- New module `scripts/topology_v_next/` containing the v_next implementation.
- No change to existing admission output. Existing callers see no behavior
  change.

**Out of scope**: shadow blocking, cutover, deletion of any existing rule.

**Acceptance**:
- 7 days of `--v-next-shadow` logs with ≥100 admission calls
- Per-call divergence report categorized by friction pattern
- Zero regression in existing topology_doctor tests
- Zero new BLOCKING checks added to global health

**Dependency**: none (entry point).

**Estimated size**: 1500–2500 LOC.

---

### P2 — `companion_required_mechanism`

**Goal**: implement the `companion_required:` admission mechanism specified
in `03_authority_drift_remediation/REMEDIATION_PLAN.md`. This converts
authority-doc drift from a "find later" problem to a "block at write time"
problem.

**Inputs**:
- `03_authority_drift_remediation/REMEDIATION_PLAN.md` § Companion-Update
  Enforcement
- `01_topology_v_next/UNIVERSAL_TOPOLOGY_DESIGN.md` §9 (Companion-Loop-Break)

**Scope**:
- Profile schema gains `companion_required:` and
  `companion_skip_acknowledge_token:` fields.
- v_next admission checks the union of changed paths against
  `allowed_files` AND `companion_required`; if either is missing the
  admission emits a structured `MISSING_COMPANION` failure with the named
  doc path.
- 3–5 starting profiles get `companion_required:` populated (the high-risk
  ones identified in DRIFT_ASSESSMENT Cohort 4).

**Acceptance**:
- Probe: editing covered source without companion produces a clear failure
  message naming the missing doc.
- Probe: same edit WITH companion-doc edit admits cleanly.
- Probe: skip-token usage is logged to a human-review queue.

**Dependency**: P1 (additive parallel route) must ship; P2 implements
companion-required as part of v_next's admission and runs as additive logic
alongside P1; P3 then runs both P1 and P2 in shadow mode.

**Estimated size**: 800–1200 LOC.

---

### P3 — `topology_v_next_phase2_shadow`

**Goal**: v_next can advise but not block; current is still authoritative.
Per-call divergence drives the cutover decision in P4.

**Inputs**: P1 output, MIGRATION_PATH.md Phase 2.

**Scope**:
- v_next emits `advisory:` field in its output alongside the old
  `admit/blockers` shape.
- Tool result shape normalization: `ok`, `decision`, `advisory`, `blockers`
  fields mandatory per UNIVERSAL_TOPOLOGY_DESIGN §11.
- Divergence dashboard: per-profile agreement %, per-friction-pattern miss
  count.

**Acceptance**:
- 14 days of shadow blocking with ≥500 admission calls
- Per-profile agreement rate >95% on the `companion_required` mechanism
- Friction pattern hit counts stable or declining

**Dependency**: P1 (additive) and P2 (companion mechanism) shipped.

**Estimated size**: 1000–1500 LOC.

---

### P4 — `topology_v_next_phase3_cutover_pilot`

**Goal**: cutover the first low-blast gate to v_next as authoritative.

**Inputs**: P3 divergence report, MIGRATION_PATH.md Phase 3.

**Scope**:
- Pick 1–2 admission paths with high P3 agreement and low blast radius
  (candidates: `docs` write-intent admission, `docs/operations packet
  creation`).
- Switch those paths to read v_next as authoritative; OLD becomes shadow.
- Rollback recipe: single CLI flag flip.

**Acceptance**:
- 7 days of cutover with zero unrecoverable false-blocks
- Cutover paths show p50/p95 admission-attempts-to-success ≤ 1.2 / ≤ 2
  (vs current observed 1.8 / 4)

**Dependency**: P3 with positive divergence report.

**Estimated size**: 600–1000 LOC.

---

### P5 — `maintenance_worker_core`

**Goal**: implement the project-agnostic maintenance worker per
`02_daily_maintenance_agent/DESIGN.md`.

**Inputs**: all four files in `02_daily_maintenance_agent/` and all three
files in `04_workspace_hygiene/`.

**Scope**:
- New repo (or new top-level directory): `maintenance_worker/` with:
  - `core/` (project-agnostic engine, validator, evidence trail, refusal
    modes, kill switch)
  - `rules/` (parser for hygiene rule files)
  - `cli/` (CLI entry; supports launchd / cron / in-process schedulers)
  - `tests/` (10-mess fixture per category)
- The Zeus-specific binding lives in P6, not P5.

**Acceptance** (per DESIGN.md acceptance criteria):
- 10-mess fixture passes per category
- Refuses on dirty repo
- Refuses on forbidden-path violation with FATAL not silent skip
- Evidence trail reconstructs any decision
- Simulated 30-day run produces zero false-positive load-bearing-packet
  archival on Zeus packet inventory test corpus

**Dependency**: none (independent of topology v_next track; can run in
parallel).

**Estimated size**: 3000–4500 LOC + 1500 LOC tests.

---

### P6 — `maintenance_worker_zeus_binding`

**Goal**: deploy the maintenance worker against Zeus's actual workspace
with Zeus-specific allowlists and notification routing.

**Inputs**: P5 output + Zeus runtime conventions.

**Scope**:
- `bindings/zeus/config.yaml`: Zeus-specific TTLs, paths, notification
  channel
- `bindings/zeus/safety_overrides.yaml`: any Zeus-specific additions to
  the universal forbidden-path list
- launchd plist: `~/Library/LaunchAgents/com.zeus.maintenance.plist`
  scheduled daily 04:30 local
- Initial 30-day dry-run mandate per DRY_RUN_PROTOCOL.md

**Acceptance**:
- 30 consecutive ticks complete without forbidden-path errors
- Each tick produces a SUMMARY.md the human can read in <5 minutes
- Per-task pause flag tested

**Dependency**: P5.

**Estimated size**: 400–700 LOC + plist.

---

### P7 — `lore_indexer_and_proposal_promoter`

**Goal**: implement `docs/lore/` indexer and the proposal-to-card promotion
flow per `04_workspace_hygiene/LORE_EXTRACTION_PROTOCOL.md`.

**Inputs**: LORE_EXTRACTION_PROTOCOL.md schema.

**Scope**:
- `scripts/lore_indexer.py`: walks `docs/lore/**`, builds topic-keyed
  lookup, emits `docs/lore/INDEX.json` for agent consumption
- Promoter: agent CLI subcommand to move a `_drafts/<id>.md` → topic
  directory
- Re-verification runner per `verification_command:` field

**Acceptance**:
- Index covers all cards with no orphaned topics
- Promoter rejects cards missing required frontmatter fields
- Re-verification runs in sandbox; signature mismatch flips
  `NEEDS_RE_VERIFICATION` status

**Dependency**: P6 (the maintenance worker emits the proposals; this
packet promotes them).

**Estimated size**: 600–900 LOC.

---

### P8 — `authority_drift_3_blocking_remediation`

**Goal**: resolve the 3 currently-BLOCKING `reference_replacement_missing_entry`
entries:
- `docs/reference/zeus_calibration_weighting_authority.md`
- `docs/reference/zeus_kelly_asymmetric_loss_handoff.md`
- `docs/reference/zeus_vendor_change_response_registry.md`

**Inputs**: `03_authority_drift_remediation/REMEDIATION_PLAN.md` §
Investigation Of The 3 TOPOLOGY BLOCKING Entries.

**Scope per doc**: pick Hypothesis A (doc drift) or B (rule mis-tuned),
execute the corresponding workflow.

**Acceptance**:
- All 3 entries no longer BLOCKING
- A short post-mortem in the packet identifies whether it was A or B and
  why; this becomes a lore card under `topic: topology` if B (rule
  mis-tuning is generalizable lore).

**Dependency**: none (independent of v_next; addresses current breakage).

**Estimated size**: 200–500 LOC depending on hypothesis split.

---

### P9 — `authority_inventory_v2`

**Goal**: extend AUTHORITY_DOCS_INVENTORY to cover Cohort 7 surfaces:
`.claude/CLAUDE.md`, all `~/.../CLAUDE.md` chain,
`architecture/modules/*.yaml`, `docs/operations/INDEX.md`,
`docs/operations/current_*.md`.

**Inputs**: `03_authority_drift_remediation/DRIFT_ASSESSMENT.md` Cohort 7.

**Scope**:
- Inventory generator script handles the additional path patterns
- Drift score computation extends to the new surfaces
- `architecture/docs_registry.yaml` updated with the new authority
  classifications

**Acceptance**:
- Cohort 7 surfaces appear in the next weekly drift surface report
- Per-surface verdict assigned

**Dependency**: P5 (the maintenance worker is the consumer of this
inventory).

**Estimated size**: 300–500 LOC.

---

### P10 — `topology_doctor_module_consolidation`

**Goal**: consolidate the 19 `topology_doctor_*.py` sub-modules where
their split cost exceeds their separation benefit. This is REFACTOR, not
behavior change. The HIDDEN_BRANCH_LESSONS observation that "every fix
added a sidecar" is partly enabled by the trivial cost of adding another
sub-module file.

**Inputs**: HIDDEN_BRANCH_LESSONS § Cross-Iteration Meta-Pattern.

**Scope**: PLANNING ONLY in this packet entry; the actual consolidation
gets its own scope packet after v_next is live (cutover via P4) so the
refactor target is stable.

**Acceptance**: defer to its own packet.

**Dependency**: P4 cutover ships first; consolidating against a moving
target is regression risk.

**Estimated size**: TBD.

## Packet Sizing Discipline

Each packet listed above keeps its self-authored LOC ≥ 300 (the project's
PR-LOC threshold) AND ≤ 4500 (the implicit "one coherent unit" ceiling
observed in this repo's larger PRs). Packets exceeding 4500 LOC must be
split BEFORE submission.

## Cross-Packet Invariants

- No packet may modify `architecture/**` without an explicit
  `companion_required:` admission entry naming the architecture file
  being changed (P2's mechanism enforces this for packets shipping after
  P2 lands).
- No packet may delete a `topology_doctor_*.py` sub-module before the
  same packet's tests prove the sub-module's checks are present in v_next.
- No packet may bypass the maintenance worker's safety contract — the
  worker is leaf, never orchestrator.
- The 4-week dry-run mandate for the maintenance worker (P6) is
  non-negotiable. No live-default flag flip before the 4-week window
  closes.

## Out-Of-Index (Future Considerations)

- Browser/Computer-Use hygiene rules (the maintenance agent currently
  doesn't cover Codex desktop tool side effects)
- Per-author authority ownership (assigning each authority doc to a
  specific human owner with on-call rotation)
- Codex parity packet for v_next admission (current scope assumes
  Claude Code is the primary admitter; Codex hooks need their own
  binding when admission becomes runtime-checked)
- Cross-repo topology federation (if the same topology core gets adopted
  by a sibling project, how do they share/diverge profiles)

These are flagged for the next planning cycle; not in scope for this
package.
