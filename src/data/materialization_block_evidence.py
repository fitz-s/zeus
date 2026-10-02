"""Typed evidence for a materialization BLOCKED, and its re-decision.

SCOPE: one city/date/metric request family. The code that decides a covered
BLOCKED reason records which predicate decided it and the facts it judged.
``evidence_holds`` answers one of two questions, on one read snapshot of the
forecasts DB with indexed lookups:
- prospective given (producer and unchanged-marker lookups): would THIS request,
  the one the caller would build now, still block for the recorded reason? The
  predicate is re-decided at the prospective request's effective clock.
- prospective omitted (admission of the worker's own verdict): do the facts the
  worker judged for its request still hold exactly (clock rows, certificate and
  stored keys, the negative selection at the recorded cut)?
The request's own bytes and dependencies are bound separately (the consumed-input
witness and the attempt fingerprint).
DRAIN: an unsupported reason, a malformed or unbound record, an unreadable fact,
or a predicate that no longer blocks binds nothing; the caller retains or reopens.
RESET: the request no longer blocks for the recorded reason.

Effective clock: the materializer's possession rule. computed_at is lifted to the
later of the two roles' possession times (``source_run.fetch_finished_at`` when the
row exists, else the request's own per-role source_available_at). A missing row or
missing source_run table is proven absence; any other read failure is unavailable.

Covered reasons (``SUPPORTED``) and their exact, required items:
- STALE_CYCLE: [CLOCK]. The anchor cycle is outside the cycle-age bound at the
  effective clock (``cycle_age_outside_bound``).
- CERT_REGRESSION (HIGH only): [CLOCK, CERT_REGRESSION]. The incumbent certificate
  at scope_key binds a posterior whose serving key is strictly newer than the
  incoming (source_cycle_time, effective clock).
- NO_COHERENT_COHORT: [CLOCK, NO_COHERENT_COHORT]. No coherent current provider
  cohort exists at the effective clock, over every model the family has rows for,
  station sources included; an empty superset cohort leaves every path's cohort
  empty, so the fusion's current shape cannot be built.
"""

# Created: 2026-10-02
# Last reused/audited: 2026-10-02
# Authority basis: merge-safety rounds 7-8 (typed evidence, prospective re-decision).

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import sqlite3

EVIDENCE_REVISION = "materialization_block_evidence_v2"
CLOCK = "MATERIALIZATION_CLOCK"
STALE_CYCLE = "OM9_SOURCE_CYCLE_TOO_STALE"
CERT_REGRESSION = "READINESS_CERT_CYCLE_REGRESSION"
NO_COHERENT_COHORT = "NO_COHERENT_CURRENT_PROVIDER_COHORT"
# reason -> the item kinds it must carry, in order (CLOCK first, each exactly once).
SUPPORTED: dict[str, tuple[str, ...]] = {
    STALE_CYCLE: (CLOCK,),
    CERT_REGRESSION: (CLOCK, CERT_REGRESSION),
    NO_COHERENT_COHORT: (CLOCK, NO_COHERENT_COHORT),
}
# clock role -> (request field naming its run, request field of its fallback clock)
_ROLES = {
    "baseline_b0": ("baseline_source_run_id", "baseline_source_available_at"),
    "openmeteo_ifs9_anchor": ("openmeteo_source_run_id", "openmeteo_source_available_at"),
}
_SCOPE_FIELDS = ("city", "target_date", "temperature_metric")


class EvidenceUnavailable(Exception):
    """A fact could not be read: never evidence of anything."""


def _source_run(conn: sqlite3.Connection, source_run_id: str | None) -> dict[str, object]:
    """One role's possession fact. A missing row or a missing source_run table is a
    proven absence; any other read failure is unavailable."""
    if not source_run_id:
        return {"source_run_id": None, "fetch_finished_at": None}
    try:
        row = conn.execute(
            "SELECT fetch_finished_at FROM source_run WHERE source_run_id = ?",
            (source_run_id,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if _source_run_table_absent(conn):
            return {"source_run_id": source_run_id, "fetch_finished_at": None}
        raise EvidenceUnavailable("source_run") from exc
    return {"source_run_id": source_run_id, "fetch_finished_at": None if row is None else row[0]}


def _source_run_table_absent(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'source_run'"
        ).fetchone() is None
    except sqlite3.Error as exc:
        raise EvidenceUnavailable("sqlite_master") from exc


def _utc(value: object, field: str) -> datetime:
    from src.data.replacement_forecast_materializer import _to_utc  # noqa: PLC0415

    return _to_utc(value if isinstance(value, datetime) else str(value), field_name=field)


def effective_computed_at(conn: sqlite3.Connection, payload: Mapping[str, object]) -> datetime:
    """The materializer's clock rule for a request given as its payload."""
    from src.contracts.availability_time import proof_of_possession_available_at  # noqa: PLC0415

    lifted = _utc(payload["computed_at"], "computed_at")
    for run_field, available_field in _ROLES.values():
        run = _source_run(conn, payload.get(run_field) or None)
        possession = run["fetch_finished_at"] or payload[available_field]
        lifted = max(lifted, _utc(proof_of_possession_available_at(possession), available_field))
    return lifted


# ---------------------------------------------------------------- recording


def clock_item(conn: sqlite3.Connection, request) -> dict[str, object]:
    return {
        "kind": CLOCK,
        "source_runs": [
            {"role": role, **_source_run(conn, getattr(request, run_field, None))}
            for role, (run_field, _available) in _ROLES.items()
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


def no_cohort_item(*, window_hours: float, decision_time_iso: str) -> dict[str, object]:
    return {
        "kind": NO_COHERENT_COHORT,
        "window_hours": float(window_hours),
        "decision_time_iso": decision_time_iso,
    }


def blocked_evidence(conn: sqlite3.Connection, request, reason: str, items=()) -> dict[str, object]:
    target_date = getattr(request, "target_date", None)
    return {
        "revision": EVIDENCE_REVISION,
        "reason": reason,
        "scope": {
            "city": getattr(request, "city", None),
            "target_date": target_date.isoformat() if hasattr(target_date, "isoformat") else target_date,
            "temperature_metric": getattr(request, "temperature_metric", None),
        },
        "items": [clock_item(conn, request), *items],
    }


# ------------------------------------------------------------- verification


def _well_formed(evidence: object, prospective: Mapping[str, object] | None) -> bool:
    """The discriminated schema: a supported reason, exactly its required items,
    a clock naming exactly the two roles; with a prospective request, a scope and
    clock roles equal to that request's."""
    if not isinstance(evidence, Mapping) or evidence.get("revision") != EVIDENCE_REVISION:
        return False
    reason = evidence.get("reason")
    required = SUPPORTED.get(reason) if isinstance(reason, str) else None
    items = evidence.get("items")
    if (
        required is None
        or not isinstance(items, (list, tuple))
        or tuple(item.get("kind") if isinstance(item, Mapping) else None for item in items) != required
    ):
        return False
    runs = items[0].get("source_runs")
    if (
        not isinstance(runs, (list, tuple))
        or len(runs) != len(_ROLES)
        or not all(isinstance(run, Mapping) and isinstance(run.get("role"), str) for run in runs)
        or {run["role"] for run in runs} != set(_ROLES)
    ):
        return False
    if prospective is None:
        return True
    scope = evidence.get("scope")
    if not isinstance(scope, Mapping) or any(
        not scope.get(field) or str(scope[field]) != str(prospective.get(field) or "")
        for field in _SCOPE_FIELDS
    ):
        return False
    if evidence["reason"] == CERT_REGRESSION and str(prospective.get("temperature_metric")) != "high":
        return False
    return all(
        run.get("source_run_id") == (prospective.get(_ROLES[run["role"]][0]) or None)
        for run in runs
    )


def _family_models(conn: sqlite3.Connection, scope: Mapping[str, object]) -> tuple[str, ...]:
    # One range seek on the (city, metric, target_date) frontier index.
    return tuple(
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT model FROM raw_model_forecasts"
            " WHERE city = ? AND metric = ? AND target_date = ?",
            (scope["city"], scope["temperature_metric"], scope["target_date"]),
        )
    )


def _cohort_empty(conn, scope: Mapping[str, object], at: datetime, window_hours: float) -> bool:
    from src.data.replacement_current_value_serving import (  # noqa: PLC0415
        read_freshest_coherent_instrument_values,
    )

    models = _family_models(conn, scope)
    return not models or not read_freshest_coherent_instrument_values(
        conn,
        city=str(scope["city"]),
        metric=str(scope["temperature_metric"]),
        target_date=str(scope["target_date"]),
        decision_time_iso=at.isoformat(),
        models=models,
        cohort_window_hours=float(window_hours),
        include_station_sources=True,
    )


def _incumbent_key(conn, item: Mapping[str, object]) -> tuple[datetime, datetime] | None:
    cert = conn.execute(
        "SELECT source_run_id FROM readiness_state WHERE scope_key = ?", (item["scope_key"],),
    ).fetchone()
    if cert is None or cert[0] != item["incumbent_source_run_id"]:
        return None
    posterior = conn.execute(
        "SELECT source_cycle_time, computed_at FROM forecast_posteriors WHERE posterior_id = ?",
        (int(item["incumbent_posterior_id"]),),
    ).fetchone()
    if posterior is None:
        return None
    return _utc(posterior[0], "source_cycle_time"), _utc(posterior[1], "computed_at")


def _recorded_facts_hold(conn, evidence: Mapping[str, object]) -> bool:
    from src.data.replacement_forecast_materializer import _serving_key_strictly_newer  # noqa: PLC0415

    if not all(
        {"role": run["role"], **_source_run(conn, run.get("source_run_id"))} == dict(run)
        for run in evidence["items"][0]["source_runs"]
    ):
        return False
    item = evidence["items"][-1]
    if evidence["reason"] == CERT_REGRESSION:
        incumbent = _incumbent_key(conn, item)
        incoming = tuple(_utc(value, "incoming_key") for value in item["incoming_key"])
        return incumbent is not None and _serving_key_strictly_newer(incumbent, incoming)
    if evidence["reason"] == NO_COHERENT_COHORT:
        return _cohort_empty(
            conn, evidence["scope"], _utc(item["decision_time_iso"], "decision_time_iso"),
            item["window_hours"],
        )
    return True  # STALE_CYCLE: a function of the request and the unchanged clock


def _prospective_blocks(conn, evidence: Mapping[str, object], payload: Mapping[str, object]) -> bool:
    from src.data.replacement_forecast_materializer import _serving_key_strictly_newer  # noqa: PLC0415

    effective = effective_computed_at(conn, payload)
    item = evidence["items"][-1]
    if evidence["reason"] == STALE_CYCLE:
        from src.data.replacement_forecast_cycle_policy import cycle_age_outside_bound  # noqa: PLC0415

        anchor_cycle = payload.get("openmeteo_source_cycle_time") or payload["source_cycle_time"]
        return cycle_age_outside_bound(effective, _utc(anchor_cycle, "openmeteo_source_cycle_time"))
    if evidence["reason"] == CERT_REGRESSION:
        incumbent = _incumbent_key(conn, item)
        prospective = (_utc(payload["source_cycle_time"], "source_cycle_time"), effective)
        return incumbent is not None and _serving_key_strictly_newer(incumbent, prospective)
    return _cohort_empty(conn, payload, effective, item["window_hours"])


def evidence_holds(
    conn: sqlite3.Connection,
    evidence: object,
    prospective: Mapping[str, object] | None = None,
) -> bool:
    """See the module doc. Malformed, unsupported, unbound or unreadable never holds."""
    if not _well_formed(evidence, prospective):
        return False
    owns = not conn.in_transaction
    try:
        if owns:
            conn.execute("BEGIN")
        if prospective is None:
            return _recorded_facts_hold(conn, evidence)
        return _prospective_blocks(conn, evidence, prospective)
    except (EvidenceUnavailable, sqlite3.Error, KeyError, TypeError, ValueError, AttributeError):
        return False
    finally:
        if owns and conn.in_transaction:
            conn.rollback()
