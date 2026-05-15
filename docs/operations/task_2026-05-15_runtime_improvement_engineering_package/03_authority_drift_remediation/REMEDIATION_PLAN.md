# Authority Doc Drift Remediation Plan

Status: SPEC
Companion: `DRIFT_ASSESSMENT.md` (the per-doc verdict cohorts)
Owner: human, with agent surfacing only.

## Principle

Authority drift is REPAIRED by humans reading the doc end-to-end against
current code, NOT by the agent rewriting prose. The agent's role is to:
1. Compute drift scores weekly (`authority_drift_surface` task).
2. Surface high-score docs to the human via configured channel.
3. Maintain a per-doc audit trail (last surfaced, ack'd, rewritten, etc.).
4. Detect tag mismatches and propose re-classification (proposal-only).
5. Block PRs that touch covered source without companion-updating the
   authority doc (this lives in topology v_next, see
   `01_topology_v_next/UNIVERSAL_TOPOLOGY_DESIGN.md`).

## Per-Cohort Action Playbook

### Cohort 1: CURRENT
- Action: none
- Surface: included in monthly summary only
- Re-assess: at quarterly authority audit

### Cohort 2: LIVE_BUT_NOT_VERIFIED
- Action: schedule end-to-end content read by human within 14 days
- Surface: include in next weekly digest with `pending_content_read: true`
  flag
- Owner: domain expert per doc (the `covers:` frontmatter names the owner
  if extension adopted)

### Cohort 3: MINOR_DRIFT
- Action: spot-check 3 random sections per doc against current source
- Surface: weekly digest
- If spot check finds drift: promote to STALE_REWRITE_NEEDED

### Cohort 4: STALE_REWRITE_NEEDED
For each doc:

1. Read the doc end-to-end.
2. Identify each claim that names a specific symbol, file, function, or
   schema field.
3. For each claim: grep current code for the named symbol. If absent or
   renamed: mark the claim as STALE_CLAIM.
4. Decide per claim:
   - `UPDATE_DOC`: code is right, doc is wrong. Edit doc.
   - `UPDATE_CODE`: doc was right, code drifted in error. Open
     non-maintenance PR to fix code.
   - `BOTH_RIGHT_DOC_AMBIGUOUS`: doc cites old symbol but the new symbol
     does the same thing. Update doc with new symbol; add a one-line
     `previously called X` note for grep continuity.
   - `RETIRE_CLAIM`: code change deliberately removed the feature; the
     claim is obsolete. Strike the claim AND log to a follow-up
     authority-evolution log so the constraint that motivated the claim
     is not lost.
5. Update the doc header: bump `last_human_audit:` date, increment
   `audit_count:`, optionally add `audit_notes:` line.
6. Companion-update any sibling doc that cites the changed claim.
7. PR opens with full claim-by-claim diff; reviewer is at minimum 1
   domain expert and ideally a critic agent.

The 3 TOPOLOGY BLOCKING docs (`zeus_calibration_weighting_authority.md`,
`zeus_kelly_asymmetric_loss_handoff.md`,
`zeus_vendor_change_response_registry.md`) each get their own dedicated
follow-up packet under `05_execution_packets/PACKET_INDEX.md`.

### Cohort 5: DEMOTE_AUTHORITY
For each doc:

1. Confirm with domain expert: "is this still authoritative?"
2. If no:
   - Add `Status: HISTORICAL_REFERENCE` header
   - Move file to `docs/historical_reference/` (preserve git history via
     `git mv`)
   - Update `architecture/docs_registry.yaml` to remove its authority
     marker
   - Grep for citations across active code and docs; update or note as
     historical
3. If yes (still authoritative, just slow-moving):
   - Add `Status: STABLE_AUTHORITY` header to clarify
   - Add a `re_audit_cadence: yearly` field
   - Leave in place

Special case for `zones.yaml`, `runtime_modes.yaml`, `maturity_model.yaml`
(0 commits/30d): high probability these are genuinely STABLE_AUTHORITY
(low-churn registry data) rather than dead. Default verdict is
`STABLE_AUTHORITY` pending one human pass.

### Cohort 6: TAG_MISMATCH

For each entry:

1. Re-classify by editing the source-of-truth tag location (the doc's own
   header AND `architecture/docs_registry.yaml`).
2. If TAG_MISMATCH suggests promotion to YES authority (e.g., root
   `AGENTS.md`):
   - Add `authority_class:` field to header
   - Add to `architecture/docs_registry.yaml`
   - Add a `covers:` frontmatter field naming the source paths the doc
     covers (this enables drift score computation in subsequent weeks)
3. If TAG_MISMATCH suggests demotion (e.g., `improvement_backlog.yaml`
   correctly marked NO):
   - Confirm and document why it is not authority
   - Optionally move to a non-authority dir to reduce future confusion

The proposed re-classifications in DRIFT_ASSESSMENT Cohort 6 are
SUGGESTIONS, not decisions. The human decides per row.

### Cohort 7 (out-of-inventory check)

A v2 inventory pass adds: `.claude/CLAUDE.md`, all `~/.../CLAUDE.md`
chain, `architecture/modules/*.yaml`, and `docs/operations/INDEX.md` /
`current_*.md`. This pass is a separate task in the agent's task catalog
(extension to `authority_drift_surface`).

## Investigation Of The 3 TOPOLOGY BLOCKING Entries

The topology_doctor reports these as BLOCKING:
```
docs/reference/zeus_calibration_weighting_authority.md
docs/reference/zeus_kelly_asymmetric_loss_handoff.md
docs/reference/zeus_vendor_change_response_registry.md
```

Two hypotheses must be tested before remediation:

**Hypothesis A**: These docs really have drifted; their content cites
constructs/files that no longer exist or have moved.

**Hypothesis B**: The topology_doctor `reference_replacement` rule is
mis-tuned (wrong matrix entry expected, or matrix logic itself
incorrect). The docs are fine; the check is wrong.

Investigation procedure:
1. Read each doc end-to-end against `architecture/reference_replacement.yaml`
   to see what entry the rule expected and why it is missing.
2. Read the rule code in `scripts/topology_doctor_reference_checks.py` to
   understand the matrix shape.
3. Pick the hypothesis per doc.
4. Hypothesis A → STALE_REWRITE_NEEDED workflow above.
5. Hypothesis B → fix the rule, not the doc; this is part of the
   `01_topology_v_next/MIGRATION_PATH.md` rule cleanup.

## Cadence

- **Daily**: agent does NOT touch authority docs (per SAFETY_CONTRACT).
- **Weekly (Monday)**: agent runs `authority_drift_surface` task; computes
  drift scores; emits `${EVIDENCE_DIR}/<date>/drift_surface/` with full
  per-doc score table; sends digest notification.
- **Quarterly**: human runs full Cohort 1–6 review across all docs. The
  agent surfaces a quarterly digest highlighting which docs have ZERO
  human-audit-pass since `last_human_audit` field was added (or never).
- **Per-PR**: a topology v_next gate (NOT the maintenance agent — see
  `01_topology_v_next/`) blocks PRs touching covered source without
  companion-updating the doc. This converts the drift problem from
  "find drift later" to "prevent drift at write time."

## Companion-Update Enforcement (Cross-Reference To Topology v_next)

The structural fix for "authority doc lags PR throughput" is NOT more
auditing. It is enforcing companion-update at admission time. The
universal topology design must include a `companion_required:` mechanism
where:

```yaml
profile: modify_calibration_weighting
allowed_files:
  - src/calibration/*.py
  - tests/test_calibration_*.py
companion_required:
  - docs/reference/zeus_calibration_weighting_authority.md
  reason: "any change to calibration weighting must update the authority
           reference; otherwise the authority drift problem compounds"
companion_skip_acknowledge_token: COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1
```

If the PR touches a path under `allowed_files` without ALSO touching the
`companion_required` path, admission fails with a clear message naming the
companion. The agent can override only with the explicit token, and that
override is logged for human review.

This is the structural fix. The maintenance agent's `authority_drift_surface`
weekly task is the SAFETY NET that catches what the structural fix misses.

## Acceptance For This Plan

- Each of Cohort 4's 15 docs has a named owner OR is queued for owner
  assignment in the next quarterly review.
- The 3 TOPOLOGY BLOCKING entries each have a hypothesis selected
  (A or B) within 14 days, and a follow-up packet entry under
  `05_execution_packets/PACKET_INDEX.md`.
- The `authority_drift_surface` weekly task is added to TASK_CATALOG.yaml
  (already done in this packet).
- The companion-update mechanism is specified in
  `01_topology_v_next/UNIVERSAL_TOPOLOGY_DESIGN.md` (sonnet's deliverable).
- A v2 inventory pass scope is defined for the out-of-inventory surfaces.

## What This Plan Does NOT Do

- Does not assign domain experts to docs (humans do this).
- Does not rewrite any doc (humans do this).
- Does not auto-merge any authority-doc PR (never).
- Does not change the topology rules (sonnet's track does that).
