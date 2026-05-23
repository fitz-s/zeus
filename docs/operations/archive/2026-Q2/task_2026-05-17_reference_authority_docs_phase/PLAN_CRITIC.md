# PLAN_CRITIC — v1 critic verdict (archived 2026-05-16)

**Source:** opus plan-critic on PLAN.md v1 (orphaned with `zeus-doc-alignment-2026-05-16` worktree post-PR-#124 merge).
**Verdict:** REVISE (5 concrete amendments, 4 minors). Structure sound; fixes mechanical.
**Disposition:** all 5 amendments + 4 minors folded into PLAN.md v2 (this commit). See v2 §"Change log v1 → v2" for fold-confirmation.

---

## Amendments (CRITICAL / MAJOR / MAJOR / MAJOR / MAJOR)

### A1 (CRITICAL) — phantom CLI subcommand

v1 §5 WAVE 2 step 3 cited `python -m maintenance_worker.cli.entry validate --binding ...`. Verified: `validate` subcommand does NOT exist; surface is `{run, dry-run, status, init}`.

**Fix in v2**: replace with grep-verified `python -m maintenance_worker.cli.entry dry-run --config <path>`. WAVE 0 SCOUT 0B records exact per-binding loader-test commands in a "Loader Command Table".

**v2 reference**: §5 WAVE 2 step 2 + §6 + §7 + §12.

### A2 (CRITICAL) — ModuleNotFoundError on topology_doctor invocation

v1 §12 + §5 cited `python scripts/topology_doctor.py --strict-health`. Verified: triggers `ModuleNotFoundError: No module named 'scripts'`.

**Fix in v2**: every invocation now `PYTHONPATH=. python -m scripts.topology_doctor --strict-health` (or `--task-boot-profiles`, etc.). Module form verified to import cleanly.

**v2 reference**: §4 PR-B gate row + §5 WAVE 2 + §6 + §12.

### A3 (MAJOR) — reality_contracts misclassified

v1 TIER 3 deferred listed `config/reality_contracts/{data,economic,execution,protocol}.yaml`. Loader `src/contracts/reality_contracts_loader.py` exists today (verified extant). These are loader-backed runtime authority.

**Fix in v2**: moved to TIER 0B in-scope. §3 count: 6 → 10 TIER 0B docs. §11 WAVE 2 effort 4-5h → 5-6h. Risk-register row added for contract-loader breakage.

**v2 reference**: §3 TIER 0B table (4 rows added) + §11 + §7.

### A4 (MAJOR) — inflated LOC estimates

v1 §3 TIER 0A `Lines` column had estimates. Critic measured actuals:

| File | v1 claim | v2 actual |
|---|---|---|
| `architecture/runtime_modes.yaml` | ~120 | 33 |
| `architecture/runtime_posture.yaml` | ~80 | 31 |
| `architecture/world_schema_version.yaml` | ~60 | 18 |
| `architecture/maturity_model.yaml` | ~140 | 48 |
| `architecture/kernel_manifest.yaml` | ~80 | 118 |
| `architecture/negative_constraints.yaml` | ~150 | 168 |

**Fix in v2**: FCI4 `wc -l` run on every TIER 0A/0B/0C cited path; actuals recorded. §11 effort budget re-derived from real totals. Tier totals:
- TIER 0A: 1,895 LOC (8 docs)
- TIER 0B: 5,669 LOC (10 docs, incl. A3 promotion)
- TIER 0C: 1,485 LOC (4 docs)

**v2 reference**: §3 (all 3 TIER 0 tables) + §11.

### A5 (MAJOR) — TIER 1 AGENTS.md undercount

v1 §3 said "~31 docs"; table summed to ~36. Actual repo: 46 AGENTS.md (verified `git ls-files '**/AGENTS.md' '*AGENTS.md' | wc -l = 46`).

**Fix in v2**: reconciled to 40 TIER 1 in-scope (= 46 total − 5 observation subdirs deferred to TIER 3 − 1 archive). Every path explicitly bulleted in §3 TIER 1. WAVE 3 batch sizing re-derived for 40 docs: ≤5 parallel sonnet executors × 8 docs each.

**v2 reference**: §3 TIER 1 bullet list + §5 WAVE 3 batch sizing + §11.

---

## Minor amendments (folded same commit)

### m-a — risk register expansion

Added 3 rows to v2 §7:
- 3-PR auto-reviewer fatigue / cross-PR contradiction (MEDIUM)
- Multi-parallel-session collision on new worktree (MEDIUM)
- Worker self-reviews its own edits (MEDIUM)

### m-b — critic brief length cap

Every WAVE critic dispatch in v2 §5 WAVES 1/2/3 explicitly notes "brief ≤30 lines" per memory `feedback_long_opus_revision_briefs_timeout` (3/4 opus revision dispatches timed out at 40+ lines on 2026-05-15).

### m-c — critic ≠ executor

v2 §5 WAVE 1 + §8 explicitly forbid the editor subagent acting as its own critic. Every critic dispatch = fresh subagent ID.

### m-d — workspace_map.md triangle anchor

v2 §3 TIER 1 footnote cites `workspace_map.md` extant at repo root (FCI4-verified). Triangle: `AGENTS.md` ↔ `docs/operations/AGENTS.md` ↔ `workspace_map.md`.

---

## Verification record (orchestrator pre-commit, 2026-05-16)

All 4 mandatory verifications PASS before v2 commit:

1. `wc -l` on all TIER 0A/0B/0C paths → actuals recorded in §3
2. `python -m maintenance_worker.cli.entry --help` → surface `{run,dry-run,status,init}` confirmed; A1 fix uses `dry-run`
3. `PYTHONPATH=. python -m scripts.topology_doctor --help` → exits 0, prints full flag list; A2 fix verified
4. `git ls-files '**/AGENTS.md' '*AGENTS.md' | wc -l` = 46 → A5 reconciliation = 40 in-scope confirmed

Additional FCI4 anchors:
- `ls src/contracts/reality_contracts_loader.py` → exists → A3 promotion justified
- `ls workspace_map.md` → exists → m-d triangle anchor verified

---

## Sign-off

- v1 critic: opus, REVISE verdict, 5 amendments + 4 minors.
- v2 planner: all 9 items folded; FCI4 re-run; PLAN.md v2 written same-commit as this critic record.
- v2 critic dispatch (deferred to orchestrator, post-WAVE-0 baseline-capture): fresh opus, brief ≤30 lines, delta-review only.
