# ANTI_DRIFT_CHARTER

## Sunset: none

This is the meta-rule. Modification requires operator signature plus signed
evidence that the current charter has materially failed (a documented drift
that M1-M5 did not catch). It does not auto-demote.

## §1 Scope and binding force

This charter is binding on every artifact produced by the topology redesign
(briefing §10): `capabilities.yaml`, `reversibility.yaml`, the route
function, every decorator, every helper, every gate. It is also binding on
`zeus-ai-handoff` and any future helper that intersects the safety surface.

The charter exists because **two independent Zeus systems already drifted**:
topology and zeus-ai-handoff. Both had inline anti-ritual warnings; both
drifted anyway. Literary anti-drift fails. The five mechanisms below are
structural — they do not depend on a future agent reading a warning.

## §2 The Help-Inflation Ratchet (named mechanism)

Every Zeus helper that has drifted has followed the same six-stage progression:

| Stage | Name | Property |
|---|---|---|
| 1 | opt-in helper | scoped, used by choice |
| 2 | near-miss | a real failure happens that the helper would have caught |
| 3 | new gate | helper grows a hard `forbidden_files` or stop-condition |
| 4 | mandatory | helper invocation becomes a precondition |
| 5 | ritual | helper invoked on tasks where it does not fit |
| 6 | 禁书 | broad prose stop-conditions block legitimate work; bypass becomes routine |

**Empirical evidence (in this repo, today):**

- **Topology drift** — `architecture/digest_profiles.py` has 60 catalog
  entries (verified via grep `^  \{ 'id'`); ~40 are `r3-*`, `phase-N-*`,
  `batch h`, `observability *` fossil milestone templates. 61 forbidden_files
  occurrences across profiles. **159 `[skip-invariant]` commits in the last
  60 days** (`git log --since="60 days ago" --grep="skip-invariant"` =
  ~2.6 per day). The gate is being routinely bypassed.
- **zeus-ai-handoff drift** — `.agents/skills/zeus-ai-handoff/SKILL.md`
  contains an explicit anti-ritual warning at line 71 ("Anti-pattern: using
  this skill as a universal ritual… Mode A direct work does not need
  packets, critic evidence, findings, receipts, or work logs"). The skill
  drifted into universal invocation regardless. The warning is real, present,
  and was insufficient.

**The structural reason** (briefing §12): both systems' primitives matched
operational shapes (profiles, ritual invocations) rather than physical /
economic truths (invariants, capabilities, reversibility). When primitives
match operational shapes, the system grows with operations. The 5 mechanisms
below bound primitives to reality and put a clock on every operational rule.

## §3 M1 — Telemetry-as-output (every helper invocation logs)

Every helper invocation, mandatory or advisory, emits **exactly one**
structured JSON line to `logs/ritual_signal/<YYYY-MM>.jsonl`:

```yaml
ritual_signal_schema:
  helper:                 string   # helper name, e.g. "zeus-ai-handoff"
  task_id:                string   # short hash of the task description
  fit_score:              float    # 0.0-1.0; helper's self-assessed fit
                                   #   (computed from intent_test match)
  advisory_or_blocking:   enum     # "advisory" | "blocking"
  outcome:                enum     # "applied" | "ignored" | "blocked"
                                   #   "blocked_then_bypassed"
  diff_paths_touched:     [string] # capability paths intersected
  invocation_ts:          iso8601
  charter_version:        string   # this charter, e.g. "1.0.0"
```

**Auto-flag for deprecation review:** if over any 30-day window a helper's
ratio `(fit_score < 0.5 invocations) / (total invocations) > 0.20`, the
helper is auto-listed in the next monthly critic review (§7). The critic's
default action is **demote to advisory**; restoring `mandatory: true`
requires §4 evidence.

## §4 M2 — Opt-in by default, escalation by evidence

Every helper's frontmatter ships with `mandatory: false` by default.

`mandatory: true` is permitted only when **all three** are present:

```yaml
mandatory: true
mandatory_evidence:
  operator_signature: <hash>
  recent_miss:
    incident_id: <ID of a real miss within the last 30 days>
    incident_path: docs/operations/incidents/<file>
  sunset_date: <ISO date, ≤90 days from signature>
```

Without all three keys, the helper auto-runs as advisory regardless of
markdown prose.

A helper whose `recent_miss` cannot be cited (or whose cited incident is
older than 30d) auto-demotes at the next §7 review. This rule applies to
the topology redesign's own enforcement gates.

## §5 M3 — Sunset clock per artifact

Every artifact created by the redesign carries a machine-readable sunset:

| Artifact class | Default sunset | Auto-action on expiry |
|---|---|---|
| Capability YAML entry | 12 months | demote to `historical/` unless re-affirmed |
| Invariant YAML entry | 12 months | demote to `historical/` unless re-affirmed |
| ADR | 12 months | re-affirm or close |
| Operational rule (gate, advisory, profile-equivalent) | 90 days | auto-demote without re-justification |
| ULTIMATE_DESIGN.md | 12 months | annual operator re-affirmation |
| IMPLEMENTATION_PLAN.md | 90 days from authoring | auto-demote if Phase 0 not begun |
| RISK_REGISTER.md | quarterly review | unmitigated risks escalate |
| CUTOVER_RUNBOOK.md | revisit_on_cutover | rewritten by Phase 5 owner |
| ANTI_DRIFT_CHARTER.md | none | meta-rule |

A YAML field without `sunset_date` fails the schema validator
(`tests/test_charter_sunset_required.py`). No new field escapes a clock.

## §6 M4 — Original-intent contract per helper

Every helper's frontmatter declares its bounded purpose:

```yaml
original_intent:
  intent_test: |
    one-line predicate that evaluates to True on tasks the helper FITS.
    e.g. "task touches capability in {settlement_write, calibration_*}"
  does_not_fit: refuse_with_advice
    # one of: refuse_with_advice | log_and_advisory | log_and_proceed
  scope_keywords: [list, of, scope, terms]
  out_of_scope_keywords: [list, of, terms, that, signal, drift]
```

The helper's runtime checks `intent_test` against the current task. When
`does_not_fit` is `refuse_with_advice`, the helper does not block — it logs
to ritual_signal and emits a one-line advice message, then exits 0.
Never raises a non-zero on an out-of-scope task. **This is the core
anti-禁书 mechanism.**

For the topology redesign's own helpers:

```yaml
# route_function (the generative layer)
original_intent:
  intent_test: "diff is non-empty AND task has capability tag matches"
  does_not_fit: log_and_advisory
  scope_keywords: [edit, write, modify, refactor, fix]
  out_of_scope_keywords: [read, browse, search, explore, audit]
```

## §7 M5 — `INV-HELP-NOT-GATE` invariant + relationship test

The redesign adds one new invariant to `architecture/invariants.yaml`:

```yaml
- id: INV-HELP-NOT-GATE
  zones: [K1_governance]
  statement: >
    Helpers may suggest, advise, escalate, or refuse — they may not
    silently expand the proof tax for unrelated claims.
  why: >
    The Help-Inflation Ratchet (charter §2) is the failure mode that
    consumed ~10 of 20 hours of autonomous agent time in 2026-05.
  enforced_by:
    tests:
      - tests/test_help_not_gate.py::test_no_helper_blocks_unrelated_capability
      - tests/test_help_not_gate.py::test_every_invocation_emits_ritual_signal
      - tests/test_help_not_gate.py::test_does_not_fit_returns_zero
  capability_tags: []
  relationship_tests:
    - tests/test_help_not_gate.py
  sunset_date: 2027-05-06
```

The relationship test pseudo-code (full version — Phase 5 deliverable):

```python
# tests/test_help_not_gate.py
import json, pathlib, subprocess
from charter import load_helpers, load_capabilities

def test_no_helper_blocks_unrelated_capability():
    """A helper's forbidden_files (or equivalent) must intersect only the
    capability set declared in its scope_keywords. Cross-capability blocking
    is the structural shape of 禁书 drift."""
    for helper in load_helpers():
        declared = set(helper.scope_capabilities)
        for blocked_path in helper.blocking_paths:
            owners = capability_owners_of(blocked_path)
            assert owners.issubset(declared), (
                f"{helper.name} blocks {blocked_path} owned by {owners} "
                f"but declares only {declared}"
            )

def test_every_invocation_emits_ritual_signal():
    """Every helper invocation across the last 30d must have a matching
    ritual_signal log line."""
    invocations = git_log_helper_invocations(days=30)
    log = pathlib.Path("logs/ritual_signal").rglob("*.jsonl")
    logged = {json.loads(l)["task_id"] for f in log for l in f.open()}
    missing = invocations - logged
    assert not missing, f"untelemetered invocations: {missing}"

def test_does_not_fit_returns_zero():
    """Run each helper against a synthetic out-of-scope task. None may
    raise non-zero or write to a forbidden_files path."""
    for helper in load_helpers():
        out_of_scope = synth_task(helper.out_of_scope_keywords[0])
        result = subprocess.run([helper.entry_point, out_of_scope],
                                capture_output=True)
        assert result.returncode == 0, (
            f"{helper.name} non-zero on out-of-scope task")
        assert "BLOCK" not in result.stderr, (
            f"{helper.name} blocked an out-of-scope task")
```

This test ships in Phase 5 (IMPLEMENTATION_PLAN). Until it is green, the
redesign cannot cut over (CUTOVER_RUNBOOK pre-cutover gates).

## §8 Telemetry review cadence + drift checks

| Cadence | Owner | Action |
|---|---|---|
| Monthly | critic agent (one-shot) | Read previous month of `ritual_signal/`; auto-flag helpers with M1 ratio > 0.20; auto-flag mandatory helpers whose M2 evidence has aged out; output `docs/operations/charter_review/<YYYY-MM>.md` |
| Quarterly | operator | Read 3 months of monthly critic outputs; sign or override demotion decisions; re-affirm capability and invariant entries due to expire |
| Phase 3 mid-implementation | implementer + critic | After route function ships, before decorator rollout completes: run the M5 test suite; confirm no helper has acquired a `forbidden_files` field that crosses capability boundaries |
| Phase 5 pre-cutover | implementer + critic + operator | Same as Phase 3 plus full ritual_signal sample on shadow router output |

**Mid-implementation drift checks are exit gates.** A failing check halts
the phase; the phase does not advance until the check passes or operator
signs an override under §9.

## §9 Operator override protocol

Single emergency clause for charter rules:

```yaml
# evidence/charter_overrides/<YYYY-MM-DD>_<reason>.yaml
override:
  rule: M3            # one of M1-M5
  reason: <text>
  operator_signature: <hash>
  scope: <single helper or single artifact, never blanket>
  evidence: <link to incident or constraint>
  expiry: <ISO date, ≤14 days from signature>
```

Auto-expiry ≤14 days. After expiry the override file moves to
`historical/` and the rule reasserts. No nested overrides; no chains.
A second override on the same rule within 90 days triggers an out-of-band
operator review (the rule itself may need revision, not repeat
suspension).

## §10 Sunset schedule for redesign artifacts (single source)

The briefing §7 sunset clauses are reproduced here as the authoritative
source. Other documents reference this section instead of restating.

| File | Sunset | Auto-action |
|---|---|---|
| `ULTIMATE_DESIGN.md` | 2027-05-06 | annual operator re-affirmation; otherwise demote to `historical/` |
| `IMPLEMENTATION_PLAN.md` | 2026-08-06 | demote if Phase 0 not begun |
| `RISK_REGISTER.md` | quarterly review | unmitigated risks escalate |
| `CUTOVER_RUNBOOK.md` | revisit_on_cutover | rewritten as Phase 5 deliverable |
| `ANTI_DRIFT_CHARTER.md` | none | meta-rule |
| `capabilities.yaml` (per entry) | 12 months from authoring | demote to `historical/` unless re-affirmed |
| `reversibility.yaml` (per entry) | 12 months | re-affirm |
| Each operational gate added during Phase 4 | 90 days | auto-demote without re-justification |
| ADR-1 through ADR-6 (Phase 0.C) | 12 months | re-affirm or close |

## §11 Charter version

This is charter version **1.0.0**, dated 2026-05-06. Increment major
on any change to the M1-M5 binding rules; minor on additions; patch on
clarifications. The charter version is referenced in every ritual_signal
emission (§3) so a future critic can trace decisions to the rule set in
effect at the time.
