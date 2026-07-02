# Created: 2026-07-02
# Last reused or audited: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 "input->q latency SLA (A2, 'THE metric')" — W0.2 packet (measure only, no gate).
"""input->q_version wall-clock latency metric (W0.2).

WHY NO NEW TABLE
-----------------
The chain source_clock_live_replacement_cycle -> replacement_forecast_production ->
replacement_cycle_advance_trigger -> replacement_forecast_materializer already
persists every timestamp this metric needs on EVERY forecast_posteriors row:
source_cycle_time (source-issued clock), source_available_at (fetched), and
computed_at (written) — see src/data/replacement_forecast_materializer.py's
_insert_posterior, which stamps all three before the INSERT. family_id is on the
same row. So "persist per materialization event, tagged by family" is already
true of the existing schema; adding a second table would just be a redundant
mirror of columns forecast_posteriors already owns, with a second write to keep
in sync — the SIMPLIFY-biased choice is to compute the metric at READ time via
LATENCY_QUERY below and to EMIT (structured log) at write time for real-time
observability, not persist it a second time.

LATENCY DEFINITION
-------------------
latency_seconds = computed_at - source_cycle_time. source_cycle_time is the
source-issued clock (the model run identity), not source_available_at (when Zeus
fetched it) or computed_at (when Zeus wrote the posterior) — this matches the
architecture doc's framing of A2 as "input->q_version latency": the input clock
is when the model run itself occurred, not when Zeus noticed it.

DASHBOARD QUERY
----------------
LATENCY_QUERY reads directly off forecast_posteriors (zeus-forecasts.db), no
new table:

    SELECT posterior_id, family_id, city, target_date, temperature_metric,
           source_cycle_time, computed_at, latency_seconds
    FROM (<LATENCY_QUERY>)
    ORDER BY computed_at DESC;

fetch_recent_latencies(conn) is a thin read helper around the same query for
callers that want dicts instead of raw SQL.

DEDUP NOTE (materializer.py:3554-3564): a materialize request that hits the
sqlite3.IntegrityError fallback-to-existing-row path (a byte-identical posterior
already exists) does NOT re-run this metric's emit call — see the call site in
_insert_posterior, which only calls emit_materialization_latency() on the
success branch. A dedup hit did not just experience new-write latency and
should not be plotted as if it did.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

_logger = logging.getLogger("zeus.q_version_latency")


def _parse_iso(value: str, *, field_name: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"latency_metrics: unparseable {field_name} {value!r}") from exc
    return parsed


def compute_latency_seconds(*, source_cycle_time: str, computed_at: str) -> float:
    """latency_seconds = computed_at - source_cycle_time, both ISO-8601 strings.

    Raises ValueError if computed_at is before source_cycle_time — a posterior
    cannot be written before its own input existed, so a negative value means a
    bad timestamp somewhere upstream, not a valid (if surprising) latency.
    """
    start = _parse_iso(source_cycle_time, field_name="source_cycle_time")
    end = _parse_iso(computed_at, field_name="computed_at")
    delta = (end - start).total_seconds()
    if delta < 0:
        raise ValueError(
            f"latency_metrics: computed_at {computed_at!r} is before "
            f"source_cycle_time {source_cycle_time!r} (delta={delta}s)"
        )
    return delta


def emit_materialization_latency(
    *,
    family_id: str | None,
    city: str,
    target_date: str,
    temperature_metric: str,
    source_cycle_time: str,
    computed_at: str,
    posterior_id: int,
) -> float:
    """Compute the latency and emit a structured INFO log line tagged by family.

    Call this from the materializer's write path AFTER the forecast_posteriors
    INSERT succeeds (the row itself is the persistence; this is the real-time
    emit side). Side-effect-free beyond logging — no DB round-trip, safe to call
    on every materialization event.
    """
    latency_seconds = compute_latency_seconds(
        source_cycle_time=source_cycle_time, computed_at=computed_at
    )
    _logger.info(
        "q_version latency: family=%s city=%s target_date=%s metric=%s "
        "posterior_id=%s latency_seconds=%.3f",
        family_id,
        city,
        target_date,
        temperature_metric,
        posterior_id,
        latency_seconds,
    )
    return latency_seconds


# Derived read: latency_seconds computed straight from the columns
# forecast_posteriors already persists. No new table.
LATENCY_QUERY = """
SELECT
    posterior_id,
    family_id,
    city,
    target_date,
    temperature_metric,
    source_cycle_time,
    source_available_at,
    computed_at,
    (julianday(computed_at) - julianday(source_cycle_time)) * 86400.0 AS latency_seconds
FROM forecast_posteriors
WHERE runtime_layer = 'live' AND training_allowed = 0
"""


def fetch_recent_latencies(
    conn: Any,
    *,
    since: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Read recent input->q_version latencies, tagged by family. Read-only.

    ``since`` filters on computed_at >= since (ISO-8601). Ordered newest-first.
    """
    query = LATENCY_QUERY + " AND computed_at >= ?" if since else LATENCY_QUERY
    query += " ORDER BY computed_at DESC LIMIT ?"
    params: tuple[Any, ...] = (since, limit) if since else (limit,)

    import sqlite3

    prior_row_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(query, params)
        rows = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.row_factory = prior_row_factory
    return rows
