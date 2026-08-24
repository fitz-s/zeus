# Created: 2026-06-08
# Last reused or audited: 2026-08-24
# Authority basis: docs/reference/design_system_decomposition_plan.md
#   §4.2 (Price-Channel / CLOB-Fact Ingest), §6 (P3 row + co-location decision:
#   a persistent WS thread is a distinct lifecycle → own service),
#   §7 (I2 no-back-coupling: durable fill bridge + execution_feasibility_evidence),
#   §8 Step 3 (lift the WS thread + market-channel + reconcile cycles),
#   §9 (regression-unconstructable proof — the reduce_only-forever latch antibody).
"""Zeus P3 price-channel-ingest daemon entry point (com.zeus.price-channel-ingest).

Lifts the CLOB-fact / price-channel ingest OUT of the order daemon (src.main) into its own
process — §4.2. It keeps the Polymarket user/market WebSocket subscribed and durably
bridges fills + book facts into the tables the order runtime only READS (interface I2):

  - the user-channel WS ingestor THREAD (``_start_user_channel_ingestor``) — a
    persistent WebSocket lifecycle, which is WHY P3 is its own service (§6 co-location:
    distinct from cron-tick daemons),
  - ``edli_market_channel_ingestor``  (market-channel online-service bootstrap, 1-min),
  - ``edli_user_channel_reconcile``   (bounded user-channel/reconcile M5 proof, 30-sec),
  - ``edli_fill_bridge_repair``       (durable fill bridge + derived redecision repair, 1-min).

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
``get_trade_connection_with_world_required`` ATTACH+SAVEPOINT path while holding the
canonical WORLD+TRADE writer lease; no independent cross-DB connection is opened — the
process boundary relocates WHICH process owns the transaction; it does not relax the
cross-DB-write law.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("zeus.price_channel_ingest")

# Module-level scheduler reference for the SIGTERM handler.
_scheduler: Any | None = None

# SIGTERM-unif (WAVE-4 parity): captured at module load so the forensic elapsed emitted in
# _graceful_shutdown matches src/main.py / src/ingest_main.py / src/riskguard/riskguard.py.
_PROCESS_START = time.monotonic()


def _git_head_at_boot() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


_PROCESS_GIT_HEAD = _git_head_at_boot()
_HEARTBEAT_GENERATION = f"{os.getpid()}-{time.monotonic_ns()}"

_heartbeat_fails = 0
_heartbeat_status = "STARTING"
_heartbeat_ready = False
_heartbeat_published = False
_bridge_keeper_conn: Any | None = None
PRICE_CHANNEL_STARTUP_STATUS_FILENAME = "price-channel-ingest-startup.json"
MARKET_CHANNEL_FIRST_FIRE_DELAY_SECONDS = 30
MARKET_CHANNEL_BOOTSTRAP_DEADLINE_SECONDS = 60.0
MARKET_CHANNEL_BOOTSTRAP_DRAIN_DEADLINE_SECONDS = 5.0
_market_channel_bootstrap_lock = threading.Lock()
_market_channel_bootstrap_worker: threading.Thread | None = None
_market_channel_bootstrap_generation: str | None = None
_market_channel_bootstrap_started_monotonic: float | None = None


def _market_channel_bootstrap_job(fn):
    """Keep scheduler capacity bounded while a restart bootstrap reaches registration.

    A timed-out worker is fenced in the lane before a successor starts.  It may
    finish its current SQLite read, but cannot register a second consumer.
    """

    from src.ingest import price_channel_ingest as lane

    global _market_channel_bootstrap_generation, _market_channel_bootstrap_started_monotonic
    global _market_channel_bootstrap_worker
    now = time.monotonic()
    with _market_channel_bootstrap_lock:
        worker = _market_channel_bootstrap_worker
        generation = _market_channel_bootstrap_generation
        started_at = _market_channel_bootstrap_started_monotonic
        if worker is not None and worker.is_alive():
            elapsed = max(0.0, now - (started_at if started_at is not None else now))
            if generation is None or elapsed < MARKET_CHANNEL_BOOTSTRAP_DEADLINE_SECONDS:
                return {
                    "thread": "bootstrap_worker_running",
                    "bootstrap_generation": generation,
                    "bootstrap_elapsed_seconds": elapsed,
                }
            # SCOPE: this one bootstrap worker generation, never all consumers.
            # DRAIN: interrupt its SQLite readers and join it before a replacement.
            # RESET: a current ready receipt lets future scheduler fires reuse the owner.
            lane._edli_cancel_market_channel_bootstrap(generation)
            worker.join(timeout=MARKET_CHANNEL_BOOTSTRAP_DRAIN_DEADLINE_SECONDS)
            if worker.is_alive():
                return {
                    "thread": "bootstrap_worker_not_drained",
                    "bootstrap_generation": generation,
                    "bootstrap_elapsed_seconds": elapsed,
                    "scheduler_failed": True,
                    "scheduler_failure_reason": "registration_worker_not_drained",
                }
            lane._edli_supersede_market_channel_bootstrap(generation)
            failure = {
                "thread": "bootstrap_worker_superseded",
                "bootstrap_generation": generation,
                "bootstrap_elapsed_seconds": elapsed,
                "scheduler_failed": True,
                "scheduler_failure_reason": "registration_not_reached",
            }
        else:
            failure = None

        readiness_error = lane._edli_market_channel_sink_readiness_error()
        if readiness_error is None:
            target = fn
            args = ()
            kwargs = {}
            generation = None
        elif (
            getattr(lane, "_edli_market_channel_thread", None) is not None
            and lane._edli_market_channel_thread.is_alive()
        ):
            # Let the lane's own generation clock retire an unregistered runner.
            # Starting another generation here would reset that clock forever.
            target = fn
            args = ()
            kwargs = {}
            with lane._market_channel_bootstrap_lock:
                generation = lane._market_channel_bootstrap_generation
        else:
            bootstrap_deadline = now + MARKET_CHANNEL_BOOTSTRAP_DEADLINE_SECONDS - 1.0
            generation = lane._edli_begin_market_channel_bootstrap(
                deadline_monotonic=bootstrap_deadline,
            )
            target = fn
            args = ()
            kwargs = {
                "bootstrap_generation": generation,
                "bootstrap_deadline_monotonic": bootstrap_deadline,
            }
        _market_channel_bootstrap_generation = generation
        _market_channel_bootstrap_started_monotonic = now
        _market_channel_bootstrap_worker = threading.Thread(
            target=target,
            args=args,
            kwargs=kwargs,
            name="edli-market-channel-bootstrap",
            daemon=True,
        )
        _market_channel_bootstrap_worker.start()
    if failure is not None:
        return failure
    return {
        "thread": "bootstrap_worker_started",
        "bootstrap_generation": generation,
        "sink_readiness_error": readiness_error,
    }


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
    _write_price_channel_startup_status("STOPPING")
    if _heartbeat_published:
        _write_price_channel_heartbeat(status="STOPPING")
    try:
        _shutdown_scheduler_if_running(_scheduler, wait=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scheduler shutdown error: %s", exc)
    _close_bridge_keeper(reason="shutdown")
    sys.exit(0)


def _shutdown_scheduler_if_running(scheduler: Any | None, *, wait: bool = True) -> None:
    if scheduler is None:
        return
    from apscheduler.schedulers.base import SchedulerNotRunningError

    try:
        scheduler.shutdown(wait=wait)
    except SchedulerNotRunningError:
        logger.info("Scheduler already stopped during shutdown")


def _start_user_channel_ingestor_async(start_fn) -> threading.Thread:
    """Keep eager credential/market discovery off scheduler construction."""

    def _runner() -> None:
        try:
            start_fn()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "price-channel-ingest: user-channel WS start raised (non-fatal; the "
                "reconcile cycle + durable bridge still run): %s",
                exc,
                exc_info=True,
            )

    worker = threading.Thread(
        target=_runner,
        name="price-channel-user-channel-start",
        daemon=True,
    )
    worker.start()
    return worker


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
                m5_receipt_identity: tuple[str, int] | None = None
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
                    health_extra = business_liveness
                    if (
                        job_name == "edli_user_channel_reconcile"
                        and isinstance(business_liveness, dict)
                        and not failed
                    ):
                        health_extra = {
                            **business_liveness,
                            "daemon_pid": os.getpid(),
                            "heartbeat_generation": _HEARTBEAT_GENERATION,
                            "heartbeat_receipt": f"{_HEARTBEAT_GENERATION}-{time.monotonic_ns()}",
                        }
                        m5_receipt_identity = (
                            str(health_extra["heartbeat_receipt"]),
                            os.getpid(),
                        )
                    _write_scheduler_health(
                        job_name,
                        failed=failed,
                        reason=reason or None,
                        extra=health_extra,
                    )
                except Exception:  # noqa: BLE001 — health write must never break the job
                    pass
                if m5_receipt_identity is not None:
                    try:
                        from src.observability.scheduler_health import (
                            read_scheduler_job_health,
                        )

                        receipt = read_scheduler_job_health(job_name)
                        observed = receipt.get("business_liveness")
                        if (
                            receipt.get("status") == "OK"
                            and isinstance(observed, dict)
                            and observed.get("heartbeat_receipt")
                            == m5_receipt_identity[0]
                            and observed.get("daemon_pid") == m5_receipt_identity[1]
                        ):
                            _promote_price_channel_heartbeat_ready()
                    except Exception:  # noqa: BLE001 — missing receipt fails closed
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


def _scheduler_skip_listener(event: Any) -> None:
    """Persist APScheduler max-instance skips as business liveness failures."""

    job_name = str(getattr(event, "job_id", "") or "")
    if not job_name:
        return
    try:
        from src.observability.scheduler_health import _write_scheduler_health

        scheduled = [
            ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            for ts in (getattr(event, "scheduled_run_times", None) or [])
        ]
        _write_scheduler_health(
            job_name,
            failed=False,
            skipped=True,
            skip_reason="max_instances_reached",
            extra={
                "scheduler_skip_reason": "max_instances_reached",
                "scheduled_run_times": scheduled,
            },
        )
    except Exception:  # noqa: BLE001
        logger.debug("failed to write scheduler skip health", exc_info=True)


def _write_price_channel_heartbeat(*, status: str | None = None) -> bool:
    """Write daemon-heartbeat-price-channel-ingest.json every 60s (liveness for the sensor)."""
    global _heartbeat_fails, _heartbeat_status, _heartbeat_published, _heartbeat_ready
    from src.config import state_path

    if status is not None:
        _heartbeat_status = str(status).upper()
        if _heartbeat_status != "READY":
            _heartbeat_ready = False
    path = state_path("daemon-heartbeat-price-channel-ingest.json")
    try:
        payload = {
            "daemon": "price-channel-ingest",
            "status": _heartbeat_status,
            "liveness": "ALIVE",
            # Existing consumers authorize from freshness/git identity, not these
            # newer status fields.  STARTING must therefore overwrite any old
            # heartbeat without an alive_at until the first successful M5 proof.
            "ready": bool(_heartbeat_ready),
            "pid": os.getpid(),
            "git_head": _PROCESS_GIT_HEAD,
        }
        if _heartbeat_ready:
            payload["alive_at"] = datetime.now(timezone.utc).isoformat()
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
        _heartbeat_published = True
        _heartbeat_fails = 0
        return True
    except Exception as exc:  # noqa: BLE001
        _heartbeat_fails += 1
        logger.error("price-channel-ingest heartbeat write failed (%d): %s", _heartbeat_fails, exc)
        if _heartbeat_fails >= 3:
            logger.critical("FATAL: price-channel heartbeat is unwritable; exiting for launchd recovery")
            os._exit(1)
    return False


def _promote_price_channel_heartbeat_ready() -> None:
    """Promote canonical liveness only after a successful current M5 proof."""

    global _heartbeat_ready, _heartbeat_status
    if _heartbeat_ready:
        return
    _heartbeat_ready = True
    _heartbeat_status = "READY"
    if not _write_price_channel_heartbeat(status="READY"):
        _heartbeat_ready = False
        _heartbeat_status = "STARTING"


def _abort_startup_failure() -> None:
    """Abort immediately so SQLite destructors cannot run a clean last-close path."""

    for handler in (*logging.getLogger().handlers, *logger.handlers):
        try:
            handler.flush()
        except Exception:  # noqa: BLE001
            pass
    os._exit(1)


def _write_price_channel_startup_status(status: str) -> None:
    """Publish non-authorizing startup phase separate from canonical heartbeat."""

    from src.config import state_path

    try:
        path = state_path(PRICE_CHANNEL_STARTUP_STATUS_FILENAME)
        normalized = str(status).upper()
        payload = {
            "daemon": "price-channel-ingest",
            "status": normalized,
            "liveness": "ALIVE" if normalized in {"STARTING", "READY"} else normalized,
            "pid": os.getpid(),
            "git_head": _PROCESS_GIT_HEAD,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001 - non-authorizing telemetry never blocks cleanup
        logger.warning("price-channel startup status write failed", exc_info=True)


def _prepare_startup_bridge(conn: Any) -> None:
    """Probe the ATTACH bridge without inheriting close-triggered WAL work.

    SQLite can run a large implicit WAL checkpoint while the last connection to a
    database closes.  This startup bridge is retained as a process-owned keeper, so
    it must have autocheckpoint disabled before its first daemon-owned query and no
    active read transaction after the probe.  The normal live daemon remains the
    owner of recurring PASSIVE checkpoints.
    """

    # This PRAGMA must precede the sqlite_master probe and any possible close path.
    conn.execute("PRAGMA wal_autocheckpoint = 0")
    conn.rollback()
    cursor = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='position_current'"
    )
    try:
        cursor.fetchone()
    finally:
        close_cursor = getattr(cursor, "close", None)
        if callable(close_cursor):
            close_cursor()
        # SELECTs are read-only, but rollback is explicit so the keeper cannot pin
        # a WAL snapshot while it waits for the durable consumers to come up.
        conn.rollback()
    if bool(getattr(conn, "in_transaction", False)):
        raise RuntimeError("price-channel startup bridge left an active transaction")


def _handoff_bridge_keeper(conn: Any) -> None:
    """Transfer a clean preflight bridge to the process-owned keeper."""

    global _bridge_keeper_conn
    if _bridge_keeper_conn is not None and _bridge_keeper_conn is not conn:
        raise RuntimeError("price-channel startup bridge keeper already exists")
    conn.rollback()
    _bridge_keeper_conn = conn
    logger.info("price-channel-ingest startup bridge handed off to process-owned keeper")


def _close_bridge_keeper(*, reason: str) -> None:
    """Deliberately release the startup keeper on failure, shutdown, or stop."""

    global _bridge_keeper_conn
    conn = _bridge_keeper_conn
    _bridge_keeper_conn = None
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001
        logger.debug("price-channel bridge keeper rollback failed during %s", reason, exc_info=True)
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        logger.warning("price-channel bridge keeper close failed during %s", reason, exc_info=True)
    else:
        logger.info("price-channel-ingest bridge keeper closed (%s)", reason)


def _abandon_startup_bridge_on_failure(*connections: Any | None) -> None:
    """Avoid a failure-path last-close checkpoint; process exit releases these FDs."""

    global _bridge_keeper_conn
    pending: list[Any] = []
    if _bridge_keeper_conn is not None:
        pending.append(_bridge_keeper_conn)
    pending.extend(conn for conn in connections if conn is not None and conn not in pending)
    _bridge_keeper_conn = None
    if pending:
        logger.critical(
            "price-channel startup failed; abandoning %d SQLite connection(s) "
            "without close to avoid a last-close WAL checkpoint; process exit will release them",
            len(pending),
        )


def main() -> None:
    global _scheduler
    from apscheduler.events import EVENT_JOB_MAX_INSTANCES
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
    _write_price_channel_startup_status("STARTING")
    # This canonical overwrite is intentionally non-authorizing: existing gates
    # require heartbeat freshness/git identity and ignore newer status fields.
    _write_price_channel_heartbeat(status="STARTING")

    # Proxy health gate — must precede any HTTP call (Gamma/CLOB/WS).
    from src.data.proxy_health import bypass_dead_proxy_env_vars
    bypass_dead_proxy_env_vars()

    # The lifted producers from the trading-lane-free module. Importing this module does
    # NOT pull in src.main / src.engine — failure-domain isolation (criterion 3).
    from src.ingest.price_channel_ingest import (
        M5_AUTHORITY_PROOF_CADENCE_SECONDS,
        _edli_fill_bridge_repair_cycle,
        _edli_held_quote_refresh_cycle,
        _edli_market_channel_ingestor_cycle,
        _edli_user_channel_reconcile_cycle,
        _start_user_channel_ingestor,
    )

    # Pre-flight (system_decomposition_plan §8 Step 3 mitigation): assert this process can
    # open the durable fill-bridge write path (trade-conn-with-world-ATTACHed) AND read the
    # forecasts market_events topology, BEFORE entering the loop. A misconfigured producer
    # would silently stop bridging fills, so fail LOUD at boot rather than silently.
    from src.state.db import (
        get_trade_connection_with_world_required,
        get_world_connection,
    )

    bridge_conn: Any | None = None
    try:
        # Keep this connection open through startup. Closing the last trade/world
        # connection here can synchronously checkpoint a very large WAL before the
        # daemon has emitted liveness. The keeper is intentionally read-idle.
        bridge_conn = get_trade_connection_with_world_required(write_class="live")
        _prepare_startup_bridge(bridge_conn)

        # Emit the separate phase after the probe but before any transient
        # connection close; the canonical STARTING overwrite already fail-closes
        # existing freshness readers.
        _write_price_channel_startup_status("STARTING")
        _handoff_bridge_keeper(bridge_conn)

        # This second probe remains a short-lived, non-last connection because the
        # process-owned ATTACH bridge keeper is already alive.
        world_conn = get_world_connection()
        try:
            world_conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='edli_live_order_events'"
            ).fetchone()
            world_conn.rollback()
        finally:
            world_conn.close()
        logger.info(
            "price-channel-ingest pre-flight OK: durable fill-bridge (trade+world ATTACH) + "
            "edli_live_order_events reachable under the sanctioned path"
        )
    except BaseException:
        _write_price_channel_startup_status("FAILED")
        keeper_owned = bridge_conn is _bridge_keeper_conn
        _abandon_startup_bridge_on_failure(bridge_conn if not keeper_owned else None)
        _abort_startup_failure()
        raise AssertionError("startup failure abort unexpectedly returned")

    # SIGTERM → graceful shutdown.
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    _scheduler = BlockingScheduler(
        timezone=timezone.utc,
        executors={
            "default": APSchedulerThreadPoolExecutor(max_workers=2),
            "m5_authority": APSchedulerThreadPoolExecutor(max_workers=1),
            "fill_bridge": APSchedulerThreadPoolExecutor(max_workers=1),
            "held_quote": APSchedulerThreadPoolExecutor(max_workers=1),
            "heartbeat": APSchedulerThreadPoolExecutor(max_workers=1),
        },
    )
    _scheduler.add_listener(_scheduler_skip_listener, EVENT_JOB_MAX_INSTANCES)

    # PRODUCER 1: start the persistent user-channel WS ingestor THREAD. This is the
    # ws_gap_guard latch WRITER — running it HERE (not in the order daemon) is the
    # reduce_only-forever antibody (§9): a WS flap's record_gap can only poison THIS
    # process's ws_gap_guard memory, never the order daemon's submit latch.
    # Fail-open: a WS-start hiccup must not block the reconcile/market-channel schedulers
    # (the durable bridge + feasibility rows are the persisted truth; the WS reconnects on
    # its own retry loop inside the started thread).
    _start_user_channel_ingestor_async(_start_user_channel_ingestor)

    # PRODUCER 2: market-channel online-service bootstrap (1-min). Job id byte-identical to
    # the order daemon's so dashboards / scheduler_health keying carry over unchanged.
    _scheduler.add_job(
        _scheduler_job("edli_market_channel_ingestor")(
            lambda: _market_channel_bootstrap_job(_edli_market_channel_ingestor_cycle)
        ),
        "interval",
        minutes=1,
        id="edli_market_channel_ingestor",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc)
        + timedelta(seconds=MARKET_CHANNEL_FIRST_FIRE_DELAY_SECONDS),
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
    # PRODUCER 3: bounded M5 authority proof. SCOPE: only WS submit-latch
    # recovery evidence; DRAIN: one completed user-channel/reconcile sweep;
    # RESET: scheduler health expires at the existing 180s guard boundary.
    _scheduler.add_job(
        _scheduler_job("edli_user_channel_reconcile")(_edli_user_channel_reconcile_cycle),
        "interval",
        seconds=M5_AUTHORITY_PROOF_CADENCE_SECONDS,
        id="edli_user_channel_reconcile",
        max_instances=1,
        coalesce=True,
        executor="m5_authority",
    )
    # PRODUCER 3A: durable fill bridge and derived fill-redecision repair.
    # SCOPE: persisted trade facts, WORLD dispositions, TRADE positions, and
    # their derived wake. DRAIN: idempotent minute sweeps retry durable orphans.
    # RESET: the next canonical-success pass clears failed scheduler health.
    # It remains single-instance and uses canonical writer gates, but a long
    # repair pass can no longer consume M5's proof cadence.
    _scheduler.add_job(
        _scheduler_job("edli_fill_bridge_repair")(_edli_fill_bridge_repair_cycle),
        "interval",
        minutes=1,
        id="edli_fill_bridge_repair",
        max_instances=1,
        coalesce=True,
        executor="fill_bridge",
    )
    # PRODUCER 4: continuous fill synchronizer (LX-T4, docs/rebuild/
    # local_ledger_excision_2026-07-12.md). Independent of WS health/findings
    # (unlike the M5 sweep, which is event-triggered) — this is the standing
    # poller that closes Attack A (a fill landing after a one-time replay but
    # before a reader cutover). 90s cadence sits inside the packet's 60-120s
    # window and off-phase from PRODUCER 3's 60s tick so the two polls don't
    # always land on the executor together. This scheduled poll is always on;
    # see fill_synchronizer_cycle.
    from src.ingest.fill_synchronizer import fill_synchronizer_cycle

    _scheduler.add_job(
        _scheduler_job("fill_synchronizer")(fill_synchronizer_cycle),
        "interval",
        seconds=90,
        id="fill_synchronizer",
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
    _write_price_channel_startup_status("READY")
    logger.info("price-channel-ingest scheduler ready. %d jobs: %s", len(jobs), jobs)

    try:
        _scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zeus price-channel-ingest daemon shutting down")
        _write_price_channel_startup_status("STOPPING")
        _write_price_channel_heartbeat(status="STOPPING")
        _shutdown_scheduler_if_running(_scheduler, wait=True)
    except Exception:
        _write_price_channel_startup_status("FAILED")
        _write_price_channel_heartbeat(status="FAILED")
        raise
    finally:
        _close_bridge_keeper(reason="scheduler_stop")


if __name__ == "__main__":
    main()
