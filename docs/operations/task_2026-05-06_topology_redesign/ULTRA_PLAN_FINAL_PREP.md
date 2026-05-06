# Zeus Ultra Plan — Final Preparation: Anti-Drift Discipline

Status: DESIGN PROPOSAL, NOT CURRENT LAW
Date: 2026-05-06
Companion to: `PLAN.md`, `PLAN_AMENDMENT.md` (same folder)
Route: `operation planning packet`

This document is the final preparation layer before the Zeus topology redesign
ultra plan kicks off. It exists because the operator surfaced a meta-failure
that PLAN.md and PLAN_AMENDMENT.md do not address: **two Zeus help systems
both drifted from "guidance" into "禁书" (forbidden literature)**. Without a
structural defense, the redesigned topology will drift the same way within
6–12 months.

This document does not authorize implementation, schema migration, live
trading unlock, or any topology runtime change.

---

## §1 The Operator Complaint, Evidence-Grounded

### The lived 20-hour failure

In a recent multi-day object-meaning invariance worktree, an autonomous agent
ran for ~20 hours. Operator estimate: roughly **half the autonomous time was
spent on topology re-planning loops** — admission denied, agent re-plans,
admission denied again, re-plans. The agent did not progress; it
metabolized topology friction.

This is not a profile-tuning problem. It is a categorical failure of the help
system's design intent. Topology was supposed to **help** the agent route
safely. It became a **gate** that ate the agent's autonomous budget.

### Two systems, same drift signature

| System | Original intent | Current behavior |
|---|---|---|
| Topology / `topology_doctor` | Route hint for "what surfaces does this task affect" | Gate that blocks ambiguous read-only planning, requires re-plan loops, 220k-token bootstrap |
| `zeus-ai-handoff` skill | Capture details a compaction will lose so the next agent resumes cleanly | Universal ritual invoked on every task regardless of whether handoff is needed |

The signature is identical:
1. Originally an **opt-in helper** with a narrow purpose.
2. Each near-miss prompts a new gate / required field / mandatory invocation.
3. Helper becomes **mandatory for any task** to "be safe."
4. Mandatory becomes **ritual** — invoked even when the original purpose does not apply.
5. Ritual becomes **block** — agents cannot complete work without satisfying the now-irrelevant ceremony.

The handoff skill's own SKILL.md already contains an "Anti-pattern: using this
skill as a universal ritual" warning (`zeus-ai-handoff/SKILL.md:71`). The
skill author saw the drift and tried to prevent it with prose. **It did not
work.** Inline warnings are literary; drift is structural. Literature does
not stop structure.

### The implication

The redesigned topology must be **structurally drift-resistant**. The
existing PLAN.md and PLAN_AMENDMENT.md address what the system should *do*;
neither addresses how it **stays** what it does. Without anti-drift, the
ultra plan ships a v3 topology that is in the same drift trajectory.

---

## §2 The Drift Mechanism, Named

Call this the **Help-Inflation Ratchet**:

```
opt-in helper
    ↓ (a near-miss happens)
add a new gate / required field
    ↓ (avoid recurrence)
make invocation mandatory on relevant tasks
    ↓ (definition of "relevant" widens to be safe)
mandatory on most tasks
    ↓ (ritual habit)
mandatory on ALL tasks
    ↓ (gate blocks unrelated work)
help becomes 禁书
```

Each step is **locally rational**. Each step compounds. The ratchet has no
release: nothing in the design tells anyone "this gate is no longer load-
bearing — remove it." Removal requires evidence of non-use, but the gate
suppresses the evidence by blocking the cases that would prove it
unnecessary.

The defining property: **literary anti-ritual warnings do not stop the
ratchet.** zeus-ai-handoff proves this — its description already warns
against universal-ritual use, and it still gets used as universal ritual.

The fix must change the structure, not the prose.

---

## §3 Five Structural Anti-Drift Mechanisms

These are first-class requirements for the ultra plan. They apply to topology,
to zeus-ai-handoff, to any future Zeus helper, and recursively to the ultra
plan itself.

### M1. Telemetry as a first-class output

Every helper invocation must emit a structured telemetry record:

```yaml
helper: topology_doctor | zeus-ai-handoff | <other>
invoked_by: <agent id / human>
task_kind: <inferred from task description>
verdict: admit | block | advisory
admit_changed_outcome: bool   # did the helper's output change what the agent did?
block_was_correct: bool       # did the block prevent a real safety violation?
ritual_signal: bool           # was the helper invoked but its output ignored?
tokens_in: int
tokens_out: int
session_id: string
```

`ritual_signal: true` for >20% of invocations over a 30-day window = **mandatory deprecation review**. Not optional. Not "if someone notices." A scheduled job dispatches a critic agent to read the telemetry log and produce a deprecation recommendation. The operator can accept or override, but the review must run.

This is the release valve the ratchet currently lacks.

### M2. Opt-in by default, escalation by evidence

The current model: skill triggers on keyword match in user prompt or task description; topology runs on every commit/edit attempt. Both are auto-summoning by default.

The new model:

- **Helpers default to advisory output.** They never block.
- **Helpers fire on explicit invocation OR on a hard kernel hit** (live venue write, canonical truth rewrite, schema migration, settlement rebuild — the small set in PLAN.md §3.1, with each item structurally tied to a capability tag).
- **Helpers may emit "consider invoking me" hints** during normal flow, but the agent decides. Hint ≠ summon.
- **Mandatory invocation requires explicit operator-signed evidence** of a recent miss attributable to non-invocation. Without that evidence, a helper that wants to become mandatory cannot.

Keyword auto-summon (the current `zeus-ai-handoff` trigger model) becomes **suggestion only** — keyword match emits a hint, never invokes the skill body.

### M3. Sunset clock per rule

Every gate, profile, required field, or mandatory step has a `sunset_date`
field set at creation: 90 days by default. Before sunset, the rule's
maintainer (or a critic agent if maintainer absent) must re-justify with
evidence:

- How many real safety violations this rule caught in the period?
- How many false-blocks did it cause?
- If renewed, what is the next sunset?

Without re-justification, the rule **auto-demotes** to advisory at sunset. It
does not vanish; it stops blocking. This preserves the institutional knowledge
without preserving the ratchet.

The 35 fossil profiles in `digest_profiles.py` (PLAN_AMENDMENT.md §A) exist
because the system has no sunset. With sunset they would have been gone
months ago.

### M4. Original-intent contract per helper

Every helper carries an `original_intent` block in its frontmatter:

```yaml
original_intent:
  designed_for:
    - "<task profile A>"
    - "<task profile B>"
  not_designed_for:
    - "<negative profile A>"
    - "<negative profile B>"
  intent_test: |
    A short prose or pseudocode test that, given a task description,
    returns 'fits' / 'does_not_fit' / 'ambiguous'.
  drift_evidence_field: |
    Where in the telemetry log to look for drift signals.
```

When a helper is invoked, the runtime evaluates the intent test against the
task. `does_not_fit` → helper **refuses to run** with an explicit message:
"This task does not match my designed-for profile; my output would be ritual,
not signal." `ambiguous` → helper runs in advisory mode only.

The handoff skill's drift would have been impossible under M4: its
designed-for profile is "session-to-session transfer with non-derivable
detail loss." A read-only investigation does not fit. The intent test would
return `does_not_fit` and refuse.

### M5. Helper, not gatekeeper, invariant

A first-class invariant in `architecture/invariants.yaml`:

```
INV-HELP-NOT-GATE: A help system may suggest, advise, escalate, or refuse
to participate. It may not silently expand the proof tax for claims the task
does not make. Hard kernel violations are the only blocking output. All
other outputs are advisory and never gate task completion.
```

Wired to a relationship test:

`tests/test_help_systems_do_not_gate.py` — for each helper, simulates a
no-kernel task; asserts that helper's output is advisory; asserts task can
complete with helper's output ignored.

This is the structural counterpart to "禁书 is forbidden." Prose forbids;
INV-HELP-NOT-GATE structurally enforces. A new gate added to topology that
violates INV-HELP-NOT-GATE fails the test, fails CI, never lands.

---

## §4 zeus-ai-handoff Rescoping (parallel work stream)

Treat handoff rescoping as a **parallel, independent work stream** to topology
redesign. They share the anti-drift mechanism but are otherwise separable —
and shipping handoff rescoping first is small, low-risk, and produces direct
operator relief.

### Restored original intent

zeus-ai-handoff's narrow original purpose:

> Capture details that compaction or session boundary will lose, so the next
> agent picking up the task resumes cleanly without recreating context.

Anything else the skill currently does is drift.

### Concrete rescoping rules

1. **Auto-summon disabled.** Keyword matches on "handoff", "transfer", "compact" emit a *hint* in the agent's context: "consider invoking zeus-ai-handoff if context loss is imminent." No automatic invocation.

2. **Explicit invocation required.** The skill runs only when the agent or operator explicitly types `/oh-my-claudecode:zeus-ai-handoff` or equivalent. Implicit invocation through skill registry is removed.

3. **Original-intent test gates execution.** On invocation, the skill evaluates: "Does this task involve (a) imminent compaction, (b) session-to-session transfer, (c) non-derivable detail capture?" If none → skill refuses with explanation.

4. **Mode A direct stays Mode A direct.** §3 of the current SKILL.md defines Mode A (direct, ≤30 min, single file, reversible) — for these tasks, the skill emits a one-line "Mode A: do the edit, verify, close" and stops. No bundle, no critic, no receipts.

5. **Modes B/C/D require demonstrated profile match.** §3 of the current SKILL.md says "uncertain → Mode A." Strengthen this: Modes B/C/D require *positive* evidence that the task matches their profile (multi-batch, K0/K1, team disagreement, etc.). Default cannot be C or D.

6. **Telemetry per M1.** Every invocation logs `ritual_signal` (was the bundle ever read by the next agent?). >20% ritual rate → mandatory deprecation review per M1.

7. **Sunset clock per M3.** SKILL.md frontmatter adds `sunset_date: 2026-08-06`. Re-justification required by then with evidence of real handoff-loss-prevention.

### Deliverable

`docs/operations/task_2026-05-06_handoff_rescope/` (new packet, parallel to topology), containing:

- `PLAN.md` — rescoping spec following the rules above.
- `tests/test_zeus_ai_handoff_intent_gate.py` — relationship test for M4.
- One PR rewriting `zeus-ai-handoff/SKILL.md` with auto-summon removed.

Estimated effort: 3 days. Does not block topology redesign.

---

## §5 Topology Rescoping Under Anti-Drift

PLAN_AMENDMENT.md (§B–§E) already covers the structural redesign (capability +
invariant primitive, generative routing, hybrid router+verifier, 15-day
Phase 0). Apply the M1–M5 mechanisms on top:

### M1 wiring

`topology_doctor.py` Phase 0.A instrumentation already required (PLAN_AMENDMENT.md §E.0.A). Add the M1 fields to the log schema. The metrics already required for Phase 0.H decision gate (false-block rate, miss-on-irreversible) are exactly the M1 fields.

### M2 wiring

The "hybrid router+verifier" of ADR-2 (PLAN_AMENDMENT.md §E.0.C) is M2 in operational form: capability-gated tools at edit-time = hard kernel only; advisory route card at orient-time = M2 hint. Make the advisory output literally a hint, not a gate. Agents may proceed against advisory advice; only hard-kernel hits block.

### M3 wiring

Every entry in:
- `architecture/capabilities.yaml` (new file from Phase 0.B)
- `architecture/invariants.yaml`
- the surviving 26 first-principles profiles (after Phase 0.D fossil retirement)

…carries `created_date` and `sunset_date` (default 90d after creation or last re-justification). A scheduled critic agent runs monthly and produces a sunset report.

### M4 wiring

`topology_doctor` already takes `--task` and `--files`. Add an `original_intent` evaluation step: each capability and each surviving profile carries a `designed_for_intent_test` block. If the current task fails the test for the profile/capability, the route card emits *advisory* — it does not promote to a block.

### M5 wiring

Add `INV-HELP-NOT-GATE` to `architecture/invariants.yaml`. Wire to:

`tests/test_topology_doctor_does_not_gate_advisory.py` — for a synthetic task with no hard-kernel hit, asserts: route card is advisory, all advice is non-blocking, the task can complete with the route card ignored.

This single test is the structural counterpart to operator's "stop making help into 禁书."

### What this combination produces

A topology system that is:

- **Small at the kernel** (~15 capabilities + ~44 invariants + ~12 hard-kernel paths).
- **Generative at the advisory layer** (route card is computed on demand, not catalog-matched).
- **Drift-resistant** (telemetry → sunset → auto-demote).
- **Provably help-not-gate** (one test + one invariant).
- **Cheap** (≤30k token bootstrap, route card ≤500 tokens for T0).

---

## §6 The 20-Hour Autonomous Replay — The Acceptance Test

Operator pain is concrete. Acceptance criteria must match. The redesign is
considered successful if and only if **replaying the 20-hour autonomous
session under the new system shows ≤2 hours of topology-attributable
friction** (down from ~10).

### Concrete construction

1. Capture the autonomous session transcript and full topology_doctor invocation log from the original 20-hour run. Stored as the **canonical replay fixture** in `docs/operations/task_2026-05-06_topology_redesign/replay_fixture/`.

2. After Phase 0.E capability spike, run the same task descriptions and same diffs through the new admission logic offline.

3. Measure: for each invocation, was the new system's verdict (a) faster (token cost), (b) less blocking (admit when old blocked + post-hoc safe), (c) equally safe (no admit when post-hoc unsafe).

4. Sum the time the new system *would have* spent on topology re-planning. Acceptance if total ≤ 2h.

5. Operator review of the replay-attributed friction reduction is a Phase 0.H gate input.

### Why this matters

Without an acceptance test grounded in the original failure, the redesign
optimizes against intuition. With this test, the redesign is judged by the
exact pain it claims to fix. A redesign that ships a new model but does not
materially change the 20-hour-replay friction has not earned its complexity.

---

## §7 Phase 0 Update (delta from PLAN_AMENDMENT.md §E)

Add the following to PLAN_AMENDMENT.md Phase 0:

| Day | Original (PLAN_AMENDMENT) | Add |
|---|---|---|
| 1–5 | 0.A baseline | + add M1 telemetry schema; + capture replay fixture |
| 3–5 | 0.B capability catalog | + each capability gets `original_intent` block (M4) and `sunset_date` (M3) |
| 5–7 | 0.C 5 ADRs | + ADR-6 (anti-drift): operator signs the M1–M5 invariants as binding |
| 7–9 | 0.D fossil retirement | unchanged; this is the first M3 sunset firing |
| 9–12 | 0.E capability spike | + run 20-hour replay against new logic; produce friction-reduction report |
| 7–14 | 0.F shadow router | + emit M5 advisory-not-gate compliance log |
| 12–15 | 0.G replay-correctness gate | unchanged |
| Day 15 | 0.H operator GO/NO-GO | + GO criterion: 20-hour replay shows ≤2h friction; M5 test passes |

Parallel new work stream:

| Day | Stream | Task |
|---|---|---|
| 1–3 | Handoff rescope | Write `task_2026-05-06_handoff_rescope/PLAN.md` |
| 3–5 | Handoff rescope | Implement M4 intent-gate test + auto-summon disable PR |
| 5–7 | Handoff rescope | Operator review and merge |

Total ultra-plan preparation budget: 15 days topology + 7 days handoff = 22
calendar days, parallelizable to ~15.

---

## §8 Self-Sunset Provisions

Apply M3 to this very document and to the redesign itself:

- `PLAN.md`, `PLAN_AMENDMENT.md`, this `ULTRA_PLAN_FINAL_PREP.md` carry an implicit `sunset_date: 2026-08-06` (90 days). After that date, if Phase 0 has not begun or has not produced GO, these documents auto-demote to historical evidence (move to `docs/archives/`) and the operator is prompted to either re-justify or close.
- The redesigned topology system carries a `sunset_date: 2026-11-06` (6 months post-Phase E). At that date, M1 telemetry must demonstrate: (a) bootstrap token cost ≤30k, (b) route card ≤500 tokens for T0, (c) `[skip-invariant]` rate <10/60d, (d) replay-fixture friction ≤2h. If any fails, mandatory re-design or revert.
- The 5 ADRs from Phase 0.C carry `sunset_date: 2027-05-06` (12 months). Annual operator re-affirmation; otherwise auto-demote to historical.

This is the structural admission that "this design is good for now, not forever." The current topology grew without sunset and is now 39,800 lines. Sunset prevents the same accretion.

---

## §9 Operator Decisions Required Before Phase 0 Starts

These decisions cannot be deferred to ADR phase; they shape Phase 0 scope:

1. **Accept the M1–M5 anti-drift mechanisms as binding for the ultra plan?**
   Specifically: telemetry per M1, opt-in default per M2, sunset clock per M3,
   original-intent contract per M4, INV-HELP-NOT-GATE per M5. All five or
   none — partial adoption recreates the ratchet.

2. **Accept the 20-hour-replay acceptance test as the Phase 0.H GO gate?**
   This commits the redesign to a concrete operator-pain metric, not
   intuition.

3. **Approve parallel handoff rescoping work stream?** It is small and low
   risk, but it is a separate packet and consumes operator attention. If
   operator wants topology focus first, defer handoff rescoping to post-Phase
   0.H.

4. **Accept self-sunset for PLAN documents and the redesigned system?**
   This is the structural admission that today's design will eventually be
   wrong; without it, anti-drift is half-applied.

5. **Confirm the 20-hour autonomous session transcript is preserved and
   capturable as a replay fixture?** If the transcript is lost, the
   acceptance test must be reconstructed from a smaller proxy session, which
   weakens the gate.

---

## §10 The Single Sentence

If the ultra plan ships only one structural change, ship `INV-HELP-NOT-GATE`
plus its relationship test. Everything else in the redesign is optimization.
That one invariant + one test is the difference between a help system that
helps and a help system that becomes 禁书. It is the operator's complaint
expressed as code.

---

## §11 References

- `PLAN.md` — base topology redesign plan.
- `PLAN_AMENDMENT.md` — structural amendment (capability primitive, generative routing, Phase 0 outline).
- `.agents/skills/zeus-ai-handoff/SKILL.md:71` — "Anti-pattern: using this skill as a universal ritual" (the literary warning that did not stop drift).
- `.agents/skills/zeus-ai-handoff/SKILL.md:59-69` — Mode A/B/C/D selection table.
- `architecture/invariants.yaml` — target home for `INV-HELP-NOT-GATE`.
- `architecture/capabilities.yaml` — new file from Phase 0.B; carries `original_intent` per M4 and `sunset_date` per M3.
- `scripts/topology_doctor.py:147-210` — existing capability/claim hooks for M1 telemetry instrumentation.
- Anthropic, *Building Effective Agents* — minimal-footprint, capability-autonomy inverse: structural support for M2.
- AgentSpec (arxiv:2503.18666) — runtime DSL for trigger/predicate/action enforcement: structural support for M5.

---

## §12 Non-Goals

This document does not authorize live trading unlock, lock removal, production
DB writes, schema migration, settlement rebuild, redemption, report
publication, archive rewrite, or replacement of any current authority surface.
It revises the ultra-plan preparation only.
