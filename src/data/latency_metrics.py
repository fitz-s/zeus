# Created: 2026-07-02
# Last reused or audited: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 "input->q latency SLA (A2, 'THE metric')" — W0.2 packet (measure only, no gate).
"""input->q_version wall-clock latency metric (W0.2). TWO variants, two start points.

WHY TWO VARIANTS
-----------------
"input->q_version latency" is ambiguous about which "input" clock starts the
stopwatch, and the two candidates already coexist as separate persisted columns
on forecast_posteriors with genuinely different meanings:

  latency_from_issue_seconds    = computed_at - source_cycle_time
    source_cycle_time is the PROVIDER-issued run identity (e.g. "the 00Z run"),
    stamped at src/data/replacement_forecast_materializer.py:2702
    (`source_cycle_time = _to_utc(request.source_cycle_time, ...)`). This
    includes provider publication delay Zeus does not control — it answers
    "how stale is our belief relative to the model run it's based on".

  latency_from_arrival_seconds  = computed_at - source_available_at
    source_available_at is Zeus's OWN "proof of possession" clock — see
    _posterior_source_available_at / _role_possession_available_at
    (replacement_forecast_materializer.py:172-228, the C1-AVAIL-CLOCK
    mechanism, 2026-06-16): max over contributing roles of
    source_run.fetch_finished_at (the REAL download-complete wall-clock Zeus
    observed), falling back per-role to the request's preflight
    source_available_at hint when no source_run row exists for that role yet.
    This is the A2 SLA target: OUR pipeline's raw-input-arrival -> posterior
    write, excluding provider publication delay we cannot act on.

Both are already persisted per forecast_posteriors row — no new table, no new
join. The materializer computes source_available_at via the honest-possession
mechanism ALREADY (not duplicated here); reusing that column instead of
re-deriving arrival time from raw_forecast_artifacts/source_run at read time
keeps a single source of truth for "arrival" instead of two definitions that
can drift apart.

KNOWN LIMIT (disclosed, not fixed here): per _role_possession_available_at's
own docstring, only the baseline role currently writes a source_run row live
— so latency_from_arrival for the openmeteo leg is a preflight-hint fallback
today, not a source_run-verified measurement, until Open-Meteo begins
recording source_run rows (the mechanism auto-upgrades then, per that
docstring; no action needed here).

DASHBOARD QUERY
----------------
LATENCY_QUERY reads both variants directly off forecast_posteriors
(zeus-forecasts.db), no new table:

    SELECT posterior_id, family_id, city, target_date, temperature_metric,
           source_cycle_time, source_available_at, computed_at,
           latency_from_issue_seconds, latency_from_arrival_seconds
    FROM (<LATENCY_QUERY>)
    ORDER BY computed_at DESC;

fetch_recent_latencies(conn) is a thin read helper around the same query for
callers that want dicts instead of raw SQL.

DEDUP NOTE (materializer.py:3554-3564): a materialize request that hits the
sqlite3.IntegrityError fallback-to-existing-row path (a byte-identical
posterior already exists) does NOT re-run this metric's emit call — see the
call site in _insert_posterior, which only calls
emit_materialization_latency() on the fresh-INSERT success branch. A dedup
hit did not just experience new-write latency and should not be plotted as
if it did.
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


def _delta_seconds(
    start: str,
    end: str,
    *,
    start_field_name: str,
    end_field_name: str,
) -> float:
    """end - start in seconds, both ISO-8601 strings.

    Raises ValueError if end is before start — a posterior cannot be written
    before its own input existed, so a negative value means a bad timestamp
    upstream, not a valid (if surprising) latency.
    """
    start_dt = _parse_iso(start, field_name=start_field_name)
    end_dt = _parse_iso(end, field_name=end_field_name)
    delta = (end_dt - start_dt).total_seconds()
    if delta < 0:
        raise ValueError(
            f"latency_metrics: {end_field_name} {end!r} is before "
            f"{start_field_name} {start!r} (delta={delta}s)"
        )
    return delta


def compute_issue_latency_seconds(*, source_cycle_time: str, computed_at: str) -> float:
    """latency_from_issue_seconds = computed_at - source_cycle_time (provider-issued clock)."""
    return _delta_seconds(
        source_cycle_time,
        computed_at,
        start_field_name="source_cycle_time",
        end_field_name="computed_at",
    )


def compute_arrival_latency_seconds(*, source_available_at: str, computed_at: str) -> float:
    """latency_from_arrival_seconds = computed_at - source_available_at (Zeus possession clock)."""
    return _delta_seconds(
        source_available_at,
        computed_at,
        start_field_name="source_available_at",
        end_field_name="computed_at",
    )


def emit_materialization_latency(
    *,
    family_id: str | None,
    city: str,
    target_date: str,
    temperature_metric: str,
    source_cycle_time: str,
    source_available_at: str,
    computed_at: str,
    posterior_id: int,
) -> dict[str, float]:
    """Compute both latency variants and emit one structured INFO log line tagged by family.

    Call this from the materializer's write path AFTER the forecast_posteriors
    INSERT succeeds (the row itself is the persistence; this is the real-time
    emit side). Side-effect-free beyond logging — no DB round-trip, safe to
    call on every materialization event.
    """
    latency_from_issue_seconds = compute_issue_latency_seconds(
        source_cycle_time=source_cycle_time, computed_at=computed_at
    )
    latency_from_arrival_seconds = compute_arrival_latency_seconds(
        source_available_at=source_available_at, computed_at=computed_at
    )
    _logger.info(
        "q_version latency: family=%s city=%s target_date=%s metric=%s "
        "posterior_id=%s latency_from_issue_seconds=%.3f latency_from_arrival_seconds=%.3f",
        family_id,
        city,
        target_date,
        temperature_metric,
        posterior_id,
        latency_from_issue_seconds,
        latency_from_arrival_seconds,
    )
    return {
        "latency_from_issue_seconds": latency_from_issue_seconds,
        "latency_from_arrival_seconds": latency_from_arrival_seconds,
    }


# Derives both latency variants straight from columns forecast_posteriors already
# persists. No new table, no join.
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
    (julianday(computed_at) - julianday(source_cycle_time)) * 86400.0 AS latency_from_issue_seconds,
    (julianday(computed_at) - julianday(source_available_at)) * 86400.0 AS latency_from_arrival_seconds
FROM forecast_posteriors
WHERE runtime_layer = 'live' AND training_allowed = 0
"""


def fetch_recent_latencies(
    conn: Any,
    *,
    since: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Read recent input->q_version latencies (both variants), tagged by family. Read-only.

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
