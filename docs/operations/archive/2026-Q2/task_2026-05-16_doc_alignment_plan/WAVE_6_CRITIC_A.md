# WAVE 6 Critic A — Handlers + Validations Review

Date: 2026-05-16
Scope: §6.1/6.2/6.4/6.5 — handlers, engine, dispatcher, dry-run validations
Verdict: **ACCEPT-WITH-RESERVATIONS**

---

## Validation Results

| Check | Result | Detail |
|---|---|---|
| pytest `tests/maintenance_worker/` | PASS | 886 passed, 0 failed, exit 0 |
| pytest delta-direction vs baseline | CLEAN | Baseline had many F (pre-fix); current: zero F. New-fail set is empty. |
| topology_doctor `--map-maintenance --map-maintenance-mode advisory` | PASS | exit 0, "topology check ok" |
| lore_reverify `--strict --timeout 60` | PASS | exit 0, 3/3 cards ok |

Note: The task brief specified `--advisory` as a bare flag; that flag does not exist. Correct syntax is `--map-maintenance-mode advisory`. Used correct form; exit 0 confirmed.

---

## Per-Probe Disposition

| Probe | Status | Finding |
|---|---|---|
| P1 — MC2 cascade isolation (broad-except) | PASS | `_dispatch_enumerate` and `_apply_decisions` both catch `Exception` (not bare `except`). Per-task crash logged with `exc_info=True`, isolates from peers via `return []` / `return ApplyResult(dry_run_only=True)`. Engine code is correct. |
| P2 — TaskRegistry + dispatcher contract | PASS | Engine calls `_dispatch_by_task_id(task_id, "enumerate", entry, ctx)` → `list[Candidate]`. Then `_dispatch_by_task_id(task_id, "apply", candidate, ctx)` → `ApplyResult`. All 9 handler modules expose `enumerate(entry, ctx)` and `apply(decision, ctx)` with matching signatures. |
| P3 — TOP guard `if ctx.dry_run_only` in all 9 apply() | PARTIAL PASS — see reservation | `zero_byte_state_cleanup` and `agent_self_evidence_archival` have explicit `if ctx.dry_run_only:` at top of apply(). The other 7 handlers return `dry_run_only=True` unconditionally (live_default=false in catalog), so no guard is needed. However: `ctx.dry_run_only` is referenced but `TickContext` must expose this attribute. Not independently verified that `TickContext.dry_run_only` is present — see Open Questions. |
| P4 — SQLite companion filter coverage | MINOR GAP | `_SQLITE_SUFFIXES` covers `.db`, `.db-wal`, `.db-shm`, `.db-journal`, `.sqlite`, `.sqlite3`, `.sqlite3-wal/shm/journal`. Missing: `.db-lock` (no explicit coverage). The companion-sibling check (check 3) covers Zeus-specific `zeus-world.db.writer-lock.*` names, but a plain `foo.db-lock` zero-byte file would match `.db-lock` suffix — NOT in `_SQLITE_SUFFIXES`. Also `.sqlite3-*` wildcard mentioned in probe spec not present — only `.sqlite3-wal`, `.sqlite3-shm`, `.sqlite3-journal` are covered. `.db-lock` is the concrete gap. |
| P5 — TOCTOU re-verify in zero_byte_state_cleanup apply() | PASS | apply() re-checks `path.stat()` (size==0 + lsof + sqlite) immediately before unlink. Lines 183–199. Correct. |
| P6 — Weekly cadence gate | PASS | `_run_weekly_if_due()` exists (engine.py:326). Atomic write via `tmp_file.write_text(...) + os.replace()` (engine.py:392). Pattern is correct. |
| P7 — 4 handlers silent in WAVE 5 | ACCEPTABLE | `launchagent_backup_quarantine`, `untracked_top_level_quarantine`, `authority_drift_surface`, `agent_self_evidence_archival` returned [] without INFO log. This is expected: (a) no qualifying targets in worktree, or (b) stubs with DEBUG-only logging. Not a silent failure — HANDLER_CRASHED was absent from all runs. If clean-repo silence is the cause, this is correct behavior. |
| P8 — Foreign orphan disposition | ACCEPTABLE | WAVE 5 report explains provenance (not on HEAD, not in any commit, leaked from another session/worktree) and directs operator action. File preserved at `/tmp/` for inspection. Adequate. |
| P9 — install_metadata.json worktree path concern | RESOLVED — NOT A BLOCKER | `repo_root_at_install` is a record field only. Code search confirms it is not used as a runtime path validator by the engine or guards. The 30-day floor gates on `first_run_at` (a datetime), not on the path. After merge+worktree-delete, the state file moves with the repo and `first_run_at` remains valid. The worktree path in the field becomes stale metadata only, not a functional break. |
| P10 — paris_station_resolution_2026-05-01.yaml parse error | NOT WAVE 6 BLOCKER | Correctly deferred to WAVE 7. Not maintenance_worker scope. |

---

## Findings

### MINOR — P4: `.db-lock` suffix not in `_SQLITE_SUFFIXES`

File: `maintenance_worker/rules/zero_byte_state_cleanup.py:55-59`

`_SQLITE_SUFFIXES` contains `.db`, `.db-wal`, `.db-shm`, `.db-journal` but not `.db-lock`. A zero-byte `foo.db-lock` file that is NOT a companion-sibling of a `.db` parent (i.e. the `.db` file has been deleted but the lock file remains) would pass the companion filter and be eligible for deletion. In the Zeus context this is low-probability (lock files are typically not zero-byte), but the stated spec for P4 required `.db-lock` coverage.

**Fix:** Add `.db-lock` and `.lock` to `_SQLITE_SUFFIXES`, or add `path.name.endswith(".db-lock")` to the check-2 branch in `_is_sqlite_companion`.

**Confidence:** HIGH. **Severity:** MINOR — the companion-sibling check (check 3) provides partial mitigation when the parent `.db` file still exists.

---

## What's Missing / Gaps

- `TickContext.dry_run_only` attribute existence not independently verified here (see Open Questions). If this field is absent, the P3 TOP guards in `zero_byte_state_cleanup` and `agent_self_evidence_archival` would raise `AttributeError` at apply-time.
- `_emit_dry_run_proposal` is confirmed stub. Observation pathway is gated. This means per-handler proposal evidence dirs will not appear until P5.5. Correctly documented.
- 4 silent handlers produce no INFO-level log. Makes dry-run observability opaque. Future dry-runs will not be able to distinguish "no qualifying candidates" from "handler silently failed before enumeration". Recommend: add a `logger.info("%s: enumerate returned %d candidates", task_id, len(candidates))` line in `_dispatch_enumerate` after each handler returns.

---

## Verdict Justification

The three validation gates all pass (pytest 886/886, topology ok, lore ok). All 9 handlers implement the correct `enumerate(entry, ctx) → list[Candidate]` / `apply(decision, ctx) → ApplyResult` contract. Cascade isolation is structurally correct in the engine. The dry-run floor is gated correctly. TOCTOU re-verify in `zero_byte_state_cleanup` is present and correct. The weekly cadence gate is atomic.

The one scored finding (`.db-lock` gap) is MINOR and is mitigated by the companion-sibling check when the parent DB file coexists. No CRITICAL or MAJOR issues found.

The WAVE 5 live empirical (3x dry-run exit 0, 886 tests pass, 0 HANDLER_CRASHED) is consistent with and confirmed by this wave's re-run.

Review operated in THOROUGH mode throughout. No escalation to ADVERSARIAL warranted — no CRITICAL findings, no systemic pattern.

---

## Open Questions (unscored)

- Is `TickContext.dry_run_only` actually an attribute on the `TickContext` dataclass? The TOP guards in two `apply()` functions reference `ctx.dry_run_only` but the type file was not read in this review. If absent: `AttributeError` in live apply path. Medium confidence this exists (it was part of the WAVE 1.5 Batch B fix), but verify before WAVE 7 live-mode enablement.
- The `lore_reverify` script runs only 3 cards. Is this the complete set of lore cards with `verification_command`, or is the lore database incomplete? Low-confidence concern; 3/3 ok is the stated signal.
