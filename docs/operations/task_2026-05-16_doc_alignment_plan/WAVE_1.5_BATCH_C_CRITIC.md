# WAVE 1.5 Batch C Critic Verdict — REVISE (2026-05-16)

Opus critic (fresh-context, agent ae54cbf91c5e04b77) review of WAVE 1.5 Batch C commits `e3d260dd23..2508d6e08a` (3 commits: C1 lore_proposal_emission + C2 authority_drift_surface + C3 agent_self_evidence_archival). Final 3 handlers closing WAVE 1.5.

**Carry-forward status from Batch A/B**: All Batch A remediations (C1+C2+M1+M2+M3) and Batch B remediations (CB1+MB1+MB2 docstring+MB3 integration test) read first and verified via `git log --oneline` showing `9ef1bf51fb fix(cb1)`, `132f9a6d15 fix(mb1)`, `db6698f43e fix(mb2)`, `62a6e0503b fix(mb3)`, `8bfd4731e5 fix(wave-1.5-minors)`. NOT re-flagged.

## Verdict: REVISE

**Composition pattern (3rd batch in a row of this shape)**: 16/16 new tests pass; 882/882 suite passes (worker's count verified empirically: `python -m pytest tests/maintenance_worker/ -q` → 882 passed in 6.84s). Handlers individually well-authored: TOP guards present where appropriate, fail-closed in C3, hardcoded dry_run_only in C1+C2 (correct per `live_default:false`). **However:** systemic issues that cross batches surface here for the first time and need closure before WAVE 1.5 is declared done — specifically (1) engine-level cascade isolation gap that Batch C's handlers concretely amplify, and (2) C2 ships ENTIRELY DORMANT with no runtime path to ever execute. The plan §1.5.5 explicitly required cascade-isolation testing and §5.2 per-handler isolation; neither survives. Realist Check downgraded both potential CRITICALs to MAJOR: the dormant-handler case fails open (no runtime risk) and the cascade case is partially mitigated by handlers wrapping their own inner IO in try/except.

## Pre-commitment Predictions vs Actual

| Probe | Prediction | Actual |
|---|---|---|
| C1 4 stubbed triggers | Worker claims safe; need to confirm whether catalog spec or just spec-deferred | Confirmed catalog-deferred (LORE_EXTRACTION_PROTOCOL.md not yet specified per worker; only trigger 1 has concrete shape in spec) — PASS as honest deviation |
| C2 weekly dormancy | Acceptable Wave 7 deferral OR shipping non-runnable handler | Worker discloses; engine.py:216 TODO confirmed; NO calling site exists. Half-fix — see MC1 |
| C2 drift formula | Div-by-zero / cap correctness / threshold edge | Div-by-zero protected (line 235 `if stale_seconds > 0`); cap correct (`min(...,1.0)`); abs() means doc-older treated same as code-older — minor semantic question, see MC-minor-1 |
| C3 live_default+TOCTOU | Must have TOCTOU re-verify + target_only enforcement | PASSED — TOCTOU re-verify at apply lines 210-241 (path.exists + resolve + sqlite_companion re-check); follows Batch B MB1 pattern. Good. |
| Cascade isolation | Engine-level only catches TaskHandlerNotFoundError | EMPIRICALLY CONFIRMED: generic OSError from handler.enumerate() propagates and would poison entire tick (see MC2) |
| Naming pattern | All 3 follow `enumerate` + `# noqa: A001` | Verified — line 74 (C1), 81 (C2), 64 (C3) — consistent with Batch A/B convention |
| Test substantiveness | No True==True | Verified — 0 trivial assertions; all tests exercise real code paths with realistic fixtures |
| C3 shared utility reuse | Does it duplicate sqlite-companion logic or import? | Imports `_is_sqlite_companion` from zero_byte_state_cleanup (line 41) — correct reuse, no duplication |
| Forbidden-path validator | Do C1/C2/C3 pass through validator.validate_action? | NO — verified across all 9 handlers; validator is engaged via `post_mutation_detector` at engine.py:284 (post-apply), not pre-handler. Established pattern; correct. |

## 0 CRITICAL Findings

(One CRITICAL candidate was downgraded to MAJOR after Realist Check — see MC1 rationale.)

## 2 MAJOR Findings

### MC1: C2 `authority_drift_surface` ships entirely dormant — no runtime path can dispatch it
**Files**: `maintenance_worker/core/engine.py:219` (hardcoded `schedule="daily"`) + `maintenance_worker/cli/entry.py` (no `--schedule` flag) + `maintenance_worker/rules/authority_drift_surface.py:22-25` (worker disclosure)
- Catalog spec at `TASK_CATALOG.yaml:185-186`: `schedule: weekly`, `schedule_day: monday`.
- `engine.py:219`: `entries = self._enumerate_candidates(config, schedule="daily")` — hardcoded, no override.
- CLI entry: `grep -n "schedule" maintenance_worker/cli/entry.py` returns 1 match in `scheduler=str(raw.get("scheduler", "launchd"))` (the scheduler-detect string, NOT a per-tick schedule key).
- No launchd/cron caller passes `schedule="weekly"`.
- **Empirical**: grep across `maintenance_worker/`, `scripts/`, `tests/maintenance_worker/` for `get_tasks_for_schedule("weekly")` or `run_tick(...weekly...)` returns 0 production-call-site hits; only 2 test fixtures (`test_parser.py:200,518`, `test_task_registry.py:179`) and 2 documentation references.
- Net: the C2 handler module exists, has tests, is wired through TaskRegistry, but **will never receive a tick in production until WAVE 7 lands**.
- Worker disclosure D2 acknowledged this; TODO at engine.py:216 marks the deferral.
- Confidence: HIGH.
- **Realist check**: NOT a CRITICAL. The handler fails open (it can't be invoked, so it can't misfire). The negative outcomes are (a) drift detection never happens (operator unaware); (b) confusion when someone expects it to run. Both are recoverable; no data loss or corruption. Downgraded from CRITICAL → MAJOR.
- **Mitigated by**: handler is `dry_run: true` + `live_default: false` per catalog — even if accidentally dispatched, it cannot edit authority docs (the `apply()` hardcodes `dry_run_only=True` at line 184). Worst case "wrong dispatch" is a no-op drift report. The dormancy itself is the only issue; not a safety surface.
- **Fix** (≤30 LOC): EITHER
  - **Option A (preferred, ≤15 LOC)**: amend engine.py to dispatch both schedules each tick, letting the registry filter:
    ```python
    # engine.py:218-223 replacement:
    entries: list[TaskCatalogEntry] = []
    for sched in ("daily", "weekly"):
        entries.extend(self._enumerate_candidates(config, schedule=sched))
    # Note: TaskRegistry.get_tasks_for_schedule("weekly") returns only weekly tasks;
    # union here is correct since registry filters by `schedule` field per entry.
    ```
    This is cheap and keeps the catalog field truthful. Add a regression test that `run_tick` with the live catalog reaches `authority_drift_surface.enumerate`.
  - **Option B (worker-preferred)**: leave dormant + document explicitly in PLAN.md WAVE 7 deferral list AND add a unit test that proves `_enumerate_candidates(config, schedule="weekly")` correctly returns the weekly entries from the production catalog (not a synthetic one). The existing `test_authority_drift_surface.py` tests only synthetic `TaskCatalogEntry` objects; there is no test proving the production catalog `schedule_day: monday` field doesn't get dropped during TaskRegistry parsing.

### MC2: Cascade isolation broken at engine layer — Batch C's filesystem-walking handlers concretely amplify the risk
**Files**: `maintenance_worker/core/engine.py:384-394` (`_dispatch_enumerate` only catches `TaskHandlerNotFoundError`) + `:450-460` (`_apply_decisions` same pattern)
- Empirically verified: a `OSError` raised inside any handler's `enumerate()` propagates out of `_dispatch_enumerate` and kills the entire tick. Repro:
  ```python
  # Synthetic handler that raises OSError → critic ran this in venv:
  # Result: "OSError PROPAGATED (cascade isolation broken): disk read failed"
  ```
- Why Batch C amplifies: C1 walks `docs/operations/task_*/` (90+ packets, reads PLAN.md per packet), C2 walks `architecture/` + `docs/operations/` + `docs/review/` with `rglob("*.md")` (thousands of files in a real repo), C3 walks `evidence_dir` with `rglob("*")` (entire evidence trail). Realistic failure modes: permission errors on individual files, broken symlinks (`OSError` from `path.resolve()` outside the catch), encoding errors during `read_text` (already caught in C1, NOT caught at outer iteration in C2), `stat()` race on file vanishing mid-iteration.
- C1/C3 wrap most inner IO in try/except (verified — `_get_mtime`, `_find_lessons_file` both catch `OSError`); C2's outer loop at lines 102-168 has NO outer try/except. A single permission-denied `.md` during `authority_dir.rglob("*.md")` would kill the loop — but C2 is dormant per MC1, so no actual blast today.
- The plan explicitly required this (§1.5.5 "cascade-isolation" test + §5.2 "per-handler isolation"). Batch A "What's Missing" gap mentioned end-to-end integration; cascade-isolation was never explicitly verified.
- Confidence: HIGH (empirical repro).
- **Realist check**: downgraded from CRITICAL → MAJOR. (a) Handlers DO wrap inner IO mostly safely (C1+C3 protected; only C2 outer loop bare); (b) the immediate blast radius is "one tick fails to run" — not data corruption, not silent wrong action. Detection time: immediate (cron stderr captures stack trace, no SUMMARY.md emitted that tick). Recovery: next tick proceeds normally after the offending file is removed/fixed. (c) MC1's dormancy means C2's bare loop cannot fire today.
- **Mitigated by**: handler internal try/except in C1+C3; C2 cannot dispatch today (MC1); engine exits cleanly on uncaught exception (no half-applied state since C1/C2 are dry_run_only and C3 only `shutil.move` is inside an inner try/except).
- **Fix** (≤25 LOC): bracket both dispatcher methods with a broad `except Exception` that logs + returns safe defaults:
  ```python
  # engine.py _dispatch_enumerate:
  try:
      result: list[Candidate] = self._dispatch_by_task_id(
          entry.spec.task_id, "enumerate", entry, ctx
      )
      return result
  except TaskHandlerNotFoundError:
      logger.debug("_dispatch_enumerate: no handler for %s; returning []", entry.spec.task_id)
      return []
  except Exception as exc:
      logger.error(
          "_dispatch_enumerate: handler %s raised %s: %s; isolating from peers",
          entry.spec.task_id, type(exc).__name__, exc, exc_info=True,
      )
      return []
  
  # engine.py _apply_decisions (analogous wrap on the dispatch call):
  try:
      result: ApplyResult = self._dispatch_by_task_id(task.task_id, "apply", candidate, ctx)
      return result
  except TaskHandlerNotFoundError:
      logger.debug(...)
      return ApplyResult(task_id=task.task_id, dry_run_only=True)
  except Exception as exc:
      logger.error("_apply_decisions: handler %s raised %s: %s; treating as dry_run",
                   task.task_id, type(exc).__name__, exc, exc_info=True)
      return ApplyResult(task_id=task.task_id, dry_run_only=True)
  ```
  Add a regression test: dispatch_enumerate on a handler that raises OSError → returns `[]`, peer handlers run normally.

## 3 MINOR Findings

- **MC-minor-1**: C2 drift formula uses `abs(doc_mtime - code_mtime)` (line 234) — treats "doc older than code" identically to "code older than doc". REMEDIATION_PLAN.md (cited authority basis) focuses on documents lagging code (authority drift = stale docs); the inverse case (newly-written docs ahead of stale code) might be intentional but it's not explicit in the catalog or handler docstring. Cosmetic/spec-clarity; not blocking.
- **MC-minor-2**: C3's `enumerate()` ordering of checks emits a `SKIP_CURRENT_TICK_DIR` candidate for **every nested file** in today's evidence dir (lines 119-129 fires before line 150 depth filter). On a production day with N files written under `evidence/2026-05-16/`, the manifest contains N noise SKIP entries. Functionally correct, but inflates the proposal manifest and SUMMARY.md output. Cheap fix: re-order — depth filter (`len(rel.parts) > 1` after computing rel) before current-tick check.
- **MC-minor-3**: C1's `_LESSONS_RE = re.compile(r"^#{1,3}\s+(Lessons|Lessons Learned|Lore)\b", ...)` (line 65) allows H1/H2/H3 with the heading "Lore". The docstring/spec at line 13 says "## Lessons" specifically. A packet with `## Lore` will match; a packet with `### Lessons` (deeper subsection) will also match. Could cause false positives. Not blocking — overcapture is safer than undercapture for a proposal-only emission, but worth a comment.

## What's Missing

- **No integration test of engine→C1/C2/C3** (same gap as Batch A/B → carried forward). The existing `tests/maintenance_worker/test_integration/test_engine_enumerate_integration.py` only covers `closed_packet_archive_proposal` (Batch A) and `test_engine_zero_byte_floor_exempt_live_path.py` covers zero_byte (Batch B fix). Neither C1 nor C2 nor C3 has end-to-end coverage. Especially missing for C3 (the second live-mutation handler).
- **No regression test for cascade isolation** despite plan §1.5.5 explicit requirement. The MC2 fix above MUST include this test.
- **No test exercising the production catalog YAML against TaskRegistry to verify** that `schedule_day: monday` (line 186) survives parsing without dropping the entry. Currently only synthetic catalog entries are tested.
- **No symlink-traversal test for C1's `docs/operations/task_*/` walk** — a symlinked packet pointing outside ops_dir could leak. Catalog doesn't say "no symlinks" but `iterdir()` does follow them. Low realistic risk in a Zeus deployment.
- **No "evidence_dir with O(10k) files" performance test for C3** — `rglob("*")` + per-file path containment checks + per-file `stat()` on a production-grown evidence trail (90 days × ~50 files/day = ~4500 files) will add O(seconds) per tick. Same class of concern Batch A flagged for `closed_packet_archive_proposal`.

## Ambiguity Risks

- C1 module docstring (line 17-19): `"the remaining 4 triggers (...) are stubbed as logger.debug() — detection logic not yet specified in the catalog"`. Two interpretations:
  - A: "the catalog explicitly lists them but the protocol is undefined" → matches reality (catalog `triggers_to_scan` at TASK_CATALOG.yaml:172-177 lists 5 trigger names; LORE_EXTRACTION_PROTOCOL.md doesn't define detection logic).
  - B: "the catalog doesn't require them yet; this is a safe defer" → also true.
  - **Risk if wrong**: a future maintainer assumes interpretation B and silently drops the stubs, removing the explicit deferral evidence. **Fix**: amend docstring to cite `TASK_CATALOG.yaml:172-177` explicitly and link the WAVE 7 deferral row.

- C2 module docstring (line 23-25): `"weekly dispatch is deferred to WAVE 7. This handler will NOT be called on normal daily ticks; it is wired but remains dormant"`. Honest. But the `apply()` docstring at line 175-179 says `"surface-only; never edits authority docs"` — this language reads as "active surface emitter that doesn't edit", obscuring the dormancy. A reader of `apply()` only would believe the handler runs weekly and emits reports; reality is it never runs. **Fix**: prepend `# DORMANT: this function is unreachable from the current engine; see module docstring lines 22-25` to `apply()`.

## Multi-Perspective Notes

- **Executor**: Following WAVE 1.5 to add a 10th handler is now well-templated. The 6-handler corpus (after Batch A+B+C) provides patterns for: live-default false dry-run-only (5 of 6), live-default true with TOCTOU (1 of 6 = zero_byte; C3 is now the 2nd, raising to 2 of 7). A 10th handler could miss: cascade isolation responsibility (MC2 — engine doesn't enforce it), need to add `# noqa: A001` (MB-minor-2 carry-forward), need to handle production-catalog `schedule_day` if not "daily". `feedback_redesign_self_discoverable` failure: no centralized "handler implementation checklist" doc exists yet.
- **Stakeholder**: Does Batch C close WAVE 1.5? Per plan §1.5.7 "top 6 (archival, quarantine, lore_proposal_emission, evidence rotation, kill_switch arm, dry_run_floor exempt) MUST land": ✓ closed_packet_archive_proposal, ✓ launchagent_backup_quarantine, ✓ lore_proposal_emission, ✓ agent_self_evidence_archival (= evidence rotation), ✓ in_repo_scratch_quarantine, ✓ untracked_top_level_quarantine, ✓ stale_worktree_quarantine, ✓ zero_byte_state_cleanup, ◐ authority_drift_surface (DORMANT — MC1). 8 of 9 fully functional; 9th is wired but un-dispatchable. **Recommendation**: either land MC1 Option A fix to honestly close the wave, OR explicitly move authority_drift_surface to WAVE 7 deferral list in PLAN.md and remove the false-implication that WAVE 1.5 closes "all 9 handlers".
- **Skeptic**: Strongest argument for failure: WAVE 1.5 declares "9 handlers wired" but one of them is unreachable code, and the engine's safety perimeter (force_dry_run, dry_run_floor) does NOT include cascade-isolation. A handler that walks a large directory tree and hits one permission denial poisons every other handler's chance to run that tick. With C2 dormant + C3's filesystem walk over evidence_dir + C1's walk over docs/operations, the first production tick after install_metadata clears the 30-day floor has higher-than-zero crash probability — and the failure mode is "no maintenance ran today" with no SUMMARY.md to alert on (engine crashes before phase 6).

## Per-Probe Disposition

| # | Probe | Verdict |
|---|-------|---------|
| 1 | C3 live_default+TOCTOU re-verify (Batch B MB1 pattern) + sqlite-companion guard reuse | PASS — TOCTOU re-verify present (lines 210-241); imports `_is_sqlite_companion` from zero_byte (line 41) |
| 2 | C1 apply() dry_run_only=True hardcoded (proposal-only); no disk mutation path | PASS — lines 116-121 unconditionally return dry_run_only=True, no I/O |
| 3 | C2 drift formula edge cases (div-by-zero, cap, threshold direction) | PASS — `stale_seconds > 0` guard line 235; `min(...,1.0)` cap line 240; abs() = either-direction (note MC-minor-1) |
| 4 | C3 retention TTL + Check #0 invocation before declaring archivable | PASS (no Check #0 needed — Check #0 is for `docs/operations/` archival; C3 acts only on `evidence_dir` per catalog `target_only`) |
| 5 | Cascade isolation — one handler's enumerate() crash poisons engine for peers | **FAIL → MC2** (empirically reproduced) |
| 6 | Naming pattern consistency `enumerate` + `# noqa: A001` across all 3 | PASS (line 74 C1, line 81 C2, line 64 C3) |
| 7 | Test substantiveness — no True==True, no mock-of-self, no presence-only asserts | PASS — 0 trivial assertions; all 16 tests exercise real code paths with patches at module boundaries not on the handler-under-test itself |
| 8 | C2 weekly dormancy — unit test exercising `_enumerate_candidates(config, schedule="weekly")` proves wiring | PARTIAL — engine test exists (Batch B test) but no test loads PRODUCTION catalog `TASK_CATALOG.yaml` with weekly entry; see MC1 "What's Missing" |
| 9 | Cross-handler import discipline — reuse vs duplication | PASS — C3 imports `_is_sqlite_companion` from zero_byte; C1+C2 share no logic since their domains are disjoint (lessons-scan vs drift-score), no duplication |
| 10 | Forbidden-path validator gate before live apply | PASS — established pattern (engine post_mutation_detector at line 284 owns validator); handlers do their own pre-checks (C3 path containment + sqlite re-check; C1+C2 inert) |

## To Upgrade to ACCEPT

1. **MC1 resolved** via Option A (≤15 LOC engine.py amendment to dispatch both schedules + regression test) OR Option B (≤10 LOC PLAN.md WAVE 7 deferral row + production-catalog unit test for `authority_drift_surface` weekly entry).
2. **MC2 resolved** via ≤25 LOC engine.py broad-except wrap on both dispatcher methods + 1 regression test proving handler-OSError → peer-handlers still run.
3. **MC-minor-2** (C3 noise reduction) and **MC-minor-3** (C1 regex docstring) may defer to a hygiene follow-up.

If the operator accepts the dormancy framing for authority_drift_surface AND ships MC2 fix only, that is a defensible REVISE→ACCEPT path; but MC1 deserves at least the PLAN.md deferral row so future agents don't believe WAVE 1.5 is fully closed.

## Provenance

WAVE 1.5 Batch C critic dispatched 2026-05-16 by orchestrator (agent ae54cbf91c5e04b77, opus, fresh-context). Per `feedback_opus_critic_on_architectural_scaffold_4_for_4_roi`: 6-for-6 catch rate now (2 MAJOR caught here; one would have shipped as production-impacting if MC2 fires before MC1 lands and weekly dispatch later activates). Per `feedback_critic_must_read_prior_remediations`: Batch A + Batch B remediations (C1+C2+M1+M2+M3+CB1+MB1+MB2+MB3+MB-minors) read first; verified via `git log` showing fix commits `9ef1bf51fb`, `132f9a6d15`, `db6698f43e`, `62a6e0503b`, `8bfd4731e5`; NOT re-flagged. Per `feedback_critic_reproduces_regression_baseline`: ran full `pytest tests/maintenance_worker/` independently → 882 passed in 6.84s, matching worker's claim. Per `feedback_critic_general_review_plus_probe_contract`: 2 of 10 probes upgraded findings (Probe 5 cascade-isolation upgraded from theoretical to empirically-confirmed; Probe 8 weekly dormancy upgraded from "verify wiring works when caller appears" to "verify caller will ever appear"). Realist Check downgraded both potential CRITICALs to MAJOR per documented mitigation rationales (MC1: fails open; MC2: handler internal try/except + cleanly-exiting tick). Critic operated in THOROUGH mode initially; escalated to ADVERSARIAL after MC1 confirmed (empirical search for weekly call sites; production catalog YAML parse path inspection). No emojis. All file:line citations grep-verified within 5 min of writing. Cascade-isolation MC2 has empirical Python repro (synthetic crashy_handler → OSError propagated out of `_dispatch_enumerate`) captured in critic session log.
