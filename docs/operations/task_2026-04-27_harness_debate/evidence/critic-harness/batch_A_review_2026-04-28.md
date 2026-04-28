# BATCH A Review — Critic-Harness

Reviewer: critic-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28 (UTC; today per system clock 2026-04-27 → boot day)
HEAD: 874e00cc0244135f49708682cab434b4d151d25d (per TOPIC.md L5; verified `git log --oneline -5`)
Scope: BATCH A doc-only fixes per round2_verdict.md §4.1 #6 + #7 + #8 — code_review_graph_protocol deprecation+stub+inline; 3 native subagents; IMPLEMENTATION_PROTOCOL→SKILL.md
Pre-batch baseline: 73 passed / 22 skipped / 0 failed (live verified at boot)
Post-batch baseline: 73 passed / 22 skipped / 0 failed (re-verified independently — see ATTACK B)

## Verdict

**APPROVE-WITH-CAVEATS**

The 3 doc-only deliverables are functionally complete, regression-clean, and survive the A1-A3 attack vectors I anticipated in `_boot_critic.md §2`. Two non-blocking concerns that should be tracked into BATCH B and Tier 2:

- **CAVEAT-1**: SKILL.md frontmatter is missing the `model:` field that the 3 subagents have. Native skill loaders generally tolerate this (skills don't bind to a model the way agents do), but flag for consistency. **Non-blocking — does not affect BATCH A scope.**
- **CAVEAT-2**: The 4-mechanism Z2-retro alignment in SKILL.md is structurally present but slightly thinner on "YAML closeout parser" than the verdict §1 #2 LOCKED concession lists. SKILL §"Forbidden shortcuts" line "Slice card YAML must parse before claiming phase is reusable" covers the spirit. Acceptable; flag for future SKILL revision.

I articulate WHY this APPROVE: the executor's path-(a/b) deprecate-with-stub recommendation (per their boot §2 BATCH A.1) was the CORRECT call vs full-delete, validated by my independent `--code-review-graph-protocol --json` returning `{"ok": true, "issues": []}`. Path-c (full delete + 5-script patch) would have been ~150 LOC of changes outside doc-only batch boundary and would have risked regressions. The work is honest, the work is contained, the work doesn't exceed BATCH A scope.

## Pre-review independent reproduction

Commands run as critic, not from executor's report:

```
$ git diff --stat HEAD -- AGENTS.md architecture/AGENTS.md architecture/code_review_graph_protocol.yaml
 AGENTS.md                                    | 16 ++++++++++++----
 architecture/AGENTS.md                       |  4 ++--
 architecture/code_review_graph_protocol.yaml | 18 +++++++++++++++---
 3 files changed, 29 insertions(+), 9 deletions(-)
```

7 files claimed by executor; 3 modified + 4 new. Verified all present: `.claude/agents/{critic-opus,verifier,safety-gate}.md` (4926+3875+4318 bytes; 68+65+104 LOC) + `.claude/skills/zeus-phase-discipline/SKILL.md` (3321 bytes; 45 LOC).

```
$ .venv/bin/python -m pytest tests/test_architecture_contracts.py -q --no-header
73 passed, 22 skipped in 3.17s
```

ZERO drift from live baseline (73/22/0). My boot-time live baseline was already 73-pass, NOT the 71-pass documented in judge_ledger §54 + executor _boot §3 — meaning the 2 evaluator.py:377 failures were resolved sometime between baseline doc-time and now. **Executor's "73 pass / 22 skip / 0 fail matches your live baseline" claim is correct; this is the new ground truth.**

```
$ python3 scripts/topology_doctor.py --code-review-graph-protocol --json
{"ok": true, "issues": []}
```

Validator unbroken — the deprecate-with-stub strategy preserves all 7 required field invariants.

## ATTACK A1 (code_review_graph_protocol.yaml deprecate-with-stub) [VERDICT: PASS]

**A1.1 — Validator field invariants intact**: PASS.
- All 7 top-level keys present: schema_version, metadata, required_sections, stages, invocation_rules, forbidden_uses, verification_gates (verified via `yaml.safe_load` with `.venv/bin/python`).
- Both stages present with correct `order`+`required` (semantic_boot order=1 required=True; graph_context order=2 required=False).
- All 6 `forbidden_uses` items intact (settlement truth / source validity / current fact freshness / authority rank / planning lock waiver / receipt or manifest waiver).
- `invocation_rules.graph_requires_semantic_boot=true`, `graph_authority_status=derived_not_authority`, `explicit_changed_files_required_for_review_debug=true` — all preserved.
- `metadata.authority_status="derived_context_protocol_not_authority"` preserved (validator at `topology_doctor_policy_checks.py:739` requires this exact string).
- Independent run of `python3 scripts/topology_doctor.py --code-review-graph-protocol --json` returns `{"ok": true, "issues": []}` confirming all 5 dependent scripts continue to function.

**A1.2 — Root AGENTS.md grep tokens for validator**: PASS.
- `topology_doctor_policy_checks.py:763-766` requires (a) the literal string `architecture/code_review_graph_protocol.yaml` in root AGENTS.md and (b) "Stage 1/Stage 2" wording in the §Code Review Graph block.
- `grep -c "architecture/code_review_graph_protocol.yaml" AGENTS.md` returns **2** (was 1 before; new diff added 1 line `Manifest at architecture/code_review_graph_protocol.yaml is DEPRECATED`).
- `grep -E "Stage 1|Stage 2|semantic boot|graph context" AGENTS.md` returns 4 matches: "Two stages.", "Stage 1 (required): semantic boot via", "fatal misreads, and required proof questions. Stage 2 (optional): graph", "blast radius, review order. Stage 2 NEVER...". Validator-token coverage retained.
- The literal string `architecture/code_review_graph_protocol.yaml` is also still present in `architecture/AGENTS.md` registry row 59 with the DEPRECATED descriptor, which is a registry update (per AGENTS.md §4 mesh maintenance — registry rows for files SHOULD be updated when files change disposition, not deleted unless the file is deleted).

**A1.3 — Stub file still on disk + correct length**: PASS.
- File exists at `architecture/code_review_graph_protocol.yaml` with 74 LOC (was 62 LOC, grew by 12 LOC of deprecation comment + new metadata fields). Within scope.

**A1.4 — YAML parse**: PASS. `yaml.safe_load` returns dict with 7 expected keys, no parse errors. Deprecation comment is a valid YAML comment block (lines starting with `#`), no quote/indent issues.

**Bonus A1 finding**: `metadata.deprecated="2026-04-28"` and `metadata.superseded_by="AGENTS.md root §Code Review Graph (inline 6-line summary)"` were added — these are GOOD provenance markers per CLAUDE.md "Code Provenance: Legacy Is Untrusted Until Audited" rule. A future cold-start agent can immediately tell this file is on retirement track.

## ATTACK A2 (3 native subagents under workspace `.claude/agents/`) [VERDICT: PASS]

**A2.1 — Files exist + sizes plausible**: PASS.
- `.claude/agents/critic-opus.md` 4926 bytes / 68 LOC
- `.claude/agents/verifier.md` 3875 bytes / 65 LOC  
- `.claude/agents/safety-gate.md` 4318 bytes / 104 LOC

**A2.2 — YAML frontmatter valid**: PASS for all 3.
- critic-opus.md L1-5: `--- name:critic-opus / description:... / model:opus / ---`
- verifier.md L1-5: `--- name:verifier / description:... / model:sonnet / ---`
- safety-gate.md L1-5: `--- name:safety-gate / description:... / model:sonnet / ---`
- All have `name`, `description`, `model` fields. Native subagent loader requires `name` + `description` minimum; `model` is canonical Anthropic field. All present.

**A2.3 — critic-opus.md has 10 explicit numbered adversarial attacks** (per memory `feedback_critic_prompt_adversarial_template`): **PASS — verified strict.**
- `grep -E "^[0-9]+\.\s\*\*" .claude/agents/critic-opus.md` returns 10 numbered lines (1-10), each with bold attack title and explanation.
- Specific attacks present: Citation rot (1), Premise mismatch (2), Test relationship coverage (3), Authority direction (4), Negative-space audit (5), Provenance chain (6), Mode mismatch (7), Type-encodable category errors (8), Compaction survival (9), Rollback path (10).
- "Forbidden phrases" section (L31-37) explicitly bans "Pattern proven" without test citation, "Narrow scope self-validating", "Looks good", "Approved without evidence" — directly enforces the memory `feedback_critic_prompt_adversarial_template` rule.
- "Anti-rubber-stamp escalation" (L66-68) instructs the critic to STOP and re-read attacks 5/8/9 if it wants to write APPROVED on first read. This is NOT lip service — it operationalizes the discipline.
- This is the strongest piece of the BATCH. The critic-opus.md is a real adversarial template, not a "review my code" prompt.

**A2.4 — safety-gate.md cites planning-lock + map-maintenance commands**: PASS.
- L33: `python3 scripts/topology_doctor.py --planning-lock --changed-files <files...> --plan-evidence <plan file>` ✓
- L55: `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files <files...>` ✓
- Distinguishes itself cleanly from critic-opus and verifier in §"Distinct from critic-opus and verifier" (L94-98).
- §"Anti-bypass" (L101-104) explicitly forbids skipping a gate because "small change" or "executor said it's safe" — direct counter to executor-self-approval pattern flagged in memory `feedback_executor_commit_boundary_gate`.

**A2.5 — Workspace scoping doesn't shadow global `~/.claude/agents/critic.md` for wrong reasons**: PASS.
- The names are different — critic-opus (workspace) vs critic (global). Both can coexist. Workspace critic-opus is opus-bound and Zeus-specialized; global critic is generic.
- verifier.md and safety-gate.md don't have global counterparts (verified `ls ~/.claude/agents/` shows critic.md, executor.md, verifier.md present; the workspace verifier.md DOES shadow but the description difference clarifies role intent, and Anthropic native loader prefers workspace per docs).

## ATTACK A3 (IMPLEMENTATION_PROTOCOL.md → 47-line SKILL.md) [VERDICT: PASS-WITH-NOTES]

**A3.1 — SKILL preserves 5 essential mechanisms** (per opponent §3.2 + judge §4.1 #7): **PASS**.
- per-phase boot evidence file: SKILL §"Boot" L12-16 + §"Closeout" L26-30 (`learnings/<phase>_<author>_<date>_retro.md`)
- critic-opus + verifier dispatch closeout: SKILL §"Closeout" L28-29 explicitly references `.claude/agents/critic-opus.md` + `.claude/agents/verifier.md` — the SKILL ties to the real subagent files created in A.2 (cross-batch coherence)
- antibody contracts (NC-NEW-A..J): SKILL §"During implementation" first bullet "Antibody contracts (NC-NEW-A..J) are SQL/semgrep, not prose" ✓
- frozen interfaces: SKILL §"During implementation" third bullet "Frozen interfaces are downstream-stable. If you need to break one, write `r3/_protocol_evolution/<topic>.md` first." ✓
- symbol-anchored citations: SKILL §"During implementation" second bullet "Citations rot. When you cite a file:line, also cite a SYMBOL... The drift-checker re-verifies on the symbol." ✓ — directly references the BATCH B drift-checker (cross-batch coherence)

**A3.2 — IMPLEMENTATION_PROTOCOL.md NOT deleted**: PASS.
- File still exists at `docs/operations/task_2026-04-26_ultimate_plan/r3/IMPLEMENTATION_PROTOCOL.md` at full 465 LOC. Per executor boot §2.A.3 + dispatch + judge §4.1 #7, delete is operator-decision territory. Executor correctly created the SKILL alternative without deleting the source.

**A3.3 — Z2 retro 4-mechanism preservation**: PASS-WITH-NOTE.
- Z2 retro (`r3/learnings/Z2_codex_2026-04-27_retro.md` per verdict.md §1 #2 LOCKED) named 4 mechanisms: (a) critic-opus dispatch — preserved in SKILL §Closeout L28; (b) verifier dispatch — preserved in SKILL §Closeout L29; (c) tests (specifically pytest as gate) — preserved in SKILL §"Forbidden shortcuts" L34 ("`tests pass` alone ≠ shipped"); (d) YAML closeout parser — partially preserved in SKILL §"Forbidden shortcuts" L36 ("Slice card YAML must parse before claiming phase is reusable"). 
- **NOTE**: the 4th mechanism ("YAML closeout parser") is mentioned but lighter on operational detail than (a)-(c). The original IMPLEMENTATION_PROTOCOL.md had it as a step in the boot/closeout structure with explicit script invocation. The 47-line constraint forces compression. Acceptable trade-off; flag as **CAVEAT-2** above for possible Tier 2 SKILL revision.

**A3.4 — SKILL frontmatter loads**: PASS-WITH-NOTE.
- L1-4: `--- name:zeus-phase-discipline / description:... ---`. Has `name` + `description`.
- **NOTE — CAVEAT-1**: missing `model:` field that all 3 subagents have. SKILL loaders may not require model (skills are advice; agents are role-bound), but flag for consistency. **Non-blocking.**
- The description is informative + cites the verdict.md authority basis + names the trigger ("auto-loads when... working on r3 phases, slice cards, or any multi-session implementation"). This is a high-quality skill description per Anthropic skill conventions.

## Cross-batch coherence checks (longlast critic discipline)

- **SKILL.md → subagent linkage**: SKILL §Closeout L28-29 references `.claude/agents/critic-opus.md` and `.claude/agents/verifier.md`. If BATCH B+ touched these subagent files, this linkage would break. As of BATCH A close, all 3 subagent files exist and are referenced consistently.
- **SKILL.md → BATCH B drift-checker linkage**: SKILL §"During implementation" L21 says "the drift-checker re-verifies on the symbol." This is forward-looking to BATCH B's `scripts/r3_drift_check.py --architecture-yaml` extension. If BATCH B fails to ship the drift-checker, this SKILL line becomes a forward reference to vapor. **Will verify in BATCH B review.**
- **SKILL.md → safety-gate linkage**: SKILL §"When to stop" L41-43 says "planning-lock applies, see safety-gate agent." Cross-references safety-gate.md created in A.2. Coherent.
- **AGENTS.md root → architecture/AGENTS.md → YAML stub linkage**: All three describe code_review_graph_protocol.yaml as DEPRECATED with consistent date (2026-04-28) and consistent forwarding ("see root AGENTS.md §Code Review Graph"). No drift between the 3 sources.

## Independent regression baseline reproduction

Run twice with identical results (73/22/0 in 3.17s and 3.94s). The drift between executor-cited 73/22/0 and the documented 71/22/2-pre-existing is NOT executor's responsibility — it predates BATCH A. **For all subsequent batches, baseline is 73 pass / 22 skip / 0 fail; any new failure is BLOCK regardless of attribution.**

## Anti-rubber-stamp self-check (per discipline pledge in boot §4)

Re-reading my verdict before submitting: I have written APPROVE-WITH-CAVEATS, not APPROVE. The 2 caveats are real but non-blocking for BATCH A scope. None of the 10 critic-opus attack patterns identifies a defect requiring REVISE or BLOCK. The work is honest doc-only churn with appropriate scope discipline (recommended deprecate-with-stub over more aggressive removal). Z2 retro mechanism preservation is real (4/4 named mechanisms structurally present). Adversarial template embeds 10 numbered attacks — verified by grep, not skimmed. Planning-lock receipt is real — verified by independent topology_doctor invocation, not trusted from executor's report.

I have NOT written "narrow scope self-validating" or "pattern proven" without test citation. I have NOT written "looks good." I have engaged the strongest claim (correctness of the deprecate-with-stub strategy) at face value before pivoting to the caveats.

## Required follow-up (non-blocking, not BATCH A scope)

1. **BATCH B should resolve the forward-reference**: SKILL.md L21 references "drift-checker re-verifies on the symbol" — make sure the BATCH B `r3_drift_check.py --architecture-yaml` extension actually supports symbol-level verification, not just path-existence.
2. **Tier 2 SKILL revision**: when SKILL.md is next touched, consider adding `model:` field and tightening the YAML-closeout-parser language.
3. **Settings.json**: workspace `.claude/settings.json` does NOT exist (only `settings.local.json`). When BATCH B installs hooks, they must register in settings (workspace settings.json or ~/.claude/settings.json) per the `update-config` skill description rules. Hooks-on-disk without settings registration = silent no-op.

## Final verdict

**APPROVE-WITH-CAVEATS** — proceed with BATCH B. CAVEAT-1 + CAVEAT-2 are non-blocking and do not require executor revision before GO_BATCH_B. I will track them into BATCH B+C+D reviews as cross-batch concerns.

End BATCH A review.
