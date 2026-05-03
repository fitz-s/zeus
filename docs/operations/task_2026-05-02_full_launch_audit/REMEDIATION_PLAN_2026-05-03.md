# PR46+PR47 Remediation Plan — 2026-05-03

## Authorship + scope
- Author: claude (post-handoff session, 2026-05-03 PM CDT)
- Branch: `healthcheck-riskguard-live-label-2026-05-02`
- Live HEAD: `11bbc8b2` (origin == local; PR46 remote already contains `cb4beb6c` rollout flip)
- Operator authority preserved:
  - **解除 block 是 operator 的决策** — `rollout_mode=live` stays; this plan does NOT revert `cb4beb6c`
  - **本 PR 内继续深度计划修剩余内容** — Phases A and B land here
  - **不让新 daemon 接入代码** — daemon hot-path files (`src/engine/evaluator.py`, `src/main.py`, `src/ingest_main.py`, `src/execution/cycle_runner.py`, `src/state/db.py` runtime callers, `scripts/healthcheck.py` `healthy` predicate) get **zero new call sites** in this PR

## Why a phased plan
Three independent reviewers (code-reviewer / critic-opus / scientist) converged on the same structural verdict: the `evaluate_entry_forecast_rollout_gate` / `evaluate_calibration_transfer_policy` / `evaluate_entry_forecast_shadow` machinery is **scaffolding tested in `tests/` but not invoked from daemon code**. The system is currently **fail-closed by accident** because `get_entry_readiness()` in `executable_forecast_reader.py:380` queries a `readiness_state` row with `strategy_key='entry_forecast'` that no daemon path writes (only `strategy_key='producer_readiness'` is written, by `producer_readiness.py:97`/`:137`).

Today's safety relies on this accidental block. Any future commit that wires an entry-forecast readiness writer without first wiring the rollout gate + calibration gate flips the system to structural fail-OPEN with no audit trail. The deep plan must close the structural debt **before** the writer is wired, and must do so without changing daemon behavior in this PR.

---

## Phase A — Docs, tests, registries (zero daemon-runtime impact)

Goal: surface the truth, retire stale antibodies, and stop architecture-mesh decay. No `src/` runtime files are touched in Phase A.

| ID | File | Action | Risk |
|---|---|---|---|
| A1 | `tests/test_entry_forecast_config.py:42-58` | Rename `test_entry_forecast_config_loads_blocked_default` → `test_entry_forecast_config_strict_load_with_blocked_override`; replace bare `entry_forecast_config()` with `replace(entry_forecast_config(), rollout_mode=BLOCKED)` for the BLOCKED branch; assert each enum/string field independently of `rollout_mode` | Pure test edit |
| A2 | `tests/test_entry_forecast_evaluator_cutover.py:77-99` | Add `monkeypatch` of `evaluator.entry_forecast_config` to a `replace(... rollout_mode=BLOCKED)` for the BLOCKED-stage canary; keep the LIVE counterpart already at lines 102-128 | Pure test edit |
| A3 | `tests/test_entry_forecast_rollout.py:40-49` | Construct `EntryForecastConfig(... rollout_mode=BLOCKED)` directly instead of relying on the on-disk default | Pure test edit |
| A4 | `tests/test_entry_forecast_shadow.py:202-217` | Pass `cfg = replace(entry_forecast_config(), rollout_mode=BLOCKED)` into `evaluate_entry_forecast_shadow(...)` | Pure test edit |
| A5 | `tests/test_live_entry_status.py:105-149` (2 tests) | Same `replace(cfg, rollout_mode=BLOCKED)` override for both BLOCKED-mode assertions | Pure test edit |
| A6 | `tests/test_entry_forecast_config.py` (new test) | Add `test_settings_json_rollout_mode_matches_plan_declaration` — reads `entry_forecast_config()`, reads a single-source-of-truth declaration in PLAN_v4 (or new `docs/operations/task_2026-05-02_live_entry_data_contract/CURRENT_ROLLOUT_MODE.md`), asserts they agree. This is the antibody replacing the BLOCKED-default canary that the live flip retired | Pure test add |
| A7 | `architecture/source_rationale.yaml` | Register 8 new modules: `src/data/calibration_transfer_policy.py`, `src/data/entry_forecast_shadow.py`, `src/data/executable_forecast_reader.py`, `src/data/forecast_fetch_plan.py`, `src/data/forecast_target_contract.py`, `src/data/live_entry_status.py`, `src/data/producer_readiness.py`, `src/control/entry_forecast_rollout.py`, `src/state/source_run_coverage_repo.py`. Each entry: zone, hazards, write routes, "NOT YET INVOKED FROM DAEMON" annotation for the four orphan modules | Registry-only |
| A8 | `architecture/test_topology.yaml` | Verify all 16 new tests are registered: `test_calibration_transfer_policy`, `test_ensemble_snapshots_v2_executable_schema`, `test_entry_forecast_config`, `test_entry_forecast_evaluator_cutover`, `test_entry_forecast_rollout`, `test_entry_forecast_shadow`, `test_executable_forecast_reader`, `test_forecast_fetch_plan`, `test_ingest_grib_source_run_context`, `test_live_entry_status`, `test_opendata_future_target_contract`, `test_opendata_release_calendar_selection`, `test_opendata_writes_v2_table` (extended), `test_producer_readiness_builder`, `test_release_calendar` (extended), `test_source_run_coverage_schema`. Add the missing entries | Registry-only |
| A9 | `docs/operations/task_2026-05-02_live_entry_data_contract/PHASE0_EVIDENCE_LOCK.md:143-153` | Replace rotted line-numbered citations (`src/data/ecmwf_open_data.py:110: def _default_cycle(now)` no longer exists; `src/ingest_main.py:484` rotted) with symbol-anchored citations. Add a closing "How to refresh this doc" section | Docs-only |
| A10 | `docs/operations/task_2026-05-02_full_launch_audit/REMAINING_TASKS.md` | Reflect post-cb4beb6c reality: rollout=live but daemon recurrently blocked at `ENTRY_READINESS_MISSING`; add the orphaned-gate findings; remove the "PR #46 OPEN" line (it merged-equivalent now that origin == local +24 commits) | Docs-only |
| A11 | New: `docs/operations/task_2026-05-02_live_entry_data_contract/PREMISE_ERRATUM_2026-05-03.md` | Document the cb4beb6c commit message premise mismatch (claimed "204 LIVE_ELIGIBLE / 51 HORIZON_OUT_OF_RANGE" vs DB-measured 408 / 102). Probable cause: 2x for high+low tracks; verify and document | Docs-only |
| A12 | New: `docs/operations/task_2026-05-02_live_entry_data_contract/CURRENT_ROLLOUT_MODE.md` | Single-source-of-truth file naming the current `rollout_mode` value, last-updated date, who flipped it, and operator-stated unblock evidence. Antibody A6 reads this | Docs-only |

**Phase A acceptance**:
- All 6 currently-red PR47 contract tests turn green via test edits (not via code changes)
- One new canary test (A6) keeps the "settings.json must match documented intent" antibody alive
- No `src/` runtime file is touched
- `architecture/test_topology.yaml` mesh advisory passes for changed files
- Commit trailer pattern: `[skip-invariant]` is acceptable because Phase A is docs/tests/registries — but each commit message names the gate that was satisfied (planning lock not triggered for these paths)

---

## Phase B — src/ structural completion, daemon-isolated (no new daemon imports)

Goal: complete the structural design that PR47 set up, so that the operator can later flip activation flags in a separate authorized PR. Every Phase-B module is **importable but not imported by any daemon hot-path file**.

| ID | File | Action | Risk |
|---|---|---|---|
| B1 | New: `src/control/entry_forecast_promotion_evidence_io.py` | Atomic write + read for `EntryForecastPromotionEvidence` (operator_approval_id, g1_evidence_id, calibration_promotion_approved, canary_success_evidence_id, timestamps). Uses `tmp + os.replace` pattern per OpenClaw convention. Storage: `state/entry_forecast_promotion_evidence.json`. NO daemon path imports this file | Module add, no daemon wire |
| B2 | New: `tests/test_entry_forecast_promotion_evidence_io.py` | Round-trip + corruption-handling + missing-file = `EVIDENCE_MISSING` tests | Test add |
| B3 | New: `src/data/entry_readiness_writer.py` | Pure function `write_entry_readiness(conn, scope, promotion_evidence, calibration_decision, ...) -> WriteResult`. Internally enforces: refuses to write `LIVE_ELIGIBLE` unless `promotion_evidence` valid AND `calibration_decision.live_promotion_approved` AND `evaluate_entry_forecast_rollout_gate(...)` returns no blocker. NO daemon path imports this file | Module add, no daemon wire |
| B4 | New: `tests/test_entry_readiness_writer.py` | Relationship tests for the chain `(producer_readiness, promotion_evidence, calibration_decision) → entry_readiness_writer → readiness_state row → get_entry_readiness`. This is the cross-module relationship test that AGENTS.md §3 describes | Test add |
| B5 | New: `tests/test_entry_forecast_chain_relationship.py` | End-to-end relationship test: write producer readiness → load promotion evidence → run shadow evaluator → run calibration gate → run rollout gate → call entry_readiness_writer → assert `read_executable_forecast` succeeds. **Uses an in-memory DB and the orphan modules directly; never touches the daemon.** | Test add |
| B6 | `src/data/entry_forecast_shadow.py:154-175` | Fix the BUG that `evaluate_entry_forecast_shadow` always returns `SHADOW_ONLY` even with `rollout_mode=LIVE` + approved calibration (per code-reviewer MED-4). Final return must honor live + calibration + rollout-gate clearance. The function still has zero daemon callers — bug fix is preparing it for Phase C activation | Logic fix in orphan module |
| B7 | `src/data/executable_forecast_reader.py:283-307` | Resolve dead-code branch (per code-reviewer MED-5): either drop the SQL `IS NOT NULL` filters and let the post-check decide, or drop the post-check. Recommend: drop SQL filters + keep post-check (lets future schema relax linkage requirement) | Code-path simplification, no behavior change at HEAD |
| B8 | `src/engine/evaluator.py:709-713` | Tighten `_live_entry_forecast_config_or_blocker` exception scope: catch `(KeyError, TypeError, ValueError)` only, not bare `Exception` (per code-reviewer MED-3). **Note**: this DOES touch a daemon hot-path file. Operator authorization required before B8 lands. If declined, document as Phase-C item | **Daemon file** — needs explicit operator OK |
| B9 | Decide on `allow_short_horizon_06_18` and `require_active_market_future_coverage` (dead knobs, code-reviewer HIGH-4) | Option 1 (clean): delete from `EntryForecastConfig` + `settings.json` + tests. Option 2 (paranoid belt): assert `cfg.allow_short_horizon_06_18 is False` at startup with a guarded log line. **Recommend Option 1** — dead knobs cause false safety perception | Config schema change |
| B10 | `src/control/entry_forecast_rollout.py`, `src/data/calibration_transfer_policy.py`, `src/data/entry_forecast_shadow.py`, `src/data/entry_readiness_writer.py` (B3) | Add docstring `"""DAEMON ACTIVATION: NOT YET WIRED. Phase-C work will register a single import site and a feature flag."""` to top of each orphan module. Future readers cannot accidentally assume runtime use | Pure docs |

**Phase B acceptance**:
- 16 PR47 contract test files pass without `replace(rollout_mode=BLOCKED)` workarounds (because the LIVE branch is now exercised by B5's relationship test)
- New tests in B2 / B4 / B5 increase coverage on the orphan chain
- `grep -rn "evaluate_entry_forecast_rollout_gate\|evaluate_calibration_transfer_policy\|write_entry_readiness" src/main.py src/ingest_main.py src/engine/ src/execution/` returns **zero hits** (orphan invariant preserved)
- Daemon healthcheck output before/after B is byte-equal in `entry_forecast_status` field
- Live cycle behavior unchanged (still recurrent `ENTRY_READINESS_MISSING` on entry_forecast read path)

**Phase B explicit non-goals**:
- Do NOT call `evaluate_entry_forecast_rollout_gate` from `evaluator.py`
- Do NOT call `write_entry_readiness` from `producer_readiness.py` or any daemon code path
- Do NOT change `scripts/healthcheck.py:330-340` `result["healthy"]` predicate
- Do NOT touch `src/main.py`, `src/ingest_main.py`, or `src/execution/cycle_runner.py` for new logic (only B8 if operator approves)

---

## Phase C — Operator-controlled activation (NOT in this PR)

Listed for handoff completeness; do **not** implement now.

| ID | Step | Authority required |
|---|---|---|
| C1 | Add `ZEUS_ENTRY_FORECAST_ROLLOUT_GATE` env flag (default OFF). Wire `evaluate_entry_forecast_rollout_gate` into `evaluator.py:1310-1322` only when flag=1 | Operator |
| C2 | Add `ZEUS_ENTRY_FORECAST_CALIBRATION_GATE` env flag (default OFF). Wire `evaluate_calibration_transfer_policy` into the same path | Operator |
| C3 | Add `ZEUS_ENTRY_FORECAST_READINESS_WRITER` env flag (default OFF). Wire `write_entry_readiness` from a new `producer_readiness.py` companion or `ingest_main.py` daemon step. **Critical**: writer flag MUST come on AFTER C1+C2 (otherwise we go fail-OPEN) | Operator |
| C4 | Update `scripts/healthcheck.py:330-340` to include `entry_forecast_blockers` in `result["healthy"]` predicate, behind `ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS` flag (default OFF until C1-C3 are stable) | Operator |
| C5 | After all four flags ON and stable for ≥1 day with smoke cap=$5: increase smoke cap, then remove the cap entirely | Operator |
| C6 | Day0 cutover: replace `ENTRY_FORECAST_DAY0_EXECUTABLE_PATH_NOT_WIRED` rejection (`evaluator.py:1469-1478`) with a real Day0 reader path (per code-reviewer BLOCKER-1b) | Operator |
| C7 | After live trading proven on this stack for ≥1 trading week: archive Phase 0/1 evidence docs, condense them into a single "PR47 retrospective" reference doc | Operator |

---

## Cross-cutting principles

- **Each commit names which Phase it belongs to** in the trailer (e.g., `[phase-A]`, `[phase-B]`). `[skip-invariant]` continues for the dynamic-SQL drift that pre-existed PR45 base.
- **Every commit lands a single coherent unit** (per `feedback_phase_commit_protocol`). No `git add -A` (per `feedback_no_git_add_all_with_cotenant`); explicit file-by-file staging because the worktree has unrelated dirty/untracked artifacts.
- **Critic + code-reviewer dispatched per phase** (per `feedback_default_dispatch_reviewers_per_phase`). Phase A: light review (docs/tests). Phase B: full adversarial 10-pattern critic + code-reviewer; both run on the phase's full diff.
- **Symbol-anchored references** wherever line numbers would rot (per `feedback_zeus_plan_citations_rot_fast`). Plan files cite function/class names, not file:line.
- **Relationship tests precede behavior changes** (per Fitz universal: "test relationships, not just functions"). B4 and B5 land before any daemon-wire work in Phase C.
- **Co-tenant write awareness**: this branch has had concurrent operator pushes. Before each commit, `git status -sb` + `git --no-pager log --oneline origin/<branch>..HEAD` to confirm linear history. Use `--force-with-lease` only if explicitly authorized (operator-only per AGENTS.md §git safety).

## Open questions for operator

1. Phase B item B8 (`evaluator.py` exception scope tightening) requires touching a daemon hot-path file. Approve, defer, or skip?
2. Phase B item B9 (dead knobs `allow_short_horizon_06_18` / `require_active_market_future_coverage`): delete (Option 1, recommended) or assert (Option 2)?
3. Where should `EntryForecastPromotionEvidence` live? `state/entry_forecast_promotion_evidence.json` (recommended — atomic, file-system-visible) or a new DB table? Atomic JSON keeps ops simple and matches OpenClaw conventions; DB table fits Zeus authority order.
4. Should Phase A and Phase B land as one PR (single push to `origin/healthcheck-riskguard-live-label-2026-05-02`) or as two stacked commits with operator review between them?
5. Premise erratum (A11): is the 408+102 vs 204+51 ratio confirmed as high+low track double-count, or is one of the two probes wrong? Need a one-time DB probe to confirm before writing the erratum doc.
