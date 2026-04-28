# R3 Implementation Protocol — Anti-drift mechanisms for 312h multi-agent work

Created: 2026-04-26
Authority: this protocol governs how `r3/slice_cards/*.yaml` are turned into shipped code over 5-8 weeks.

R3 is **312 hours of work distributed across rotating agents over weeks**.
Without explicit anti-drift mechanisms, the plan will rot in 14 ways. This
document catalogs each predicted failure mode + the mechanism that prevents
it + the concrete artifact (script, template, ledger) that makes the
mechanism enforceable.

---

## §1 The 14 predicted failure modes

| # | Failure mode | Mechanism | Artifact |
|---|---|---|---|
| 1 | **Citation drift over weeks** — `polymarket_client.py:194-197` rots when someone refactors the file | Symbol-anchored citations + biweekly re-anchor | `scripts/r3_drift_check.py` + `_re_anchor_<date>.md` |
| 2 | **Agent context loss across compaction** — agent in phase M3 has no memory of decisions made in Z2 | Per-phase boot protocol + invariants ledger | `PHASE_BOOT_PROTOCOL.md` + `INVARIANTS_LEDGER.md` |
| 3 | **Schema drift between phases** — U2 lands; M3 reads U2 schema; someone amends U2 between; M3 silently breaks | Frozen-interface docs + cross-phase contract tests | `frozen_interfaces/<phase_id>.md` + acceptance test |
| 4 | **Multi-agent disagreement on ambiguous spec** — Z4 says "pUSD vs CTF" but doesn't define wrapped-CTF | Ambiguity gate before phase entry | `deep-interview` skill mandatory for HIGH-risk phases |
| 5 | **Operator gate latency** — 8 gates × 1-3 days = 16 days dead time if serial | Parallelizable gate tracker + engineering proceeds to gate edge | `operator_decisions/INDEX.md` `parallelizable_with:` field |
| 6 | **Cross-phase invariant break** — phase passes its own antibodies; integration breaks an upstream invariant | Cross-phase invariant ledger run on every phase merge | `scripts/invariant_ledger_check.py` |
| 7 | **Test interaction / shared fixture rot** — fake_venue fixture state leaks; tests pass solo, fail in suite | Per-phase test isolation + phase-shard CI | `pytest` markers + CI matrix per phase |
| 8 | **External fact rot** — V2 SDK at v1.0.0 today; v1.2.0 by week 4 with subtle behavior change | SDK version pin + reference excerpt freezing | `reference_excerpts/<topic>_<date>.md` + `requirements.txt` exact pins |
| 9 | **Memory drift across agents** — phase A creates memory entry; phase B doesn't read it; opposite decision | Mandatory memory consultation at phase boot | per-phase prompt lists relevant memory entries |
| 10 | **"Almost done" trap** — antibodies pass but spirit not captured | Critic-opus phase gate with spirit check | `critic_gate: critic-opus` + spirit-check rubric |
| 11 | **Decimal precision drift** — Decimal at write, float at read, calibration corrupts silently | Type-checked I/O via dataclass + mypy strict on src/state/ | `mypy --strict` CI gate |
| 12 | **Async/sync race conditions** — heartbeat coroutine + cycle_runner sequential | Explicit happens-before doc per async slice | `<phase>_concurrency_model.md` |
| 13 | **Performance regression** — 30 antibodies + frozen-replay = 30min CI, nobody runs locally | Phase-shard CI + perf budget per phase | CI matrix + `perf_budget_seconds:` field |
| 14 | **Branch + merge conflict cascades** — multi-engineer parallelism creates merge storms | Phase-locked branches + merge-window discipline | `_phase_status.yaml` + branch protection |

---

## §2 Per-phase boot protocol (the most important mechanism)

Every fresh agent picking up a phase MUST complete this protocol before
writing any code. Skip-the-boot is the #1 source of drift. This is enforced
by the prompt template (`templates/phase_prompt_template.md`) and the
critic-opus gate (which rejects PRs lacking the boot evidence).

### Boot steps (mandatory, in order)

1. **Read R3_README.md** in full (~300 lines).
2. **Read the phase yaml**: `slice_cards/<phase_id>.yaml` in full.
3. **Read the phase's `links.r2_cards`**: each R2 card from `../slice_cards/`.
4. **Read the phase's `links.r2_evidence`** (if present): R2 converged docs.
5. **Read this file** (IMPLEMENTATION_PROTOCOL.md) — every time, no exceptions.
6. **Read INVARIANTS_LEDGER.md**: see which NC-NEW + INV-NEW are LIVE on HEAD.
7. **Run `scripts/r3_drift_check.py --phase <id>`**: outputs a report listing all `file_line:` citations + their HEAD verification status.
8. **Read flagged drift entries**: if any cite is SEMANTIC_MISMATCH, FREEZE and write `_blocked_<phase_id>.md` with the mismatch detail. Do NOT proceed.
9. **Consult memory entries**: each phase yaml has `memory_consult:` field listing relevant `feedback_*` entries. Read them.
10. **Write `boot/<phase_id>_<author>_<date>.md`**: 5-10 sentences capturing what you read + your working hypothesis for implementation.
11. **Optional but recommended**: dispatch `deep-interview` skill if the phase has any genuine ambiguity (HIGH-risk phases MUST do this).
12. **Mark phase IN_PROGRESS** in `_phase_status.yaml` with timestamp + author + branch.

Only after step 12 may code be written.

### Why this matters

R2's debate caught 80% citation drift via grep audit. Implementation will
add MORE drift because code refactors land between phases. Without per-phase
boot protocol, agents will silently implement against rotted citations.

---

## §3 Symbol-anchored citations (replaces line numbers)

R2 cards cite `polymarket_client.py:194-197`. By week 2, the file has
shifted by 80 lines (Z2 inserted v2_preflight). Citation rotted; antibody
broken.

**R3 discipline**: every `file_line:` entry MUST cite by SYMBOL, not bare
line number. Format:

```yaml
file_line:
  - symbol: PolymarketClient.place_limit_order
    file: src/data/polymarket_client.py
    seam_marker: "after _ensure_client(); two-step create_order/post_order"
    head_verified_line: 194-197    # informational only, may rot
    head_verified_at: 874e00cc
```

`scripts/r3_drift_check.py` greps for the SYMBOL + seam_marker; reports
PASS regardless of line drift as long as symbol exists with matching
seam content.

For symbols that don't exist yet (will be created by the phase):

```yaml
file_line:
  - symbol: PolymarketV2Adapter.create_submission_envelope
    file: src/venue/polymarket_v2_adapter.py
    status: WILL_CREATE_IN_PHASE_Z2
```

---

## §4 INVARIANTS_LEDGER (cross-phase invariant tracking)

Single file at `r3/INVARIANTS_LEDGER.md`. Format:

```markdown
| ID | Phase | Rule | Antibody | Last verified | Last commit | Status |
|---|---|---|---|---|---|---|
| NC-NEW-A | U2 | No INSERT INTO venue_commands outside repo | semgrep `zeus-venue-commands-repo-only` | 2026-XX-XX | abc123 | LIVE |
| NC-NEW-G | Z2 | Provenance pinned at envelope, not seam | tests/test_v2_adapter.py::test_one_step_path_still_produces_envelope | 2026-XX-XX | def456 | LIVE |
| ... |
```

- Updated by EACH phase merge — CI auto-appends rows on green.
- Cold-start agent reads this BEFORE writing code (boot step 6).
- If a NC-NEW or INV-NEW has Status != LIVE, the phase yaml that depends
  on it MUST treat it as not-yet-shipped.

`scripts/invariant_ledger_check.py` runs on every PR; verifies all
LIVE invariants still pass on the PR's HEAD.

---

## §5 Frozen-interface docs (cross-phase API contract)

Each phase that exposes a public API to other phases writes a frozen
interface doc at `r3/frozen_interfaces/<phase_id>.md`. Example for Z2:

```markdown
# Z2 Frozen Interface — PolymarketV2Adapter

Exposed publicly. Other phases consume THIS contract; internals may change.

## Class
`src/venue/polymarket_v2_adapter.py::PolymarketV2Adapter`

## Methods (signatures stable; semantics versioned)
- `preflight() -> PreflightResult`
- `create_submission_envelope(intent, snapshot, order_type, post_only=False) -> VenueSubmissionEnvelope`
- ... [all methods]

## Versioning
SCHEMA_VERSION = 1. Bumping to 2 requires planning-lock.

## Consumed by
- M1 (lifecycle grammar reads OrderType enum)
- M3 (user-channel ingest reads OrderState enum)
- M4 (cancel parser reads CancelOutcome)
- T1 (fake venue implements this contract)

## Forbidden
- Direct `from py_clob_client_v2.client import ClobClient` outside this module (NC-NEW-G)
- Bypass via `polymarket_client.py` (legacy, deprecated)
```

When Z2 lands, ALL downstream phases read this doc, NOT Z2's source code.
This prevents implementation-detail leakage.

---

## §6 Phase prompt template

Every fresh agent that picks up a phase gets this exact prompt structure
(see `templates/phase_prompt_template.md` for the fill-in template). Key
sections:

1. **Identity**: "You are implementing R3 phase <ID>. Your work survives across compactions only via on-disk artifacts."
2. **Mandatory reads**: explicit file list (R3_README, phase yaml, INVARIANTS_LEDGER, etc.).
3. **Drift check requirement**: "Run `scripts/r3_drift_check.py --phase <id>` before writing code. If any cite is SEMANTIC_MISMATCH, freeze."
4. **Memory consultation list**: specific `feedback_*` entries.
5. **Skill invocation list**: which skill to invoke at which step (see SKILLS_MATRIX.md).
6. **Boundaries**: "DO NOT modify files outside the phase's `deliverables.extended_modules` list. If you need to, write `_cross_phase_question.md` and ask the user."
7. **Exit criteria**: antibodies pass + critic-opus PASS + INVARIANTS_LEDGER updated + phase status COMPLETE.
8. **Operator gate map**: which gates are blocking + which artifacts to look for.
9. **Failure modes specific to this phase** (lifted from card's risk: section).
10. **Branch + commit discipline**: branch name pattern, PR template, when to merge.

The template is parameterized with `<phase_id>`. Just fill in and dispatch.

---

## §7 Skills matrix (which skill at which step)

See `SKILLS_MATRIX.md`. High-level pattern:

| Phase step | Skill | Why |
|---|---|---|
| Boot — read R3_README + phase yaml | (no skill, agent reads directly) | trust the agent's reading |
| Boot — drift check | Bash (run script) | mechanical verification |
| Boot — ambiguity gate (HIGH-risk phases) | `deep-interview` | force precision before code |
| Implementation — file exploration | `explore` | find downstream callers, related symbols |
| Implementation — write code | `executor` (default) | focused implementation |
| Implementation — complex multi-file | `general-purpose` | wider context |
| Implementation — schema design | `architect` (read-only sanity check) | structural reasoning |
| Implementation — UI / status surfaces | `designer` | when surfaces are operator-facing |
| Tests — unit + integration | `test-engineer` | TDD discipline + flake hardening |
| Tests — verify spec coverage | `verifier` | acceptance-evidence audit |
| External SDK fact-check | `document-specialist` + WebFetch | V2 SDK source / TIGGE docs / Polymarket docs |
| On-chain identity dispute | sub-agent + Bash + curl (per memory `feedback_on_chain_eth_call_for_token_identity`) | direct RPC eth_call |
| Pre-merge — review | `code-reviewer` + `critic` (HIGH-risk only) | severity-rated review + adversarial |
| Pre-merge — security review | `security-reviewer` | OWASP / secrets / unsafe patterns |
| Pre-merge — simplification | `code-simplifier` | reduce complexity post-MVP |
| Pre-merge — completion check | `verifier` | evidence-based completion |
| Memory update | `remember` skill | persist phase-specific learnings |
| Phase exit — packet close | 5-angle review per `feedback_multi_angle_review_at_packet_close` | architect + critic + explore + scientist + verifier |

For HIGH-risk phases (Z1-Z4, U2, M1-M5, R1, T1, A1, A2, G1), critic-opus
gate is MANDATORY pre-merge.

---

## §8 Drift detection script (`scripts/r3_drift_check.py`)

Runs on demand + daily via CI. Checks:

1. **Citation drift**: for every `file_line:` entry in every phase yaml,
   verify the symbol exists at the cited file with matching `seam_marker`.
2. **Antibody liveness**: for every NC-NEW + INV-NEW with `Status: LIVE`
   in INVARIANTS_LEDGER.md, run the antibody test (semgrep + pytest).
3. **HEAD anchor**: compare current HEAD to the anchor in R3_README.md;
   report drift in commits.
4. **External version pin**: verify `requirements.txt` `py-clob-client-v2`
   pin matches reference_excerpts version.
5. **Operator gate artifacts**: for every gate marked `OPEN` in
   operator_decisions/INDEX.md, check if the artifact is now present.
6. **Schema drift**: compare U1 + U2 schemas at HEAD to the DDL in cards;
   flag any unauthorized columns / drops.

Outputs `r3/drift_reports/<date>.md`. Color-coded:
- GREEN: zero drift.
- YELLOW: line drift but symbol intact (informational).
- RED: SEMANTIC_MISMATCH or antibody fail (blocking).

---

## §9 Phase status tracker (`_phase_status.yaml`)

Single source of truth for "who is doing what". Format:

```yaml
phases:
  Z0:
    status: COMPLETE
    started_at: 2026-04-27
    completed_at: 2026-04-27
    author: agent-1
    branch: r3/z0-plan-lock
    commit: abc123
    critic_review: APPROVED (link to PR review)
    invariants_added: []
  Z1:
    status: IN_PROGRESS
    started_at: 2026-04-28
    author: agent-2
    branch: r3/z1-cutover-guard
  Z2:
    status: BLOCKED
    blocked_on: [Q1-zeus-egress, Z1]
  ...
```

Updated by phase agents on entry (IN_PROGRESS) + critic on exit (COMPLETE).

---

## §10 Cross-phase coordination patterns

### Pattern A: Frozen interface (preferred)
- Phase A defines interface in `frozen_interfaces/<A>.md`.
- Phase B reads ONLY the frozen interface.
- Implementation changes within A do NOT propagate to B.

### Pattern B: Ledger-tracked invariant
- Phase A introduces NC-NEW-X.
- Adds row to INVARIANTS_LEDGER.md with antibody.
- Phase B reads ledger, not A's source. Antibody enforces invariant.

### Pattern C: Schema migration coordination
- Phase A owns schema (e.g., U2 owns 5-projection schema).
- Phase B reads schema via U2's documented DDL.
- Schema changes go through planning-lock + amendment commit.

### Pattern D: Cross-phase question escalation
- If implementer discovers a cross-phase ambiguity, write `_cross_phase_question.md` with: (i) phases involved, (ii) ambiguity, (iii) proposed resolution.
- Don't unilaterally resolve. User reviews + arbitrates.

---

## §11 Branch + commit discipline

- One branch per phase: `r3/<phase_id>-<author-tag>`.
- One PR per phase. PR template at `templates/phase_pr_template.md`.
- Critic-opus review MANDATORY on PRs marked `critic_gate: critic-opus`.
- No phase merges until INVARIANTS_LEDGER is updated.
- No phase merges until `_phase_status.yaml` is updated.
- No phase merges if drift_check is RED.
- Atomic commits — if hook fails, fix + new commit (memory `feedback_phase_commit_protocol`).
- NEVER `git add -A` (memory `feedback_no_git_add_all_with_cotenant`).
- Co-authoring tag on every commit for traceability.

---

## §12 CI matrix (phase-shard testing)

`.github/workflows/r3_phase_shard.yml` runs:

```yaml
matrix:
  phase: [Z0, Z1, Z2, Z3, Z4, U1, U2, M1, M2, M3, M4, M5, R1, T1, F1, F2, F3, A1, A2, G1]
steps:
  - run pytest -m phase_<phase>
  - run semgrep scan for phase's NC-NEW rules
  - run drift_check --phase <phase>
```

Each shard targets ~3-5 minutes runtime. Full suite via aggregator job.

Tests are tagged with pytest markers:
```python
@pytest.mark.phase_z2
def test_one_step_sdk_path_still_produces_envelope(...): ...
```

This means a developer in phase M3 only runs `pytest -m phase_m3` (~3 min)
locally; CI runs the full matrix.

---

## §13 Memory discipline

Per-phase memory entries:
- After each phase completes, agent MUST write `feedback_<phase_id>_<topic>.md` with phase-specific learnings.
- Cold-start agent boot step 9 reads ALL phase memory entries.
- Memory entries are append-only across phases.

Topic patterns:
- `feedback_z2_envelope_ast_shape_contract.md` (the why)
- `feedback_m3_ws_gap_detection_threshold.md` (the tuning)
- `feedback_t1_failure_injection_taxonomy.md` (the catalog)

Already-existing memory consultation list (read at boot):
- `feedback_lifecycle_decomposition_for_execution`
- `feedback_multi_angle_review_at_packet_close`
- `feedback_grep_gate_before_contract_lock`
- `feedback_zeus_plan_citations_rot_fast`
- `feedback_on_chain_eth_call_for_token_identity`
- `feedback_critic_prompt_adversarial_template`
- `feedback_default_dispatch_reviewers_per_phase`
- `feedback_no_git_add_all_with_cotenant`
- `feedback_grep_gate_before_contract_lock`

---

## §14 Operator gate parallelism

Operator gates are listed in `operator_decisions/INDEX.md`. Each gate has
a `parallelizable_with:` field listing OTHER gates that can be issued in
parallel without sequencing concerns. For example:

- Q1-zeus-egress (Z2) parallelizable with Q-HB-cadence (Z3) — both are
  external probes.
- Q-FX-1 (Z4) parallelizable with TIGGE-ingest go-live (F3) — different
  decision domains.
- INV-29 amendment (M1) NOT parallelizable with anything in M phase.

This way operator can issue 3-4 gate decisions in a single sitting (~1 day
turnaround for the batch) instead of 8 serial decisions over 2-3 weeks.

---

## §15 Failure recovery patterns

### F-1: Phase precondition violated by upstream change
- FREEZE phase implementation.
- Write `_blocked_<phase_id>.md` with mismatch detail.
- Open `_cross_phase_question.md` for resolution.
- Notify user via session message.
- Do NOT auto-update phase yaml — that's a planning-lock event.

### F-2: Antibody fails after merge (drift_check RED)
- Highest priority. Block ALL new phase merges.
- Bisect to commit that broke the antibody.
- Either: revert that commit, OR amend antibody (planning-lock).
- Update INVARIANTS_LEDGER row to LIVE again only after fix.

### F-3: Multi-agent merge conflict cascade
- Adopt phase-locked branches: only ONE phase active per branch group.
- If two agents need to be parallel: enforce via `_phase_status.yaml`
  IN_PROGRESS lock; second agent must wait or pick a non-overlapping phase.

### F-4: Operator gate stuck OPEN > 5 days
- Critic agent escalates: writes `_gate_stuck_<gate_id>.md` summary for user.
- Engineering may proceed to phase edge but not cross runtime gate.
- If gate stuck > 14 days, consider re-scoping the phase to be gate-independent.

### F-5: External SDK upgrade breaks antibody
- requirements.txt PIN is the firewall.
- If pin needs to bump: dispatch `document-specialist` to capture diff in
  `reference_excerpts/<sdk>_<old_ver>_to_<new_ver>_diff.md`.
- Re-run all phase antibodies against new pin.
- If antibodies fail: planning-lock event.

---

## §16 The "spirit check" — critic-opus's special job

For phases with `critic_gate: critic-opus`, the critic's review is NOT a
line-by-line code review. It's a SPIRIT CHECK:

- Does the implementation honor the structural intent?
- Is NC-NEW-G provenance-not-seam ACTUALLY captured (versus a thin wrapper
  that's still seam-pinned)?
- Are MATCHED ≠ CONFIRMED states ACTUALLY enforced (versus a single
  `state` column with documented but not type-checked semantics)?
- Are antibody tests testing the BEHAVIOR or the TYPE? (memory
  `feedback_critic_prompt_adversarial_template`).

Critic asks 10 explicit adversarial questions per memory directive. If
answer is "narrow scope self-validating" or "pattern proven", critic
rejects.

---

## §17 What this protocol is NOT

- It is NOT a substitute for engineering judgment. Mechanism > rule.
- It is NOT a guarantee against drift. It REDUCES drift; it doesn't eliminate.
- It does NOT replace code review. It frames it.
- It does NOT replace manual operator decisions. It schedules them.

The protocol's purpose is to make the EXPECTED FAILURE MODES VISIBLE so
implementers don't fall into them silently.

---

## §18 Quick-start checklist for the user

When you (operator) are ready to start R3 implementation:

1. ☐ Run `scripts/r3_drift_check.py` — confirm GREEN baseline
2. ☐ Open `operator_decisions/INDEX.md` — issue any gates that don't block
   each other (Q1, Q-HB, etc.)
3. ☐ Review `IMPLEMENTATION_PROTOCOL.md` (this file) — validate the protocol
4. ☐ Pick the first phase to start: Z0 (always available)
5. ☐ Open `templates/phase_prompt_template.md` — fill in `<phase_id>`
6. ☐ Dispatch a fresh agent with the filled prompt
7. ☐ Monitor `_phase_status.yaml` for progress; pick next phase when status flips to COMPLETE
8. ☐ Schedule biweekly re-anchor passes via `scripts/r3_drift_check.py --re-anchor`
9. ☐ Run multi-angle review at every wave close (Wave A, B, C, D, E, F)
10. ☐ G1 fully green = LIVE deploy gate

---

## Appendix: Artifacts created by this protocol

- `IMPLEMENTATION_PROTOCOL.md` — this file
- `PHASE_BOOT_PROTOCOL.md` — boot steps reference
- `SKILLS_MATRIX.md` — skill-per-step matrix
- `INVARIANTS_LEDGER.md` — cross-phase invariant tracker (lives, updated by CI)
- `_phase_status.yaml` — phase progress tracker
- `templates/phase_prompt_template.md` — fill-in prompt for fresh agents
- `templates/phase_pr_template.md` — PR description template
- `frozen_interfaces/<phase_id>.md` — produced per phase as it lands
- `boot/<phase_id>_<author>_<date>.md` — produced per phase entry by agent
- `drift_reports/<date>.md` — produced by drift_check
- `scripts/r3_drift_check.py` — drift detector
- `scripts/invariant_ledger_check.py` — ledger verifier
