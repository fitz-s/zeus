# Created: 2026-07-08
# Last reused or audited: 2026-07-08
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R6 (venue 契约层 —
#   price_channel_ingest 3.1K 拆 venue-fact 桥接 vs re-decision 路由: venue 不决定谁
#   re-solve); docs/architecture/system_decomposition_plan.md §4.2/§7 I2 (price-channel
#   is a quote-EVIDENCE producer, never a trading authority).
"""EDLI price-channel RE-DECISION ROUTING — split out of src.ingest.price_channel_ingest.

WHY THIS MODULE EXISTS (R6 defect #4 — decision logic leaking into a boundary layer):
  ``src.ingest.price_channel_ingest`` is the venue-fact BRIDGE: it subscribes to the
  Polymarket WS channels and translates raw venue book/price/fill data into typed facts
  (``position_current`` fill bridging, ``execution_feasibility_evidence`` quote witnesses).
  Deciding WHICH money-path families a book move should trigger a re-solve for is a
  DIFFERENT concern — a decision-layer policy, not a venue-boundary fact. Before this split
  both lived in the same file, so the venue module silently doubled as a trading-decision
  router. This module is the sole owner of that decision: given a batch of quote-changed
  events, resolve which (city, target_date, temperature_metric) families are eligible for
  Tier-0 redecision and emit ``EDLI_REDECISION_PENDING`` for exactly those.

THE BOUNDARY OWNS NO DECISION LOGIC: ``price_channel_ingest`` wires
``_edli_price_channel_redecision_sink`` in as an injected ``market_event_sink`` dependency
(``MarketChannelIngestor(..., market_event_sink=_edli_price_channel_redecision_sink(conn))``)
— it never inlines the family-resolution or entry-screen logic itself. This module has no
knowledge of WS transport, REST budgets, or thread lifecycles; it only turns already-durable
quote-change events into a routing decision over connections it is handed.

THREE ADMISSION BUCKETS (unchanged from the pre-split behavior):
  - HELD: families with open local/chain exposure — always admitted, no entry screen (open
    exposure is itself money-path evidence).
  - RESTING: families with Zeus's own open resting orders — bypasses the live entry screen
    (managing existing exposure, not proposing a new entry); the redecision consumer runs
    the full decide anyway.
  - ENTRY (screened): non-held families admitted only after the live continuous-entry screen
    (current q_lcb, fresh executable price, spine inputs, full-economics backoff) clears them.

ALL cross-module imports stay LAZY (inside functions), mirroring the boundary module's own
discipline, so importing this module pulls in no trading lane (src.main / src.engine /
src.execution / src.strategy / src.signal) at load time.
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("zeus.price_channel_redecision_router")


def _edli_quote_event_token_ids(events) -> set[str]:
    tokens: set[str] = set()
    for event in events or ():
        if getattr(event, "event_type", "") not in {"BOOK_SNAPSHOT", "BEST_BID_ASK_CHANGED"}:
            continue
        try:
            payload = json.loads(str(getattr(event, "payload_json", "") or "{}"))
        except Exception:
            continue
        token = str(payload.get("token_id") or "").strip()
        if token and token != "None":
            tokens.add(token)
    return tokens


def _edli_money_path_family_keys_for_tokens(
    trade_conn,
    forecasts_conn,
    token_ids,
    *,
    trade_schema: str = "",
) -> set[tuple[str, str, str]]:
    """Resolve quote token ids to live money-path families.

    Price-channel events are token-keyed cache facts; EDLI decisions are
    family-keyed forecast events. This bridge intentionally admits only tokens
    that already belong to held exposure, resting entry commands, or the active
    weather topology. It never turns arbitrary market noise into reactor work.
    """
    from src.ingest.price_channel_ingest import _edli_schema_prefix, _edli_table_exists

    tokens = {
        str(token or "").strip()
        for token in (token_ids or set())
        if str(token or "").strip() and str(token or "").strip() != "None"
    }
    if not tokens:
        return set()

    families: set[tuple[str, str, str]] = set()
    trade_prefix = _edli_schema_prefix(trade_schema)
    placeholders = ",".join("?" for _ in tokens)

    if _edli_table_exists(trade_conn, "position_current", schema=trade_schema):
        try:
            rows = trade_conn.execute(
                f"""
                SELECT DISTINCT city, target_date, temperature_metric
                  FROM {trade_prefix}position_current
                 WHERE phase IN ('pending_entry','active','day0_window','pending_exit')
                   AND (
                        token_id IN ({placeholders})
                     OR no_token_id IN ({placeholders})
                   )
                   AND city IS NOT NULL AND TRIM(city) != ''
                   AND target_date IS NOT NULL AND TRIM(target_date) != ''
                   AND temperature_metric IN ('high', 'low')
                """,
                (*tuple(tokens), *tuple(tokens)),
            ).fetchall()
            for row in rows:
                families.add((str(row[0]), str(row[1]), str(row[2])))
        except Exception:
            pass

    condition_ids: set[str] = set()
    snapshot_table = "executable_market_snapshots"
    latest_table = "executable_market_snapshot_latest"
    if _edli_table_exists(trade_conn, latest_table, schema=trade_schema):
        pragma = (
            f"PRAGMA {trade_schema}.table_info({latest_table})"
            if trade_schema
            else f"PRAGMA table_info({latest_table})"
        )
        try:
            latest_columns = {str(row[1]) for row in trade_conn.execute(pragma).fetchall()}
            if {
                "condition_id",
                "selected_outcome_token_id",
                "yes_token_id",
                "no_token_id",
            } <= latest_columns and trade_conn.execute(
                f"SELECT 1 FROM {trade_prefix}{latest_table} LIMIT 1"
            ).fetchone():
                snapshot_table = latest_table
        except Exception:
            pass

    if _edli_table_exists(trade_conn, snapshot_table, schema=trade_schema):
        try:
            rows = trade_conn.execute(
                f"""
                SELECT DISTINCT condition_id
                  FROM {trade_prefix}{snapshot_table}
                 WHERE selected_outcome_token_id IN ({placeholders})
                    OR yes_token_id IN ({placeholders})
                    OR no_token_id IN ({placeholders})
                """,
                (*tuple(tokens), *tuple(tokens), *tuple(tokens)),
            ).fetchall()
            condition_ids.update(str(row[0] or "").strip() for row in rows)
        except Exception:
            pass
    condition_ids.discard("")
    condition_ids.discard("None")

    if condition_ids and _edli_table_exists(forecasts_conn, "market_events"):
        cond_placeholders = ",".join("?" for _ in condition_ids)
        try:
            rows = forecasts_conn.execute(
                f"""
                SELECT DISTINCT city, target_date, temperature_metric
                  FROM market_events
                 WHERE condition_id IN ({cond_placeholders})
                   AND city IS NOT NULL AND TRIM(city) != ''
                   AND target_date IS NOT NULL AND TRIM(target_date) != ''
                   AND temperature_metric IN ('high', 'low')
                """,
                tuple(condition_ids),
            ).fetchall()
            for row in rows:
                families.add((str(row[0]), str(row[1]), str(row[2])))
        except Exception:
            pass

    return {
        (city.strip(), target_date.strip(), metric.strip())
        for city, target_date, metric in families
        if city.strip() and target_date.strip() and metric.strip() in {"high", "low"}
    }


def _edli_held_family_keys_for_tokens(
    trade_conn,
    token_ids,
    *,
    trade_schema: str = "",
) -> set[tuple[str, str, str]]:
    from src.ingest.price_channel_ingest import _edli_schema_prefix, _edli_table_exists

    tokens = {
        str(token or "").strip()
        for token in (token_ids or set())
        if str(token or "").strip() and str(token or "").strip() != "None"
    }
    if not tokens or not _edli_table_exists(trade_conn, "position_current", schema=trade_schema):
        return set()
    trade_prefix = _edli_schema_prefix(trade_schema)
    placeholders = ",".join("?" for _ in tokens)
    try:
        rows = trade_conn.execute(
            f"""
            SELECT DISTINCT city, target_date, temperature_metric
              FROM {trade_prefix}position_current
             WHERE phase IN ('pending_entry','active','day0_window','pending_exit')
               AND (
                    token_id IN ({placeholders})
                 OR no_token_id IN ({placeholders})
               )
               AND city IS NOT NULL AND TRIM(city) != ''
               AND target_date IS NOT NULL AND TRIM(target_date) != ''
               AND temperature_metric IN ('high', 'low')
            """,
            (*tuple(tokens), *tuple(tokens)),
        ).fetchall()
    except Exception:
        return set()
    return {
        (str(row[0]).strip(), str(row[1]).strip(), str(row[2]).strip())
        for row in rows
        if str(row[0]).strip() and str(row[1]).strip() and str(row[2]).strip() in {"high", "low"}
    }


def _edli_own_resting_order_token_ids(
    trade_conn,
    token_ids,
    *,
    trade_schema: str = "",
) -> set[str]:
    """Tokens (from ``token_ids``) carrying one of Zeus's own OPEN resting orders.

    "Open" means the LATEST venue_order_facts row per command_id (by
    local_sequence — the table is append-only) has a state in the canonical
    OPEN_ORDER_FACT_STATES. Same "latest row per command_id in
    OPEN_ORDER_FACT_STATES" predicate self_trade_guard's now-deleted
    single-token loader used (removed as dead code in the gate-stack
    simplification, Phase 1, 2026-07-06), generalized here from a single
    token to a batch of quote-changed tokens.
    """
    from src.ingest.price_channel_ingest import _edli_schema_prefix, _edli_table_exists

    tokens = {
        str(token or "").strip()
        for token in (token_ids or set())
        if str(token or "").strip() and str(token or "").strip() != "None"
    }
    if not tokens:
        return set()
    if not (
        _edli_table_exists(trade_conn, "venue_commands", schema=trade_schema)
        and _edli_table_exists(trade_conn, "venue_order_facts", schema=trade_schema)
    ):
        return set()

    from src.state.canonical_projections import OPEN_ORDER_FACT_STATES

    trade_prefix = _edli_schema_prefix(trade_schema)
    token_placeholders = ",".join("?" for _ in tokens)
    open_state_placeholders = ",".join("?" for _ in OPEN_ORDER_FACT_STATES)
    try:
        rows = trade_conn.execute(
            f"""
            SELECT DISTINCT vc.token_id
              FROM {trade_prefix}venue_commands vc
             WHERE vc.token_id IN ({token_placeholders})
               AND upper(COALESCE(vc.state, '')) NOT IN (
                     'CANCELLED', 'CANCELED', 'EXPIRED', 'REJECTED',
                     'SUBMIT_REJECTED', 'FILLED'
               )
               AND EXISTS (
                     SELECT 1
                       FROM {trade_prefix}venue_order_facts vof
                      WHERE vof.command_id = vc.command_id
                        AND vof.state IN ({open_state_placeholders})
                        AND vof.local_sequence = (
                              SELECT MAX(vof2.local_sequence)
                                FROM {trade_prefix}venue_order_facts vof2
                               WHERE vof2.command_id = vc.command_id
                        )
               )
            """,
            (*tuple(tokens), *sorted(OPEN_ORDER_FACT_STATES)),
        ).fetchall()
    except Exception:
        return set()
    return {str(row[0] or "").strip() for row in rows if str(row[0] or "").strip()}


def _edli_resting_family_keys_for_tokens(
    trade_conn,
    forecasts_conn,
    token_ids,
    *,
    trade_schema: str = "",
) -> set[tuple[str, str, str]]:
    """Families with Zeus's own open resting orders on a quote-changed token.

    Resting capital is managing existing exposure (an entry not yet filled, or
    an exit not yet filled), not proposing a new entry — so this bucket is
    admitted WITHOUT the live entry screen
    (``_edli_screened_entry_family_keys_for_price_channel``): the redecision
    consumer runs the full decide anyway. Reuses the same token->condition_id
    ->market_events join chain as ``_edli_money_path_family_keys_for_tokens``.
    """

    resting_tokens = _edli_own_resting_order_token_ids(
        trade_conn,
        token_ids,
        trade_schema=trade_schema,
    )
    if not resting_tokens:
        return set()
    return _edli_money_path_family_keys_for_tokens(
        trade_conn,
        forecasts_conn,
        resting_tokens,
        trade_schema=trade_schema,
    )


def _edli_screened_entry_family_keys_for_price_channel(
    world_conn,
    trade_conn,
    forecasts_conn,
    families: set[tuple[str, str, str]],
    *,
    decision_time: datetime,
    trade_schema: str = "",
) -> set[tuple[str, str, str]]:
    """Entry families whose current quote tick still clears the live screen.

    The price-channel sidecar is a quote-evidence producer, not a trading
    authority. A non-held family may enter Tier-0 redecision only after the same
    continuous entry screen proves current q_lcb, fresh executable price, spine
    inputs, and recent full-economics backoff all allow it. Held families are
    handled separately because open exposure itself is money-path evidence.
    """

    clean_families = {
        (str(city or "").strip(), str(target_date or "").strip(), str(metric or "").strip())
        for city, target_date, metric in (families or set())
        if str(city or "").strip()
        and str(target_date or "").strip()
        and str(metric or "").strip() in {"high", "low"}
    }
    if not clean_families:
        return set()
    try:
        from src.events.continuous_redecision import (
            _all_latest_beliefs,
            filter_redecisions_with_spine_members,
            screen_entry_redecisions,
            screened_family_keys,
        )
    except Exception:
        return set()
    decision_iso = decision_time.astimezone(timezone.utc).isoformat()
    try:
        beliefs = [
            belief
            for belief in _all_latest_beliefs(
                world_conn,
                decision_time=decision_iso,
                forecast_only_admissible=True,
                family_keys=clean_families,
            )
            if (
                str(belief.city or "").strip(),
                str(belief.target_date or "").strip(),
                str(belief.metric or "").strip(),
            )
            in clean_families
        ]
    except Exception:
        return set()
    if not beliefs:
        return set()

    screen_trade_conn = trade_conn
    close_trade_conn = False
    if trade_schema:
        try:
            from src.state.db import get_trade_connection_read_only

            screen_trade_conn = get_trade_connection_read_only()
            close_trade_conn = True
        except Exception:
            return set()
    try:
        redecisions = screen_entry_redecisions(
            world_conn,
            screen_trade_conn,
            decision_time=decision_iso,
            min_edge=0.01,
            acted_state=None,
            beliefs=beliefs,
        )
    except Exception:
        return set()
    finally:
        if close_trade_conn:
            try:
                screen_trade_conn.close()
            except Exception:
                pass
    if not redecisions:
        return set()
    try:
        redecisions = filter_redecisions_with_spine_members(
            forecasts_conn,
            redecisions,
            beliefs=beliefs,
            decision_time=decision_iso,
        )
        return screened_family_keys(world_conn, redecisions, beliefs=beliefs)
    except Exception:
        return set()


def _edli_pending_redecision_entity_keys(world_conn) -> set[str]:
    from src.ingest.price_channel_ingest import _edli_table_exists

    if not (
        _edli_table_exists(world_conn, "opportunity_events")
        and _edli_table_exists(world_conn, "opportunity_event_processing")
    ):
        return set()
    try:
        rows = world_conn.execute(
            """
            SELECT e.entity_key
              FROM opportunity_events e
              JOIN opportunity_event_processing p ON p.event_id = e.event_id
             WHERE e.event_type = 'EDLI_REDECISION_PENDING'
               AND p.consumer_name = 'edli_reactor_v1'
               AND p.processing_status IN ('pending','processing')
            """
        ).fetchall()
    except Exception:
        return set()
    return {str(row[0] or "").strip() for row in rows if str(row[0] or "").strip()}


def _edli_pending_redecision_family_keys(
    entity_keys: set[str],
) -> set[tuple[str, str, str]]:
    """Recover the canonical family prefix from FSR redecision entity keys."""

    families: set[tuple[str, str, str]] = set()
    for entity_key in entity_keys:
        parts = str(entity_key or "").split("|", 3)
        if len(parts) != 4:
            continue
        city, target_date, metric, source_run_id = (part.strip() for part in parts)
        if city and target_date and metric in {"high", "low"} and source_run_id:
            families.add((city, target_date, metric))
    return families


def _edli_redecision_event_with_origin(
    event,
    origin: str,
    *,
    changed_token_ids=(),
):
    from src.events.opportunity_event import make_opportunity_event

    try:
        payload = json.loads(str(event.payload_json or "{}"))
        if not isinstance(payload, dict):
            return event
        payload["redecision_origin"] = str(origin)
        tokens = sorted(
            {
                str(token or "").strip()
                for token in changed_token_ids
                if str(token or "").strip()
                and str(token or "").strip() != "None"
            }
        )
        if origin == "market_price" and tokens:
            payload["price_changed_token_ids"] = tokens
        return make_opportunity_event(
            event_type=event.event_type,
            entity_key=event.entity_key,
            source=event.source,
            observed_at=event.observed_at,
            available_at=event.available_at,
            received_at=event.received_at,
            causal_snapshot_id=event.causal_snapshot_id,
            payload=payload,
            priority=event.priority,
            expires_at=event.expires_at,
            created_at=event.created_at,
        )
    except Exception:
        return event


def _edli_price_channel_redecision_events_for_events(
    world_conn,
    trade_conn,
    forecasts_conn,
    events,
    *,
    received_at: str,
    trade_schema: str = "",
) -> list:
    """Build EDLI_REDECISION_PENDING events from durable quote changes.

    The raw market-channel events stay cache-only/ignored. This function derives
    the family-level decision trigger from successfully persisted quote evidence,
    so live orders and positions re-enter the normal forecast decision path on
    price movement without letting the entire market-data stream flood reactor
    priority lanes.
    """

    tokens = _edli_quote_event_token_ids(events)
    families = _edli_money_path_family_keys_for_tokens(
        trade_conn,
        forecasts_conn,
        tokens,
        trade_schema=trade_schema,
    )
    pending_entity_keys = _edli_pending_redecision_entity_keys(world_conn)
    pending_families = _edli_pending_redecision_family_keys(pending_entity_keys)
    families.difference_update(pending_families)
    if not families:
        logger.debug(
            "EDLI price-channel redecision skipped: all quote families already pending "
            "tokens=%d pending_families=%d",
            len(tokens),
            len(pending_families),
        )
        return []
    try:
        decision_time = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
        if decision_time.tzinfo is None:
            decision_time = decision_time.replace(tzinfo=timezone.utc)
        decision_time = decision_time.astimezone(timezone.utc)
    except Exception:
        decision_time = datetime.now(timezone.utc)
    held_families = _edli_held_family_keys_for_tokens(
        trade_conn,
        tokens,
        trade_schema=trade_schema,
    )
    held_families.intersection_update(families)
    entry_families = _edli_screened_entry_family_keys_for_price_channel(
        world_conn,
        trade_conn,
        forecasts_conn,
        set(families) - set(held_families),
        decision_time=decision_time,
        trade_schema=trade_schema,
    )
    # Resting-capital families (Zeus's own open resting orders) bypass the
    # live entry screen entirely — they are managing existing exposure, not
    # proposing a new entry, and the redecision consumer runs the full decide
    # anyway. No new cap is added for this bucket: the entity-key debounce in
    # _edli_pending_redecision_entity_keys (consumer edli_reactor_v1) already
    # bounds the lane to one pending row per family by construction.
    unresolved_families = families - held_families - entry_families
    resting_families = set()
    if unresolved_families:
        resting_families = _edli_resting_family_keys_for_tokens(
            trade_conn,
            forecasts_conn,
            tokens,
            trade_schema=trade_schema,
        )
        resting_families.intersection_update(unresolved_families)
    families = held_families | entry_families | resting_families
    if not families:
        return []
    from src.events.triggers.forecast_snapshot_ready import (
        ForecastSnapshotReadyTrigger,
        executable_forecast_live_eligible_reader,
    )
    from src.events.event_writer import EventWriter

    trigger = ForecastSnapshotReadyTrigger(
        EventWriter(world_conn),
        live_eligibility_reader=executable_forecast_live_eligible_reader(forecasts_conn),
    )
    events_to_emit = trigger.build_committed_snapshot_events(
        forecasts_conn=forecasts_conn,
        decision_time=decision_time,
        received_at=decision_time.isoformat(),
        limit=None,
        source=f"market_channel_price:{decision_time.isoformat()}",
        already_pending_keys=pending_entity_keys,
        event_type="EDLI_REDECISION_PENDING",
        restrict_to_families=families,
        phase_filter_exempt_families=held_families | resting_families,
    )
    (logger.info if events_to_emit else logger.debug)(
        "EDLI price-channel redecision buckets held=%d screened=%d resting=%d "
        "union=%d events=%d",
        len(held_families),
        len(entry_families),
        len(resting_families),
        len(families),
        len(events_to_emit),
    )
    return [
        _edli_redecision_event_with_origin(
            event,
            "market_price",
            changed_token_ids=tokens,
        )
        for event in events_to_emit
    ]


def _edli_emit_price_channel_redecisions_for_events(
    world_conn,
    trade_conn,
    forecasts_conn,
    events,
    *,
    received_at: str,
    trade_schema: str = "",
) -> int:
    """Emit redecision events on a caller-coordinated WORLD writer."""

    events_to_emit = _edli_price_channel_redecision_events_for_events(
        world_conn,
        trade_conn,
        forecasts_conn,
        events,
        received_at=received_at,
        trade_schema=trade_schema,
    )
    return _edli_write_price_channel_redecision_events(world_conn, events_to_emit)


def _edli_write_price_channel_redecision_events(world_conn, events) -> int:
    """Atomically debounce and write one pending redecision per family."""

    from src.events.event_writer import EventWriter

    claimed = _edli_pending_redecision_entity_keys(world_conn)
    admitted = []
    for event in events:
        entity_key = str(getattr(event, "entity_key", "") or "").strip()
        if not entity_key or entity_key in claimed:
            continue
        claimed.add(entity_key)
        admitted.append(event)
    emitted = EventWriter(world_conn).write_many(admitted)
    return sum(1 for result in emitted if result.inserted)


def _edli_price_channel_redecision_sink(_world_with_trades_conn=None, *, trade_schema: str = "trades"):
    """Build an independently coordinated market-event sink.

    This is the ONE seam the venue-fact boundary (``price_channel_ingest``) reaches into
    the decision layer through: it hands this sink to ``MarketChannelIngestor`` as its
    ``market_event_sink`` dependency and never inlines the routing decision itself.
    """

    def _sink(events) -> None:
        from src.state.db import (
            get_forecasts_connection_read_only,
            get_trade_connection_read_only,
            get_world_connection_read_only,
        )

        with contextlib.ExitStack() as stack:
            world_read = stack.enter_context(
                contextlib.closing(get_world_connection_read_only())
            )
            trade_read = stack.enter_context(
                contextlib.closing(get_trade_connection_read_only())
            )
            forecasts_read = stack.enter_context(
                contextlib.closing(get_forecasts_connection_read_only())
            )
            events_to_emit = _edli_price_channel_redecision_events_for_events(
                world_read,
                trade_read,
                forecasts_read,
                events,
                received_at=datetime.now(timezone.utc).isoformat(),
                trade_schema="",
            )
        if not events_to_emit:
            return

        from src.ingest.price_channel_ingest import (
            _edli_price_channel_world_write_connection,
        )

        with _edli_price_channel_world_write_connection(
            owner="price_channel_redecision_emit"
        ) as world_write:
            emitted = _edli_write_price_channel_redecision_events(
                world_write,
                events_to_emit,
            )
            world_write.commit()
        if emitted:
            from src.runtime.reactor_wake import publish_reactor_wake

            event_ids = tuple(
                event_id
                for event in events_to_emit
                if (event_id := str(getattr(event, "event_id", "") or "").strip())
            )
            publish_reactor_wake(
                source="price_channel_redecision_router",
                reason="market_price_advanced",
                event_ids=event_ids,
            )
            logger.info(
                "EDLI market-channel price trigger emitted redecision events=%d "
                "quote_events=%d reactor_wake_events=%d",
                emitted,
                len(events),
                len(event_ids),
            )

    return _sink


class _CoalescingPriceChannelRedecisionSink:
    """Keep decision routing off the WS receive loop and retain latest per token."""

    def __init__(self, sink) -> None:  # noqa: ANN001
        self._sink = sink
        self._lock = threading.Lock()
        self._pending: dict[str, object] = {}
        self._running = False
        self._idle = threading.Event()
        self._idle.set()

    @staticmethod
    def _event_key(event) -> str | None:  # noqa: ANN001
        tokens = _edli_quote_event_token_ids((event,))
        return next(iter(tokens), None)

    def __call__(self, events) -> None:  # noqa: ANN001
        start = False
        with self._lock:
            for event in events or ():
                key = self._event_key(event)
                if key is not None:
                    self._pending[key] = event
            if self._pending and not self._running:
                self._running = True
                self._idle.clear()
                start = True
        if start:
            threading.Thread(
                target=self._drain,
                name="price-channel-redecision",
                daemon=True,
            ).start()

    def _drain(self) -> None:
        failures = 0
        while True:
            with self._lock:
                if not self._pending:
                    self._running = False
                    self._idle.set()
                    return
                batch = tuple(self._pending.values())
                self._pending.clear()
            try:
                self._sink(batch)
            except Exception as exc:  # noqa: BLE001 - derived work retries off-loop
                failures += 1
                with self._lock:
                    for event in batch:
                        key = self._event_key(event)
                        if key is not None:
                            self._pending.setdefault(key, event)
                delay = min(30.0, float(2 ** min(failures - 1, 5)))
                logger.warning(
                    "price-channel redecision worker failed; retry_after_seconds=%.1f "
                    "batch=%d pending=%d: %s: %s",
                    delay,
                    len(batch),
                    len(self._pending),
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                time.sleep(delay)
            else:
                failures = 0

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)


def _edli_coalesced_price_channel_redecision_sink(
    _world_with_trades_conn=None,
    *,
    trade_schema: str = "trades",
):
    return _CoalescingPriceChannelRedecisionSink(
        _edli_price_channel_redecision_sink(
            _world_with_trades_conn,
            trade_schema=trade_schema,
        )
    )
