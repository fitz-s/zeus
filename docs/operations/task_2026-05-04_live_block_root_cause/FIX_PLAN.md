# Live-Block Fix Plan v2 — 2026-05-04

**Companion to:** `ROOT_CAUSE.md` (same directory).
**Status:** post-critic v2. critic-opus verdict: APPROVE-WITH-CHANGES, 2026-05-04. All critic must-fixes integrated.
**Goal:** unblock daemon, surface real ValueError, install antibodies for SF1-SF4 within architectural constraints.
**PR strategy:** **2 PRs** total. PR-A surgical unblock + traceback capture (today). PR-B structural antibodies (next week).

---

## Critic-driven changes from v1

| Critic finding | Resolution in v2 |
|---|---|
| Citation rot: `AGENTS.md:18` does not contain "Must reuse" | **Fixed.** Real authority is `src/control/AGENTS.md:18` + `architecture/digest_profiles.py:1336, 1382`. Constraint is real and architectural. |
| Two-file tombstone violates "no second tombstone source" architectural constraint | **Switched to single-file JSON owner payload** `{"owner": ..., "reason": ..., "effective_until": ..., "written_at": ...}`. Halves test matrix, satisfies constraint. |
| 5s in-memory cache creates stale-False window after fresh pause | **Cache dropped.** `is_entries_paused()` reads DB+tombstone every call (sub-ms). |
| PR-A acceptance gate too soft ("daemon didn't crash") | **Hardened.** A0.7 requires next cycle to yield either `candidates>0` or full traceback in stderr before declaring success. |
| B5 AST-walk scope too narrow (just src/engine + src/control) | **Expanded** to all of `src/**/*.py` minus a tightly-scoped allow-list. |
| B1 migration silent on partial/empty/corrupt tombstone files | **Fixed.** Migration wraps read in try/except; on parse failure, defaults to writing both-owner payload + WARN log; test fixtures include partial / empty / unrecognized cases. |
| `ZEUS_TOMBSTONE_OWNERSHIP_V2` flag had no wiring detail | **Renamed `ZEUS_TOMBSTONE_OWNER_FIELD_V2`. Wiring specified.** Read site: `control_plane.py` `refresh_control_state` early branch. Default OFF preserves legacy `os.path.exists` check. ON enables JSON-payload parsing. |
| B3 (refresh override bypassing DB expiry) was implicit fix | **Made explicit** in plan as discrete sub-item. |
| Plan independence claim: PR-A leaves PR-B test fixtures with zero production samples | **Fixed.** B1 tests use synthetic legacy tombstones (heartbeat_cancel_suspected + auto_pause:ValueError + empty + partial) regardless of production state. |

---

## PR-A: Unblock + Capture (Phase 0)

**Goal:** make next ValueError visible, free daemon, identify D1.
**Branch:** `live-block-traceback-capture-2026-05-04`
**Touches:** 1-3 files, ~10 lines.
**Risk:** low — adds logging, removes one tombstone file, restarts daemon.

### Pre-execution prep (audit before code change)

| # | Action |
|---|---|
| pre.1 | Grep `src/engine/` and `src/control/` for `except Exception` followed within 5 lines by `logger.error\|logger.warning` calls without `exc_info=True`. List all matches to disk: `docs/operations/task_2026-05-04_live_block_root_cause/A2_AUDIT.md` |
| pre.2 | Confirm `cycle_runtime.py:2988` is the leading offender (already known); document any others |

### Execution steps

| # | Action | Detail |
|---|---|---|
| A0.1 | Add `exc_info=True` at `src/engine/cycle_runtime.py:2988` | One-line edit: `deps.logger.error("Evaluation failed for %s %s: %s", city.name, candidate.target_date, e, exc_info=True)` |
| A0.2 | Apply same fix to all sites identified in pre.1 audit | Same commit |
| A0.3 | Commit on new branch `live-block-traceback-capture-2026-05-04` with message `fix(observability): add exc_info=True to silent except-Exception loggers (PR-A) [skip-invariant]`; push | Single atomic commit |
| A0.4 | DB unpause via SQL (sqlite3 CLI):<br>```sql<br>INSERT INTO control_overrides_history (override_id, target_type, target_key, action_type, value, issued_by, issued_at, effective_until, reason, precedence, operation, recorded_at) VALUES ('control_plane:global:entries_paused', 'global', 'entries', 'gate', 'false', 'control_plane', strftime('%Y-%m-%dT%H:%M:%fZ','now'), NULL, 'manual_unblock_for_traceback_capture_PR_A', 100, 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ','now'));```<br>Verify: `sqlite3 state/zeus-world.db "SELECT value FROM control_overrides WHERE override_id='control_plane:global:entries_paused';"` returns `false` | Flips view's latest projection |
| A0.5 | `rm state/auto_pause_failclosed.tombstone` | Remove sticky lock |
| A0.6 | `rm -f state/auto_pause_streak.json` | Reset streak counter (defensive) |
| A0.7 | `launchctl kickstart -k gui/501/com.zeus.live-trading` | Picks up A0.1 changes + clean state |
| A0.8 | **HARDENED ACCEPTANCE GATE.** Tail stderr ≤15 min, await first cycle. Two acceptable outcomes:<br>(a) `Cycle ...: N candidates` with N≥1 → success path verified<br>(b) `Evaluation failed for ... <message>` followed by full Traceback in stderr → traceback path verified<br>**Anything else (no cycle in 15 min, daemon crashloop, partial output) → BLOCK and rollback.** | "Daemon didn't crash" is INSUFFICIENT |
| A0.9 | If A0.8 captured traceback: write to `docs/operations/task_2026-05-04_live_block_root_cause/D1_TRACEBACK.md` with verbatim stderr block + identified file:line | Disk artifact |
| A0.10 | Decide D1 fix scope: surgical (1-2 lines) → same PR-A; larger (e.g. data-shape upstream change) → split into PR-A-followup branch | Conditional |

### Acceptance criteria for PR-A merge

- [ ] `cycle_runtime.py:2988` has `exc_info=True`
- [ ] All A2 audit gaps closed
- [ ] Next live cycle either produces `candidates>0` OR full traceback in stderr (one of these two; not "no crash")
- [ ] `state/auto_pause_failclosed.tombstone` does not exist
- [ ] DB latest control_overrides row for entries_paused is `value='false'`
- [ ] If traceback captured: D1_TRACEBACK.md committed
- [ ] If D1 surgical fix bundled: that change in same commit
- [ ] PR description references `ROOT_CAUSE.md` + this `FIX_PLAN.md`

### Rollback

- A0.7 daemon restart leaves daemon worse than before: `git revert` PR-A on new branch + `launchctl kickstart -k gui/501/com.zeus.live-trading`
- Tombstone removal is forward-only but A0.4 unpause row keeps DB consistent regardless
- DB unpause row is auditable history (operation='upsert', reason='manual_unblock_for_traceback_capture_PR_A')

---

## PR-B: Structural Antibodies (Phases 1-4, single PR)

**Goal:** make SF1-SF4 categories of failure structurally impossible going forward.
**Branch:** `tombstone-structural-antibodies-2026-05-XX`
**Touches:** ~6-10 files, mostly contracts + tests.
**Risk:** medium — changes refresh_control_state semantics, HeartbeatSupervisor lifecycle, INV registration.

### B1. Owner-aware single tombstone (SF2) — JSON payload

**Constraint:** `src/control/AGENTS.md:18` and `architecture/digest_profiles.py:1382` forbid a second tombstone file. Solution: keep one file, encode owner as JSON field.

**New tombstone format** (replaces plain-text reason):
```json
{
  "owner": "heartbeat" | "valueerror",
  "reason": "heartbeat_cancel_suspected" | "auto_pause:ValueError" | etc,
  "written_at": "2026-05-04T03:01:00+00:00",
  "effective_until": "2026-05-04T03:16:00+00:00" | null,
  "schema_version": 1
}
```

**Files:**
- `src/control/heartbeat_supervisor.py:207-220` — `_write_failclosed_tombstone` writes JSON payload with `owner: "heartbeat"`, `effective_until: null` (cleared on recovery, not by timer)
- `src/control/control_plane.py:264-275` — `pause_entries`'s db-fault tombstone fallback writes JSON with `owner: "valueerror"`, `effective_until: now + 15min`
- `src/control/control_plane.py:373-378` — `refresh_control_state` reads tombstone via new helper `_parse_tombstone_payload(path)` returning `{owner, effective_until, ...}` or None on absent / corrupt; legacy plain-text content (no JSON) is treated as `owner="legacy_unknown"` and forces paused=True (fail-closed during migration)
- `src/control/control_plane.py:390-391` — `_control_state["entries_pause_source"]` set to `"heartbeat_failclosed" | "valueerror_failclosed" | "legacy_unknown"` based on owner field
- new helper: `src/control/tombstone_payload.py` (or inline in control_plane.py) — atomic write via tempfile+os.replace; strict-parse with try/except
- Migration handled inline at first read post-deploy; **no separate migration script needed** because legacy file is read once and rewritten by next writer

**Tests (new file `tests/test_tombstone_owner_payload.py`):**
- `test_write_then_read_roundtrip_heartbeat`
- `test_write_then_read_roundtrip_valueerror`
- `test_legacy_plain_text_treated_as_legacy_unknown_paused`
- `test_empty_file_treated_as_legacy_unknown_paused` — fail-closed
- `test_partial_json_treated_as_legacy_unknown_paused` — fail-closed (e.g. `{"owner": "heartbeat",` truncated)
- `test_unrecognized_owner_value_treated_as_legacy_unknown_paused` — fail-closed
- `test_no_file_unpaused`
- `test_atomic_write_no_tmp_residue`

### B2. Tombstone self-clear on recovery (SF3)

**Files:**
- `src/control/heartbeat_supervisor.py` — add `_consecutive_healthy_count` field; increment on each HEALTHY tick; when `≥ N=10` AND `_tombstone_written`, call new `_clear_heartbeat_tombstone_if_owner_matches()` and reset `_tombstone_written=False`
- The clear method reads the tombstone JSON; if `owner == "heartbeat"`, deletes the file; if `owner == "valueerror"`, leaves it (heartbeat recovered but ValueError owns the lock now); if absent, no-op
- `src/control/control_plane.py` — `refresh_control_state` tombstone-parse branch already handles `effective_until` expiry from B1 (treats expired-valueerror tombstone as cleared)

**Tests:**
- `test_heartbeat_recovery_clears_heartbeat_tombstone_after_N_ticks`
- `test_heartbeat_recovery_leaves_valueerror_tombstone_intact`
- `test_valueerror_tombstone_self_expires_via_effective_until`
- `test_two_owners_simultaneous_priority` — if heartbeat fails after valueerror tombstone exists, owner-flip semantics defined and tested

### B3. DB ↔ in-memory consistency (SF4) — DB-first, no cache

**Files:**
- `src/control/control_plane.py:117-118` — `is_entries_paused()` rewritten:
  ```python
  def is_entries_paused() -> bool:
      # No 5s cache (critic FAIL #5): stale-False after fresh pause is capital risk.
      # DB read is sub-ms; no perf justification for cache.
      try:
          conn = get_world_connection()
          state = query_control_override_state(conn)
          db_paused = bool(state.get("entries_paused", False))
      except Exception:
          db_paused = True  # fail-closed on query error
      finally:
          if conn is not None:
              try: conn.close()
              except: pass
      tomb_paused = _tombstone_says_paused()  # owner-aware via B1
      return db_paused or tomb_paused
  ```
- Delete `_control_state["entries_paused"]` reads everywhere (audit grep: `_control_state.get("entries_paused"`); replace with `is_entries_paused()` calls
- `_control_state["entries_pause_source"]` and `_control_state["entries_pause_reason"]` still used for status_summary; refresh_control_state still populates them

**Tests:**
- `test_db_expiry_unpauses_without_restart` — write DB row with effective_until in past → is_entries_paused() returns False without daemon restart
- `test_db_query_failure_fails_closed` — close connection mid-query → returns True
- `test_in_memory_flag_no_longer_consulted` — corrupt _control_state["entries_paused"] = True; DB and tombstone both clean; result is False (proves it's not read)

### B4. History cleanup (E1) — one-shot script

**Action:** `scripts/cleanup_legacy_pause_overrides.py`
- For every `control_overrides_history` row with `reason LIKE 'auto_pause:%' AND effective_until IS NULL`, append a synthetic expire row at `now()` with `value='false'`, `operation='legacy_expire'`, `issued_by='cleanup_script_2026_05_04'`
- Audit-preserving: no DELETE, only append
- Run once after PR-B deploys

**Test:**
- `test_cleanup_script_does_not_delete_history` — count(*) before == count(*) after - new_rows
- `test_cleanup_script_only_targets_auto_pause_with_null_effective_until`

### B5. Cross-cutting antibody — logger exc_info enforcement (SF1)

**File:** `tests/test_logger_exc_info_invariant.py`
- AST-walks all `src/**/*.py` (NOT just src/engine + src/control — critic CONCERN #8)
- Allow-list: small explicit YAML/list of `(file, line)` tuples documented as intentional silent catches with required justification comment in source
- For each `try/except Exception (as <name>)?:` block, every `logger.error(...)` or `logger.warning(...)` whose first positional arg references the exception name must include `exc_info=True` keyword OR be `logger.exception(...)` (which auto-sets exc_info)
- Indirect dispatch caveat: AST cannot prove `deps.logger` is the project logger; test treats any `*.error\|*.warning` method call inside except-Exception as in scope. Allow-list overrides for known third-party loggers
- Test fails CI if any unmarked offender exists

**Test fixture:**
- `test_test_logger_exc_info_invariant_catches_known_offender` — fabricate a fake offender file, run AST checker, assert it would have flagged the historical `cycle_runtime.py:2988` shape

### B6. INV registration

**File:** `architecture/invariants.yaml` (verified to exist, critic PASS #7)
- New: **INV-NN — tombstone owner-aware payload.** "auto_pause_failclosed.tombstone is owner-tagged JSON. refresh_control_state discriminates owner via `_parse_tombstone_payload`. Plain-text legacy content fails closed to paused state. src/control/AGENTS.md:18 + architecture/digest_profiles.py:1382 single-file constraint preserved."
- New: **INV-NN+1 — tombstone self-clearing.** "Heartbeat-owned tombstone is cleared after N=10 consecutive HEALTHY heartbeats by HeartbeatSupervisor. ValueError-owned tombstone respects in-payload effective_until and is treated cleared past that time."
- New: **INV-NN+2 — exc_info enforcement.** "All `except Exception` blocks in `src/**/*.py` whose body logs the exception via logger.error / logger.warning must use `exc_info=True` (or logger.exception). Enforced by tests/test_logger_exc_info_invariant.py."

### B7. Feature flag wiring (deployment safety)

**Flag:** `ZEUS_TOMBSTONE_OWNER_FIELD_V2`
- Read site: `src/control/control_plane.py` top of `refresh_control_state` body
- Default: OFF (`os.environ.get("ZEUS_TOMBSTONE_OWNER_FIELD_V2", "0") != "1"` → legacy path)
- ON path (`"1"`): runs new `_parse_tombstone_payload` logic; legacy plain text → fail-closed
- OFF path: original `os.path.exists()` check (the bug-buggy path) — preserved for one-deploy rollback window
- Writers (HeartbeatSupervisor + pause_entries) ALWAYS write JSON regardless of flag (so reader catches up cleanly when flag flips)
- After 24h on with no incidents: remove flag in follow-up PR
- Test: `test_v2_flag_off_preserves_legacy_existence_check`

### Acceptance criteria for PR-B merge

- [ ] All B1-B7 complete with tests green
- [ ] `tests/test_logger_exc_info_invariant.py` runs in CI; fabricated offender test demonstrates it would catch original bug
- [ ] INV registry updated with 3 new invariants
- [ ] `src/control/AGENTS.md:18` updated to reflect "single file with owner-tagged JSON"
- [ ] `architecture/digest_profiles.py:1336, 1382` constraint text updated to clarify "single-file with owner discrimination" still satisfies the constraint
- [ ] 7-day live observation window after deploy: zero `auto_pause:ValueError` rows in `control_overrides_history`
- [ ] Feature flag `ZEUS_TOMBSTONE_OWNER_FIELD_V2=1` flipped after 24h burn-in

### Risk mitigation

- Deploy with flag OFF; flip 24h after observe
- Roll-forward only on tombstone format (writers always write JSON); no roll-back of writer code
- B5 invariant test runs in CI BEFORE merge — protects against same-day regression

---

## Deferred / Out of Scope

- **SF5 (VIEW masking)** — lower priority. Add to `docs/to-do-list/known_gaps.md` with file pointer to this fix plan.
- **F1 (RejectionStage enum)** — already closed.
- The unexplained "21:00→21:31 paused=True from somewhere besides DB+tombstone" mystery in ROOT_CAUSE.md E4 — known unknown; document in `docs/to-do-list/known_gaps.md`. PR-B's B3 (DB-first read) makes this less load-bearing.

---

## Sequencing

```
PR-A (today, 1-2h)         : unblock + capture D1
        ↓
D1 fix (PR-A or split)     : surgical fix of actual ValueError source
        ↓
7-day observation          : confirm no new auto_pause:ValueError rows
        ↓
PR-B (next week, 4-6h)     : SF1-SF4 antibodies + history cleanup
        ↓
Flag flip + 24h burn-in    : enable owner-aware path
        ↓
Flag removal               : follow-up PR after stable
```
