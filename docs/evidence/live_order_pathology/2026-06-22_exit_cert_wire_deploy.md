# Exit-Q Certificate: Wire Verification + Boot-Safety Report
**Date:** 2026-06-22
**Worktree verified:** `/Users/leofitz/zeus/.claude/worktrees/agent-a97c6c702ae123343` (HEAD a11bdd82)

---

## 1. Capture Seam — Verified Wired

**File:** `src/execution/exit_lifecycle.py:1871`
**Function:** `_execute_live_exit` — inside `_dual_write_canonical_pending_exit_if_available`, after `EXIT_ORDER_POSTED` event is emitted.

The call site:
```python
capture_exit_decision_certificate(
    conn,
    position=position,
    exit_context=exit_context,
    sell_result=sell_result,
    limit_price=sell_result.submitted_price,
)
```
Called AFTER `EXIT_ORDER_POSTED` so the order is committed to the venue before any cert write attempt. The helper is fully `try/except`-wrapped (lines 3068–3200); any exception is logged and swallowed — it **never blocks the exit order**.

**Best-effort guard:** `capture_exit_decision_certificate` returns `False` (no-op) on `conn is None`, catches all exceptions internally, and returns `False` on any failure. The exit money-path is structurally unchanged.

**Idempotency:** UNIQUE constraint on `(position_id, command_id)` — `INSERT … ON CONFLICT DO UPDATE` makes re-runs safe.

---

## 2. Boot-Safety Verification

### 2a. Schema fingerprint
```
Schema fingerprint OK: d58c2e1d1ad17b9d9a2ba76e54a0592d197fcf34b5aae095f0d4824ea2132da9
```
`python scripts/check_schema_fingerprint.py` — PASS.

### 2b. Registry declaration
`architecture/db_table_ownership.yaml` contains:
```yaml
- name: exit_decision_certificates
  db: world
  schema_class: world_class
  schema_version_owner: SCHEMA_VERSION
  created_by: init_schema
  pk_col: exit_decision_id
```

### 2c. `init_schema` creates the table
`src/state/db.py:3181-3182` — in the world-class DDL block, `init_schema` calls `_ensure_exit_decision_certificates_table(conn)`. Verified by running `init_schema` on a fresh `:memory:` DB and querying `sqlite_master` — the table IS present.

### 2d. `assert_db_matches_registry` — new table causes no FATAL

Test `tests/state/test_exit_decision_certificates_schema.py::test_boot_registry_assert_passes_for_new_table` uses `init_schema_world_only` on `:memory:` then calls `assert_db_matches_registry(conn, DBIdentity.WORLD)`. It asserts that **`exit_decision_certificates` is NOT in the mismatch**. This test **PASSED**.

Note: `day0_oracle_anomaly_flags` causes a registry mismatch on a *completely fresh* DB because it is `created_by: runtime_module` (lazy-created on first flag write, not by `init_schema`). This is a **pre-existing condition on the main branch** — confirmed by running the same assert against the unmodified main tree (same failure, same table). The live daemon DB already has `day0_oracle_anomaly_flags` from lazy creation. This E bundle does NOT introduce or worsen this pre-existing state.

**Boot-safety verdict for the new table:**
- `exit_decision_certificates` in registry: YES
- Created by `init_schema`: YES
- Absent from `assert_db_matches_registry` mismatch: YES (tested by passing test)
- **NO boot FATAL introduced by this bundle.**

---

## 3. Test Results

```
27 passed, 1 warning in 1.26s
```

Full suite:
- `tests/execution/test_exit_decision_certificate_capture.py` — 4 tests PASS
- `tests/state/test_exit_decision_certificates_schema.py` — 5 tests PASS
- `tests/analysis/test_exit_timing_attribution.py` — 18 tests PASS

Compile check (all touched Python files):
```
src/execution/exit_lifecycle.py        — OK
src/state/schema/exit_decision_certificates_schema.py — OK
src/analysis/exit_timing_attribution.py — OK
src/state/db.py                        — OK
```

---

## 4. Files Required for Atomic Deploy to zeus-live-main

These files **must ALL go together** or the daemon boots FATAL (registry/fingerprint mismatch):

| File | Role |
|------|------|
| `src/state/schema/exit_decision_certificates_schema.py` | NEW — DDL + `ensure_table` helper |
| `src/state/db.py` | Calls `ensure_table` from `init_schema` — world-class table creation |
| `architecture/db_table_ownership.yaml` | Registry declaration (`world_class`, `created_by: init_schema`) |
| `architecture/_schema_fingerprint.txt` | Fingerprint pinned to `d58c2e1d...` — must match deployed schema |
| `src/execution/exit_lifecycle.py` | Capture wired at `_execute_live_exit:1871`; `import uuid`; direction enum normalize |
| `src/analysis/exit_timing_attribution.py` | Grader reads cert for real `q_lcb_exit` authority (not bool-proxy) |

`src/engine/cycle_runtime.py` — NOT touched (capture is inside `exit_lifecycle.py`, not `cycle_runtime`).

On deploy `init_schema` will create `exit_decision_certificates` on the live world DB. The live DB already has `day0_oracle_anomaly_flags` (lazy-created), so `assert_db_matches_registry` will see no mismatch for either table after deploy.

---

## 5. Status

- Capture seam: **WIRED** at `src/execution/exit_lifecycle.py:1871`
- Best-effort guard: **CONFIRMED** (try/except in helper, exit never blocked)
- `check_schema_fingerprint.py`: **PASS**
- `assert_db_matches_registry` for `exit_decision_certificates`: **PASS** (test-verified)
- 27 cert/grader/schema tests: **PASS**
- Compile: **PASS** (all 4 Python files)
- Pre-existing `day0_oracle_anomaly_flags` drift: **NOT introduced by this bundle** (pre-exists on main)
