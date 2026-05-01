# Self-Learning Protocol — Capture and propagate what you learn

This protocol turns implementation work into reusable knowledge. Without
it, every phase agent rediscovers the same lessons; with it, lessons
compound across the 20-phase plan.

**Foundation principle**: a learning that isn't on disk is going to
disappear at the next compaction. Treat every non-obvious discovery
as a candidate for capture.

---

## §1 What counts as a learning

You learned something worth capturing if:

1. **External SDK / API behavior** — a function returned a different
   shape than docs implied; an error code wasn't documented; an endpoint
   has rate-limit you didn't expect.
2. **Code edge case** — your test caught a bug that the spec didn't
   mention; a particular call sequence produces a non-obvious failure.
3. **Spec interpretation** — you resolved an ambiguity (via
   `deep-interview` or operator clarification) that future agents on
   the same phase would also hit.
4. **Tooling gotcha** — a skill behaved differently than expected; a
   particular agent prompt pattern produced bad results.
5. **Cross-phase coupling** — your phase touched something that the
   phase yaml didn't predict, and other phases will hit the same.
6. **Performance pattern** — a particular query / approach is slow;
   the alternative is fast.
7. **Test brittleness** — a fixture pattern fails under conditions the
   yaml doesn't mention.
8. **Plan vs reality drift** — the plan said X; reality says Y; here's
   why.

If your learning fits ANY of these, capture it. Capture cost is ~3-5
minutes; rediscovery cost is ~30-60 minutes per future agent that hits
the same.

---

## §2 The three buckets

### Bucket A: Phase-specific learning

Goes to: `r3/learnings/<phase_id>_<short_topic>.md`

Read by: future agents on the same phase (e.g., a re-scope or
re-implementation), and the wave-close multi-review.

Format:

```markdown
# <Short title>

Phase: <phase_id>
Author: <agent-tag>
Date: <YYYY-MM-DD>
Commit: <git rev-parse HEAD>

## What I learned
<2-4 sentences>

## Evidence
<URL, code citation, RPC response, test failure log>

## Why it matters
<2-3 sentences explaining downstream implications>

## What changed because of this
<the code / yaml / test that captures the learning>
```

Examples of phase-specific learnings:
- "Z2: V2 SDK's `create_order` returns `signed: bytes`, not `signed_order: dict`. Updated VenueSubmissionEnvelope.signed_order field type."
- "M3: WebSocket `MARKET` channel emits per-condition_id; `USER` channel emits per-funder_address. We need both."
- "F2: Platt fit is non-deterministic across SciPy versions; pin scipy==1.11.x in requirements.txt and frozen-replay snapshot bytes match across CI runs."

### Bucket B: Memory entry (cross-phase / cross-session)

Goes to: `~/.claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus/memory/feedback_<topic>.md`

Read by: ALL future agent sessions in the Zeus project, automatically
loaded at session start.

Use the `remember` skill to write this. Format follows the project's
memory schema (see existing `feedback_*.md` files for shape).

When a learning belongs in memory:
- It applies to ANY future Zeus work, not just this phase or this packet.
- It captures a generalizable principle, not a phase-specific detail.
- It would change the way a future agent approaches a NEW task.

Examples of memory-grade learnings:
- "When implementing async coroutines that integrate with apscheduler,
  ALWAYS dispatch the test suite under both event-loop modes (asyncio.run
  vs apscheduler-managed) — different scheduling semantics."
- "py-clob-client-v2 SDK uses Python's `Decimal` for prices but converts
  to `str` at the HTTP boundary; never assume float round-trips."
- "When designing exchange-side reconciliation: position-drift findings
  must include 1-cycle suppression for newly-FILLED commands, otherwise
  every cycle floods findings with false-positives during normal trading."

DO NOT promote learnings that are phase-specific (those go in Bucket A).

### Bucket C: Protocol evolution

Goes to: `r3/_protocol_evolution/<short_topic>.md`

Read by: operator + critic-opus reviewer; may trigger amendment to
`IMPLEMENTATION_PROTOCOL.md`, `CONFUSION_CHECKPOINTS.md`,
`SKILLS_MATRIX.md`, or `templates/phase_prompt_template.md`.

When a learning belongs here:
- It exposes a gap in the protocol itself, not just in your phase.
- A future agent following the protocol-as-written will repeat the
  problem you just hit.
- The protocol document needs an amendment.

Format:

```markdown
# <Short title>

Author: <agent-tag>
Date: <YYYY-MM-DD>
Phase context: <phase_id>

## Current protocol says
<quote from IMPLEMENTATION_PROTOCOL.md / CONFUSION_CHECKPOINTS.md / SKILLS_MATRIX.md>

## Reality showed
<what happened>

## Proposed amendment
<exact text to add / change / remove>

## Evidence
<commit hash, _confusion/ link, _orientation/ link>

## Risk if NOT amended
<what's the next agent's predictable failure if we don't update?>
```

Examples:
- "CONFUSION_CHECKPOINTS.md is missing a CC-13 for 'discovering that an
  R2 card was already partially shipped at HEAD'. Three phases hit this;
  add explicit checkpoint."
- "SKILLS_MATRIX recommends `architect` for schema design but doesn't
  say it must be READ-ONLY (architect is read-only by default but the
  matrix should make it explicit; saw a misuse where agent invoked
  architect to write code)."

---

## §3 When to capture (the moment matters)

Capture at the moment of discovery, NOT at end of phase:

| Moment | Action |
|---|---|
| Just verified an external fact via WebFetch / RPC | Bucket A: write to `learnings/<phase>_<topic>.md` immediately |
| Resolved an ambiguity via `deep-interview` | Bucket A: write to learnings immediately + maybe Bucket B if generalizable |
| Spotted a test that was passing for the wrong reason | Bucket A + Bucket C if it suggests a missing CC checkpoint |
| Found that a citation rotted | Bucket A (the fix) + run drift_check to see if other phases share the rot |
| Found a cross-phase coupling the yaml didn't predict | Bucket A + write `_cross_phase_question.md` + maybe Bucket C |
| Hit a confusion checkpoint not in CC-1..CC-12 | Bucket C: propose a new CC |
| Operator clarified something | Bucket A (capture the clarification) + maybe Bucket B if widely applicable |
| Phase complete, end-of-phase retro | Run all 3 buckets for any straggler learnings |

**End-of-phase retrospective**: at the end of each phase, write
`r3/learnings/<phase>_<author>_<date>_retro.md` with:
- 3-5 things you learned during this phase
- 1-2 things that should propagate to memory
- 1-2 protocol gaps you spotted

This retro is part of the phase's exit criteria.

---

## §4 How to write a learning that's actually useful

Bad learning (too vague):
> "M3 WebSocket integration was hard. Be careful with auth."

Good learning (actionable):
> "M3: Polymarket V2 WebSocket auth requires `Authorization: Bearer
> <api_key>` header AND a per-message HMAC signature using `hmac.new(
> secret, condition_id, sha256)`. The HMAC requirement is undocumented
> (verified by RPC trace at evidence link). If only the Bearer header
> is sent, server returns `subscribe_ok: false` with error
> `signature_required` — but the connection STAYS OPEN, so naive code
> sees `connected=True` and assumes auth succeeded. Antibody added:
> tests/test_user_channel_ingest.py::test_subscription_auth_failure_blocks_new_submit."

The good version cites evidence, names the failure mode, names the
antibody, and tells future agents what to look for.

---

## §5 Bucket selection heuristics

When unsure which bucket:

- **Will the same agent on the same phase need this?** → Bucket A
- **Will a different agent on a different phase need this?** → Bucket B
- **Should the protocol document be amended?** → Bucket C
- **Could be either A or B?** → Write A first; promote to B at end of
  phase if it's clearly generalizable.

When in doubt: **write A**. Phase-specific learning that's read by 1
future agent is still worth capturing. Bucket A is cheap; Bucket B
should be reserved for actually-generalizable lessons.

---

## §6 Reading what others wrote

Cold-start agent at phase boot reads (per `templates/phase_prompt_template.md`):
1. ALL `r3/learnings/<your_phase>_*.md` files
2. ALL recent `r3/learnings/*_retro.md` from the last 5 completed phases
3. ALL relevant `feedback_<phase>_*.md` memory entries
4. ALL `r3/_protocol_evolution/*.md` files (some may already be
   incorporated; check edit dates against `IMPLEMENTATION_PROTOCOL.md`)

This is part of the boot protocol — see `PHASE_BOOT_PROTOCOL.md`.

---

## §7 Operator workflow

Operator reviews `r3/learnings/`, `r3/_protocol_evolution/`, and proposed
memory entries periodically (recommended: at every wave close + ad-hoc
when agents flag urgency).

Operator decisions:
- **Approve a Bucket B promotion** → operator runs `remember` skill or
  authorizes the agent to do so.
- **Amend the protocol** (Bucket C) → operator edits the
  `IMPLEMENTATION_PROTOCOL.md` / `CONFUSION_CHECKPOINTS.md` /
  `SKILLS_MATRIX.md` and marks the `_protocol_evolution/` file as
  RESOLVED with link to the amending commit.
- **Decline an amendment** → operator marks `_protocol_evolution/` file
  as DECLINED with rationale; learning stays as Bucket A.

---

## §8 Memory cross-references

This protocol pairs with:
- `feedback_lifecycle_decomposition_for_execution` (R3 structure)
- `feedback_long_horizon_multi_agent_anti_drift` (the parent protocol)
- `feedback_multi_angle_review_at_packet_close` (wave close review)

It also seeds NEW memory entries — every Bucket B promotion creates a
new `feedback_*.md` entry that future Zeus sessions consume.

---

## §9 The compounding effect

After 20 phases, expect:
- **~80-150 Bucket A learnings** at `r3/learnings/` — phase-specific
  lessons that helped that phase ship.
- **~10-20 Bucket B promotions** — generalizable lessons that improve
  ALL future Zeus work.
- **~5-10 Bucket C amendments** — protocol improvements that improve ALL
  future multi-agent plans.

This is the difference between "we shipped R3" and "we shipped R3 AND
we got better at shipping". Both happen via this protocol.

If you don't write learnings, the second outcome doesn't happen.
