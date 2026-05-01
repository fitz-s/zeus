# Executor Boot — zeus-harness-debate-2026-04-27

Created: 2026-04-27
Author: executor-harness-fixes@zeus-harness-debate-2026-04-27
Judge: team-lead
Peers: proponent-harness, opponent-harness (both idle, LONG-LAST)
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (per TOPIC.md L5; branch plan-pre5)

## §0 Read summary

Sources read in full or substantially:

- `docs/operations/task_2026-04-27_harness_debate/TOPIC.md` (93 lines) — debate framing
- `verdict.md` (202 lines) — round-1 verdict; mixed net-negative tilt; §1 12 LOCKED concessions
- `round2_verdict.md` (258 lines) — synthesized middle; §4.1 has the 8 ready-today items I execute
- `evidence/opponent/round2_proposal.md` §3.1-§3.4 (lines 121-296) — code templates for HK HKO ABC + 47-line SKILL.md + r3_drift_check.py extension diff
- `evidence/proponent/round2_critique.md` §6 (lines 229-312) — A1-A6 acceptances + H1-H4 holds
- `AGENTS.md` (root, 335 lines) — §3 navigation/topology routing, §4 planning lock, §4 mesh maintenance
- `architecture/code_review_graph_protocol.yaml` (62 lines, full)
- `architecture/invariants.yaml` lines 100-150 (INV-13 thru INV-19; PRUNE_CANDIDATE markers on INV-16/17 confirmed at lines 119-134)
- `architecture/fatal_misreads.yaml` (153 lines, full) — `hong_kong_hko_explicit_caution_path` row at lines 118-134
- `architecture/AGENTS.md` lines 50-65 (registry table including `code_review_graph_protocol.yaml`)
- `r3/IMPLEMENTATION_PROTOCOL.md` lines 1-150 (14-mech catalog + per-phase boot protocol structure)
- `src/contracts/settlement_semantics.py` (182 lines, full)
- `r3/scripts/r3_drift_check.py` (303 lines, full) — at non-canonical path
- `architecture/map_maintenance.yaml` — checked for code_review_graph refs (none found)
- `~/.claude/agents/` — existing executor.md + verifier.md + critic.md patterns to model from

## §1 Path corrections from dispatch (load-bearing)

The dispatch references several paths whose actual locations differ from assumption. **Operator MUST acknowledge these before BATCH approvals.**

| Dispatch said | Actual on HEAD | Implication |
|---|---|---|
| `r3/IMPLEMENTATION_PROTOCOL.md` | `docs/operations/task_2026-04-26_ultimate_plan/r3/IMPLEMENTATION_PROTOCOL.md` | r3 is nested under task folder; not at repo root |
| `scripts/r3_drift_check.py` | `docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py` | Script lives inside task r3/ subdir |
| `tests/test_settlement_semantics.py` | DOES NOT EXIST | Will need CREATE in BATCH C |
| Workspace `.claude/agents/`, `.claude/skills/`, `.claude/hooks/` | NONE EXIST | Need CREATE on first use |

`tests/test_architecture_contracts.py` exists (3759 lines). `src/contracts/settlement_semantics.py` exists (182 lines).

## §2 Per-batch execution plan

### BATCH A — doc-only (lowest risk)

**A.1 Inline+DELETE `architecture/code_review_graph_protocol.yaml` (§4.1 #8)** — HIGH BLAST RADIUS, NOT trivial. Grep shows the file is referenced by:
- `scripts/topology_doctor.py:47,154,274,794-795` (loader + run-id wiring)
- `scripts/topology_doctor_core_map.py:31` (canonical-owner list)
- `scripts/topology_doctor_ownership_checks.py:126` (issue codes)
- `scripts/topology_doctor_policy_checks.py:723-761` (~40 lines of validator)
- `scripts/topology_doctor_code_review_graph.py` (23KB module — name only, presumably orchestrates the protocol)
- `architecture/topology_schema.yaml:172,392-398` (canonical_owner + 7 issue codes)
- `architecture/history_lore.yaml:1881,1904,1914` (3 lore-card refs; safe to leave; lore is historical)
- `architecture/module_manifest.yaml:768,772,774` (3 manifest refs)
- `architecture/AGENTS.md:59` (registry table row)

**Risk verdict**: Pure DELETE breaks topology_doctor's `--code-review-graph-protocol` validator and `--code-review-graph-status` flag (62-line manifest is what this validator validates). Safe-execution path: (a) inline 6-line summary into root AGENTS.md, (b) leave the YAML on disk but mark it DEPRECATED with stub fields satisfying the validator, OR (c) execute full removal but ALSO patch all 5 scripts + topology_schema.yaml to drop the `code_review_graph_protocol_*` checks. Path (c) is ~150 LOC of script changes, not within doc-only batch boundary. **Recommend Path (a/b) for BATCH A: inline summary into AGENTS.md, mark YAML DEPRECATED with header + retain stub for validator until BATCH B+ touches scripts.** Operator decides.

**A.2 Native subagents** (§4.1 #6) — CREATE 3 files at `/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/agents/`:
- `critic-opus.md` — adversarial template per memory `feedback_critic_prompt_adversarial_template` (10 explicit asks, no rubber-stamp)
- `verifier.md` — evidence-based completion check pattern; modeled on `~/.claude/agents/verifier.md` (179 lines reference) but Zeus-scoped
- `safety-gate.md` — planning-lock + map-maintenance enforcement; cites `scripts/topology_doctor.py --planning-lock` and `--map-maintenance`

Scope: **workspace-scoped, NOT global.** Per dispatch ("use SCOPED location"), and verified workspace `.claude/` exists; create `agents/` subdir.

**A.3 IMPLEMENTATION_PROTOCOL → SKILL.md** (§4.1 #7) — CREATE `/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/skills/zeus-phase-discipline/SKILL.md` per opponent's §3.2 47-line template. Preserve essential anti-drift mechanisms (per-phase boot, citation rot via symbol anchors, antibody contracts NC-NEW-A..J, critic+verifier dispatch closeout, frozen interfaces). Do NOT delete `IMPLEMENTATION_PROTOCOL.md` — operator approval required for delete (per dispatch "let operator approve before delete").

**Planning-lock evidence path for BATCH A**: `docs/operations/task_2026-04-27_harness_debate/round2_verdict.md` (this is the authoritative plan; both sides locked §4.1 items).

### BATCH B — mechanical, contained

**B.1 Hooks** (§4.1 #5) — CREATE `/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/hooks/`:
- `pre-edit-architecture.sh` — refuses Edit/Write to `architecture/**` unless plan-evidence env or arg present
- `pre-commit-invariant-test.sh` — runs `pytest tests/test_architecture_contracts.py -q --no-header` before commit; aborts on new failures vs baseline (71 pass / 22 skipped per judge ledger §54)

**B.2 r3_drift_check.py extension** (§4.1 #3) — Two options: (a) extend the version at `docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py` with new `check_yaml_citations()` function, (b) create a new top-level `scripts/r3_drift_check.py` per dispatch path. Dispatch says `scripts/r3_drift_check.py` so I will CREATE a top-level shim at `scripts/r3_drift_check.py` that imports from the r3-located module + adds the `--architecture-yaml` flag. **Need operator confirmation: shim approach OK, or extend in-place at r3 path?**

### BATCH C — architecture; HIGH CARE; K0_frozen_kernel zone

**C.1 SettlementRoundingPolicy ABC** (§4.1 #4) — Add to `src/contracts/settlement_semantics.py`. **Critical integration concern**: existing module already implements rounding via `RoundingRule = Literal["wmo_half_up", "floor", "ceil", "oracle_truncate"]` + `SettlementSemantics.round_values()` dispatch on string. Adding ABC + `HKO_Truncation` + `WMO_HalfUp` subclasses creates a parallel structure. Two paths:
- **Append-only** (lower risk, opponent §3.1 verbatim): add ABC + 2 subclasses + `settle_market(market, raw_temp, policy)` function that asserts `isinstance(policy, HKO_Truncation)` for HK / `not HK` for non-HK. New code path; existing `assert_settlement_value` unchanged.
- **Replace** (higher risk, opponent's "make category impossible" full vision): refactor `SettlementSemantics.round_values()` to delegate to a `policy: SettlementRoundingPolicy` field, deprecate `rounding_rule` Literal. Touches every caller of `round_single`/`assert_settlement_value`.

Dispatch language ("add SettlementRoundingPolicy ABC + HKO_Truncation + WMO_HalfUp subclasses (~30-60 LOC)") matches **append-only**. **Will execute append-only**; document path-2 as future work in BATCH C closeout.

`fatal_misreads.yaml` HK row update: append `TYPE_ENCODED:src/contracts/settlement_semantics.py:HKO_Truncation` token to existing row's `proof_files` or `correction` block. Do NOT delete row (per dispatch).

Test: CREATE `tests/test_settlement_semantics.py` with `test_hko_policy_required_for_hk_city_raises_typeerror` + `test_wmo_policy_for_hk_city_raises_typeerror` + `test_hko_policy_for_non_hk_raises_typeerror`. Three relationship assertions.

### BATCH D — DELETE INV-16 + INV-17

**D.1** Lines 119-126 (INV-16 block) and 127-134 (INV-17 block) in `architecture/invariants.yaml`. Both have PRUNE_CANDIDATE markers from 2026-04-28 (verified at offset). Delete both blocks cleanly.

**D.2** After delete: scan `architecture/negative_constraints.yaml` for NC-12/NC-13 references. If those NCs exist independently with their own `enforced_by` blocks → leave alone. If they were paired with INV-16/17 only → flag for operator review (do NOT delete NCs without explicit approval).

**D.3** Check for any other INV that cross-cites INV-16 or INV-17 (e.g., `related: [INV-16]` blocks). Update those refs if found.

## §3 Risk assessment per batch

| Batch | Risk level | Failure mode | Mitigation |
|---|---|---|---|
| A | LOW (docs/skills) + MEDIUM (YAML delete blast radius) | Deleting `code_review_graph_protocol.yaml` outright breaks 5 topology_doctor scripts | Use Path (a/b) deprecation-with-stub; full delete deferred to script-aware batch |
| B | LOW-MEDIUM | Hooks could block legitimate work; new script path differs from existing r3-located script | Hooks have soft-allow via env var (`ARCH_PLAN_EVIDENCE=<path>`); shim approach for drift check needs operator OK |
| C | HIGH (K0_frozen_kernel) | Type-encoded ABC could conflict with existing `RoundingRule` Literal pattern; HK city detection logic could mis-route | Append-only path (separate function `settle_market`); existing callers untouched; 3 relationship tests cover the type-mixing surface |
| D | LOW | NC-12/NC-13 deletion cascade if cross-referenced | Read-only audit of NCs first; flag-only, no NC delete without explicit operator approval |

Pre-existing test baseline per judge ledger §54: **71 passed / 22 skipped / 2 pre-existing failures in evaluator.py:377 temperature_metric** (NOT mine). Per-batch test report will diff against this baseline.

## §4 Discipline pledges

- Before any `architecture/**` edit: run `python3 scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence docs/operations/task_2026-04-27_harness_debate/round2_verdict.md`
- After each batch: run `.venv/bin/python -m pytest tests/test_architecture_contracts.py -q --no-header`
- file:line citations grep-verified within 10 min before any architecture/** lock
- Disk-first (write before SendMessage)
- NO git commits without explicit operator instruction (operator commits all batches together at end)
- After each batch: SendMessage exactly `BATCH_X_DONE files=<count + paths> tests=<X passed Y failed> planning_lock=<receipt path or N/A>` then idle for next GO

## §5 Out-of-scope (per dispatch §4.2 — operator-decision territory; will NOT touch)

- §4.2 #10 topology.yaml 90-day audit
- §4.2 #11 module_manifest.yaml replacement
- §4.2 #12 history_lore.yaml archive vs delete
- §4.2 #13 @enforced_by decorator prototype
- §4.3 larger work (separate dispatch)

## §6 Open clarifications for team-lead

1. **A.1 Path choice**: deprecate-with-stub (preserves topology_doctor validator) vs full-delete-with-script-patches (~150 LOC additional)? Recommend deprecate-with-stub for BATCH A.
2. **B.2 Drift check path**: top-level shim at `scripts/r3_drift_check.py` (matches dispatch language) vs in-place extension at `docs/operations/.../r3/scripts/r3_drift_check.py` (preserves existing layout)?
3. **A.2 Skills/agents location**: WORKSPACE-scoped (`/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/`) per dispatch hint "SCOPED" — confirm? (Global `~/.claude/agents/` already has critic.md + verifier.md; Zeus-scoped versions would override.)
4. **C.1 Append-only vs replace**: confirm append-only (parallel structure, lower risk) is OK, or do you want full integration with existing `RoundingRule` Literal?

Will idle after BOOT_ACK_EXECUTOR. Will execute BATCH A only after explicit `GO_BATCH_A` from team-lead, with answers to §6 clarifications (or default-to-recommendation if no specific guidance).

End of boot.
