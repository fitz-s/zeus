"""Typed evidence for a materialization BLOCKED: exactly what decided it.

SCOPE: one worker BLOCKED outcome for one city/date/metric request. The code that
decides a covered reason returns ``evidence``: the database facts that make the
verdict follow from the request. The request's own bytes are bound separately (the
consumed-input witness and the attempt fingerprint). ``evidence_holds`` re-verifies
every fact with indexed lookups on the canonical read-only forecasts connection,
inside one read transaction, and says whether the same request would block now.
DRAIN: an unsupported reason, or evidence that fails to verify or cannot be read,
binds nothing: the parent retains the request for fair retry.
RESET: a changed row, authority value, clock or a selection that fills.

Every record carries the MATERIALIZATION_CLOCK item: ``computed_at`` is lifted to
the later of the two source_run possession times (``fetch_finished_at``), so those
rows (or their absence) are part of every verdict's input.

Covered reasons:
- OM9_SOURCE_CYCLE_TOO_STALE (any prewrite BLOCKED that includes it): a function of
  the request and the clock alone.
- READINESS_CERT_CYCLE_REGRESSION, HIGH only: the incumbent certificate (readiness_state
  by scope_key) bound to posterior N whose serving key is strictly newer than the
  incoming key (the request's cycle and the clock). LOW also runs a retired-dataset
  yield proof over further rows, so LOW binds nothing.
- REQUIREMENTS_NOT_MET from FUSION_DECLINED:CURRENT_SHAPE_PROVIDER_COHORT_BELOW_PAIR
  with an empty cohort: no coherent current provider cohort exists at the decision
  cut. Re-verified over every model the family has rows for, station sources
  included: an empty superset cohort leaves every path's cohort empty, the current
  shape cannot be built, and the fusion declines (the shape is always required).
Everything else (other capture outcomes, Day0 ensemble bundles) is unsupported.
"""

# Created: 2026-10-02
# Last reused/audited: 2026-10-02
# Authority basis: merge-safety round 7 (typed evidence per BLOCKED reason).

from __future__ import annotations

from collections.abc import Mapping
import sqlite3

EVIDENCE_REVISION = "materialization_block_evidence_v1"
CLOCK = "MATERIALIZATION_CLOCK"
STALE_CYCLE = "OM9_SOURCE_CYCLE_TOO_STALE"
CERT_REGRESSION = "READINESS_CERT_CYCLE_REGRESSION"
NO_COHERENT_COHORT = "NO_COHERENT_CURRENT_PROVIDER_COHORT"


def _source_run(conn: sqlite3.Connection, source_run_id: str | None) -> dict[str, object]:
    """The possession fact one source_run row contributes; absence is a fact too."""
    row = None
    if source_run_id:
        try:
            row = conn.execute(
                "SELECT fetch_finished_at FROM source_run WHERE source_run_id = ?",
                (source_run_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None  # table absent: the same fact the clock reader returns
    return {"source_run_id": source_run_id, "fetch_finished_at": None if row is None else row[0]}


def clock_item(conn: sqlite3.Connection, request) -> dict[str, object]:
    return {
        "kind": CLOCK,
        "source_runs": [
            _source_run(conn, request.baseline_source_run_id),
            _source_run(conn, request.openmeteo_source_run_id),
        ],
    }


def cert_regression_item(
    *, scope_key: str, incumbent_posterior_id: int, incumbent_key: tuple, incoming_key: tuple,
) -> dict[str, object]:
    return {
        "kind": CERT_REGRESSION,
        "scope_key": scope_key,
        "incumbent_source_run_id": f"posterior:{incumbent_posterior_id}",
        "incumbent_posterior_id": incumbent_posterior_id,
        "incumbent_key": [value.isoformat() for value in incumbent_key],
        "incoming_key": [value.isoformat() for value in incoming_key],
    }


def no_cohort_item(
    *, city: str, metric: str, target_date: str, decision_time_iso: str, window_hours: float,
) -> dict[str, object]:
    return {
        "kind": NO_COHERENT_COHORT,
        "city": city,
        "metric": metric,
        "target_date": target_date,
        "decision_time_iso": decision_time_iso,
        "window_hours": float(window_hours),
    }


def blocked_evidence(conn: sqlite3.Connection, request, reason: str, items=()) -> dict[str, object]:
    return {
        "revision": EVIDENCE_REVISION,
        "reason": reason,
        "items": [clock_item(conn, request), *items],
    }


def _family_models(conn: sqlite3.Connection, item: Mapping[str, object]) -> tuple[str, ...]:
    # One range seek on the (city, metric, target_date) frontier index.
    return tuple(
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT model FROM raw_model_forecasts"
            " WHERE city = ? AND metric = ? AND target_date = ?",
            (item["city"], item["metric"], item["target_date"]),
        )
    )


def _holds_one(conn: sqlite3.Connection, item: Mapping[str, object]) -> bool:
    kind = item.get("kind")
    if kind == CLOCK:
        return all(
            _source_run(conn, run.get("source_run_id")) == dict(run)
            for run in item["source_runs"]
        )
    if kind == CERT_REGRESSION:
        cert = conn.execute(
            "SELECT source_run_id FROM readiness_state WHERE scope_key = ?",
            (item["scope_key"],),
        ).fetchone()
        if cert is None or cert[0] != item["incumbent_source_run_id"]:
            return False
        posterior = conn.execute(
            "SELECT source_cycle_time, computed_at FROM forecast_posteriors WHERE posterior_id = ?",
            (int(item["incumbent_posterior_id"]),),
        ).fetchone()
        if posterior is None:
            return False
        from src.data.replacement_forecast_materializer import (  # noqa: PLC0415
            _serving_key_strictly_newer, _to_utc,
        )

        current = (
            _to_utc(str(posterior[0]), field_name="source_cycle_time"),
            _to_utc(str(posterior[1]), field_name="computed_at"),
        )
        incoming = tuple(_to_utc(str(v), field_name="incoming_key") for v in item["incoming_key"])
        return _serving_key_strictly_newer(current, incoming)
    if kind == NO_COHERENT_COHORT:
        from src.data.replacement_current_value_serving import (  # noqa: PLC0415
            read_freshest_coherent_instrument_values,
        )

        models = _family_models(conn, item)
        return not models or not read_freshest_coherent_instrument_values(
            conn,
            city=str(item["city"]),
            metric=str(item["metric"]),
            target_date=str(item["target_date"]),
            decision_time_iso=str(item["decision_time_iso"]),
            models=models,
            cohort_window_hours=float(item["window_hours"]),
            include_station_sources=True,
        )
    return False


def evidence_holds(conn: sqlite3.Connection, evidence: object) -> bool:
    """Whether every fact still holds, read on one snapshot of one database.

    An empty, malformed, foreign-revision or unreadable record never holds.
    """
    if (
        not isinstance(evidence, Mapping)
        or evidence.get("revision") != EVIDENCE_REVISION
        or not isinstance(evidence.get("items"), (list, tuple))
        or len(evidence["items"]) < 1
        or evidence["items"][0].get("kind") != CLOCK
    ):
        return False
    owns = not conn.in_transaction
    try:
        if owns:
            conn.execute("BEGIN")
        return all(isinstance(item, Mapping) and _holds_one(conn, item) for item in evidence["items"])
    except (sqlite3.Error, KeyError, TypeError, ValueError, AttributeError):
        return False
    finally:
        if owns and conn.in_transaction:
            conn.rollback()
