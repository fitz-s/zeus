# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: docs/architecture/system_decomposition_plan.md
#   §4.2 (Price-Channel / CLOB-Fact Ingest), §6 (P3 row + co-location decision:
#   a persistent WS thread is a distinct lifecycle → own service),
#   §7 (I2 no-back-coupling: durable fill bridge + execution_feasibility_evidence),
#   §8 Step 3 (lift the WS thread + market-channel + reconcile cycles),
#   §9 (regression-unconstructable proof — the reduce_only-forever latch antibody).
"""Zeus P3 price-channel-ingest daemon entry point (com.zeus.price-channel-ingest).

Lifts the CLOB-fact / price-channel ingest OUT of the order daemon (src.main) into its own
process — §4.2. It keeps the Polymarket user/market WebSocket subscribed and durably
bridges fills + book facts into the tables the order runtime only READS (interface I2):

  - the user-channel WS ingestor THREAD (``_start_user_channel_ingestor_if_enabled``) — a
    persistent WebSocket lifecycle, which is WHY P3 is its own service (§6 co-location:
    distinct from cron-tick daemons),
  - ``edli_market_channel_ingestor``  (market-channel online-service bootstrap, 1-min),
  - ``edli_user_channel_reconcile``   (user-channel/reconcile + durable fill bridge, 1-min).

All producer bodies live in ``src.ingest.price_channel_ingest`` (a trading-lane-free
module). The order runtime reads the durable fill bridge + ``execution_feasibility_evidence``
(DB-mediated) and KEEPS its boot fill-bridge recovery — the durable bridge is the persisted
truth, so NO fill is lost across the conceptual cutover.

WHY THIS IS ITS OWN PROCESS (system_decomposition_plan §4.2/§9):
  - ALWAYS_ON (criterion 1): the channel must stay subscribed while trading is paused.
  - Distinct CLOB authority (criterion 2): the user/market WebSocket is its own truth source.
  - FAILURE-DOMAIN isolation (criterion 3) — AND the reduce_only-FOREVER LATCH antibody:
    the WS thread, on auth/transport failure, records a gap in the PROCESS-GLOBAL
    ``ws_gap_guard`` submit latch (``record_gap(AUTH_FAILED)``). In the order daemon that
    poisoned the SAME in-memory latch the executor reads via ``assert_ws_allows_submit`` —
    leaving the daemon stuck in reduce_only mode forever (src/main.py:2610-2622 history).
    With the WS thread lifted HERE, its record_gap writes only THIS process's ws_gap_guard
    memory; the order daemon's submit latch is in a different address space and can no
    longer be poisoned by a WS flap. The order daemon sees a WS outage only as STALE/ABSENT
    execution_feasibility_evidence rows (DB-mediated, observable) — not a shared-process
    exception or a latched gate. The WS-failure state no longer LIVES in the order daemon.

This module mirrors the existing daemon pattern (src/ingest/substrate_observer_daemon.py):
logging split, SIGTERM graceful shutdown, a BlockingScheduler, the WS thread start, and a
60s heartbeat tick. It imports NO trading lane (src.main / src.engine / src.execution /
src.strategy / src.signal).

ARTIFACT-ONLY DEPLOY: the launchd plist
(deploy/launchd/com.zeus.price-channel-ingest.plist) is an artifact; this refactor does NOT
load/kickstart any service.

INV-37: the reconcile cycle's fill-bridge cross-DB write goes through the sanctioned
``get_trade_connection_with_world_required`` ATTACH+SAVEPOINT path; no independent cross-DB
connection is opened — the process boundary relocates WHICH process owns the transaction; it
does not relax the cross-DB-write law.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("zeus.price_channel_ingest")

# Module-level scheduler reference for the SIGTERM handler.
_scheduler: Any | None = None

# SIGTERM-unif (WAVE-4 parity): captured at module load so the forensic elapsed emitted in
# _graceful_shutdown matches src/main.py / src/ingest_main.py / src/riskguard/riskguard.py.
_PROCESS_START = time.monotonic()

_heartbeat_fails = 0


def _graceful_shutdown(signum, frame) -> None:
    """SIGTERM handler — wait for in-flight jobs then exit 0 (daemon parity).

    The WS ingestor + market-channel runners are DAEMON threads; they are torn down with
    the process. The durable fill bridge is the persisted truth, so a SIGTERM mid-cycle
    drops NO fill — the next start (here or in the order daemon's boot recovery) re-derives
    the bridge work set from edli_live_order_events and heals any orphan.
    """
    logger.info("price-channel-ingest daemon received SIGTERM; shutting down scheduler")
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

    Mirrors src/ingest/substrate_observer_daemon.py:_scheduler_job. On success writes a
    scheduler_jobs_health.json OK entry; on exception logs + writes FAILED, never
    re-raising (a WS/ingest fault must not crash the scheduler — the next tick retries, and
    the order-runtime consumer fail-closes on the stale feasibility row, never on a
    cross-process exception).
    """
    def _decorator(fn):
        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
                try:
                    from src.observability.scheduler_health import _write_scheduler_health
                    business_liveness = result if isinstance(result, dict) else None
                    failed = bool(
                        isinstance(business_liveness, dict)
                        and business_liveness.get("scheduler_failed")
                    )
                    reason = (
                        str(
                            business_liveness.get("scheduler_failure_reason")
                            or business_liveness.get("status")
                            or ""
                        )
                        if isinstance(business_liveness, dict)
                        else None
                    )
                    _write_scheduler_health(
                        job_name,
                        failed=failed,
                        reason=reason or None,
                        extra=business_liveness,
                    )
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


def _write_price_channel_heartbeat() -> None:
    """Write daemon-heartbeat-price-channel-ingest.json every 60s (liveness for the sensor)."""
    global _heartbeat_fails
    from src.config import state_path

    path = state_path("daemon-heartbeat-price-channel-ingest.json")
    try:
        payload = {
            "daemon": "price-channel-ingest",
            "alive_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
        }
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
        _heartbeat_fails = 0
    except Exception as exc:  # noqa: BLE001
        _heartbeat_fails += 1
        logger.error("price-channel-ingest heartbeat write failed (%d): %s", _heartbeat_fails, exc)


def main() -> None:
    global _scheduler
    from apscheduler.executors.pool import ThreadPoolExecutor as APSchedulerThreadPoolExecutor
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
    logger.info("Zeus price-channel-ingest daemon starting (pid=%d)", os.getpid())

    # Proxy health gate — must precede any HTTP call (Gamma/CLOB/WS).
    from src.data.proxy_health import bypass_dead_proxy_env_vars
    bypass_dead_proxy_env_vars()

    # The lifted producers from the trading-lane-free module. Importing this module does
    # NOT pull in src.main / src.engine — failure-domain isolation (criterion 3).
    from src.ingest.price_channel_ingest import (
        _edli_held_quote_refresh_cycle,
        _edli_market_channel_ingestor_cycle,
        _edli_user_channel_reconcile_cycle,
        _start_user_channel_ingestor_if_enabled,
    )

    # Pre-flight (system_decomposition_plan §8 Step 3 mitigation): assert this process can
    # open the durable fill-bridge write path (trade-conn-with-world-ATTACHed) AND read the
    # forecasts market_events topology, BEFORE entering the loop. A misconfigured producer
    # would silently stop bridging fills, so fail LOUD at boot rather than silently.
    from src.state.db import (
        get_trade_connection_with_world_required,
        get_world_connection,
    )

    _bridge_conn = get_trade_connection_with_world_required(write_class="live")
    try:
        # Confirm the durable fill-bridge target table is reachable under the ATTACH path.
        _bridge_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='position_current'"
        ).fetchone()
    finally:
        _bridge_conn.close()
    _world_conn = get_world_connection()
    try:
        _world_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='edli_live_order_events'"
        ).fetchone()
    finally:
        _world_conn.close()
    logger.info(
        "price-channel-ingest pre-flight OK: durable fill-bridge (trade+world ATTACH) + "
        "edli_live_order_events reachable under the sanctioned path"
    )

    # SIGTERM → graceful shutdown.
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    _scheduler = BlockingScheduler(
        timezone=timezone.utc,
        executors={
            "default": APSchedulerThreadPoolExecutor(max_workers=2),
            "held_quote": APSchedulerThreadPoolExecutor(max_workers=1),
            "heartbeat": APSchedulerThreadPoolExecutor(max_workers=1),
        },
    )

    # PRODUCER 1: start the persistent user-channel WS ingestor THREAD. This is the
    # ws_gap_guard latch WRITER — running it HERE (not in the order daemon) is the
    # reduce_only-forever antibody (§9): a WS flap's record_gap can only poison THIS
    # process's ws_gap_guard memory, never the order daemon's submit latch.
    # Fail-open: a WS-start hiccup must not block the reconcile/market-channel schedulers
    # (the durable bridge + feasibility rows are the persisted truth; the WS reconnects on
    # its own retry loop inside the started thread).
    try:
        _start_user_channel_ingestor_if_enabled()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "price-channel-ingest: user-channel WS start raised (non-fatal; the reconcile "
            "cycle + durable bridge still run, WS retries on its own loop): %s",
            exc,
            exc_info=True,
        )

    # PRODUCER 2: market-channel online-service bootstrap (1-min). Job id byte-identical to
    # the order daemon's so dashboards / scheduler_health keying carry over unchanged.
    _scheduler.add_job(
        _scheduler_job("edli_market_channel_ingestor")(_edli_market_channel_ingestor_cycle),
        "interval",
        minutes=1,
        id="edli_market_channel_ingestor",
        max_instances=1,
        coalesce=True,
    )
    # PRODUCER 2A: held-position quote witness refresh. This must not share executor
    # capacity with broad user-channel reconcile or market-substrate scans; monitor/
    # redecision preflight is keyed to these rows for open exposure.
    _scheduler.add_job(
        _scheduler_job("edli_held_quote_refresh")(_edli_held_quote_refresh_cycle),
        "interval",
        seconds=60,
        id="edli_held_quote_refresh",
        max_instances=1,
        coalesce=True,
        executor="held_quote",
        next_run_time=datetime.now(timezone.utc),
    )
    # PRODUCER 3: user-channel/reconcile + durable fill bridge (1-min).
    _scheduler.add_job(
        _scheduler_job("edli_user_channel_reconcile")(_edli_user_channel_reconcile_cycle),
        "interval",
        minutes=1,
        id="edli_user_channel_reconcile",
        max_instances=1,
        coalesce=True,
    )

    # 60s liveness heartbeat (file-only; writes no DB). The heartbeat-sensor watches mtime.
    _scheduler.add_job(
        _write_price_channel_heartbeat,
        "interval",
        seconds=60,
        id="price_channel_ingest_heartbeat",
        max_instances=1,
        coalesce=True,
        executor="heartbeat",
        next_run_time=datetime.now(timezone.utc),
    )

    jobs = [j.id for j in _scheduler.get_jobs()]
    logger.info("price-channel-ingest scheduler ready. %d jobs: %s", len(jobs), jobs)

    try:
        _scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zeus price-channel-ingest daemon shutting down")
        _shutdown_scheduler_if_running(_scheduler, wait=True)


if __name__ == "__main__":
    main()
