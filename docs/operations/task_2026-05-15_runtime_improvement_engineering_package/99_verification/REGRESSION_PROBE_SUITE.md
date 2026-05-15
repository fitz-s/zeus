# Regression Probe Suite

Status: SPEC
Purpose: a permanent set of probes the topology v_next implementation
(packets P1–P4) and the maintenance agent (packets P5–P6) must pass before
each promotion. These probes exist as evergreen tests; they are not run
once and discarded.

## Why A Permanent Probe Suite

The hidden-branch lessons identified that every prior iteration shipped a
sidecar fix without changing the underlying admission unit. A probe suite
that exercises the structural failure modes — not just the surface symptoms
— prevents future iterations from regressing back into the same pattern.

A probe is a self-contained reproducible runtime check. Probes are
executed by:
- CI on every commit touching `scripts/topology_doctor*.py`,
  `scripts/topology_v_next/**`, or `architecture/task_boot_profiles.yaml`
- The maintenance agent's `topology_health_probe` weekly task
- Manually by a human investigating a v_next regression

## Topology v_next Probes

### TPV-1: Lexical-paraphrase admission consistency

**What it tests**: LEXICAL_PROFILE_MISS pattern is structurally fixed.

**Setup**: a fixed file-path-list `[<file_a>, <file_b>]` whose change is
covered by exactly one profile.

**Probe**: invoke admission with 5 different natural-language task phrases
expressing the same intent. Examples (project-agnostic): "modify the
external API client", "fix vendor SDK call", "update integration adapter",
"change the upstream client method", "patch the third-party connector".

**Expected**: all 5 phrasings admit the same profile with the same
allowed_files set. Any divergence is a regression.

**Failure code**: `TPV_LEXICAL_DIVERGENCE`

### TPV-2: Coherent-union scope admission

**What it tests**: UNION_SCOPE_EXPANSION is structurally fixed via
composition rules (UNIVERSAL_TOPOLOGY_DESIGN §7).

**Setup**: a file set spanning two profiles where the change is one
coherent unit (e.g., `<production_file>`, `<test_for_production_file>`,
`<recovery_helper>`).

**Probe**: invoke admission once with the union; then invoke once with
each profile's subset.

**Expected**: the union admission produces a clean composed result, NOT
`scope_expansion`. The composed result lists allowed_files = union of
component allowed_files. If composition is forbidden for this pair, the
admission emits `COMPOSITION_FORBIDDEN: <reason>` not generic
`scope_expansion`.

**Failure code**: `TPV_UNION_REJECTED_WITHOUT_REASON`

### TPV-3: Intent enum extension surface

**What it tests**: INTENT_ENUM_TOO_NARROW is fixed via the documented
extension mechanism.

**Setup**: a binding-layer extension declaring a new intent (e.g.,
`PROJECT_NS:edit_existing`).

**Probe**: invoke admission with the new intent on a covered file set.

**Expected**: admission succeeds; the new intent appears in
`accepted_intents` documentation; using `edit_existing` without namespace
produces a structured `INTENT_NOT_REGISTERED` failure with the
namespace-qualified suggestion in `next_best`.

**Failure code**: `TPV_INTENT_EXTENSION_BROKEN`

### TPV-4: Failure-as-diagnosis output

**What it tests**: every admission miss returns next-best profile + the
admitting phrase + the unrouted file paths (UNIVERSAL_TOPOLOGY_DESIGN §12).

**Setup**: a deliberately-malformed admission request (wrong phrase, wrong
file set, wrong intent).

**Probe**: capture the failure response.

**Expected**: response contains:
- `failure_code` (one of the documented enum values, NOT a free-form
  string)
- `next_best_profile` (string or null)
- `admitting_phrase_suggestion` (string or null)
- `unrouted_files` (list of paths)
- `phrase_distance` or equivalent semantic-similarity diagnostic

**Failure code**: `TPV_FAILURE_NOT_DIAGNOSTIC`

### TPV-5: Output normalization

**What it tests**: ADVISORY_OUTPUT_INVISIBILITY is structurally fixed.

**Setup**: an admission that triggers an advisory warning but not a hard
block.

**Probe**: capture the tool result shape.

**Expected**:
- `ok` field is FALSE if any advisory is present (not TRUE) — advisory is
  a soft block, not silent permission. The agent's tool-result reader sees
  failure-by-default if any advisory fires.
- `decision` field is `ADVISE_REVIEW` (not `ADMIT`).
- `advisory` field is a non-empty list of structured items each with
  `code`, `path`, `message`, `suggested_action`.
- The shape is identical to the hard-block shape modulo `decision`
  enum value, so any consumer that handles hard-block correctly also
  handles advisory correctly.

**Failure code**: `TPV_ADVISORY_AS_OK_TRUE`

### TPV-6: Coverage map invariant

**What it tests**: every repo path is either covered, explicitly orphaned,
or forbidden (UNIVERSAL_TOPOLOGY_DESIGN §6).

**Setup**: walk all tracked files in the repo.

**Probe**: for each path, query coverage map; assert the path classifies
into exactly one of `COVERED_BY_PROFILE(<id>)`, `ORPHANED`, `FORBIDDEN`.

**Expected**: zero paths return `UNCLASSIFIED` (the legacy default that
caused implicit escalation). Every orphaned path appears in a
`coverage_orphans.tsv` artifact reviewed by humans monthly.

**Failure code**: `TPV_PATHS_UNCLASSIFIED`

### TPV-7: Closed-artifact authority distinction

**What it tests**: CLOSED_PACKET_STILL_LOAD_BEARING is fixed via separate
"packet status" and "evidence authority status" fields
(UNIVERSAL_TOPOLOGY_DESIGN §13).

**Setup**: a closed packet whose evidence is still load-bearing.

**Probe**: invoke admission for an action that would violate the
load-bearing evidence.

**Expected**: admission detects the load-bearing evidence even though the
packet is `closed`. Failure cites the specific load-bearing artifact, not
just the packet status.

**Failure code**: `TPV_CLOSED_PACKET_AUTHORITY_LOST`

### TPV-8: Friction budget tracking

**What it tests**: every admission tracks attempts-to-success and the
running p50/p95 SLO is exposed.

**Setup**: instrument admission with attempt counter.

**Probe**: invoke a typical session of 50 admissions. Read the
`<state>/admission_attempts.tsv` file.

**Expected**: file exists, has 50 rows, each row has `phrase_attempts`
column with a monotonic non-decreasing prefix per session. Aggregate p50
≤ 1.5, p95 ≤ 3 for v_next (current observed: p50 1.8, p95 4).

**Failure code**: `TPV_FRICTION_BUDGET_REGRESSED`

### TPV-9: Companion-required enforcement

**What it tests**: P2 mechanism blocks PRs that touch covered source
without companion-doc edit (REMEDIATION_PLAN.md companion-update
enforcement).

**Setup**: a profile with `companion_required: <doc_path>` populated.

**Probe**: simulate a PR touching the covered source ONLY. Then a PR
touching source AND companion. Then a PR touching source with skip-token.

**Expected**: source-only fails with `MISSING_COMPANION` naming the doc.
Source+companion admits. Source+skip-token admits but logs to human-review
queue.

**Failure code**: `TPV_COMPANION_NOT_ENFORCED`

### TPV-10: Non-negotiable guardrail integrity

**What it tests**: UNIVERSAL_TOPOLOGY_DESIGN §15 guardrails (planning
evidence, dirty-worktree refusal, runtime-truth registry separation,
forbidden-file blocks) are NOT loosened by v_next.

**Setup**: try each historically-blocked condition (dirty repo, missing
plan evidence, write to forbidden path).

**Probe**: invoke admission for each.

**Expected**: each is still blocked. The block is now diagnostic
(per TPV-4) but the block itself is preserved.

**Failure code**: `TPV_GUARDRAIL_LOOSENED`

## Maintenance Worker Probes

### MW-1: Refusal on dirty repo

**Setup**: repo with one uncommitted file change on currently-checked-out branch.

**Probe**: invoke maintenance worker tick.

**Expected**: exit non-zero with reason `not_dirty_repo`. No mutation.
SUMMARY.md exists with the refusal logged.

**Failure code**: `MW_RAN_ON_DIRTY_REPO`

### MW-2: Forbidden-path validator

**Setup**: rule file proposes an action targeting `src/foo.py`.

**Probe**: invoke maintenance worker tick with that rule active.

**Expected**: FATAL ERROR before any mutation. errors.tsv records the
attempted path and the matching forbidden rule. Self-quarantine file
written.

**Failure code**: `MW_FORBIDDEN_PATH_LEAKED`

### MW-3: Dry-run output is the source of truth

**Setup**: any task in dry-run mode.

**Probe**: read the proposal `.md` and `.diff` files.

**Expected**: every action that would have been taken is described in the
`.diff` with the exact `mv`/`git mv`/etc. command. No surprise
mutations not previewed.

**Failure code**: `MW_DRY_RUN_LIES`

### MW-4: Acknowledge round-trip

**Setup**: human acks a proposal hash.

**Probe**: invoke next tick.

**Expected**: matching task runs live ONCE. Ack moves to `applied/`.
Subsequent tick on same proposal: dry-run again (no second live exec).

**Failure code**: `MW_ACK_REUSED`

### MW-5: Stale-ack invalidation

**Setup**: ack a proposal, then change the underlying candidates so the
proposal hash differs.

**Probe**: invoke next tick.

**Expected**: ack is NOT honored. Fresh dry-run emitted with diff vs the
prior proposal. Human re-ack required.

**Failure code**: `MW_STALE_ACK_HONORED`

### MW-6: Eight-check archival exemption

**Setup**: a closed packet that should be archived per age but has 1 of
the 8 exemption checks failing (e.g., a current authority doc cites it).

**Probe**: invoke `closed_packet_archive_proposal` task.

**Expected**: packet classified as `LOAD_BEARING_DESPITE_AGE`. Row added
to `LOAD_BEARING_REGISTRY.md`. NOT archived. Per-check outcomes recorded
in proposal.

**Failure code**: `MW_LOAD_BEARING_PACKET_ARCHIVED`

### MW-7: 30-day load-bearing zero-false-positive

**Setup**: simulate 30 ticks against the Zeus packet inventory test
corpus.

**Probe**: collect every packet the agent would have archived live (i.e.,
proposals with all 8 checks passing).

**Expected**: zero packets that, under human inspection, are actually
load-bearing. If even one is found, the exemption-check rule is
under-tuned and must be tightened before live promotion.

**Failure code**: `MW_30D_FALSE_POSITIVE`

### MW-8: Kill-switch sticky behavior

**Setup**: write `KILL_SWITCH` file.

**Probe**: invoke 5 consecutive ticks.

**Expected**: all 5 refuse to run. Kill switch never auto-removed.

**Failure code**: `MW_KILL_SWITCH_BYPASSED`

### MW-9: Self-quarantine on contract violation

**Setup**: inject a synthetic forbidden-path mutation (test-only hook).

**Probe**: invoke tick.

**Expected**: mutation detected, SELF_QUARANTINE file written, agent
exits, future ticks refuse.

**Failure code**: `MW_NO_SELF_QUARANTINE`

### MW-10: Notification routing

**Setup**: configured Discord channel.

**Probe**: complete one tick.

**Expected**: SUMMARY.md content posted to the channel. Refusal exits
post a distinct alert.

**Failure code**: `MW_NOTIFICATION_LOST`

## Probe Suite Maintenance

- New v_next features add new TPV-N probes BEFORE shipping
- Probe failures triggered by intentional behavior change require updating
  the probe AND a paired note in HIDDEN_BRANCH_LESSONS.md so future
  iterations know the change was deliberate
- Probes never silently disable. A skipped probe is a regression signal.

## Acceptance For This Suite

The suite is acceptable when:
- All 10 TPV probes have a runnable implementation (P1 ships TPV-1, TPV-3,
  TPV-4, TPV-5, TPV-6 minimum)
- All 10 MW probes have a runnable implementation (P5 ships all)
- CI integration is wired (PR touching topology source runs TPV-1..10;
  PR touching maintenance worker source runs MW-1..10)
- A failed probe blocks merge; a skipped probe blocks merge with louder
  alert (skip = unknown state, fail = known state)
