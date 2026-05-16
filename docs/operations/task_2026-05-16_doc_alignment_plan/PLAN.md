# Plan: Post-PR-#119 Major Authority Update + Repo Hygiene (大扫除) — v3 critic-amended

## Critic verdict on v2: REVISE (3 CRITICAL + 6 MAJOR)

Plan-critic (opus, fresh-context) flagged 3 CRITICAL issues with v2:
1. WAVE 1.5 invented a `rules/handlers/<task>.py` architecture that doesn't match `engine.py:268-337`'s signatures + `TaskRegistry.get_tasks_for_schedule()` (already exists at `maintenance_worker/rules/task_registry.py`).
2. Plan cited 6 scout artifacts; only 4 actually written to disk (`SCAFFOLD_GAPS_VERIFIED.md` + `WORKTREE_AUDIT.md` missing as files — their content IS in conversation BATCH_DONE returns, but plan steps that reference "per worktree audit" need the artifact captured).
3. WAVE 4.1 BLOCKING→ADVISORY hook reversal undoes deliberate commit `342bd73ff2` (2026-05-09) cost-curve-calibrated decision; operator memory `feedback_pr_300_loc_threshold_with_education` enforces BLOCKING.

Plus 6 MAJORs (INV-27 governance violation, bare-file stub rule missing, pytest baseline missing, test-count undershoot, opus-critic-stall risk in WAVE 6, missing risks). All amendments applied below.

---

## Context (unchanged from v2 — verified intact by critic)

PR #119 merged 2026-05-16 (257 files / +50K LOC / 120 commits). Six rounds of read-only Explore agents surfaced: 4 verified PR-#119 implementation gaps (9 stubbed handlers, missing Check #0, hardcoded validator paths, missing wave-family logic, missing registries/state dirs), 7 semantic drifts in `architecture/*.yaml`, ≥80% citation rot in nav docs, 11 hook ecosystem gaps, 46-entry archive backlog, 21 worktrees + 18 branches + orphan state.

User decisions:
1. Single bundled PR (大扫除).
2. Implement ALL missing handlers in this PR.
3. Use worktree (live processes on main).
4. Clean residual worktrees + branches.
5. Plan must pass opus critic — **NOW DONE; this v3 incorporates verdict**.

---

## WAVE 0 — Safety + repo hygiene + scout artifact persistence (~2 hr)

**Goal**: clean operating surface; capture missing scout artifacts; pre-flight verification.

0.1. Create fresh worktree from `origin/main`: `git worktree add ~/.openclaw/workspace-venus/zeus-doc-alignment-2026-05-16 -b feat/doc-alignment-2026-05-16 origin/main`. All edits land here.

0.2. **Persist the 2 missing scout artifacts** (their content exists in conversation BATCH_DONE; write to disk now so plan execution can reference):
   - Write `docs/operations/task_2026-05-16_doc_alignment_plan/SCAFFOLD_GAPS_VERIFIED.md` from verification scout BATCH_DONE (4 CONFIRMED + 4 RECLASSIFIED gaps table).
   - Write `docs/operations/task_2026-05-16_doc_alignment_plan/WORKTREE_AUDIT.md` from worktree-audit scout BATCH_DONE (21 worktree triage table + 2 stash table + main-checkout uncommitted table).

0.3. **Commit orphan `CRITIC_REVIEW_IMPLEMENTATION.md`** to new branch at `docs/operations/task_2026-05-15_runtime_improvement_engineering_package/`.

0.4. **Triage stashes** (verify FIRST via `git stash list`):
   - stash@{0}: confirmed substantive hook improvements (dispatch.py PR-monitor semantics) — extract and apply in WAVE 4 only.
   - stash@{1}: if exists per worktree-audit, run `git stash show -p stash@{1}` and only apply if substantive AND not already committed elsewhere.
   - DROP from stash@{0}: any src/venue/polymarket_v2_adapter.py + tests/test_v2_adapter.py portions (would revert merged fix 75630214e1).

0.5. **Worktree cleanup** (executed in canonical checkout, per-path verification BEFORE remove per critic CRITICAL #2 amendment):
   - For each `.claude/worktrees/agent-*` worktree: (a) `git -C <path> status --short` — if any uncommitted, SKIP; (b) `git log -1 --format='%H' worktree-agent-<id>` — if HEAD ∈ main commits AND no uncommitted, safe `git worktree remove --force` + `git branch -D`; (c) if HEAD has unique commits OR uncommitted → SKIP + log to operator review.
   - PRESERVE always: canonical `/zeus`, `zeus-live-order-goal-2026-05-15` (other-session WIP), `zeus-live-order-e2e-verification-2026-05-15` (other-session WIP), `zeus-doc-alignment-2026-05-16` (this work), the deploy-branch worktree at `agent-a22208b47f876fb48` if confirmed shared-state with canonical deploy (per critic verification — currently sits on `deploy/live-order-e2e-verification-2026-05-15`).
   - Stale feature branches: `git log --oneline main..<branch>` — non-empty means orphan WIP → ASK USER before delete.

0.6. **HIGH-priority orphan investigation** (per ORPHAN_SCAN.md, with critic-required pre-delete verification):
   - 3 zero-byte DB files (`state/zeus_world.db` underscore-typo, `state/zeus-trades.db`, `state/zeus-risk.db`): `git log --all --diff-filter=A -- <path>` + `grep -rn "<basename>" src/ scripts/ maintenance_worker/ tests/`. If zero readers + zero-byte + no recent commit: safe `git rm`. Else ASK USER.
   - Stale lock `state/entry_forecast_promotion_evidence.json.lock`: verify no process holds it (`lsof <path>`); if dead, `rm`.

0.7. **pytest baseline capture** (critic MAJOR amendment): run `pytest tests/ -q --tb=no 2>&1 | tee state/maintenance_state/pytest_baseline_2026-05-16.txt` BEFORE any edits. Captures pre-existing flake/fail set. Git-track this file. WAVE 6.4 will compare delta-direction (per `feedback_critic_reproduces_regression_baseline`).

0.8. **Pre-flight green** (scoped tests): `pytest tests/maintenance_worker/ tests/topology_v_next/ tests/test_v2_adapter.py -q` → all green. `python -m scripts.topology_doctor --map-maintenance --advisory` → no NEW blockers.

**Gate**: worktrees ≥18 → ≤6 (with verified-safe removals only); orphan DBs disposed via verified pathway; pre-flight scoped tests green; pytest baseline file present.

---

## WAVE 1 — Fill verified PR-#119 implementation gaps (~3-4 hr, opus critic)

(Unchanged from v2 except 1.4 + 1.5 rewordings per critic.)

1.1. **`architecture/module_manifest.yaml`** — 3 entries for `maintenance_worker`, `topology_v_next`, `zeus_bindings`. Schema-match existing entries.

1.2. **CREATE `architecture/artifact_authority_status.yaml`** per UNIVERSAL_TOPOLOGY_DESIGN §13 + ZEUS_BINDING §8. Initial 10 rows (5 SCAFFOLD docs from PR #119 + 5 highest-blast nav docs). Schema: `path`, `status`, `last_confirmed`, `confirmation_ttl_days`, `owner`, `archival_ok`.

1.3. **`architecture/docs_registry.yaml`** — add 5 SCAFFOLD doc registrations (dup-check per P9.0 critic).

1.4. **NEW IMPLEMENTATION — Check #0 in `maintenance_worker/core/archival_check_0.py`** (new module — keeps archival concerns separate from validator.py per existing module boundaries):
   - Function `check_authority_status(path: Path, registry: dict) -> CheckResult` returns LOAD_BEARING / ARCHIVABLE / WARN_REGISTRY_ABSENT.
   - Registry loader caches the YAML per-process.
   - Called from engine's archival rule module (added in WAVE 1.5).
   - **Regression test**: registry hit forces LOAD_BEARING; archival_ok overrides; absent → WARN log.

1.5. **NEW REFACTOR — `maintenance_worker/core/validator.py` load forbidden paths from bindings** (per critic FAIL confirmation — current `_FORBIDDEN_RULES` hardcoded at lines 117-190):
   - Replace hardcoded list with loader reading `bindings/zeus/safety_overrides.yaml` (109 lines, already has real rules) UNIONED with a new `bindings/universal/safety_defaults.yaml` (CREATE — split universal-vs-Zeus).
   - Loader cache-per-process; missing file FAIL-CLOSED.
   - **Regression test**: bindings edit changes validator behavior; missing universal-defaults raises clear error.

1.6. **NEW IMPLEMENTATION — wave-family logic at `maintenance_worker/rules/wave_family.py`** per ARCHIVAL_RULES §"Special Case: Wave Packets":
   - Regex match `task_*_wave[0-9]+` → family slug
   - ATOMIC GROUP: family exempted if ANY member fails any exemption check
   - **Regression test**: 3-packet wave family, mixed verdicts → all 3 stay.

1.7. **Run install script**: `python -m maintenance_worker.cli.entry install --binding bindings/zeus/config.yaml --commit-now`. Creates `state/maintenance_state/install_metadata.json` with `first_run_at = 2026-05-16T<ts>` — day 1 of 30-day floor.

1.8. **Create `state/topology_v_next_shadow/.gitkeep`** so divergence_logger has target dir.

1.9. **OPUS CRITIC** on WAVE 1 deliverables. Probes per critic-suggested floor.

**Gate**: critic CLEAR_PASS/ACCEPT_WITH_FOLLOWUP; tests green; install_metadata.json + topology_v_next_shadow/ exist; archival_check_0 + safety_overrides loader + wave_family pass unit tests.

---

## WAVE 1.5 — Wire 9 stubbed task handlers via existing TaskRegistry (~4-6 hr, REWRITTEN per critic CRITICAL #1)

**Architectural anchor** (per critic verification of `engine.py:268-277` + `rules/task_registry.py`):

```
engine.run_tick(ctx)
  → ctx.proposals = _enumerate_candidates(config)   # STUB: returns []
                       ↓ FIX (1.5.1): wire to TaskRegistry
                    TaskRegistry.from_catalog(config.task_catalog_path)
                      .get_tasks_for_schedule(schedule_key)   # EXISTS
                      → list[TaskSpec]
  for task in proposals:
    candidates = dispatch_by_task_id(task.task_id, "enumerate", task, ctx)
    proposal_manifest = _emit_dry_run_proposal(task, candidates)
    if ApplyMode.real:
      result = _apply_decisions(task, proposal_manifest, force_dry_run, install_meta)
                       ↓ FIX (1.5.2): wire to dispatcher
                    dispatch_by_task_id(task.task_id, "apply", decision, ctx)
                      returns ApplyResult
```

Per-task-id rule modules at `maintenance_worker/rules/<task_id>.py` (NOT `rules/handlers/` per v2 — critic confirmed naming mismatch). Each module exports `enumerate(spec, ctx) -> list[Candidate]` and `apply(decision, ctx) -> ApplyResult`. Engine dispatches via task_id lookup table built from TaskRegistry's loaded entries.

1.5.1. **Wire `_enumerate_candidates`**: replace stub (engine.py:277 `return []`) with `TaskRegistry.from_catalog(config.task_catalog_path).get_tasks_for_schedule(schedule_from(ctx))`. TaskRegistry exists; this is wiring only.

1.5.2. **Add `_dispatch_by_task_id(task_id: str, method: str, *args) -> Any`** in engine.py. Lookup table built lazily from `maintenance_worker.rules` package imports. Method ∈ {"enumerate", "apply"}.

1.5.3. **Refactor `_apply_decisions`** (engine.py:292-337): after the F2 dry-run-floor gate (unchanged), dispatch to per-task-id rule via `_dispatch_by_task_id(task.task_id, "apply", proposal, ctx)`.

1.5.4. **9 rule modules** at `maintenance_worker/rules/<task_id>.py` (task_ids enumerated from TASK_CATALOG.yaml at execution time):
   - Each module: `enumerate(spec, ctx)` returns `list[Candidate]`; `apply(decision, ctx)` returns `ApplyResult` with **top-of-function guard**: `if ctx.dry_run_only: return ApplyResult(task_id=spec.task_id, dry_run_only=True, diff=mock_diff(decision))` (per critic MAJOR fix — defense-in-depth beyond engine-level enforcement).
   - Critic-flagged risk: handler reuse outside engine. Top-guard ensures even outside-engine call is safe.

1.5.5. **Per-handler tests** at `tests/maintenance_worker/test_rules/test_<task_id>.py`: per handler, ≥4 tests (happy / refusal / dry_run / cascade-isolation). Total ≥36 tests. Critic-corrected test LOC estimate: 1500-2000 LOC (not 800-1200).

1.5.6. **Total LOC estimate (rewritten)**: 9 modules × ~120 LOC + dispatcher wiring ~80 LOC = ~1160 LOC code. Tests +1500-2000 LOC. **Total ~2700-3200 LOC for this wave.** Down from v2's 2500-3200 because TaskRegistry already exists and dispatcher is small.

1.5.7. **OPUS CRITIC per batch of 3 modules** — 3 critic dispatches. Brief ≤30 lines each to avoid stall (per `feedback_long_opus_revision_briefs_timeout`). Brief = "review these 3 task rule modules + their tests against TASK_CATALOG.yaml spec".

**Gate**: 9 modules implemented; dispatcher wired; ≥36 tests green; per-batch critic CLEAR_PASS. May defer bottom 3 if budget exceeds session — top 6 (archival, quarantine, lore_proposal_emission, evidence rotation, kill_switch arm, dry_run_floor exempt) MUST land.

---

## WAVE 2 — Archive reconciliation (Option A migration, ~3 hr, critic-amended bare-file rule)

2.1. **One-off `scripts/archive_migration_2026-05-16.py`**: list 46 entries; compute target NEW path per quarter (date in name).

2.2. **For DIRECTORY entries (~23 of 46)**: `git mv docs/archives/packets/<entry>/ docs/operations/archive/<YYYY>-Q<N>/<entry>/`; create stub at `docs/operations/<entry>.archived` per ARCHIVAL_RULES.md:84-102 (12-line stub).

2.3. **For BARE-FILE entries (~23 of 46, per critic MAJOR)**: ARCHIVAL_RULES doesn't define stubs for bare files. **Apply same pattern with adjusted target**: `git mv docs/archives/packets/<entry>.md docs/operations/archive/<YYYY>-Q<N>/<entry>.md`; create stub at `docs/operations/<entry>.archived` with metadata pointing to the file (not dir). **Pre-check each bare-file entry for prior deletion**: e.g., `PROPOSALS_2026-05-04.md` per ORPHAN_SCAN was deleted in `eba80d2b9d` — exclude from migration; capture in migration log as "already-deleted, no migration needed".

2.4. **Create `docs/operations/archive/2026-Q2/INDEX.md`** listing actual migrated entries (exclude prior-deleted from count).

2.5. **Convert `docs/archive_registry.md`** to deprecated forwarding doc.

2.6. **Update `docs/operations/AGENTS.md`** archive section reference to ARCHIVAL_RULES.md.

2.7. **Fix ARCHIVAL_RULES.md line 79 cosmetic** ("eight" → "nine") per CRITIC_REVIEW_DISPOSITION Residue-1.

2.8. **SONNET CRITIC** on migration. Probes: 5 spot-checked stubs resolve; 5 active docs grepped for refs to OLD paths (now broken); INDEX row count = actual migration count; bare-file vs dir handling consistent.

**Gate**: actual migration count matches INDEX; no broken back-refs; critic CLEAR_PASS.

---

## WAVE 3 — Authority doc + semantic drift refresh (~4 hr, opus critic; INV-27 REMOVED per critic CRITICAL)

3.1. **`AGENTS.md`**: replace 5 confirmed-broken file:line refs with file:symbol-anchored refs. Spot more, fix. ~30-50 fixes total.

3.2. **`REVIEW.md`**: Tier 0 additions (maintenance_worker/core/{validator,apply_publisher}.py, scripts/topology_v_next/{admission_engine,hard_safety_kernel}.py, bindings/zeus/safety_overrides.yaml).

3.3. **`docs/operations/INDEX.md`**: regenerate or hand-add 15+ missing task packets.

3.4. **`docs/operations/current_state.md / current_data_state.md / current_source_validity.md`**: refresh.

3.5. **`.claude/CLAUDE.md`** (16 lines): pointer verify.

3.6. **Generate `docs/lore/INDEX.json`** via `python -m scripts.lore_indexer --output docs/lore/INDEX.json`.

3.7. **Fix 6 semantic drifts in `architecture/*.yaml`** (INV-27 REMOVED per critic CRITICAL #4 — governance violation; re-scope as separate governance packet at WAVE 7):
   - `topology.yaml`: doc-comment "Root registry only → now active nav authority"
   - `core_claims.yaml`: claim_status: replaced → spell out
   - `data_rebuild_topology.yaml`: `ensemble_snapshots` → `_v2`
   - `module_manifest.yaml`: maturity skeletal → stable for modules with full tests
   - `fatal_misreads.yaml`: fix count metadata 8 → 9
   - Settlement description in AGENTS.md align with `harvester.py` + `internal_resolver_v1`

3.8. **NEW fatal_misread entry**: `artifact_authority_status_missing_gate` — agents must not assume MISSING == 0.

3.9. **OPUS CRITIC with probe contract** on AGENTS.md + REVIEW.md + drift fixes. Probes: 10 sampled new refs all verify; fatal_misread loads; no INV edits present (sanity check that 3.7 didn't sneak INV-27 back).

**Gate**: critic CLEAR_PASS; lore INDEX.json valid.

---

## WAVE 4 — Hooks ecosystem repair (~1.5 hr, sonnet critic; BLOCKING-reversal REMOVED per critic CRITICAL #3)

**v2's WAVE 4.1 (BLOCKING → ADVISORY for pr_create_loc_accumulation + pre_merge_comment_check) is DELETED**. Per critic CRITICAL #3: commit `342bd73ff2` (2026-05-09) deliberately elevated `pr_create_loc_accumulation` to BLOCKING with cost-curve-calibrated rationale; operator memory `feedback_pr_300_loc_threshold_with_education` enforces it. The HOOKS_AUDIT observation is descriptive (notes tier mismatch with v2 protocol), NOT prescriptive (does not recommend reversal). Carry as ledger entry: "HOOKS_AUDIT row 'pr_create_loc_accumulation BLOCKING' = INTENTIONAL drift; v2 protocol amendment is a separate governance packet."

4.1. **Wire 4 unwired hooks** in `.claude/settings.json` per HOOKS_AUDIT: `pr_thread_reply_waste`, 2 worktree advisors, + 1 other (specific names from HOOKS_AUDIT.md). 

4.2. **Mirror 6 Zeus hooks → `.codex/hooks/zeus-router.mjs`** per PR #77 mirror contract.

4.3. **Fix "80 LOC" → "300 LOC"** in `.claude/settings.json` description (~5 min, text-only).

4.4. **Apply hook improvements from stash@{0}**: `dispatch.py` PR-monitor semantics (reviewer-appearance-not-completion, PR-state-check, thread-aware fetch instructions).

4.5. **NEW: maintenance_worker_dry_run_floor advisory hook**: warns when worker about to apply real action with floor not met or task non-exempt. ADVISORY-only per v2.

4.6. **SONNET CRITIC** on hook changes: boot self-test passes; settings.json valid; Codex mirror covers all advisory.

**Gate**: hook boot self-test passes; tier consistency clean (no surprise BLOCKING reversals); Codex mirror complete.

---

## WAVE 5 — Dogfood maintenance_worker first real dry-run (~1.5 hr supervised, day 1 of 30-day mandate)

5.1. `python -m maintenance_worker.cli.entry run --dry-run --binding bindings/zeus/config.yaml`. First non-stub real run with handlers + Check #0 + safety_overrides loader functional.

5.2. **Hand-review evidence trail** at `state/maintenance_state/evidence_trail/2026-05-16/`. Critic-required cascade isolation: per-handler evidence sub-dir; one handler crash should NOT pollute peer evidence.

5.3. **Failure modes captured as fixtures**: any unexpected verdict adds a test in `tests/maintenance_worker/`.

**Gate**: dry-run no crash; evidence trail readable in <5 min per DRY_RUN_PROTOCOL; SUMMARY.md generated; per-handler isolation verified.

---

## WAVE 6 — Post-validation + final critic (~2 hr, multi-sonnet critic per critic MAJOR fix)

6.1. `python -m scripts.lore_reverify --strict --timeout 60`.

6.2. `python -m scripts.topology_doctor --map-maintenance --advisory` + `--task-boot-profiles`.

6.3. **Enable `--v-next-shadow`** flag in production cron/launchd invocations. CAREFUL — runtime daemons. Edit `~/Library/LaunchAgents/com.zeus.*.plist`.

6.4. **Full test suite vs baseline** (per critic MAJOR fix on missing baseline): `pytest tests/ -q --tb=short 2>&1 | tee state/maintenance_state/pytest_post_2026-05-16.txt` and `diff pytest_baseline_2026-05-16.txt pytest_post_2026-05-16.txt` → delta-direction is the signal (per `feedback_critic_reproduces_regression_baseline`). Expect non-zero flake; new-fail set must be EMPTY.

6.5. **3 SONNET critics in parallel** (per critic MAJOR fix on opus stall risk):
   - Sonnet critic A: handlers (WAVE 1.5) — scope ~2700 LOC + tests
   - Sonnet critic B: migration (WAVE 2) — scope ~46 file moves + stubs
   - Sonnet critic C: docs+hooks (WAVE 3 + 4) — scope nav docs + semantic drifts + hooks
   - Each brief ≤30 lines, scope-bounded.
   - Aggregate verdict: ALL three CLEAR_PASS/ACCEPT_WITH_FOLLOWUP → proceed to PR-open. Any FIX_REQUIRED → remediate before PR-open.

**Gate**: lore_reverify exit 0; topology_doctor no NEW BLOCKING; pytest delta-direction acceptable (zero NEW fails); 3 sonnet critics non-FIX_REQUIRED.

---

## WAVE 7 — Documented deferrals (NOT executed in this PR)

- **INV-27 wording revision** → separate governance packet citing actual code-blocking guard with file:line, operator decision required (per critic CRITICAL #4 carve-out).
- **Hook v2 protocol amendment** to recognize PR-discipline BLOCKING carve-outs → separate governance packet amending `task_2026-05-07_hook_redesign_v2/PLAN.md` (per critic CRITICAL #3 carve-out).
- F4 utcnow deprecation → Python 3.13 PR.
- Mass archive sweep beyond 46-entry migration → blocked on first 60d aging (~2026-07-03).
- P10 topology_doctor consolidation → blocked on P4 cutover.
- P4 cutover packet → opens ~2026-05-31 after 14-day shadow window.
- Bottom 3 of 9 maintenance_worker handlers if scope exceeded → P5.next packet.
- Mass file:line → file::symbol citation refactor beyond AGENTS.md/REVIEW.md → follow-up.
- Other-session WIP on canonical deploy (chain_reconciliation + tests) → owned by live-order-e2e session.
- 200MB log rotation (zeus-ingest.err + zeus-live.err) → operations follow-up.
- `.omc/state/agent-replay-*.jsonl > 7d` cleanup → routine.

---

## Verification Strategy (critic-amended)

| Wave | Gate |
|---|---|
| 0 | scout artifacts written; worktrees ≥18→≤6 (per-path verified); orphan DBs disposed (verified-not-just-deleted); pytest baseline file present; pre-flight scoped tests green |
| 1 | opus critic CLEAR_PASS; install_metadata + topology_v_next_shadow/ dirs created; archival_check_0 + safety_overrides loader + wave_family tests green |
| 1.5 | 9 (or 6+3-deferred) rule modules implemented; dispatcher wired; ≥36 tests green; per-batch critic CLEAR_PASS |
| 2 | bare-file rule applied to ~23 entries; actual migration count matches INDEX; no broken back-refs; critic CLEAR_PASS |
| 3 | opus critic CLEAR_PASS; lore INDEX.json valid; INV-27 NOT touched |
| 4 | sonnet critic CLEAR_PASS; hook boot self-test passes; BLOCKING tier intact (no surprise reversals); Codex mirror covers all advisory |
| 5 | dry-run no crash; evidence trail readable + per-handler isolated; SUMMARY.md generated |
| 6 | lore_reverify exit 0; topology_doctor no NEW BLOCKING; pytest delta vs baseline acceptable; 3 parallel sonnet critics non-FIX_REQUIRED |

## Files Critical to This Plan (critic-amended)

**Created (~14 files)**:
- `architecture/artifact_authority_status.yaml`
- `bindings/universal/safety_defaults.yaml`
- `maintenance_worker/core/archival_check_0.py` (NEW per critic-suggested separation from validator.py)
- `maintenance_worker/rules/<task_id>.py` × 9 (NOT `rules/handlers/` per critic)
- `maintenance_worker/rules/wave_family.py`
- `docs/operations/archive/2026-Q2/INDEX.md`
- `docs/operations/<entry>.archived` × actual-migration-count (likely 44-45 after excluding prior-deleted)
- `docs/operations/task_2026-05-16_doc_alignment_plan/{PLAN.md,STATUS.md,POSTMORTEM.md,SCAFFOLD_GAPS_VERIFIED.md,WORKTREE_AUDIT.md,PLAN_CRITIC.md}` (last 3 NEW per critic amendments)
- `state/maintenance_state/install_metadata.json` (via install script)
- `state/maintenance_state/pytest_baseline_2026-05-16.txt`
- `state/topology_v_next_shadow/.gitkeep`
- `docs/lore/INDEX.json` (via lore_indexer)
- `tests/maintenance_worker/test_rules/test_<task_id>.py` × 9+
- `tests/maintenance_worker/test_archival_check_0.py` + `test_safety_overrides_loader.py` + `test_wave_family.py`

**Modified (~22 files)** — unchanged from v2 except: NO architecture/invariants.yaml edit (INV-27 removed); NO `.claude/hooks/registry.yaml` BLOCKING-tier flip (lines 64-102 untouched).

**Existing tools reused**:
- `maintenance_worker.rules.task_registry.TaskRegistry` (NOT re-invented per critic)
- `scripts/lore_indexer.py`, `lore_reverify.py`, `authority_inventory_v2.py`, `topology_doctor.py`
- `git mv` (history-preserving)

## Risk register (critic-amended)

| Risk | Mitigation |
|---|---|
| WAVE 1.5 architecture confusion | RESOLVED v2→v3 per critic: wires to existing TaskRegistry not invents new pattern |
| Maintenance_worker first dry-run crashes | WAVE 5 supervised; failures captured as fixtures; per-handler evidence isolation |
| Archive migration breaks live refs | git mv preserves; stubs prevent 404; bare-file rule covers ~23 entries; sonnet critic spot-checks |
| Hooks rewiring breaks settings.json or boot self-test | Sonnet critic includes boot self-test verification; BLOCKING tier NOT touched per critic |
| Other-session WIP gets clobbered | WAVE 0 explicit no-touch; fresh worktree from origin/main; per-path verification before worktree-remove |
| Removing agent-* worktrees deletes data | WAVE 0.5 per-path uncommitted verification before remove; skip-if-unique-commits |
| Single bundled PR exceeds 4500 LOC soft ceiling | Coherent unit per 大扫除 framing; estimate ~3500-5500 LOC; user-authorized scope |
| Opus revision dispatches stall | WAVE 1.5 per-batch critics use ≤30 line briefs; WAVE 6 final critic decomposed to 3 parallel sonnet critics per critic MAJOR fix |
| Critic-of-critic recursion error (~50% per memory) | Each critic finding spot-verified before remediation; per-batch sonnet critics in WAVE 6 cross-check |
| pytest flake masking real regressions | WAVE 0.7 baseline captured; WAVE 6.4 compares delta-direction (new-fail set must be empty); known-flaky tolerated |
| First-tick cascade across handlers | WAVE 5.2 per-handler isolated evidence sub-dir; cascade-failure test in WAVE 1.5.5 |
| INV-27 governance violation | RESOLVED per critic CRITICAL: removed from this PR; carve-out to WAVE 7 governance packet |
| Hook BLOCKING-tier reversal undoing deliberate operator decision | RESOLVED per critic CRITICAL: removed from this PR; carve-out to WAVE 7 governance packet |

## Estimated total effort (critic-amended)

- WAVE 0: 2 hr (+30 min for scout-artifact persistence + baseline capture)
- WAVE 1: 3-4 hr
- WAVE 1.5: 4-6 hr (down from 6-10 — TaskRegistry exists)
- WAVE 2: 3 hr (+bare-file rule overhead)
- WAVE 3: 4 hr (-INV-27 row)
- WAVE 4: 1.5 hr (-BLOCKING reversal)
- WAVE 5: 1.5 hr
- WAVE 6: 2 hr (+baseline diff + 3-parallel-sonnet critics)
- **Total**: 21-26 hr (slight reduction from v2's 22-27)
- Critic dispatches: 1 opus (WAVE 1) + 3 opus per-batch (WAVE 1.5) + 1 opus (WAVE 3) + 3 sonnet parallel (WAVE 6) + 1 sonnet (WAVE 2) + 1 sonnet (WAVE 4) = 5 opus + 5 sonnet. Brief-length discipline per critic memory.
- Estimated LOC: 3500-5000 (slightly down from v2 due to TaskRegistry reuse)
