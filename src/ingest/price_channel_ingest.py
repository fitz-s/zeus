# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: docs/architecture/system_decomposition_plan.md
#   §4.2 (Price-Channel / CLOB-Fact Ingest), §6 (P3 row), §7 (I2 no-back-coupling:
#   durable fill bridge + execution_feasibility_evidence), §8 Step 3 (lift the
#   user-channel WS thread + market-channel + reconcile cycles), §9 (regression-
#   unconstructable proof — failure-domain isolation of the WS submit latch).
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
  ``edli_live_order_events`` on EVERY cycle, so NO fill is lost across the conceptual
  cutover from "WS thread in src.main" to "WS thread in P3". The order-runtime BOOT
  recovery (``_edli_boot_fill_bridge_recovery``, which STAYS in src.main) imports THIS
  same scan helper so a restart on either side heals any orphaned confirmed fill. The
  scan is the single canonical copy — src.main imports it from here (mirroring the P4
  pattern ``from src.execution.post_trade_capital import _harvester_cycle``).

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
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import settings

logger = logging.getLogger("zeus.price_channel_ingest")

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

# Live-execution-mode constants (moved verbatim from src/main.py:83-96) — needed by the
# reconcile-runtime gate. Kept LOCAL here so the lane module never imports src.main.
LIVE_EXECUTION_MODES = {
    "legacy_cron",
    "edli_shadow_no_submit",
    "edli_submit_disabled_bridge",
    "edli_live_canary",
    "edli_live",
    "disabled",
}
EDLI_EVENT_DRIVEN_MODES = {
    "edli_shadow_no_submit",
    "edli_submit_disabled_bridge",
    "edli_live_canary",
    "edli_live",
}

MARKET_CHANNEL_CANDIDATE_QUOTE_REFRESH_BUDGET_SECONDS_DEFAULT = 15.0
MARKET_CHANNEL_HELD_QUOTE_REFRESH_BUDGET_SECONDS_DEFAULT = 45.0

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
    already_bridged_repair_limit: int = 50,
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

    INV-37 / transaction ownership: reads ``edli_live_order_events`` and writes
    ``position_current`` / ``position_events`` ON THE SAME connection ``conn``
    (in production a trade connection with ``world`` ATTACHed). Performs NO
    independent connection and does NOT commit — the caller owns the transaction
    boundary (the cycle / boot wrapper commits once after the scan).

    Returns the number of orphaned fills bridged this pass.
    """
    from src.events.edli_position_bridge import (
        DISPOSITION_QUARANTINED,
        DISPOSITION_SETTLED_MARKET,
        _QUARANTINE_THRESHOLD,
        _aggregate_event_rows,
        _edli_events_table,
        _has_confirmed_fill,
        _increment_failure_count,
        _latest_payload,
        _market_is_settled,
        _quarantine_aggregate,
        _record_settled_disposition,
        edli_bridge_position_id,
        edli_bridge_position_id_legacy,
        get_fill_bridge_disposition,
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
    already_bridged_repairs_seen = 0
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
            SELECT position_id
              FROM position_current
             WHERE position_id IN (?, ?)
             ORDER BY CASE WHEN position_id = ? THEN 0 ELSE 1 END
             LIMIT 1
            """,
            (position_id, legacy_position_id, position_id),
        ).fetchone()
        if existing is not None:
            existing_position_id = str(_row_get(existing, "position_id"))
            if already_bridged_repairs_seen < max(0, already_bridged_repair_limit):
                already_bridged_repairs_seen += 1
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
            # Already bridged (wide or legacy id) — idempotent skip.
            continue

        # Disposition check: skip terminally routed aggregates (settled or quarantined).
        # These do NOT count against the new-fill budget.
        prior_disposition = get_fill_bridge_disposition(conn, aggregate_id)
        if prior_disposition in (DISPOSITION_SETTLED_MARKET, DISPOSITION_QUARANTINED):
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
            if attempt_count >= _QUARANTINE_THRESHOLD:
                logger.error(
                    "EDLI fill-bridge: QUARANTINED aggregate=%s after %d consecutive "
                    "failures (excluded from future scans); last_error=%s",
                    aggregate_id,
                    attempt_count,
                    error_str[:500],
                )
                try:
                    _quarantine_aggregate(conn, aggregate_id, error_str, attempt_count, now_str)
                except Exception:  # noqa: BLE001
                    pass
            else:
                logger.error(
                    "EDLI durable fill-bridge: failed to bridge aggregate %s "
                    "(attempt %d/%d; EDLI events persist, next scan retries): %s",
                    aggregate_id,
                    attempt_count,
                    _QUARANTINE_THRESHOLD,
                    exc,
                    exc_info=True,
                )
    return bridged


def _edli_user_channel_reconcile_runtime_enabled(edli_cfg: dict) -> bool:
    if not edli_cfg.get("enabled"):
        return False
    if bool(edli_cfg.get("edli_user_channel_reconcile_enabled", False)):
        return True
    return (
        _live_execution_mode(edli_cfg) == "edli_shadow_no_submit"
        and _truthy_env("ZEUS_USER_CHANNEL_WS_ENABLED")
    )


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
    from src.state.db import get_world_connection

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

    conn = get_world_connection(write_class="live")
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
        from src.events.edli_trade_fact_bridge import append_confirmed_trade_facts_to_edli

        reconcile_count += append_confirmed_trade_facts_to_edli(conn, now=now)
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


def _edli_candidate_priority_token_ids(world_conn, *, lookback_hours: float = 48.0, limit: int = 4000) -> set[str]:
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
        return set()
    try:
        has_table = world_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='no_trade_regret_events'"
        ).fetchone()
    except Exception:
        return set()
    if not has_table:
        return set()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(0.0, lookback_hours))).isoformat()
    try:
        rows = world_conn.execute(
            """
            SELECT DISTINCT token_id
            FROM no_trade_regret_events
            WHERE token_id IS NOT NULL AND token_id != '' AND token_id != 'None'
              AND created_at >= ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (cutoff, int(limit)),
        ).fetchall()
    except Exception:
        return set()
    return {str(r[0]) for r in rows if r and r[0]}


def _edli_held_position_priority_token_ids(trade_conn) -> set[str]:
    """Tokens for open local/chain exposure that need immediate quote evidence."""

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
        rows = trade_conn.execute(
            """
            SELECT token_id, no_token_id
              FROM position_current
             WHERE phase IN ('pending_entry','active','day0_window','pending_exit')
            """
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


def _edli_order_token_ids_by_feasibility_age(
    trade_conn,
    token_ids: set[str],
) -> list[str]:
    """Oldest/missing quote evidence first for bounded held-position refreshes."""

    tokens = sorted({str(token_id) for token_id in token_ids if str(token_id or "").strip()})
    if not tokens:
        return []
    try:
        has_table = trade_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_feasibility_evidence'"
        ).fetchone()
    except Exception:
        return tokens
    if not has_table:
        return tokens
    latest_by_token: dict[str, str | None] = {}
    for token in tokens:
        try:
            row = trade_conn.execute(
                """
                SELECT created_at
                  FROM execution_feasibility_evidence
                 WHERE token_id = ?
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (token,),
            ).fetchone()
        except Exception:
            return tokens
        latest_by_token[token] = str(row[0]) if row and row[0] else None
    return sorted(
        tokens,
        key=lambda token: (
            latest_by_token.get(token) is not None,
            latest_by_token.get(token) or "",
            token,
        ),
    )


def _edli_market_channel_seed_first_token_ids(
    *,
    held_priority_token_ids: set[str],
    candidate_priority_token_ids: set[str],
) -> set[str]:
    """REST-seed tokens that must be fresh before the broad market universe.

    Open exposure owns the strictest freshness SLA: monitor/redecision/exit can
    act only when held-position quote evidence is current. Candidate tokens still
    stay pinned in the subscribed universe, but seeding a large candidate set
    before held tokens lets open exposure age past preflight/redecision limits.
    When there is no open exposure, fall back to candidate seeding so entry
    witness rows are still warmed promptly.
    """

    held = {str(token or "").strip() for token in held_priority_token_ids}
    held.discard("")
    held.discard("None")
    if held:
        return held
    candidates = {str(token or "").strip() for token in candidate_priority_token_ids}
    candidates.discard("")
    candidates.discard("None")
    return candidates


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
        _world_write_mutex,
        active_weather_token_metadata_for_tokens,
    )
    from src.state.db import get_trade_connection, get_world_connection

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
        token_metadata = active_weather_token_metadata_for_tokens(
            trade_read,
            token_ids=ordered_held_token_ids,
        )
    finally:
        trade_read.close()

    if not token_metadata:
        return {
            "held_priority_token_ids": len(held_token_ids),
            "held_quote_refresh_events": 0,
            "skipped": "no_held_token_metadata",
        }

    # INV-37 (PR415 B5, 2026-06-20): write the world event (opportunity_events) AND
    # the trade-owned book witness (execution_feasibility_evidence) through ONE
    # connection with a SINGLE atomic commit, never two independent connections
    # committed separately. world.db is MAIN (so the EventStore's unqualified
    # opportunity_events + its sqlite_master guard resolve to the real world log)
    # and zeus_trades.db is ATTACHed as 'trades' (so the schema-qualified feasibility
    # insert reaches the runtime-read trades table, never the world shadow). A single
    # conn.commit() on the ATTACHed connection is atomic across BOTH databases — the
    # same INV-37 atomic-commit shape the EDLI position bridge uses.
    from src.state.db import get_world_connection_with_trades_required

    ordered_metadata_tokens = [
        token_id for token_id in ordered_held_token_ids if token_id in token_metadata
    ]

    # The single ATTACHed connection preserves the atomic world-event +
    # trades.feasibility commit. Do not use the flocked context here: REST book
    # fetches happen inside seed_rest_books_in_chunks before each DB chunk write,
    # and holding cross-process trade/world writer flocks across those network
    # calls starves live redecision's executable snapshot refresh.
    conn = get_world_connection_with_trades_required(write_class="live")
    try:
        def _commit_atomic_cross_db() -> None:
            conn.commit()

        with PolymarketClient() as clob:
            service = MarketChannelOnlineService(
                MarketChannelIngestor(
                    EventWriter(conn),
                    active_token_ids=set(token_metadata),
                    token_metadata=token_metadata,
                    feasibility_conn=conn,
                    feasibility_schema="trades",
                    coalescer=EventCoalescer(max_market_keys=1000),
                ),
                fetch_orderbook=clob.get_orderbook_snapshot,
            )
            written = service.seed_rest_books_in_chunks(
                token_ids=ordered_metadata_tokens,
                received_at=datetime.now(timezone.utc).isoformat(),
                world_mutex=_world_write_mutex(),
                commit=_commit_atomic_cross_db,
                logger=logger,
                deadline_monotonic=deadline,
            )
        elapsed_seconds = max(0.0, time.monotonic() - started_monotonic)
        return {
            "held_priority_token_ids": len(held_token_ids),
            "held_token_metadata": len(token_metadata),
            "held_quote_refresh_events": int(written),
            "held_quote_refresh_attempted_tokens": len(ordered_metadata_tokens),
            "budget_seconds": budget,
            "elapsed_seconds": elapsed_seconds,
            "budget_exhausted": elapsed_seconds >= budget,
            "budget_skipped_tokens": max(0, len(ordered_metadata_tokens) - int(written)),
        }
    finally:
        conn.close()


def _edli_refresh_candidate_priority_quote_evidence(
    *,
    limit: int = 128,
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
        _world_write_mutex,
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
    budget = max(0.001, float(budget_seconds))
    deadline = started_monotonic + budget
    if not candidate_token_ids:
        return {"candidate_priority_token_ids": 0, "candidate_quote_refresh_events": 0}

    trade_read = get_trade_connection(write_class=None)
    try:
        token_metadata = active_weather_token_metadata_for_tokens(
            trade_read,
            token_ids=candidate_token_ids,
        )
    finally:
        trade_read.close()

    if not token_metadata:
        return {
            "candidate_priority_token_ids": len(candidate_token_ids),
            "candidate_quote_refresh_events": 0,
            "skipped": "no_candidate_token_metadata",
        }

    # INV-37 (PR415 B5, 2026-06-20): single connection + single atomic commit for the
    # world-event + trade-feasibility cross-DB pair (see the held-priority twin above
    # for the full rationale + the shadow-table hazard this world-MAIN + ATTACHed
    # 'trades' + schema-qualified-feasibility shape avoids).
    from src.state.db import get_world_connection_with_trades_required

    # Same lock discipline as held-position refresh: one world-main connection
    # with trades attached, but no cross-process writer flock held across REST
    # fetches. Each seed chunk still commits atomically on this single connection.
    conn = get_world_connection_with_trades_required(write_class="live")
    try:
        def _commit_atomic_cross_db() -> None:
            conn.commit()

        with PolymarketClient() as clob:
            service = MarketChannelOnlineService(
                MarketChannelIngestor(
                    EventWriter(conn),
                    active_token_ids=set(token_metadata),
                    token_metadata=token_metadata,
                    feasibility_conn=conn,
                    feasibility_schema="trades",
                    coalescer=EventCoalescer(max_market_keys=1000),
                ),
                fetch_orderbook=clob.get_orderbook_snapshot,
            )
            written = service.seed_rest_books_in_chunks(
                token_ids=set(token_metadata),
                received_at=datetime.now(timezone.utc).isoformat(),
                world_mutex=_world_write_mutex(),
                commit=_commit_atomic_cross_db,
                logger=logger,
                deadline_monotonic=deadline,
            )
        elapsed_seconds = max(0.0, time.monotonic() - started_monotonic)
        return {
            "candidate_priority_token_ids": len(candidate_token_ids),
            "candidate_token_metadata": len(token_metadata),
            "candidate_quote_refresh_events": int(written),
            "budget_seconds": budget,
            "elapsed_seconds": elapsed_seconds,
            "budget_exhausted": elapsed_seconds >= budget,
        }
    finally:
        conn.close()


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
    _write_scheduler_health(
        "edli_held_quote_refresh",
        failed=False,
        extra=result,
    )
    return result


def _edli_market_channel_ingestor_cycle() -> None:
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
                default=128,
                maximum=1000,
            ),
            budget_seconds=_edli_bounded_positive_float(
                edli_cfg,
                "market_channel_candidate_quote_refresh_budget_seconds",
                default=MARKET_CHANNEL_CANDIDATE_QUOTE_REFRESH_BUDGET_SECONDS_DEFAULT,
                maximum=120.0,
            ),
        )
        _write_scheduler_health(
            "edli_market_channel_ingestor",
            failed=False,
            extra={
                "thread": "alive",
                "quote_cache_enabled": bool(edli_cfg.get("market_channel_quote_cache_enabled", False)),
                "fill_authority": "user_channel_or_reconcile_only",
                "held_quote_refresh": "delegated_to_edli_held_quote_refresh",
                "candidate_quote_refresh": candidate_refresh,
            },
        )
        return

    from src.events.triggers.market_channel_ingestor import active_weather_token_metadata_for_tokens
    from src.state.db import get_trade_connection, get_world_connection

    # Candidate universe (Blocker #52): tokens the reactor recently decided on must
    # be PINNED into the ingestor universe so each has a fresh execution_feasibility_
    # evidence row before the pre-submit witness reads it. The full latest-per-market
    # universe is captured up to the cap; candidates are never dropped by the cap.
    candidate_priority_token_ids: set[str] = set()
    world_read = get_world_connection(write_class=None)
    try:
        candidate_priority_limit = _edli_bounded_positive_int(
            edli_cfg,
            "market_channel_candidate_priority_max_tokens",
            default=128,
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

    trade_conn = get_trade_connection(write_class=None)
    try:
        held_priority_token_ids = _edli_held_position_priority_token_ids(trade_conn)
        priority_token_ids = set(candidate_priority_token_ids)
        priority_token_ids.update(held_priority_token_ids)
        seed_first_token_ids = _edli_market_channel_seed_first_token_ids(
            held_priority_token_ids=held_priority_token_ids,
            candidate_priority_token_ids=candidate_priority_token_ids,
        )
        token_metadata = active_weather_token_metadata_for_tokens(
            trade_conn,
            token_ids=priority_token_ids,
        )
        token_ids = set(token_metadata)
    finally:
        trade_conn.close()

    if not token_ids:
        _write_scheduler_health(
            "edli_market_channel_ingestor",
            failed=False,
            extra={
                "active_weather_token_ids": 0,
                "priority_token_ids": len(priority_token_ids),
                "held_priority_token_ids": len(held_priority_token_ids),
                "seed_first_token_ids": len(seed_first_token_ids),
                "quote_cache_enabled": bool(edli_cfg.get("market_channel_quote_cache_enabled", False)),
                "fill_authority": "user_channel_or_reconcile_only",
                "skipped": "no_priority_token_metadata",
            },
        )
        return

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

        # INV-37 (PR415 B5, 2026-06-20): the long-lived market-channel ingestor writes
        # the world event (opportunity_events) AND the trade-owned feasibility witness
        # (execution_feasibility_evidence) atomically per unit through ONE connection
        # (world.db MAIN + zeus_trades.db ATTACHed as 'trades'), never two independent
        # connections committed separately. The NON-flocked helper is used here because
        # this connection lives for the whole forever-loop — holding cross-DB writer
        # flocks for that lifetime would starve every other writer; each per-unit
        # commit is still atomic across both DBs (single connection) and serialized on
        # zeus-world.db by the world write mutex inside the service loop. The
        # feasibility insert is schema-qualified 'trades' (feasibility_schema below) so
        # it reaches the runtime-read trades table, never the world shadow.
        conn = get_world_connection_with_trades_required(write_class="live")
        world_conn = conn  # EventWriter target = world MAIN (unqualified opportunity_events)
        feasibility_conn = conn

        def _commit_event_and_feasibility() -> None:
            conn.commit()

        def _rollback_event_and_feasibility() -> None:
            conn.rollback()

        try:
            def _invalidate_snapshot_action(action: "MarketChannelAction") -> None:
                from src.state.db import get_trade_connection

                trade_conn = get_trade_connection(write_class="live")
                try:
                    invalidated = invalidate_executable_snapshots_for_market_channel_action(
                        trade_conn,
                        action,
                        invalidated_at=datetime.now(timezone.utc),
                    )
                    if invalidated:
                        trade_conn.commit()
                finally:
                    trade_conn.close()

            def _refresh_snapshot_action(action: "MarketChannelAction") -> None:
                from src.data.market_scanner import (
                    MarketEventsPersistenceError,
                    find_weather_markets_or_raise,
                    refresh_executable_market_substrate_snapshots,
                )
                from src.state.db import get_trade_connection

                substrate_acquired = _market_substrate_refresh_lock.acquire(blocking=False)
                if not substrate_acquired:
                    logger.info(
                        "EDLI market-channel refresh skipped: executable substrate refresh already running"
                    )
                    return
                trade_conn = None
                try:
                    trade_conn = get_trade_connection(write_class="live")
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
                    summary = refresh_executable_market_substrate_snapshots(
                        trade_conn,
                        **_edli_market_channel_refresh_kwargs(
                            action, markets, clob, datetime.now(timezone.utc)
                        ),
                    )
                    trade_conn.commit()
                finally:
                    try:
                        if trade_conn is not None:
                            trade_conn.close()
                    finally:
                        _market_substrate_refresh_lock.release()
                logger.info(
                    "EDLI market-channel refreshed executable snapshots: reason=%s token_id=%s condition_id=%s summary=%s",
                    action.reason,
                    action.token_id,
                    action.condition_id,
                    summary,
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
                    ),
                    fetch_orderbook=clob.get_orderbook_snapshot,
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
                )
        finally:
            conn.close()

    _edli_market_channel_thread = threading.Thread(
        target=_runner,
        name="edli-market-channel",
        daemon=True,
    )
    _edli_market_channel_thread.start()
    _write_scheduler_health(
        "edli_market_channel_ingestor",
        failed=False,
        extra={
            "active_weather_token_ids": len(token_ids),
            "priority_token_ids": len(priority_token_ids),
            "held_priority_token_ids": len(held_priority_token_ids),
            "seed_first_token_ids": len(seed_first_token_ids),
            "quote_cache_enabled": bool(edli_cfg.get("market_channel_quote_cache_enabled", False)),
            "fill_authority": "user_channel_or_reconcile_only",
            "thread": "started",
            "rest_seed_status": "polymarket_public_orderbook",
            "websocket_endpoint": "polymarket_public_market_channel",
        },
    )
