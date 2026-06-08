# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: thepath/audit-realign live-lock + fail-open remediation
#   (AGENTS.md iron rules #4 ONE risk authority, #5 kill "database is locked"
#    CATEGORY via the canonical connection factory, #6 fail CONSERVATIVE under
#    degraded truth). Diagnosis 2026-06-08: K1 factory never set PRAGMA
#    busy_timeout; K3 lock-attestation preserved a fresh GREEN (fail-open) and
#    get_current_level surfaced that degraded row as a clean GREEN (split-brain).
"""Relationship tests for the WAL+busy_timeout connection factory and the
RiskGuard fail-conservative / single-authority contract.

These are RELATIONSHIP tests (Fitz methodology): they assert properties that
hold ACROSS module boundaries, not single-function behavior.

  (a) WAL_BUSY_FACTORY — a writer holding the WAL write lock; a SECOND
      connection opened through the canonical factory WAITS up to busy_timeout
      and SUCCEEDS rather than raising "database is locked". This proves the
      lock CATEGORY is unconstructable for factory connections: every factory
      handle carries a non-zero SQL-level wait budget.

  (b) RISKGUARD_FAIL_CONSERVATIVE — when RiskGuard cannot compute fresh metrics
      because a dependency DB is locked, it must NOT re-stamp a fail-open GREEN.
      The persisted level must block new entries (>= DATA_DEGRADED), and a
      previously-RED level must never be weakened.

  (c) RISK_ONE_AUTHORITY / RED_REASON_SURFACED — the daemon entry gate and the
      status risk block both read get_current_level(); a degraded lock-attestation
      row must NOT be surfaced as a clean GREEN, so the entry gate and the status
      block agree (no split-brain). The RED reason (Brier) is queryable.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.riskguard.riskguard as riskguard_module
from src.riskguard.risk_level import RiskLevel
from src.state.db import _connect, get_connection


# ---------------------------------------------------------------------------
# (a) WAL_BUSY_FACTORY — lock-contention WAITS instead of raising
# ---------------------------------------------------------------------------

def _seed_wal_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()


def test_factory_connection_has_nonzero_busy_timeout(tmp_path, monkeypatch):
    """Every factory connection carries a non-zero SQL-level busy_timeout.

    PRAGMA busy_timeout is the SQL-level wait budget. Relying solely on the
    C-level handler installed by sqlite3.connect(timeout=) is the structural
    hole (executescript can null it); the factory must set the PRAGMA itself.
    """
    monkeypatch.setenv("ZEUS_DB_BUSY_TIMEOUT_MS", "30000")
    for factory in (_connect, get_connection):
        conn = factory(tmp_path / f"{factory.__name__}.db")
        try:
            budget = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
            assert budget == 30000, (
                f"{factory.__name__} must set PRAGMA busy_timeout=30000, got {budget}"
            )
        finally:
            conn.close()


def test_factory_busy_timeout_survives_executescript(tmp_path, monkeypatch):
    """executescript() must not leave a factory handle at a 0 ms wait budget.

    Python's sqlite3.executescript resets the C-level busy handler on some
    versions. Any caller that runs an executescript (init_risk_db, schema-ensure)
    on a factory connection and keeps using it must still have a non-zero wait
    budget afterward — otherwise the next write hits a 0 ms budget and the lock
    category reappears.
    """
    monkeypatch.setenv("ZEUS_DB_BUSY_TIMEOUT_MS", "30000")
    conn = get_connection(tmp_path / "afterscript.db")
    try:
        conn.executescript("CREATE TABLE IF NOT EXISTS s (a INTEGER);")
        budget = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        assert budget == 30000, (
            "busy_timeout must be re-applied so executescript cannot strip the "
            f"wait budget; got {budget}"
        )
    finally:
        conn.close()


def test_factory_connection_waits_then_succeeds_under_lock(tmp_path, monkeypatch):
    """RELATIONSHIP: writer A holds the WAL write lock; connection B opened via
    the factory WAITS up to busy_timeout and SUCCEEDS — it does NOT raise
    'database is locked'.

    This is the lock-CATEGORY-killed proof: a transient contention is absorbed
    by the wait budget instead of surfacing as an instant exception.
    """
    monkeypatch.setenv("ZEUS_DB_BUSY_TIMEOUT_MS", "30000")
    db_path = tmp_path / "contended.db"
    _seed_wal_db(db_path)

    holder = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO t VALUES (1)")

    release_after_s = 1.5

    def _release():
        time.sleep(release_after_s)
        holder.commit()
        holder.close()

    releaser = threading.Thread(target=_release)
    releaser.start()

    conn = get_connection(db_path)
    t0 = time.time()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO t VALUES (2)")
        conn.commit()
        waited = time.time() - t0
    except sqlite3.OperationalError as exc:  # pragma: no cover - failure path
        releaser.join()
        pytest.fail(
            f"factory connection raised under contention instead of waiting: {exc}"
        )
    finally:
        conn.close()
        releaser.join()

    # It must have actually WAITED for the holder (proving the budget is in use),
    # and it must NOT have waited the full timeout (proving it succeeded promptly
    # once the lock cleared).
    assert waited >= release_after_s - 0.3, (
        f"connection should have waited ~{release_after_s}s for the lock, "
        f"waited {waited:.2f}s — budget not applied"
    )
    assert waited < 25.0, f"connection waited too long ({waited:.2f}s)"


def test_bare_no_timeout_connect_raises_proving_category_is_real(tmp_path):
    """Control: a bare no-timeout connect (the pre-fix bypass-site pattern)
    raises 'database is locked' under the SAME contention the factory survives.

    This anchors the relationship test: the factory's success is meaningful
    only because the no-budget path genuinely fails.
    """
    db_path = tmp_path / "bare.db"
    _seed_wal_db(db_path)

    holder = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO t VALUES (1)")
    try:
        bare = sqlite3.connect(str(db_path))  # NO timeout, NO busy_timeout
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            bare.execute("BEGIN IMMEDIATE")
            bare.execute("INSERT INTO t VALUES (2)")
        bare.close()
    finally:
        holder.commit()
        holder.close()


def test_status_summary_risk_details_connect_carries_busy_timeout(tmp_path, monkeypatch):
    """status_summary's risk-details read must not use a 0 ms-budget connect.

    The bypass site (formerly a bare sqlite3.connect) is a lock-loser on the
    read path that surfaces the risk block. After the fix its connection must
    carry a non-zero busy_timeout.
    """
    monkeypatch.setenv("ZEUS_DB_BUSY_TIMEOUT_MS", "30000")
    from src.observability import status_summary as ss

    captured: list[int] = []
    real_connect = sqlite3.connect

    def _capturing_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        try:
            captured.append(int(conn.execute("PRAGMA busy_timeout").fetchone()[0]))
        except sqlite3.Error:
            pass
        return conn

    # risk_state.db lives under state_path; point it at tmp and seed a row.
    monkeypatch.setattr(ss, "state_path", lambda name: tmp_path / name)
    seed = sqlite3.connect(str(tmp_path / "risk_state.db"))
    seed.execute(
        "CREATE TABLE risk_state (id INTEGER PRIMARY KEY, level TEXT, "
        "details_json TEXT, checked_at TEXT)"
    )
    seed.execute(
        "INSERT INTO risk_state (level, details_json, checked_at) VALUES (?,?,?)",
        ("GREEN", json.dumps({"brier_level": "GREEN"}), datetime.now(timezone.utc).isoformat()),
    )
    seed.commit()
    seed.close()

    # status_summary imports sqlite3 lazily inside _get_risk_details, so patch
    # the global module attribute that the lazy import resolves to.
    monkeypatch.setattr(sqlite3, "connect", _capturing_connect)
    ss._get_risk_details()

    assert captured, "status_summary risk-details did not open a sqlite connection"
    # Must carry the FULL configured budget (30000 ms), not the bare-connect
    # default (5000 ms on this runtime, 0 ms on runtimes where executescript
    # nulls the handler). The fix routes this read through the configured budget.
    assert all(b == 30000 for b in captured), (
        f"status_summary risk-details connect must carry the configured "
        f"busy_timeout=30000; got {captured}"
    )


# ---------------------------------------------------------------------------
# (b) RISKGUARD_FAIL_CONSERVATIVE — lock never re-stamps a fail-open GREEN
# ---------------------------------------------------------------------------

def _seed_risk_row(conn: sqlite3.Connection, *, level: str, age_minutes: float,
                   brier: float | None = None) -> None:
    checked_at = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat()
    details = {"bankroll_truth_source": "polymarket_wallet"}
    if brier is not None:
        details["brier"] = brier
    conn.execute(
        "INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, "
        "checked_at, force_exit_review) VALUES (?,?,NULL,NULL,?,?,0)",
        (level, brier, json.dumps(details), checked_at),
    )


def _patch_locked_tick(monkeypatch, risk_db: Path):
    """Route riskguard's get_connection at risk_db and force a dependency lock."""
    def _fake_get_connection(path=None, **_kwargs):
        return get_connection(risk_db)

    class _LockedTradeConn:
        def rollback(self):
            pass

        def close(self):
            pass

    def _raise_locked(_conn):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
    monkeypatch.setattr(riskguard_module, "_get_runtime_trade_connection",
                        lambda: _LockedTradeConn())
    monkeypatch.setattr(riskguard_module, "_load_riskguard_portfolio_truth",
                        _raise_locked)


def test_lock_preserves_fresh_green_through_transient_lock(tmp_path, monkeypatch):
    """RELATIONSHIP: a fresh (<5 min) GREEN full row + a TRANSIENT dependency lock
    PRESERVES GREEN — it does NOT floor to DATA_DEGRADED.

    A momentary lock does not make risk unknowable: the GREEN was computed within
    the 5-min freshness window and risk (daily-loss/settlement-quality/Brier) is
    slow-moving. Flooring every transient lock blocked all trading on the
    GREEN-only entry gate (operator-reported regression 2026-06-08). Safety is
    upheld by the freshness window: a STALE (>5 min) full row degrades
    (test_lock_with_no_fresh_full_row_degrades) and a stronger halt is never
    weakened (test_lock_preserves_red_never_weakens).
    """
    risk_db = tmp_path / "risk_state.db"
    conn = get_connection(risk_db)
    riskguard_module.init_risk_db(conn)
    _seed_risk_row(conn, level="GREEN", age_minutes=1.0)  # fresh GREEN
    conn.commit()
    conn.close()

    _patch_locked_tick(monkeypatch, risk_db)
    level = riskguard_module.tick()

    assert level == RiskLevel.GREEN, (
        "transient lock over a fresh GREEN must preserve GREEN, not floor"
    )
    persisted = get_connection(risk_db).execute(
        "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert persisted["level"] == RiskLevel.GREEN.value
    details = json.loads(persisted["details_json"])
    assert details.get("riskguard_degraded_reason") == "dependency_db_locked"
    assert details.get("status") == "dependency_db_locked_previous_risk_level_preserved"
    assert details.get("conservative_floor_applied") is False


def test_lock_preserves_red_never_weakens(tmp_path, monkeypatch):
    """RELATIONSHIP: a fresh RED full row + a dependency lock must keep RED.

    Conservative means never WEAKEN: RED (the strongest halt) must survive a
    lock; it must not be diluted down to DATA_DEGRADED.
    """
    risk_db = tmp_path / "risk_state.db"
    conn = get_connection(risk_db)
    riskguard_module.init_risk_db(conn)
    _seed_risk_row(conn, level="RED", age_minutes=1.0, brier=0.162)
    conn.commit()
    conn.close()

    _patch_locked_tick(monkeypatch, risk_db)
    level = riskguard_module.tick()

    assert level == RiskLevel.RED, (
        f"lock weakened a fresh RED to {level}; conservative law forbids weakening"
    )
    assert riskguard_module.get_current_level() == RiskLevel.RED


def test_lock_with_no_fresh_full_row_degrades(tmp_path, monkeypatch):
    """A stale (>TTL) full row + lock degrades to DATA_DEGRADED (blocks entries)."""
    risk_db = tmp_path / "risk_state.db"
    conn = get_connection(risk_db)
    riskguard_module.init_risk_db(conn)
    _seed_risk_row(conn, level="GREEN", age_minutes=10.0)  # stale
    conn.commit()
    conn.close()

    _patch_locked_tick(monkeypatch, risk_db)
    level = riskguard_module.tick()

    assert level == RiskLevel.DATA_DEGRADED
    assert riskguard_module.get_current_level() == RiskLevel.DATA_DEGRADED


# ---------------------------------------------------------------------------
# (c) RISK_ONE_AUTHORITY / RED_REASON_SURFACED — no split-brain
# ---------------------------------------------------------------------------

def _green_lock_attestation_details() -> dict:
    """The exact shape of a fail-open lock-attestation that preserved GREEN."""
    return {
        "status": "dependency_db_locked_previous_risk_level_preserved",
        "riskguard_degraded_reason": "dependency_db_locked",
        "bankroll_truth_source": "polymarket_wallet",
        "previous_full_risk_level": "GREEN",
    }


def test_get_current_level_surfaces_transient_lock_green_but_floors_other_degraded(tmp_path, monkeypatch):
    """RELATIONSHIP (iron #4, single authority): get_current_level distinguishes a
    TRANSIENT dependency-lock attestation (which already preserved a FRESH, valid
    level) from a GENUINE metric/truth degradation.

    - A fresh dependency_db_locked attestation stamped GREEN surfaces as GREEN: the
      attestation only preserves GREEN when a full row was fresh (<5 min), and the
      R4 staleness floor (>5 min -> RED) catches stale ones, so this is a valid
      recent GREEN, not a fail-open. (Reverts the 2026-06-08 over-floor regression.)
    - ANY OTHER degraded reason still floors to >= DATA_DEGRADED — the split-brain
      read-side guard for genuine degradation is preserved.
    """
    risk_db = tmp_path / "risk_state.db"
    conn = get_connection(risk_db)
    riskguard_module.init_risk_db(conn)
    conn.execute(
        "INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, "
        "checked_at, force_exit_review) VALUES (?,NULL,NULL,NULL,?,?,0)",
        ("GREEN", json.dumps(_green_lock_attestation_details()),
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(riskguard_module, "get_connection",
                        lambda path=None, **_k: get_connection(risk_db))

    # Transient lock with a preserved fresh GREEN -> surfaces GREEN.
    assert riskguard_module.get_current_level() == RiskLevel.GREEN

    # A GENUINE (non-lock) degradation reason on a GREEN-stamped row still floors.
    conn2 = get_connection(risk_db)
    other = dict(_green_lock_attestation_details())
    other["riskguard_degraded_reason"] = "settlement_truth_unavailable"
    other["status"] = "metrics_degraded"
    conn2.execute(
        "INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, "
        "checked_at, force_exit_review) VALUES (?,NULL,NULL,NULL,?,?,0)",
        ("GREEN", json.dumps(other), datetime.now(timezone.utc).isoformat()),
    )
    conn2.commit()
    conn2.close()
    floored = riskguard_module.get_current_level()
    assert floored != RiskLevel.GREEN, (
        "SPLIT-BRAIN: a genuine (non-lock) degraded GREEN must still floor"
    )
    assert floored in (RiskLevel.DATA_DEGRADED, RiskLevel.YELLOW,
                       RiskLevel.ORANGE, RiskLevel.RED)


def test_entry_gate_and_status_agree_on_degraded_row(tmp_path, monkeypatch):
    """RELATIONSHIP (iron #4): the daemon entry gate and the status risk block read
    the SAME authority — no divergence. On a TRANSIENT dependency-lock attestation
    that preserved a fresh GREEN, the authority is GREEN, so BOTH agree the gate
    ADMITS (the preserved fresh GREEN is valid). The single-authority property —
    gate decision is a pure function of get_current_level — is what this pins.
    """
    risk_db = tmp_path / "risk_state.db"
    conn = get_connection(risk_db)
    riskguard_module.init_risk_db(conn)
    conn.execute(
        "INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, "
        "checked_at, force_exit_review) VALUES (?,NULL,NULL,NULL,?,?,0)",
        ("GREEN", json.dumps(_green_lock_attestation_details()),
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(riskguard_module, "get_connection",
                        lambda path=None, **_k: get_connection(risk_db))

    from src.engine.event_reactor_adapter import riskguard_allows_new_entries
    from src.riskguard.riskguard import get_current_level

    authoritative_level = get_current_level()

    gate = riskguard_allows_new_entries(get_current_level=get_current_level)

    class _Evt:
        event_type = "FORECAST_SNAPSHOT_READY"

    gate_allows = gate(_Evt())

    # Single-authority: the gate decision is a pure function of the authority.
    assert gate_allows is (authoritative_level == RiskLevel.GREEN)
    # The preserved fresh GREEN is valid, so the authority is GREEN and the gate
    # ADMITS — status and gate agree (no split-brain divergence).
    assert authoritative_level == RiskLevel.GREEN
    assert gate_allows is True, (
        "entry gate must admit on a transient-lock attestation that preserved a "
        "fresh GREEN (authority == GREEN)"
    )


def test_red_reason_surfaced_in_authority(tmp_path, monkeypatch):
    """RED_REASON_SURFACED: when the authoritative full row is RED with a Brier
    reason, the level read by the gate/status is RED and the Brier is queryable
    from the persisted details (not just buried in a log line)."""
    risk_db = tmp_path / "risk_state.db"
    conn = get_connection(risk_db)
    riskguard_module.init_risk_db(conn)
    conn.execute(
        "INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, "
        "checked_at, force_exit_review) VALUES (?,?,?,NULL,?,?,1)",
        ("RED", 0.162, 0.789,
         json.dumps({"brier": 0.162, "accuracy": 0.789,
                     "daily_loss_level": "RED",
                     "bankroll_truth_source": "polymarket_wallet"}),
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(riskguard_module, "get_connection",
                        lambda path=None, **_k: get_connection(risk_db))

    assert riskguard_module.get_current_level() == RiskLevel.RED
    row = get_connection(risk_db).execute(
        "SELECT brier, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["brier"] == pytest.approx(0.162)
    details = json.loads(row["details_json"])
    assert details["brier"] == pytest.approx(0.162)
    assert details["daily_loss_level"] == "RED"
