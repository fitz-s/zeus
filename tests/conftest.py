# Created: 2026-04-27
# Last reused/audited: 2026-05-18
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/T1.yaml
#                  + docs/operations/task_2026-05-01_bankroll_truth_chain/architect_memo.md §7
#                  + PLAN docs/operations/task_2026-05-11_init_schema_boot_invariant/PLAN.md §5.6
"""Shared pytest fixtures for R3 T1 fake venue parity tests."""

from __future__ import annotations

import os

import pytest

from tests.fakes.polymarket_v2 import FakeClock, FakeCollateralLedger, FakePolymarketVenue


os.environ.setdefault("ZEUS_MODE", "live")


@pytest.fixture(autouse=True)
def _bankroll_provider_test_isolation(monkeypatch):
    """P0-A antibody: deterministic bankroll, no live wallet fetches in tests.

    The bankroll provider wraps an on-chain wallet query. Without this fixture
    every ``riskguard.tick()`` codepath would silently dial out to the live
    Polymarket endpoint during pytest collection, AND the module-level cache
    would leak real wallet values across tests.

    Default behaviour: every test gets a deterministic non-config wallet
    fixture with canonical authority. The value is deliberately not tied to
    historical capital-base settings; tests that need a different wallet value
    monkeypatch ``src.runtime.bankroll_provider.current`` over this default.
    Live fetches are explicitly forbidden — ``_fetch_balance`` raises if any
    path slips through the default.
    """
    from datetime import datetime, timezone

    from src.runtime import bankroll_provider

    bankroll_provider.reset_cache_for_tests()

    def _default_current(**_kwargs):
        return bankroll_provider.BankrollOfRecord(
            value_usd=211.37,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            source="polymarket_wallet",
            authority="canonical",
            staleness_seconds=0.0,
            cached=False,
        )

    def _forbid_live_fetch():
        raise AssertionError(
            "bankroll_provider._fetch_balance was invoked from a test. "
            "Live wallet queries are forbidden in unit tests; monkeypatch "
            "bankroll_provider.current() with a BankrollOfRecord fixture."
        )

    monkeypatch.setattr(bankroll_provider, "current", _default_current)
    monkeypatch.setattr(bankroll_provider, "_fetch_balance", _forbid_live_fetch)
    yield
    bankroll_provider.reset_cache_for_tests()


@pytest.fixture
def fake_venue() -> FakePolymarketVenue:
    return FakePolymarketVenue(ledger=FakeCollateralLedger(), clock=FakeClock())


@pytest.fixture
def failure_injector(fake_venue: FakePolymarketVenue):
    def _inject(mode, **params):
        fake_venue.inject(mode, **params)
        return fake_venue

    return _inject


@pytest.fixture(autouse=True)
def r3_default_risk_allocator_for_unit_tests():
    """Keep legacy live-executor unit tests focused on their targeted guard.

    Production defaults fail closed when the A2 allocator has not been
    refreshed by the cycle runner.  Older executor/collateral/heartbeat tests
    predate A2 and patch only their local guard under test; this fixture gives
    those tests an explicit healthy allocator baseline while still allowing
    individual risk tests to call ``clear_global_allocator()`` and assert the
    fail-closed default directly.
    """

    from src.control.heartbeat_supervisor import HeartbeatHealth
    from src.control import ws_gap_guard
    from src.risk_allocator import (
        AllocationDecision,
        GovernorState,
        RiskAllocator,
        clear_global_allocator,
        configure_global_allocator,
    )

    class UnitTestRiskAllocator(RiskAllocator):
        def can_allocate(self, intent, governor_state):  # type: ignore[override]
            return AllocationDecision(True, "unit_test_default", 0)

        def maker_or_taker(self, snapshot, governor_state):  # type: ignore[override]
            return "MAKER"

        def kill_switch_reason(self, governor_state):  # type: ignore[override]
            return None

        def reduce_only_mode_active(self, governor_state):  # type: ignore[override]
            return False

    ws_gap_guard.clear_for_test()
    configure_global_allocator(
        UnitTestRiskAllocator(),
        GovernorState(
            current_drawdown_pct=0.0,
            heartbeat_health=HeartbeatHealth.HEALTHY,
            ws_gap_active=False,
            ws_gap_seconds=0,
            unknown_side_effect_count=0,
            reconcile_finding_count=0,
        ),
    )
    try:
        yield
    finally:
        clear_global_allocator()
        ws_gap_guard.clear_for_test()


# ---------------------------------------------------------------------------
# Dual-DB fixture helper — Clusters A + D (G4 cleanup, 2026-05-18)
# ---------------------------------------------------------------------------
# make_world_forecasts_pair(tmp_path) creates isolated world + forecasts DBs
# for tests that INSERT into ensemble_snapshots_v2, settlements_v2, or
# readiness_state — tables that live in init_schema_forecasts, not init_schema.
#
# Named make_world_forecasts_pair (not make_dual_db) to avoid confusion with
# the pytest fixture `dual_db` in tests/state/test_daily_obs_cross_db_atomicity.py.
# This is a plain helper function (not a pytest fixture), so tests call it
# directly: world_conn, forecasts_conn = make_world_forecasts_pair(tmp_path)
# ---------------------------------------------------------------------------

def make_world_forecasts_pair(tmp_path):
    """Create isolated world + forecasts SQLite connections for dual-DB tests.

    Returns (world_conn, forecasts_conn) with both schemas initialised.
    Temporarily monkeypatches ZEUS_WORLD_DB_PATH / ZEUS_FORECASTS_DB_PATH
    so init_schema_forecasts can ATTACH world_path when copying schema.
    Both connections are left open; callers are responsible for closing them.

    Usage::
        world_conn, forecasts_conn = make_world_forecasts_pair(tmp_path)
        world_conn.execute("INSERT INTO ...")
        forecasts_conn.execute("INSERT INTO settlements_v2 ...")
    """
    import sqlite3 as _sqlite3
    import src.state.db as _db_mod

    world_path = tmp_path / "zeus-world.db"
    forecasts_path = tmp_path / "zeus-forecasts.db"

    orig_w = _db_mod.ZEUS_WORLD_DB_PATH
    orig_f = _db_mod.ZEUS_FORECASTS_DB_PATH
    try:
        _db_mod.ZEUS_WORLD_DB_PATH = world_path
        _db_mod.ZEUS_FORECASTS_DB_PATH = forecasts_path

        world_conn = _sqlite3.connect(str(world_path))
        _db_mod.init_schema(world_conn)
        world_conn.commit()

        forecasts_conn = _sqlite3.connect(str(forecasts_path))
        _db_mod.init_schema_forecasts(forecasts_conn)
        forecasts_conn.commit()
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig_w
        _db_mod.ZEUS_FORECASTS_DB_PATH = orig_f

    return world_conn, forecasts_conn


# ---------------------------------------------------------------------------
# SQLite Writer-Lock Antibody — Track A.3 (v4 plan §10).
#
# Collection-time enforcement that scans src/ + scripts/ for:
#   1. Direct sqlite3.connect() outside the canonical-shim allowlist.
#   2. (Reserved) _connect() calls without write_class kwarg in scope —
#      activated in Phase 1 once retrofit lands.
#   3. (Reserved) Raw subprocess.{Popen,run,...} outside the helper
#      allowlist — activated in Phase 1.y.
#
# Scope: src/ + scripts/ only (NOT repo-wide rglob). Empirical Phase 0
# baseline: 433 files / 157 KLOC parses cold in ≤ 1 s; mtime-keyed cache
# brings steady-state to ≤ 200 ms.
#
# Bypass: ZEUS_DISABLE_WRITER_LOCK_ANTIBODY=1 disables the antibody
# (documented as emergency-only; CI builds set =0 explicitly).
#
# Track A.3 posture (PR #92): check (1) is now FAIL-CI.  Any new
# sqlite3.connect() site outside this allowlist fails the test run
# immediately, preventing unreviewed direct connections from landing.
# Add to allowlist only with a cited reason (read_only / pending_track_a6
# / already_guarded).
# ---------------------------------------------------------------------------

import ast as _wla_ast
import json as _wla_json
from pathlib import Path as _wla_Path

from src.state.db_writer_lock import SQLITE_CONNECT_ALLOWLIST as _WLA_PRODUCTION_ALLOWLIST

_WLA_REPO_ROOT = _wla_Path(__file__).resolve().parent.parent
_WLA_SCAN_ROOTS = (_WLA_REPO_ROOT / "src", _WLA_REPO_ROOT / "scripts")
_WLA_CACHE_PATH = _WLA_REPO_ROOT / ".pytest_cache" / "writer_lock_antibody.json"

# Allowlisted files where direct ``sqlite3.connect`` is permitted.
#
# F26 follow-up (2026-05-18): 42 CURRENT_REUSABLE entries have been migrated
# to src/state/db_writer_lock.SQLITE_CONNECT_ALLOWLIST (the production owner).
# F26 cleanup (2026-05-18): 29 STALE_REWRITE entries + 1 QUARANTINED entry
# resolved — all promoted to SQLITE_CONNECT_ALLOWLIST or dropped.
#
# Conftest now owns ONLY:
#   - canonical infra not owned by db_writer_lock — _WLA_CANONICAL_INFRA_ALLOWLIST
#     (src/state/db.py is intentionally also in the production allowlist; the
#     dual listing is by design)
#   - genuinely-unresolved daemon sites — _WLA_RESIDUAL_ALLOWLIST
#     (2 entries: market_scanner + chunk_boundary_events, pending Track A.6)
#
# The effective gate-allowlist = canonical_infra | residual | production.
#
# `_WLA_RESIDUAL_ALLOWLIST` is the single source of truth for paths that
# MUST NOT appear in the production allowlist. tests/test_allowlist_migration_f26.py
# imports it directly so there is no duplicate hand-maintained copy that could
# drift (the two-truth bug this antibody is meant to catch).
#
# Reason tags used in comments:
#   canonical_shim      — the canonical DB helper; direct connect is the point
#   pending_track_a6    — daemon-level src/ site; full retrofit deferred to Track A.6 (#246)
#
# Canonical infrastructure. These ARE allowed to also appear in the
# production db_writer_lock allowlist (src/state/db.py is the canonical
# shim and is intentionally in both). Tracked as a separate subset so
# the no-leak check below only fires on genuinely-unresolved entries
# that must not promote to production.
#
# NOTE: src/state/db_writer_lock.py is intentionally NOT allowlisted. The file
# has no sqlite3.connect() call sites today; if a future edit introduces one,
# the antibody SHOULD fire so this module stays a coordination layer (not a
# connect path). Allowlisting a no-connect file would weaken the gate.
_WLA_CANONICAL_INFRA_ALLOWLIST = frozenset({
    "src/state/db.py",                              # canonical_shim
    "src/state/collateral_ledger.py",               # singleton_persistent_conn (2026-05-13 fix): CollateralLedger(db_path=) opens a ledger-owned conn for the process-wide singleton so it survives transient caller-conn lifecycles. Single connect site, no schema mutation outside init_collateral_schema.
})

# Residual must-not-leak set: daemon src/ sites pending Track A.6 retrofit.
# Any path here that also appears in db_writer_lock.SQLITE_CONNECT_ALLOWLIST
# is a scope-creep regression (Track A.6 retrofit was skipped without a
# principled decision). The F26 antibody in tests/test_allowlist_migration_f26.py
# imports this set directly so a re-addition fails the test without a parallel
# update there.
#
# F26 cleanup (2026-05-18): 30 entries removed (29 STALE_REWRITE + 1 QUARANTINED).
# All resolved: already_guarded scripts promoted to production allowlist;
# verify_truth_surfaces promoted as read_only; _zeus_emergency_k2 dropped (file
# deleted post-run); migrate_backtest_runs retrofitted with db_writer_lock wrap.
# 2 daemon src/ sites remain — unresolved pending Track A.6.
_WLA_RESIDUAL_ALLOWLIST = frozenset({
    # --- src/ daemon sites: pending Track A.6 (#246) ---
    "src/data/market_scanner.py",       # pending_track_a6: daemon INSERT writes to market_events_v2; no db_writer_lock yet
    "src/state/chunk_boundary_events.py",  # pending_track_a6: F11 daemon-thread observability write; intentionally separate conn from BulkChunker's conn to avoid lock-order conflict; failure-silent
})

# Effective allowlist: canonical infra + residual (Track A.6 daemon sites only;
# STALE_REWRITE + QUARANTINED fully resolved in F26 cleanup) + production owner set.
_WLA_SQLITE_CONNECT_ALLOWLIST = (
    _WLA_CANONICAL_INFRA_ALLOWLIST | _WLA_RESIDUAL_ALLOWLIST | _WLA_PRODUCTION_ALLOWLIST
)



def _wla_is_bypassed() -> bool:
    """Honor operator emergency bypass via env-var."""
    return os.environ.get("ZEUS_DISABLE_WRITER_LOCK_ANTIBODY") == "1"


def _wla_load_cache() -> dict:
    if not _WLA_CACHE_PATH.exists():
        return {}
    try:
        return _wla_json.loads(_WLA_CACHE_PATH.read_text())
    except (OSError, _wla_json.JSONDecodeError):
        return {}


def _wla_save_cache(cache: dict) -> None:
    try:
        _WLA_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _WLA_CACHE_PATH.write_text(_wla_json.dumps(cache))
    except OSError:
        # Cache failure is non-fatal — Phase 0 antibody must not break CI.
        pass


def _wla_scan_file(py_file: _wla_Path) -> dict:
    """Parse a single file and return (rel-path-keyed) violations dict."""
    rel = py_file.relative_to(_WLA_REPO_ROOT).as_posix()
    out: dict = {"direct_sqlite_connect": []}
    try:
        source = py_file.read_text()
    except (OSError, UnicodeDecodeError):
        return out
    try:
        tree = _wla_ast.parse(source, filename=rel)
    except SyntaxError:
        return out
    for node in _wla_ast.walk(tree):
        if (
            rel not in _WLA_SQLITE_CONNECT_ALLOWLIST
            and isinstance(node, _wla_ast.Call)
            and isinstance(node.func, _wla_ast.Attribute)
            and node.func.attr == "connect"
            and isinstance(node.func.value, _wla_ast.Name)
            and node.func.value.id == "sqlite3"
        ):
            out["direct_sqlite_connect"].append(node.lineno)
    return out


def _wla_scan_all() -> dict:
    """Scan src/ + scripts/ with mtime-keyed cache; return aggregated violations."""
    cache = _wla_load_cache()
    new_cache: dict = {}
    aggregate: dict = {"direct_sqlite_connect": []}
    for root in _WLA_SCAN_ROOTS:
        if not root.exists():
            continue
        for py_file in root.rglob("*.py"):
            try:
                mtime = py_file.stat().st_mtime
            except OSError:
                continue
            rel = py_file.relative_to(_WLA_REPO_ROOT).as_posix()
            allowlisted = rel in _WLA_SQLITE_CONNECT_ALLOWLIST
            cached = cache.get(rel)
            if cached and cached.get("mtime") == mtime and cached.get("allowlisted") == allowlisted:
                violations = cached["violations"]
            else:
                violations = _wla_scan_file(py_file)
            new_cache[rel] = {
                "mtime": mtime,
                "allowlisted": allowlisted,
                "violations": violations,
            }
            for kind, linenos in violations.items():
                for lineno in linenos:
                    aggregate.setdefault(kind, []).append(f"{rel}:{lineno}")
    _wla_save_cache(new_cache)
    return aggregate


def pytest_configure(config) -> None:
    """Run the writer-lock antibody once at session-configure time.

    Track A.3 posture (PR #92): FAIL-CI on any direct sqlite3.connect()
    outside the allowlist.  Advisory→fail-CI upgrade per Track A plan.

    F26 cleanup (2026-05-18): STALE_REWRITE and QUARANTINED classes are fully
    resolved.  _WLA_RESIDUAL_ALLOWLIST now holds only daemon src/ sites pending
    Track A.6 (#246).  New sites should go to SQLITE_CONNECT_ALLOWLIST in
    src/state/db_writer_lock.py (CURRENT_REUSABLE) or, if a daemon src/ site
    requiring Track A.6 work, to _WLA_RESIDUAL_ALLOWLIST with reason tag
    pending_track_a6.
    """
    if _wla_is_bypassed():
        config.issue_config_time_warning(
            UserWarning(
                "writer-lock antibody bypassed via "
                "ZEUS_DISABLE_WRITER_LOCK_ANTIBODY=1"
            ),
            stacklevel=1,
        )
        return
    aggregate = _wla_scan_all()
    findings = aggregate.get("direct_sqlite_connect", [])
    if findings:
        # Track A.3: fail-CI — any unallowlisted site is a hard error.
        allowlist_size = len(_WLA_SQLITE_CONNECT_ALLOWLIST)
        raise pytest.UsageError(
            f"writer-lock antibody (Track A.3 FAIL-CI): "
            f"{len(findings)} direct sqlite3.connect() site(s) outside "
            f"allowlist ({allowlist_size} entries). "
            f"For CURRENT_REUSABLE sites add to SQLITE_CONNECT_ALLOWLIST in "
            f"src/state/db_writer_lock.py. For daemon src/ sites pending Track A.6 "
            f"(#246) add to _WLA_RESIDUAL_ALLOWLIST in tests/conftest.py with "
            f"reason tag pending_track_a6. "
            f"Violations: {findings[:5]}"
            + (f" ... and {len(findings) - 5} more" if len(findings) > 5 else "")
        )


# ---------------------------------------------------------------------------
# Schema-version drift guard (PLAN §5.6, 2026-05-11)
#
# Session-scoped autouse fixture that runs scripts/check_schema_version.py
# once per pytest invocation.  Fails fast if sqlite_master hash of a fresh
# init_schema DB does not match tests/state/_schema_pinned_hash.txt.
#
# Remediation on failure:
#   1. Bump SCHEMA_VERSION in src/state/db.py.
#   2. Run:  python scripts/check_schema_version.py --write-pin
# ---------------------------------------------------------------------------

import subprocess as _sv_subprocess
import sys as _sv_sys

_SV_REPO_ROOT = _wla_Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _enforce_schema_pinned_hash():
    """Fail the test session if schema hash drifted without bumping SCHEMA_VERSION."""
    r = _sv_subprocess.run(
        [_sv_sys.executable, "scripts/check_schema_version.py"],
        capture_output=True,
        text=True,
        cwd=str(_SV_REPO_ROOT),
    )
    if r.returncode != 0:
        pytest.exit(
            f"SCHEMA DRIFT — bump SCHEMA_VERSION in src/state/db.py "
            f"and re-pin with: python scripts/check_schema_version.py --write-pin\n"
            f"{r.stdout}{r.stderr}",
            returncode=1,
        )


# ---------------------------------------------------------------------------
# DB Isolation Antibody — TI-1 (2026-05-18)
# Reject any sqlite3.connect() call inside a pytest run that resolves to a
# live Zeus DB path. Allow :memory:, file:...?mode=ro URIs, and any path
# under a per-test tmpdir or other non-live locations.
# Bypass (emergency-only): ZEUS_DISABLE_DB_ISOLATION_ANTIBODY=1
# Authority: RESTART_READINESS_PLAN.md §3 TI-1; JOB fda4e853 audit_2026_05_17
# ---------------------------------------------------------------------------

import sqlite3 as _ti1_sqlite3
from pathlib import Path as _ti1_Path

from src.state.db import (
    ZEUS_WORLD_DB_PATH as _TI1_WORLD,
    ZEUS_FORECASTS_DB_PATH as _TI1_FORECASTS,
    _zeus_trade_db_path as _ti1_trade_path,
)

_TI1_LIVE_PATHS: frozenset[str] = frozenset({
    str(_TI1_WORLD.resolve()),
    str(_TI1_FORECASTS.resolve()),
    str(_ti1_trade_path().resolve()),
})


def _ti1_is_blocked(database: str) -> bool:
    """Return True iff `database` resolves to a live Zeus DB path.

    Handles plain paths, file: URIs, and query-string variants.
    Only ``file:...?mode=ro`` URIs are allowed against live paths
    (read-only by SQLite semantics — no writes possible).
    All other file: URIs that resolve to a live path are blocked.
    """
    from urllib.parse import parse_qs, urlparse

    if not isinstance(database, str):
        return False
    # :memory: and named-memory variants — never writes to disk
    if database == ":memory:" or database.startswith("file::memory:"):
        return False
    if database.startswith("file:"):
        parsed = urlparse(database)
        # Allow read-only URIs — SQLite enforces no writes
        qs = parse_qs(parsed.query)
        mode = qs.get("mode", [""])[0]
        if mode == "ro":
            return False
        # All other file: URIs: extract path and check against live paths
        try:
            db_path = parsed.path
            resolved = str(_ti1_Path(db_path).resolve())
        except (OSError, ValueError):
            return False
        return resolved in _TI1_LIVE_PATHS
    try:
        resolved = str(_ti1_Path(database).resolve())
    except (OSError, ValueError):
        return False
    return resolved in _TI1_LIVE_PATHS


_ti1_orig_connect = _ti1_sqlite3.connect


def _ti1_guarded_connect(database, *args, **kwargs):
    if _ti1_is_blocked(str(database)):
        raise AssertionError(
            f"TI-1 antibody: test attempted to open live Zeus DB at {database!r}. "
            "Use the autouse `_ti1_redirect_live_db` fixture (default) or pass an "
            "explicit tmp_path. Bypass: ZEUS_DISABLE_DB_ISOLATION_ANTIBODY=1 (emergency-only)."
        )
    return _ti1_orig_connect(database, *args, **kwargs)


@pytest.fixture(scope="session", autouse=True)
def _ti1_install_db_isolation_antibody():
    """Session-scope: wrap sqlite3.connect to block opens of live Zeus DB paths."""
    if os.environ.get("ZEUS_DISABLE_DB_ISOLATION_ANTIBODY") == "1":
        yield
        return
    _ti1_sqlite3.connect = _ti1_guarded_connect
    try:
        yield
    finally:
        _ti1_sqlite3.connect = _ti1_orig_connect


# ---------------------------------------------------------------------------
# Per-test live-DB redirect — TI-1 (2026-05-18)
# Belt-and-suspenders: redirect `src.state.db._connect` calls aimed at any
# of the live DB paths to a per-test tmpdir mirror. The sqlite3.connect
# antibody above is the safety net; this fixture is the default-correct
# behaviour so tests silently get isolated storage.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _ti1_redirect_live_db(tmp_path, monkeypatch):
    """Redirect _connect() calls and ATTACH targets for live Zeus DBs to per-test tmp mirrors.

    Belt-and-suspenders: patches BOTH the _connect() helper AND the module-level
    path constants (ZEUS_WORLD_DB_PATH, ZEUS_FORECASTS_DB_PATH, and the return
    value of _zeus_trade_db_path). This ensures that cross-DB helpers such as
    get_forecasts_connection_with_world() and trade_connection_with_world_flocked()
    also land on mirrors when they issue ``ATTACH DATABASE ? AS world/forecasts``
    using those constants.
    """
    if os.environ.get("ZEUS_DISABLE_DB_ISOLATION_ANTIBODY") == "1":
        yield
        return
    from src.state import db as _state_db

    tmp_world = tmp_path / "zeus-world.db"
    tmp_forecasts = tmp_path / "zeus-forecasts.db"
    tmp_trades = tmp_path / "zeus_trades.db"

    mirrors = {
        str(_TI1_WORLD.resolve()): tmp_world,
        str(_TI1_FORECASTS.resolve()): tmp_forecasts,
        str(_ti1_trade_path().resolve()): tmp_trades,
    }
    orig_connect = _state_db._connect

    def _redirecting_connect(db_path, *args, **kwargs):
        resolved = str(_ti1_Path(db_path).resolve()) if db_path else ""
        target = mirrors.get(resolved, db_path)
        return orig_connect(target, *args, **kwargs)

    monkeypatch.setattr(_state_db, "_connect", _redirecting_connect)
    # Also redirect the module-level path constants so ATTACH DATABASE calls
    # inside cross-DB helpers (get_forecasts_connection_with_world,
    # trade_connection_with_world_flocked) resolve to the per-test mirrors.
    monkeypatch.setattr(_state_db, "ZEUS_WORLD_DB_PATH", tmp_world)
    monkeypatch.setattr(_state_db, "ZEUS_FORECASTS_DB_PATH", tmp_forecasts)
    monkeypatch.setattr(_state_db, "_zeus_trade_db_path", lambda: tmp_trades)
    yield
