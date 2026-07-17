# Created: 2026-06-08
# Last reused or audited: 2026-07-08
# Authority basis: docs/architecture/system_decomposition_plan.md
#   §4.2 (Price-Channel / CLOB-Fact Ingest), §6 (P3 row), §7 (I2 no-back-coupling:
#   durable fill bridge + execution_feasibility_evidence), §8 Step 3 (lift the
#   user-channel WS thread + market-channel + reconcile cycles), §9 (regression-
#   unconstructable proof — failure-domain isolation of the WS submit latch).
#   docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R6 (2026-07-08 split: re-decision
#   routing extracted to src.events.price_channel_redecision_router — see below).
"""P3 price-channel / CLOB-fact ingest producer logic — lifted out of the order daemon.

This module owns the CLOB-FACT producer that keeps the Polymarket user/market WebSocket
subscribed and durably bridges fills + book facts into the tables the order runtime only
READS (interface I2):

  - the durable fill bridge (``position_current`` / ``position_events`` materialised from
    ``edli_live_order_events`` confirmed fills), and
  - ``execution_feasibility_evidence`` (the pre-submit book witness rows).

WHY IT LIVES HERE (and NOT in src.main) — system_decomposition_plan §4.2/§9:
  - It is ALWAYS_ON (criterion 1): the channel must stay subscribed while trading is
    paused, so fills/book-facts keep flowing even when the order daemon makes no decisions.
  - It owns a DISTINCT external authority (criterion 2): the Polymarket user/market CLOB
    WebSocket stream is its own truth source; the order runtime is a pure CONSUMER.
  - It is FAILURE-DOMAIN-isolated (criterion 3): a WS auth/transport flap must not crash
    the reactor. CRUCIALLY this is also what kills the reduce_only-FOREVER LATCH: the WS
    thread, on auth failure, records a gap in the PROCESS-GLOBAL ``ws_gap_guard`` submit
    latch (``record_gap(AUTH_FAILED)``). When the thread lived in the ORDER DAEMON, that
    record_gap poisoned the same in-memory latch the order daemon's executor reads via
    ``assert_ws_allows_submit`` — the daemon stayed stuck in reduce_only mode forever
    (src/main.py:2610-2622 history). With the thread lifted HERE, its record_gap writes
    only THIS process's ws_gap_guard memory; the order daemon's submit latch is in a
    different address space and can no longer be poisoned by a WS flap. The order daemon
    sees a WS outage only as STALE/ABSENT ``execution_feasibility_evidence`` rows
    (DB-mediated, observable), never as a shared-process exception or a latched gate.

THE DURABLE FILL BRIDGE IS THE PERSISTED TRUTH (system_decomposition_plan §8 Step 3):
  ``_edli_durable_fill_bridge_scan`` re-derives the bridge work set from the persisted
  ``edli_live_order_events`` on EVERY cycle for fills that still have no
  ``position_current`` row, so NO confirmed fill is lost across the conceptual cutover
  from "WS thread in src.main" to "WS thread in P3". Already-materialised historical
  projection repair is an explicit maintenance action, not part of the per-minute live
  hot path, because it can rewrite old rows and contend with fresh book/substrate writes.
  The order-runtime BOOT recovery (``_edli_boot_fill_bridge_recovery``, which STAYS in
  src.main) imports THIS same scan helper so a restart on either side heals any orphaned
  confirmed fill. The scan is the single canonical copy — src.main imports it from here
  (mirroring the P4 pattern ``from src.execution.post_trade_capital import
  _harvester_cycle``).

NO-BACK-COUPLING (system_decomposition_plan §7 I2): P3's trigger is the WS stream + its
  own 1-min reconcile clock. The reactor reads the durable fill bridge; it never signals
  P3. P3 is NEVER gated on the order daemon's queue/flags.

INV-37: the reconcile cycle's fill-bridge cross-DB write (world.edli_live_order_events ->
  trades.position_current/position_events) goes through the sanctioned ATTACH+SAVEPOINT
  path (``get_trade_connection_with_world_required``); no independent cross-DB connection
  is opened.

ALL imports are LAZY (inside functions), exactly as the order daemon kept them, so this
  module's top-level import graph pulls in NO trading lane (src.main / src.engine /
  src.execution / src.strategy / src.signal) — failure-domain isolation (criterion 3).

RE-DECISION ROUTING LIVES ELSEWHERE (R6 split, 2026-07-08 — EXECUTION_MASTER §E R6
  defect #4: "venue does not decide who re-solves"): deciding WHICH money-path families a
  book move should trigger a re-solve for is a decision-layer policy, not a venue-fact-
  bridge fact translation. That routing now lives in
  ``src.events.price_channel_redecision_router``; this module only WIRES its sink in as an
  injected ``market_event_sink`` dependency of ``MarketChannelIngestor`` (still a lazy
  import at each wiring call site — the router module is never imported at load time).
  The pre-split private names are still resolvable as ``price_channel_ingest._edli_X`` /
  ``from src.ingest.price_channel_ingest import _edli_X`` via the module ``__getattr__``
  below, so no external caller (tests included) needed to repoint.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import settings

logger = logging.getLogger("zeus.price_channel_ingest")

# Re-decision routing surface (R6 split, 2026-07-08): these names moved to
# src.events.price_channel_redecision_router. Resolved lazily via __getattr__ below (PEP
# 562) so this module's own top-level import graph stays free of the cross-module import
# (matching "ALL imports are LAZY" above) while every pre-split external reference —
# `from src.ingest.price_channel_ingest import _edli_X` / `pci._edli_X` — keeps working.
_REDECISION_ROUTER_EXPORTS = (
    "_edli_quote_event_token_ids",
    "_edli_money_path_family_keys_for_tokens",
    "_edli_held_family_keys_for_tokens",
    "_edli_own_resting_order_token_ids",
    "_edli_resting_family_keys_for_tokens",
    "_edli_screened_entry_family_keys_for_price_channel",
    "_edli_pending_redecision_entity_keys",
    "_edli_redecision_event_with_origin",
    "_edli_emit_price_channel_redecisions_for_events",
    "_edli_price_channel_redecision_sink",
)


def __getattr__(name: str):
    if name in _REDECISION_ROUTER_EXPORTS:
        from src.events import price_channel_redecision_router as _router

        return getattr(_router, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# --- module globals (moved verbatim from src/main.py) -----------------------
# The WS user-channel ingestor handle + its daemon thread, and the market-channel
# ingestor thread. These are PROCESS-LOCAL to P3; nothing in the order daemon references
# them anymore (the latch writer they arm runs only in this address space).
_user_channel_ingestor = None
_user_channel_thread: "threading.Thread | None" = None
_edli_market_channel_thread: "threading.Thread | None" = None

# In-process lock that serializes the market-channel reactive snapshot refresh against
# itself. (In P2 the substrate observer owns its OWN copy of a like-named lock for its two
# producers; this P3 copy only serializes the market-channel refresh callback within this
# process — the two processes write the snapshot table via the same single-writer
# discipline, and the lock is per-process by construction.)
_market_substrate_refresh_lock = threading.Lock()
_held_quote_seed_refresh_lock = threading.Lock()
_candidate_quote_seed_refresh_lock = threading.Lock()

# Live-execution-mode constants (kept aligned with src/main.py) — needed by the
# reconcile-runtime gate. Kept LOCAL here so the lane module never imports src.main.
LIVE_EXECUTION_MODES = {
    "legacy_cron",
    "edli_live",
    "disabled",
}
EDLI_EVENT_DRIVEN_MODES = {
    "edli_live",
}

MARKET_CHANNEL_CANDIDATE_QUOTE_REFRESH_BUDGET_SECONDS_DEFAULT = 30.0
MARKET_CHANNEL_HELD_QUOTE_REFRESH_BUDGET_SECONDS_DEFAULT = 30.0
MARKET_CHANNEL_PRIORITY_QUOTE_REFRESH_CHUNK_SIZE_DEFAULT = 4
MARKET_CHANNEL_CANDIDATE_PRIORITY_RECENT_ROW_SCAN_MIN = 128
MARKET_CHANNEL_CANDIDATE_PRIORITY_RECENT_ROW_SCAN_MAX = 2048
MARKET_CHANNEL_HELD_QUOTE_REFRESH_MAX_TOKENS_PER_CYCLE_DEFAULT = 32
MARKET_CHANNEL_CANDIDATE_QUOTE_REFRESH_MAX_TOKENS_PER_CYCLE_DEFAULT = 32
PRICE_CHANNEL_DB_WRITE_LEASE_DEADLINE_MS = 15000
PRICE_CHANNEL_DB_WRITE_MAX_HOLD_MS = 1000
PRICE_CHANNEL_CLOB_REQUEST_MAX_TIMEOUT_SECONDS = 2.5
PRICE_CHANNEL_CLOB_REQUEST_DEADLINE_RESERVE_SECONDS = 0.25


def _bound_price_channel_sqlite_wait(conn) -> None:
    """Keep SQLite contention inside the price-channel writer hold budget.

    The composed world+trade gate serializes every live writer behind this
    process. A connection retaining the repo-wide 30s SQLite busy timeout can
    therefore hold all three gates while waiting for a legacy writer, turning
    ordinary backpressure into a cross-process deadlock. The gate's declared
    1s maximum hold is only telemetry; make it executable for each attached
    price-channel writer connection.
    """

    conn.execute(f"PRAGMA busy_timeout = {PRICE_CHANNEL_DB_WRITE_MAX_HOLD_MS}")


def _price_channel_clob_timeout(deadline_monotonic: float):
    """Return a per-request CLOB timeout bounded by the refresh wall-clock budget."""

    remaining = float(deadline_monotonic) - time.monotonic()
    reserve = PRICE_CHANNEL_CLOB_REQUEST_DEADLINE_RESERVE_SECONDS
    if remaining <= reserve:
        raise TimeoutError(
            f"price-channel quote refresh budget exhausted before CLOB fetch: "
            f"remaining_seconds={remaining:.3f}"
        )
    budget = max(0.1, remaining - reserve)
    phase = min(PRICE_CHANNEL_CLOB_REQUEST_MAX_TIMEOUT_SECONDS, budget)

    import httpx

    return httpx.Timeout(
        connect=min(2.0, phase),
        read=phase,
        write=min(1.0, phase),
        pool=min(0.5, phase),
    )


def _budgeted_orderbook_fetchers(clob, *, deadline_monotonic: float):
    """Wrap CLOB book fetchers so every REST call consumes the caller's budget."""

    def _fetch_orderbook(token_id: str) -> dict:
        return clob.get_orderbook_snapshot(
            token_id,
            timeout=_price_channel_clob_timeout(deadline_monotonic),
        )

    fetch_many = getattr(clob, "get_orderbook_snapshots", None)
    if fetch_many is None:
        return _fetch_orderbook, None

    def _fetch_orderbooks(token_ids: list[str]) -> dict[str, dict]:
        return fetch_many(
            token_ids,
            timeout=_price_channel_clob_timeout(deadline_monotonic),
        )

    return _fetch_orderbook, _fetch_orderbooks


class _PriceChannelWorldTradeWriteGate:
    """Reusable context manager for one price-channel world+trade write unit."""

    def __init__(self, *, owner: str) -> None:
        self._owner = owner
        self._stack: contextlib.ExitStack | None = None

    def __enter__(self):
        from src.events.triggers.market_channel_ingestor import _world_write_mutex
        from src.state.write_coordinator import (
            DBIdentity,
            default_runtime_write_coordinator,
        )

        stack = contextlib.ExitStack()
        try:
            # Global lock order is legacy world writer mutex first, then the
            # canonical multi-DB coordinator.  The money path already enters
            # the world mutex before it reaches trade-owned writes.  Taking the
            # coordinator's WORLD+TRADE gates first here creates the inverse
            # edge and can deadlock both daemons (price channel waits for the
            # world mutex while entry/exit waits for a trade writer gate).
            stack.enter_context(_world_write_mutex())
            stack.enter_context(
                default_runtime_write_coordinator().lease(
                    (DBIdentity.WORLD, DBIdentity.TRADE),
                    owner=self._owner,
                    write_class="live",
                    deadline_ms=PRICE_CHANNEL_DB_WRITE_LEASE_DEADLINE_MS,
                    max_hold_ms=PRICE_CHANNEL_DB_WRITE_MAX_HOLD_MS,
                )
            )
        except BaseException:
            stack.close()
            raise
        self._stack = stack
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        if self._stack is None:
            return False
        try:
            return self._stack.__exit__(exc_type, exc, tb)
        finally:
            self._stack = None


def _edli_price_channel_world_trade_write_gate(*, owner: str) -> _PriceChannelWorldTradeWriteGate:
    return _PriceChannelWorldTradeWriteGate(owner=owner)


@contextlib.contextmanager
def _edli_price_channel_world_write_connection(*, owner: str):
    """Yield one WORLD writer after all decision reads have completed."""

    from src.events.triggers.market_channel_ingestor import _world_write_mutex
    from src.state.db import get_world_connection
    from src.state.write_coordinator import (
        DBIdentity,
        default_runtime_write_coordinator,
    )

    with _world_write_mutex():
        with default_runtime_write_coordinator().lease(
            (DBIdentity.WORLD,),
            owner=owner,
            write_class="live",
            deadline_ms=PRICE_CHANNEL_DB_WRITE_LEASE_DEADLINE_MS,
            max_hold_ms=PRICE_CHANNEL_DB_WRITE_MAX_HOLD_MS,
        ):
            conn = get_world_connection(write_class=None)
            _bound_price_channel_sqlite_wait(conn)
            try:
                yield conn
            finally:
                conn.close()


def _edli_price_channel_trade_write_context_factory(*, owner: str):
    def _factory():
        from src.state.write_coordinator import DBIdentity, default_runtime_write_coordinator

        return default_runtime_write_coordinator().lease(
            (DBIdentity.TRADE,),
            owner=owner,
            write_class="live",
            deadline_ms=PRICE_CHANNEL_DB_WRITE_LEASE_DEADLINE_MS,
            max_hold_ms=PRICE_CHANNEL_DB_WRITE_MAX_HOLD_MS,
        )

    return _factory


def _rest_quote_refresh_backpressure_result(
    *,
    kind: str,
    started_monotonic: float,
    budget: float,
    token_ids: int,
    token_metadata: int,
    attempted_tokens: int,
    extra: dict | None = None,
) -> dict:
    elapsed_seconds = max(0.0, time.monotonic() - started_monotonic)
    result = {
        f"{kind}_quote_refresh_events": 0,
        f"{kind}_quote_refresh_attempted_tokens": 0,
        "budget_seconds": budget,
        "elapsed_seconds": elapsed_seconds,
        "budget_exhausted": False,
        "budget_skipped_tokens": max(0, int(attempted_tokens)),
        "skipped": f"price_channel_{kind}_quote_refresh_in_progress",
        "backpressure": True,
    }
    if kind == "held":
        result.update(
            {
                "held_priority_token_ids": int(token_ids),
                "held_token_metadata": int(token_metadata),
            }
        )
    else:
        result.update(
            {
                "candidate_priority_token_ids": int(token_ids),
                "candidate_token_metadata": int(token_metadata),
            }
        )
    if extra:
        result.update(extra)
    return result


def _price_channel_quote_refresh_failed(
    result: dict,
    *,
    token_key: str,
    event_key: str,
) -> tuple[bool, str | None]:
    """Return business-health failure for quote refresh that made no coverage progress."""

    token_count = int(result.get(token_key) or 0)
    events = int(result.get(event_key) or 0)
    skipped_tokens = int(result.get("budget_skipped_tokens") or 0)
    if token_count <= 0:
        return False, None
    if result.get("backpressure"):
        return True, str(result.get("write_backpressure_reason") or result.get("skipped") or "quote_refresh_backpressure")
    if skipped_tokens > 0:
        if events > 0:
            return True, "quote_refresh_partial_coverage"
        return True, "quote_refresh_budget_exhausted_no_coverage"
    if result.get("budget_exhausted") and events <= 0:
        return True, "quote_refresh_budget_exhausted_no_coverage"
    if events > 0:
        return False, None
    skipped = str(result.get("skipped") or "")
    if skipped:
        return True, skipped
    return False, None


# Required env for the user-channel WS (moved verbatim from src/main.py:1867).
USER_CHANNEL_REQUIRED_ENV_VARS = (
    "ZEUS_USER_CHANNEL_WS_ENABLED",
    "POLYMARKET_USER_WS_CONDITION_IDS",
)


# ---------------------------------------------------------------------------
# Small pure helpers (moved verbatim from src/main.py). _settings_section /
# _live_execution_mode / _truthy_env / _edli_bounded_positive_int are tiny pure
# utilities; the lane module carries its own copies so it never imports src.main.
# (_edli_bounded_positive_int is ALSO used by staying src.main code, so src.main
# keeps its copy too — both copies are byte-identical pure functions.)
# ---------------------------------------------------------------------------

def _settings_section(name: str, default=None):
    source = settings._data if hasattr(settings, "_data") else settings
    if isinstance(source, dict):
        value = source.get(name)
        if value is None and name == "edli_v1":
            value = source.get("edli")
        return value if value is not None else default
    try:
        return source[name]
    except KeyError:
        if name == "edli_v1":
            try:
                return source["edli"]
            except KeyError:
                pass
        return default


def _live_execution_mode(edli_cfg: dict) -> str:
    mode = str(edli_cfg.get("live_execution_mode") or "legacy_cron")
    if mode not in LIVE_EXECUTION_MODES:
        raise ValueError(f"UNSUPPORTED_LIVE_EXECUTION_MODE:{mode}")
    return mode


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in {"1", "true", "yes", "on"}


def _edli_bounded_positive_int(config: dict, key: str, *, default: int, maximum: int) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    if value <= 0:
        value = default
    return max(1, min(value, maximum))


def _edli_bounded_positive_float(
    config: dict,
    key: str,
    *,
    default: float,
    maximum: float,
) -> float:
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    if value <= 0:
        value = default
    return max(0.001, min(value, maximum))


def _edli_quote_refresh_max_tokens(
    config: dict,
    key: str,
    *,
    default: int,
    maximum: int = 128,
) -> int:
    return _edli_bounded_positive_int(config, key, default=default, maximum=maximum)


def _row_get(row, key: str):
    try:
        return row[key]
    except Exception:
        try:
            return getattr(row, key)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# User-channel condition-id derivation (moved verbatim from src/main.py).
# ---------------------------------------------------------------------------

def _parse_market_event_recorded_at(value: object) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _dedupe_user_channel_condition_ids(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        condition_id = str(value or "").strip()
        if not condition_id or condition_id in seen:
            continue
        seen.add(condition_id)
        result.append(condition_id)
    return result


def _market_events_fallback_max_age_hours() -> float:
    raw = os.environ.get("ZEUS_USER_CHANNEL_WS_MARKET_EVENTS_FALLBACK_MAX_AGE_HOURS", "36")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "invalid ZEUS_USER_CHANNEL_WS_MARKET_EVENTS_FALLBACK_MAX_AGE_HOURS=%r; "
            "using default 36h",
            raw,
        )
        return 36.0
    if value <= 0:
        logger.warning(
            "non-positive ZEUS_USER_CHANNEL_WS_MARKET_EVENTS_FALLBACK_MAX_AGE_HOURS=%r; "
            "using default 36h",
            raw,
        )
        return 36.0
    return value


def _market_events_user_channel_condition_ids(
    *,
    now: datetime | None = None,
) -> list[str]:
    """Read fresh condition_ids from canonical market_events."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    max_age_hours = _market_events_fallback_max_age_hours()
    cutoff = current - timedelta(hours=max_age_hours)
    try:
        from src.state.db import get_forecasts_connection

        conn = get_forecasts_connection()
        try:
            rows = conn.execute(
                """
                SELECT condition_id, target_date, recorded_at
                  FROM market_events
                 WHERE condition_id IS NOT NULL
                   AND TRIM(condition_id) != ''
                   AND target_date >= ?
                 ORDER BY recorded_at DESC, condition_id
                 LIMIT 2048
                """,
                (current.date().isoformat(),),
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("user-channel WS market_events fallback failed: %s", exc)
        return []

    fresh_ids: list[str] = []
    for row in rows:
        recorded_at = _parse_market_event_recorded_at(row["recorded_at"])
        if recorded_at is None or recorded_at < cutoff:
            continue
        fresh_ids.append(row["condition_id"])
    return _dedupe_user_channel_condition_ids(fresh_ids)


def _auto_derive_user_channel_condition_ids(
    *,
    now: datetime | None = None,
) -> list[str]:
    """Derive the user-channel WS subscription set.

    Fresh persisted ``market_events`` rows are primary. When those rows are
    missing at boot, Gamma scanning is enabled by default so the one-shot
    user-channel starter does not latch to an empty subscription set for the
    lifetime of the live process. Operators can disable this fallback by setting
    ``ZEUS_USER_CHANNEL_BOOT_GAMMA_SCAN=0``.

    Total failure still returns [] rather than raising; the daemon then stays in
    the fail-closed WS posture recorded by the gap guard.
    """
    persisted_ids = _market_events_user_channel_condition_ids(now=now)
    if persisted_ids:
        return persisted_ids
    if os.getenv("ZEUS_USER_CHANNEL_BOOT_GAMMA_SCAN", "1").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        logger.warning(
            "user-channel WS found no fresh market_events condition_ids; "
            "boot Gamma scan disabled by ZEUS_USER_CHANNEL_BOOT_GAMMA_SCAN=0"
        )
        return []
    try:
        from src.data.market_scanner import (
            MarketEventsPersistenceError,
            extract_executable_condition_ids,
            find_weather_markets_or_raise,
        )

        events = find_weather_markets_or_raise(
            min_hours_to_resolution=0.0,
            include_slug_pattern=False,
        )
        return extract_executable_condition_ids(events)
    except MarketEventsPersistenceError as exc:
        logger.warning(
            "user-channel WS scanner: market_events persistence failure — "
            "degrading to empty condition_ids: %s", exc,
        )
        return []
    except Exception as exc:
        logger.warning("user-channel WS scanner failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# PRODUCER 1: the user-channel WS ingestor thread (moved verbatim from
# src/main.py:_start_user_channel_ingestor_if_enabled). THIS is the WS-failure
# latch WRITER — in P3 its record_gap can only poison THIS process's
# ws_gap_guard, never the order daemon's (the reduce_only-forever antibody).
# ---------------------------------------------------------------------------

def _start_user_channel_ingestor_if_enabled() -> None:
    """Start M3 Polymarket user-channel ingest in a daemon thread when enabled.

    Disabled by default so M3 adds no live WebSocket side effect until an
    operator explicitly enables `ZEUS_USER_CHANNEL_WS_ENABLED=1` and supplies
    condition IDs or enables condition auto-derive. L2 API credentials come
    from the Polymarket adapter's signer-bound SDK client, not static env. If
    enabled but misconfigured, the WS guard records an auth/config gap so new
    submits fail closed.

    Live-blockers 2026-05-01: when the WS is NOT enabled (or required env
    vars are missing) we now emit a single CLEAR WARNING line listing every
    missing var. Today the silent skip leaves operators with the cryptic
    ``ws_user_channel.gap_reason='not_configured'`` symptom and no surface
    explanation of which env vars to add to the launchd plist before the
    daemon can leave reduce_only mode.

    Auto-derive (2026-05-01): when ``ZEUS_USER_CHANNEL_WS_AUTO_DERIVE=1`` is
    set together with the master toggle and ``POLYMARKET_USER_WS_CONDITION_IDS``
    is empty, the subscription list is derived from the live market scanner
    so the daemon subscribes to exactly the markets it can trade, without
    a hardcoded plist value that would drift from on-chain truth as markets
    rotate (operator directive 2026-05-01: hardcoded values are structural
    failures). Operator can still pin a list via the env var; a non-empty
    env var always wins. Auto-derive returning 0 markets is a WARNING, not
    an error — the daemon stays in reduce_only mode, the WS guard reports
    ``condition_ids_missing``, and no exception escapes boot.
    """
    global _user_channel_ingestor, _user_channel_thread
    if not _truthy_env("ZEUS_USER_CHANNEL_WS_ENABLED"):
        missing = [
            name for name in USER_CHANNEL_REQUIRED_ENV_VARS
            if not (os.environ.get(name) or "").strip()
        ]
        logger.warning(
            "user-channel WS not configured: missing env vars %s; "
            "daemon stays in reduce_only=True mode",
            missing,
        )
        return
    if _user_channel_thread is not None and _user_channel_thread.is_alive():
        return

    raw_markets = os.environ.get("POLYMARKET_USER_WS_CONDITION_IDS", "")
    condition_ids = [m.strip() for m in raw_markets.split(",") if m.strip()]
    auto_derived = False
    if not condition_ids and _truthy_env("ZEUS_USER_CHANNEL_WS_AUTO_DERIVE"):
        condition_ids = _auto_derive_user_channel_condition_ids()
        auto_derived = True
        logger.info(
            "user-channel WS auto-derive yielded %d condition_ids "
            "(POLYMARKET_USER_WS_CONDITION_IDS empty, ZEUS_USER_CHANNEL_WS_AUTO_DERIVE=1)",
            len(condition_ids),
        )

    if not condition_ids:
        from src.control.ws_gap_guard import record_gap

        record_gap("condition_ids_missing", subscription_state="MARKET_MISMATCH")
        if auto_derived:
            logger.warning(
                "user-channel WS auto-derive yielded 0 condition_ids; daemon stays "
                "in reduce_only=True mode. Markets may be empty or the gamma query "
                "failed; check src.data.market_scanner."
            )
            return
        raise RuntimeError("POLYMARKET_USER_WS_CONDITION_IDS is required when ZEUS_USER_CHANNEL_WS_ENABLED=1")

    from src.data.polymarket_client import PolymarketClient
    from src.control.ws_gap_guard import record_gap
    from src.ingest.polymarket_user_channel import PolymarketUserChannelIngestor, WSAuth

    adapter = PolymarketClient()._ensure_v2_adapter()

    _WS_RETRY_BASE_SECONDS = 5
    _WS_RETRY_MAX_SECONDS = 300  # cap at 5 minutes

    # Boot-time transient failures from signer-bound L2 credential derivation
    # used to latch AUTH_FAILED forever because the
    # creds fetch lived outside the retry loop with a bare `return` on exception —
    # no thread ever started, ws_gap_guard never received a SUBSCRIBED message,
    # daemon stayed in reduce_only=True until the next SIGTERM.
    #
    # Structural fix: factor creds+ingestor construction into a helper that gets
    # invoked (a) eagerly so a healthy boot constructs synchronously like before,
    # and (b) again from inside the retry loop whenever the prior attempt failed
    # or the start() coroutine exited. Either path independently advances the
    # daemon — transient API failures no longer permanently latch the WS guard.
    # Map exception types to ws_gap_guard subscription_state so operator
    # telemetry distinguishes "auth/creds failed" from generic transport/network
    # failures. AUTH_FAILED gates differently from DISCONNECTED in the gap guard
    # (auth requires operator intervention; disconnect retries cleanly).
    # Conservative classification: only treat creds-shape failures as AUTH_FAILED.
    def _classify_build_failure(exc: BaseException) -> str:
        name = type(exc).__name__
        msg = str(exc).lower()
        auth_signals = (
            "creds",
            "auth",
            "api_key",
            "api-key",
            "passphrase",
            "secret",
            "signature",
            "unauthorized",
            "401",
            "403",
        )
        if any(sig in msg for sig in auth_signals):
            return "AUTH_FAILED"
        if name in {"WSAuthMissing", "ValueError", "TypeError"} and "creds" in msg:
            return "AUTH_FAILED"
        return "DISCONNECTED"

    def _build_ingestor() -> "PolymarketUserChannelIngestor | None":
        global _user_channel_ingestor
        # Invalidate the adapter's memoized SDK client so this attempt forces a
        # fresh signer-bound L2 credential derivation rather than reusing a cached
        # client whose creds were None from a prior failed boot
        # (codereview-may19 / Codex P1: src/venue/polymarket_v2_adapter.py:286
        # memoizes self._client; without reset, every retry sees the same bad
        # creds and the loop never recovers).
        try:
            adapter._client = None  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            # Adapter might not expose the attribute on all stub paths; non-fatal.
            pass

        try:
            sdk_client = adapter._sdk_client()
            sdk_creds = sdk_client.creds
            if sdk_creds is None:
                raise RuntimeError(
                    "adapter._sdk_client().creds is None "
                    "(signer-bound L2 credential derivation failed)"
                )
            ws_auth = WSAuth(
                api_key=sdk_creds.api_key,
                secret=sdk_creds.api_secret,
                passphrase=sdk_creds.api_passphrase,
            )
            ingestor = PolymarketUserChannelIngestor(
                adapter, condition_ids, auth=ws_auth
            )
            _user_channel_ingestor = ingestor
            return ingestor
        except Exception as exc:
            subscription_state = _classify_build_failure(exc)
            gap_reason = f"user_channel_attempt_failed:{type(exc).__name__}"
            record_gap(gap_reason, subscription_state=subscription_state)
            logger.error(
                "M3 user-channel ingestor build failed (subscription_state=%s): %s; "
                "will retry inside daemon thread",
                subscription_state,
                exc,
                exc_info=True,
            )
            return None

    # Eager best-effort construction (preserves the synchronous-build contract
    # that callers and unit tests rely on when the boot environment is healthy).
    _build_ingestor()

    def _runner() -> None:
        global _user_channel_ingestor
        import asyncio
        import time as _time

        attempt = 0
        while True:
            attempt += 1
            ingestor = _user_channel_ingestor or _build_ingestor()
            if ingestor is not None:
                try:
                    asyncio.run(ingestor.start())
                    logger.warning(
                        "M3 user-channel ingestor exited cleanly; reconnecting"
                    )
                except Exception as exc:
                    logger.error(
                        "M3 user-channel ingestor attempt %d stopped: %s",
                        attempt,
                        exc,
                        exc_info=True,
                    )
                # Force a fresh creds fetch on the next iteration — auth tokens may
                # have expired and a stale ingestor would just fail-loop again.
                _user_channel_ingestor = None
            backoff = min(
                _WS_RETRY_BASE_SECONDS * (2 ** min(attempt - 1, 6)),
                _WS_RETRY_MAX_SECONDS,
            )
            logger.info(
                "M3 user-channel ingestor will retry in %.0fs (attempt %d)",
                backoff,
                attempt,
            )
            _time.sleep(backoff)

    _user_channel_thread = threading.Thread(
        target=_runner,
        name="polymarket-user-channel",
        daemon=True,
    )
    _user_channel_thread.start()
    logger.info(
        "M3 user-channel ingestor thread launched for %d condition_ids "
        "(auto_derived=%s); creds re-fetched per-attempt inside retry loop on failure",
        len(condition_ids),
        auto_derived,
    )


# ---------------------------------------------------------------------------
# EDLI reconcile helper cluster (moved verbatim from src/main.py). All pure /
# DB-only; none import the trading lane.
# ---------------------------------------------------------------------------

def _edli_jsonl_records(path_value: "str | os.PathLike[str] | None") -> list[dict]:
    path_text = str(path_value or "").strip()
    if not path_text:
        return []
    path = Path(path_text)
    if not path.exists():
        return []
    records: list[dict] = []
    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"EDLI_USER_CHANNEL_RECONCILE_QUEUE_INVALID_JSON:{path}:{line_number}") from exc
        if not isinstance(record, dict):
            raise RuntimeError(f"EDLI_USER_CHANNEL_RECONCILE_QUEUE_RECORD_NOT_OBJECT:{path}:{line_number}")
        records.append(record)
    return records


class _EdliJsonlUserChannelReader:
    def __init__(self, path_value: "str | os.PathLike[str] | None"):
        self._path_value = path_value

    def poll(self, *, max_messages: int) -> list[dict]:
        return _edli_jsonl_records(self._path_value)[:max(0, max_messages)]


class _EdliJsonlVenueReconcileReader:
    def __init__(self, path_value: "str | os.PathLike[str] | None"):
        self._facts = _edli_jsonl_records(path_value)

    def reconcile(self, pending) -> dict | None:
        aggregate_id = _row_get(pending, "aggregate_id")
        event_id = _row_get(pending, "event_id")
        final_intent_id = _row_get(pending, "final_intent_id")
        venue_order_id = _row_get(pending, "venue_order_id")
        for fact in self._facts:
            if fact.get("aggregate_id") and fact.get("aggregate_id") == aggregate_id:
                return fact
            if fact.get("venue_order_id") and fact.get("venue_order_id") == venue_order_id:
                return fact
            if fact.get("event_id") == event_id and fact.get("final_intent_id") == final_intent_id:
                return fact
        return None


def _edli_user_channel_reader(edli_cfg: dict) -> _EdliJsonlUserChannelReader:
    return _EdliJsonlUserChannelReader(edli_cfg.get("edli_user_channel_message_queue_path"))


def _edli_venue_reconcile_reader(edli_cfg: dict) -> _EdliJsonlVenueReconcileReader:
    return _EdliJsonlVenueReconcileReader(edli_cfg.get("edli_venue_reconcile_facts_path"))


def _parse_edli_runtime_time(payload: dict, *, default: datetime) -> datetime:
    for key in ("occurred_at", "observed_at", "timestamp", "created_at"):
        value = payload.get(key)
        if not value:
            continue
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        if isinstance(value, str):
            text = value.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError as exc:
                raise RuntimeError(f"EDLI_RUNTIME_TIMESTAMP_INVALID:{key}") from exc
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return default


def _parse_edli_runtime_bool(value, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _resolve_edli_user_channel_aggregate_id(conn, message: dict) -> str:
    aggregate_id = str(message.get("aggregate_id") or "").strip()
    if aggregate_id:
        return aggregate_id
    venue_order_id = str(message.get("venue_order_id") or message.get("order_id") or "").strip()
    if venue_order_id:
        row = conn.execute(
            """
            SELECT aggregate_id
            FROM edli_live_order_projection
            WHERE venue_order_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (venue_order_id,),
        ).fetchone()
        if row is not None:
            return str(_row_get(row, "aggregate_id"))
    event_id = str(message.get("event_id") or "").strip()
    final_intent_id = str(message.get("final_intent_id") or "").strip()
    if event_id and final_intent_id:
        return f"{event_id}:{final_intent_id}"
    raise RuntimeError("EDLI_USER_CHANNEL_MESSAGE_AGGREGATE_UNRESOLVED")


def _edli_user_channel_message_seen(conn, *, aggregate_id: str, message_hash: str) -> bool:
    import json as _json

    if not message_hash:
        return False
    rows = conn.execute(
        """
        SELECT payload_json
        FROM edli_live_order_events
        WHERE aggregate_id = ? AND event_type IN ('UserOrderObserved','UserTradeObserved')
        """,
        (aggregate_id,),
    ).fetchall()
    for row in rows:
        payload = _json.loads(str(_row_get(row, "payload_json")))
        if payload.get("raw_user_channel_message_hash") == message_hash:
            return True
    return False


def _edli_user_channel_message_not_stale(conn, *, aggregate_id: str, occurred_at: datetime) -> None:
    row = conn.execute(
        """
        SELECT occurred_at
        FROM edli_live_order_events
        WHERE aggregate_id = ? AND event_type = 'ExecutionCommandCreated'
        ORDER BY event_sequence DESC
        LIMIT 1
        """,
        (aggregate_id,),
    ).fetchone()
    if row is None:
        return
    command_time = datetime.fromisoformat(str(_row_get(row, "occurred_at")))
    if command_time.tzinfo is None:
        command_time = command_time.replace(tzinfo=timezone.utc)
    if occurred_at < command_time:
        raise RuntimeError("EDLI_USER_CHANNEL_MESSAGE_STALE_BEFORE_COMMAND")


def _edli_pending_reconcile_aggregates(conn, *, limit: int) -> list:
    return list(
        conn.execute(
            """
            SELECT aggregate_id, event_id, final_intent_id, venue_order_id
            FROM edli_live_order_projection
            WHERE pending_reconcile = 1
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (max(0, limit),),
        ).fetchall()
    )


# ---------------------------------------------------------------------------
# THE DURABLE FILL BRIDGE SCAN — the persisted truth shared across the cutover
# (moved verbatim from src/main.py). src.main's BOOT recovery imports THIS.
# ---------------------------------------------------------------------------

def _edli_durable_fill_bridge_scan(
    conn,
    *,
    now=None,
    limit: int = 500,
    already_bridged_repair_limit: int = 0,
) -> int:
    """MF-1: durable, idempotent, self-healing EDLI fill -> position_current scan.

    THE authoritative bridge trigger (replaces the transient
    ``_edli_fill_bridge_aggregate_ids`` set as the source of truth). Finds every
    aggregate in ``edli_live_order_events`` carrying a ``UserTradeObserved`` with
    ``fill_authority_state == 'FILL_CONFIRMED'`` whose deterministic
    ``edli_bridge_position_id`` has NO ``position_current`` row, and materialises
    each via the idempotent canonical bridge.

    Why this closes the orphan window (the verified DEFECT): the old path only
    bridged aggregates that went PENDING->PROCESSED *this cycle*, holding them in
    an in-memory set. A daemon death OR a swallowed bridge exception between the
    inbox PROCESSED commit and the separate bridge commit left a FILL_CONFIRMED
    aggregate with no position_current row; on restart the set was empty and
    nothing re-bridged it -> capital orphaned. This scan re-derives the work set
    durably from ``edli_live_order_events`` (the persisted truth), so it heals any
    such orphan on the very next cycle AND at boot, regardless of process restarts.

    Idempotency: ``materialize_position_current_from_edli_fill`` upserts
    ``position_current`` (ON CONFLICT(position_id) DO UPDATE) and appends
    ``position_events`` keyed UNIQUE(position_id, sequence_no) — re-bridging an
    already-bridged fill is a no-op for events and a safe UPDATE for the
    projection. The absence filter below ALSO skips already-bridged aggregates so
    a healthy daemon does no redundant work.

    Already-bridged repair is opt-in via ``already_bridged_repair_limit``. The
    per-minute live cycle must stay focused on fresh/orphaned fills; repeatedly
    repairing historical projections can hold the trade DB writer and starve the
    substrate/redecision snapshot path.

    INV-37 / transaction ownership: reads ``edli_live_order_events`` and writes
    ``position_current`` / ``position_events`` ON THE SAME connection ``conn``
    (in production a trade connection with ``world`` ATTACHed). Performs NO
    independent connection and does NOT commit — the caller owns the transaction
    boundary (the cycle / boot wrapper commits once after the scan).

    Returns the number of orphaned fills bridged this pass.
    """
    from src.events.edli_position_bridge import (
        DISPOSITION_SETTLED_MARKET,
        DISPOSITION_UNRECOVERABLE_MANUAL_REVIEW,
        _aggregate_event_rows,
        _edli_events_table,
        _has_confirmed_fill,
        _increment_failure_count,
        _latest_payload,
        _market_is_settled,
        _record_settled_disposition,
        _venue_command_row_for_execution_command_id,
        disposition_reason_and_age,
        edli_bridge_position_id,
        edli_bridge_position_id_legacy,
        get_fill_bridge_disposition,
        is_retry_eligible,
        materialize_position_current_from_edli_fill,
        sync_venue_command_position_link_for_edli_fill,
    )

    now = now or datetime.now(timezone.utc)
    now_str = now.isoformat()
    today_utc = now_str[:10]

    table = _edli_events_table(conn)
    try:
        if table == "world.edli_live_order_events":
            sql = """
            SELECT DISTINCT aggregate_id
            FROM world.edli_live_order_events
            WHERE event_type = 'UserTradeObserved'
              AND json_extract(payload_json, '$.fill_authority_state') = 'FILL_CONFIRMED'
            ORDER BY aggregate_id ASC
            """
        elif table == "edli_live_order_events":
            sql = """
            SELECT DISTINCT aggregate_id
            FROM edli_live_order_events
            WHERE event_type = 'UserTradeObserved'
              AND json_extract(payload_json, '$.fill_authority_state') = 'FILL_CONFIRMED'
            ORDER BY aggregate_id ASC
            """
        else:
            raise ValueError(f"unexpected EDLI events table: {table!r}")

        candidate_rows = conn.execute(sql).fetchall()
        incomplete_open_position_ids: set[str] = set()
        command_position_by_aggregate: dict[str, str] = {}
        try:
            incomplete_rows = conn.execute(
                """
                SELECT position_id
                  FROM position_current
                 WHERE phase IN ('active', 'day0_window', 'pending_exit')
                   AND (
                        p_posterior IS NULL
                     OR p_posterior <= 0.0
                     OR entry_method IS NULL
                     OR entry_method = ''
                     OR entry_method = 'ens_member_counting'
                   )
                """
            ).fetchall()
            incomplete_open_position_ids = {
                str(_row_get(r, "position_id"))
                for r in incomplete_rows
                if _row_get(r, "position_id")
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "EDLI durable fill-bridge scan: incomplete projection query failed "
                "(non-fatal; normal scan continues): %s",
                exc,
            )
        try:
            command_rows = conn.execute(
                f"""
                WITH command_events AS (
                    SELECT aggregate_id,
                           json_extract(payload_json, '$.execution_command_id') AS execution_command_id
                      FROM {table}
                     WHERE event_type = 'ExecutionCommandCreated'
                       AND json_extract(payload_json, '$.execution_command_id') IS NOT NULL
                )
                SELECT ce.aggregate_id, vc.position_id
                  FROM command_events ce
                  JOIN venue_commands vc
                    ON vc.command_id = ce.execution_command_id
                    OR vc.decision_id = ce.execution_command_id
                  JOIN position_current pc
                    ON pc.position_id = vc.position_id
                 WHERE vc.position_id IS NOT NULL
                   AND vc.position_id != ''
                """
            ).fetchall()
            command_position_by_aggregate = {
                str(_row_get(r, "aggregate_id")): str(_row_get(r, "position_id"))
                for r in command_rows
                if _row_get(r, "aggregate_id") and _row_get(r, "position_id")
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "EDLI durable fill-bridge scan: command-linked position query failed "
                "(non-fatal; hash/legacy scan continues): %s",
                exc,
            )
        if incomplete_open_position_ids:
            candidate_rows.sort(
                key=lambda r: (
                    0
                    if edli_bridge_position_id(str(_row_get(r, "aggregate_id")))
                    in incomplete_open_position_ids
                    or edli_bridge_position_id_legacy(str(_row_get(r, "aggregate_id")))
                    in incomplete_open_position_ids
                    or command_position_by_aggregate.get(str(_row_get(r, "aggregate_id")))
                    in incomplete_open_position_ids
                    else 1,
                    str(_row_get(r, "aggregate_id")),
                )
            )
    except Exception as exc:  # noqa: BLE001
        # Missing table / attach (e.g. a degraded boot) must not crash the
        # caller — the EDLI events persist and the next cycle retries.
        logger.error(
            "EDLI durable fill-bridge scan: candidate query failed "
            "(non-fatal; retries next cycle): %s",
            exc,
            exc_info=True,
        )
        return 0

    bridged = 0
    new_fills_seen = 0
    already_bridged_link_sync_seen = 0
    already_bridged_repairs_attempted = 0
    for row in candidate_rows:
        aggregate_id = str(_row_get(row, "aggregate_id"))
        position_id = edli_bridge_position_id(aggregate_id)
        # Dual-probe: check BOTH the wide (new, 68-char) ID and the legacy
        # narrow (old, 11-char) ID.  The 101 rows written before FIX #96
        # carry the old short ID; probing only the wide ID would miss them
        # and re-bridge the same aggregate into a second position_current row
        # (duplicate position identity = live-money hazard).
        legacy_position_id = edli_bridge_position_id_legacy(aggregate_id)
        existing = conn.execute(
            """
            SELECT position_id, p_posterior, entry_method
              FROM position_current
             WHERE position_id IN (?, ?)
             ORDER BY CASE WHEN position_id = ? THEN 0 ELSE 1 END
             LIMIT 1
            """,
            (position_id, legacy_position_id, position_id),
        ).fetchone()
        if existing is None:
            command_position_id = command_position_by_aggregate.get(aggregate_id)
            if command_position_id:
                existing = conn.execute(
                    """
                    SELECT position_id, p_posterior, entry_method
                      FROM position_current
                     WHERE position_id = ?
                     LIMIT 1
                    """,
                    (command_position_id,),
                ).fetchone()
            else:
                events_for_command = _aggregate_event_rows(conn, aggregate_id)
                command = _latest_payload(events_for_command, "ExecutionCommandCreated") or {}
                command_row = _venue_command_row_for_execution_command_id(
                    conn,
                    str(command.get("execution_command_id") or ""),
                )
                command_position_id = str(_row_get(command_row, "position_id") or "")
                if command_position_id:
                    existing = conn.execute(
                        """
                        SELECT position_id, p_posterior, entry_method
                          FROM position_current
                         WHERE position_id = ?
                         LIMIT 1
                        """,
                        (command_position_id,),
                    ).fetchone()
        if existing is not None:
            existing_position_id = str(_row_get(existing, "position_id"))
            if already_bridged_link_sync_seen < max(0, already_bridged_repair_limit):
                already_bridged_link_sync_seen += 1
                try:
                    sync_venue_command_position_link_for_edli_fill(
                        conn,
                        aggregate_id,
                        position_id=existing_position_id,
                        now=now,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "EDLI durable fill-bridge: command position-link sync failed "
                        "for already-bridged aggregate=%s position_id=%s: %s",
                        aggregate_id,
                        existing_position_id,
                        exc,
                    )
            try:
                p_posterior = float(_row_get(existing, "p_posterior") or 0.0)
            except (TypeError, ValueError):
                p_posterior = 0.0
            entry_method = str(_row_get(existing, "entry_method") or "")
            incomplete_projection = (
                p_posterior <= 0.0 or entry_method in {"", "ens_member_counting"}
            )
            if (
                incomplete_projection
                and already_bridged_repairs_attempted
                < max(0, already_bridged_repair_limit)
            ):
                already_bridged_repairs_attempted += 1
                try:
                    result = materialize_position_current_from_edli_fill(
                        conn, aggregate_id, now=now
                    )
                    if result is not None:
                        logger.warning(
                            "EDLI durable fill-bridge: REPAIRED incomplete bridged fill "
                            "aggregate=%s -> position_id=%s p_posterior_was=%s "
                            "entry_method_was=%s",
                            aggregate_id,
                            result.get("position_id"),
                            p_posterior,
                            entry_method,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "EDLI durable fill-bridge: incomplete bridged fill repair failed "
                        "for aggregate=%s position_id=%s: %s",
                        aggregate_id,
                        existing_position_id,
                        exc,
                    )
            # Already bridged (wide or legacy id) — idempotent skip.
            continue

        # Disposition check: skip terminally routed aggregates (settled market —
        # accounting truth, over for good). Does NOT count against the new-fill budget.
        prior_disposition = get_fill_bridge_disposition(conn, aggregate_id)
        if prior_disposition == DISPOSITION_SETTLED_MARKET:
            continue

        # An operator/script has diagnosed this aggregate as structurally
        # unrecoverable (never set by this scan itself — see
        # mark_unrecoverable_manual_review). Automatic retry stops wasting
        # attempts on a known-dead payload, but the row stays LOUDLY visible:
        # every pass logs a WARNING with its age so it cannot silently
        # disappear the way the retired permanent quarantine did.
        if prior_disposition == DISPOSITION_UNRECOVERABLE_MANUAL_REVIEW:
            detail = disposition_reason_and_age(conn, aggregate_id, now)
            reason, age_str = detail if detail else ("", "unknown")
            logger.warning(
                "EDLI fill-bridge: aggregate=%s flagged UNRECOVERABLE_MANUAL_REVIEW "
                "age=%s reason=%s -- awaiting operator action, not auto-retried",
                aggregate_id,
                age_str,
                reason,
            )
            continue

        # Retry-cadence gate: an accumulating bridge-failure aggregate is retried
        # only when its decaying backoff window has elapsed (bounded per-cycle
        # cost). It is NEVER excluded — a fresh aggregate or one due for retry
        # falls through; a confirmed fill is truth that must eventually
        # materialise. Does NOT count against the new-fill budget.
        if not is_retry_eligible(conn, aggregate_id, now):
            continue

        # Before attempting to bridge, route fills for already-settled markets into
        # accounting disposition instead of creating active position_current rows.
        try:
            events = _aggregate_event_rows(conn, aggregate_id)
            if events and _has_confirmed_fill(events):
                pre_submit = _latest_payload(events, "PreSubmitRevalidated") or {}
                city = str(pre_submit.get("city") or "").strip()
                target_date = str(pre_submit.get("target_date") or "").strip()
                metric = str(
                    pre_submit.get("metric")
                    or pre_submit.get("temperature_metric")
                    or ""
                ).strip().lower()
                if target_date:
                    is_settled, evidence = _market_is_settled(
                        conn,
                        city=city,
                        target_date=target_date,
                        temperature_metric=metric,
                        today_utc=today_utc,
                    )
                    if is_settled:
                        logger.warning(
                            "EDLI fill-bridge: SETTLED_MARKET_FILL_BOOKED — "
                            "aggregate=%s market already settled (%s); booked "
                            "for accounting, no position_current row created",
                            aggregate_id,
                            evidence,
                        )
                        _record_settled_disposition(conn, aggregate_id, evidence, now_str)
                        continue
        except Exception as settle_exc:  # noqa: BLE001
            logger.debug(
                "EDLI fill-bridge: settled-market check failed for %s (non-fatal): %s",
                aggregate_id,
                settle_exc,
            )

        if new_fills_seen >= max(0, limit):
            break
        new_fills_seen += 1
        try:
            result = materialize_position_current_from_edli_fill(
                conn, aggregate_id, now=now
            )
            if result is not None:
                bridged += 1
                logger.warning(
                    "EDLI durable fill-bridge: HEALED orphaned confirmed fill "
                    "aggregate=%s -> position_id=%s shares=%s cost_basis_usd=%s",
                    aggregate_id,
                    result.get("position_id"),
                    result.get("shares"),
                    result.get("cost_basis_usd"),
                )
        except Exception as exc:  # noqa: BLE001
            error_str = str(exc)
            try:
                attempt_count = _increment_failure_count(conn, aggregate_id, error_str, now_str)
            except Exception:  # noqa: BLE001
                attempt_count = 1
            logger.error(
                "EDLI durable fill-bridge: failed to bridge aggregate %s "
                "(attempt %d; EDLI events persist, retried on decaying backoff "
                "cadence, never excluded): %s",
                aggregate_id,
                attempt_count,
                exc,
                exc_info=True,
            )
    return bridged


def _edli_user_channel_reconcile_runtime_enabled(edli_cfg: dict) -> bool:
    if not edli_cfg.get("enabled"):
        return False
    if bool(edli_cfg.get("edli_user_channel_reconcile_enabled", False)):
        return True
    return False


# ---------------------------------------------------------------------------
# PRODUCER 2: the user-channel / reconcile cycle (moved verbatim from
# src/main.py:_edli_user_channel_reconcile_cycle). WRITES the durable fill
# bridge via the sanctioned ATTACH path. Undecorated here — the P3 daemon
# applies its own scheduler-health wrapper (the P2 pattern).
# ---------------------------------------------------------------------------

def _edli_user_channel_reconcile_cycle() -> None:
    """EDLI user-channel/reconcile service boundary.

    Disabled by default. The live-order aggregate may only accept fill/lifecycle
    facts from authenticated user channel or explicit reconcile writers; public
    market-channel data remains quote evidence only.
    """
    from src.observability.scheduler_health import _write_scheduler_health
    from src.state.db import get_world_connection_with_trades_required

    edli_cfg = _settings_section("edli_v1", {})
    if not _edli_user_channel_reconcile_runtime_enabled(edli_cfg):
        return
    max_messages = int(edli_cfg.get("edli_user_channel_reconcile_max_messages", 50))
    pending_limit = int(edli_cfg.get("edli_user_channel_reconcile_pending_limit", 50))
    now = datetime.now(timezone.utc)
    message_count = 0
    reconcile_count = 0
    # DEFECT-1: aggregates whose user-channel TRADE message was processed this
    # cycle. After the world-conn commit, the bridge materialises a canonical
    # position_current row for each that reached FILL_CONFIRMED.
    _edli_fill_bridge_aggregate_ids: set[str] = set()
    from src.events.live_order_aggregate import LiveOrderAggregateLedger
    from src.events.live_order_reconcile import append_reconciled
    from src.events.triggers.user_channel_ingestor import (
        INBOX_DUPLICATE,
        INBOX_FAILED,
        INBOX_PROCESSED,
        INBOX_STALE_REJECTED,
        append_user_channel_message,
        enqueue_user_channel_inbox_message,
        inbox_row_to_user_channel_message,
        mark_user_channel_inbox_status,
        pending_user_channel_inbox_messages,
    )

    conn = get_world_connection_with_trades_required(write_class="live")
    try:
        ledger = LiveOrderAggregateLedger(conn)
        user_channel_reader = _edli_user_channel_reader(edli_cfg)
        for message in user_channel_reader.poll(max_messages=max_messages):
            aggregate_id = _resolve_edli_user_channel_aggregate_id(conn, message)
            message_hash = str(message.get("message_hash") or "").strip()
            if not message_hash:
                raise RuntimeError("EDLI_USER_CHANNEL_MESSAGE_HASH_REQUIRED")
            occurred_at = _parse_edli_runtime_time(message, default=now)
            enqueue_user_channel_inbox_message(
                conn,
                message=message,
                aggregate_id=aggregate_id,
                occurred_at=occurred_at,
                received_at=now,
            )

        for inbox_row in pending_user_channel_inbox_messages(conn, limit=max_messages):
            message_hash = str(_row_get(inbox_row, "message_hash"))
            aggregate_id = str(_row_get(inbox_row, "aggregate_id"))
            try:
                message = inbox_row_to_user_channel_message(inbox_row)
                occurred_at = _parse_edli_runtime_time(
                    {"occurred_at": _row_get(inbox_row, "occurred_at")},
                    default=now,
                )
                _edli_user_channel_message_not_stale(conn, aggregate_id=aggregate_id, occurred_at=occurred_at)
                if _edli_user_channel_message_seen(conn, aggregate_id=aggregate_id, message_hash=message_hash):
                    mark_user_channel_inbox_status(
                        conn,
                        message_hash=message_hash,
                        status=INBOX_DUPLICATE,
                        processed_at=now,
                    )
                    continue
                append_user_channel_message(
                    ledger,
                    aggregate_id=aggregate_id,
                    message=message,
                    occurred_at=occurred_at,
                )
                mark_user_channel_inbox_status(
                    conn,
                    message_hash=message_hash,
                    status=INBOX_PROCESSED,
                    processed_at=now,
                )
                message_count += 1
                # DEFECT-1 bridge (capital recoverability): a confirmed EDLI
                # fill must materialise a canonical position_current row so
                # chain-reconciliation / exit-lifecycle / harvester / redeem can
                # see it. The actual cross-DB write happens AFTER this world-conn
                # commit, on a trade-connection-with-world-attached (INV-37) —
                # here we only record which aggregates received a trade message.
                _message_kind = str(message.get("message_type") or message.get("type") or "").lower()
                if _message_kind == "trade":
                    _edli_fill_bridge_aggregate_ids.add(aggregate_id)
            except RuntimeError as exc:
                status = INBOX_STALE_REJECTED if "STALE" in str(exc) else INBOX_FAILED
                mark_user_channel_inbox_status(
                    conn,
                    message_hash=message_hash,
                    status=status,
                    processed_at=now,
                    error=str(exc),
                )
            except Exception as exc:
                mark_user_channel_inbox_status(
                    conn,
                    message_hash=message_hash,
                    status=INBOX_FAILED,
                    processed_at=now,
                    error=str(exc),
                )

        venue_reconcile_reader = _edli_venue_reconcile_reader(edli_cfg)
        for pending in _edli_pending_reconcile_aggregates(conn, limit=pending_limit):
            fact = venue_reconcile_reader.reconcile(pending)
            if not fact:
                continue
            append_reconciled(
                ledger,
                aggregate_id=str(_row_get(pending, "aggregate_id")),
                event_id=str(fact.get("event_id") or _row_get(pending, "event_id")),
                final_intent_id=str(fact.get("final_intent_id") or _row_get(pending, "final_intent_id")),
                source=str(fact.get("source") or "venue_reconcile"),
                pending_reconcile=_parse_edli_runtime_bool(fact.get("pending_reconcile"), default=False),
                occurred_at=_parse_edli_runtime_time(fact, default=now),
                payload=fact.get("payload") if isinstance(fact.get("payload"), dict) else None,
            )
            reconcile_count += 1
        from src.events.edli_trade_fact_bridge import (
            append_confirmed_trade_facts_to_edli,
            append_rest_filled_orphan_trade_facts_to_edli,
        )

        reconcile_count += append_confirmed_trade_facts_to_edli(conn, now=now)
        reconcile_count += append_rest_filled_orphan_trade_facts_to_edli(conn, now=now)
        conn.commit()
    finally:
        conn.close()

    # MF-1 / DEFECT-1 bridge pass (capital recoverability). The EDLI events are
    # now durable on world.db. Materialise a canonical position_current row for
    # any aggregate that reached FILL_CONFIRMED so the legacy lifecycle
    # (chain-reconciliation / exit / harvester / redeem) can see and recover the
    # position.
    #
    # AUTHORITATIVE TRIGGER = the durable, idempotent scan
    # (_edli_durable_fill_bridge_scan): it re-derives the work set from the
    # persisted edli_live_order_events on EVERY cycle, so a confirmed fill orphaned
    # by a daemon death / swallowed exception between the inbox PROCESSED commit
    # and this bridge commit is healed on the next cycle regardless of process
    # restarts. The transient `_edli_fill_bridge_aggregate_ids` set is kept ONLY
    # as a fast in-cycle optimisation (bridges the just-processed fills with zero
    # extra scan cost); it is NO LONGER the source of truth, so the orphan window
    # is closed. Both run on the SAME bridge connection within the SAME commit.
    #
    # INV-37: runs on a trade connection with world ATTACHed — the bridge reads
    # world.edli_live_order_events and writes position_current / position_events on
    # the SAME connection (ATTACH + SAVEPOINT, no independent connection).
    # Idempotent: replay UPDATEs the same row, never duplicates; the durable scan
    # skips aggregates that already have a position_current row.
    # Fail-safe: a bridge error must not crash the scheduler job — log and retry
    # next cycle (the EDLI events persist; the next durable scan re-runs).
    bridged_positions = 0
    if True:  # always run the durable scan; the fast set is an optimisation only
        from src.events.edli_position_bridge import (
            materialize_position_current_from_edli_fill,
        )
        from src.state.db import get_trade_connection_with_world_required

        bridge_conn = None
        try:
            bridge_conn = get_trade_connection_with_world_required(write_class="live")
            # Fast in-cycle path: bridge the fills processed THIS cycle first
            # (zero extra scan). These will already exist by the time the durable
            # scan runs, so the scan's absence filter skips them — no double work.
            for _agg_id in sorted(_edli_fill_bridge_aggregate_ids):
                try:
                    result = materialize_position_current_from_edli_fill(
                        bridge_conn, _agg_id, now=now
                    )
                    if result is not None:
                        bridged_positions += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "EDLI position bridge failed for aggregate %s (non-fatal; "
                        "EDLI events persist, durable scan retries): %s",
                        _agg_id,
                        exc,
                        exc_info=True,
                    )
            # Authoritative durable scan: heal ANY orphaned confirmed fill,
            # including ones stranded by a prior restart / swallowed exception.
            bridged_positions += _edli_durable_fill_bridge_scan(bridge_conn, now=now)
            bridge_conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "EDLI position bridge pass failed (non-fatal): %s", exc, exc_info=True
            )
        finally:
            if bridge_conn is not None:
                try:
                    bridge_conn.close()
                except Exception:  # noqa: BLE001
                    pass

    _write_scheduler_health(
        "edli_user_channel_reconcile",
        failed=False,
        extra={
            "status": "processed_user_channel_reconcile_cycle",
            "fill_authority": "user_channel_or_reconcile_only",
            "public_market_channel_fill_truth": "forbidden",
            "user_channel_messages": message_count,
            "venue_reconciliations": reconcile_count,
            "edli_positions_bridged": bridged_positions,
        },
    )


# ---------------------------------------------------------------------------
# Market-channel helpers + PRODUCER 3: the market-channel ingestor cycle
# (moved verbatim from src/main.py). WRITES execution_feasibility_evidence (via
# the market-channel online service) the order runtime reads (I2). Undecorated.
# ---------------------------------------------------------------------------

def _edli_filter_markets_for_condition(markets: list[dict], condition_id: str | None) -> list[dict]:
    condition = str(condition_id or "").strip()
    if not condition:
        return list(markets)
    filtered = []
    for market in markets:
        if str(market.get("condition_id") or market.get("market_id") or "") == condition:
            filtered.append(market)
            continue
        outcomes = market.get("outcomes", []) or []
        if any(
            str(outcome.get("condition_id") or outcome.get("market_id") or "") == condition
            for outcome in outcomes
            if isinstance(outcome, dict)
        ):
            filtered.append(market)
    return filtered


def _edli_candidate_priority_token_ids(world_conn, *, lookback_hours: float = 48.0, limit: int = 4000) -> list[str]:
    """Tokens the EDLI reactor has recently decided on — the candidate universe.

    These are the YES/NO tokens of opportunity families the reactor actually
    evaluates. They MUST be pinned into the market-channel ingestor universe so a
    fresh ``execution_feasibility_evidence`` row exists for each by the time the
    reactor decides on it (Blocker #52). ``no_trade_regret_events`` records every
    reactor decision (incl. the witness-failure rejections we are fixing), so its
    recent token set is a precise, self-maintaining candidate signal — no
    cross-DB topology read in the hot path.

    PROVENANCE (P3 lift, system_decomposition_plan §7 I2): this READS world-DB
    ``no_trade_regret_events`` rows the reactor writes — a queryable TABLE, not an
    in-process queue handle. It is data-coupled to reactor STATE via DB rows
    (observable, acceptable), NEVER gated on the reactor's in-process backlog; a
    reactor backlog changes WHICH tokens are prioritised in the ingest universe,
    never WHETHER P3 runs. No back-coupling is introduced by the cross-process read.
    """

    if world_conn is None:
        return []
    try:
        has_table = world_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='no_trade_regret_events'"
        ).fetchone()
    except Exception:
        return []
    if not has_table:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(0.0, lookback_hours))).isoformat()
    requested_limit = max(1, int(limit or 1))
    scan_rows = min(
        max(
            MARKET_CHANNEL_CANDIDATE_PRIORITY_RECENT_ROW_SCAN_MIN,
            requested_limit * 16,
        ),
        MARKET_CHANNEL_CANDIDATE_PRIORITY_RECENT_ROW_SCAN_MAX,
    )
    try:
        rows = world_conn.execute(
            """
            SELECT token_id, created_at
              FROM no_trade_regret_events
             WHERE token_id IS NOT NULL AND token_id != '' AND token_id != 'None'
               AND created_at >= ?
             ORDER BY created_at DESC, rowid DESC
             LIMIT ?
            """,
            (cutoff, scan_rows),
        ).fetchall()
    except Exception:
        return []
    return list(dict.fromkeys(str(row[0]) for row in rows if row and row[0]))[:requested_limit]


def _edli_held_position_priority_token_ids(trade_conn) -> set[str]:
    """Tokens for open local/chain exposure that need immediate quote evidence.

    Excision T-consolidations #2 investigation (docs/rebuild/quarantine_excision_2026-07-11.md):
    the ``phase IN ('quarantined','voided') AND chain_state IN CURRENT_MONEY_RISK_CHAIN_STATES``
    exposure clause below answers "does this token need EDLI quote-priority
    because it might still carry live risk" — a broader question than
    redecision eligibility, with no direction gate and a 1e-6 chain_shares
    epsilon (vs 0.01 elsewhere). T5 (docs/rebuild/quarantine_excision_2026-07-11.md):
    the 'quarantined' half of the phase literal is now permanently dead — no
    writer mints it and the DB CHECK no longer admits it post-migration — but
    the clause is a raw-SQL OR against 'voided' too, so it is left as a
    harmless residual rather than restructured here; the cycle_runtime.py
    redecision-eligibility predicate this was once compared against has
    since been retired as fully unreachable. See
    tests/test_excision_t_consolidations_characterization.py::test_edli_priority_tokens_includes_voided_phase_and_broader_chain_states.
    """

    if trade_conn is None:
        return set()
    try:
        has_table = trade_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='position_current'"
        ).fetchone()
    except Exception:
        return set()
    if not has_table:
        return set()
    try:
        columns = {
            str(row[1])
            for row in trade_conn.execute("PRAGMA table_info(position_current)").fetchall()
        }
        if not {"phase", "token_id", "no_token_id"}.issubset(columns):
            return set()
        from src.contracts.position_truth import CURRENT_MONEY_RISK_CHAIN_STATES

        chain_state_values = tuple(sorted(CURRENT_MONEY_RISK_CHAIN_STATES))
        chain_placeholders = ",".join("?" for _ in chain_state_values)
        open_phase_clause = "phase IN ('pending_entry','active','day0_window','pending_exit')"
        exposure_clause = open_phase_clause
        params: tuple[object, ...] = ()
        if "chain_shares" in columns and "chain_state" in columns:
            exposure_clause = (
                f"({open_phase_clause} OR ("
                "phase IN ('quarantined','voided') "
                f"AND COALESCE(chain_state, '') IN ({chain_placeholders}) "
                "AND COALESCE(chain_shares, 0) > ?"
                "))"
            )
            params = (*chain_state_values, 0.000001)
        elif "chain_shares" in columns:
            exposure_clause = (
                f"({open_phase_clause} OR ("
                "phase IN ('quarantined','voided') "
                "AND COALESCE(chain_shares, 0) > ?"
                "))"
            )
            params = (0.000001,)
        rows = trade_conn.execute(
            f"""
            SELECT token_id, no_token_id
              FROM position_current
             WHERE {exposure_clause}
            """,
            params,
        ).fetchall()
    except Exception:
        return set()
    tokens: set[str] = set()
    for token_id, no_token_id in rows:
        for value in (token_id, no_token_id):
            token = str(value or "").strip()
            if token and token != "None":
                tokens.add(token)
    return tokens


def _edli_open_rest_priority_token_ids(trade_conn) -> set[str]:
    """Selected tokens for live entry commands that still need rest/reprice evidence."""

    if trade_conn is None:
        return set()
    try:
        has_table = trade_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='venue_commands'"
        ).fetchone()
    except Exception:
        return set()
    if not has_table:
        return set()
    try:
        columns = {
            str(row[1])
            for row in trade_conn.execute("PRAGMA table_info(venue_commands)").fetchall()
        }
    except Exception:
        return set()
    required = {"token_id", "intent_kind", "state"}
    if not required <= columns:
        return set()
    open_states = {
        "POSTING",
        "POST_ACKED",
        "SUBMITTING",
        "SUBMIT_UNKNOWN_SIDE_EFFECT",
        "ACKED",
        "PARTIAL",
    }
    placeholders = ",".join("?" for _ in open_states)
    try:
        rows = trade_conn.execute(
            f"""
            SELECT DISTINCT token_id
              FROM venue_commands
             WHERE intent_kind = 'ENTRY'
               AND state IN ({placeholders})
               AND token_id IS NOT NULL
               AND token_id != ''
            """,
            tuple(sorted(open_states)),
        ).fetchall()
    except Exception:
        return set()
    tokens = {str(row[0] or "").strip() for row in rows}
    tokens.discard("")
    tokens.discard("None")
    return tokens


def _edli_priority_family_token_ids(
    trade_conn,
    forecasts_conn,
    token_ids,
    *,
    limit: int = 2000,
) -> set[str]:
    """Expand high-value token seeds to their complete weather families."""

    seeds = {
        str(token or "").strip()
        for token in token_ids
        if str(token or "").strip() and str(token or "").strip() != "None"
    }
    if not seeds or trade_conn is None or forecasts_conn is None:
        return seeds
    try:
        seed_conditions: set[str] = set()
        ordered_seeds = sorted(seeds)
        for offset in range(0, len(ordered_seeds), 400):
            chunk = ordered_seeds[offset : offset + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows = trade_conn.execute(
                f"""
                SELECT DISTINCT condition_id
                  FROM executable_market_snapshot_latest
                 WHERE selected_outcome_token_id IN ({placeholders})
                """,
                chunk,
            ).fetchall()
            seed_conditions.update(
                str(row[0] or "").strip() for row in rows if row
            )
        seed_conditions.discard("")
        seed_conditions.discard("None")
        if not seed_conditions:
            return seeds

        families: set[tuple[str, str, str]] = set()
        ordered_conditions = sorted(seed_conditions)
        for offset in range(0, len(ordered_conditions), 400):
            chunk = ordered_conditions[offset : offset + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows = forecasts_conn.execute(
                f"""
                SELECT DISTINCT city, target_date, temperature_metric
                  FROM market_events
                 WHERE condition_id IN ({placeholders})
                   AND city IS NOT NULL AND TRIM(city) != ''
                   AND target_date IS NOT NULL AND TRIM(target_date) != ''
                   AND temperature_metric IN ('high', 'low')
                """,
                chunk,
            ).fetchall()
            families.update(
                (
                    str(row[0]).strip(),
                    str(row[1]).strip(),
                    str(row[2]).strip(),
                )
                for row in rows
            )
        if not families:
            return seeds

        family_conditions: set[str] = set()
        ordered_families = sorted(families)
        for offset in range(0, len(ordered_families), 200):
            chunk = ordered_families[offset : offset + 200]
            requested = ",".join("(?,?,?)" for _ in chunk)
            params = tuple(value for family in chunk for value in family)
            rows = forecasts_conn.execute(
                f"""
                WITH requested(city, target_date, metric) AS (VALUES {requested})
                SELECT DISTINCT market.condition_id
                  FROM requested
                  JOIN market_events AS market
                    ON market.city = requested.city
                   AND market.target_date = requested.target_date
                   AND market.temperature_metric = requested.metric
                 WHERE market.condition_id IS NOT NULL
                   AND TRIM(market.condition_id) != ''
                """,
                params,
            ).fetchall()
            family_conditions.update(
                str(row[0] or "").strip() for row in rows if row
            )
        family_conditions.discard("")
        family_conditions.discard("None")

        expanded = set(seeds)
        ordered_family_conditions = sorted(family_conditions)
        for offset in range(0, len(ordered_family_conditions), 400):
            chunk = ordered_family_conditions[offset : offset + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows = trade_conn.execute(
                f"""
                SELECT selected_outcome_token_id, yes_token_id, no_token_id
                  FROM executable_market_snapshot_latest
                 WHERE condition_id IN ({placeholders})
                """,
                chunk,
            ).fetchall()
            for row in rows:
                for raw_token in row:
                    token = str(raw_token or "").strip()
                    if token and token != "None":
                        expanded.add(token)
    except Exception:
        return seeds

    remaining = max(0, max(int(limit), len(seeds)) - len(seeds))
    return seeds | set(sorted(expanded - seeds)[:remaining])


def _edli_order_token_ids_by_feasibility_age(
    trade_conn,
    token_ids,
) -> list[str]:
    """Oldest/missing quote evidence first for bounded held-position refreshes."""

    if isinstance(token_ids, (set, frozenset)):
        raw_tokens = sorted(str(token_id) for token_id in token_ids if str(token_id or "").strip())
    else:
        raw_tokens = [str(token_id) for token_id in token_ids if str(token_id or "").strip()]
    tokens = list(dict.fromkeys(raw_tokens))
    if not tokens:
        return []
    priority_index = {token: idx for idx, token in enumerate(tokens)}
    try:
        has_table = trade_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_feasibility_evidence'"
        ).fetchone()
    except Exception:
        return tokens
    if not has_table:
        return tokens
    latest_by_token: dict[str, str | None] = {token: None for token in tokens}

    def _created_by_token_from(table: str, subset: list[str]) -> dict[str, str]:
        if not subset:
            return {}
        placeholders = ",".join("?" for _ in subset)
        rows = trade_conn.execute(
            f"""
            SELECT token_id, MAX(created_at) AS created_at
              FROM {table}
             WHERE token_id IN ({placeholders})
             GROUP BY token_id
            """,
            tuple(subset),
        ).fetchall()
        return {
            str(row[0]): str(row[1])
            for row in rows
            if row and row[0] is not None and row[1] is not None
        }

    try:
        if _edli_table_exists(trade_conn, "execution_feasibility_latest"):
            latest_by_token.update(
                _created_by_token_from("execution_feasibility_latest", tokens)
            )
        missing_from_latest = [
            token for token in tokens if latest_by_token.get(token) is None
        ]
        if missing_from_latest:
            latest_by_token.update(
                _created_by_token_from(
                    "execution_feasibility_evidence",
                    missing_from_latest,
                )
            )
    except Exception:
        return tokens
    return sorted(
        tokens,
        key=lambda token: (
            latest_by_token.get(token) is not None,
            latest_by_token.get(token) or "",
            priority_index[token],
            token,
        ),
    )


def _edli_market_channel_seed_first_token_ids(
    *,
    held_priority_token_ids: set[str],
    open_rest_priority_token_ids: set[str] | None = None,
    candidate_priority_token_ids,
) -> set[str]:
    """REST-seed tokens that must be fresh before the broad market universe.

    Open exposure owns the strictest freshness SLA: monitor/redecision/exit can
    act only when held-position quote evidence is current. Resting entry orders
    have the same SLA because cancel/reprice/hold decisions are live money-path
    actions, not background discovery. Candidate tokens also stay seed-first so
    the entry witness does not wait behind the broad market universe.
    """

    held = {str(token or "").strip() for token in held_priority_token_ids}
    held.discard("")
    held.discard("None")
    open_rest = {str(token or "").strip() for token in (open_rest_priority_token_ids or set())}
    open_rest.discard("")
    open_rest.discard("None")
    candidates = {str(token or "").strip() for token in candidate_priority_token_ids}
    candidates.discard("")
    candidates.discard("None")
    return held | open_rest | candidates


def _edli_schema_prefix(schema: str = "") -> str:
    clean = str(schema or "").strip()
    return f"{clean}." if clean else ""


def _edli_table_exists(conn, table: str, *, schema: str = "") -> bool:
    clean_table = str(table or "").strip()
    if not clean_table:
        return False
    master = f"{_edli_schema_prefix(schema)}sqlite_master"
    try:
        return (
            conn.execute(
                f"SELECT 1 FROM {master} WHERE type='table' AND name=?",
                (clean_table,),
            ).fetchone()
            is not None
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# RE-DECISION ROUTING MOVED (R6 split, 2026-07-08): deciding WHICH money-path
# families a book move should trigger a re-solve for is a decision-layer concern,
# not a venue-fact-bridge one (blueprint defect #4: "venue does not decide who
# re-solves"). The routing cluster (_edli_quote_event_token_ids through
# _edli_price_channel_redecision_sink) now lives in
# src.events.price_channel_redecision_router. This module wires the sink in as an
# injected market_event_sink dependency below — it owns no routing decision itself.
# Re-exported here (see imports above) so existing external references keep working.
# ---------------------------------------------------------------------------


def _edli_market_channel_refresh_kwargs(action, markets, clob, captured_at) -> dict:
    """Build refresh_executable_market_substrate_snapshots kwargs for a market-channel action.

    Authority is always VERIFIED (snapshots come from verified Gamma/CLOB data);
    the EDLI channel trigger reason is carried as non-authoritative refresh_reason
    metadata so it appears in the summary log without polluting the capture contract.

    Separating these two carriers fixes P1-1: the original code passed
    ``scan_authority=f"EDLI_MARKET_CHANNEL:{action.reason}"`` which caused
    capture_executable_market_snapshot to raise ExecutableSnapshotCaptureError on
    every attempt (it requires scan_authority == "VERIFIED"), making the entire
    reactive snapshot-refresh path silently dead.
    """
    return dict(
        markets=markets,
        clob=clob,
        captured_at=captured_at,
        scan_authority="VERIFIED",
        refresh_reason=f"EDLI_MARKET_CHANNEL:{action.reason}",
        max_outcomes=20,
        budget_seconds=15.0,
    )


def _edli_refresh_held_position_quote_evidence(
    *,
    budget_seconds: float | None = None,
) -> dict:
    """Refresh executable quote evidence for currently held exposure.

    The long-lived market-channel WebSocket can be healthy while quiet markets
    emit no book deltas. Held-position monitor/redecision needs a wall-clock
    freshness guarantee, so the scheduler performs a bounded REST refresh for
    open exposure every cycle even when the WS thread is alive.
    """

    from src.data.polymarket_client import PolymarketClient
    from src.events.event_coalescer import EventCoalescer
    from src.events.event_writer import EventWriter
    from src.events.triggers.market_channel_ingestor import (
        MarketChannelIngestor,
        MarketChannelOnlineService,
        active_weather_token_metadata_for_tokens,
    )
    from src.state.db import get_trade_connection

    edli_cfg = _settings_section("edli_v1", {})
    budget = max(
        0.001,
        float(
            budget_seconds
            if budget_seconds is not None
            else _edli_bounded_positive_float(
                edli_cfg,
                "market_channel_held_quote_refresh_budget_seconds",
                default=MARKET_CHANNEL_HELD_QUOTE_REFRESH_BUDGET_SECONDS_DEFAULT,
                maximum=55.0,
            )
        ),
    )
    started_monotonic = time.monotonic()
    deadline = started_monotonic + budget

    trade_read = get_trade_connection(write_class=None)
    try:
        held_token_ids = _edli_held_position_priority_token_ids(trade_read)
        if not held_token_ids:
            return {"held_priority_token_ids": 0, "held_quote_refresh_events": 0}
        ordered_held_token_ids = _edli_order_token_ids_by_feasibility_age(
            trade_read,
            held_token_ids,
        )
        max_tokens = _edli_quote_refresh_max_tokens(
            edli_cfg,
            "market_channel_held_quote_refresh_max_tokens_per_cycle",
            default=MARKET_CHANNEL_HELD_QUOTE_REFRESH_MAX_TOKENS_PER_CYCLE_DEFAULT,
        )
        selected_held_token_ids: list[str] = []
        scanned_held_token_ids: list[str] = []
        metadata_missing_token_ids: list[str] = []
        token_metadata = {}
        batch_size = max(1, max_tokens)
        for offset in range(0, len(ordered_held_token_ids), batch_size):
            batch = ordered_held_token_ids[offset : offset + batch_size]
            if not batch:
                continue
            scanned_held_token_ids.extend(batch)
            batch_metadata = active_weather_token_metadata_for_tokens(
                trade_read,
                token_ids=batch,
                purpose="exit",
            )
            token_metadata.update(batch_metadata)
            for token_id in batch:
                if token_id in batch_metadata:
                    selected_held_token_ids.append(token_id)
                    if len(selected_held_token_ids) >= max_tokens:
                        break
                else:
                    metadata_missing_token_ids.append(token_id)
            if len(selected_held_token_ids) >= max_tokens:
                break
    finally:
        trade_read.close()

    if selected_held_token_ids:
        token_metadata = {
            token_id: token_metadata[token_id]
            for token_id in selected_held_token_ids
            if token_id in token_metadata
        }

    if not token_metadata:
        return {
            "held_priority_token_ids": len(held_token_ids),
            "held_quote_refresh_selected_tokens": len(selected_held_token_ids),
            "held_quote_refresh_metadata_scanned_tokens": len(scanned_held_token_ids),
            "held_quote_refresh_metadata_missing_tokens": len(metadata_missing_token_ids),
            "held_quote_refresh_deferred_tokens": max(
                0,
                len(ordered_held_token_ids) - len(scanned_held_token_ids),
            ),
            "held_quote_refresh_events": 0,
            "skipped": "no_held_token_metadata",
        }

    # Quote/book refresh writes trade-owned execution_feasibility_evidence and may
    # synchronously emit derived EDLI_REDECISION_PENDING world events. Raw
    # BOOK_SNAPSHOT/BEST_BID_ASK_CHANGED cache facts are intentionally not persisted
    # to opportunity_events; keeping them there was write amplification, not
    # decision truth. The attached connection still keeps the trade witness and any
    # derived world event on one commit boundary.
    from src.state.db import get_world_connection_with_trades_required

    ordered_metadata_tokens = [
        token_id for token_id in selected_held_token_ids if token_id in token_metadata
    ]

    rest_seed_acquired = _held_quote_seed_refresh_lock.acquire(blocking=False)
    if not rest_seed_acquired:
        return _rest_quote_refresh_backpressure_result(
            kind="held",
            started_monotonic=started_monotonic,
            budget=budget,
            token_ids=len(held_token_ids),
            token_metadata=len(token_metadata),
            attempted_tokens=len(ordered_metadata_tokens),
            extra={
                "held_quote_refresh_selected_tokens": len(selected_held_token_ids),
                "held_quote_refresh_deferred_tokens": max(
                    0,
                    len(ordered_held_token_ids) - len(selected_held_token_ids),
                ),
            },
        )

    # Do not use the flocked context here: REST book fetches happen inside
    # seed_rest_books_in_chunks before each DB chunk write, and holding
    # cross-process trade/world writer flocks across those network calls starves
    # live redecision's executable snapshot refresh.
    conn = None
    try:
        conn = get_world_connection_with_trades_required(write_class="live")
        _bound_price_channel_sqlite_wait(conn)

        def _commit_atomic_cross_db() -> None:
            conn.commit()

        # The redecision-routing decision (WHICH families to re-solve) is a decision-layer
        # concern this boundary module only WIRES IN, never inlines (R6 split).
        from src.events.price_channel_redecision_router import _edli_price_channel_redecision_sink

        with PolymarketClient() as clob:
            fetch_orderbook, fetch_orderbooks = _budgeted_orderbook_fetchers(
                clob,
                deadline_monotonic=deadline,
            )
            service = MarketChannelOnlineService(
                MarketChannelIngestor(
                    EventWriter(conn),
                    active_token_ids=set(token_metadata),
                    token_metadata=token_metadata,
                    feasibility_conn=conn,
                    feasibility_schema="trades",
                    coalescer=EventCoalescer(max_market_keys=1000),
                    market_event_sink=_edli_price_channel_redecision_sink(conn),
                    market_event_sink_independently_coordinated=True,
                ),
                fetch_orderbook=fetch_orderbook,
                fetch_orderbooks=fetch_orderbooks,
            )
            written = service.seed_rest_books_in_chunks(
                token_ids=ordered_metadata_tokens,
                received_at=datetime.now(timezone.utc).isoformat(),
                world_mutex=_edli_price_channel_world_trade_write_gate(
                    owner="price_channel_held_quote_refresh"
                ),
                commit=_commit_atomic_cross_db,
                logger=logger,
                chunk_size=MARKET_CHANNEL_PRIORITY_QUOTE_REFRESH_CHUNK_SIZE_DEFAULT,
                deadline_monotonic=deadline,
            )
        elapsed_seconds = max(0.0, time.monotonic() - started_monotonic)
        result = {
            "held_priority_token_ids": len(held_token_ids),
            "held_token_metadata": len(token_metadata),
            "held_quote_refresh_events": int(written),
            "held_quote_refresh_selected_tokens": len(selected_held_token_ids),
            "held_quote_refresh_metadata_scanned_tokens": len(scanned_held_token_ids),
            "held_quote_refresh_metadata_missing_tokens": len(metadata_missing_token_ids),
            "held_quote_refresh_deferred_tokens": max(
                0,
                len(ordered_held_token_ids) - len(scanned_held_token_ids),
            ),
            "held_quote_refresh_attempted_tokens": len(ordered_metadata_tokens),
            "budget_seconds": budget,
            "elapsed_seconds": elapsed_seconds,
            "budget_exhausted": elapsed_seconds >= budget,
            "budget_skipped_tokens": max(0, len(ordered_metadata_tokens) - int(written)),
        }
        if service.rest_seed_backpressure_count:
            result["backpressure"] = True
            result["write_backpressure_count"] = service.rest_seed_backpressure_count
            result["write_backpressure_reason"] = service.rest_seed_backpressure_reason
        return result
    finally:
        try:
            if conn is not None:
                conn.close()
        finally:
            _held_quote_seed_refresh_lock.release()


def _edli_refresh_candidate_priority_quote_evidence(
    *,
    limit: int = 32,
    budget_seconds: float = MARKET_CHANNEL_CANDIDATE_QUOTE_REFRESH_BUDGET_SECONDS_DEFAULT,
) -> dict:
    """Refresh executable quote evidence for recently selected candidate tokens.

    The long-lived market-channel thread captures its token universe at thread
    start. Candidate tokens can appear minutes later through reactor no-trade
    receipts, so they need the same bounded REST freshness path as held exposure
    rather than waiting for the WS universe to restart.
    """

    from src.data.polymarket_client import PolymarketClient
    from src.events.event_coalescer import EventCoalescer
    from src.events.event_writer import EventWriter
    from src.events.triggers.market_channel_ingestor import (
        MarketChannelIngestor,
        MarketChannelOnlineService,
        active_weather_token_metadata_for_tokens,
    )
    from src.state.db import get_trade_connection, get_world_connection

    world_read = get_world_connection(write_class=None)
    try:
        candidate_token_ids = _edli_candidate_priority_token_ids(
            world_read,
            limit=limit,
        )
    finally:
        world_read.close()
    started_monotonic = time.monotonic()
    requested_budget = max(0.001, float(budget_seconds))
    trade_read = get_trade_connection(write_class=None)
    try:
        held_token_ids = _edli_held_position_priority_token_ids(trade_read)
        open_rest_token_ids = _edli_open_rest_priority_token_ids(trade_read)
        priority_token_ids = list(
            dict.fromkeys(
                list(sorted(open_rest_token_ids))
                + [str(token) for token in candidate_token_ids if str(token or "").strip()]
            )
        )
        if not priority_token_ids:
            return {
                "candidate_priority_token_ids": 0,
                "open_rest_priority_token_ids": 0,
                "candidate_quote_refresh_events": 0,
            }
        ordered_candidate_token_ids = _edli_order_token_ids_by_feasibility_age(
            trade_read,
            priority_token_ids,
        )
        max_tokens = _edli_quote_refresh_max_tokens(
            _settings_section("edli_v1", {}),
            "market_channel_candidate_quote_refresh_max_tokens_per_cycle",
            default=MARKET_CHANNEL_CANDIDATE_QUOTE_REFRESH_MAX_TOKENS_PER_CYCLE_DEFAULT,
        )
        selected_candidate_token_ids = ordered_candidate_token_ids[:max_tokens]
        token_metadata = active_weather_token_metadata_for_tokens(
            trade_read,
            token_ids=selected_candidate_token_ids,
        )
    finally:
        trade_read.close()
    held_priority_count = len(held_token_ids)
    # Held exposure has its own independent edli_held_quote_refresh job. Do not
    # steal candidate/redecision refresh budget just because a position exists;
    # that starves entry and repricing quote evidence whenever the book is wide.
    budget = requested_budget
    deadline = started_monotonic + budget

    if not token_metadata:
        return {
            "candidate_priority_token_ids": len(candidate_token_ids),
            "open_rest_priority_token_ids": len(open_rest_token_ids),
            "held_priority_token_ids": held_priority_count,
            "quote_priority_token_ids": len(priority_token_ids),
            "candidate_quote_refresh_selected_tokens": len(selected_candidate_token_ids),
            "candidate_quote_refresh_deferred_tokens": max(
                0,
                len(ordered_candidate_token_ids) - len(selected_candidate_token_ids),
            ),
            "candidate_quote_refresh_events": 0,
            "skipped": "no_candidate_token_metadata",
        }

    # Same attached-connection shape as held refresh: quote evidence lands in
    # trades.execution_feasibility_evidence, while only derived redecision events
    # touch world.opportunity_events.
    from src.state.db import get_world_connection_with_trades_required

    # Same lock discipline as held-position refresh: one world-main connection
    # with trades attached, but no cross-process writer flock held across REST
    # fetches. Each seed chunk still commits on this single attached connection.
    ordered_metadata_tokens = [
        token_id for token_id in selected_candidate_token_ids if token_id in token_metadata
    ]
    rest_seed_acquired = _candidate_quote_seed_refresh_lock.acquire(blocking=False)
    if not rest_seed_acquired:
        return _rest_quote_refresh_backpressure_result(
            kind="candidate",
            started_monotonic=started_monotonic,
            budget=budget,
            token_ids=len(candidate_token_ids),
            token_metadata=len(token_metadata),
            attempted_tokens=len(ordered_metadata_tokens),
            extra={
                "open_rest_priority_token_ids": len(open_rest_token_ids),
                "quote_priority_token_ids": len(priority_token_ids),
                "held_priority_token_ids": held_priority_count,
                "candidate_quote_refresh_selected_tokens": len(selected_candidate_token_ids),
                "candidate_quote_refresh_deferred_tokens": max(
                    0,
                    len(ordered_candidate_token_ids) - len(selected_candidate_token_ids),
                ),
                "budget_seconds": budget,
            },
        )

    conn = None
    try:
        conn = get_world_connection_with_trades_required(write_class="live")
        _bound_price_channel_sqlite_wait(conn)

        def _commit_atomic_cross_db() -> None:
            conn.commit()

        # The redecision-routing decision (WHICH families to re-solve) is a decision-layer
        # concern this boundary module only WIRES IN, never inlines (R6 split).
        from src.events.price_channel_redecision_router import _edli_price_channel_redecision_sink

        with PolymarketClient() as clob:
            fetch_orderbook, fetch_orderbooks = _budgeted_orderbook_fetchers(
                clob,
                deadline_monotonic=deadline,
            )
            service = MarketChannelOnlineService(
                MarketChannelIngestor(
                    EventWriter(conn),
                    active_token_ids=set(token_metadata),
                    token_metadata=token_metadata,
                    feasibility_conn=conn,
                    feasibility_schema="trades",
                    coalescer=EventCoalescer(max_market_keys=1000),
                    market_event_sink=_edli_price_channel_redecision_sink(conn),
                    market_event_sink_independently_coordinated=True,
                ),
                fetch_orderbook=fetch_orderbook,
                fetch_orderbooks=fetch_orderbooks,
            )
            written = service.seed_rest_books_in_chunks(
                token_ids=ordered_metadata_tokens,
                received_at=datetime.now(timezone.utc).isoformat(),
                world_mutex=_edli_price_channel_world_trade_write_gate(
                    owner="price_channel_candidate_quote_refresh"
                ),
                commit=_commit_atomic_cross_db,
                logger=logger,
                chunk_size=MARKET_CHANNEL_PRIORITY_QUOTE_REFRESH_CHUNK_SIZE_DEFAULT,
                deadline_monotonic=deadline,
            )
        elapsed_seconds = max(0.0, time.monotonic() - started_monotonic)
        result = {
            "candidate_priority_token_ids": len(candidate_token_ids),
            "open_rest_priority_token_ids": len(open_rest_token_ids),
            "held_priority_token_ids": held_priority_count,
            "quote_priority_token_ids": len(priority_token_ids),
            "candidate_token_metadata": len(token_metadata),
            "candidate_quote_refresh_events": int(written),
            "candidate_quote_refresh_selected_tokens": len(selected_candidate_token_ids),
            "candidate_quote_refresh_deferred_tokens": max(
                0,
                len(ordered_candidate_token_ids) - len(selected_candidate_token_ids),
            ),
            "candidate_quote_refresh_attempted_tokens": len(ordered_metadata_tokens),
            "budget_seconds": budget,
            "requested_budget_seconds": requested_budget,
            "elapsed_seconds": elapsed_seconds,
            "budget_exhausted": elapsed_seconds >= budget,
            "budget_skipped_tokens": max(0, len(ordered_metadata_tokens) - int(written)),
        }
        if service.rest_seed_backpressure_count:
            result["backpressure"] = True
            result["write_backpressure_count"] = service.rest_seed_backpressure_count
            result["write_backpressure_reason"] = service.rest_seed_backpressure_reason
        return result
    finally:
        try:
            if conn is not None:
                conn.close()
        finally:
            _candidate_quote_seed_refresh_lock.release()


def _edli_held_quote_refresh_cycle() -> dict:
    """Scheduler entry point for held-position quote freshness.

    This is deliberately separate from ``_edli_market_channel_ingestor_cycle``:
    the market-channel/user-channel lanes can spend minutes in broad reconcile
    or substrate scans, but held exposure needs bounded quote evidence refresh
    before monitor/redecision can safely resume.
    """

    from src.observability.scheduler_health import _write_scheduler_health

    try:
        result = _edli_refresh_held_position_quote_evidence()
    except Exception as exc:  # noqa: BLE001
        _write_scheduler_health(
            "edli_held_quote_refresh",
            failed=True,
            reason=f"{type(exc).__name__}: {exc}",
        )
        raise
    failed, reason = _price_channel_quote_refresh_failed(
        result,
        token_key="held_token_metadata",
        event_key="held_quote_refresh_events",
    )
    if failed:
        result["scheduler_failed"] = True
        result["scheduler_failure_reason"] = reason or "held_quote_refresh_no_coverage"
    _write_scheduler_health(
        "edli_held_quote_refresh",
        failed=failed,
        reason=reason,
        extra=result,
    )
    return result


def _edli_market_channel_ingestor_cycle() -> dict | None:
    """EDLI market-channel online data-service bootstrap.

    This daemon-side job discovers active weather tokens and prepares the public
    market-channel ingestor/quote cache. Actual fills remain user-channel or
    reconcile authority only.
    """
    from src.observability.scheduler_health import _write_scheduler_health

    edli_cfg = _settings_section("edli_v1", {})
    if not edli_cfg.get("enabled") or not edli_cfg.get("market_channel_ingestor_enabled"):
        return
    global _edli_market_channel_thread
    if _edli_market_channel_thread is not None and _edli_market_channel_thread.is_alive():
        candidate_refresh = _edli_refresh_candidate_priority_quote_evidence(
            limit=_edli_bounded_positive_int(
                edli_cfg,
                "market_channel_candidate_priority_max_tokens",
                default=32,
                maximum=1000,
            ),
            budget_seconds=_edli_bounded_positive_float(
                edli_cfg,
                "market_channel_candidate_quote_refresh_budget_seconds",
                default=MARKET_CHANNEL_CANDIDATE_QUOTE_REFRESH_BUDGET_SECONDS_DEFAULT,
                maximum=120.0,
            ),
        )
        candidate_failed, candidate_reason = _price_channel_quote_refresh_failed(
            candidate_refresh,
            token_key="candidate_token_metadata",
            event_key="candidate_quote_refresh_events",
        )
        if candidate_failed:
            candidate_refresh["scheduler_failed"] = True
            candidate_refresh["scheduler_failure_reason"] = (
                candidate_reason or "candidate_quote_refresh_no_coverage"
            )
        health = {
            "thread": "alive",
            "quote_cache_enabled": bool(edli_cfg.get("market_channel_quote_cache_enabled", False)),
            "fill_authority": "user_channel_or_reconcile_only",
            "held_quote_refresh": "delegated_to_edli_held_quote_refresh",
            "candidate_quote_refresh": candidate_refresh,
        }
        if candidate_failed:
            health["scheduler_failed"] = True
            health["scheduler_failure_reason"] = candidate_reason or "candidate_quote_refresh_no_coverage"
        _write_scheduler_health(
            "edli_market_channel_ingestor",
            failed=candidate_failed,
            reason=candidate_reason,
            extra=health,
        )
        return health

    from src.events.triggers.market_channel_ingestor import active_weather_token_metadata_for_tokens
    from src.state.db import (
        get_forecasts_connection_read_only,
        get_trade_connection,
        get_world_connection,
    )

    # Candidate universe (Blocker #52): tokens the reactor recently decided on must
    # be PINNED into the ingestor universe so each has a fresh execution_feasibility_
    # evidence row before the pre-submit witness reads it. The full latest-per-market
    # universe is captured up to the cap; candidates are never dropped by the cap.
    candidate_priority_token_ids: list[str] = []
    world_read = get_world_connection(write_class=None)
    try:
        candidate_priority_limit = _edli_bounded_positive_int(
            edli_cfg,
            "market_channel_candidate_priority_max_tokens",
            default=32,
            maximum=1000,
        )
        candidate_priority_token_ids = _edli_candidate_priority_token_ids(
            world_read,
            limit=candidate_priority_limit,
        )
    except Exception as exc:  # noqa: BLE001 - priority pinning is best-effort, universe still captured
        logger.warning("EDLI ingestor candidate-priority read failed (non-fatal): %s", exc)
    finally:
        if world_read is not None:
            world_read.close()

    forecasts_read = None
    try:
        forecasts_read = get_forecasts_connection_read_only()
    except Exception as exc:
        logger.warning(
            "EDLI ingestor family-priority forecast read failed (non-fatal): %s",
            exc,
        )
    trade_conn = get_trade_connection(write_class=None)
    try:
        held_priority_token_ids = _edli_held_position_priority_token_ids(trade_conn)
        open_rest_priority_token_ids = _edli_open_rest_priority_token_ids(trade_conn)
        priority_token_ids = set(candidate_priority_token_ids)
        priority_token_ids.update(held_priority_token_ids)
        priority_token_ids.update(open_rest_priority_token_ids)
        seed_first_token_ids = _edli_market_channel_seed_first_token_ids(
            held_priority_token_ids=held_priority_token_ids,
            open_rest_priority_token_ids=open_rest_priority_token_ids,
            candidate_priority_token_ids=candidate_priority_token_ids,
        )
        priority_token_ids = _edli_priority_family_token_ids(
            trade_conn,
            forecasts_read,
            priority_token_ids,
        )
        entry_token_ids = set(priority_token_ids)
        token_metadata = active_weather_token_metadata_for_tokens(
            trade_conn,
            token_ids=entry_token_ids,
        )
        token_metadata.update(
            active_weather_token_metadata_for_tokens(
                trade_conn,
                token_ids=held_priority_token_ids,
                purpose="exit",
            )
        )
        token_ids = set(token_metadata)
    finally:
        trade_conn.close()
        if forecasts_read is not None:
            forecasts_read.close()

    if not token_ids:
        health = {
            "active_weather_token_ids": 0,
            "priority_token_ids": len(priority_token_ids),
            "held_priority_token_ids": len(held_priority_token_ids),
            "open_rest_priority_token_ids": len(open_rest_priority_token_ids),
            "seed_first_token_ids": len(seed_first_token_ids),
            "quote_cache_enabled": bool(edli_cfg.get("market_channel_quote_cache_enabled", False)),
            "fill_authority": "user_channel_or_reconcile_only",
            "skipped": "no_priority_token_metadata",
        }
        _write_scheduler_health(
            "edli_market_channel_ingestor",
            failed=False,
            extra=health,
        )
        return health

    def _runner() -> None:
        from src.data.polymarket_client import PolymarketClient
        from src.events.event_coalescer import EventCoalescer
        from src.events.event_writer import EventWriter
        from src.events.triggers.market_channel_ingestor import (
            MarketChannelAction,
            MarketChannelIngestor,
            MarketChannelOnlineService,
            invalidate_executable_snapshots_for_market_channel_action,
            run_market_channel_service_forever,
        )
        from src.state.db import get_world_connection_with_trades_required

        # The long-lived market-channel ingestor commits quote evidence through one
        # attached WORLD+TRADE connection. Derived redecision screening runs only after
        # that commit on read-only connections, then emits through a short WORLD-only
        # writer lease. It does not persist raw BOOK_SNAPSHOT/BEST_BID_ASK_CHANGED rows.
        # The NON-flocked helper is used here because this connection lives for the
        # whole forever-loop; holding cross-DB writer flocks for that lifetime would
        # starve every other writer. Feasibility writes are schema-qualified 'trades'
        # so they reach the runtime-read trades table, never the world ghost copy.
        conn = get_world_connection_with_trades_required(write_class="live")
        _bound_price_channel_sqlite_wait(conn)
        world_conn = conn  # EventWriter target = world MAIN (unqualified opportunity_events)
        feasibility_conn = conn

        def _commit_event_and_feasibility() -> None:
            conn.commit()

        def _rollback_event_and_feasibility() -> None:
            conn.rollback()

        try:
            def _invalidate_snapshot_action(action: "MarketChannelAction") -> None:
                from src.state.db import get_trade_connection

                with _edli_price_channel_trade_write_context_factory(
                    owner="price_channel_snapshot_invalidate"
                )() as write_lease:
                    trade_conn = get_trade_connection(write_class="live")
                    before_changes = int(trade_conn.total_changes)
                    try:
                        invalidated = invalidate_executable_snapshots_for_market_channel_action(
                            trade_conn,
                            action,
                            invalidated_at=datetime.now(timezone.utc),
                        )
                        if invalidated:
                            commit_started = time.monotonic()
                            trade_conn.commit()
                            write_lease.record_commit(
                                commit_ms=(time.monotonic() - commit_started) * 1000.0,
                                rows_changed=max(
                                    0,
                                    int(trade_conn.total_changes) - before_changes,
                                ),
                            )
                    finally:
                        trade_conn.close()

            def _refresh_snapshot_action(action: "MarketChannelAction") -> None:
                from src.data.market_scanner import (
                    MarketEventsPersistenceError,
                    find_weather_markets_or_raise,
                    refresh_executable_market_substrate_snapshots,
                )
                from src.data.dual_run_lock import acquire_lock
                from src.state.db import get_trade_connection

                substrate_acquired = _market_substrate_refresh_lock.acquire(blocking=False)
                if not substrate_acquired:
                    logger.info(
                        "EDLI market-channel refresh skipped: executable substrate refresh already running"
                    )
                    return
                process_lock_ctx = acquire_lock("market_substrate_refresh")
                process_entered = False
                process_acquired = False
                trade_conn = None
                try:
                    process_acquired = process_lock_ctx.__enter__()
                    process_entered = True
                    if not process_acquired:
                        logger.info(
                            "EDLI market-channel refresh skipped: cross-process executable substrate refresh already running"
                        )
                        return
                    try:
                        markets = find_weather_markets_or_raise(
                            min_hours_to_resolution=0.0,
                            include_slug_pattern=True,
                        )
                    except MarketEventsPersistenceError as _persistence_exc:
                        logger.error(
                            "EDLI market-channel refresh aborted: market_events persistence "
                            "failure — snapshot substrate not refreshed: %s",
                            _persistence_exc,
                        )
                        return
                    if action.condition_id:
                        markets = _edli_filter_markets_for_condition(markets, action.condition_id)
                        if not markets:
                            logger.warning(
                                "EDLI market-channel refresh skipped: condition_id=%s not found in active weather markets",
                                action.condition_id,
                            )
                            return
                    trade_conn = get_trade_connection(write_class="live")
                    summary = refresh_executable_market_substrate_snapshots(
                        trade_conn,
                        **_edli_market_channel_refresh_kwargs(
                            action, markets, clob, datetime.now(timezone.utc)
                        ),
                        snapshot_write_context_factory=_edli_price_channel_trade_write_context_factory(
                            owner="price_channel_snapshot_refresh"
                        ),
                    )
                finally:
                    try:
                        if trade_conn is not None:
                            trade_conn.close()
                    finally:
                        try:
                            if process_entered:
                                process_lock_ctx.__exit__(None, None, None)
                        finally:
                            _market_substrate_refresh_lock.release()
                logger.info(
                    "EDLI market-channel refreshed executable snapshots: reason=%s token_id=%s condition_id=%s summary=%s",
                    action.reason,
                    action.token_id,
                    action.condition_id,
                    summary,
                )

            # The redecision-routing decision (WHICH families to re-solve) is a decision-layer
            # concern this boundary module only WIRES IN, never inlines (R6 split).
            from src.events.price_channel_redecision_router import (
                _edli_price_channel_redecision_sink,
            )

            with PolymarketClient() as clob:
                service = MarketChannelOnlineService(
                    MarketChannelIngestor(
                        EventWriter(world_conn),
                        active_token_ids=token_ids,
                        token_metadata=token_metadata,
                        feasibility_conn=feasibility_conn,
                        feasibility_schema="trades",
                        coalescer=EventCoalescer(max_market_keys=1000),
                        market_event_sink=_edli_price_channel_redecision_sink(conn),
                        market_event_sink_independently_coordinated=True,
                    ),
                    fetch_orderbook=clob.get_orderbook_snapshot,
                    fetch_orderbooks=getattr(clob, "get_orderbook_snapshots", None),
                    invalidate_snapshot=_invalidate_snapshot_action,
                    refresh_snapshot=_refresh_snapshot_action,
                    max_refresh_actions_per_window=_edli_bounded_positive_int(
                        edli_cfg,
                        "market_channel_refresh_max_actions_per_window",
                        default=5,
                        maximum=20,
                    ),
                    refresh_window_seconds=float(edli_cfg.get("market_channel_refresh_window_seconds", 60.0) or 60.0),
                    seed_first_token_ids=seed_first_token_ids,
                )
                run_market_channel_service_forever(
                    service,
                    logger=logger,
                    commit=_commit_event_and_feasibility,
                    rollback=_rollback_event_and_feasibility,
                    world_mutex=_edli_price_channel_world_trade_write_gate(
                        owner="price_channel_market_channel"
                    ),
                )
        finally:
            conn.close()

    _edli_market_channel_thread = threading.Thread(
        target=_runner,
        name="edli-market-channel",
        daemon=True,
    )
    _edli_market_channel_thread.start()
    health = {
        "active_weather_token_ids": len(token_ids),
        "priority_token_ids": len(priority_token_ids),
        "held_priority_token_ids": len(held_priority_token_ids),
        "open_rest_priority_token_ids": len(open_rest_priority_token_ids),
        "seed_first_token_ids": len(seed_first_token_ids),
        "quote_cache_enabled": bool(edli_cfg.get("market_channel_quote_cache_enabled", False)),
        "fill_authority": "user_channel_or_reconcile_only",
        "thread": "started",
        "rest_seed_status": "polymarket_public_orderbook",
        "websocket_endpoint": "polymarket_public_market_channel",
    }
    _write_scheduler_health(
        "edli_market_channel_ingestor",
        failed=False,
        extra=health,
    )
    return health
