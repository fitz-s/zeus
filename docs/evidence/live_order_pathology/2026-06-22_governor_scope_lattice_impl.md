# Governor scope-lattice implementation — global-freeze → scoped isolation (2026-06-22)

Authority basis: docs/evidence/live_order_pathology/2026-06-22_governor_scope_lattice_decision.md
(frontier consult REQ-20260621-211850, Pro Extended, HIGH confidence; full text /tmp/cgc_answer_REQ-20260621-211850-d63b45.txt)

Tier-0 real-capital change. TDD (RED → GREEN). Implementation + tests only — NO deploy,
NO daemon restart, NO write to any live state DB or control file. Worktree:
`/Users/leofitz/zeus` on branch `deploy/full-lifecycle-fix-20260621`.

---

## 1. One-off diagnostic (run FIRST, read-only, `?immutable=1`)

Query: `state/zeus_trades.db` `venue_commands` in the unresolved unknown side-effect
states (`SUBMIT_UNKNOWN_SIDE_EFFECT`, `UNKNOWN`, `REVIEW_REQUIRED`), then applied the
governor's own `count_unknown_side_effects` / risky-row classifier, grouped by market_id.

Result:

```
Total venue_commands in unresolved states: 1
state=REVIEW_REQUIRED  market_id=2615258  count=1
   cmd=7e07c586500d pos=41e314c8-00a intent=ENTRY side=BUY venue_order_id=YES

count_unknown_side_effects:  effective count = 1, markets = ('2615258',)
RISKY rows total = 1
RISKY rows with EMPTY market_id (UNSCOPEABLE -> SYSTEMIC) = 0
RISKY rows by market_id:  2615258: 1
```

**Grouping verdict:** This is ONE market (`2615258`), one risky row, **scopeable**
(non-empty market_id, venue_order_id present → carries submit side-effect risk per
`_review_required_carries_submit_side_effect_risk`). There is NO cross-market pattern
and NO unscopeable row. This is exactly the SCOPED single-market case the decision doc
describes → ISOLATE, not global. (If a future grouping ever shows the same failure
across ≥2 independent markets, the new escalator flips it to systemic → global.)

---

## 2. Exact diff summary (files + functions changed)

3 files, 135 insertions / 13 deletions in src; 1 new test file. (`git diff --stat`)

### `src/risk_allocator/governor.py`
- **`CapPolicy`** — additive field `systemic_market_count_limit: int = 2` (+ `__post_init__`
  validation `>= 1`); `load_cap_policy` reads it from config with default fallback.
- **`GovernorState`** — additive field `systemic_unknown_side_effect_count: int = 0`
  (+ surfaced in `to_dict`).
- **`RiskAllocator.reduce_only_mode_active`** — replaced the over-broad global latch
  `if unknown_side_effect_count > 0 or reconcile_finding_count > 0` with:
  `reconcile_finding_count > 0` (systemic, unchanged) **OR** `_systemic_unknown_present(state)`.
- **`_gather_risky_unknown_rows(conn)`** — NEW private helper; extracts the existing
  risky-row gather (identical SQL/classification) so the legacy counter and the new
  scope classifier share one pass.
- **`count_unknown_side_effects(conn)`** — unchanged `(count, markets)` signature/behavior,
  now delegates row-gathering to `_gather_risky_unknown_rows` (no behavior change).
- **`UnknownSideEffectScope`** — NEW frozen dataclass: `total_count`, `scoped_markets`,
  `unscopeable_count`, `systemic_count`, `is_systemic`.
- **`classify_unknown_side_effect_scope(conn, cap_policy)`** — NEW; classifies risky rows
  into SCOPED vs SYSTEMIC.
- **`_systemic_unknown_present(governor_state)`** — NEW; the gating predicate + fail-closed
  default.
- **`refresh_global_allocator`** — now calls `classify_unknown_side_effect_scope`, publishes
  `unknown_side_effect_markets=scope.scoped_markets` (per-market isolation, line-186 path)
  AND `systemic_unknown_side_effect_count=scope.systemic_count` (global latch driver).
- File-header provenance updated (`# Last reused/audited: 2026-06-22` + scope-lattice authority).

### `src/risk_allocator/__init__.py`
- Export `UnknownSideEffectScope` and `classify_unknown_side_effect_scope`; header provenance updated.

### `tests/test_governor_scope_lattice.py` (NEW)
- 13 tests: 6 pure-unit gating-predicate cases + 5 DB scope-classifier cases + 2 live-wiring
  end-to-end cases driving `refresh_global_allocator`.

NOTE: the unattributed HK position itself (`2615258` / token `5397…7487`) was NOT touched —
no live DB/state/control writes. The fix is purely the gating predicate + classification.

---

## 3. Test results (RED → GREEN)

**RED (before implementation):** new test file failed at import —
`ImportError: cannot import name 'classify_unknown_side_effect_scope'` and the missing
`systemic_unknown_side_effect_count` field / `systemic_market_count_limit` policy. Contract
well-formed, implementation absent. (Correct RED.)

**GREEN (after implementation):**
- `tests/test_governor_scope_lattice.py` — **13 passed**.
- Existing governor suites (run in isolation, with my changes):
  - `tests/test_unknown_side_effect.py` — **50 passed**.
  - `tests/test_command_recovery.py` — **131 passed**.
  - `tests/test_exit_safety.py` + `tests/money_path/test_edli_live_bridge_allocator_refresh.py`
    + `tests/test_deterministic_400_no_latch.py` — **69 passed** (exercises `refresh_global_allocator`).
  - `tests/test_risk_allocator.py` — **22 passed, 6 failed** (see §4).

### §4 caveat — 6 PRE-EXISTING failures (NOT caused by this change)
`tests/test_risk_allocator.py` has 6 failures with `sqlite3.OperationalError: no such
column: position_id` inside the risky-row SELECT. **Verified failure-neutral:** stashing my
changes and running the file on the clean baseline (`d690170b`) produces the *identical* 6
failures at the OLD `governor.py:754`. They are a pre-existing worktree/test-harness schema
artifact (those specific tests build a connection whose `venue_commands` lacks `position_id`,
unrelated to the scope lattice — `init_schema` itself DOES create `position_id`). My refactor
preserved the SELECT byte-for-byte. Failing tests:
`test_execute_exit_order_kill_switch_blocks_before_persistence_or_sdk`,
`test_live_entry_submit_uses_allocator_selected_FOK_for_shallow_book`,
`test_live_exit_submit_uses_allocator_selected_FOK_when_heartbeat_is_degraded`,
`test_position_lots_reader_uses_latest_append_only_state_and_counts_guards`,
`test_pre_sdk_review_required_no_order_id_does_not_latch_unknown_side_effect_count`,
`test_refresh_global_allocator_accepts_live_default_sqlite_row_factory`.

(Collection note: this worktree carries two UNTRACKED `scripts/audit_*.py` files that trip the
writer-lock antibody at conftest collection. They are not part of this change; I moved them
aside only to run tests and restored them — worktree left identical to as-found.)

---

## 4. Existing tests modified

**NONE.** No existing safety assertion was changed or deleted. The two tests that encoded
the old "global on any unknown" behavior both still pass because of the fail-closed default:
- `test_uncertain_side_effect_states_are_reduce_only_not_exit_kill_switch[{"unknown_side_effect_count":1}]`
- `test_summary_entry_blocks_when_reduce_only_without_kill_switch` (`unknown_side_effect_count=1`)

Both construct a `GovernorState` with a bare count and NO scope evidence (no
`unknown_side_effect_markets`, default `systemic_unknown_side_effect_count=0`).
`_systemic_unknown_present` treats "count present + no scope evidence" as unscopeable →
fail closed → global → reduce_only stays True. Old behavior preserved exactly.

---

## 5. The precise new GLOBAL gating predicate (as implemented)

`RiskAllocator.reduce_only_mode_active` (unchanged conditions elided):

```
reduce_only_mode_active = kill_switch_armed
   OR heartbeat in {STARTING, DEGRADED, LOST, DISABLED_FOR_NON_RESTING_ONLY}
   OR ws_gap_active
   OR reconcile_finding_count > 0                 # SYSTEMIC, unchanged
   OR _systemic_unknown_present(state)            # NEW — scope lattice
   OR risk_level in {DATA_DEGRADED, YELLOW, ORANGE, RED}
```

`_systemic_unknown_present(state)`:
```
if state.systemic_unknown_side_effect_count > 0:     return True   # unscopeable OR cross-market
if state.unknown_side_effect_count <= 0:             return False
return not state.unknown_side_effect_markets          # FAIL CLOSED if count w/o scope evidence
```

`classify_unknown_side_effect_scope(conn, policy)` (drives `systemic_unknown_side_effect_count`
via `refresh_global_allocator`):
```
risky_rows        = unresolved REVIEW_REQUIRED/UNKNOWN venue_commands carrying submit risk
scoped_markets    = sorted distinct NON-EMPTY market_ids of risky_rows
unscopeable_count = risky_rows with blank/whitespace market_id      # cannot bind to 1 market
cross_market      = len(scoped_markets) >= policy.systemic_market_count_limit   # default 2
systemic_count    = total_count if cross_market else unscopeable_count
```

Per-market isolation is UNCHANGED: `can_allocate` line ~186 rejects an intent whose
`market_id ∈ unknown_side_effect_markets` with reason `unknown_side_effect_same_market`.
`unknown_side_effect_limit = 0` semantics preserved: global limit 0 for systemic (any
systemic_count > 0 trips global); per-market 0 for the affected market (line-186 reject).
No other global safety condition weakened; collateral logic untouched.

Live-instance outcome (verified by `test_refresh_global_allocator_scoped_market_does_not_freeze_book`):
for the single scoped `2615258` unknown → `systemic_count=0`, `scoped_markets=('2615258',)` →
GLOBAL reduce_only NOT tripped (`entry.allow_submit = True`), market 2615258 still isolated,
all other markets admit.

---

## 6. Fail-closed rule confirmation

CONFIRMED implemented at two layers:

1. **Classifier (`classify_unknown_side_effect_scope`):** a risky row whose `market_id` is
   blank/whitespace cannot be bound to one market → counted in `unscopeable_count` →
   `systemic_count >= 1` → SYSTEMIC. Test:
   `test_classify_unscopeable_empty_market_is_systemic` (and end-to-end
   `test_refresh_global_allocator_unscopeable_freezes_book` → `allow_submit=False`).

2. **Predicate (`_systemic_unknown_present`):** even when no classifier ran (a `GovernorState`
   carrying an unknown count but no scope evidence — e.g. legacy callers), the absence of
   scope evidence is treated as unscopeable → global. Tests:
   `test_unscopeable_unknown_fails_closed_to_global`,
   `test_bare_unknown_count_without_scope_classification_fails_closed_to_global`.

Net: an unknown that cannot be confidently scoped to a single market freezes the book
globally until scoping exists, exactly as the consult's critical safety caveat requires.
