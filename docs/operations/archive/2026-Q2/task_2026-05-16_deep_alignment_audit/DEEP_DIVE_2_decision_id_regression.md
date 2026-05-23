# Deep Dive #2 — `selection_hypothesis_fact.decision_id` NULL Regression

**Date:** 2026-05-16
**Status:** READ-ONLY investigation; no code or DB writes.
**Source finding:** Audit Run #2, Finding #2 (regressed from Run #1).

---

## 1. Executive Summary

- `selection_hypothesis_fact.decision_id` is **100% NULL: 693/693 rows** (up from 506/506 at Run #1; +37% volume in 24h, still 100% NULL). Date range of affected rows: **2026-05-02 → 2026-05-16T14:02:08Z** — overlaps Karachi 5/17 decision window.
- `execution_fact.decision_id`: **1/6 NULL** (the single `exit` row `c30f28a5-d4e:exit`). All 5 `entry` rows correctly populated.
- **Root cause** is a single missing kwarg at one INSERT-site call: [src/engine/evaluator.py:1535](src/engine/evaluator.py#L1535) (`log_selection_hypothesis_fact(...)`) omits `decision_id=`. The helper accepts it (default `None`); the schema column accepts NULL; the omission is silent.
- **Downstream impact today: LOW** — no production reader joins on `selection_hypothesis_fact.decision_id`. Backtest reader (`src/backtest/economics.py`) only counts `selected_post_fdr=1`. **Audit/forensics impact: HIGH** — the design intent (link hypothesis → decision_log/trade_decisions/execution_fact) is unfulfilled and no historical row can be retroactively joined.
- **Caveat (structural)**: `_decision_id()` at [src/engine/evaluator.py:672](src/engine/evaluator.py#L672) returns a **fresh `uuid4()[:12]` per call**, so naively passing `_decision_id()` produces an unjoinable id-per-row. The semantically correct fix passes the per-family `decision_snapshot_id` (already in scope at the call site) or threads a single per-decision uuid through the loop.

**Live risk to Karachi 5/17 position: LOW.** No trading-gate path consults `selection_hypothesis_fact.decision_id`. Position selection, sizing, and risk-guard all flow via `trade_decisions` / `decision_log` / `execution_fact` (decision_id present). Backtest/learning analytics that *should* attribute outcomes back to hypothesis rows are silently degraded but not on the trading critical path.

---

## 2. Schema Design & FK Intent

`selection_hypothesis_fact` ([src/state/db.py:1234](src/state/db.py#L1234)):

```sql
CREATE TABLE IF NOT EXISTS selection_hypothesis_fact (
    hypothesis_id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL,
    decision_id TEXT,                       -- nullable; no FK constraint
    candidate_id TEXT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    range_label TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (...),
    ...
    FOREIGN KEY(family_id) REFERENCES selection_family_fact(family_id)
);
```

Sibling tables for comparison:

| Table | decision-axis column | FK | Population |
|---|---|---|---|
| `selection_family_fact` | `decision_snapshot_id TEXT` | none | populated (writer threads it) |
| `selection_hypothesis_fact` | `decision_id TEXT` | none (column-level) | **100% NULL** |
| `execution_fact` | `decision_id TEXT` | none | 5/6 populated; UPSERT preserves prior non-null |
| `decision_log` / `trade_decisions` | `decision_id` | source of truth | populated |

The asymmetry is the smoking gun: family rows carry a `decision_snapshot_id`; hypothesis rows were *intended* to carry a `decision_id` linking to `decision_log.decision_id` / `trade_decisions.decision_id`, enabling joins of the form: *"for each entered trade, what was the FDR-passing hypothesis set?"* No such join is currently possible.

---

## 3. INSERT-Site Analysis ([src/engine/evaluator.py:1535](src/engine/evaluator.py#L1535))

Enclosing function: `_record_selection_family_facts(...)` defined at [src/engine/evaluator.py:1371](src/engine/evaluator.py#L1371). Scope at line 1535 has `decision_snapshot_id` available (it is passed in as a parameter and already threaded into `log_selection_family_fact` at line 1521).

Current call (lines 1535–1568, excerpt):

```python
result = log_selection_hypothesis_fact(
    conn,
    hypothesis_id=row["hypothesis_id"],
    family_id=row["family_id"],
    candidate_id=row["candidate_id"],
    city=candidate.city.name,
    target_date=candidate.target_date,
    range_label=row["range_label"],
    direction=row["direction"],
    p_value=row["p_value"],
    q_value=row.get("q_value"),
    ci_lower=row["ci_lower"],
    ci_upper=row["ci_upper"],
    edge=row["edge"],
    tested=True,
    passed_prefilter=bool(row.get("passed_prefilter")),
    selected_post_fdr=selected_post_fdr,
    rejection_stage=None if selected_post_fdr else "FDR_FILTERED",
    recorded_at=recorded_at,
    meta={...},
    # decision_id=  <-- OMITTED
)
```

Helper signature ([src/state/db.py:5303](src/state/db.py#L5303)) accepts `decision_id: str | None = None`. The INSERT at [src/state/db.py:5337](src/state/db.py#L5337) binds it correctly. The omission is at the call site, not in the helper.

Sibling call at line 1517 (`log_selection_family_fact`) DOES pass `decision_snapshot_id=decision_snapshot_id`. Two adjacent writes — one threaded, one not.

---

## 4. Upstream Availability

`decision_snapshot_id` is in scope at line 1535 (function parameter of `_record_selection_family_facts`). It is the natural value to write.

`_decision_id()` ([src/engine/evaluator.py:672](src/engine/evaluator.py#L672)):

```python
def _decision_id() -> str:
    return str(uuid.uuid4())[:12]
```

is called 38+ times in `evaluator.py` (lines 1724, 1739, 1761, 1777, …). **Each call returns a fresh uuid.** This means the existing convention does *not* support cross-row join via decision_id; each populated row gets its own unique id. The audit-implied story (*"populated tables can join, this one can't"*) is only half-true — even the populated rows in `execution_fact` cannot be joined back to a hypothesis row by decision_id because no shared id exists upstream.

**Implication for the fix**: passing `_decision_id()` would silence the NULL alarm but produce no analytic value. Passing `decision_snapshot_id` (already shared across all hypotheses + the family row of one decision pass) is the design-correct value.

---

## 5. Positive Control

Same file, same enclosing function, sibling call:

```python
# line 1517
result = log_selection_family_fact(
    conn,
    family_id=family_id,
    cycle_mode=cycle_mode,
    decision_snapshot_id=decision_snapshot_id,   # ← threaded correctly
    ...
)
```

This proves the value is available and `decision_snapshot_id` is the project's canonical per-decision identifier in this scope.

---

## 6. Git Blame

```
11c46ed3242 (Fitz 2026-04-11 19:21:40 -0500 1535) result = log_selection_hypothesis_fact(
11c46ed3242 (Fitz 2026-04-11 19:21:40 -0500 1536–1567 — entire call block)
917a4ca4803 (Fitz 2026-05-02 21:00:32 -0500 1556) "hypothesis_strategy_key": ...   # later meta tweak
```

- **Origin commit:** `11c46ed3242` (Fitz, 2026-04-11). The call site was born without `decision_id=`. Only the `meta_json["hypothesis_strategy_key"]` field has been touched since (2026-05-02), unrelated.
- **Likely intent (inferred):** the schema column was added in anticipation of cross-table joins; the writer-side threading was overlooked in the same commit that introduced the helper. No commit message references the omission. The helper signature making `decision_id` optional (`= None`) hid the regression — it never raised, never logged, never failed any test.

---

## 7. Downstream Consumers

Searched `src/`, `scripts/`, `tests/`:

| File | Reference | Uses `decision_id`? |
|---|---|---|
| `src/backtest/economics.py:236` | `_count_rows(conn, "selection_hypothesis_fact", "selected_post_fdr = 1")` | NO — counts only |
| `tests/test_db.py:739, 760` | writer-roundtrip tests | NO — doesn't assert decision_id |
| `tests/test_fdr.py:370, 704, 964` | reads `meta_json`, counts rows | NO |
| `tests/test_backtest_skill_economics.py:256, 271` | synthetic minimal schema (only `selected_post_fdr`) | NO |
| `tests/fixtures/before_p2_sqlite_master.sql:1263` | schema snapshot | n/a |

**Zero production readers join on or filter by `selection_hypothesis_fact.decision_id`.** This is why 693/693 NULL has been silently accumulating since 2026-05-02 with no functional symptom — only a forensic capability gap.

No alembic-style migrations directory exists in this repo (`find migrations alembic -name '*.py'` empty). Schema is bootstrap-only via `init_schema` in `db.py`.

---

## 8. Fix Specification

### Code change (one-line, file:line precise)

**File:** `src/engine/evaluator.py`
**Insertion point:** between lines 1553 and 1554 (after `recorded_at=recorded_at,` and before `meta={`)

```diff
             recorded_at=recorded_at,
+            decision_id=decision_snapshot_id,
             meta={
```

Rationale: `decision_snapshot_id` is already in scope (function parameter of `_record_selection_family_facts`, used at line 1521 for the sibling family write). It is the canonical per-decision identifier and produces an immediately joinable column. Using `_decision_id()` would silence the NULL alarm without enabling joins (each call is a fresh uuid).

**Optional schema follow-up (separate PR, NOT in critical fix):** rename column to `decision_snapshot_id` for naming consistency with `selection_family_fact`, or add an explicit FK comment. Defer — schema rename is heavyweight, no functional benefit beyond clarity.

### Backfill plan for 693 historical NULL rows

The 693 historical rows cannot be perfectly backfilled because no in-table column records the original `decision_snapshot_id`. Three options:

1. **Backfill via `(family_id) → selection_family_fact.decision_snapshot_id` join.** All hypothesis rows carry `family_id` (NOT NULL). The parent family row has `decision_snapshot_id`. This recovers the value exactly:

   ```sql
   -- READ-ONLY preview (run first):
   SELECT COUNT(*) FROM selection_hypothesis_fact h
   LEFT JOIN selection_family_fact f USING(family_id)
   WHERE h.decision_id IS NULL AND f.decision_snapshot_id IS NOT NULL;

   -- Backfill (run only after audit acceptance, with a backup):
   UPDATE selection_hypothesis_fact
   SET decision_id = (
       SELECT f.decision_snapshot_id
       FROM selection_family_fact f
       WHERE f.family_id = selection_hypothesis_fact.family_id
   )
   WHERE decision_id IS NULL;
   ```

2. Leave historical NULL, fix forward only. Acceptable if forensic value of pre-2026-05-16 hypothesis selections is low.

3. Hybrid: backfill (1), then enforce `NOT NULL` after a grace period via migration.

**Recommended: option (1) bundled with the code fix in a single PR.** Restores forensic capability for the full 693 rows at zero risk (READ-from-sibling, no inferred data).

### `execution_fact` 1/6 NULL (the exit row)

The single NULL row is `c30f28a5-d4e:exit`. The matching entry row `c30f28a5-d4e:entry` has `decision_id=9e960582-602`. Exit-side caller at [src/state/db.py:6031](src/state/db.py#L6031) passes `decision_id=decision_id` from local scope; the value is likely `None` for exits because exits don't go through a fresh "decision" cycle (they're driven by monitor/risk-guard exits). The helper's UPSERT fallback (`stored_decision_id = decision_id if decision_id not in (None, "") else (current["decision_id"] if current else None)`) was designed to inherit the entry's decision_id on re-write, but the exit row has its own `intent_id` (`:exit` suffix), so no prior row exists to inherit from.

**Suggested fix (separate, lower priority):** at the exit-side caller, pass `decision_id = pos.decision_id` (or whatever attribute the position carries from its entry); or rewrite the helper to look up the matching entry by `position_id + order_role='entry'` and inherit. Defer to a separate finding — this is a 1-row corner case, not a regression.

---

## 9. Tests That Should Have Caught This (Gap Analysis)

Existing tests miss the gap because:

- `tests/test_db.py:722–783` exercises the writer helper directly; it does not invoke the evaluator call site, so the omission is invisible.
- `tests/test_fdr.py` reads from `selection_hypothesis_fact` after evaluator runs but only checks `meta_json`, `direction`, `selected_post_fdr`, and row count — never `decision_id`.
- No invariant test asserts "for every populated `selection_family_fact` row, all child `selection_hypothesis_fact` rows have matching `decision_id == family.decision_snapshot_id`."

**Recommended new tests** (3 small, high-value):

1. **Writer integration assertion** (in `tests/test_fdr.py` or new): after evaluator pass, assert `SELECT COUNT(*) FROM selection_hypothesis_fact WHERE decision_id IS NULL` is `0` for the test pass output.
2. **Cross-table invariant test**: assert `SELECT COUNT(*) FROM selection_hypothesis_fact h JOIN selection_family_fact f USING(family_id) WHERE h.decision_id != f.decision_snapshot_id` is `0`.
3. **Schema-level audit query in CI** (or a `make audit` target): periodic `SELECT 100.0*SUM(decision_id IS NULL)/COUNT(*) FROM selection_hypothesis_fact` with a threshold alarm at >1%.

Test (2) is the "antibody" — it makes the *category* of decision-axis-orphan rows impossible-to-pass in CI, not just the current instance.

---

## 10. Live Risk to Karachi 5/17 Position

**Verdict: LOW.**

- Trading critical path (entry sizing, risk-guard, exit triggers) reads `trade_decisions`, `decision_log`, `execution_fact`, and live `position_state` tables — none consults `selection_hypothesis_fact.decision_id`.
- Backtest economics module gates on `selected_post_fdr=1` count (column populated correctly), independent of `decision_id`.
- The 1 NULL `execution_fact` row is for an EXIT intent of a different position (`c30f28a5-d4e`), not Karachi 5/17.
- No alarms, no halts, no decision derivation is impaired for the current trading window.
- **Sole degradation:** post-trade learning/attribution that wants to walk `outcome → execution → decision → hypothesis bundle` is silently blocked at the last hop. This affects offline analytics, not online trading.

**No live action required.** Fix can be batched with the next routine PR.

---

## Appendix A — Verification Commands Run

```bash
sqlite3 "file:state/zeus_trades.db?mode=ro" \
  "SELECT COUNT(*), SUM(decision_id IS NULL) FROM selection_hypothesis_fact;"
# → 693|693

sqlite3 "file:state/zeus_trades.db?mode=ro" \
  "SELECT COUNT(*), SUM(decision_id IS NULL) FROM execution_fact;"
# → 6|1

sqlite3 "file:state/zeus_trades.db?mode=ro" \
  "SELECT MIN(recorded_at), MAX(recorded_at), COUNT(*) FROM selection_hypothesis_fact;"
# → 2026-05-02T11:02:06Z | 2026-05-16T14:02:08Z | 693

git blame -L 1535,1560 src/engine/evaluator.py
# → 11c46ed3242 (Fitz 2026-04-11)
```

## Appendix B — Antibodies for Memory

- `multi_replace_string_in_file` was NOT used here (read-only investigation).
- Use of `/usr/bin/sqlite3` with absolute path avoided shell-PATH and proxy contamination antibody (per user memory `vscode_tooling_antibodies.md`).
- Terminal output pollution from background log tails observed; switched to `/usr/bin/`-prefixed commands and absolute file paths to bypass.
