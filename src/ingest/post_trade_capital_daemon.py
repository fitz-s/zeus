# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: docs/architecture/system_decomposition_plan.md
#   §4.3 (Post-Trade Capital Lifecycle), §6 (P4 row + co-location decision),
#   §7 (I3 commit-before-HTTP no-back-coupling; I4 ingest->P4),
#   §8 Step 2 (lift chain-sync READ + redeem/wrap pollers), §9 (regression-unconstructable).
"""Zeus P4 post-trade-capital daemon entry point (com.zeus.post-trade-capital).

Lifts the POST_TRADE capital follow-up OUT of the order daemon (src.main) into its own
process — §4.3. It runs the cycles that resolve settlement P&L, redeem winnings, wrap the
proceeds, and reconcile chain truth, plus the chain-sync READ phase that the order daemon
used to bundle with exit monitoring:

  - ``chain_sync_read_cycle``      (chain-truth sync READ phase, 2-min)
  - ``_harvester_cycle``           (settlement P&L resolver, 1h; REDEEM_INTENT_CREATED producer)
  - ``_redeem_reconciler_cycle``   (10-min)
  (``_redeem_submitter_cycle`` DELETED 2026-07-08, R6-a, along with its 5-min
  scheduler registration: dead redeem-submission machinery, Zeus never submits
  redeem tx per operator law 2026-06-10 -- it already unconditionally
  calm-skipped every cycle.)
  - ``_wrap_intent_creator_cycle`` (5-min)
  - ``_wrap_submitter_cycle``      (2-min)
  - ``_wrap_reconciler_cycle``     (2-min)
  - ``collateral_snapshot_refresh_cycle`` (30s; pUSD/CTF collateral truth)

All cycle bodies live in ``src.execution.post_trade_capital``. The EXIT-monitoring /
exit-SUBMIT phase of the former ``_chain_sync_and_exit_monitor_cycle`` STAYS in the order
daemon (it posts real sell orders) — §8 Step 2. This process NEVER posts a sell order.

WHY THIS IS ITS OWN PROCESS (system_decomposition_plan §4.3/§9):
  - POST_TRADE / ALWAYS_ON (criterion 1): a settled position must be harvested/redeemed/
    wrapped even if trading is paused for weeks or the order daemon is dead.
  - WAL-lock starvation (§4.3, I3): in the order daemon the bundled chain-sync held the
    trades.db write lock across per-position HTTP and starved riskguard.tick() ->
    DATA_DEGRADED flaps that block ALL trades. Here ``chain_sync_read_cycle`` commits its
    writes before returning and there is no per-position monitoring HTTP after it, so the
    lock-across-HTTP contention is gone from the trading lane.
  - FAILURE_DOMAIN isolation (criterion 3): a chain-sync / redeem / wrap fault is contained
    in this process; it cannot raise into the reactor.

CASCADE-LIVENESS ANTIBODY (travels with the jobs): the redeem/wrap/harvester pollers are
``required_pollers`` in architecture/cascade_liveness_contract.yaml. The boot guard that
asserts every such poller is registered (formerly only in src.main) is carried here so a
missing poller still fails LOUD at boot in this process — the antibody is not lost in the move.

This module mirrors the existing daemon pattern (src/ingest/substrate_observer_daemon.py):
logging split, SIGTERM graceful shutdown, connection pre-flight, a BlockingScheduler, and a
60s heartbeat tick.

ARTIFACT-ONLY DEPLOY: the launchd plist
(deploy/launchd/com.zeus.post-trade-capital.plist) is an artifact; this refactor does NOT
load/kickstart any service.

INV-37: every cross-DB write each cycle performs goes through the sanctioned single-DB
connection helpers (``get_trade_connection`` / ``get_world_connection`` /
``get_forecasts_connection`` / the trade+world ATTACH ``get_connection``) — the process
boundary relocates WHICH process owns the transaction; it does not relax the ATTACH+SAVEPOINT
cross-DB-write law.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import pathlib
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("zeus.post_trade_capital")

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

_heartbeat_fails = 0

# The post-trade pollers this daemon OWNS (their cascade-liveness obligation moved here from
# the order daemon). Keyed by job id -> the source module:owner string the contract carries.
# Asserted present in the scheduler at boot by _assert_cascade_liveness_contract below.
_OWNED_CASCADE_POLLER_IDS = frozenset({
    "harvester",
    "redeem_reconciler",
    "wrap_intent_creator",
    "wrap_submitter",
    "wrap_reconciler",
})


def _graceful_shutdown(signum, frame) -> None:
    """SIGTERM handler — wait for in-flight jobs then exit 0 (daemon parity)."""
    logger.info("post-trade-capital daemon received SIGTERM; shutting down scheduler")
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
    scheduler_jobs_health.json OK entry; on exception logs + writes FAILED. The redeem/wrap
    pollers intentionally RAISE on partial failure (so the operator sees FAILED); this wrapper
    records that failure to scheduler_health without crashing the scheduler — the next tick
    retries the durable state-machine rows.
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


def _assert_cascade_liveness_contract(scheduler) -> None:
    """Boot-time fail-closed mirror of src/main.py:_assert_cascade_liveness_contract.

    The cascade-liveness antibody moved to this process WITH the pollers it guards. Refuses
    to start the daemon if any required poller this daemon OWNS (per
    architecture/cascade_liveness_contract.yaml, owner pointing at
    src/execution/post_trade_capital.py) is missing from the scheduler. Guards against an edit
    that deletes a job registration without updating the contract (or vice versa).

    Only the pollers whose contract owner is the P4 module are enforced here — pollers owned
    by other daemons are enforced by those daemons' own boot guards.
    """
    import yaml

    contract_path = (
        pathlib.Path(__file__).resolve().parent.parent.parent
        / "architecture"
        / "cascade_liveness_contract.yaml"
    )
    if not contract_path.exists():
        logger.error(
            "_assert_cascade_liveness_contract: %s missing; skipping boot check",
            contract_path,
        )
        return
    contract = yaml.safe_load(contract_path.read_text())
    job_ids = {j.id for j in scheduler.get_jobs()}
    missing: list[tuple[str, str]] = []
    for sm in contract.get("state_machines", []) or []:
        for poller in sm.get("required_pollers", []) or []:
            owner = str(poller.get("owner", ""))
            # Only enforce the pollers THIS daemon owns (P4 module).
            if "post_trade_capital" not in owner:
                continue
            if poller["id"] not in job_ids:
                missing.append((sm["table"], poller["id"]))
    if missing:
        raise SystemExit(
            f"FATAL: cascade_liveness_contract violation in post-trade-capital daemon: "
            f"missing pollers {missing!r}. Refusing to boot. Either register the job in "
            f"src/ingest/post_trade_capital_daemon.py OR repoint the contract owner in "
            f"architecture/cascade_liveness_contract.yaml."
        )


def _write_post_trade_capital_heartbeat() -> None:
    """Write daemon-heartbeat-post-trade-capital.json every 60s (liveness for the sensor)."""
    global _heartbeat_fails
    from src.config import state_path

    path = state_path("daemon-heartbeat-post-trade-capital.json")
    try:
        payload = {
            "daemon": "post-trade-capital",
            "alive_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "git_head": _PROCESS_GIT_HEAD,
        }
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
        _heartbeat_fails = 0
    except Exception as exc:  # noqa: BLE001
        _heartbeat_fails += 1
        logger.error("post-trade-capital heartbeat write failed (%d): %s", _heartbeat_fails, exc)


def main() -> None:
    global _scheduler
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
    logger.info("Zeus post-trade-capital daemon starting (pid=%d)", os.getpid())

    # Proxy health gate — must precede any HTTP call (Gamma/CLOB/RPC).
    from src.data.proxy_health import bypass_dead_proxy_env_vars
    bypass_dead_proxy_env_vars()

    # The lifted post-trade cycle bodies.
    from src.execution.post_trade_capital import (
        chain_sync_read_cycle,
        collateral_snapshot_refresh_cycle,
        _harvester_cycle,
        _redeem_reconciler_cycle,
        _wrap_intent_creator_cycle,
        _wrap_submitter_cycle,
        _wrap_reconciler_cycle,
    )

    # Pre-flight (system_decomposition_plan §8 Step 2 mitigation): assert this process can open
    # the trades-DB and world-DB writer connections under the sanctioned path before entering
    # the loop. A misconfigured producer = stuck capital, so fail LOUD at boot rather than
    # silently. (forecasts conn is opened per-tick by the harvester; not pre-flighted here to
    # avoid holding a third connection at boot.)
    from src.state.db import get_trade_connection, get_world_connection

    _trade_conn = get_trade_connection(write_class="live")
    try:
        _trade_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settlement_commands'"
        ).fetchone()
    finally:
        _trade_conn.close()
    _world_conn = get_world_connection()
    try:
        _world_conn.execute("SELECT 1").fetchone()
    finally:
        _world_conn.close()
    logger.info(
        "post-trade-capital pre-flight OK: trades-DB settlement_commands + world-DB reachable "
        "under the sanctioned path"
    )

    # SIGTERM → graceful shutdown.
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    # Single-writer executor mirrors the order daemon's per-job max_instances=1 + coalesce:
    # each poller is serialized against itself; distinct pollers may interleave, but each owns
    # its own connection lifecycle so there is no shared in-process write lock to contend.
    _scheduler = BlockingScheduler()

    # Cadences are byte-identical to the order daemon's former registrations (src/main.py):
    #   chain_sync_and_exit_monitor 2-min ; harvester 1h ; redeem_submitter 5-min ;
    #   redeem_reconciler 10-min ; wrap_intent_creator 5-min ; wrap_submitter 2-min ;
    #   wrap_reconciler 2-min. Job ids are byte-identical so scheduler_health keying carries
    #   over (the chain-sync READ job uses a NEW id 'chain_sync_read' since the order daemon's
    #   'chain_sync_and_exit_monitor' id now belongs to the exit-SUBMIT phase that STAYS in P1).
    _scheduler.add_job(
        _scheduler_job("chain_sync_read")(chain_sync_read_cycle),
        "interval", minutes=2, id="chain_sync_read",
        max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        _scheduler_job("harvester")(_harvester_cycle),
        "interval", hours=1, id="harvester",
        max_instances=1, coalesce=True,
    )
    # redeem_submitter job registration DELETED 2026-07-08 (R6-a) along with
    # _redeem_submitter_cycle itself -- see module docstring.
    _scheduler.add_job(
        _scheduler_job("redeem_reconciler")(_redeem_reconciler_cycle),
        "interval", minutes=10, id="redeem_reconciler",
        max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        _scheduler_job("wrap_intent_creator")(_wrap_intent_creator_cycle),
        "interval", minutes=5, id="wrap_intent_creator",
        max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        _scheduler_job("wrap_submitter")(_wrap_submitter_cycle),
        "interval", minutes=2, id="wrap_submitter",
        max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        _scheduler_job("wrap_reconciler")(_wrap_reconciler_cycle),
        "interval", minutes=2, id="wrap_reconciler",
        max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        _scheduler_job("collateral_snapshot_refresh")(collateral_snapshot_refresh_cycle),
        "interval", seconds=30, id="collateral_snapshot_refresh",
        max_instances=1, coalesce=True,
        next_run_time=datetime.now(timezone.utc),
    )

    # 60s liveness heartbeat (file-only). The heartbeat-sensor watches this file's mtime.
    _scheduler.add_job(
        _write_post_trade_capital_heartbeat,
        "interval", seconds=60, id="post_trade_capital_heartbeat",
        max_instances=1, coalesce=True,
        next_run_time=datetime.now(timezone.utc),
    )

    # Boot-time fail-closed cascade-liveness contract check — the antibody travels with the
    # jobs. MUST run AFTER all add_job calls (so it sees the complete job set) and BEFORE
    # scheduler.start() (so a contract violation prevents booting).
    _assert_cascade_liveness_contract(_scheduler)

    jobs = [j.id for j in _scheduler.get_jobs()]
    logger.info("post-trade-capital scheduler ready. %d jobs: %s", len(jobs), jobs)

    try:
        _scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zeus post-trade-capital daemon shutting down")
        _shutdown_scheduler_if_running(_scheduler, wait=True)


if __name__ == "__main__":
    main()
