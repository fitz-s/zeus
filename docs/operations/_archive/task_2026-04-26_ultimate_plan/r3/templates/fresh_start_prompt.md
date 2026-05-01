# Fresh-Start Prompt — Zeus R3 Implementation

This is the prompt to paste into a brand-new agent session (compacted, zero
context, fresh login) to begin or resume R3 implementation work. The agent
is responsible for orienting itself before doing anything.

If you are the operator: copy from `--- BEGIN PROMPT ---` to `--- END PROMPT
---` and paste as the agent's task. Customize the optional `<MODE>` /
`<PHASE_HINT>` blocks if you want to nudge the agent toward a specific phase.

---

--- BEGIN PROMPT ---

# Your task: Zeus R3 implementation, fresh-start orientation

You are joining a long-horizon implementation project (~312 engineering
hours, 5-8 weeks across rotating agent sessions). You may be the first
agent to start the work, or you may be picking up after several phases
have shipped. **Your immediate job is to ORIENT yourself; not to start
writing code.**

The plan and its protocol are LIVING DOCUMENTS. As you work, you will:
- discover facts that contradict the plan,
- get confused about specs,
- find external claims you cannot verify,
- learn things that should propagate to future phases.

This prompt teaches you how to do all four AS YOU GO, not at the end.

## §1 First: read the entry point

Open `/Users/leofitz/.openclaw/workspace-venus/zeus/docs/operations/task_2026-04-26_ultimate_plan/r3/R3_README.md` in full. It is your map. Do
not skim. ~300 lines.

After R3_README, read these (in order):
1. `r3/IMPLEMENTATION_PROTOCOL.md` — how implementation works (the 14
   failure modes + their mechanisms; do not skip).
2. `r3/CONFUSION_CHECKPOINTS.md` — moments where you MUST stop and verify.
3. `r3/SELF_LEARNING_PROTOCOL.md` — how to capture + propagate what you
   learn during work.
4. `r3/_phase_status.yaml` — current state of all 20 phases. Look for
   `ready_to_start:` field.
5. `r3/INVARIANTS_LEDGER.md` — which invariants are LIVE on HEAD now.
6. `r3/operator_decisions/INDEX.md` — which operator gates are OPEN.
7. The latest drift report at `r3/drift_reports/<latest-date>.md`.

## §2 Orientation, not execution

You are NOT starting code yet. Your first deliverable is a brief
orientation report that proves you understood the packet. Write it to:

```
r3/_orientation/<your-tag>_<YYYY-MM-DD>.md
```

(Create the `_orientation/` directory if it does not exist.)

The orientation report has 6 sections (≤500 words total):

1. **Current state** — HEAD anchor; phases COMPLETE / IN_PROGRESS / PENDING.
2. **Drift status** — drift_check GREEN/YELLOW/RED; if RED, what's blocking.
3. **Operator gates** — list OPEN gates and which phases they block.
4. **Ready phases** — phases where status=PENDING + all depends_on
   COMPLETE + no blocking operator gate. List with priority order.
5. **Concerns** — anything in the packet that confuses you or seems
   inconsistent. Be honest. The packet was authored by another agent
   and may have errors.
6. **Proposed next action** — which phase you propose to start, and why.
   If multiple are ready, recommend ONE based on critical-path priority
   (consult `slice_summary_r3.md`'s critical path).

After writing the orientation report, **STOP and notify the operator**.
Do NOT auto-start a phase. The operator reviews your orientation, may
correct your understanding, and explicitly authorizes you to begin.

## §3 If operator authorizes a phase

Switch to the phase prompt template at `r3/templates/phase_prompt_template.md`.
Fill in `<PHASE_ID>` with the authorized phase. That template is your
operating contract for the phase implementation.

## §4 During work: the four living-protocol loops

These are the loops that turn this from a static plan into a living
implementation. Run them ALL, ALWAYS:

### Loop A — Confusion handling (most important)

Read `r3/CONFUSION_CHECKPOINTS.md` BEFORE you start any phase. It lists
the 12 specific moments when you MUST stop and verify rather than
proceed on assumption.

Quick summary of the 12 moments:
1. You're about to assume external behavior (SDK, API, on-chain) without a
   citation → **WebFetch authoritative source OR dispatch sub-agent OR
   on-chain RPC eth_call**.
2. A spec is genuinely ambiguous after re-reading → **dispatch
   `deep-interview` skill OR write `_confusion/<topic>.md` and ask user**.
3. A cite says line N but reality says symbol moved → **re-grep + update
   citation by SYMBOL anchor (not line number)**.
4. Your test passes but you suspect it tests the wrong thing → **dispatch
   `critic` for spirit check OR write `_confusion/<topic>.md`**.
5. You discover a fact that contradicts a phase yaml → **STOP, write
   `_confusion/<topic>.md`, propose amendment, ask user**.
6. You need to modify a file outside your phase boundary → **always ask
   user via `_cross_phase_question.md`; never decide unilaterally**.
7. Operator gate appears blocking but you're tempted to bypass → **STOP.
   Operator gates are non-negotiable**.
8. You see a NC-NEW invariant that contradicts your plan → **invariants
   beat plans; redesign your approach**.
9. Your hour estimate is exceeding 1.5× the card's `estimated_h` → **write
   `_overrun_<phase>.md`, ask user whether to descope or extend**.
10. Schema migration shape decision has downstream implications you can't
    fully predict → **ask user; this is planning-lock territory**.
11. Antibody test fails on first run; you're tempted to weaken the assertion
    → **NEVER weaken antibodies to make tests pass; the test is the
    contract**.
12. External SDK version differs from pin → **freeze; dispatch
    `document-specialist` to capture diff**.

For each confusion moment, write `r3/_confusion/<short-topic>.md` with:
- what you don't know
- what you're proposing to do about it
- what you'd want the operator to confirm

### Loop B — Self-learning capture

When you discover a fact during work that future phases / future agents
will need, write it down BEFORE you forget. Three buckets:

- **Phase-specific learning** → `r3/learnings/<phase>_<topic>.md`. Read
  by future agents at the same phase.
- **Cross-phase pattern** → propose memory entry via `remember` skill.
  Becomes a `feedback_*` memory file accessible across all sessions.
- **Protocol gap** → `r3/_protocol_evolution/<short-topic>.md`. Operator
  decides whether to amend `IMPLEMENTATION_PROTOCOL.md`.

When to capture:
- You found a non-obvious behavior of an external SDK / API.
- You found an edge case in the spec that the card didn't cover.
- You found a test pattern that's brittle vs robust.
- You found a way the protocol could prevent a class of error.
- You found a contradiction between the plan and reality.

Read `r3/SELF_LEARNING_PROTOCOL.md` for the full how-to.

### Loop C — Protocol evolution

The implementation protocol (`r3/IMPLEMENTATION_PROTOCOL.md`) is itself
a hypothesis. As you work, you may discover that:
- a prescribed step is unnecessary,
- a missing step is critical,
- a skill recommendation is wrong,
- a confusion checkpoint is missed,
- the prompt template misleads agents.

When you spot a protocol gap, write `r3/_protocol_evolution/<short-topic>.md`
with:
- what the protocol currently says
- what reality showed
- what you propose to change
- evidence (logs, commits, learnings)

The operator + future critic-opus reviews evolve the protocol. You don't
have to wait until the operator merges your suggestion — you can start
following the proposed change in your own phase if it's clearly an
improvement and you flag it explicitly.

### Loop D — Verification before claim

Before you claim a phase complete, run the verification triad:
1. `r3/scripts/r3_drift_check.py` returns GREEN/YELLOW for your phase.
2. All `acceptance_tests` from the phase yaml pass on your branch.
3. `r3/INVARIANTS_LEDGER.md` is updated (rows for your new NC-NEW + INV-NEW).

For phases marked `critic_gate: critic-opus`, dispatch `critic` skill
BEFORE merge. Critic checks the SPIRIT, not just the letter.

## §5 What to do if you find this prompt is wrong

If, while orienting, you find:
- The packet is internally inconsistent
- The phase yaml is genuinely under-specified
- The protocol contradicts itself
- A previously-COMPLETE phase has actually drifted

Write `r3/_confusion/_packet_integrity_<your-tag>_<date>.md` with the
specific contradictions. STOP. Notify operator. Do NOT proceed with any
phase implementation until the integrity issue is resolved.

This is not failure. This is exactly the signal the protocol expects you
to surface.

## §6 Skill invocation discipline

Read `r3/SKILLS_MATRIX.md` for the full mapping. High-level rules:

- AMBIGUITY before code → `deep-interview` (opus)
- EXTERNAL FACT → `document-specialist` + WebFetch (or sub-agent for
  on-chain RPC; memory `feedback_on_chain_eth_call_for_token_identity`)
- IMPLEMENTATION → `executor` (default), opus for HIGH-risk
- TESTS → `test-engineer`
- SCHEMA / STRUCTURAL → `architect` (read-only sanity check)
- MATH / DATA → `scientist`
- PRE-MERGE REVIEW → `code-reviewer` + `critic` (opus, mandatory for
  HIGH-risk per `feedback_default_dispatch_reviewers_per_phase`)
- MEMORY PERSIST → `remember` skill at end of phase

Do NOT skip skill invocations to save time. Each one catches a class of
error. Skipping is drift.

## §7 Memory consultation

Before you write any code, read these memory entries (all under
`/Users/leofitz/.claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus/memory/`):
- `feedback_lifecycle_decomposition_for_execution`
- `feedback_long_horizon_multi_agent_anti_drift`
- `feedback_multi_angle_review_at_packet_close`
- `feedback_grep_gate_before_contract_lock`
- `feedback_zeus_plan_citations_rot_fast`
- `feedback_on_chain_eth_call_for_token_identity`
- `feedback_critic_prompt_adversarial_template`
- `feedback_default_dispatch_reviewers_per_phase`
- `feedback_no_git_add_all_with_cotenant`

Plus any `feedback_*` entries from PRIOR phases (look for files matching
`feedback_<phase>_*.md`). These contain learnings that are load-bearing
for your current work.

## §8 The single most important rule

**Disk > context.** Anything you discover, decide, or learn must be
written to disk BEFORE the next compaction. Context is ephemeral. Disk
is durable. The protocol's anti-drift mechanisms work BECAUSE every
critical decision lands as a file other agents can read.

If you can't decide whether to write something to disk: **write it**.

## §9 Final check before you start

Confirm you have read:
- ☐ This prompt (you are reading it now)
- ☐ R3_README.md
- ☐ IMPLEMENTATION_PROTOCOL.md
- ☐ CONFUSION_CHECKPOINTS.md
- ☐ SELF_LEARNING_PROTOCOL.md
- ☐ _phase_status.yaml
- ☐ INVARIANTS_LEDGER.md
- ☐ operator_decisions/INDEX.md
- ☐ latest drift report
- ☐ SKILLS_MATRIX.md
- ☐ all memory entries listed in §7

Confirm you have:
- ☐ Written orientation report to `r3/_orientation/<your-tag>_<date>.md`
- ☐ Notified operator
- ☐ Awaiting operator authorization to start a specific phase

If all checks pass: notify operator with one-line summary of your
orientation report + your proposed next action. Wait.

If any check fails: read the missing artifact, then re-check. Do not
shortcut.

## Optional operator hints (uncomment + customize if needed)

<!--
<MODE>: <fresh_start|resume_existing>
<PHASE_HINT>: I'd like you to start with phase <PHASE_ID> if it's ready.
<URGENCY>: <low|normal|high>
<COLLABORATION>: <solo|paired_with_<agent_name>>
-->

--- END PROMPT ---

---

## Operator notes

**When to use this prompt**: at the start of a new agent session. Either
implementation kick-off (no phase complete) or resume after compaction
(several phases complete; new agent picks up).

**What it does**: forces orientation BEFORE execution. Catches packet-
integrity issues before they propagate. Surfaces operator-decision
needs upfront.

**What it doesn't do**: pick a phase autonomously. The agent proposes;
you authorize. This is intentional — phase selection has cross-cutting
implications (what's the critical path right now? are there blocking
gates? do we have parallel capacity?) that benefit from operator review.

**Expected output**: an orientation report at `r3/_orientation/` + a
notification message. Time to first output: 30-90 minutes (depending on
how many phase yamls / R2 evidence files the agent has to read).

**Then**: review the orientation report. Authorize a phase via
`templates/phase_prompt_template.md`. Or, if the agent surfaced a
packet-integrity issue, resolve it first.
