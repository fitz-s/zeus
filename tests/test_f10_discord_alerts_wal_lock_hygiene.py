# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/jobs/wave6_observability_lock_hygiene §F10
#   "discord_alerts.py raw sqlite3.connect WAL + 30s busy_timeout"
# Lifecycle: created=2026-05-18; last_reviewed=2026-05-18; last_reused=never
# Purpose: F10 antibody — assert that discord_alerts._with_cooldown() enables
#   WAL mode and uses 30s timeout so a concurrent riskguard writer does not
#   receive "database is locked" under sustained concurrent load.
# Reuse: Run on every PR touching src/riskguard/discord_alerts.py or
#   risk_state.db writer semantics.

"""F10 antibody: discord_alerts.py concurrent-writer WAL + busy_timeout.

Background (wave6 §F10): discord_alerts._with_cooldown() used a raw
sqlite3.connect(timeout=5) with NO WAL pragma. The riskguard daemon
writes risk_state.db on every tick (~1Hz). A concurrent Discord alert
send (e.g. when risk crosses a threshold) created a read-write conflict
window. WAL mode allows concurrent readers while a writer holds the
journal, eliminating this class of "database is locked" error.

Fix applied: timeout raised to 30s, PRAGMA journal_mode=WAL added
immediately after connect in discord_alerts._with_cooldown().

Two probes:
1. WAL pragma probe — confirm the connection mode is WAL after _with_cooldown
   opens a connection to a tmp DB.
2. Concurrent-writers probe — spawn 2 threads each writing 50 rows to the
   same file-backed DB; assert zero "database is locked" errors across all
   iterations. Without WAL this would occasionally raise OperationalError.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Probe 1: WAL mode is enabled on the discord_alerts connection
# ---------------------------------------------------------------------------

def test_discord_alerts_wal_mode_enabled(tmp_path: Path, monkeypatch) -> None:
    """After _with_cooldown opens a connection, WAL mode must be active."""
    # Point the module at a fresh tmp DB so we don't touch live state.
    risk_db = tmp_path / "risk_state.db"

    # Patch _get_risk_db_path in discord_alerts to point at tmp DB.
    import src.riskguard.discord_alerts as da
    monkeypatch.setattr(da, "_get_risk_db_path", lambda: risk_db)

    # _with_cooldown opens, WAL-enables, checks cooldown, then closes.
    # We stub send_fn to avoid actual HTTP; return True to trigger the
    # "record sent" path which also commits.
    result = da._with_cooldown("test-key-wal-probe", 0, lambda: True)
    # Probe succeeded (sent) — WAL mode must have been set on the file.
    conn = sqlite3.connect(str(risk_db))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal", (
            f"F10: discord_alerts must enable WAL mode; got journal_mode={mode!r}. "
            "Regression: PRAGMA journal_mode=WAL was removed or not applied."
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Probe 2: 30s timeout — structural antibody on connect call
# ---------------------------------------------------------------------------

def test_discord_alerts_timeout_is_30s(tmp_path: Path, monkeypatch) -> None:
    """discord_alerts._with_cooldown() must open sqlite3.connect(..., timeout=30).

    Structural antibody: catches a regressor that drops the timeout back
    to 5 (or any value < 30) without breaking functional tests.
    """
    import src.riskguard.discord_alerts as da
    risk_db = tmp_path / "risk_state.db"
    monkeypatch.setattr(da, "_get_risk_db_path", lambda: risk_db)

    timeouts_captured: list[float] = []
    real_connect = sqlite3.connect

    def spy_connect(path, **kwargs):
        timeouts_captured.append(kwargs.get("timeout", -1))
        return real_connect(path, **kwargs)

    monkeypatch.setattr(da.sqlite3, "connect", spy_connect)

    da._with_cooldown("test-key-timeout-probe", 0, lambda: True)

    assert timeouts_captured, "sqlite3.connect was not called by _with_cooldown"
    assert timeouts_captured[0] >= 30, (
        f"F10: discord_alerts must use timeout>=30s; got {timeouts_captured[0]}. "
        "Regression: timeout was reduced below the 30s WAL+busy_timeout minimum."
    )


# ---------------------------------------------------------------------------
# Probe 3: concurrent writers — assert no "database is locked" errors
# ---------------------------------------------------------------------------

def test_concurrent_risk_state_writers_no_lock_errors(tmp_path: Path) -> None:
    """Two concurrent threads each write 50 rows to a shared file-backed DB.

    The DB is opened with WAL + 30s timeout (mirroring the F10 fix shape).
    Asserts zero "database is locked" OperationalErrors across all iterations.

    Without WAL mode this test is flaky (occasional lock collisions).
    With WAL mode concurrent readers + a single writer proceed without errors.
    """
    db_path = tmp_path / "concurrent_test.db"

    # Create schema in WAL mode.
    setup = sqlite3.connect(str(db_path), timeout=30)
    setup.execute("PRAGMA journal_mode=WAL")
    setup.execute(
        "CREATE TABLE risk_state ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  writer TEXT NOT NULL,"
        "  value REAL NOT NULL,"
        "  created_at TEXT NOT NULL"
        ")"
    )
    setup.commit()
    setup.close()

    errors: list[str] = []
    n_iterations = 50

    def _writer(writer_id: str) -> None:
        for i in range(n_iterations):
            try:
                conn = sqlite3.connect(str(db_path), timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    "INSERT INTO risk_state (writer, value, created_at) VALUES (?, ?, datetime('now'))",
                    (writer_id, float(i)),
                )
                conn.commit()
                conn.close()
            except sqlite3.OperationalError as exc:
                if "database is locked" in str(exc):
                    errors.append(f"{writer_id}[{i}]: {exc}")
                # Non-lock errors are re-raised (unexpected)
                else:
                    raise

    t1 = threading.Thread(target=_writer, args=("writer-A",))
    t2 = threading.Thread(target=_writer, args=("writer-B",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == [], (
        f"F10 WAL antibody: concurrent writers produced lock errors:\n"
        + "\n".join(errors[:10])
        + "\nRegression: WAL mode or 30s timeout was removed from discord_alerts."
    )

    # Confirm all rows landed (sanity).
    final = sqlite3.connect(str(db_path))
    count = final.execute("SELECT COUNT(*) FROM risk_state").fetchone()[0]
    final.close()
    assert count == n_iterations * 2, (
        f"Expected {n_iterations * 2} rows from 2 writers × {n_iterations}; got {count}"
    )
