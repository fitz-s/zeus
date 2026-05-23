# Critic Review — Implementation Code (PR #119)

Reviewer: critic (opus, fresh-context per task brief).
Scope: implementation code only; spec-level items pre-disposed in CRITIC_REVIEW_DISPOSITION.md.
Branch: deploy/live-order-e2e-verification-2026-05-15 → main.
Files in PR: 257 / +50351 / -281 / 100 commits.

---

## 0. PR Snapshot

PR #119 lands the implementation packets P1–P9 of the runtime improvement
engineering package: topology v_next admission engine, maintenance worker core
+ guards + scheduler, Zeus binding overlay, lore index/promote/reverify, and
authority_inventory v2. Layered, mostly clean implementation work with
well-documented authority headers. One in-scope integration test fails on the
current tree, one safety-spec promise (dry-run floor enforcement) is wired in
the validator but never invoked from the engine/cmd_run path, and one
silent-skip window exists if install_metadata is absent at tick time. The
brief itself contains two false alarms verified during review (test count and
STATUS.md staleness) — flagging here to prevent re-litigation.

---

## 1. Coverage Map

### Tier 0 / Tier 1 surfaces — REVIEWED

| Surface | File(s) | Status |
|---|---|---|
| Admission engine | `scripts/topology_v_next/admission_engine.py` | reviewed |
| Hard safety kernel (topology layer) | `scripts/topology_v_next/hard_safety_kernel.py` | reviewed |
| Composition rules + §3.0 trap closure | `scripts/topology_v_next/composition_rules.py` | reviewed |
| Companion loop break | `scripts/topology_v_next/companion_loop_break.py` | reviewed |
| `_check_companion_required` | `scripts/topology_v_next/admission_engine.py:247-335` | reviewed |
| Severity overrides | `scripts/topology_v_next/severity_overrides.py` | reviewed (sampling) |
| CLI integration shim | `scripts/topology_v_next/cli_integration_shim.py` | reviewed (sampling) |
| Topology doctor dual-import shim | `scripts/topology_doctor.py:21-29` | reviewed + import-smoke verified |
| ActionValidator (5 SAFETY_CONTRACT guarantees) | `maintenance_worker/core/validator.py` | reviewed |
| ApplyPublisher wiring | `maintenance_worker/core/apply_publisher.py` + `cli/entry.py:165-230` | reviewed |
| Provenance (env-var, SIGKILL-safe) | `maintenance_worker/core/provenance.py` | reviewed |
| Refusal + Path A/B separation | `maintenance_worker/core/refusal.py`, `engine.py` | reviewed |
| Kill switch | `maintenance_worker/core/kill_switch.py` | sampled |
| Engine (run_tick state machine) | `maintenance_worker/core/engine.py` | reviewed |
| Evidence writer | `maintenance_worker/core/evidence_writer.py` | sampled |
| Install metadata + dry-run floor | `maintenance_worker/core/install_metadata.py` | reviewed |
| Subprocess guard (env/xargs/tee fix) | `maintenance_worker/core/subprocess_guard.py` | reviewed |
| Git operation guard (URL allowlist) | `maintenance_worker/core/git_operation_guard.py` | reviewed |
| GH operation guard | `maintenance_worker/core/gh_operation_guard.py` | sampled |
| Hard pre-tick guards (8) | `maintenance_worker/core/guards.py` `evaluate_all` | reviewed |
| Rules parser + task registry | `maintenance_worker/rules/*.py` | sampled |
| CLI entry + scheduler bindings | `maintenance_worker/cli/**` | reviewed |
| Zeus binding overlay | `bindings/zeus/{config.yaml,safety_overrides.yaml,launchd_plist.plist,install_metadata_template.json}` | reviewed |
| Lore index/promoter/reverify | `scripts/lore_indexer.py`, `lore_promoter.py`, `lore_reverify.py` | sampled (structure + subprocess safety) |
| Authority inventory v2 | `scripts/authority_inventory_v2.py` | not deep-read (722 lines; LOW residue) |
| pUSD allowance fix (adjacent) | `src/venue/polymarket_v2_adapter.py:490-559` | reviewed, fix intact |
| Test coverage | `tests/topology_v_next/**`, `tests/maintenance_worker/**`, `tests/test_v2_adapter.py` | enumerated + executed |

### Unreviewed but in PR

- `scripts/authority_inventory_v2.py` — read only enough to confirm structure;
  detailed read deferred (LOW: P9 already revised per dispositions).
- `divergence_logger.py`, `divergence_summary.py`, `companion_skip_logger.py` —
  imported successfully; not line-read.
- Hooks (`.claude/hooks/dispatch.py`, `.claude/hooks/registry.yaml`,
  `.codex/hooks/zeus-router.mjs`) — modified on disk but out of Tier-0 scope.

### Working-tree state (brief disclosure)

Brief said "working tree is clean except docs/operations/AGENTS.md (M) and
docs/operations/task_2026-05-15_autonomous_agent_runtime_audit/ (??)." Actual
state at review start: 17 files modified (incl. all reviewed implementation
files), 1 untracked. Review proceeded on the on-disk state since that is what
would be committed.

---

## 2. Findings

| # | Severity | File:Line | Description | Suggested Fix |
|---|---|---|---|---|
| F1 | **CRITICAL** | `maintenance_worker/core/engine.py:248` | `_validate_config` uses `str(p) == ""` to detect empty paths, but `str(Path("")) == "."`, so a `repo_root=Path("")` config is silently accepted. The in-scope integration test `test_full_tick_invalid_config_exits_nonzero` (added by this PR in commit `f9c704e97a`) catches the bug — and currently **fails on the tip of the branch** (1 fail / 1301 pass in `tests/topology_v_next/` + `tests/maintenance_worker/` + `tests/test_v2_adapter.py`). The PR ships a failing test it authored, in the safety surface it is supposed to harden. | Replace `str(p) == ""` with `str(p) in ("", ".")` OR check `p == Path("")`. Then re-run the failing test. Do not merge until green. |
| F2 | **MAJOR** | `maintenance_worker/core/engine.py` and `maintenance_worker/cli/entry.py` (absence) | Spec disposition M4 promised structural enforcement of the 30-day dry-run floor via `enforce_dry_run_floor` / `validate_action_with_floor`. Both functions exist in `validator.py:585`/`install_metadata.py:164` and are unit-tested, but **neither is imported or called from `engine.py`, `_apply_decisions`, or `cmd_run`**. Today this is latent because `_apply_decisions` is a stub that always returns `dry_run_only=True` (lines 298-302) — but the moment P5.3/P5.4 wires real ack-driven apply logic, live mutations will bypass the floor unless the wiring lands in the same commit. SCAFFOLD M4 disposition would be **regressed at implementation**. | Either (a) wire `validate_action_with_floor` into `_apply_decisions` now so the gate is structural rather than honor-system on next packet, or (b) add an explicit `TODO(P5.next): wire floor` near line 295 plus a test that fails when ack-state is added without floor. Option (a) is preferred. |
| F3 | **MAJOR** | `maintenance_worker/cli/entry.py:192-204` | If `install_metadata.json` is absent at tick time, the `if meta_path.exists()` branch is skipped silently. Meanwhile `engine.run_tick()` has already executed `APPLY_DECISIONS` + `POST_DETECT`. In the (currently stub) future where those stages mutate disk, you get on-disk mutations with no provenance commit, no PR, and no remote-URL allowlist check. Mitigated by: (1) `_apply_decisions` is currently a stub that always returns `dry_run_only=True`, so no real mutations happen yet; (2) the next tick's `check_dirty_repo` guard would refuse to proceed, surfacing the orphaned state. Blast radius today: near-zero. Once real apply lands: one tick of uncommitted state, detected at next tick start. | Reorder: read install_metadata BEFORE `engine.run_tick()`; if absent, `refuse_fatal(RefusalReason.CONFIG_INVALID, ...)` before any apply-stage runs. The defensive comment ("written on first tick; may not exist yet") is incorrect — the install script writes it, not the first tick. |
| F4 | **MINOR** | `scripts/topology_v_next/companion_skip_logger.py:148` | `datetime.datetime.utcnow()` is deprecated in Python 3.13+ (`DeprecationWarning` raised in test run). | Replace with `datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")`. |
| F5 | **NIT** | Brief vs reality (informational) | Brief claimed "1357 tests"; actual collection: 1300 tests in `tests/topology_v_next/ + tests/maintenance_worker/ + tests/test_v2_adapter.py`. Off by ~4%. Not a code defect, but the PR description / brief should match what the collector reports. | Update copy to "1300 tests" (or recount with whatever inclusive scope was intended). |

### Probe results (per task brief)

| # | Probe | Verdict |
|---|---|---|
| 1 | ApplyPublisher wired into cmd_run? | **PASS** — `entry.py:205-213` instantiates and invokes `publish()` for every non-dry-run apply_result. |
| 2 | 8 hard guards registered in `evaluate_all` + Path A/B separation? | **PASS** — `guards.py:319-344` lists all 8 (kill_switch, self_quarantined, dirty_repo, active_rebase, disk_free, inflight_pr, pause_flag, oncall_quiet); Path A enforced in `validator.py:27-30` + `refusal.py:96-97`; Path B in `engine.py:215-218`. (Note: subprocess/git/gh "guards" the brief grouped here are separate `*_EXEC` validators, not in `evaluate_all` — by design.) |
| 3 | §3.0 companion_required pre-registration fires BEFORE composition_conflict? | **PASS** — `composition_rules.py:103` calls `_preregister_companion_paths` at line 103, BEFORE `touched_profiles` computed at line 105. Replacement at `_preregister_companion_paths:253` collapses touched_profiles to 1, closing the trap. |
| 4 | Hard safety kernel — 14 forbidden patterns + realpath + READ-on-forbidden? | **SPLIT** — Two distinct kernels exist: (a) topology layer `scripts/topology_v_next/hard_safety_kernel.py` enforces `coverage_map.hard_stop_paths` (no realpath; no need: input is git-relative paths). (b) Maintenance-worker SAFETY_CONTRACT validator `maintenance_worker/core/validator.py:117-190` enforces 6 forbidden groups with `_canonicalize` realpath (line 216-223), READ→FORBIDDEN_PATH (line 8-10), private-key byte check (line 276-290), source-extension check (line 226-245). All 14 forbidden patterns covered when enumerated across groups 1–6. |
| 5 | subprocess_guard blocks env/xargs/tee per commit `59eca382e7`? | **PASS** — `_BLOCKED_COMMANDS` (lines 42-60) covers rm/chmod/chown/curl/wget/etc.; xargs and tee are absent from BOTH _BLOCKED_COMMANDS and _ALLOWED_COMMANDS so they hit the default-deny branch at line 382; env recurses on inner command via `_check_env_argv:253-296`; sed -i blocked via `_is_sed_mutating:297-307`. |
| 6 | URL allowlist for git remote per commit `99e057f793`? | **PASS** — `git_operation_guard.py:225-234`: push without install_meta+remote_url is fail-closed FORBIDDEN; mismatch is FORBIDDEN; allowlist check runs alongside force-push and protected-branch checks. |
| 7 | Provenance per-commit env-var override + SIGKILL-safe wrap_file_operation? | **PASS (with naming caveat)** — `provenance.py:69-109` uses env-var context manager (no persistent git config mutation, restores prior values on exit). The brief named `wrap_file_operation` but the actual API is `wrap_file_with_header` (pure function, no FS touch, trivially SIGKILL-safe). |
| 8 | Topology doctor dual-import works both pytest and direct invocation? | **PASS** — `scripts/topology_doctor.py:21-24` tries `topology_v_next.cli_integration_shim` first, falls back to `scripts.topology_v_next.cli_integration_shim`. Verified by direct `python -c "from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare"` and `python -c "import sys; sys.path.insert(0, 'scripts'); from topology_v_next.cli_integration_shim import maybe_shadow_compare"` — both OK. |
| 9 | 1357 tests claim plausible? | **PARTIAL** — Actual collection: 1300 tests in scoped trees. PR ran the test suite (`python -m pytest tests/topology_v_next/ tests/maintenance_worker/ tests/test_v2_adapter.py`): **1 failure / 1301 passes** — the F1 finding above. |
| 10 | STATUS.md currency? | **PASS (brief was wrong)** — STATUS.md correctly shows P3/P5/P9 as LANDED with sub-task breakdown. The text "P5.0 SCAFFOLD design running ~1.5hr" cited in brief does not appear in the file. Brief itself was stale. |

---

## 3. Verdict

**REVISE — FIX_REQUIRED**

Rationale: A safety-system PR cannot ship with a failing test it authored in
the safety surface itself (F1). The bug is one line, the test that exposes it
is in-tree, and resolution is mechanical. F2/F3 are MAJOR concerns about the
distance between the SCAFFOLD safety promises and the wired implementation,
and they want addressing before subsequent packets land real mutation logic
(at which point the latent gaps become live).

What would upgrade the verdict to ACCEPT_WITH_FOLLOWUP:
1. F1: `engine.py:248` fixed, test green. **Blocker.**
2. F2 OR F3: address one; the other can be deferred with a tracked TODO and a
   test that fails when the next packet wires real apply logic without the
   gate.
3. F5: trivial copy fix.
4. F4: low priority; can be a separate PR.

Verdict justification — Realist Check applied:
- F1 stayed CRITICAL because a failing test in the safety surface is below
  the bar this PR explicitly sets for itself. The advisor agreed; downgrading
  it would be reviewer-side hunting-mode bias *in the other direction* (going
  easy on a structural defect because the day-1 blast radius is small).
- F3 was downgraded from CRITICAL to MAJOR because `_apply_decisions` is
  currently a stub returning `dry_run_only=True` (no real mutations happen),
  and the next tick's `check_dirty_repo` guard would catch any orphaned state
  within 24h.
- Mode escalation: thorough mode throughout; the failing test + missing floor
  wiring + silent install_metadata skip do constitute a pattern (gaps in
  exactly the "wired safety vs unit-tested-but-not-invoked" boundary), but
  the surface area is bounded enough that going adversarial-mode would not
  surface additional Tier-0 issues. Held at THOROUGH.

---

## 4. Carry-Forward / DEFERRED dispositions

| ID | Item | Disposition | Reason |
|---|---|---|---|
| CF-1 | `scripts/authority_inventory_v2.py` (722 LOC) not deep-read | DEFERRED to next critic pass | P9 already revised per spec disposition; structural skim only |
| CF-2 | `divergence_logger.py`, `divergence_summary.py`, `companion_skip_logger.py` not line-read | DEFERRED to next critic pass | imports OK; tests pass; lower Tier than admission engine itself |
| CF-3 | Hooks (`.claude/hooks/**`, `.codex/hooks/**`) modified on disk | DEFERRED to hooks-specific review | out of Tier-0 scope per brief |
| CF-4 | `F4` (utcnow deprecation) | DEFERRED to standalone Python-3.13-readiness PR | non-blocking |
| CF-5 | F2 vs F3 — whichever is not addressed in the revise pass | DEFERRED with mandatory TODO + failing-test commitment | acceptable to land one, defer one |

---

## 5. Open Questions (unscored)

- Q1 (LOW confidence): The §3.0 `_preregister_companion_paths` REPLACES (not
  adds) the profile assignment for companion paths that glob-matched another
  profile. This is intentional per SCAFFOLD §3.0, but worth confirming no
  Zeus binding actually has a profile that legitimately co-owns a path that
  is also a `companion_required` target for another profile. Did not find a
  failing case in fixtures; surfacing for awareness.
- Q2: ApplyPublisher uses a single `run_id` per tick across all tasks. If two
  tasks both fail to publish, the rollback `git reset --soft HEAD~1` is
  per-failure (line 192 of apply_publisher.py), but tasks share state via the
  HEAD pointer. Need to confirm: if Task A commits and pushes successfully,
  then Task B commits but push fails, does the rollback of Task B accidentally
  undo Task A's already-pushed commit? (Probably not because Task A's commit
  is at HEAD~1 after Task B's commit; `reset --soft HEAD~1` only rewinds Task
  B.) Did not write a stress test; carrying as Q for next review.

---

## 6. Ralplan summary row (not applicable)

This is implementation-code review, not a ralplan deliberation pass.

---

End of review.
