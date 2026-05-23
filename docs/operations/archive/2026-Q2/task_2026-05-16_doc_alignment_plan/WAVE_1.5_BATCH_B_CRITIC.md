# WAVE 1.5 Batch B Critic Verdict — REVISE (2026-05-16)

Opus critic (fresh-context, agent a14b25bf7458a5428) review of WAVE 1.5 Batch B commits `33b5925602..c871eec147` (4 commits: M3 schedule param + B1 untracked + B2 zero_byte + B3 launchagent).

## Verdict: REVISE

**Carry-forward status from Batch A**: M3 (parameterize schedule) is **APPLIED** in `33b5925602` and verified by test `test_enumerate_candidates_weekly_schedule` — Batch A REVISE gate condition #3 cleared. C1+C2+M1+M2 already addressed in pre-Batch-B commits `24ddf535c4`+`2aad4190cf`+`1ea16ab114`. No re-flagging.

**Composition pattern**: 15/15 new tests pass; handlers individually well-authored (TOP guards present, fail-closed in 2 of 3, regex compiles, glob coverage tested). **However:** the ONLY handler with real `path.unlink()` authority (`zero_byte_state_cleanup`) carries a TOCTOU race + an under-defended sqlite-suffix filter that misses real Zeus state-dir filenames. These are blocking for any future live activation, not blocking for the current dry-run-only catalog posture — but the catalog has `live_default: true` for this handler. Realist check downgraded one CRITICAL to MAJOR; one CRITICAL retained.

## 1 CRITICAL Finding (blocks live activation)

### CB1: `zero_byte_state_cleanup` sqlite-suffix filter misses real Zeus state filenames
**File**: `maintenance_worker/rules/zero_byte_state_cleanup.py:53` (`_SQLITE_SUFFIXES`) + `:214-221` (`_is_sqlite_attached`)
- `_SQLITE_SUFFIXES = {'.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite-wal', '.sqlite-shm'}` — does NOT include:
  - `.db-journal` (SQLite rollback-journal mode; deleting while parent .db mid-transaction = corruption)
  - `.sqlite3`, `.sqlite3-wal`, `.sqlite3-shm` (sqlite3 extension common)
- Real Zeus state-dir contains `zeus-world.db.writer-lock.bulk`, `zeus-forecasts.db.writer-lock.bulk` (verified via `find state/ -name '*.db*'`). `Path.suffix` returns `.bulk` for these; sqlite filter does NOT skip them. If such a file becomes zero-byte AND old AND not lsof-locked (writer process exited), handler marks it `ZERO_BYTE_DELETE_CANDIDATE` and live-applies `unlink()`. Deleting a writer-lock file while DB is active enables concurrent writes and corrupts the DB.
- This is the **ONLY handler with live_default=true + dry_run_floor_exempt=true** — bypasses both engine guards. The only line of defense is `_is_sqlite_attached` + `_is_locked_by_lsof`. Both are too narrow.
- Catalog spec at `TASK_CATALOG.yaml:159` says forbidden = `paths_referenced_by_active_sqlite_attach` — the handler reduces this to "suffix matches a fixed list". Not equivalent.
- **Confidence**: HIGH (empirically verified suffix mismatch + real-repo file scan).
- **Realist check**: NOT downgraded. live_default=true + floor-exempt + real-deletion + real Zeus filenames = production-impacting path that survives all three downgrade questions. Detection time after corruption = hours (next DB-read), recovery requires WAL replay or restore from snapshot.
- **Fix** (≤30 LOC):
  ```python
  # in zero_byte_state_cleanup.py
  _SQLITE_SUFFIXES = frozenset([
      ".db", ".db-wal", ".db-shm", ".db-journal",
      ".sqlite", ".sqlite-wal", ".sqlite-shm", ".sqlite-journal",
      ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".sqlite3-journal",
  ])
  # Add to _is_sqlite_attached: walk path components and check if ANY parent file
  # (path.parent / f"{path.stem.split('.')[0]}.db") exists — if companion .db exists,
  # treat this zero-byte file as sqlite-related even if suffix doesn't match.
  def _is_sqlite_attached(path: Path) -> bool:
      if path.suffix in _SQLITE_SUFFIXES:
          return True
      if path.name.endswith(("-wal", "-shm", "-journal")):
          return True
      # Companion check: if a .db / .sqlite sibling exists with our stem-prefix, skip
      stem_prefix = path.name.split(".")[0]  # zeus-world.db.writer-lock.bulk → zeus-world
      for ext in (".db", ".sqlite", ".sqlite3"):
          if (path.parent / f"{stem_prefix}{ext}").exists():
              return True
      return False
  ```

## 3 MAJOR Findings

### MB1: TOCTOU race between enumerate() and apply() in zero_byte_state_cleanup
**File**: `zero_byte_state_cleanup.py:174-186` (`apply()` body)
- `enumerate()` (line 87-149) checks `size==0`, lsof not held, no sqlite suffix, mtime > age_days. Captures `Candidate`.
- `apply()` proceeds straight to `path.unlink()` without re-verifying any of those conditions.
- Engine processes all enumerations, builds manifests, then iterates apply per candidate — race window can be 100s of ms to seconds.
- Realistic scenario: log-rotator creates empty `app.log` (zero-byte), buffer flushes immediately. Process held an open handle (lsof) before/during enumerate but enumerate ran during the brief unlocked window. By apply-time, the file is being actively written.
- Confidence: HIGH (read engine path; no re-check exists).
- Realist check: downgraded from CRITICAL → MAJOR because: (a) zero-byte files written-to between enumerate and apply remain zero bytes only momentarily; deleting an actively-written file is recoverable (process continues writing to inode, file resurrects on next open or is detected as "process holding deleted inode" via lsof — annoying but not catastrophic); (b) the more dangerous case (sqlite/lock-file) is covered by CB1's broader fix.
- **Mitigated by**: existing `_is_sqlite_attached` + `_is_locked_by_lsof` filters in enumerate path; CB1 fix expands sqlite coverage; this leaves only generic zero-byte stale files (lower blast radius).
- **Fix** (≤15 LOC): re-verify in apply():
  ```python
  # before unlink, after VERDICT_CANDIDATE check:
  try:
      cur = decision.path.stat()
  except (OSError, FileNotFoundError):
      return ApplyResult(task_id="zero_byte_state_cleanup", dry_run_only=True)
  if cur.st_size != 0:
      logger.info("zero_byte_state_cleanup: skip; file no longer zero-byte at apply: %s", decision.path)
      return ApplyResult(task_id="zero_byte_state_cleanup", dry_run_only=True)
  if _is_locked_by_lsof(decision.path) or _is_sqlite_attached(decision.path):
      logger.info("zero_byte_state_cleanup: skip; race-detected post-enumerate guard: %s", decision.path)
      return ApplyResult(task_id="zero_byte_state_cleanup", dry_run_only=True)
  ```

### MB2: `TickContext.dry_run_only` field's documented engine-contract is not implemented
**File**: `maintenance_worker/types/specs.py:71-73` + `maintenance_worker/core/engine.py:149-154` + commit `33b5925602` message
- Docstring says: "Engine sets this to True when MANUAL_CLI or dry-run-floor forces dry-run mode."
- Engine constructs `TickContext` exactly once at `engine.py:149` with default `dry_run_only=False`. **Never updates ctx with force_dry_run / floor outcome.** Field is constant False for the lifetime of every tick.
- The zero_byte TOP guard `if ctx.dry_run_only:` is therefore **dead code in engine-driven runs** — the engine's outer guards (`force_dry_run` return at engine.py:433, floor-result return at :443) short-circuit BEFORE calling `_dispatch_by_task_id(...,"apply",...)`.
- Net behavior is correct (engine doesn't deliver live mode in dry-run scenarios), but the documented contract is false. Field works only for direct test calls + out-of-engine callers.
- Confidence: HIGH (verified via grep for `dry_run_only=` assignments; engine has 0 assignments to `ctx.dry_run_only`).
- Realist check: not downgraded — this is a CONTRACT VIOLATION (translation-loss class per Fitz Constraint #2). A future maintainer reading the docstring will trust that engine drives the field; if a handler is later refactored to assume engine sets it, the silent default-False becomes a footgun. This is the Fitz "philosophy as design intent survives only at ~20%" pattern; explicit engine-side wiring or honest docstring is the antibody.
- **Fix** (≤20 LOC): either (preferred) make engine actually wire it:
  ```python
  # in engine.py _apply_decisions, after force_dry_run check:
  inner_ctx = dataclasses.replace(ctx, dry_run_only=False)
  if force_dry_run or floor_result == "ALLOWED_BUT_DRY_RUN_ONLY":
      inner_ctx = dataclasses.replace(ctx, dry_run_only=True)
  # ... pass inner_ctx to _dispatch_by_task_id("apply", candidate, inner_ctx)
  ```
  OR (lighter) amend docstring at `specs.py:71-73`:
  ```
  dry_run_only: defense-in-depth flag for handler TOP guards. NOTE: engine
    currently relies on early-return guards in _apply_decisions rather than
    propagating this field; ctx is always constructed with default False.
    The field's value is honored by handlers and useful for test/external
    callers; engine integration is deferred to a future packet.
  ```

### MB3: No integration test exercising engine → real handler for any of B1/B2/B3
**File**: `tests/maintenance_worker/test_integration/` (only `test_engine_enumerate_integration.py` exists; covers `closed_packet_archive_proposal` from Batch A)
- Per Batch A critic's "What's Missing" gap: "No integration test exercising engine→real-handler end-to-end. Dispatcher test uses fake module."
- B1+B2+B3 ship without adding this coverage. Especially missing: end-to-end test for `zero_byte_state_cleanup` that walks engine → enumerate → apply with `dry_run_floor_exempt: true` actually bypassing the floor (the ONLY task that can reach live deletion).
- Per `feedback_one_failed_test_is_not_a_diagnosis`: a unit-passed handler is not a verified handler until engine drives it.
- Confidence: HIGH (verified absence via grep).
- Realist check: NOT downgraded — this is THE handler with real `unlink()` authority + floor-exempt, the highest-blast surface in the entire wave 1.5 suite. Catalog change to `dry_run_floor_exempt: true` is irreversible after install_metadata is written; better to catch wiring bugs before that crystallizes.
- **Fix** (≤30 LOC): add `test_engine_zero_byte_floor_exempt_live_path.py` constructing a real catalog with `zero_byte_state_cleanup` only, `live_default=True`, no override file, install_meta=10 days ago (well within floor); assert: (a) engine reaches handler.apply for a zero-byte file; (b) file is unlinked; (c) ApplyResult.dry_run_only=False; (d) re-run with a non-zero file → no unlink, dry_run_only=True.

## 3 MINOR Findings

- **MB-minor-1**: Backup regex `\.(bak|backup|replaced|locked|before_[a-z_]+)[-._]?[0-9TZ]*(?:\.bak)?$` (launchagent_backup, line 52) does NOT match common timestamp-suffixed forms with embedded separators, e.g. `com.zeus.X.plist.bak.2026-05-16T10-00-00Z` (verified empirically). Compact-ISO (no dashes) matches; real-world `*.bak.YYYY-MM-DD` does not. Real impact: stale backups with timestamp suffixes evade quarantine and accumulate. Add `-` to char class: `[0-9TZ_:-]*`.
- **MB-minor-2**: All 3 modules shadow builtin `enumerate` at module level (matches Batch A pattern). `# noqa: A001` is correct lint suppression, but a future maintainer adding `for i, x in enumerate(seq):` inside the module silently calls the handler's enumerate and gets a TypeError. Consider renaming the public function `enumerate_candidates(entry, ctx)` and exposing `enumerate = enumerate_candidates` as a thin alias at module bottom for engine dispatch.
- **MB-minor-3**: `untracked_top_level_quarantine._check_forbidden` (line 182-199) has redundant "task_* in pattern" backup logic on top of `fnmatch(rel_path, "docs/operations/task_*/**")` which already catches it. Not wrong, just dead-on-arrival belt + suspenders + parachute. Tighten or document why.

## What's Missing

- No regression test for the `*.db.writer-lock.bulk` Zeus-specific filename (CB1 surface).
- No test for `_is_sqlite_attached` behavior on `.db-journal` (CB1 surface).
- No race-condition test for zero_byte: write 0-byte file, enumerate, then write content before apply, assert apply skips (MB1 surface).
- No symlink-traversal test for untracked: symlink at top-level → inside `docs/operations/task_*/file.md`; verify handler protects symlink target (catalog says `forbidden_paths: ['docs/operations/task_*/**']`, but the handler only checks the rel_path string, not the resolved target).
- No engine-driven test of M3 weekly dispatch reaching an actual weekly handler (the unit test calls `_enumerate_candidates(..., schedule="weekly")` directly but no `run_tick(schedule="weekly")` exercises the full path; M3 leaves the call site at engine.py:216 hardcoded to `"daily"`, so no scheduled mechanism yet dispatches weekly — half-fix from a runtime perspective, full-fix from a unit perspective).
- No integration test that `ctx.dry_run_only=True` (set by external caller) actually prevents `zero_byte_state_cleanup.apply()` from deleting (the test exists at `test_zero_byte_state_cleanup.py:244-266` but is unit-scope, not engine-driven; MB2's contract gap means engine-driven runs never set the field).

## Ambiguity Risks

- "always dry_run_only (live_default: false in catalog)" — appears in B1+B3 module docstrings. Two interpretations:
  - A: "engine guarantees ctx.dry_run_only=True when live_default=false" → false; engine doesn't wire the field (MB2).
  - B: "the handler hardcodes dry_run_only=True in its return; the field on ctx is irrelevant" → true; this is what B1+B3 actually do (they ignore ctx.dry_run_only entirely).
  - Risk: a future maintainer believing interpretation A may change apply() to "respect ctx.dry_run_only" and create a live-execution path that depends on engine wiring that doesn't exist.

## Multi-Perspective Notes

- **Executor**: Can a maintainer follow Batch B + Batch A to add the remaining 3 handlers (lore_proposal_emission, authority_drift_surface, agent_self_evidence_archival)? Mostly yes — the pattern is now well-established and 6 handlers ship as templates. Gap: no doc explains why `ctx.dry_run_only` is ignored by some handlers and honored by zero_byte (`feedback_redesign_self_discoverable` failure).
- **Stakeholder**: Does Batch B solve the stated WAVE 1.5 goal? Mostly. 3 more handlers shipped + M3 schedule parameterization done. But the only handler with live-delete (zero_byte) ships with CB1 + MB1 unhardened; the dry-run-floor-exempt + live_default=true posture means these will bite when install_metadata clears the 30-day floor (today + 30d).
- **Skeptic**: Strongest argument that this approach will fail: the engine's defense (force_dry_run + dry_run_floor + per-task `dry_run: true` default) is layered, but the LIVE_DEFAULT=true exemption is concentrated in exactly one handler whose internal defenses (suffix-based sqlite check + lsof check at enumerate-time only) don't match the catalog spec ("active_sqlite_attach" + TOCTOU safety). The cost-asymmetry says: one wrong delete corrupts a DB; zero wrong deletes leaves 7 zero-byte files in state/. Hardening zero_byte further is cheap; the brief should require it before the 30-day floor clears.

## Per-Probe Disposition

| # | Probe | Verdict |
|---|-------|---------|
| 1 | `ctx.dry_run_only` field clean seam vs contract violation | FAIL → MB2 (docstring promises engine wiring that isn't there) |
| 2 | B2 zero_byte: TOP guard correctness + race + sqlite suffix completeness | FAIL → CB1 (sqlite suffix narrow + `.bulk` filename), MB1 (TOCTOU) |
| 3 | B1 task_*/ forbidden + symlink traversal | PASS (forbidden matches at glob + backup component check; symlink target via `Path.stat()` would land in dry-run-only move; low risk) |
| 4 | B3 mtime + symlink + `_find_active_plist` correctness | PASS (fails-closed on no-active-plist; symlink resolves via stat; edge of `*.plist → /dev/null` is theoretical) |
| 5 | M3 schedule parameterization completeness | PASS for unit; PARTIAL for runtime — run_tick still hardcodes `"daily"` at engine.py:216, no `run_tick(schedule="weekly")` plumbing yet (left as TODO in commit msg) — see "What's Missing" |
| 6 | Candidate/ApplyResult signature consistency with Batch A | PASS (all 3 handlers match `enumerate(entry, ctx) → list[Candidate]` and `apply(decision, ctx) → ApplyResult`) |
| 7 | Builtin `enumerate` shadowing | PASS (no shadow conflict at runtime; `# noqa: A001` correct; future-fragile per MB-minor-2) |
| 8 | dry_run_only=True hardcoded vs ctx-gated category coherence | FAIL → MB2 (the per-handler choice is correct, but the engine-contract docstring is wrong) |
| 9 | Forbidden-path fail-closed enforcement parity with M2 from Batch A | PASS (all 3 handlers fail-closed on forbidden patterns + missing pre_check resources) |
| 10 | Test coverage substance (15 new tests claimed) | PASS (15 tests, all substantive; no True==True patterns; mocks at module boundaries not self-mock); 1 missing-class gap per MB3 |

## To Upgrade to ACCEPT

1. **CB1 fixed**: sqlite filter expanded to cover `.db-journal`, `.sqlite3*`, `-journal`, and companion-db check for `*.db.writer-lock.bulk`-style sidecars.
2. **MB1 fixed**: `apply()` re-verifies size/lsof/sqlite before `unlink()`.
3. **MB3 fixed**: integration test for engine → `zero_byte_state_cleanup` floor-exempt live path.
4. **MB2** may defer IF docstring at `specs.py:71-73` is amended to honestly describe current behavior (engine doesn't wire; field is for test/external callers). Engine wiring can land in a follow-up packet.

## Provenance

WAVE 1.5 Batch B critic dispatched 2026-05-16 by orchestrator (agent a14b25bf7458a5428, opus, fresh-context). Per `feedback_opus_critic_on_architectural_scaffold_4_for_4_roi`: 5-for-5 catch rate now (1 CRITICAL + 3 MAJOR caught here). Per `feedback_critic_must_read_prior_remediations`: Batch A remediations (C1+C2+M1+M2+M3) read first; not re-flagged. Per `feedback_critic_general_review_plus_probe_contract`: 3 of 10 probes upgraded findings beyond brief (CB1's `.bulk` filename, MB2's contract gap, MB-minor-1's regex separator). Realist Check downgraded MB1 from CRITICAL → MAJOR per real-world mitigation (post-CB1 the only generic-stale case is recoverable) — explicit rationale documented inline. Critic operated in THOROUGH mode initially; escalated to ADVERSARIAL after CB1 surfaced (real-repo file scan added; symlink probes added; companion-file analysis added). No emojis. All file:line citations grep-verified within 5 min of writing.
