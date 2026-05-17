# Critic Round 2 — Opus critic verdict on revised SCAFFOLD + PLAN

Created: 2026-05-17
Critic: opus (a247c6b55db96d2a4)
Verdict: **FIX_REQUIRED**

Probes: V1=PASS V2=FAIL V3=PARTIAL_FAIL V4=FAIL V5=PARTIAL V6=PASS V7=ESCALATE

## Critical (block Phase-1)

1. **SCAFFOLD §3 topology.yaml verdict cite is WRONG.** Cited `(scripts/topology_doctor.py, L41 NAMING_CONVENTIONS_PATH + L57 DOCS_REGISTRY_PATH, sha8=58ec7cab)` for the **topology.yaml** verdict. Actual `topology.yaml` loader = `TOPOLOGY_PATH` at **L34**. Cited lines load DIFFERENT yamls. Sha8-correct, line-wrong, on wrong yaml. **Fix**: `SCAFFOLD.md::§3 topology.yaml verdict` → loader cite = `(scripts/topology_doctor.py, L34 TOPOLOGY_PATH, sha8=58ec7cab)`.
2. **sha8 contract ambiguity.** Both `docs_registry.yaml` and `topology.yaml` verdicts cite `sha8=58ec7cab` = full-file SHA of `topology_doctor.py`, not content-at-line. W2 lint will reject SCAFFOLD's own cites by W2's rules unless the cite-format triplet is normalized. **Fix**: Lock the contract explicitly — `(path, line, file_sha8)` OR `(path, anchor_text, content_sha8)` — and apply consistently to all 5 §3 cites BEFORE W2 dispatches.
3. **W4 enumerates 2 phantom inbound sites.** `docs/authority/AGENTS.md` + `REVIEW.md` have NO existing `ARCHIVAL_RULES` ref — they need INSERT not UPDATE. "4 packet bodies" claim is unverified. Actual = 11 verified update sites. **Fix**: `EXECUTION_PLAN.md::§2 W4` → split into `update_existing` (11) + `insert_new` (3 incl. `docs/authority/AGENTS.md`, `REVIEW.md`, optionally `docs/AGENTS.md`); drop or enumerate "4 packet bodies".
4. **Phase-1b is vapor.** No planning artifact for `src/contracts/source_family.py`. Plan says "Phase-1b sibling PR, parallel" but no packet dir, no plan file, no acceptance criteria. FM-10 type-system antibody has no committed delivery surface. **Fix**: choose (a) skeleton packet `docs/operations/task_2026-05-17_source_family_typed_enum/PLAN.md`, OR (b) MOVE source_family.py + test into Phase-1 W5 (~80 LOC, budget allows), OR (c) explicitly disclaim Phase-1 FM-10 prevention.

## Major (cause rework, don't block)

5. **FM-10 `topic_authority` is kind-substitution.** SCAFFOLD §4 FM-10 row claims `topic_authority` registry field as "docs-level prevention" — but `topic_authority` is a per-doc canonical-claim deduper, doesn't encode source-family identity. The R1 critic explicitly stated type-system IS the correct KIND for FM-10. **Fix**: `SCAFFOLD.md::§4 FM-10 row` → remove "Phase-1 ships topic_authority (docs-level prevention)"; state "Phase-1 prevention for FM-10 = NONE" (if Phase-1b stays vapor) OR "FM-10 prevention = source_family.py typed enum in W5" (if moved to Phase-1).
6. **`test_lifecycle_field_orthogonality.py` has no assertion shape.** SCAFFOLD §3 says "asserts the three enum sets maintain documented orthogonality" — what does "orthogonality" mean computationally? **Fix**: spell out invariant: (a) pairwise-disjoint enum value sets across `artifact_authority_status.status`, `docs_registry.lifecycle_state`, `ARCHIVAL_RULES` verdicts; (b) `status == ARCHIVED ⇒ lifecycle_state ∈ {historical}`; (c) `ARCHIVAL_RULES verdict == LOAD_BEARING ⇒ status ∉ {ARCHIVED}`.
7. **W3/W4 swap is commentary-only.** PLAN has parenthetical notes "W3 executes after W4" but wave table still presents W3 before W4 numerically. Executor following wave numbers will hit the authority-pointer-inversion. **Fix**: renumber waves (W1→W2→W4→W3→W5→W6 actual exec order) OR add explicit "Execution sequence: W1 W2 W4 W3 W5 W6" callout at top of §2 AND at every wave header.
8. **W2 retro-cite algorithm unspecified.** Plan says "retro-cite pass for SCAFFOLD.md + EXECUTION_PLAN.md to dogfood lint" — deliverable, not algorithm. **Fix**: spell out step-by-step: (a) enumerate all `path:line` cites in SCAFFOLD/PLAN via regex; (b) for each, resolve to canonical triplet using locked format from Critical #2; (c) replace inline with HTML-comment marker placed above cited text; (d) re-run lint, require exit 0.

## What's missing

- Pre-revision residual `topology_doctor.py:11/12` cite verification — confirm fully removed from §3.
- `topology_doctor_freshness_checks.py` + `topology_doctor_digest.py` existence — PLAN W3 calls them but doesn't verify. If they don't exist, W3 gate is vapor.
- `archive/cold/` `expected_empty: true` schema field — no probe confirms `topology.yaml` schema supports this flag.
- W3 rollback if break — "A → B → C" mitigation chain doesn't include "revert all 3 yaml edits via git checkout".
- `architecture/test_topology.yaml` + `data_rebuild_topology.yaml` (siblings loaded by `topology_doctor.py:L39/L42`) — do W3 manifest changes need parallel edits to these?
- `docs/AGENTS.md` is in §5 self-discoverability checklist but NOT in W6 files-touched — checklist-vs-wave-table inconsistency.

## Ambiguity risks

- `"retro-cite or W6 exclusion"` either/or framing → executor picks easier (B = exclusion), defeating dogfooding. Lock to ONE.
- `"4 packet bodies"` in W4 row → unenumerated, executor will skip silently.

## Delta assessment

R1→R2 was net-improvement on the EASY fixes (P5 rejection well-defended, W4/W6 counts updated, W3 gate expanded, fixture sub-step added) but net-regression on the HARD fix (citation rot — R2 trades old wrong cites for new wrong cites and adds vapor deferral).

Critic explicitly recommends opus for R3 because remaining fixes are contract-design decisions, not enumeration tasks. (Orchestrator override: sonnet R3 with pre-decided contracts in brief — see ROUND_2_REVISE_ORCHESTRATOR_DIRECTIVES below.)
