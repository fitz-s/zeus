# Zeus Agent Runtime — Compounding Improvement Plan

**Date:** 2026-05-16 (revised same day after 4-critic adversarial pass)
**Parent:** `docs/operations/zeus_system_review_2026-05-16.md` (Parts 4, 6 Tier 2, 7)
**Scope:** Agent runtime (not Zeus trading domain). Mixed split: instrumentation in `~/.claude`, antibody specs in `zeus/.claude`.
**Philosophy:** Finite enumeration first. Mechanize already-known predictable patterns. Defer open-ended telemetry-loop infrastructure to Wave 2.

**Revision history:** v1 first draft. v2 applied 5 SEV-1 + 10 SEV-2 from 4-critic round 1 (§9 v2 table). v2.1 (this file) applies 1 MAJOR + 3 MINOR from A2 closed-loop verification (§9 v2.1 table).

---

## 1. Net-New Finding: A Family of Read-Back Defects, Not One Isomorphism

The parent draft Part 7 names MEMORY.md as write-only. Investigation found a **family of related read-back defects**, but they decompose into three distinct shapes — not one isomorphism (this is the v2 correction; v1 over-unified them).

**Bucket A — Writer with no runtime/operator-facing consumer:**

| Surface | Writes happen | Existing consumer | Evidence |
|---------|---------------|-------------------|----------|
| `state/refit_armed.json` (drift detector) | Daily | None | Parent draft Part 1 |
| `MEMORY.md` 45 feedback entries | Per session | Agent self-skim (per parent Part 7) | Parent draft Part 7 |
| `~/.claude/telemetry/*.jsonl` | 18,848 events across 372 JSONL files (`1p_failed_events.*.jsonl`) | None | `ls ~/.claude/telemetry \| wc -l` = 372; `wc -l ~/.claude/telemetry/* \| tail -1` = 18,848 |
| Hook `telemetry.ritual_signal_emitted: true` | Per hook invocation | Test contract only (`tests/test_zeus_risk_halt_e2e.py:130`) — no runtime/operator-facing consumer | `.claude/hooks/registry.yaml` (multiple) |

**Bucket B — Computation done, persistence layer missing:**

| Surface | Status | Evidence |
|---------|--------|----------|
| `src/state/edge_observation.py` | Read-only computation library with 5 active consumers (`attribution_drift.py:57`, `calibration_observation.py:94`, `learning_loop_observation.py:128`, `ws_poll_reaction.py:70`, `scripts/edge_observation_weekly.py`); explicit `NO write path` per `:10-11`. Computation produced, time-series persistence missing. | Parent draft Part 3.D |
| `DriftReport` | Transient dataclass returned per `compute_drift()` call (`src/calibration/retrain_trigger_v2.py`). Time-series persistence not built (parent draft Part 3.B). | Parent draft Part 3.B |

**Bucket C — Write path admitted but downstream promotion/triage missing:**

| Surface | Status | Evidence |
|---------|--------|----------|
| `architecture/improvement_backlog.yaml` | Topology admits writes (`architecture/topology.yaml:1264-1287,3403,3493,3580`); test enforcement (`tests/test_topology_doctor.py:4539`, `tests/test_digest_profile_matching.py:473-516`). Operator-honor-system triage; V2 promotion mechanism deferred. | `architecture/improvement_backlog.yaml:11-19` |

**Why this matters for the plan.** The three buckets need different remediation shapes — Bucket A needs a reader/aggregator, Bucket B needs persistence, Bucket C needs a triage promotion step. The v1 framing "one architectural decision applied seven times" was wrong; the correct framing is "three related but architecturally distinct read-back defects, with Wave 1 addressing 2 of 5 Bucket A surfaces (telemetry via W1.3, MEMORY via W1.6) and 0 of Buckets B/C." Coverage gap acknowledged in §6.

---

## 2. The ADVISORY-Only Constraint Reshapes Compounding Vectors

Hooks were demoted to ADVISORY-only on 2026-05-07 (`docs/operations/task_2026-05-07_hook_redesign_v2/PLAN.md`). Enforcement is gone as a leverage path. Compounding can only ride three vectors:

- **OA — Operator attention.** Discord alert, SessionStart priority context injection, daily digest read by the operator.
- **MA — Mechanical artifact.** Auto-generated tests, types, hook specs, skill templates. Once shipped, executes without active agent participation.
- **CL — Closed-loop measurement.** Violation count drives auto-promotion / sunset; the artifact gets better over time without manual curation.

A "compounding" candidate that does not use one of these is actually just accumulation (linear value per use, not compounding).

**v2 note on the DriftReport analogy:** The v1 framing called DriftReport "the canonical write-back pattern." This was overstated — DriftReport is a transient dataclass; the time-series persistence layer the analogy invokes is itself unbuilt (parent Part 3.B). The honest framing: parent draft prescribes the pattern; Zeus has not yet built it domain-side either. The agent runtime work below should not assume a working template exists to copy.

---

## 3. Wave 1 Candidate Matrix

Seven candidates. Each tagged with vector(s). Definition of strict compounding: per-use signal/cost/accuracy improves over time as the artifact corpus grows.

| # | Candidate | Vector | Wave-1 classification | Risk class |
|---|-----------|--------|----------------------|------------|
| W1.1 | Probe-3-rule template (verdicts require 3 probes) | OA | Linear | Instrument |
| W1.2 | Citation grep-gate (PreToolUse on Edit/Write) | MA | Linear | **Act** |
| W1.3 | Tier-cost daily digest | CL + OA | **Strict** *(conditional on signal→behavior step, see §4)* | Instrument |
| W1.4 | SCAFFOLD→critic as mandatory skill phase | MA | Linear | Instrument |
| W1.5 | Session handoff Stop-hook + SessionStart loader | OA + accumulation | **Strict** *(bounded by 500-char/last-3 retrieval ceiling)* | **Act** |
| W1.6 | MEMORY citation self-audit + repair lane | MA + CL | **Strict** *(only if W1.6b repair lane ships)* | Instrument + light Act |
| W1.7 | `antibody_hook_spec:` field on MEMORY entries | MA pipeline seed | **Linear pipeline-seed in Wave 1** *(potentially Strict in Wave 2)* | Instrument |

Strict-compounding subset (revised after critic pass): 3 candidates with explicit conditions — W1.3 needs signal→behavior wiring, W1.5 acknowledges retrieval ceiling, W1.6 strict only if repair lane (W1.6b) ships. W1.7 reclassified from v1's "Strict" to **Linear pipeline-seed** because schema-only work has no in-Wave-1 per-session benefit (Critic A+C SEV-2 consensus).

Risk classification: **Instrument** = pure read, no new writes/hooks/cron; ship today without governance. **Act** = writes/hooks/cron, runtime risk; require the operator walkthrough defined in §5.

---

## 4. Wave 1 Unit Specs

### W1.1 — Probe-3-Rule Verdict Template (Instrument, OA)

**Pain anchor:** `feedback_one_failed_test_is_not_a_diagnosis` — named "dominant orchestrator failure mode".

**Mechanism:** Skill template addition. Any BLOCKED / GREEN / verified verdict the orchestrator emits must populate a `probes: [p1, p2, p3]` field with three distinct probes. Empty or <3 → skill emits "verdict-malformed" warning.

**Files (v2 corrected — drop `oh-my-claudecode:` namespace prefix; filesystem paths are bare):**
- `~/.claude/skills/verify/SKILL.md` — add probe-3 contract section
- `~/.claude/skills/ultraqa/SKILL.md` — same
- New: `~/.claude/skills/verify/templates/verdict.md` — canonical verdict shape

**Cost:** ~30 lines of template across 2-3 skill files.
**Risk:** Instrument. Skill instruction only.
**Ship gate:** Self-test — orchestrator emits malformed verdict in test session, verify skill catches it.

### W1.2 — Citation Grep-Gate (Act, MA)

**Pain anchor:** `feedback_grep_gate_before_contract_lock` + `feedback_zeus_plan_citations_rot_fast` — 20-30% premise mismatch within 10 minutes.

**Mechanism:** PreToolUse hook on `Edit` / `Write`. Parse `old_string` / `new_string` for `file_path:line_number` patterns; verify each cited line still exists. Drift → ADVISORY context block.

**Files:**
- `zeus/.claude/hooks/registry.yaml` — add `citation_grep_gate` entry, severity ADVISORY, sunset 2026-08-16
- `zeus/.claude/hooks/citation_grep_gate.py` — implementation
- `zeus/architecture/antibody_specs.yaml` (new) — citation pattern regex spec (must handle both `:line` and `Lline` forms — Critic D minor finding)

**Cost:** ~80 lines hook code + 1 spec entry.
**Risk:** Act. See §5 for full walkthrough gate.
**Ship gate:** See §5 Act gate.

### W1.3 — Tier-Cost Daily Digest (Instrument, CL+OA, **Strict-conditional**)

**Pain anchor:** `feedback_orchestrator_offload_lookups` + `feedback_long_opus_revision_briefs_timeout`.

**Mechanism — revised after Critic D SEV-2 + v2.2 actual-inventory results:** Read-only aggregator over `~/.claude/telemetry/*.jsonl` (372 files, 18,848 events, 60-day date range 2026-03-16 to 2026-05-15). Field inventory completed 2026-05-16 (`~/.claude/skills/runtime-digest/TELEMETRY_FIELDS.md`); 84 unique `event_name` values enumerated.

**Derivable metrics (confirmed by inventory):**
- Per-day `model` distribution → `haiku_share` / `sonnet_share` / `opus_share` (proxy for orchestrator tier discipline)
- Sessions per day, events per day, top event_names by frequency
- 7-day moving averages over the above

**Non-derivable per inventory (DO NOT revive in future revisions):** `token_cost`, `haiku_eligible_opus_waste` (no `tool_name` in payload), `tool_call_duration`, `brief_length`, `retry_reason`. Plan v1+v2 metrics naming these were aspirational; actual telemetry payload doesn't carry them. If operator wants these, separate work: instrument the agent harness to emit them.

**Baseline (v2.2):** week-1 (2026-03-16 to -22) `haiku_share` average = **0.836**. This is the v2.2 named primary metric for §8 criterion 1.

**Signal→behavior loop:** Daily digest writes one line into SessionStart priority context budget: `"Last week: haiku_share = <value>, target ≥0.85 (rises = tier discipline improved)"`. Without this wiring CL→OA-only and degrades to accumulation, not strict compounding.

**Files (v2.3 corrected — scheduler is macOS `crontab -e`, not openclaw cron. Pre-PR critic CRITICAL #1: openclaw `cron/jobs.json` schema is `agentTurn`-payload-based, not flat `{name,kind,expr,tz,cmd}` as v2/v2.1 wrote. Pivoted to plain macOS cron for zero-schema-risk operator path.):**
- `~/.claude/skills/runtime-digest/aggregator.py` — telemetry parser (stdlib only)
- `~/.claude/skills/runtime-digest/TELEMETRY_FIELDS.md` — field inventory (ship-gate artifact)
- `~/.claude/skills/runtime-digest/CRONTAB_LINES.txt` — exact two lines operator pastes via `crontab -e` (08:30 daily + Monday 08:00 weekly for W1.6a); uses `/usr/bin/python3` explicitly (no PATH ambiguity per critic MAJOR #4)
- `~/.claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus/runtime_digest/` — output dir (`mkdir -p` on first run; per-machine-per-user; portability caveat §7)

**Cost:** Actual: 213-line aggregator + 200-line TELEMETRY_FIELDS.md + 12-line CRONTAB_LINES.txt.
**Risk:** Instrument. Pure read on existing files; cron writes only into project-local output dir. Activation = operator runs `crontab -e`.
**Ship gate — MET 2026-05-16:** Field inventory written; aggregator metrics cite only enumerated fields; runtime 0.24s (target <60s, met by 250×); baseline JSON non-empty; second-run watermark verified. Remaining gate: operator pastes CRONTAB_LINES into `crontab -e`.

### W1.4 — SCAFFOLD→Critic Mandatory Skill Phase (Instrument, MA)

**Pain anchor:** `feedback_opus_critic_on_architectural_scaffold_4_for_4_roi` — 4/4 SEV-1 catches.

**Mechanism:** Add mandatory `scaffold_critic_dispatch` phase to `orchestrator-delivery` skill. After any SCAFFOLD/architecture/spec phase, skill emits critic dispatch automatically (opus, brief = scaffold + invariants ref). **v2 change per Critic B SEV-3:** Skip mechanism removed — if ROI is 4/4, don't make skipping easy. Single mandatory path, no `--skip-critic` escape hatch.

**Files (v2 corrected — drop namespace prefix):**
- `~/.claude/skills/orchestrator-delivery/SKILL.md` — add phase
- `~/.claude/skills/orchestrator-delivery/templates/scaffold_critic_brief.md` — canonical brief

**Cost:** ~50 lines.
**Risk:** Instrument. Skill template change only.
**Ship gate:** Run orchestrator-delivery on a no-op scaffold; verify critic dispatch fires.

### W1.5 — Session Handoff Stop-Hook + SessionStart Loader (Act, OA+accumulation, **Strict** with bounded retrieval)

**Pain anchor:** Parent draft Part 4 blind spot — "Session-to-session reasoning continuity loss."

**Mechanism (v2 corrected — Critic B+D SEV-1 fix):** v1 routed the write through `~/.claude/hooks/post-tool-use.mjs`, which is a PostToolUse handler and never fires on session close. Correct path: register a new `~/.claude/hooks/session-handoff.mjs` as a **third Stop-event hook** in `~/.claude/settings.json` (joining `persistent-mode.mjs` + `code-simplifier.mjs`). Before registering, executor must read `persistent-mode.mjs` in full to confirm no schema collision on stop artifacts.

Handoff schema (yaml):
```yaml
session_id: <uuid>
date: 2026-05-16
verified: [<file:line refs the agent re-grepped and confirmed>]
attempted_failed: [<approaches tried that didn't work>]
files_audited: [<paths and audit verdict CURRENT/STALE/DEAD>]
open_questions: [<unresolved>]
```

**Retrieval ceiling acknowledged:** SessionStart loader reads latest 3 handoffs under 500-char budget. Per Critic C minor: re-derivation cost asymptotes to a floor set by the 500-char + last-3 selector, not zero. W2.B knowledge graph addresses this if needed.

**Files (v2 corrected):**
- `~/.claude/hooks/lib/session-handoff.mjs` — schema writer
- `~/.claude/hooks/session-handoff.mjs` — **new file**, registered as Stop hook
- `~/.claude/hooks/session-start.mjs` — read & inject (existing file, add ~30 lines)
- `~/.claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus/handoffs/<date>-<session_id>.yaml` — output (v2.1: filename includes session_id to prevent concurrent-session overwrite per A2 MINOR; SessionStart reader picks latest 3 by mtime)

**Cost:** ~150 lines spanning new Stop hook + SessionStart loader. Actual: 277 lines (Worker F shipped writer+loader+lib+settings-patch).
**Risk:** Act. See §5 for walkthrough gate. Additional concern: handoff write must not add >50ms to session shutdown (measure before enabling).
**Compounding:** Each session contributes one handoff; after 30 sessions, 30-day corpus of pre-verified evidence. Subject to retrieval ceiling above.
**Ship gate:** Two consecutive sessions show handoff-write + handoff-load + ≥1 prior-handoff citation reused.

**v2.3 honest gap (pre-PR critic MAJOR #3):** Worker F's implementation expects an agent-written staging file at `<cwd>/.omc/state/sessions/<sessionId>/handoff-draft.yaml`. The plan v1-v2.2 spec'd the OUTPUT schema but never named the WRITER. Without an agent-side writer the handoff is empty-stub-only and §8 criterion 2 (≥30% sessions cite handoff) is structurally unreachable. **Wave 1 ships W1.5a (stub writer + reader infra); writer logic deferred to W1.5b (separate unit, post-Wave-1):** either (i) instruct agents via SKILL.md addition to write the draft before session end, OR (ii) make Stop hook auto-extract verified citations from the session JSONL transcript (more robust, more work). Until W1.5b lands, §8 criterion 2 is downgraded to "≥1 non-empty handoff written" as the minimum signal that infrastructure works.

### W1.6 — MEMORY Citation Self-Audit + Repair Lane (Instrument + light Act, MA+CL, **Strict** only with W1.6b)

**Pain anchor:** `feedback_audit_of_audit_antibody_recursive` — recursive trust-but-verify.

**Mechanism — split into two units per Critic B+C SEV-2 consensus:**

- **W1.6a (audit, Instrument):** Weekly scan of 46 `feedback_*.md` files for `file_path:line` and `file.py L<num>` patterns; grep cited content; flag drift. Output: `~/.claude/projects/.../memory_audit_<date>.md` with `CURRENT / STALE / DEAD` verdicts.
- **W1.6b (repair lane, light Act) — required for strict-compounding claim to hold:** Audit output triggers operator-triaged repair pass. Repair workflow: STALE entries flagged → operator confirms each → mechanical correction PR for unambiguous cases (line shift within same file with matching content), manual edit for renamed/deleted. Without W1.6b the count plateaus after one cycle (per Critic C).

**Files (v2.3 corrected — macOS crontab not openclaw cron, see W1.3 explanation):**
- `~/.claude/skills/runtime-digest/memory_audit.py` (co-located with W1.3) — stdlib only
- `~/.claude/skills/runtime-digest/CRONTAB_LINES.txt` — Monday 08:00 line included (shared file with W1.3)
- `scripts/memory_repair_apply.py` (Zeus repo) — repair pass automation. **v2.3 patch (pre-PR critic CRITICAL #2):** `_anchor()` pre-citation backtick fallback removed; "no anchor → manual review" is safer than wrong anchor from prose preamble.

**Cost:** ~80 lines audit + ~100 lines repair pass + plist.
**Risk:** W1.6a Instrument; W1.6b light Act (writes back to MEMORY repo).
**Compounding mechanism:** STALE-citation count → repair → count drops → MEMORY trust grows. Without W1.6b, mechanism is accumulation only.
**Ship gate:** First W1.6a run produces verdicts for all 46 entries; first W1.6b run lands ≥3 repair commits OR documents why no repair was needed.

### W1.7 — `antibody_hook_spec:` Field on MEMORY Entries (Instrument, **Linear pipeline-seed in Wave 1**)

**Pain anchor:** `feedback_frontload_predictable_remediation` + Fitz Constraint #1.

**Mechanism:** Add optional yaml frontmatter field (existing frontmatter is additive-safe per Critic D minor — entries already use `name/description/type/originSessionId`):
```yaml
antibody_hook_spec:
  vector: MA | OA | CL
  artifact_type: hook | test | type | skill_template | digest_metric
  trigger: <when the antibody fires>
  detection: <how it detects the failure mode>
  promotion_criteria: <what makes this graduate to actual artifact>
```

**v2 classification correction (Critic A+C SEV-2 consensus):** Schema-only Wave 1 work does NOT compound in Wave 1. Compounding accrues only when (a) future entries populate the field and (b) Wave 2 auto-promotion materializes — both lie outside Wave 1. Honest label: **Linear pipeline-seed.**

**Wave-1-internal adoption metric (added per Critic C MAJOR):** Track `% of new feedback entries written during Wave 1 with field populated / total new entries`. Target ≥80% by Wave 1 close. This makes the schema's adoption observable inside Wave 1 rather than waiting for Wave 2.

**Files:**
- `~/.claude/projects/.../memory/SCHEMA.md` (new) — frontmatter spec
- Backfill: 5 highest-violation feedback entries get field populated as exemplar (W1.6a identifies these)

**Cost:** ~40 lines schema + 5 backfill edits.
**Risk:** Instrument. Documentation only.
**Ship gate:** 5 backfill entries reviewed; schema file lints; adoption metric baselined at zero.

---

## 5. Ordering, Dependencies, and the Act Gate

```
Day 0  →  W1.7 (schema, 1 hr)          [Instrument]
Day 0  →  W1.1 (probe-3 template, 1 hr) [Instrument]
Day 1  →  W1.4 (SCAFFOLD critic, 1 hr)  [Instrument]
Day 1  →  W1.6a (MEMORY audit, 3 hr)    [Instrument]
Day 2  →  W1.3 telemetry field inventory (1 hr) [Instrument prereq]
Day 3  →  W1.3 aggregator + crontab line (4 hr) [Instrument]
Week 2 →  W1.6b repair lane (3 hr)      [light Act, see gate]
Week 2 →  W1.5 (session handoff, 6 hr)  [Act, see gate]
Week 2 →  W1.2 (citation grep-gate, 4 hr) [Act, see gate]
```

Cumulative cost: ~24 hours focused implementation across 2 weeks (v1 said 20; revised after splitting W1.6 into a/b and adding field-inventory prereq).

**Act gate — specified concretely per Critic B SEV-2 to mirror the calibration-retrain bar (parent draft Part 6 Tier 0):**

| Condition | Specification |
|-----------|---------------|
| 1. False-positive threshold | <5% over 7-day dry-run window (W1.2 citation gate); <2% spurious handoff writes (W1.5) |
| 2. Performance budget | <50ms added to session shutdown (W1.5); <100ms per Edit/Write call (W1.2) |
| 3. Named rollback action | Remove hook entry from `~/.claude/settings.json` (W1.5) or `zeus/.claude/hooks/registry.yaml` (W1.2); documented in unit's ship-gate file |
| 4. Named operator confirmation | Operator (Fitz) explicitly approves promotion from dry-run-log to user-facing ADVISORY emission; recorded in `docs/operations/act_gate_decisions.md` |

All four must be met before any Act item promotes from dry-run to live emission.

---

## 6. Wave 2 — Deferred (Criteria for Revisiting)

**Honest acknowledgment per Critic A SEV-2:** §1 frames write-only as endemic across 4 Bucket A surfaces, but Wave 1 only meaningfully addresses 2 (telemetry via W1.3, MEMORY via W1.6). The other 2 Bucket A surfaces (`state/refit_armed.json`, hook `ritual_signal_emitted`) and all of Buckets B+C are not in Wave 1. This is a deliberate scope choice (finite enumeration) but should not be misread as full coverage.

**W2.A — Telemetry violation tagging + auto-promotion cycle.** The actual loop-closer for the MEMORY-rule-violation question (W1.3 is only aggregation, not violation tagging).

**Revisit criteria (now falsifiable):** Trigger Wave 2 evaluation if **any** of these holds at 2026-06-15:
- W1.3 digest identifies ≥1 named pattern that W1.7's `antibody_hook_spec` field cannot pre-declare, OR
- Operator observes ≥3 repeat-violation incidents in Wave 1 window for rules that have a populated `antibody_hook_spec`, OR
- W1.6a audit shows STALE+DEAD count >50% of citations (indicates MEMORY itself can't be relied on; needs structural rework not incremental repair)

**W2.B — Session knowledge graph from JSONL transcripts.** Addresses the W1.5 retrieval ceiling (500-char / last-3 floor). Auto-tag every session by topic/files-touched/verdict via sonnet jobs; query via existing `session_search` MCP skill.

**Revisit criteria:** W1.5 handoff infrastructure proven over 30 sessions AND operator records ≥3 instances of "I needed older context that handoffs alone didn't surface."

**W2.C — Bucket B persistence and Bucket C triage promotion.** Separately scoped; outside agent-runtime; falls to Zeus trading-domain follow-up.

---

## 7. What This Plan Does Not Address (And Why)

- **Back-fill of historical telemetry into the digest.** W1.3 is forward-rolling only. Back-fill is one-time research value, separable.
- **Restoring blocking hooks.** Parent draft Part 5 verified PR #74 fixed three active bugs; all Wave 1 hooks ADVISORY by design.
- **Cross-OMC-project portability.** Per user decision, `~/.claude/skills/runtime-digest/` is global and will benefit Mars/Jupiter/Neptune once those workspaces have telemetry; Zeus-local items (W1.2, antibody_specs.yaml) intentionally domain-bound.
- **Discord wiring for Wave 1 digests.** Out of scope; operator reads JSON. Separable follow-up after 7 days of digest data confirms signal quality.
- **3 of 5 Bucket A surfaces + all of Buckets B+C.** Acknowledged in §6; deferred to Wave 2.x.

---

## 8. Success Criterion (30 Days Out) — Directional, Falsifiable

Wave 1 succeeds if **all 5** of the following hold at 2026-06-15 (v2 raises the bar per Critic A+C — v1's "4 of 5" with 3 existence checks was structurally unfalsifiable):

1. **W1.3 directional metric (v2.2 specified).** `~/.claude/projects/.../runtime_digest/` contains ≥25 daily JSON files (5 days slack). `haiku_share` week-4 average strictly greater than week-1 baseline (0.836 measured 2026-05-16) by ≥2 percentage points. Hypothesized direction: SessionStart priority-context reminder drives operator/orchestrator tier discipline up. If `haiku_share` decreases or holds flat, criterion fails — Wave 1 has not produced compounding behavior change.
2. **W1.5 retrieval citations.** ≥30% of sessions in week 4 of Wave 1 cite ≥1 prior-handoff verified item in their work (counted via session-start log).
3. **W1.6 STALE drop (v2.2 revised — baseline measured 2026-05-16 = 0/11).** Either (a) STALE+DEAD count at run #4 strictly ≤ run #1's count AND any STALE/DEAD discovered during Wave 1 is repaired within 7 days of audit detection (≥1 such repair commit IF any STALE arose); OR (b) baseline holds at 0 across all 4 weekly runs (the strictly-less-than gate is trivially satisfied with no drift). Note: baseline of 0 means low repair-opportunity in Wave 1; W1.6's compounding value materializes when MEMORY grows or files refactor — measurement is a tripwire, not a guaranteed event.
4. **W1.7 adoption.** ≥3 MEMORY entries **graduated** to actual artifacts; AND ≥80% of new feedback entries written during Wave 1 have `antibody_hook_spec:` populated.
   - *Graduated definition (v2.1):* the entry's `promotion_criteria` flipped from open to met (operator attestation in MEMORY entry frontmatter), AND a concrete artifact materialized (hook file in `~/.claude/hooks/` or `zeus/.claude/hooks/`, skill template diff, or digest metric line) whose commit message cites the MEMORY entry slug.
5. **Probe-3 compliance.** ≥80% of orchestrator `verify` / `ultraqa` verdict emissions during Wave 1 carry a populated `probes:` field.

**Wave 1 fails if any 2 of 5 miss.** Failure triggers a post-mortem before Wave 2 evaluation.

---

## 9. v2 Revision Diff Log (from 4-Critic Adversarial Pass)

| Finding | Source | Section affected | v2 change |
|---------|--------|------------------|-----------|
| SEV-1: edge_observation.py is read-only library, not write-only writer | Critic A | §1 | Moved to new Bucket B (computation-no-persistence); §1 restructured into 3 buckets |
| SEV-1: 18,848 is event count, not file count (372 files) | Critic A | §1, §7 | Phrasing corrected; both counts shown |
| SEV-1: W1.3 06:00 cron collides with zeus-daily-audit | Critic B | §4 W1.3 | Moved to 08:30 |
| SEV-1: W1.5 post-tool-use.mjs is PostToolUse not Stop | Critic B+D | §4 W1.5 | New session-handoff.mjs registered as 3rd Stop hook |
| SEV-1: `~/.claude/scheduled-tasks/` is OMC skill dir not cron | Critic D | §4 W1.3, W1.6 | Replaced with launchd plists |
| SEV-2: DriftReport is transient dataclass, analogy weak | Critic A | §2 | Added honest caveat |
| SEV-2: improvement_backlog has admitted write path | Critic A | §1 | Moved to new Bucket C |
| SEV-2: ritual_signal has test consumer | Critic A | §1 | Bucket A entry annotated "test contract only" |
| SEV-2: W1.7 not compounding in Wave 1 | Critic A+C | §3, §4 W1.7 | Reclassified Linear pipeline-seed; added Wave-1 adoption metric |
| SEV-2: Wave 2 deferral undercuts thesis | Critic A | §6 | Explicit acknowledgment + falsifiable revisit triggers |
| SEV-2: W1.6 needs repair lane or count plateaus | Critic B+C | §4 W1.6 | Split into W1.6a (audit) + W1.6b (repair lane) |
| SEV-2: Act gate under-specified | Critic B | §5 | New 4-condition gate table mirroring calibration-retrain bar |
| SEV-2: W1.6 Monday 07:00 collides with zeus-antibody-scan | Critic B | §4 W1.6 | Moved to Monday 08:00 |
| SEV-2: W1.3 fields tool_name/brief_length don't exist | Critic D | §4 W1.3 | Field inventory now a ship-gate prerequisite |
| SEV-2: Skill paths use namespace not filesystem name | Critic D | §4 W1.1, W1.4 | Drop `oh-my-claudecode:` prefix |
| SEV-3: §8 criteria not directionally falsifiable | Critic A+C | §8 | Rewritten with strictly-greater-than / strictly-less-than gates and "fail if any 2 of 5 miss" clause |
| SEV-3: W1.4 skip mechanism not enforceable | Critic B | §4 W1.4 | Skip removed entirely |
| SEV-3: W1.6 venv dependency | Critic B | §4 W1.6 | Stdlib-only constraint stated |
| SEV-3: W1.6 regex needs L-prefix + colon | Critic D | §4 W1.6a, §4 W1.2 | Spec covers both forms |

---

**v2.1 diff (A2 closed-loop verification):**

| Finding | Source | Section | v2.1 change |
|---------|--------|---------|-------------|
| MAJOR: launchd vs cron dual-stack glossed over | A2 | §4 W1.3, W1.6 | Scheduler switched to `~/.openclaw/cron/jobs.json` `kind: cron` entries, matching zeus-daily-audit/zeus-antibody-scan precedent (single runbook, single rollback path) |
| MINOR: handoff schema lacks dedup/conflict spec | A2 (Critic C carry-over) | §4 W1.5 | Filename includes `<session_id>` to prevent concurrent overwrite; reader uses mtime |
| MINOR: §8 #4 "graduated" undefined | A2 | §8 #4 | Added definition: `promotion_criteria` met + concrete artifact citing MEMORY slug |
| MINOR: MEMORY entry count 45 not 46 | A2 | §1, §4 W1.7 | Corrected to 45 |

A2 verdict: ACCEPT-WITH-RESERVATIONS. Day 0-2 Instrument work proceeds independent of the MAJOR; only Day 3 W1.3 ship-gate is gated on the scheduler switch above.

**v2.2 diff (post-implementation reality check):**

| Finding | Source | Section | v2.2 change |
|---------|--------|---------|-------------|
| W1.3 plan-named metrics (token_cost, tool_name, brief_length) NOT in telemetry payload | Worker E field inventory 2026-05-16 | §4 W1.3 | Metric spec rewritten to derivable-only: `haiku_share` / model distribution / sessions/day. Non-derivable list explicitly named so future readers don't re-add. |
| §8 criterion 1 named metric resolved | Worker E baseline | §8 #1 | Replaces "named primary metric" placeholder with concrete `haiku_share` directional gate (≥+2pp over week-1 0.836 baseline) |
| W1.3 ship-gate status changed: MET (impl + baseline) except operator cron apply | Worker E delivery | §4 W1.3 | Ship-gate updated to reflect actual artifacts, runtime 0.24s, watermark verified |
| §8 #3 STALE+DEAD baseline = 0 (11 citations across MEMORY); criterion was unfalsifiable in strict-less-than direction with empty floor | Worker D-finish baseline | §8 #3 | Criterion split: (a) repair within 7 days IF STALE arises during Wave 1; OR (b) baseline holds at 0. Acknowledges audit as tripwire-not-guaranteed-event. |

**v2.3 diff (pre-PR opus critic, 10 probes, ACCEPT-WITH-PRE-PR-FIXES):**

| Finding | Severity | Section | v2.3 change |
|---------|----------|---------|-------------|
| CRITICAL #1: `CRON_ENTRY_*.json` schema is fabricated; real `~/.openclaw/cron/jobs.json` uses `agentTurn`-payload-based nested schema not flat `{name,kind,expr,tz,cmd}` | A2 missed during round 2; pre-PR critic empirically verified against zeus-daily-audit shape | §4 W1.3, W1.6 | Pivoted to plain macOS `crontab -e` via shared `CRONTAB_LINES.txt`. Deleted the two `CRON_ENTRY_*.json` fabrications. Zero new schema. Activation = `crontab -e` paste. |
| CRITICAL #2: Worker H `_anchor()` pre-citation backtick fallback empirically picks `with conn:` from prose preamble instead of identifier near citation | Pre-PR critic reproduced | §4 W1.6, `scripts/memory_repair_apply.py:70-74` | Fallback removed. "No anchor → manual review" preferred over wrong-anchor patch. Baseline=0 still passes. |
| MAJOR #3: Worker F invented `handoff-draft.yaml` staging file with no agent-side writer; handoffs will be empty stubs | Pre-PR critic | §4 W1.5, §8 #2 | W1.5 honestly split into W1.5a (infra shipped) + W1.5b (writer logic, post-Wave-1). §8 #2 downgraded to "≥1 non-empty handoff written" pending W1.5b. |
| MAJOR #4: `python` vs `python3` interpreter ambiguity | Pre-PR critic | `CRONTAB_LINES.txt` | Both lines use explicit `/usr/bin/python3` — no PATH dependence. |
| MINOR: §5 ordering still said "launchd" | Pre-PR critic | §5 | Changed to "crontab line". |

Pre-PR critic verdict: ACCEPT-WITH-PRE-PR-FIXES → ACCEPT-FOR-PR after fixes above all landed in this PR.

---

*Plan derived from parent system review + 1 advisor consultation + 4-critic round-1 adversarial pass (architecture/operational/compounding/feasibility) + 1 A2 closed-loop verification (opus). v2 changes per §9 v2 table; v2.1 changes per §9 v2.1 table.*
