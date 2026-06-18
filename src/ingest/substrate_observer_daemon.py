# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: docs/architecture/system_decomposition_plan.md
#   §4.1 (Executable-Substrate Observer), §6 (P2 row + co-location decision),
#   §7 I1 (no-back-coupling), §8 Step 1 (lift), §9 (regression-unconstructable proof).
"""Zeus P2 substrate-observer daemon entry point (com.zeus.substrate-observer).

Lifts the executable-substrate observer OUT of the order daemon (src.main) into its own
process — the zero-trade regression site (system_decomposition_plan §0). It runs the two
substrate producers that share the in-process snapshot-refresh lock:

  - ``_market_discovery_cycle``        (universe sweep, 5-min, STALENESS-triggered)
  - ``_edli_market_substrate_warm_cycle`` (pending-family warm, 20s)

Both producers live in ``src.data.substrate_observer`` (a trading-lane-free module) and
write ``executable_market_snapshots`` / ``book_hash_transitions`` on trades.db. The order
runtime (src.main) becomes a pure READER of those tables
(``_latest_snapshot_rows_for_event_family``) — interface I1.

WHY THIS IS ITS OWN PROCESS (system_decomposition_plan §4.1/§9):
  - ALWAYS_ON (criterion 1): substrate must be captured even when no trading happens.
  - The two producers share ``_market_substrate_refresh_lock`` so they cannot race-write
    the snapshot table; they MUST run in ONE process (§4.1) — so they are lifted together.
  - No reactor handle / no ``pending_count`` exists in this process, so the old
    gate-capture-on-backlog regression is UN-WRITABLE across the boundary (§9 point 1).
  - Failure-domain isolation (criterion 3): this daemon imports NO trading lane.

This module mirrors the existing daemon pattern (src/ingest_main.py): logging split,
SIGTERM graceful shutdown, schema init, world_schema_ready sentinel, a BlockingScheduler,
and a 60s heartbeat tick. It does NOT import src.engine / src.execution / src.strategy /
src.signal / src.control / src.main.

ARTIFACT-ONLY DEPLOY: the launchd plist
(deploy/launchd/com.zeus.substrate-observer.plist) is an artifact; this refactor does NOT
load/kickstart any service.

INV-37: the substrate producer WRITE is single-DB (trades.db) via the sanctioned
``get_trade_connection`` path; the only cross-DB touch is a READ-ONLY ATTACH of forecasts
for topology. No independent cross-DB connection is opened — the cross-DB ATTACH+SAVEPOINT
law is not relaxed; it is simply not triggered (no cross-DB write here).
"""
from __future__ import annotations

import functools
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("zeus.substrate_observer")

# Module-level scheduler reference for the SIGTERM handler.
_scheduler: Any | None = None

# SIGTERM-unif (WAVE-4 parity): captured at module load so the forensic elapsed emitted in
# _graceful_shutdown matches src/main.py / src/ingest_main.py / src/riskguard/riskguard.py.
_PROCESS_START = time.monotonic()

# Substrate-warm cadence (mirrors src/main.py:_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS, the
# value the lifted warm job was registered with). The budget<interval invariant below is
# asserted at registration exactly as it was in the order daemon.
_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS = 20.0

_heartbeat_fails = 0


def _graceful_shutdown(signum, frame) -> None:
    """SIGTERM handler — wait for in-flight jobs then exit 0 (daemon parity)."""
    logger.info("substrate-observer daemon received SIGTERM; shutting down scheduler")
    logger.error(
        "SIGTERM_RECEIVED pid=%s ppid=%s elapsed=%ss",
        os.getpid(), os.getppid(), int(time.monotonic() - _PROCESS_START),
    )
    try:
        _shutdown_scheduler_if_running(_scheduler, wait=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scheduler shutdown error: %s", exc)
    sys.exit(0)


def _shutdown_scheduler_if_running(scheduler: Any | None, *, wait: bool = True) -> None:
    if scheduler is None:
        return
    from apscheduler.schedulers.base import SchedulerNotRunningError

    try:
        scheduler.shutdown(wait=wait)
    except SchedulerNotRunningError:
        logger.info("Scheduler already stopped during shutdown")


def _scheduler_job(job_name: str):
    """Uniform error-swallowing + health-write wrapper for APScheduler targets.

    Mirrors src/ingest_main.py:_scheduler_job. On success writes a
    scheduler_jobs_health.json OK entry; on exception logs + writes FAILED, never
    re-raising (a producer fetch error must not crash the scheduler — the next tick
    retries, and the order-runtime consumer fail-closes on the stale snapshot row).
    """
    def _decorator(fn):
        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
                try:
                    from src.observability.scheduler_health import _write_scheduler_health
                    _write_scheduler_health(job_name, failed=False, reason=None)
                except Exception:  # noqa: BLE001 — health write must never break the job
                    pass
                return result
            except Exception as exc:  # noqa: BLE001
                logger.error("%s failed: %s", job_name, exc, exc_info=True)
                try:
                    from src.observability.scheduler_health import _write_scheduler_health
                    _write_scheduler_health(job_name, failed=True, reason=str(exc))
                except Exception:  # noqa: BLE001
                    pass
        return _wrapper
    return _decorator


def _write_substrate_observer_heartbeat() -> None:
    """Write daemon-heartbeat-substrate-observer.json every 60s (liveness for the sensor)."""
    global _heartbeat_fails
    from src.config import state_path

    path = state_path("daemon-heartbeat-substrate-observer.json")
    try:
        payload = {
            "daemon": "substrate-observer",
            "alive_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
        }
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
        _heartbeat_fails = 0
    except Exception as exc:  # noqa: BLE001
        _heartbeat_fails += 1
        logger.error("substrate-observer heartbeat write failed (%d): %s", _heartbeat_fails, exc)


def main() -> None:
    global _scheduler
    from apscheduler.executors.pool import ThreadPoolExecutor as _APSchedulerThreadPoolExecutor
    from apscheduler.schedulers.blocking import BlockingScheduler

    # Logging split: INFO/DEBUG → stdout (.log), WARNING+ → stderr (.err) — daemon parity.
    _fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    _stdout_h = logging.StreamHandler(sys.stdout)
    _stdout_h.setLevel(logging.INFO)
    _stdout_h.setFormatter(_fmt)
    _stdout_h.addFilter(lambda r: r.levelno < logging.WARNING)
    _stderr_h = logging.StreamHandler(sys.stderr)
    _stderr_h.setLevel(logging.WARNING)
    _stderr_h.setFormatter(_fmt)
    _root = logging.getLogger()
    _root.handlers.clear()
    _root.setLevel(logging.INFO)
    _root.addHandler(_stdout_h)
    _root.addHandler(_stderr_h)
    logger.info("Zeus substrate-observer daemon starting (pid=%d)", os.getpid())

    # Proxy health gate — must precede any HTTP call (Gamma/CLOB).
    from src.data.proxy_health import bypass_dead_proxy_env_vars
    bypass_dead_proxy_env_vars()

    # The lifted producers from the trading-lane-free module. Importing this module does
    # NOT pull in src.engine / src.main — failure-domain isolation (criterion 3).
    from src.data.substrate_observer import (
        _edli_market_substrate_warm_cycle,
        _market_discovery_cycle,
    )

    # Pre-flight (system_decomposition_plan §8 Step 1 mitigation): assert this process can
    # open the trades-DB snapshot writer connection AND read the world-DB pending rows + the
    # forecasts market_events topology under the sanctioned path, before entering the loop.
    # A misconfigured producer = no coverage, so fail LOUD at boot rather than silently.
    from src.state.db import (
        ZEUS_FORECASTS_DB_PATH,
        get_trade_connection,
        get_world_connection,
    )

    _trade_conn = get_trade_connection(write_class="live")
    try:
        _trade_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='executable_market_snapshots'"
        ).fetchone()
    finally:
        _trade_conn.close()
    _world_conn = get_world_connection()
    try:
        _attached = {row[1] for row in _world_conn.execute("PRAGMA database_list").fetchall()}
        if "forecasts" not in _attached:
            _world_conn.execute(
                "ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),)
            )
        _world_conn.execute(
            "SELECT 1 FROM opportunity_event_processing LIMIT 1"
        ).fetchone()
    finally:
        _world_conn.close()
    logger.info(
        "substrate-observer pre-flight OK: trades-DB snapshot table + world-DB pending rows "
        "+ forecasts ATTACH reachable under the sanctioned path"
    )

    # SIGTERM → graceful shutdown.
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    # Single-writer executor: BOTH producers write executable_market_snapshots and share the
    # in-process _market_substrate_refresh_lock; max_workers=1 + per-job max_instances=1
    # serializes them so neither overlaps itself nor the other (system_decomposition_plan §4.1).
    _scheduler = BlockingScheduler(
        executors={"default": _APSchedulerThreadPoolExecutor(max_workers=1)},
    )

    # Budget<interval invariant (carried over from src/main.py:7691, Fitz #5): the warm
    # refresh budget MUST be strictly less than the warm interval, else every overlapping
    # cycle is skipped ("maximum number of running instances reached") and the substrate is
    # never refreshed (coverage NONE). Assert at registration so an env/default drift fails
    # LOUDLY at boot instead of silently re-starving coverage.
    _warm_refresh_budget_s = max(
        5.0,
        float(os.environ.get("ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", "17.0")),
    )
    if _warm_refresh_budget_s >= _EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS:
        raise RuntimeError(
            "substrate-observer warm budget-vs-interval misconfiguration: "
            f"ZEUS_REACTOR_REFRESH_BUDGET_SECONDS={_warm_refresh_budget_s}s must be "
            f"STRICTLY LESS than the {_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS}s warm interval, "
            "else every overlapping cycle is skipped and the executable substrate is never "
            "refreshed (coverage NONE, daemon starved)."
        )

    # The daemon applies its OWN observability wrapper (the lifted functions are not
    # decorated in the trading-lane-free module). Job ids are byte-identical to the order
    # daemon's so dashboards / scheduler_health keying carry over unchanged.
    _scheduler.add_job(
        _scheduler_job("edli_market_substrate_warm")(_edli_market_substrate_warm_cycle),
        "interval",
        seconds=_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS,
        id="edli_market_substrate_warm",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _scheduler_job("market_discovery")(_market_discovery_cycle),
        "interval",
        minutes=5,
        id="market_discovery",
        max_instances=1,
        coalesce=True,
    )

    # 60s liveness heartbeat (file-only; safe to run on the single-writer pool — it does not
    # write any DB). The heartbeat-sensor watches this file's mtime.
    _scheduler.add_job(
        _write_substrate_observer_heartbeat,
        "interval",
        seconds=60,
        id="substrate_observer_heartbeat",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc),
    )

    jobs = [j.id for j in _scheduler.get_jobs()]
    logger.info("substrate-observer scheduler ready. %d jobs: %s", len(jobs), jobs)

    try:
        _scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zeus substrate-observer daemon shutting down")
        _shutdown_scheduler_if_running(_scheduler, wait=True)


if __name__ == "__main__":
    main()
