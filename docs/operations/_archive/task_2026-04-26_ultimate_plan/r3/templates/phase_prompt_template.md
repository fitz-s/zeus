# Phase Implementation Prompt — Template

Fill in `<PHASE_ID>` (e.g., Z2) and dispatch to a fresh agent. The agent
should treat this as a complete, self-contained brief.

**For brand-new agent sessions (no prior context)**: use
`fresh_start_prompt.md` instead. It includes orientation BEFORE phase
assignment.

**For agents already oriented**: this template is your operating contract
for ONE phase.

---

You are implementing **Zeus R3 Phase `<PHASE_ID>`**. Your work survives
across compactions ONLY via on-disk artifacts. Treat every disk write as
durable; every in-context assertion as ephemeral.

**The protocol is LIVING. As you discover, get confused, learn — capture
it ALL on disk so future agents and the protocol itself benefit.** Read
`../CONFUSION_CHECKPOINTS.md` and `../SELF_LEARNING_PROTOCOL.md` BEFORE
you write any code.

## Your identity

- Phase: `<PHASE_ID>`
- Card: `docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/<PHASE_ID>.yaml`
- HEAD anchor expected: `874e00cc0244135f49708682cab434b4d151d25d` (`main`)
  — if HEAD has drifted, follow §"What to do if HEAD has changed" in
  `r3/R3_README.md`.

## Mandatory reads (in order, before writing ANY code)

1. `docs/operations/task_2026-04-26_ultimate_plan/r3/R3_README.md` (full)
2. `docs/operations/task_2026-04-26_ultimate_plan/r3/IMPLEMENTATION_PROTOCOL.md` (full — every time, no exceptions)
3. `docs/operations/task_2026-04-26_ultimate_plan/r3/CONFUSION_CHECKPOINTS.md` (the 12 stop-and-verify moments)
4. `docs/operations/task_2026-04-26_ultimate_plan/r3/SELF_LEARNING_PROTOCOL.md` (how to capture + propagate learnings)
5. `docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/<PHASE_ID>.yaml` (full)
6. The phase yaml's `links.r2_cards` entries — each R2 card from `../slice_cards/`
7. The phase yaml's `links.r2_evidence` entries (if listed)
8. `docs/operations/task_2026-04-26_ultimate_plan/r3/INVARIANTS_LEDGER.md` (cross-phase invariant tracker)
9. `docs/operations/task_2026-04-26_ultimate_plan/r3/operator_decisions/INDEX.md` (gates that may block this phase)
10. `docs/operations/task_2026-04-26_ultimate_plan/r3/SKILLS_MATRIX.md` (which skill to invoke at which step)
11. **Prior learnings for this phase**: `docs/operations/task_2026-04-26_ultimate_plan/r3/learnings/<PHASE_ID>_*.md` (if any)
12. **Recent retros from completed phases**: `docs/operations/task_2026-04-26_ultimate_plan/r3/learnings/*_retro.md` (last 3-5)
13. **Open confusions**: `docs/operations/task_2026-04-26_ultimate_plan/r3/_confusion/*.md` with status=OPEN — does any affect your phase?
14. **Pending protocol amendments**: `docs/operations/task_2026-04-26_ultimate_plan/r3/_protocol_evolution/*.md` with status=PROPOSED or ACCEPTED — apply them in your work even if not yet INCORPORATED
15. Memory consultation (read each):
    - `feedback_lifecycle_decomposition_for_execution`
    - `feedback_long_horizon_multi_agent_anti_drift`
    - `feedback_multi_angle_review_at_packet_close`
    - `feedback_grep_gate_before_contract_lock`
    - `feedback_zeus_plan_citations_rot_fast`
    - `feedback_on_chain_eth_call_for_token_identity` (if phase touches token identity)
    - `feedback_critic_prompt_adversarial_template`
    - `feedback_default_dispatch_reviewers_per_phase`
    - `feedback_no_git_add_all_with_cotenant`
    - `feedback_subagent_for_oneoff_checks`
    - any `feedback_<phase_id>_*` entries from prior phases (Bucket B promotions)

## Boot protocol (mandatory before code)

Run, in order:

```bash
# Step 1: drift check for THIS phase
.venv/bin/python docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase <PHASE_ID>

# Step 2: read drift report; if any cite is SEMANTIC_MISMATCH, FREEZE
cat docs/operations/task_2026-04-26_ultimate_plan/r3/drift_reports/$(date +%Y-%m-%d).md
```

If drift report has RED status on a load-bearing citation: STOP. Write
`docs/operations/task_2026-04-26_ultimate_plan/r3/_blocked_<PHASE_ID>.md`
describing the mismatch. Notify user. DO NOT proceed.

If drift report is GREEN or YELLOW (line drift only): proceed.

## Boot evidence write

Write `docs/operations/task_2026-04-26_ultimate_plan/r3/boot/<PHASE_ID>_<author-tag>_<YYYY-MM-DD>.md`
with these sections (≤500 words total):

```markdown
# Boot — <PHASE_ID>

Author: <agent-tag>
Started: <ISO8601>
HEAD: <git rev-parse HEAD>
Branch: <branch-name>

## Reads completed
- [list]

## Drift status
- citations verified: <N/M>
- antibody status: <count>/<total> live
- yellow flags: [list]

## Working hypothesis
<3-5 sentences describing what you'll implement and the key design decisions you anticipate>

## Operator gates blocking
- [list of gate ids; "none" if unblocked]

## Cross-phase touchpoints
- frozen interfaces consumed: [list]
- frozen interfaces produced: [list — what THIS phase exposes to others]
```

## Implementation rules

- DO modify ONLY files listed in the phase yaml's
  `deliverables.extended_modules` + `deliverables.new_modules`. Modifying
  other files = boundary violation. → trigger CC-6.
- If you discover you need to modify a file outside the boundary: write
  `_cross_phase_question_<topic>.md` with the required modification.
  STOP. Notify operator. Do not unilaterally resolve.
- Use SKILLS per `SKILLS_MATRIX.md` — `executor` for code, `test-engineer`
  for tests, `explore` for navigation, `document-specialist` for SDK
  fact-checks, `architect` for schema review (read-only).
- For HIGH-risk phases: dispatch `deep-interview` skill BEFORE writing
  code if any ambiguity remains in the card.
- For phases involving on-chain identity: when in doubt, dispatch a
  sub-agent for direct eth_call (memory rule).
- Citations in code comments + tests use SYMBOL anchors, NOT line numbers
  (per IMPLEMENTATION_PROTOCOL §3).

## Living-protocol loops (run CONTINUOUSLY during work)

The plan is a hypothesis. Reality refines it. Run these four loops at
EVERY major step, not just at end of phase:

### Loop A — Confusion handling (CC-1..CC-12)

When any of the 12 triggers in `CONFUSION_CHECKPOINTS.md` fires:
1. STOP code work.
2. Run the prescribed action (WebFetch / sub-agent / `deep-interview` / etc.).
3. Write `_confusion/<phase>_<topic>.md` with: what's confused, what you
   tried, your recommendation, what you'd want operator to confirm.
4. If trigger is CC-5 (spec contradiction), CC-6 (boundary), CC-7
   (operator gate), CC-8 (invariant), CC-10 (schema), or CC-11
   (antibody): NOTIFY OPERATOR via session message.
5. Resume only after the trigger is resolved.

**Do not wait until end-of-phase to surface confusions.** Same-day
disclosure is the protocol.

### Loop B — Self-learning capture (3 buckets)

When you discover something non-obvious:
- **Phase-specific** → write `r3/learnings/<PHASE_ID>_<topic>.md` immediately.
- **Cross-phase generalizable** → propose memory entry via `remember`
  skill at end of phase (Bucket B promotion).
- **Protocol gap** → write `r3/_protocol_evolution/<topic>.md` with
  proposed amendment.

Discovery moments include: external SDK behavior verified, ambiguity
resolved, citation rot fixed, edge case found, operator clarification,
test brittleness uncovered, performance pattern observed.

Format: see `SELF_LEARNING_PROTOCOL.md` §2.

### Loop C — Protocol evolution

If you spot that the protocol document itself is wrong / missing /
misleading: write `_protocol_evolution/<topic>.md`. You may apply your
proposed amendment in your own phase work even before operator merges
it — flag explicitly in commit message + PR description.

### Loop D — Verification before claim

Before claiming the phase complete:
1. Run `r3/scripts/r3_drift_check.py --phase <PHASE_ID>` → must be GREEN/YELLOW.
2. All `acceptance_tests` from yaml pass on your branch.
3. `INVARIANTS_LEDGER.md` updated with rows for new NC-NEW + INV-NEW.
4. `_phase_status.yaml` updated to COMPLETE.
5. Critic-opus dispatched (HIGH-risk phases) — APPROVED verdict.
6. End-of-phase retro written: `r3/learnings/<PHASE_ID>_<author>_<date>_retro.md`.
7. Memory entries written for any Bucket B promotions.

## Phase status updates

When you start: update `_phase_status.yaml` to set `<PHASE_ID>.status: IN_PROGRESS`.

When you finish (all antibodies green + critic review complete):
- Update `_phase_status.yaml` to set `<PHASE_ID>.status: COMPLETE` with
  `completed_at`, `commit`, `critic_review` fields.
- Append rows to `INVARIANTS_LEDGER.md` for each new NC-NEW + INV-NEW.
- Write `frozen_interfaces/<PHASE_ID>.md` if this phase exposes a public API.
- Write `feedback_<PHASE_ID>_<topic>.md` to memory with phase-specific learnings.

## Branch + PR discipline

- Branch name: `r3/<phase_id-lowercase>-<author-tag>`
- Commit messages: include phase ID prefix (e.g., `Z2: add VenueSubmissionEnvelope`)
- NEVER `git add -A` (memory rule); stage specific files
- Pre-commit hook runs phase test shard + drift_check; do NOT skip
- PR template at `templates/phase_pr_template.md`
- For phases with `critic_gate: critic-opus`: dispatch critic-opus review
  via Agent tool BEFORE merge

## Exit criteria

You are done when ALL of these hold:

1. ☐ All `acceptance_tests` from the phase yaml pass on HEAD
2. ☐ All NC-NEW + INV-NEW from the phase are LIVE in INVARIANTS_LEDGER.md
3. ☐ `frozen_interfaces/<PHASE_ID>.md` written (if phase exposes API)
4. ☐ `_phase_status.yaml` shows `<PHASE_ID>.status: COMPLETE`
5. ☐ Critic-opus review APPROVED (HIGH-risk phases)
6. ☐ End-of-phase retro written: `learnings/<PHASE_ID>_<author>_<date>_retro.md`
7. ☐ Bucket B memory promotions written (for generalizable learnings)
8. ☐ Bucket C protocol-evolution proposals filed (if any)
9. ☐ All `_confusion/<PHASE_ID>_*.md` files marked RESOLVED or ESCALATED
10. ☐ All `_cross_phase_question_*.md` you created are resolved
11. ☐ No drift_check RED status

## Failure modes specific to this phase

(Read the phase yaml's `risk:` and `notes:` sections. Each lists
phase-specific failure modes. Common ones:
- citation rot during your work — re-grep before contract lock
- cross-phase invariant break — run invariant_ledger_check.py before merge
- SDK version drift — verify requirements.txt pin
- antibody too narrow — critic-opus spirit check)

## When to ask vs decide

- Ambiguity in card spec → dispatch `deep-interview`, then ask user if still ambiguous.
- Cross-phase modification needed → ALWAYS ask user. Do NOT decide unilaterally.
- Operator gate appears blocking → ALWAYS ask user. Do NOT bypass.
- Schema migration shape decision → ALWAYS ask user via `_cross_phase_question.md`.
- Implementation detail within phase boundary → DECIDE. You own the phase.

## Time budget

The phase yaml's `estimated_h:` is the LOW estimate. Multi-review verifier
predicts 20-40% buffer. Plan accordingly. If you exceed `estimated_h * 1.5`,
write `_overrun_<PHASE_ID>.md` with status + rationale.

## End of prompt

Begin with the mandatory reads. Do NOT write code until all reads complete + drift_check is GREEN/YELLOW + boot evidence is on disk + ambiguity is resolved.

**Final note**: this protocol is rigid in scaffolding (boot, drift check,
exit criteria) but FLEXIBLE in execution. The four living-protocol loops
exist precisely BECAUSE reality will deviate from the plan. Surfacing
deviations is your job, not avoiding them.

Drift in any single phase compounds across the next 19 phases. Discipline
in the scaffolding (always boot, always confusion-checkpoint, always
write learnings, always update ledger) → ship in 5-8 weeks. Cutting
scaffolding "to save time" → drift cascade by week 3.

The simplest test: when in doubt, write to disk. Disk is the substrate
the protocol operates on. Context is ephemeral.
