# Critic-Harness Boot — zeus-harness-debate-2026-04-27

Created: 2026-04-27
Author: critic-harness@zeus-harness-debate-2026-04-27
Judge: team-lead
Peers: proponent-harness, opponent-harness (idle, longlast); executor-harness-fixes (active mid-BATCH-A)
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (per TOPIC.md L5; branch plan-pre5)
Lifecycle: longlast (Tier 1 + Tier 2)

## §0 Read summary (~16 min wall, full or substantial)

- TOPIC.md (93 L), verdict.md (round-1, 202 L), round2_verdict.md (258 L), judge_ledger.md (244 L), DEEP_PLAN.md (327 L)
- evidence/executor/_boot_executor.md (135 L) — 4 path corrections + 4 clarifications + per-batch risk grid
- AGENTS.md (root, 343 L) — §3 navigation/topology, §4 planning lock, §4 mesh maintenance, **§3 has new "Code Review Graph" inline §** (lines ~240-253; cites code_review_graph_protocol.yaml as DEPRECATED stub — confirms BATCH A.1 doc-side already landed)
- architecture/AGENTS.md (85 L) — registry table; row 59 confirms code_review_graph_protocol.yaml DEPRECATED 2026-04-28
- architecture/code_review_graph_protocol.yaml (74 L, full) — DEPRECATED stub; field shape preserved (validator-compatible)
- architecture/invariants.yaml lines 100-260 — INV-13..INV-29; PRUNE_CANDIDATE markers on INV-16 (L120) + INV-17 (L128) confirmed
- src/contracts/settlement_semantics.py (182 L, full) — `RoundingRule` Literal pattern at L11; `for_city` HK branch at L161-173 already implements oracle_truncate via string dispatch
- tests/test_architecture_contracts.py (50 L head) — header confirms 2026-04-25 last reuse
- topology_doctor[_*].py refs to code_review_graph_protocol — 8 sites grep-verified (executor's count correct)

## §1 Pytest baseline reference (LIVE — drift from documented baseline detected)

Documented baseline (judge_ledger §54 + executor _boot §3): "71 passed / 22 skipped / 2 pre-existing failures in evaluator.py:377"

**LIVE baseline at boot (this session)**: `73 passed / 22 skipped / 0 failures` (3.94s wall).

Implication: the 2 evaluator.py failures appear to have been resolved between baseline doc-time (~2026-04-23 midstream verdict v2) and boot-time (2026-04-28 evening). I will use **73-pass, 22-skip, 0-fail** as my live regression baseline. Any new failures = BLOCK regardless of the executor's "no new failures vs documented baseline" claim.

## §2 Per-batch attack vectors (10-attack template per memory feedback_critic_prompt_adversarial_template)

### BATCH A — doc-only (executor mid-flight per task #8 status)

A.1 inline+stub `code_review_graph_protocol.yaml` (deprecate-with-stub per judge §4.1 #8):
- **A1.1**: Does the stub still satisfy ALL 7 validator field invariants (`required_sections`, 2 stages with order, `graph_requires_semantic_boot=true`, `graph_authority_status=derived_not_authority`, `explicit_changed_files_required_for_review_debug=true`, ALL 6 `forbidden_uses` items)? Verify by running `python3 scripts/topology_doctor.py --code-review-graph-protocol --json` — expect zero issues.
- **A1.2**: Did the AGENTS.md §Code Review Graph inline addition include the THREE root-AGENTS.md tokens grep-required by `topology_doctor_policy_checks.py:763-766` (`architecture/code_review_graph_protocol.yaml` reference + Stage 1/Stage 2 wording + presumably `semantic_boot` + `graph_context` words)? If missing → `code_review_graph_protocol_root_reference_missing` issue.
- **A1.3**: Did executor accidentally delete the YAML file (full-delete path) instead of stub-deprecating? Confirm file still exists at `architecture/code_review_graph_protocol.yaml` ≥ 60 LOC.
- **A1.4**: Stub's deprecation comment must NOT fail YAML parse (no unbalanced quotes, no tab/space mix). Run `yaml.safe_load`.

A.2 native subagents at `.claude/agents/` (workspace-scoped):
- **A2.1**: Files exist? `critic-opus.md`, `verifier.md`, `safety-gate.md` — confirmed via ls (4926 + 3875 + 4318 bytes).
- **A2.2**: Do they have proper YAML frontmatter? Most native-agent loaders require `name:` and `description:` minimum.
- **A2.3**: Does `critic-opus.md` actually embed the **10 explicit adversarial asks** per memory `feedback_critic_prompt_adversarial_template`? "Adversarial template" without 10 numbered checks = LARP.
- **A2.4**: Does `safety-gate.md` actually cite `scripts/topology_doctor.py --planning-lock` and `--map-maintenance` per dispatch §6 #3?
- **A2.5**: Workspace `.claude/agents/` does NOT shadow global `~/.claude/agents/critic.md` for the wrong reasons — critic-opus.md, verifier.md, safety-gate.md must be DIFFERENT names than global to coexist (which they are; critic vs critic-opus is fine).

A.3 IMPLEMENTATION_PROTOCOL.md → SKILL.md (`zeus-phase-discipline`):
- **A3.1**: Does the SKILL.md preserve the **5 essential mechanisms** (per opponent's §3.2 47-line template + judge §4.1 #7)?
  - per-phase boot evidence file
  - critic-opus + verifier dispatch closeout
  - antibody contracts (NC-NEW-A..J reference)
  - frozen interfaces
  - symbol-anchored citations (citation rot resistance)
- **A3.2**: Did executor delete `IMPLEMENTATION_PROTOCOL.md` outright? Per dispatch + boot §A.3, NO delete without operator approval. Verify file still exists at full 465 LOC.
- **A3.3**: Z2 retro `r3/learnings/Z2_codex_2026-04-27_retro.md` named **4 specific mechanisms** (critic + verifier + tests + YAML closeout parser). Did the SKILL preserve at least these 4? If not → known regression.
- **A3.4**: SKILL.md frontmatter requires `name`, `description`, optional `triggers` for keyword auto-detection. Validate it loads (some skills systems want explicit YAML).

### BATCH B — mechanical hooks + drift-checker

B.1 hooks at `.claude/hooks/`:
- **B1.1**: `pre-edit-architecture.sh` — does it actually FIRE on architecture/** edits? Hooks must be registered in settings.json or settings.local.json (otherwise file exists but never fires).
- **B1.2**: Hook needs to support a soft-allow bypass via env var (e.g., `ARCH_PLAN_EVIDENCE=path`) per executor §B.1; without it, BATCH C+D will be blocked by the hook the executor just installed (foot-gun loop).
- **B1.3**: `pre-commit-invariant-test.sh` — must run pytest against the DOCUMENTED 71-pass baseline OR adapted live 73-pass. Any drift in reference number = silent escape.
- **B1.4**: chmod +x on hook scripts? Without execute bit, hook silently no-ops.
- **B1.5**: `.claude/hooks/` is workspace-scoped — does Claude Code actually invoke workspace hooks, or only `~/.claude/hooks/`? Risk of "feature ships, never fires" if scope wrong.

B.2 r3_drift_check.py extension (top-level shim per judge ruling):
- **B2.1**: Does the new `scripts/r3_drift_check.py` actually import the r3 module successfully? `docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/` is NOT a Python package (no `__init__.py`); shim must use sys.path manipulation or runpy.
- **B2.2**: New `--architecture-yaml` flag — does it scan `architecture/*.yaml` for citation paths and verify file existence? Or does it just print "OK" with no actual coverage?
- **B2.3**: Verify it WOULD have caught the original 7-INV `migrations/...` drift if run on pre-fix HEAD. If the script doesn't catch the case it was BUILT to catch → defect.
- **B2.4**: Check it doesn't throw on the PRUNE_CANDIDATE marker comment lines (must be YAML-tolerant).
- **B2.5**: Does extension preserve original behavior of r3_drift_check (back-compat)? Don't break the Tier 0 use case.

### BATCH C — K0_frozen_kernel zone (HIGH RISK)

C.1 SettlementRoundingPolicy ABC + HKO_Truncation + WMO_HalfUp subclasses (append-only per judge):
- **C1.1**: Does new ABC + subclasses live in `src/contracts/settlement_semantics.py` AS APPEND? Existing `SettlementSemantics` dataclass + `RoundingRule` Literal must be UNCHANGED. If executor refactored existing `for_city` dispatch → SCOPE BREACH (replace, not append).
- **C1.2**: Does the new ABC actually make the wrong code UNCONSTRUCTABLE per Fitz "make category impossible"? An ABC alone doesn't enforce — there must be a `settle_market(market, raw, policy)` (or equivalent) function that validates `isinstance(policy, HKO_Truncation)` for HK and rejects WMO_HalfUp via TypeError at call time.
- **C1.3**: Test file `tests/test_settlement_semantics.py` is NEW (per executor §1; did NOT exist on HEAD). Three relationship assertions per executor §C: `test_hko_policy_required_for_hk_city_raises_typeerror`, `test_wmo_policy_for_hk_city_raises_typeerror`, `test_hko_policy_for_non_hk_raises_typeerror`. Verify all 3 actually call the new `settle_market` function (or equivalent) and verify TypeError is raised — not just `policy is HKO_Truncation` introspection (would be tautological).
- **C1.4**: HK city detection — what's the predicate? `city.name == "HK"`? `city.settlement_source_type == "hko"`? Hardcoded list? **The predicate IS the antibody surface.** If executor used `if "HK" in city.name`, it will mis-route any city with HK in the name (Hong Kong AND others). Existing `for_city` uses `source_type == "hko"` which is the right anchor.
- **C1.5**: `fatal_misreads.yaml` row update — must APPEND `TYPE_ENCODED:src/contracts/settlement_semantics.py:HKO_Truncation` token; must NOT delete the antibody row (defense-in-depth per round-2 verdict §1.3 #4).
- **C1.6**: planning-lock receipt — architecture/** edits AND src/** K0_frozen_kernel edits both require receipt; verify executor ran topology_doctor with `--plan-evidence round2_verdict.md`.
- **C1.7**: NumPy import + np.asarray pattern — new ABC subclasses must not introduce a different floor/round implementation than existing `round_wmo_half_up_values` (would create silent divergence between paths).
- **C1.8**: Does the test file have `# Created: 2026-04-27` + `# Authority basis:` per project convention (CLAUDE.md provenance rule)?
- **C1.9**: Existing 73-pass baseline preservation — new test file must add ≥3 NEW passes (resulting 76 pass / 22 skip / 0 fail), with no decrease in existing 73.

### BATCH D — DELETE INV-16 + INV-17

D.1 invariants.yaml block deletion:
- **D1.1**: Are `tests/test_phase6_causality_status.py` (INV-16, ~3 tests) and `tests/test_dt1_commit_ordering.py` (INV-17, ~6 tests) STILL PASSING after the YAML delete? **CRITICAL FINDING** during my boot: INV-16 and INV-17 ARE actually backed by tests in those files — they are NOT pure prose-as-law as the PRUNE_CANDIDATE markers claim. The markers cite verdict §6.1 but the verdict itself called them "pure prose-as-law on HEAD" which is WRONG by my grep evidence:
  - `tests/test_phase6_causality_status.py` 5K bytes, 3 tests citing INV-16 directly
  - `tests/test_dt1_commit_ordering.py` 11K bytes, 6 tests citing INV-17 directly  
  - `tests/test_phase10d_closeout.py` and `test_phase10e_closeout.py` ALSO cite INV-16/17
  - These are NOT registered in the YAML's `enforced_by.tests:` block but they DO exist
  
  **This is a verdict-source defect, not necessarily an executor defect.** Whether DELETE is correct depends on interpretation of "enforcement": YAML `enforced_by:` block is empty of tests, but tests EXIST that cite the INV name in docstrings. Both readings are defensible. **Recommendation: I will flag this in BATCH D review and recommend BLOCK pending operator decision** — whether the verdict's "pure prose" judgment was based on incomplete grep, or whether tests-citing-INV-by-name don't count as "enforcement" in the project's strict definition.
- **D1.2**: NC-12 (referenced by INV-16, also by INV-14) and NC-13 (referenced by INV-17) — verify NC-14 cross-refs unchanged after delete. NC-12 specifically is referenced by INV-14 too (line 108: `negative_constraints: [NC-11, NC-12]`). If executor deletes NC-12 cascade-style → orphans INV-14. **Read-only audit; flag-only per executor §D.2.**
- **D1.3**: Total INV count after delete = 28 (currently 30; verify via `grep -c "^  - id: INV-" architecture/invariants.yaml` post-delete = 28).
- **D1.4**: pytest baseline preserved — 73-pass survives delete.
- **D1.5**: `architecture/invariants.yaml` parses with `yaml.safe_load` post-delete.
- **D1.6**: Do any other architecture/*.yaml files cross-reference `INV-16` or `INV-17`? Quick grep needed — if `topology.yaml` or `module_manifest.yaml` cite them, those references become broken.
- **D1.7**: Topology doctor planning-lock receipt for invariants.yaml edit (architecture/**). Executor must cite round2_verdict.md as plan_evidence.

## §3 Per-batch verification commands (will run on REVIEW_BATCH_X notification)

```bash
# Universal regression baseline
.venv/bin/python -m pytest tests/test_architecture_contracts.py -q --no-header
# Expect: 73 passed / 22 skipped / 0 failed (LIVE baseline)

# Universal planning-lock verification
python3 scripts/topology_doctor.py --planning-lock --changed-files <executor's listed files> --plan-evidence docs/operations/task_2026-04-27_harness_debate/round2_verdict.md

# BATCH A specific
python3 scripts/topology_doctor.py --code-review-graph-protocol --json
python3 -c "import yaml; print(yaml.safe_load(open('architecture/code_review_graph_protocol.yaml')))"
ls -la .claude/agents/{critic-opus,verifier,safety-gate}.md
ls -la .claude/skills/zeus-phase-discipline/SKILL.md
grep -c "10\." .claude/agents/critic-opus.md  # rough proxy for "10 adversarial asks"

# BATCH B specific
ls -la .claude/hooks/{pre-edit-architecture,pre-commit-invariant-test}.sh
test -x .claude/hooks/pre-edit-architecture.sh && echo "EXEC bit OK"
.venv/bin/python scripts/r3_drift_check.py --architecture-yaml --json 2>&1 | head -30

# BATCH C specific
.venv/bin/python -m pytest tests/test_settlement_semantics.py -v
.venv/bin/python -c "from src.contracts.settlement_semantics import HKO_Truncation, WMO_HalfUp, SettlementRoundingPolicy; print('imports OK')"

# BATCH D specific
grep -c "^  - id: INV-" architecture/invariants.yaml  # expect 28
.venv/bin/python -m pytest tests/test_phase6_causality_status.py tests/test_dt1_commit_ordering.py tests/test_phase10d_closeout.py tests/test_phase10e_closeout.py -q
grep -rn "INV-16\|INV-17" architecture/ 2>&1  # expect: 0 in invariants.yaml; survey other YAML refs
```

## §4 Anti-rubber-stamp discipline pledge

- I will write `batch_X_review_2026-04-28.md` (≤300 L) BEFORE SendMessage `BATCH_X_REVIEW <verdict>`.
- I will articulate WHY I APPROVE even on green tests — never just "looks fine".
- I will NOT write "narrow scope self-validating" or "pattern proven" without the specific test that proves it.
- I will engage executor's strongest claim at face value before pivoting.
- I will note specifically when executor's claim is unverified vs verified.
- I will BLOCK on any new pytest failure regardless of attribution.
- I will REVISE on planning-lock missing/incomplete receipts.
- I will track per-batch defects + regressions across batches (longlast watch for cross-batch drift).

## §5 Cross-batch drift watch (longlast critic)

Things that won't show up in single-batch review but will across the 4-batch sweep:
- BATCH A creates a hook (skill mention) → BATCH B installs hook that BLOCKS BATCH C-D edits. Watch for self-foot-gun.
- BATCH C type encoding may CONFLICT with BATCH D INV-16/17 delete if INV-17 (commit-ordering) interacts with `SettlementSemantics.assert_settlement_value` DB-write gate.
- BATCH B drift-checker should CATCH any new path drift introduced by BATCH C+D edits — meta-test of executor's own work.
- Multiple batches each adding `# Created: 2026-04-27` headers — verify date consistency (all "Created: 2026-04-27" not "2026-04-28").

## §6 Idle until notified

Will idle until team-lead SendMessages `REVIEW_BATCH_X executor_path=<descriptor>`. Will NOT review BATCH A on speculation (executor mid-flight at boot time per task #8 status; my BATCH-A discoveries above are pre-review notes, not the actual review).

End boot.
