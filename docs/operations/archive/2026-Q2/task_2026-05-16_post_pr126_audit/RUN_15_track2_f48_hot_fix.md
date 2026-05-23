# Run #15 — Track 2: F48 second pass — `_check_persistence_anomaly` hot fix

## 1. Metadata

| Field | Value |
|---|---|
| Run | #15 — Track 2 |
| Date | 2026-05-17 |
| Branch | `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `7fb380c59d` |
| Worktree | `/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/zeus-deep-alignment-audit-skill` |
| Mandate | Run #14 Track B F48 fix is **incomplete**; settlements still silently returns 1.0. Re-pin root cause + emit antibody. |
| Production code | **NOT MODIFIED** (audit-only; patch proposed as text block below). |

---

## 2. `src/engine/monitor_refresh.py` conn-source second-pass table

Every `conn.execute(...)` site, with the table accessed, the *resolved physical DB* under the runtime conn (trades.db MAIN + `world` + `forecasts` ATTACHed — see `cycle_runner.py:68-90`), and the verdict.

| Line | Function | Bare table in SQL | SQLite name resolution under trades+world+forecasts ATTACH | Physical DB hit | Rows in that DB | Verdict |
|---|---|---|---|---|---|---|
| 1040–1041 | `_check_persistence_anomaly` | `settlements` | MAIN wins (trades.db has `settlements`) | **trades.db** | **0** | **DEAD READ** ❌ |
| 1064 | `_check_persistence_anomaly` | `temp_persistence` | MAIN wins (trades.db has `temp_persistence`) | **trades.db** | **0** | **DEAD READ** ❌ |
| 1139 | `_recent_price_delta` | `token_price_log` | MAIN wins (trades.db has `token_price_log`) | trades.db | 53,088 (max ts `2026-05-17 23:44:45`) | LIVE ✓ |
| 1404 | `monitor_probability_refresh` (inline 1h velocity) | `token_price_log` | MAIN wins | trades.db | 53,088 | LIVE ✓ |

Sites where `conn` is forwarded as a kwarg downstream (`conn=conn` at 328, 453, 860, 1288, 1344): same conn instance (single cycle conn), so they inherit the same resolution rules.

### Sub-verdict
- **4 `conn.execute` sites**; **2 DEAD READs** (1040–1041 + 1064). Both belong to the same persistence-anomaly helper.
- Both deads happen even after a naive "rename to `settlements_v2`" fix (Run #14 Track B): `FROM settlements_v2` still binds to MAIN trades.db (0 rows). The Run #14 fix is **insufficient**; schema-qualifier is required.

### Imports from `src.state.db` in monitor_refresh.py (606, 659)
- `log_microstructure` — write; opens its own conn internally, not part of this read path.

---

## 3. Function context around line 1041 (verbatim, lines 998–1086)

```python
def _check_persistence_anomaly(
    conn, city_name: str, target_date, predicted_high: float,
    *, temperature_metric=None,
) -> float:
    """Check if ENS-predicted temp change from recent days is historically rare.

    Looks at the last 3 days of settlements and averages the delta to smooth out
    single-day noise. Discount is confidence-scaled by sample size:
    - n < 30: not enough data → no discount
    - n=30: 10% discount
    - n=100+: 30% max discount

    LOW metric gate: legacy settlements has no metric column; LOW lookups would
    cross-compare against HIGH historical values. Defer to metric-aware query
    when settlements_v2 populated (P10D).
    """
    if temperature_metric is not None:
        is_low = (
            getattr(temperature_metric, "is_low", lambda: False)()
            or temperature_metric == "low"
        )
        if is_low:
            return 1.0  # no persistence discount for LOW

    from datetime import timedelta

    try:
        from src.calibration.manager import season_from_date, lat_for_city
        season = season_from_date(target_date.isoformat(), lat=lat_for_city(city_name))

        # Average delta over last 3 available settlement days
        deltas = []
        for days_back in range(1, 4):
            d = (target_date - timedelta(days=days_back)).isoformat()
            # H3 (2026-04-24): pin temperature_metric='high' explicitly.
            # LOW callers early-return at L453-459 before reaching this query,
            # so the HIGH filter is safe: any caller reaching this SELECT has
            # already committed to the HIGH axis (via explicit HIGH
            # temperature_metric kwarg, or the default pre-dual-track path).
            # Without the filter, a future LOW settlement row for the same
            # (city, target_date) would silently match and produce a cross-
            # metric delta anyway.
            row = conn.execute(
                "SELECT settlement_value FROM settlements "
                "WHERE city = ? AND target_date = ? "
                "AND temperature_metric = 'high' "
                "AND authority = 'VERIFIED' LIMIT 1",
                (city_name, d),
            ).fetchone()
            if row and row["settlement_value"] is not None:
                # Note: uses WMO half-up as generic directional delta.
                # oracle_truncate precision not critical here (±0.5 max).
                deltas.append(
                    predicted_high - round_wmo_half_up_value(float(row["settlement_value"]))
                )

        if not deltas:
            logger.warning(
                "PERSISTENCE_CHECK_DISABLED: all 3 recent settlement days NULL for %s/%s — returning 1.0 (no discount)",
                city_name, target_date,
            )
            return 1.0

        delta = sum(deltas) / len(deltas)
        bucket = _delta_bucket(delta)

        freq_row = conn.execute(
            "SELECT frequency, n_samples FROM temp_persistence "
            "WHERE city = ? AND season = ? AND delta_bucket = ?",
            (city_name, season, bucket),
        ).fetchone()

        if freq_row and freq_row["frequency"] < 0.05:
            n = freq_row["n_samples"]
            if n < 30:
                return 1.0  # Too few samples to trust the frequency estimate
            # Scale discount: 10% at n=30, grows linearly to 30% at n>=100
            discount_magnitude = min(0.30, 0.10 + 0.20 * (n - 30) / 70.0)
            return 1.0 - discount_magnitude

    except Exception as e:
        logger.debug("Persistence anomaly check failed for %s: %s", city_name, e)

    return 1.0
```

### What `_persistence_discount` returning 1.0 means downstream
- Caller `monitor_probability_refresh` at L544 multiplies `alpha *= anomaly_discount`. `alpha` is the model-trust weight blended with the market prior to form `p_cal_native` (the calibrated native YES probability).
- `1.0` ⇒ **no discount** ⇒ alpha unchanged ⇒ model trust at full weight.
- Downstream this affects **both Kelly sizing** (via posterior edge) **and exit-trigger evaluation** (via `current_p_posterior` feeding `_build_exit_context` → `pos.evaluate_exit(exit_context)`).
- Direction of bias when DEAD-READ returns 1.0 spuriously: when ENS predicts a historically-rare temp jump, the model SHOULD be discounted ⇒ market price should pull the posterior in. With discount=1.0, the model's anomalous prediction is taken at face value ⇒ posterior overweights the model ⇒ for a Karachi long-YES position the exit signal is under-conservative when the model is over-predicting heat.

---

## 4. Live evidence

### 4.1 DB topology (post-K1 split 2026-05-11)
- `state/zeus_trades.db` (644 MB) — MAIN under cycle conn. Has tables `settlements` (0 rows), `settlements_v2` (0 rows), `temp_persistence` (0 rows).
- `state/zeus-world.db` (38.97 GB) — ATTACH `world`. Has `settlements` (0 rows), `settlements_v2` (0 rows), `temp_persistence` (0 rows), plus archives `settlements_archived_2026_05_11`, `settlements_v2_archived_2026_05_11`.
- `state/zeus-forecasts.db` (49.31 GB) — ATTACH `forecasts`. Has `settlements` (5,599 rows, max `settled_at=2026-05-17T05:46:44+00:00`), `settlements_v2` (4,016 rows, max `recorded_at=2026-05-17T05:46:44+00:00`). **No `temp_persistence` table.**
- `state/zeus.db` (4 KB legacy stub) — no tables.

### 4.2 Bare-name SQLite resolution proof
```text
$ python3 -c "import sqlite3; c=sqlite3.connect('state/zeus_trades.db');
              c.execute('ATTACH DATABASE \"state/zeus-world.db\" AS world');
              c.execute('ATTACH DATABASE \"state/zeus-forecasts.db\" AS forecasts');
              for q in ['SELECT COUNT(*) FROM settlements','SELECT COUNT(*) FROM world.settlements',
                        'SELECT COUNT(*) FROM forecasts.settlements']: print(q, c.execute(q).fetchone())"
SELECT COUNT(*) FROM settlements                  (0,)
SELECT COUNT(*) FROM world.settlements            (0,)
SELECT COUNT(*) FROM forecasts.settlements        (5599,)
```

Bare `FROM settlements` ⇒ MAIN ⇒ trades.db ⇒ **0 rows**. Per SQLite docs (https://sqlite.org/lang_attach.html): unqualified table names search MAIN first, then ATTACHes in attach order.

### 4.3 Karachi 5/17 spot-probe
```text
SELECT COUNT(*), MAX(settled_at) FROM settlements WHERE city='Karachi' AND temperature_metric='high' AND authority='VERIFIED'
  → (0, None)              -- against current bare-MAIN binding (trades.db)
```
Confirms Karachi persistence-anomaly check has been a **silent no-op** under live trading.

### 4.4 Log scan
```text
$ grep -i 'PERSISTENCE_CHECK_DISABLED' logs/zeus-live.log    →  no matches
$ grep -i 'persistence_anomaly_discount' logs/zeus-live.log  →  no matches
$ grep -i 'settlements_v2' logs/zeus-live.log                →  no matches
```
The `logger.warning("PERSISTENCE_CHECK_DISABLED ...")` should fire on every call that hits the bare-MAIN 0-row table. Absence of any matches suggests **the warning is being swallowed by the outer `except Exception` at L1083** (some path raises before reaching the warning, e.g. `temp_persistence` lookup if `deltas` did populate via some prior path) — OR the helper is rarely reached because of the early `is_low` return. Either way: no operational visibility that this signal is dead. Antibody (§6) closes this visibility gap.

### 4.5 `temp_persistence` corollary (NEW finding F102)
`temp_persistence` is **empty in all 3 active DBs** AND does not exist in forecasts.db. Even if §5's schema-qualifier fix lands, the secondary `SELECT … FROM temp_persistence` at L1064 still returns `None` ⇒ no `freq_row` ⇒ persistence discount never triggers. **Full repair requires both §5 and a separate fix to repopulate `temp_persistence`** (or rewrite the helper to use forecasts.settlements_v2 frequencies directly).

---

## 5. Hot fix specification (text block; not applied to src/)

Two coordinated edits to `src/engine/monitor_refresh.py` — both must land together.

### Edit A — L1040–1046 (settlements query: bare → forecasts-qualified `settlements_v2`)

**Current (lines 1040–1046):**
```python
            row = conn.execute(
                "SELECT settlement_value FROM settlements "
                "WHERE city = ? AND target_date = ? "
                "AND temperature_metric = 'high' "
                "AND authority = 'VERIFIED' LIMIT 1",
                (city_name, d),
            ).fetchone()
```

**Proposed:**
```python
            row = conn.execute(
                # F48 second pass (Run #15 Track 2 2026-05-17): legacy
                # `settlements` bare-name resolves to trades.db MAIN under
                # cycle conn (0 rows) — DEAD READ. K1-canonical truth is
                # forecasts.settlements_v2 (DR-33 harvester_truth_writer).
                # Must schema-qualify; `settlements_v2` bare still binds to
                # MAIN trades.db (also 0 rows).
                "SELECT settlement_value FROM forecasts.settlements_v2 "
                "WHERE city = ? AND target_date = ? "
                "AND temperature_metric = 'high' "
                "AND authority = 'VERIFIED' LIMIT 1",
                (city_name, d),
            ).fetchone()
```

### Edit B — L1067–1074 (replace `logger.warning` with explicit counter; raise to ERROR-tier visibility)

**Current (lines 1067–1074):**
```python
        if not deltas:
            logger.warning(
                "PERSISTENCE_CHECK_DISABLED: all 3 recent settlement days NULL for %s/%s — returning 1.0 (no discount)",
                city_name, target_date,
            )
            return 1.0
```

**Proposed:**
```python
        if not deltas:
            # F48 antibody (Run #15 Track 2): counter + warning so dead-read
            # regressions are visible in metrics, not just transient logs.
            try:
                from src.observability.counters import increment as _inc
                _inc("PERSISTENCE_FALLBACK_TRIGGERED", labels={
                    "city": city_name,
                    "target_date": target_date.isoformat(),
                })
            except Exception:  # noqa: BLE001
                pass
            logger.warning(
                "PERSISTENCE_FALLBACK_TRIGGERED city=%s target_date=%s — "
                "3-day settlements_v2 lookback empty; returning 1.0 (no discount). "
                "If counter > 0 sustained, verify forecasts.settlements_v2 churn "
                "and conn-source (must have forecasts ATTACHed).",
                city_name, target_date,
            )
            return 1.0
```

### Edit C — L1019–1029 (drop stale H3 comment that contradicts the fix)

**Current (lines 1019–1027, inside docstring):**
```python
    """Check if ENS-predicted temp change from recent days is historically rare.

    Looks at the last 3 days of settlements and averages the delta to smooth out
    single-day noise. Discount is confidence-scaled by sample size:
    - n < 30: not enough data → no discount
    - n=30: 10% discount
    - n=100+: 30% max discount

    LOW metric gate: legacy settlements has no metric column; LOW lookups would
    cross-compare against HIGH historical values. Defer to metric-aware query
    when settlements_v2 populated (P10D).
    """
```

**Proposed (docstring rewrite):**
```python
    """Check if ENS-predicted temp change from recent days is historically rare.

    K1 binding (Run #15 Track 2 2026-05-17): reads
    `forecasts.settlements_v2` via schema-qualifier. Caller's `conn` MUST
    have forecasts.db ATTACHed (cycle_runner.get_connection() does so).

    Looks at the last 3 days of settlements and averages the delta to smooth
    out single-day noise. Discount is confidence-scaled by sample size:
    - n < 30: not enough data → no discount
    - n=30: 10% discount
    - n=100+: 30% max discount

    LOW metric gate: settlements_v2 has a `temperature_metric` column, so LOW
    lookups are unambiguous, but LOW callers early-return at L453-459 before
    reaching this query — the `temperature_metric='high'` filter is therefore
    a safety belt against a future LOW caller bypass.
    """
```

### Why NOT change the conn-source instead
Two options were considered: (i) schema-qualify the SELECT (Edits A–C above); (ii) refactor `_check_persistence_anomaly` to open its own `get_forecasts_connection()`. Option (ii) is structurally cleaner (per Fitz Constraint #1) but adds a per-position read of a 49 GB DB on the hot monitor path and conflicts with the K1 cross-DB-ATTACH design that `cycle_runner.get_connection()` was rebuilt for in K1. Edits A–C respect that design — the cycle conn is the right vehicle; the bug was the bare-name binding.

---

## 6. Antibody test (drop into `tests/engine/test_check_persistence_anomaly_conn_binding.py`)

```python
# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: Run #15 Track 2 F48 second pass — RUN_15_track2_f48_hot_fix.md
"""F48 antibody: `_check_persistence_anomaly` MUST read forecasts.settlements_v2.

If a future refactor reintroduces bare `FROM settlements` (or unqualified
`FROM settlements_v2`), the bare-name binding resolves to trades.db MAIN
which is empty for these tables ⇒ 3-day NULL fallback ⇒ silent no-op.
This test pins the K1-canonical binding (forecasts schema-qualifier).
"""
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.engine.monitor_refresh import _check_persistence_anomaly


def _build_three_db_conn(tmpdir: Path):
    """Mirror cycle_runner.get_connection(): trades MAIN + world + forecasts ATTACH."""
    trades_p = tmpdir / "trades.db"
    world_p = tmpdir / "world.db"
    forecasts_p = tmpdir / "forecasts.db"

    # Trades MAIN: a `settlements` table that EXISTS but is EMPTY (DEAD-READ trap).
    tc = sqlite3.connect(str(trades_p))
    tc.execute(
        "CREATE TABLE settlements (id INTEGER PRIMARY KEY, city TEXT, target_date TEXT, "
        "settlement_value REAL, authority TEXT, temperature_metric TEXT)"
    )
    tc.execute(
        "CREATE TABLE settlements_v2 (settlement_id INTEGER PRIMARY KEY, city TEXT, "
        "target_date TEXT, settlement_value REAL, authority TEXT, temperature_metric TEXT, "
        "recorded_at TEXT)"
    )
    tc.commit()
    tc.close()

    # World ATTACH: empty.
    wc = sqlite3.connect(str(world_p))
    wc.execute(
        "CREATE TABLE settlements (id INTEGER PRIMARY KEY, city TEXT, target_date TEXT, "
        "settlement_value REAL, authority TEXT, temperature_metric TEXT)"
    )
    wc.commit()
    wc.close()

    # Forecasts ATTACH: populated with 3 recent days of HIGH/VERIFIED settlements
    # for city 'Karachi' at target_date+1..3 days back.
    fc = sqlite3.connect(str(forecasts_p))
    fc.execute(
        "CREATE TABLE settlements_v2 (settlement_id INTEGER PRIMARY KEY, city TEXT, "
        "target_date TEXT, settlement_value REAL, authority TEXT, temperature_metric TEXT, "
        "recorded_at TEXT)"
    )
    target = date(2026, 5, 17)
    rows = [
        ("Karachi", (target - timedelta(days=1)).isoformat(), 95.0, "VERIFIED", "high"),
        ("Karachi", (target - timedelta(days=2)).isoformat(), 96.0, "VERIFIED", "high"),
        ("Karachi", (target - timedelta(days=3)).isoformat(), 94.0, "VERIFIED", "high"),
    ]
    fc.executemany(
        "INSERT INTO settlements_v2(city, target_date, settlement_value, authority, "
        "temperature_metric, recorded_at) VALUES (?, ?, ?, ?, ?, '2026-05-17T00:00:00+00:00')",
        rows,
    )
    fc.commit()
    fc.close()

    conn = sqlite3.connect(str(trades_p))
    conn.row_factory = sqlite3.Row
    conn.execute(f"ATTACH DATABASE '{world_p}' AS world")
    conn.execute(f"ATTACH DATABASE '{forecasts_p}' AS forecasts")
    return conn, target


def test_persistence_anomaly_reads_forecasts_settlements_v2_via_schema_qualifier(tmp_path, monkeypatch):
    """If bare `FROM settlements` regresses, deltas stay empty ⇒ 1.0.

    With the F48 hot fix (Edit A), SELECT uses `forecasts.settlements_v2`
    explicitly. Three populated rows in forecasts.settlements_v2 with mean
    settlement 95°F vs predicted 110°F ⇒ delta=+15 ⇒ helper exits the
    `if not deltas: return 1.0` branch. Whether the final return is <1.0
    or exactly 1.0 depends on temp_persistence (separate F102 finding);
    the antibody asserts only the BINDING: deltas must be non-empty, which
    is provable by checking the helper does NOT take the
    PERSISTENCE_FALLBACK_TRIGGERED branch.

    Strategy: monkeypatch logger.warning to capture; assert
    PERSISTENCE_FALLBACK_TRIGGERED is NOT emitted.
    """
    conn, target = _build_three_db_conn(tmp_path)

    captured = []
    from src.engine import monitor_refresh as mr
    monkeypatch.setattr(mr.logger, "warning", lambda msg, *a, **kw: captured.append(msg % a if a else msg))

    # Stub calibration manager imports the helper performs on its happy path.
    monkeypatch.setattr("src.calibration.manager.season_from_date", lambda d, lat=None: "summer")
    monkeypatch.setattr("src.calibration.manager.lat_for_city", lambda c: 24.86)

    result = _check_persistence_anomaly(
        conn, "Karachi", target, predicted_high=110.0, temperature_metric="high"
    )

    # The whole point: with the fix, deltas DOES populate from forecasts.settlements_v2,
    # so the "PERSISTENCE_FALLBACK_TRIGGERED" / "PERSISTENCE_CHECK_DISABLED" warning
    # is NOT emitted.
    fired = [m for m in captured if "PERSISTENCE_FALLBACK" in m or "PERSISTENCE_CHECK_DISABLED" in m]
    assert not fired, (
        f"F48 regression: bare-name `settlements` binding hit MAIN trades.db "
        f"(0 rows) ⇒ fallback fired. Captured warnings: {fired}"
    )

    # Optional sanity: result is a finite multiplier in [0.0, 1.0].
    assert 0.0 <= result <= 1.0
```

---

## 7. Karachi 5/17 blast-radius re-assessment

### Before the fix (current production)
- `_check_persistence_anomaly` returns **1.0 for 100% of HIGH calls** (DEAD READ). No `alpha` discount applied.
- Persistence-anomaly signal is **silently disabled across the entire fleet** since K1 split (2026-05-11), not just Karachi.

### After Edits A–C only (no F102 fix)
- L1041 SELECT now hits `forecasts.settlements_v2` (4,016 rows; Karachi/2026-05-14..16 likely present after harvester runs each day).
- `deltas` populates ⇒ control flow advances past `if not deltas: return 1.0`.
- BUT: L1064 `SELECT … FROM temp_persistence` still resolves to MAIN trades.db (0 rows) ⇒ `freq_row=None` ⇒ skips `freq < 0.05` branch ⇒ falls through to `return 1.0` at L1086.
- **Net qualitative change to Karachi exit signal: NONE.** Still under-conservative.

### After Edits A–C AND F102 fix (repopulate temp_persistence, or qualify L1064 to `forecasts.…`)
- `freq_row` populates. If Karachi's 5/17 ENS-predicted high vs recent 3-day mean falls in a low-frequency delta bucket (`<0.05` historical hit-rate), discount activates (10–30% reduction on `alpha`).
- Direction: `alpha` ↓ ⇒ posterior pulls toward market prior ⇒ `current_p_posterior` shifts toward market consensus ⇒ `current_forward_edge = current_p_posterior − current_p_market` shrinks ⇒ exit thresholds (price-edge based) trigger **earlier**.
- For a long-YES Karachi heat position with the model over-predicting heat: **exit signal becomes more conservative; likelier to exit on the next monitor tick.**
- Magnitude: bounded by ≤30% multiplicative alpha discount; effect on posterior depends on prior weight and bootstrap-CI width. Single-digit-percent shift in `current_p_posterior` is typical.

### Operator action recommended
Apply Edits A–C **plus** open F102 as a separate hot-fix work item (repopulate `temp_persistence` in the correct DB, or qualify L1064 similarly). Edits A–C alone are necessary but not sufficient.

---

## 8. F102+ findings discovered in second pass

> Numbering note: Run #15 Track 1 (cron diff) consumed F93–F95; Run #15 Track 3 (heartbeat) consumed F99–F101 (commit `91deaf104d`). This track therefore starts at F102.

### F102 — `temp_persistence` table is empty / missing across all 3 DBs (NEW, SEV-2, HOT)

- Author: Run #15 Track 2 (2026-05-17)
- Site: `src/engine/monitor_refresh.py:1064` (frequency lookup) + L1446 `CREATE TABLE IF NOT EXISTS temp_persistence` (schema in `src/state/db.py:1446`)
- Evidence: `temp_persistence` row counts — trades.db: 0; world.db: 0; forecasts.db: **table does not exist**.
- Impact: secondary DEAD READ in the same helper. Even with F48 Edit A landed, the discount path cannot activate.
- Suggested fix: separate work item. Either (a) re-route to a forecasts-resident frequency table, or (b) repopulate `temp_persistence` from `settlements_v2` deltas via a backfill cron. Owning team: signal / data-pipeline.
- Karachi-hot? YES — secondary blocker to F48 fix taking effect.

### F103 — Run #14 Track B F48 fix is **insufficient** (NEW, SEV-1, META)
- Author: Run #15 Track 2 (2026-05-17)
- Run #14 Track B (commit `127c9d5676`) specified a 1-line fix of `FROM settlements` → `FROM settlements_v2`. Without schema-qualifier, that fix STILL DEAD-READS (binds to MAIN trades.settlements_v2, 0 rows).
- This entry is filed to surface the antibody discipline lesson: SQLite ATTACH name-resolution requires **schema-qualified** SELECTs in mixed-DB contexts. Bare-name renames without verification of the resolution path can be no-ops.
- Antibody (proposed): `tools/lint/zeus_db_alias.py` (Run #14 Track C) should extend to flag bare `FROM <table>` for any table that exists in MAIN but is canonically owned by an ATTACHed DB — require schema-qualifier on cycle-conn SELECTs.

### F104 — `PERSISTENCE_CHECK_DISABLED` warning never observed in logs despite DEAD-READ being permanent (NEW, SEV-3, observability)

- Author: Run #15 Track 2 (2026-05-17)
- Either: (i) the helper is rarely reached (early `is_low` return covers most positions), (ii) the WARN-level message is being suppressed by log config, or (iii) the outer `except Exception` at L1083 is swallowing a pre-warning exception.
- Edit B (counter + reworded warning to include `PERSISTENCE_FALLBACK_TRIGGERED`) closes (i)/(ii) via a counter; (iii) is mitigated by widening counter emission to the `except` block as well (consider follow-up F105). 

---

## 9. Provenance + headers (per CLAUDE.md "Code Provenance" rule)

If/when Edits A–C land, header block to add (or update) at top of `src/engine/monitor_refresh.py`:
```python
# Created: 2026-04-24 (existing)
# Last reused or audited: 2026-05-17  (Run #15 Track 2 F48 second-pass; schema-qualified settlements_v2)
# Authority basis: docs/operations/task_2026-05-16_post_pr126_audit/RUN_15_track2_f48_hot_fix.md §5
```

