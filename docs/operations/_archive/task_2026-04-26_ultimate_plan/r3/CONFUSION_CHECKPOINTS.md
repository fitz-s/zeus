# Confusion Checkpoints — When to STOP and verify

This file lists the 12 specific moments during R3 implementation when
the agent MUST stop and verify rather than proceed on assumption. Skipping
these checkpoints is the #1 source of silent drift.

The agent is encouraged to write `r3/_confusion/<short-topic>.md` whenever
ANY of these moments occur. The operator reviews `_confusion/` periodically
and either resolves or escalates.

**Foundation principle**: confusion is a signal, not a failure. The
operator wants to hear about it. A surfaced confusion is cheap to fix; a
silent assumption that turns out wrong is expensive.

---

## CC-1: Assuming external behavior without a citation

**Trigger**: You are about to write code (or a test assertion) that
depends on the behavior of an external system: SDK, HTTP API, on-chain
contract, third-party data source, library function not in Zeus's
codebase.

**Examples**:
- "py-clob-client-v2's `create_order` returns a dict with `signed_order_hash` field"
- "Polygon RPC returns 64-byte signed messages for `personal_sign`"
- "TIGGE archive returns GRIB2 with parameter table 4"
- "Polymarket WebSocket emits `MATCHED` events with `trade_id` field"

**What to do**:
1. **WebFetch authoritative source** — official docs, GitHub source code
   at the pinned version, RFC, contract on Polygonscan.
2. If WebFetch is blocked / rate-limited / the page is ambiguous:
   **dispatch a sub-agent** with `document-specialist` or `general-purpose`
   skill (per memory `feedback_subagent_for_oneoff_checks`).
3. For on-chain identity / behavior: **dispatch sub-agent for direct
   eth_call** (per memory `feedback_on_chain_eth_call_for_token_identity`).
4. Capture the verified fact in `r3/reference_excerpts/<topic>_<date>.md`
   so the next agent doesn't re-fetch.
5. Cite the excerpt in your code comment + test assertion.

**Anti-pattern**: writing the assumption directly into code with a
"// TODO: verify this" comment. Verify FIRST.

---

## CC-2: Spec is genuinely ambiguous after re-reading

**Trigger**: You've read the phase yaml twice, the linked R2 cards, and
the relevant `frozen_interfaces/` doc, and you still can't decide between
two implementations.

**Examples**:
- Z4: "pUSD reservation released atomically when sell command transitions
  to CANCELED/FILLED/EXPIRED" — what about REVIEW_REQUIRED? Released or
  retained?
- M3: "WS gap → force M5 sweep before unblocking" — what defines "gap"?
  3 seconds? 30 seconds? 3× cadence?
- A2: "kill switch on (reconcile_finding_count > N)" — what's N?

**What to do**:
1. **Dispatch `deep-interview` skill** (opus) — it forces precision via
   Socratic questioning before code is written.
2. If `deep-interview` doesn't resolve it: write
   `r3/_confusion/<phase>_<topic>.md` with:
   - what the spec says
   - the two (or more) interpretations
   - implications of each
   - your recommendation + why
3. Notify operator. Await authorization to proceed with one interpretation.

**Anti-pattern**: picking the easier-to-implement interpretation silently.

---

## CC-3: Citation rot — line number says X, reality says Y

**Trigger**: A phase yaml cites `polymarket_client.py:194-197`. You grep
the file at HEAD and find lines 194-197 say something else.

**What to do**:
1. **Re-grep for the SYMBOL** (function name + seam marker), not line
   number. Per memory `feedback_grep_gate_before_contract_lock`.
2. If symbol still exists with matching seam content but at a different
   line: YELLOW drift; update the citation in the yaml to use symbol
   anchor; document in commit message.
3. If symbol is GONE (renamed / deleted / refactored): RED drift. STOP.
   Write `r3/_confusion/<phase>_citation_<file>.md`. The phase may need
   re-design. Notify operator.
4. ALWAYS update `r3/scripts/r3_drift_check.py`'s seam_marker entry if
   you can identify a more stable anchor.

**Anti-pattern**: implementing against the cited line number even when it
no longer matches the spec.

---

## CC-4: Test passes but you suspect it tests the wrong thing

**Trigger**: Your antibody test is green, but in your gut you feel like
it doesn't actually enforce the invariant you wrote it for.

**Examples**:
- NC-NEW-G envelope-not-seam — your test asserts "envelope object exists",
  but it doesn't assert "envelope works when SDK shape changes".
- NC-NEW-K sell-not-substitute-pUSD — your test passes when sell uses
  pUSD because you didn't construct the high-pUSD-zero-token scenario.

**What to do**:
1. **Dispatch `critic` skill (opus)** with the antibody test code +
   the invariant statement. Critic checks SPIRIT not LETTER.
2. If critic returns "this test is too narrow": rewrite the test to
   include the failure scenario explicitly.
3. Add the rewritten test to the phase's acceptance_tests in the yaml.
4. Document the rewrite in `r3/learnings/<phase>_antibody_<topic>.md` so
   future phases learn from your test-design lesson.

**Anti-pattern**: shipping the green test on the assumption that "tests
green = invariant holds". Memory `feedback_critic_prompt_adversarial_template`
applies — never write "narrow scope self-validating".

---

## CC-5: Discovered fact contradicts a phase yaml

**Trigger**: You're implementing phase M3 (WebSocket ingest) and discover
that the V2 SDK's WebSocket surface looks fundamentally different from
what M3.yaml describes (e.g., subscription model is per-asset not
per-condition; auth handshake takes API key + nonce, not just key).

**What to do**:
1. STOP implementing. Do NOT silently adapt.
2. Write `r3/_confusion/<phase>_spec_vs_reality_<topic>.md`:
   - what the yaml says
   - what reality (cited source!) shows
   - implications for downstream phases
   - your proposed amendment to the yaml
3. **Cite the source**: WebFetch URL, GitHub commit, RPC response, etc.
4. Notify operator. The yaml amendment is a planning-lock-adjacent event.
5. If the change is structural (e.g., cascades to U2 schema): wait for
   operator authorization. If it's localized (e.g., one method signature
   difference): operator may authorize you to proceed with a documented
   amendment.

**Anti-pattern**: silently adapting the implementation to reality and
leaving the yaml stale. Future agents will rely on the stale yaml.

---

## CC-6: Need to modify a file outside phase boundary

**Trigger**: Your phase's `deliverables.extended_modules` lists files A,
B, C. While implementing, you realize you also need to modify file D.

**What to do**:
1. STOP. Do NOT modify file D.
2. Write `r3/_cross_phase_question_<phase>_<file>.md`:
   - which file you need to modify
   - why (the dependency you discovered)
   - which other phase "owns" file D (check yaml ownership)
   - proposed resolution (e.g., extend your phase's boundary, or
     coordinate with the owning phase)
3. Notify operator.
4. Operator decides:
   - Authorize boundary extension → update YOUR phase's yaml + proceed.
   - Defer to owning phase → wait, or coordinate with that phase's agent.
   - Re-scope your phase → planning-lock event.

**Anti-pattern**: just modifying file D. Even one out-of-boundary
modification breaks the cross-phase coordination model that prevents
merge cascades.

---

## CC-7: Operator gate appears blocking; tempted to bypass

**Trigger**: Your phase has `operator_decision_required: blocking: yes`
on a gate that hasn't cleared. You think the work is otherwise complete.

**Examples**:
- Q-FX-1 not yet decided; you've written down-07 + R1 redemption code;
  tempted to ship without the dual-gate.
- INV-29 amendment commit pending; M1 PR ready; tempted to merge with a
  TODO comment.
- impact_report v2 critic-opus gate not approved; tempted to ship Z2
  pretending the impact_report v2 is "good enough".

**What to do**:
1. STOP. Operator gates are non-negotiable.
2. Confirm the gate is genuinely blocking (re-read the gate's INDEX.md
   entry; check `default if absent` behavior).
3. If your phase can ship to the EDGE of the gate (everything except the
   gate-runtime check): do so. Write `r3/_phase_at_gate_edge_<phase>.md`
   describing what's done and what awaits the gate.
4. Notify operator. Operator either resolves the gate or accepts the
   phase landing in "at-gate-edge" status (status=COMPLETE_AT_GATE).

**Anti-pattern**: shipping with a TODO comment. The runtime gate exists
to fail-closed when operator decision is missing. A TODO is not the
runtime gate.

---

## CC-8: An NC-NEW invariant contradicts your plan

**Trigger**: You're implementing phase M3 and you encounter a code path
that, if implemented as you planned, would violate NC-NEW-A (no
INSERT INTO venue_commands outside venue_command_repo.py). You're tempted
to add a small exception.

**What to do**:
1. STOP. Invariants beat plans. Always.
2. Re-read `INVARIANTS_LEDGER.md` row for the invariant.
3. Re-design your approach to NOT violate the invariant. The invariant
   was minted in an earlier phase via planning-lock; bypassing it
   re-introduces the failure mode it was designed against.
4. If after re-design you genuinely cannot satisfy both your phase
   spec AND the invariant: this is a planning-lock event. Write
   `r3/_protocol_evolution/<topic>.md` proposing either an invariant
   amendment OR a phase yaml amendment. Notify operator.

**Anti-pattern**: adding a # noqa or special case to bypass the
invariant. Antibodies exist BECAUSE this temptation is universal.

---

## CC-9: Hour estimate exceeded by 1.5×

**Trigger**: Phase yaml says `estimated_h: 12`. You're at hour 18.

**What to do**:
1. STOP and audit. Are you over because:
   - You discovered scope you didn't anticipate?
   - You're solving the wrong problem?
   - The card is genuinely under-estimated?
   - You hit a confusion checkpoint and worked around it?
2. Write `r3/_overrun_<phase>.md`:
   - hours spent vs estimated
   - what's done vs remaining
   - root cause analysis
   - 3 options: descope (remove some acceptance_tests with operator
     authorization), extend (operator authorizes more hours), or
     re-scope (split phase in two).
3. Notify operator. Wait.

**Anti-pattern**: "I'll just push through, it's almost done". Halt-and-
audit at 1.5× catches scope creep before it becomes 3×.

---

## CC-10: Schema migration with downstream implications you can't predict

**Trigger**: Your phase requires an `ALTER TABLE` that adds a column.
You're not sure whether downstream phases (or future Wave-E cutover) will
need to backfill, recompute indexes, or reshape consumers.

**What to do**:
1. **Dispatch `architect` skill (opus, read-only)** with the proposed
   schema change + the list of consumers (grep all phase yamls for the
   table name).
2. Architect returns a downstream-impact report.
3. If impacts are local: proceed with documented migration plan.
4. If impacts are wider: write `r3/_confusion/<phase>_schema_<table>.md`
   + notify operator. Schema decisions are planning-lock-adjacent.

**Anti-pattern**: shipping the ALTER and discovering downstream breakage
in Wave E.

---

## CC-11: Antibody test fails on first run; tempted to weaken assertion

**Trigger**: You wrote the antibody test per the phase yaml. It fails
when run against your implementation. You realize the assertion is
"too strict" and weakening it would make the test pass.

**What to do**:
1. STOP. Do NOT weaken the assertion.
2. The antibody is the contract. If it fails, the IMPLEMENTATION is
   wrong, not the antibody.
3. Re-read the phase yaml's spec for what the antibody is meant to
   protect against. The implementation must hold that invariant.
4. If after re-design you still can't satisfy the antibody: it's a
   planning-lock-adjacent event. Either the antibody is mis-specified
   (operator amends yaml) or the phase scope is wrong (operator
   re-scopes).
5. Write `r3/_confusion/<phase>_antibody_<test_name>.md` with the
   evidence.

**Anti-pattern**: weakening `assert response.signed_order_hash is not None`
to `assert "signed_order_hash" in response or response.error_code == "MISSING"`.
That makes the test pass and the invariant fail.

---

## CC-12: External SDK / library version differs from pin

**Trigger**: `requirements.txt` pins `py-clob-client-v2==1.0.0`. You
discover the installed version is 1.1.3 (operator manually upgraded).

**What to do**:
1. STOP. Do NOT proceed against an unverified version.
2. **Dispatch `document-specialist`** to capture v1.0.0 → v1.1.3 diff
   in `r3/reference_excerpts/py_clob_client_v2_v1.0.0_to_v1.1.3_diff_<date>.md`.
3. Re-run all phase antibodies against v1.1.3.
4. If antibodies still pass: update pin to 1.1.3, document the upgrade
   in commit message, notify operator.
5. If antibodies fail: planning-lock event. Notify operator. Do not
   amend antibodies to match new behavior without operator review.

**Anti-pattern**: shipping with the upgraded version because "it's
probably backward-compatible".

---

## How to use this list

Cold-start agents read this file once during orientation (per
`templates/fresh_start_prompt.md` §1).

During implementation, when ANY of CC-1 through CC-12 trigger:
1. STOP code work.
2. Run the prescribed action.
3. If the action involves writing `r3/_confusion/<topic>.md`, do that
   FIRST before anything else. Disk > context.
4. Notify operator (or sub-agent for verification, depending on
   checkpoint).
5. Resume only after the trigger is resolved.

Operator: review `r3/_confusion/` directory periodically. Each file
either resolves quickly (operator clarifies, agent continues) or
escalates to a yaml/protocol amendment.

---

## Memory cross-references

These checkpoints derive from prior memory entries:
- `feedback_grep_gate_before_contract_lock` → CC-3
- `feedback_zeus_plan_citations_rot_fast` → CC-3
- `feedback_critic_prompt_adversarial_template` → CC-4, CC-11
- `feedback_on_chain_eth_call_for_token_identity` → CC-1
- `feedback_subagent_for_oneoff_checks` → CC-1
- `feedback_no_git_add_all_with_cotenant` → CC-6 (not git-add but boundary)
- `feedback_default_dispatch_reviewers_per_phase` → CC-4 (critic dispatch)

These checkpoints will themselves evolve. If you encounter a confusion
moment that doesn't fit CC-1..CC-12, write `r3/_protocol_evolution/<topic>.md`
proposing a new CC-N. The operator decides whether to add it.
