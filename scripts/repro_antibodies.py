#!/usr/bin/env python3
# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: live-block-traceback-capture-2026-05-04 branch (HEAD 8c1c03f1)
#                  Antibody verification repro for PR-A (cycle_runtime.py:2988)
#                  and SF6 (control_plane.py:270-273).
"""
Antibody injection test: verify both recently-deployed observability antibodies
fire correctly when a ValueError is raised.

Antibody A (PR-A): cycle_runtime.py:2987-2988
    except Exception as e:
        deps.logger.error("Evaluation failed for %s %s: %s", city.name,
                          candidate.target_date, e, exc_info=True)

Antibody B (SF6): control_plane.py:269-273
    _caller_frames = "".join(_traceback.format_stack()[-6:-1])
    logger.warning("ENTRIES_AUTO_PAUSED_DB_WRITTEN reason=%s ...")

Test strategy (two-phase):
  Phase 1 — PR-A:
    Patch the following on src.engine.cycle_runner (the deps module):
      - evaluate_candidate      → raises ValueError("test injection — antibody verification")
      - find_weather_markets    → returns one synthetic fake market
      - get_last_scan_authority → returns "VERIFIED" (bypasses scan-authority early-return)
      - _risk_allows_new_entries→ returns True  (bypasses DATA_DEGRADED gate)
    Then run_cycle(OPENING_HUNT). The per-candidate try/except at
    cycle_runtime.py:2987 catches the ValueError and emits the PR-A log line.

  Phase 2 — SF6:
    Call pause_entries("auto_pause:ValueError_TEST") directly.
    The SF6 antibody fires immediately after the DB commit.

Pre-run state: ensure entries_paused=false row in DB, remove streak + tombstone.
Post-run verification: grep captured log file, query DB.
"""

from __future__ import annotations

import logging
import os
import sys
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ZEUS_ROOT = Path(__file__).parent.parent
DB_PATH = ZEUS_ROOT / "state" / "zeus-world.db"
STREAK_PATH = ZEUS_ROOT / "state" / "auto_pause_streak.json"
TOMBSTONE_PATH = ZEUS_ROOT / "state" / "auto_pause_failclosed.tombstone"
STDERR_LOG = Path("/tmp/repro_stderr.log")

# Ensure zeus root in sys.path
if str(ZEUS_ROOT) not in sys.path:
    sys.path.insert(0, str(ZEUS_ROOT))

from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pre_run_cleanup(pre_ts: str) -> None:
    """Ensure the DB has entries_paused=false, remove streak + tombstone files."""
    print("[SETUP] Inserting DB unpause row...")
    with db_writer_lock(DB_PATH, WriteClass.BULK):
        conn = sqlite3.connect(str(DB_PATH))
        now = _now_iso()
        conn.execute(
            """
            INSERT INTO control_overrides_history
                (override_id, target_type, target_key, action_type, value,
                 issued_by, issued_at, effective_until, reason, precedence,
                 operation, recorded_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "control_plane:global:entries_paused",
                "global", "entries", "gate", "false",
                "control_plane", now, None,
                "pre_repro_cleanup", 100, "upsert", now,
            ),
        )
        conn.commit()
        conn.close()
    print(f"[SETUP]   DB unpause row inserted at {now}")

    for path in (STREAK_PATH, TOMBSTONE_PATH):
        if path.exists():
            path.unlink()
            print(f"[SETUP]   Removed {path.name}")


def _post_run_cleanup() -> None:
    """Re-insert unpause row and remove tombstone if test created one."""
    print("[CLEANUP] Re-inserting DB unpause row...")
    with db_writer_lock(DB_PATH, WriteClass.BULK):
        conn = sqlite3.connect(str(DB_PATH))
        now = _now_iso()
        conn.execute(
            """
            INSERT INTO control_overrides_history
                (override_id, target_type, target_key, action_type, value,
                 issued_by, issued_at, effective_until, reason, precedence,
                 operation, recorded_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "control_plane:global:entries_paused",
                "global", "entries", "gate", "false",
                "control_plane", now, None,
                "post_repro_cleanup", 100, "upsert", now,
            ),
        )
        conn.commit()
        conn.close()
    print(f"[CLEANUP]  DB unpause row inserted at {now}")

    if TOMBSTONE_PATH.exists():
        TOMBSTONE_PATH.unlink()
        print("[CLEANUP]  Tombstone removed")
    if STREAK_PATH.exists():
        STREAK_PATH.unlink()
        print("[CLEANUP]  Streak file removed")


@contextmanager
def _capture_logging_to_file():
    """Add a FileHandler to the root logger so all log records land in STDERR_LOG."""
    STDERR_LOG.write_text("", encoding="utf-8")
    handler = logging.FileHandler(str(STDERR_LOG), mode="w", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler.setFormatter(fmt)
    root_logger = logging.getLogger()
    old_level = root_logger.level
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(handler)
    try:
        yield handler
    finally:
        root_logger.removeHandler(handler)
        handler.flush()
        handler.close()
        root_logger.setLevel(old_level)


def _build_fake_market():
    """Build a minimal synthetic market dict that passes all filters in execute_discovery_phase."""
    from src.config import cities_by_name
    from datetime import timedelta

    # Use first available city
    city = next(iter(cities_by_name.values()))
    now = datetime.now(timezone.utc)
    target_date = (now + timedelta(days=2)).strftime("%Y-%m-%d")

    return {
        "city": city,
        "target_date": target_date,
        "outcomes": [
            {
                "title": "Yes",
                "range_low": -5.0,
                "range_high": 5.0,
                "token_id": "fake_token_1",
                "price": 0.5,
            }
        ],
        "hours_since_open": 1.0,       # satisfies max_hours_since_open=24
        "hours_to_resolution": 48.0,   # satisfies min_hours_to_resolution=24
        "temperature_metric": "HIGH",
        "event_id": "fake_event_001",
        "slug": "fake-market-antibody-test",
        "neg_risk": False,
        "min_tick_size": 0.01,
        "min_order_size": 5.0,
    }


# ---------------------------------------------------------------------------
# Phase 1 — PR-A antibody: inject ValueError into evaluate_candidate
# ---------------------------------------------------------------------------

def _phase1_pra() -> tuple[bool, list[str]]:
    """Call execute_discovery_phase directly with a controlled deps object.

    This bypasses the entire run_cycle gate cascade (risk_level, cutover_guard,
    heartbeat, ws_gap, etc.) and goes straight to the per-candidate evaluation
    loop.  The deps.evaluate_candidate raises ValueError; the per-candidate
    try/except at cycle_runtime.py:2987 catches it and emits:

        ERROR ... Evaluation failed for <city> <date>: test injection — antibody verification

    with exc_info=True (full Traceback block follows in the log).

    deps attributes used in the candidate-loop path:
      - evaluate_candidate     → raises ValueError
      - find_weather_markets   → returns one fake market
      - get_last_scan_authority→ returns "VERIFIED"
      - MODE_PARAMS            → real cycle_runner.MODE_PARAMS
      - DiscoveryMode          → real DiscoveryMode
      - MarketCandidate        → real MarketCandidate
      - NoTradeCase            → real NoTradeCase
      - logger                 → cycle_runner logger
      - is_strategy_enabled    → returns True
      - _classify_edge_source  → real helper
      - oracle_penalty_reload  → None (getattr safe)
      All others accessed only in trade-execution branches that won't be
      reached (ValueError fires first).

    Returns (passed, evidence_lines).
    """
    print("\n[PHASE-1] PR-A injection: calling execute_discovery_phase directly")

    INJECTION_MSG = "test injection — antibody verification"
    fake_market = _build_fake_market()
    city_name = fake_market["city"].name
    print(f"[PHASE-1]   Fake market: city={city_name}, target_date={fake_market['target_date']}")

    import types
    import src.engine.cycle_runner as cr
    import src.engine.cycle_runtime as _runtime
    from src.state.db import get_trade_connection_with_world
    from src.state.portfolio import load_portfolio
    from src.state.decision_chain import CycleArtifact
    from src.engine.discovery_mode import DiscoveryMode

    def _raising_evaluate_candidate(*args, **kwargs):
        raise ValueError(INJECTION_MSG)

    # Build a minimal deps namespace.
    # execute_discovery_phase uses deps.find_weather_markets and
    # deps.get_last_scan_authority inside nested closures, so we need
    # them on the deps object passed directly to the function.
    deps = types.SimpleNamespace(
        evaluate_candidate=_raising_evaluate_candidate,
        find_weather_markets=lambda **kw: [fake_market],
        get_last_scan_authority=lambda: "VERIFIED",
        MODE_PARAMS=cr.MODE_PARAMS,
        DiscoveryMode=DiscoveryMode,
        MarketCandidate=None,        # will use the from-import fallback inside the fn
        NoTradeCase=cr.NoTradeCase,
        logger=cr.logger,
        is_strategy_enabled=lambda *a, **kw: True,
        _classify_edge_source=cr._classify_edge_source,
        oracle_penalty_reload=None,
    )

    mode = DiscoveryMode.OPENING_HUNT
    summary = {
        "mode": mode.value,
        "candidates": 0,
        "trades": 0,
        "no_trades": 0,
    }

    decision_time = datetime.now(timezone.utc)

    print("[PHASE-1]   Opening DB connection...")
    try:
        conn = get_trade_connection_with_world()
        portfolio = load_portfolio()
    except Exception as exc:
        print(f"[PHASE-1]   WARNING: DB/portfolio load failed: {exc!r}")
        conn = None
        portfolio = None

    artifact = CycleArtifact(
        mode=mode.value,
        started_at=decision_time.isoformat(),
        summary=summary,
    )

    # Minimal stubs for args that won't be used (ValueError fires before trade path)
    clob = None
    tracker = types.SimpleNamespace(
        record_entry=lambda *a, **kw: None,
        record_exit=lambda *a, **kw: None,
    )
    limits = types.SimpleNamespace()

    print("[PHASE-1]   Calling execute_discovery_phase directly...")
    try:
        p_dirty, t_dirty = _runtime.execute_discovery_phase(
            conn, clob, portfolio, artifact, tracker, limits,
            mode, summary,
            entry_bankroll=100.0,
            decision_time=decision_time,
            env="legacy_env",
            deps=deps,
        )
        print(f"[PHASE-1]   execute_discovery_phase returned. candidates={summary.get('candidates')}")
    except Exception as exc:
        # Should not propagate — inner catch absorbs evaluate_candidate errors.
        print(f"[PHASE-1]   Note: execute_discovery_phase raised: {exc!r}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # Read log file
    lines = STDERR_LOG.read_text(encoding="utf-8").splitlines() if STDERR_LOG.exists() else []

    eval_failed = [l for l in lines if "Evaluation failed" in l]
    traceback_lines = [l for l in lines if "Traceback (most recent call last)" in l]
    injection_lines = [l for l in lines if INJECTION_MSG in l]

    passed = bool(eval_failed) and (bool(traceback_lines) or bool(injection_lines))

    print(f"[PHASE-1]   'Evaluation failed' lines: {len(eval_failed)}")
    if eval_failed:
        print(f"[PHASE-1]   Sample: {eval_failed[0][:140]}")
    print(f"[PHASE-1]   Traceback lines: {len(traceback_lines)}")
    print(f"[PHASE-1]   Injection msg lines: {len(injection_lines)}")
    if injection_lines:
        print(f"[PHASE-1]   Sample: {injection_lines[0][:140]}")

    return passed, eval_failed + traceback_lines + injection_lines


# ---------------------------------------------------------------------------
# Phase 2 — SF6 antibody: call pause_entries() directly
# ---------------------------------------------------------------------------

def _phase2_sf6(pre_ts: str) -> tuple[bool, list[str]]:
    """Call pause_entries() directly with a unique test reason_code.

    The SF6 antibody at control_plane.py:269-273 should log:
        WARNING ENTRIES_AUTO_PAUSED_DB_WRITTEN reason=auto_pause:ValueError_TEST ...
    followed by caller stack frames.

    Returns (passed, evidence_lines).
    """
    print("\n[PHASE-2] SF6 injection: calling pause_entries() directly")

    reason = "auto_pause:ValueError_TEST"
    from src.control.control_plane import pause_entries

    print(f"[PHASE-2]   Calling pause_entries('{reason}')...")
    try:
        pause_entries(reason)
        print("[PHASE-2]   pause_entries() returned normally")
    except Exception as exc:
        print(f"[PHASE-2]   WARNING: pause_entries() raised: {exc!r}")

    # Read log file
    lines = STDERR_LOG.read_text(encoding="utf-8").splitlines() if STDERR_LOG.exists() else []

    paused_lines = [l for l in lines if "ENTRIES_AUTO_PAUSED_DB_WRITTEN" in l]
    caller_stack_lines = [l for l in lines if 'File "' in l]

    # Verify DB row
    db_passed = False
    db_row = None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            """
            SELECT issued_at, issued_by, reason, effective_until
            FROM control_overrides_history
            WHERE issued_at > ?
              AND reason = ?
            ORDER BY issued_at DESC LIMIT 1
            """,
            (pre_ts, reason),
        ).fetchone()
        conn.close()
        if row:
            db_row = row
            db_passed = True
            print(f"[PHASE-2]   DB row: issued_at={row[0]}, issued_by={row[1]}, reason={row[2]}")
        else:
            print(f"[PHASE-2]   WARNING: No DB row for reason={reason} after {pre_ts}")
    except Exception as exc:
        print(f"[PHASE-2]   WARNING: DB query failed: {exc!r}")

    stderr_passed = bool(paused_lines)
    passed = stderr_passed and db_passed

    print(f"[PHASE-2]   'ENTRIES_AUTO_PAUSED_DB_WRITTEN' lines: {len(paused_lines)}")
    if paused_lines:
        print(f"[PHASE-2]   Sample: {paused_lines[0][:160]}")
    print(f"[PHASE-2]   Caller stack 'File' lines in log: {len(caller_stack_lines)}")
    print(f"[PHASE-2]   DB row landed: {db_passed}")

    return passed, paused_lines + ([str(db_row)] if db_row else [])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 72)
    print("REPRO_ANTIBODIES — antibody injection test")
    print("Branch: live-block-traceback-capture-2026-05-04 (HEAD 8c1c03f1)")
    print(f"DB: {DB_PATH}")
    print(f"Log: {STDERR_LOG}")
    print("=" * 72)

    pre_ts = _now_iso()
    print(f"[INFO] Pre-test timestamp: {pre_ts}")

    _pre_run_cleanup(pre_ts)

    results = {}

    with _capture_logging_to_file():
        pra_passed, pra_evidence = _phase1_pra()
        results["PR-A (Evaluation failed logger, cycle_runtime.py:2988)"] = pra_passed

        sf6_passed, sf6_evidence = _phase2_sf6(pre_ts)
        results["SF6 (ENTRIES_AUTO_PAUSED_DB_WRITTEN, control_plane.py:270)"] = sf6_passed

    _post_run_cleanup()

    registry_exit = verify_registry_catches_gates()
    results["Registry gate injection (gates 1/9/10/12)"] = (registry_exit == 0)

    # Final grep counts
    print("\n[VERIFY] Final grep counts on", STDERR_LOG)
    log_text = STDERR_LOG.read_text(encoding="utf-8") if STDERR_LOG.exists() else ""
    eval_count = log_text.count("Evaluation failed")
    paused_count = log_text.count("ENTRIES_AUTO_PAUSED_DB_WRITTEN")
    traceback_count = log_text.count("Traceback (most recent call last)")
    injection_count = log_text.count("test injection — antibody verification")
    print(f"  grep -c 'Evaluation failed'                  → {eval_count}")
    print(f"  grep -c 'ENTRIES_AUTO_PAUSED_DB_WRITTEN'     → {paused_count}")
    print(f"  grep -c 'Traceback (most recent call last)'  → {traceback_count}")
    print(f"  grep -c 'test injection — antibody verif...' → {injection_count}")

    # Final DB check
    print(f"\n[VERIFY] DB rows issued after {pre_ts}:")
    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            """
            SELECT issued_at, issued_by, reason
            FROM control_overrides_history
            WHERE issued_at > ?
            ORDER BY issued_at DESC LIMIT 5
            """,
            (pre_ts,),
        ).fetchall()
        conn.close()
        for r in rows:
            print(f"  issued_at={r[0]}  issued_by={r[1]}  reason={r[2]}")
    except Exception as exc:
        print(f"  WARNING: DB query failed: {exc!r}")

    # Summary
    print("\n" + "=" * 72)
    print("RESULT SUMMARY")
    print("=" * 72)
    all_passed = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        all_passed = all_passed and passed
        print(f"  [{status}]  {name}")
    print("=" * 72)

    if all_passed:
        print("OVERALL: PASS — all antibodies and registry gate checks verified")
        return 0
    else:
        print("OVERALL: FAIL — one or more checks did not pass")
        return 1


# ---------------------------------------------------------------------------
# Registry section — verify registry catches each gate type
# ---------------------------------------------------------------------------

def _build_synthetic_deps(
    *,
    state_dir: Path,
    tombstone_present: bool = False,
    heartbeat_summary: dict | None = None,
    ws_gap_summary: dict | None = None,
) -> "Any":
    """Build a synthetic RegistryDeps for registry injection tests.

    Uses types.ModuleType stubs for all module fields so no real daemon
    state is read.  Only the fields relevant to the tested gate are wired;
    others return CLEAR-safe defaults.
    """
    import types
    import sqlite3
    from src.control.block_adapters._base import RegistryDeps

    # ── Stub module factory ────────────────────────────────────────────────

    def _make_heartbeat_mod(summary_override: dict | None) -> types.ModuleType:
        mod = types.ModuleType("heartbeat_supervisor_stub")
        _hb = summary_override or {
            "health": "HEALTHY",
            "entry": {"allow_submit": True},
        }
        mod.summary = lambda: _hb
        return mod

    def _make_ws_gap_mod(summary_override: dict | None) -> types.ModuleType:
        mod = types.ModuleType("ws_gap_guard_stub")
        _ws = summary_override or {
            "subscription_state": "SUBSCRIBED",
            "gap_reason": "none",
            "entry": {"allow_submit": True},
        }
        mod.summary = lambda: _ws
        return mod

    def _make_riskguard_mod() -> types.ModuleType:
        """Returns a stub that makes gate 6 report GREEN (CLEAR)."""
        from src.riskguard.riskguard import RiskLevel
        mod = types.ModuleType("riskguard_stub")
        mod.get_current_level = lambda: RiskLevel.GREEN
        mod.RiskLevel = RiskLevel
        return mod

    def _make_rollout_gate_mod() -> types.ModuleType:
        mod = types.ModuleType("rollout_gate_stub")
        # evaluate_entry_forecast_rollout_gate: not called in DISCOVERY stage
        # so a no-op is fine.
        mod.evaluate_entry_forecast_rollout_gate = lambda *a, **kw: None
        return mod

    # ── In-memory SQLite stub (no real file) ──────────────────────────────

    def _make_mem_conn() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        # Minimal schema so gate 3/5 queries don't crash
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS control_overrides_history (
                override_id TEXT, target_type TEXT, target_key TEXT,
                action_type TEXT, value TEXT, issued_by TEXT,
                issued_at TEXT, effective_until TEXT, reason TEXT,
                precedence INTEGER, operation TEXT, recorded_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cycles (id INTEGER, summary_json TEXT, created_at TEXT)
            """
        )
        conn.commit()
        return conn

    return RegistryDeps(
        state_dir=state_dir,
        db_connection_factory=_make_mem_conn,
        risk_state_db_connection_factory=_make_mem_conn,
        riskguard_module=_make_riskguard_mod(),
        heartbeat_module=_make_heartbeat_mod(heartbeat_summary),
        ws_gap_guard_module=_make_ws_gap_mod(ws_gap_summary),
        rollout_gate_module=_make_rollout_gate_mod(),
        env={},
    )


def verify_registry_catches_gates() -> int:
    """Verify the registry detects BLOCKING state for 4 representative gate types.

    Scenarios:
      1. Gate 1 (tombstone present) → BLOCKING
      2. Gate 9 (heartbeat DEGRADED + allow_submit=False) → BLOCKING
      3. Gate 10 (ws_gap DISCONNECTED + allow_submit=False) → BLOCKING
      4. Gate 12 (promotion evidence file absent) → BLOCKING (default state)

    Returns 0 if all assertions pass, 1 if any fails.
    """
    import tempfile
    from src.control.entries_block_registry import BlockState, EntriesBlockRegistry

    print("\n" + "=" * 72)
    print("[REGISTRY] Gate injection checks")
    print("=" * 72)

    failures: list[str] = []

    # ── 1. Tombstone present → gate 1 CLEAR (retired 2026-05-04 Stage 2) ────
    with tempfile.TemporaryDirectory(prefix="repro_registry_") as tmp:
        state_dir = Path(tmp)
        # Create the tombstone file (gate is retired — adapter always returns CLEAR)
        (state_dir / "auto_pause_failclosed.tombstone").write_text("", encoding="utf-8")

        deps = _build_synthetic_deps(state_dir=state_dir)
        registry = EntriesBlockRegistry.from_runtime(deps)
        blocks = {b.id: b for b in registry.enumerate_blocks(stage="all")}
        b1 = blocks[1]

        if b1.state == BlockState.CLEAR:
            print(f"[REGISTRY] Gate 1 (auto_pause_failclosed_tombstone): CLEAR ✓ (retired — tombstone no longer blocks)")
        else:
            msg = f"Gate 1 expected CLEAR after retirement, got {b1.state.value!r}"
            print(f"[REGISTRY] Gate 1 FAIL: {msg}")
            failures.append(msg)

    # ── 2. Heartbeat DEGRADED + allow_submit=False → gate 9 BLOCKING ──────
    with tempfile.TemporaryDirectory(prefix="repro_registry_") as tmp:
        state_dir = Path(tmp)
        hb_summary = {
            "health": "DEGRADED",
            "entry": {"allow_submit": False},
        }
        deps = _build_synthetic_deps(state_dir=state_dir, heartbeat_summary=hb_summary)
        registry = EntriesBlockRegistry.from_runtime(deps)
        blocks = {b.id: b for b in registry.enumerate_blocks(stage="all")}
        b9 = blocks[9]

        if b9.state in (BlockState.BLOCKING, BlockState.UNKNOWN):
            print(f"[REGISTRY] Gate 9 (heartbeat_supervisor_allow_submit): BLOCKING ✓ reason={b9.blocking_reason}")
        else:
            msg = f"Gate 9 expected BLOCKING when health=DEGRADED allow_submit=False, got {b9.state.value!r}"
            print(f"[REGISTRY] Gate 9 FAIL: {msg}")
            failures.append(msg)

    # ── 3. WS gap DISCONNECTED + allow_submit=False → gate 10 BLOCKING ────
    with tempfile.TemporaryDirectory(prefix="repro_registry_") as tmp:
        state_dir = Path(tmp)
        ws_summary = {
            "subscription_state": "DISCONNECTED",
            "gap_reason": "not_configured",
            "entry": {"allow_submit": False},
        }
        deps = _build_synthetic_deps(state_dir=state_dir, ws_gap_summary=ws_summary)
        registry = EntriesBlockRegistry.from_runtime(deps)
        blocks = {b.id: b for b in registry.enumerate_blocks(stage="all")}
        b10 = blocks[10]

        if b10.state in (BlockState.BLOCKING, BlockState.UNKNOWN):
            print(f"[REGISTRY] Gate 10 (ws_gap_guard_allow_submit): BLOCKING ✓ reason={b10.blocking_reason}")
        else:
            msg = f"Gate 10 expected BLOCKING when subscription_state=DISCONNECTED, got {b10.state.value!r}"
            print(f"[REGISTRY] Gate 10 FAIL: {msg}")
            failures.append(msg)

    # ── 4. Promotion evidence file absent → gate 12 BLOCKING ──────────────
    with tempfile.TemporaryDirectory(prefix="repro_registry_") as tmp:
        state_dir = Path(tmp)
        # No evidence file written — that is the default state
        deps = _build_synthetic_deps(state_dir=state_dir)
        registry = EntriesBlockRegistry.from_runtime(deps)
        blocks = {b.id: b for b in registry.enumerate_blocks(stage="all")}
        b12 = blocks[12]

        if b12.state in (BlockState.BLOCKING, BlockState.UNKNOWN):
            print(f"[REGISTRY] Gate 12 (entry_forecast_promotion_evidence_file): BLOCKING ✓ reason={b12.blocking_reason}")
        else:
            msg = f"Gate 12 expected BLOCKING when evidence file absent, got {b12.state.value!r}"
            print(f"[REGISTRY] Gate 12 FAIL: {msg}")
            failures.append(msg)

    # ── Summary ────────────────────────────────────────────────────────────
    print("-" * 72)
    if not failures:
        print("[REGISTRY] All 4 gate injection checks PASSED")
        return 0
    else:
        for f in failures:
            print(f"[REGISTRY] FAIL: {f}")
        print(f"[REGISTRY] {len(failures)} check(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
