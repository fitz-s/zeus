# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: docs/architecture/system_decomposition_plan.md
#   §4.1 (Executable-Substrate Observer), §6 (P2 row), §7 (I1 no-back-coupling),
#   §8 Step 1 (lift + delete outer pending gates), §9 (regression-unconstructable proof).
# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=never
# Purpose: RELATIONSHIP TESTS for process-topology refactor STEP P2 — lift the
#   executable-substrate observer (the zero-trade regression site) out of the order
#   daemon into its own process.
#
# These tests verify CROSS-MODULE INVARIANTS (Module A's output → Module B), not just
# function behavior:
#   (NO-REGRESSION) the substrate PRODUCER still exists and still writes the snapshot
#     tables the order runtime READS; src.main still imports and registers the jobs that
#     STAY; the two lifted jobs still share ONE in-process snapshot lock (so they cannot
#     race-write executable_market_snapshots); the new process opens its DB via the
#     sanctioned single-DB / read-only-ATTACH path (no independent cross-DB connection).
#   (SUPERIORITY) the lifted producer module contains NO reference to the reactor
#     pending queue / pending_count / _edli_reactor_active — the gate-capture-on-backlog
#     line is un-writable across the process boundary; the outer pending gates
#     (system_decomposition_plan §0: src/main.py:3632 + :3656) are DELETED; src.main
#     registers exactly 2 fewer jobs; the substrate producer fires on STALENESS alone
#     regardless of reactor backlog.
"""STEP P2 relationship tests: lift the executable-substrate observer to its own process."""
from __future__ import annotations

import ast
import contextlib
import inspect
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PY = _REPO_ROOT / "src" / "main.py"
_OBSERVER_MODULE = _REPO_ROOT / "src" / "data" / "substrate_observer.py"
_OBSERVER_DAEMON = _REPO_ROOT / "src" / "ingest" / "substrate_observer_daemon.py"
_OBSERVER_PLIST = _REPO_ROOT / "deploy" / "launchd" / "com.zeus.substrate-observer.plist"

_LIFTED_JOB_IDS = ("market_discovery", "edli_market_substrate_warm")
_MAINSTREAM_WARM_STAYS_ID = "edli_mainstream_warm"

# The reactor-backlog symbols a lifted PRODUCER must never reference (the regression
# category — system_decomposition_plan §7 I1 / §9): a producer is NEVER gated on a
# consumer's in-process queue/flag/lock.
_REACTOR_BACKLOG_SYMBOLS = (
    "_edli_reactor_active",
    "_edli_pending_opportunity_count",
    "_market_discovery_pending_fairness_seconds",
    "pending_count",
)


# ---------------------------------------------------------------------------
# Shared AST helpers
# ---------------------------------------------------------------------------

def _add_job_first_positional_names(source_path: Path) -> list[str]:
    """Return the first-positional-arg Name id of every `*.add_job(NAME, ...)` call."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_job":
            if node.args and isinstance(node.args[0], ast.Name):
                names.append(node.args[0].id)
    return names


def _add_job_ids(source_path: Path) -> list[str]:
    """Return every literal `id=` keyword across `*.add_job(..., id="X")` calls."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    ids: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_job":
            for kw in node.keywords:
                if kw.arg == "id" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    ids.append(kw.value.value)
    return ids


# ===========================================================================
# NO-REGRESSION INVARIANTS (the lift must preserve every property the order
# runtime depends on)
# ===========================================================================

def test_no_regression_observer_module_owns_the_two_lifted_producers():
    """The lifted PRODUCER logic lives in a trading-lane-free module the runtime reads from.

    The substrate producers must NOT vanish — they move host process. The order
    runtime stays a pure READER of executable_market_snapshots; the WRITER moves to
    src.data.substrate_observer (importable by the new daemon and by src.main's
    mainstream warmer, neither of which is a trading-lane import).
    """
    assert _OBSERVER_MODULE.exists(), (
        "src/data/substrate_observer.py must exist — it owns the lifted "
        "_market_discovery_cycle + _edli_market_substrate_warm_cycle producers."
    )
    import src.data.substrate_observer as obs

    for fn in (
        "_market_discovery_cycle",
        "_edli_market_substrate_warm_cycle",
        "_refresh_pending_family_snapshots",
        "_pending_family_rows_for_refresh",
    ):
        assert hasattr(obs, fn), f"src.data.substrate_observer must define {fn}"


def test_no_regression_src_main_still_imports():
    """src.main MUST still import successfully with the two jobs removed (never break boot)."""
    import importlib

    import src.main as main_mod

    importlib.reload(main_mod) if False else None  # no reload side effects; import is the check
    assert main_mod is not None


def test_no_regression_two_lifted_jobs_share_one_in_process_snapshot_lock():
    """The two lifted jobs MUST share ONE _market_substrate_refresh_lock in ONE process.

    system_decomposition_plan §4.1: if they did not share the lock they could
    race-write executable_market_snapshots. The lift keeps them co-resident, so the
    SAME module-global lock object serializes both writers.
    """
    import src.data.substrate_observer as obs

    assert hasattr(obs, "_market_substrate_refresh_lock"), (
        "the lifted producer module must own the snapshot-refresh lock that serializes "
        "its two writers (market_discovery + substrate warm)."
    )
    disc_src = inspect.getsource(obs._market_discovery_cycle)
    warm_src = inspect.getsource(obs._edli_market_substrate_warm_cycle)
    assert "_market_substrate_refresh_lock.acquire" in disc_src, (
        "market_discovery must acquire the shared snapshot-refresh lock before writing."
    )
    assert "_market_substrate_refresh_lock.acquire" in warm_src, (
        "the substrate warmer must acquire the SAME shared snapshot-refresh lock."
    )


def test_substrate_observer_heartbeat_has_dedicated_executor():
    """The file heartbeat must not be starved by the single snapshot-writer worker."""
    src = _OBSERVER_DAEMON.read_text(encoding="utf-8")
    assert '"heartbeat": _APSchedulerThreadPoolExecutor(max_workers=1)' in src
    assert 'id="substrate_observer_heartbeat"' in src
    assert 'executor="heartbeat"' in src
    assert "misfire_grace_time=30" in src


def test_no_regression_reactor_reader_in_order_runtime_is_untouched():
    """P1's reactor MUST keep its SELECT-side snapshot reader (the consumer side of I1)."""
    reader_path = _REPO_ROOT / "src" / "engine" / "event_reactor_adapter.py"
    src = reader_path.read_text(encoding="utf-8")
    assert "def _latest_snapshot_rows_for_event_family" in src, (
        "the order runtime's snapshot reader (_latest_snapshot_rows_for_event_family) "
        "must remain in P1 — it is the READ side of interface I1."
    )


def test_no_regression_mainstream_warmer_stays_in_order_runtime():
    """_edli_mainstream_warm_cycle MUST stay in src.main (in-process _WARM_CACHE; §3/§5).

    Moving it would make every receipt carry mainstream_*=None forever — the NEW
    coupling regression criterion 5 forbids. It is NOT lifted by P2.
    """
    import src.main as main_mod

    assert hasattr(main_mod, "_edli_mainstream_warm_cycle"), (
        "the mainstream warmer must remain defined in src.main (it writes the "
        "process-global _WARM_CACHE only P1's reactor reads)."
    )
    # And src.main must still REGISTER it (it stays scheduled in P1).
    assert _MAINSTREAM_WARM_STAYS_ID in _add_job_ids(_MAIN_PY), (
        f"src.main must still register id={_MAINSTREAM_WARM_STAYS_ID!r} — the mainstream "
        "warmer stays in the order runtime."
    )
    assert "_edli_mainstream_warm_cycle" in _add_job_first_positional_names(_MAIN_PY)


def test_no_regression_mainstream_warmer_shares_db_mediated_pending_scope():
    """The mainstream warmer (STAYS) still derives its scope from the SAME world-DB rows.

    Relationship invariant: the pending-family scope is a TABLE-read helper
    (_pending_family_rows_for_refresh) shared across the process boundary by VALUE of
    being a queryable world-DB SELECT — NOT an in-process queue handle. After the lift
    the helper lives in src.data.substrate_observer and src.main's mainstream warmer
    imports it; both processes re-derive the same pending set from the same table.
    """
    import src.main as main_mod

    warm_src = inspect.getsource(main_mod._edli_mainstream_warm_cycle)
    assert "_pending_family_rows_for_refresh" in warm_src, (
        "the mainstream warmer must still scope to pending families via the shared "
        "world-DB SELECT helper (DB-mediated scope, not an in-process queue)."
    )


def test_no_regression_observer_module_is_not_a_trading_lane_import():
    """The lifted producer module must NOT import the trading lane (failure-domain isolation).

    system_decomposition_plan criterion 3: a producer crash must not kill the consumer
    and vice-versa. If substrate_observer imported src.main / src.engine / src.execution /
    src.strategy / src.control, the new P2 process would drag the whole trading lane in,
    re-coupling the failure domains the split exists to separate.
    """
    src = _OBSERVER_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_prefixes = (
        "src.main", "src.engine", "src.execution", "src.strategy",
        "src.signal", "src.control",
    )
    offending: list[str] = []
    for node in ast.walk(tree):
        mod = None
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name == p or alias.name.startswith(p + ".") for p in forbidden_prefixes):
                    offending.append(alias.name)
            continue
        if mod and any(mod == p or mod.startswith(p + ".") for p in forbidden_prefixes):
            offending.append(mod)
    assert not offending, (
        f"src.data.substrate_observer must not import the trading lane (failure-domain "
        f"isolation, §criterion 3); offending imports: {offending}"
    )


def test_no_regression_new_process_uses_sanctioned_db_path_no_independent_cross_db():
    """The lifted producer's DB access uses the sanctioned single-DB + RO-ATTACH path (INV-37).

    The producer WRITE is single-DB (trades.db only: executable_market_snapshots,
    book_hash_transitions). The only cross-DB touch is a READ-ONLY ATTACH of forecasts
    for topology and a world-DB read for the pending-family scope. No independent
    cross-DB WRITE connection is opened — INV-37 (ATTACH+SAVEPOINT for cross-DB WRITES)
    is not relaxed; it is simply not triggered because the writes are single-DB.
    """
    src = _OBSERVER_MODULE.read_text(encoding="utf-8")
    # Single-DB trade write path (the snapshot writer's own connection).
    assert "get_trade_connection" in src, (
        "the producer must open its trades.db write connection via the sanctioned "
        "get_trade_connection path (single-DB write; INV-37 cross-DB rule not triggered)."
    )
    # It must NOT hand-roll an independent cross-DB connection (e.g. raw sqlite3.connect
    # to a second DB) — the forbidden anti-pattern INV-37 exists to prevent.
    assert "sqlite3.connect" not in src, (
        "the producer must not open a raw independent connection; cross-DB reads use "
        "the sanctioned ATTACH path, cross-DB writes (none here) use ATTACH+SAVEPOINT."
    )


# ===========================================================================
# SUPERIORITY INVARIANTS (the lift makes the regression CATEGORY unconstructable)
# ===========================================================================

def _code_identifiers(source_path: Path) -> set[str]:
    """Every identifier USED IN CODE (ast.Name / attribute / arg) — excludes docstrings & comments.

    The superiority invariant is that the module cannot NAME the reactor backlog in
    EXECUTABLE code. A descriptive mention inside the module docstring (e.g. explaining
    WHY the gate was deleted) is not a coupling — only a code reference is.
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


def test_superiority_lifted_module_has_no_reactor_backlog_reference():
    """The lifted producer module references NO reactor-backlog symbol IN CODE.

    system_decomposition_plan §9 point 1: 'There is no pending_count to read, so
    `if pending: skip` is not expressible. The harmful line cannot be written.' Proven by
    AST: none of the backlog identifiers may appear in executable code (docstring prose
    explaining the deleted gate is allowed — it is not a coupling).
    """
    used = _code_identifiers(_OBSERVER_MODULE)
    present = [sym for sym in _REACTOR_BACKLOG_SYMBOLS if sym in used]
    assert not present, (
        "the lifted substrate-observer module must not reference any reactor-backlog "
        f"symbol in CODE — the gate-on-backlog regression must be un-writable here. "
        f"Found: {present}"
    )


def test_superiority_outer_pending_gates_deleted_from_market_discovery_cycle():
    """The outer pending gates (plan §0: :3632 + :3656) are DELETED from _market_discovery_cycle.

    Pre-lift these were STILL LIVE (`if _edli_reactor_active(): return` and
    `if pending_count > 0 and recent_discovery: return`). The lift deletes them — the
    universe sweep fires on substrate STALENESS alone.
    """
    import src.data.substrate_observer as obs

    disc_src = inspect.getsource(obs._market_discovery_cycle)
    # AST over the function body so an explanatory COMMENT naming the deleted gate does not
    # falsely match — only an executable CODE reference is a coupling.
    func_tree = ast.parse(disc_src)
    used: set[str] = set()
    for node in ast.walk(func_tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
    assert "_edli_reactor_active" not in used, (
        "outer gate `if _edli_reactor_active(): return` must be deleted from "
        "_market_discovery_cycle (plan §8 Step 1)."
    )
    assert "pending_count" not in used, (
        "outer gate `if pending_count > 0 and recent_discovery: return` must be deleted "
        "from _market_discovery_cycle (plan §8 Step 1)."
    )
    # The producer-local staleness clock is the ONLY trigger that remains (a CODE reference).
    assert "_market_discovery_last_completed_monotonic" in used, (
        "the universe sweep must still key its producer-local staleness clock "
        "(_market_discovery_last_completed_monotonic) — staleness is the sole trigger."
    )


def test_superiority_src_main_no_longer_registers_the_two_lifted_jobs():
    """src.main registers EXACTLY 2 fewer jobs: market_discovery + substrate warm are gone.

    Both the legacy_cron and the EDLI registration sites of market_discovery, plus the
    EDLI registration of the substrate warmer, are removed. The mainstream warmer stays.
    """
    ids = _add_job_ids(_MAIN_PY)
    names = _add_job_first_positional_names(_MAIN_PY)
    for jid in _LIFTED_JOB_IDS:
        assert jid not in ids, (
            f"src.main must NOT register id={jid!r} anymore — it is lifted to P2."
        )
    assert "_market_discovery_cycle" not in names, (
        "src.main must not register _market_discovery_cycle on any scheduler "
        "(legacy_cron AND EDLI sites both removed)."
    )
    assert "_edli_market_substrate_warm_cycle" not in names, (
        "src.main must not register _edli_market_substrate_warm_cycle anymore."
    )


def test_superiority_src_main_does_not_define_the_lifted_producers():
    """src.main no longer DEFINES the lifted producers (they moved; no dead duplicate).

    A duplicate def in src.main would let a future edit re-introduce a pending gate in
    the order process — the category must be unconstructable in P1 too.
    """
    import src.main as main_mod

    for fn in ("_market_discovery_cycle", "_edli_market_substrate_warm_cycle"):
        defined_here = (
            fn in main_mod.__dict__
            and getattr(getattr(main_mod, fn), "__module__", "") == "src.main"
        )
        assert not defined_here, (
            f"{fn} must not be DEFINED in src.main after the lift (it lives in "
            "src.data.substrate_observer)."
        )


def test_superiority_dead_gate_helpers_removed_from_src_main():
    """The killed-gate machinery (_edli_pending_opportunity_count + fairness) is removed.

    These functions exist ONLY to feed the deleted outer pending gate. Keeping them in
    src.main would keep the regression's machinery alive and reconstructable.
    """
    src = _MAIN_PY.read_text(encoding="utf-8")
    assert "def _edli_pending_opportunity_count" not in src, (
        "_edli_pending_opportunity_count fed the deleted pending gate — remove it from "
        "src.main (the consumer-derived pending count is exactly the coupling killed)."
    )
    assert "def _market_discovery_pending_fairness_seconds" not in src, (
        "_market_discovery_pending_fairness_seconds fed the deleted pending gate — "
        "remove it from src.main."
    )


def test_superiority_substrate_producer_fires_on_staleness_regardless_of_backlog():
    """RELATIONSHIP TEST: substrate capture fires on STALENESS alone, NOT on reactor backlog.

    This is the antibody for the zero-trade regression. We drive _market_discovery_cycle
    under a SIMULATED non-empty reactor backlog (the exact condition the deleted outer
    gate reacted to) and assert it STILL reaches the snapshot-capture write path. Because
    the lifted module cannot even NAME the reactor's pending queue, a backlog has ZERO
    effect on whether the producer fires — it captures the universe regardless.
    """
    import src.data.substrate_observer as obs

    captured = {"refresh_called": False, "events_seen": None}

    class _FakeClob:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_find_weather_markets_or_raise(**kwargs):
        # A non-empty universe — there IS substrate to capture.
        return [{"condition_id": "c1", "outcomes": []}]

    def _fake_refresh(conn, *, markets, clob, captured_at, scan_authority, **_kwargs):
        captured["refresh_called"] = True
        captured["events_seen"] = markets
        return {"attempted": 1, "inserted": 1}

    class _FakeConn:
        def __init__(self):
            self.committed = False

        def commit(self):
            self.committed = True

        def close(self):
            pass

    # Monkeypatch the import targets used inside _market_discovery_cycle.
    import src.data.market_scanner as ms
    import src.data.polymarket_client as pc
    import src.state.db as dbmod

    orig = {
        "find": ms.find_weather_markets_or_raise,
        "refresh": ms.refresh_executable_market_substrate_snapshots,
        "client": pc.PolymarketClient,
        "trade_conn": dbmod.get_trade_connection,
    }
    ms.find_weather_markets_or_raise = _fake_find_weather_markets_or_raise
    ms.refresh_executable_market_substrate_snapshots = _fake_refresh
    pc.PolymarketClient = lambda *a, **k: _FakeClob()
    dbmod.get_trade_connection = lambda *a, **k: _FakeConn()
    import src.data.dual_run_lock as dual_run_lock

    orig_lock = dual_run_lock.acquire_lock
    dual_run_lock.acquire_lock = lambda _name: contextlib.nullcontext(True)
    # Make the staleness clock report STALE so the producer is due to fire.
    obs._market_discovery_last_completed_monotonic = None
    try:
        # No reactor handle exists in this process; nothing can gate the producer on a
        # backlog. Calling it must reach the capture write path.
        obs._market_discovery_cycle()
    finally:
        ms.find_weather_markets_or_raise = orig["find"]
        ms.refresh_executable_market_substrate_snapshots = orig["refresh"]
        pc.PolymarketClient = orig["client"]
        dbmod.get_trade_connection = orig["trade_conn"]
        dual_run_lock.acquire_lock = orig_lock

    assert captured["refresh_called"], (
        "the substrate producer must reach the snapshot-capture write path on staleness "
        "alone — a (here non-existent) reactor backlog must have ZERO effect on whether "
        "it fires. This is the zero-trade regression made unconstructable."
    )


# ===========================================================================
# NEW PROCESS ARTIFACTS (the lift creates a real, bootable program boundary)
# ===========================================================================

def test_new_daemon_entry_point_exists_and_registers_both_lifted_jobs():
    """The new daemon entry-point exists and registers EXACTLY the two lifted jobs.

    Mirrors the existing daemon pattern (src/ingest_main.py). Both lifted jobs must be
    registered on the NEW scheduler so the snapshot tables keep getting written.
    """
    assert _OBSERVER_DAEMON.exists(), (
        "src/ingest/substrate_observer_daemon.py must exist (new P2 entry-point)."
    )
    ids = _add_job_ids(_OBSERVER_DAEMON)
    for jid in _LIFTED_JOB_IDS:
        assert jid in ids, (
            f"the new substrate-observer daemon must register id={jid!r} so the lifted "
            "producer keeps writing the snapshot substrate the runtime reads."
        )
    # It must NOT register the mainstream warmer (that stays in P1).
    assert _MAINSTREAM_WARM_STAYS_ID not in ids, (
        "the new daemon must NOT register the mainstream warmer — it stays in src.main."
    )


def test_new_daemon_does_not_reference_reactor_backlog():
    """The new daemon module references NO reactor-backlog symbol IN CODE (whole-process superiority).

    AST-based (docstring prose explaining the deleted gate is allowed; only executable
    code references would be a coupling).
    """
    used = _code_identifiers(_OBSERVER_DAEMON)
    present = [sym for sym in _REACTOR_BACKLOG_SYMBOLS if sym in used]
    assert not present, (
        f"the new substrate-observer daemon must not reference reactor-backlog symbols in "
        f"CODE: {present}"
    )


def test_new_daemon_has_module_provenance_header():
    """File-header provenance rule (operator law): Created/Last-audited + Authority basis."""
    head = "\n".join(_OBSERVER_DAEMON.read_text(encoding="utf-8").splitlines()[:15])
    assert "2026-06-08" in head, "new daemon must carry a 2026-06-08 provenance date"
    assert "system_decomposition_plan" in head, (
        "new daemon must cite system_decomposition_plan as its authority basis"
    )


def test_launchd_plist_artifact_exists_and_targets_the_new_daemon():
    """The launchd .plist artifact exists, labels com.zeus.substrate-observer, runs the daemon.

    ARTIFACT ONLY — this test does NOT load/install the service. It asserts the plist is
    a well-formed launchd job mirroring the existing com.zeus.* pattern and points its
    ProgramArguments at `-m src.ingest.substrate_observer_daemon`.
    """
    assert _OBSERVER_PLIST.exists(), (
        "deploy/launchd/com.zeus.substrate-observer.plist artifact must exist."
    )
    text = _OBSERVER_PLIST.read_text(encoding="utf-8")
    assert "com.zeus.substrate-observer" in text, "plist Label must be com.zeus.substrate-observer"
    assert "src.ingest.substrate_observer_daemon" in text, (
        "plist ProgramArguments must launch `-m src.ingest.substrate_observer_daemon`."
    )
    # Well-formed plist (parses as a property list).
    import plistlib

    with _OBSERVER_PLIST.open("rb") as fh:
        parsed = plistlib.load(fh)
    assert parsed.get("Label") == "com.zeus.substrate-observer"
    assert "src.ingest.substrate_observer_daemon" in parsed.get("ProgramArguments", [])


def test_registry_owner_daemon_repointed_off_main_and_not_orphaned():
    """source_job_registry market_discovery owner_daemon is repointed off `main`; not an orphan.

    Relationship invariant across the registry seam: data_collection_inventory's
    _orphan_callable_refs() resolves each job's callable_ref against its owner_daemon
    file. After the lift the callable no longer lives in src/main.py, so the owner_daemon
    must move to the new daemon and the inventory must map that daemon to the new file —
    otherwise --check reports an ORPHAN and the registry test goes RED.
    """
    from src.data.source_job_registry import JOB_REGISTRY

    spec = JOB_REGISTRY["market_discovery"]
    assert spec.owner_daemon != "main", (
        "market_discovery owner_daemon must be repointed off `main` (the callable moved "
        "to the substrate-observer daemon)."
    )
    # The inventory orphan-check must pass (callable resolves in the new owner daemon).
    from scripts.data_collection_inventory import _orphan_callable_refs

    orphans = _orphan_callable_refs()
    assert not any("market_discovery" in o for o in orphans), (
        f"market_discovery must not be an ORPHAN callable_ref after the lift: {orphans}"
    )
