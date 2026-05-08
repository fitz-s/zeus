from __future__ import annotations

import sqlite3

from src.architecture.decorators import capability


POSITION_EVENT_ENVS = ("live", "test", "replay", "backtest", "shadow")


CANONICAL_POSITION_CURRENT_COLUMNS = (
    "position_id",
    "phase",
    "trade_id",
    "market_id",
    "city",
    "cluster",
    "target_date",
    "bin_label",
    "direction",
    "unit",
    "size_usd",
    "shares",
    "cost_basis_usd",
    "entry_price",
    "p_posterior",
    "last_monitor_prob",
    "last_monitor_edge",
    "last_monitor_market_price",
    "decision_snapshot_id",
    "entry_method",
    "strategy_key",
    "edge_source",
    "discovery_mode",
    "chain_state",
    "token_id",
    "no_token_id",
    "condition_id",
    "order_id",
    "order_status",
    "updated_at",
    "temperature_metric",
)


def normalize_position_event_env(value: object, *, default: str | None = None) -> str:
    env = str(value if value not in (None, "") else (default or "")).strip().lower()
    if env not in POSITION_EVENT_ENVS:
        raise ValueError(f"position event env={env!r} is invalid")
    return env


def ordered_values(payload: dict, columns: tuple[str, ...]) -> tuple:
    return tuple(payload.get(column) for column in columns)


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def require_payload_fields(payload: dict, columns: tuple[str, ...], *, label: str) -> None:
    missing = [column for column in columns if column not in payload]
    if missing:
        raise ValueError(f"{label} missing fields: {missing}")


def validate_event_projection_pair(event: dict, projection: dict) -> None:
    if event.get("position_id") != projection.get("position_id"):
        raise ValueError("event/projection position_id mismatch")
    if event.get("strategy_key") != projection.get("strategy_key"):
        raise ValueError("event/projection strategy_key mismatch")
    phase_after = event.get("phase_after")
    if phase_after and projection.get("phase") and phase_after != projection.get("phase"):
        raise ValueError("event/projection phase mismatch")
    snapshot_id = event.get("snapshot_id")
    decision_snapshot_id = projection.get("decision_snapshot_id")
    if snapshot_id and decision_snapshot_id and snapshot_id != decision_snapshot_id:
        raise ValueError("event/projection snapshot mismatch")
    order_id = event.get("order_id")
    projection_order_id = projection.get("order_id")
    if order_id and projection_order_id and order_id != projection_order_id:
        raise ValueError("event/projection order_id mismatch")


def validate_event_projection_batch(events: list[dict], projection: dict) -> None:
    if not events:
        raise ValueError("event batch must not be empty")
    for event in events:
        if event.get("position_id") != projection.get("position_id"):
            raise ValueError("event/projection position_id mismatch")
        if event.get("strategy_key") != projection.get("strategy_key"):
            raise ValueError("event/projection strategy_key mismatch")
    final_phase = events[-1].get("phase_after")
    if final_phase and projection.get("phase") and final_phase != projection.get("phase"):
        raise ValueError("event/projection phase mismatch")


@capability("canonical_position_write", lease=True)
def upsert_position_current(conn: sqlite3.Connection, projection: dict) -> None:
    conn.execute(
        f"""
        INSERT INTO position_current ({", ".join(CANONICAL_POSITION_CURRENT_COLUMNS)})
        VALUES ({", ".join(["?"] * len(CANONICAL_POSITION_CURRENT_COLUMNS))})
        ON CONFLICT(position_id) DO UPDATE SET
            phase=excluded.phase,
            trade_id=excluded.trade_id,
            market_id=excluded.market_id,
            city=excluded.city,
            cluster=excluded.cluster,
            target_date=excluded.target_date,
            bin_label=excluded.bin_label,
            direction=excluded.direction,
            unit=excluded.unit,
            size_usd=excluded.size_usd,
            shares=excluded.shares,
            cost_basis_usd=excluded.cost_basis_usd,
            entry_price=excluded.entry_price,
            p_posterior=excluded.p_posterior,
            last_monitor_prob=excluded.last_monitor_prob,
            last_monitor_edge=excluded.last_monitor_edge,
            last_monitor_market_price=excluded.last_monitor_market_price,
            decision_snapshot_id=excluded.decision_snapshot_id,
            entry_method=excluded.entry_method,
            strategy_key=excluded.strategy_key,
            edge_source=excluded.edge_source,
            discovery_mode=excluded.discovery_mode,
            chain_state=excluded.chain_state,
            token_id=excluded.token_id,
            no_token_id=excluded.no_token_id,
            condition_id=excluded.condition_id,
            order_id=excluded.order_id,
            order_status=excluded.order_status,
            updated_at=excluded.updated_at,
            temperature_metric=excluded.temperature_metric
        """,
        ordered_values(projection, CANONICAL_POSITION_CURRENT_COLUMNS),
    )
