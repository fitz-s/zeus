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
- CERT_SUPERSEDED: [CLOCK, CERT_SUPERSEDED], admitted only with the exact failed
  request. LOW additionally binds a current incumbent baseline dataset, which
  disproves the retired-dataset yield prerequisite. It cannot fence a family.
- NO_COHERENT_COHORT: [CLOCK, NO_COHERENT_COHORT]. No coherent current provider
  cohort exists at the effective clock, over every model the family has rows for,
  station sources included; an empty superset cohort leaves every path's cohort
  empty, so the fusion's current shape cannot be built.
- ZERO_EXTRAS: [CLOCK, ZERO_EXTRAS]. The completed physical/current serving
  and capture selector admitted no non-anchor instrument, at the exact cut.
- DAY0_REQUIRED: [DAY0_REQUIRED]. The original request's target day has started,
  but its immutable input has neither an extreme nor a typed zero-observation
  declaration. Exact-request only; no prospective family fence or DB absence.
"""

# Created: 2026-10-02
# Last reused/audited: 2026-10-03
# Authority basis: merge-safety rounds 7-8 (typed evidence, prospective re-decision).

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
import sqlite3

EVIDENCE_REVISION = "materialization_block_evidence_v2"
CLOCK = "MATERIALIZATION_CLOCK"
STALE_CYCLE = "OM9_SOURCE_CYCLE_TOO_STALE"
CERT_REGRESSION = "READINESS_CERT_CYCLE_REGRESSION"
CERT_SUPERSEDED = "READINESS_CERT_SUPERSEDED"
NO_COHERENT_COHORT = "NO_COHERENT_CURRENT_PROVIDER_COHORT"
ZERO_EXTRAS = "ZERO_MULTI_MODEL_EXTRAS"
DAY0_REQUIRED = "DAY0_OBSERVED_EXTREME_REQUIRED"
DAY0_MISSING_INPUT_REVISION = "original_day0_missing_observation_v1"
EXTRAS_SELECTION_REVISION = "current_capture_extra_selection_v1"
# reason -> required item kinds, each exactly once. DB-dependent kinds start
# with CLOCK; the original missing-input predicate depends only on its request.
SUPPORTED: dict[str, tuple[str, ...]] = {
    STALE_CYCLE: (CLOCK,),
    CERT_REGRESSION: (CLOCK, CERT_REGRESSION),
    CERT_SUPERSEDED: (CLOCK, CERT_SUPERSEDED),
    NO_COHERENT_COHORT: (CLOCK, NO_COHERENT_COHORT),
    ZERO_EXTRAS: (CLOCK, ZERO_EXTRAS),
    DAY0_REQUIRED: (DAY0_REQUIRED,),
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


def current_low_incumbent_basis(conn, posterior_id: int) -> dict[str, object] | None:
    """A current dataset proves the retired-LOW yield precondition false.

    This positive, exact-row proof covers only current incumbents. A retired,
    missing or unreadable baseline remains unbound; do not duplicate or weaken
    the migration's snapshot/coverage/current-ENS predicate.
    """
    from src.data.replacement_forecast_source_run_identity import expected_replacement_dependency_identity_by_role

    row = conn.execute(
        "SELECT dependency_source_run_ids_json FROM forecast_posteriors WHERE posterior_id=?",
        (posterior_id,),
    ).fetchone()
    if row is None:
        return None
    dependencies = json.loads(str(row[0]))
    run_id = dependencies.get("baseline_b0") if isinstance(dependencies, Mapping) else None
    if not isinstance(run_id, str) or not run_id:
        return None
    run = conn.execute("SELECT source_id,dataset_id FROM source_run WHERE source_run_id=?", (run_id,)).fetchone()
    expected = expected_replacement_dependency_identity_by_role("low")["baseline_b0"]
    if run is None or run[0] != "ecmwf_open_data" or run[1] != expected.data_version:
        return None
    return {"dependencies_json": row[0], "baseline_source_run_id": run_id,
            "source_id": run[0], "dataset_id": run[1]}


def no_cohort_item(*, window_hours: float, decision_time_iso: str,
                   day0_remaining_from_iso: str | None = None) -> dict[str, object]:
    item = {
        "kind": NO_COHERENT_COHORT,
        "window_hours": float(window_hours),
        "decision_time_iso": decision_time_iso,
    }
    if day0_remaining_from_iso is not None:
        # The Day0 tau the selector read; absent, the item is exactly as before.
        item["day0_remaining_from_iso"] = day0_remaining_from_iso
    return item


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
        "items": (list(items) if reason == DAY0_REQUIRED else [clock_item(conn, request), *items]),
    }


def day0_missing_input_item(request) -> dict[str, object] | None:
    """The original prewrite predicate on missing request fields, never physical absence.

    Only literal missing inputs are covered. Invalid values/declarations remain
    unbound, as do dynamic requests altered by possession/frontier materialization.
    """
    from src.data.replacement_forecast_materializer import (
        _day0_observed_extreme_c, _target_local_day_has_started,
    )

    if (
        request.day0_observed_extreme_c is not None
        or request.day0_observation_state not in (None, "")
        or _day0_observed_extreme_c(request) is not None
        or not _target_local_day_has_started(request)
    ):
        return None
    return {
        "kind": DAY0_REQUIRED,
        "predicate_revision": DAY0_MISSING_INPUT_REVISION,
        "city_timezone": request.city_timezone,
        "computed_at": _utc(request.computed_at, "computed_at").isoformat(),
        "day0_observed_extreme_c": None,
        "day0_observation_state": None,
    }


def _day0_missing_input_holds(evidence, exact_request) -> bool:
    from types import SimpleNamespace

    if not isinstance(exact_request, Mapping):
        return False
    scope = evidence.get("scope")
    if (
        not isinstance(scope, Mapping)
        or scope.get("temperature_metric") not in ("high", "low")
        or any(not scope.get(field) or str(scope[field]) != str(exact_request.get(field) or "")
               for field in _SCOPE_FIELDS)
    ):
        return False
    request = SimpleNamespace(**{
        field: exact_request.get(field) for field in (
            "city_timezone", "computed_at", "target_date",
            "day0_observed_extreme_c", "day0_observation_state",
        )
    })
    current = day0_missing_input_item(request)
    return current is not None and current == evidence["items"][0]


def zero_extras_item(
    *, city, metric, target_date, source_cycle_time_iso, decision_time_iso,
    day0_remaining_from_iso, latitude, longitude, timezone_name, lead_days,
    scheme, served,
) -> dict[str, object] | None:
    """Record the actual current-capture predicate, never an empty-cohort proxy.

    Empty serving has a different producer refusal. No usable schema/physical
    serving record, injected fetch or interrupted read can prove this reason.
    """
    import hashlib
    from dataclasses import asdict
    from src.contracts.availability_time import proof_of_possession_available_at
    from src.data.bayes_precision_fusion_capture import select_current_extra_models
    from src.forecast.model_selection import GLOBAL_LIKELIHOOD_MODELS, REGIONAL_MODELS, POLYGON_CONFIG_PATH

    if not served:
        return None
    configured = () if scheme is None else tuple(str(model) for model in scheme.weights)
    availability = {}
    for model, value in served.items():
        if value.captured_at:
            try:
                availability[model] = proof_of_possession_available_at(value.captured_at)
            except (TypeError, ValueError):
                pass
    present, dropped, selection = select_current_extra_models(
        values={model: value.value_c for model, value in served.items()},
        latitude=latitude, longitude=longitude, lead_days=lead_days,
        decision_utc=_utc(decision_time_iso, "decision_time_iso"),
        model_available_at=availability, configured=configured,
    )
    if selection.likelihood_globals or selection.regional_experts:
        return None
    item = json.loads(json.dumps({
        "kind": ZERO_EXTRAS, "selection_revision": EXTRAS_SELECTION_REVISION,
        "source_cycle_time_iso": source_cycle_time_iso,
        "decision_time_iso": decision_time_iso,
        "day0_remaining_from_iso": day0_remaining_from_iso,
        "lead_days": lead_days,
        "configuration": {
            "latitude": latitude, "longitude": longitude, "timezone_name": timezone_name,
            "scheme_weights": None if scheme is None else dict(scheme.weights),
            "candidates": [*GLOBAL_LIKELIHOOD_MODELS, *REGIONAL_MODELS],
            "polygon_sha256": hashlib.sha256(POLYGON_CONFIG_PATH.read_bytes()).hexdigest(),
        },
        "served": {model: value.as_provenance() for model, value in served.items()},
        "present_values": present, "dropped_models": dropped,
        "selection": asdict(selection),
    }, allow_nan=False))
    item["selection_identity_hash"] = hashlib.sha256(
        json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return item


def _current_zero_extras_item(conn, scope, *, source_cycle_time_iso, decision_time_iso,
                             day0_remaining_from_iso):
    from datetime import date
    from src.config import runtime_cities_by_name
    from src.data.replacement_forecast_materializer import (
        _bayes_precision_fusion_city_local_lead_days, _read_current_capture_serving,
        _resolve_source_clock_scheme,
    )

    city = runtime_cities_by_name().get(str(scope["city"]))
    if city is None:
        return None
    metric = str(scope["temperature_metric"])
    scheme = _resolve_source_clock_scheme(str(scope["city"]), metric)
    configured = () if scheme is None else tuple(str(model) for model in scheme.weights)
    tz_name = str(city.timezone)
    lead_days = _bayes_precision_fusion_city_local_lead_days(
        computed_at=_utc(decision_time_iso, "decision_time_iso"),
        target_local_date=date.fromisoformat(str(scope["target_date"])), tz_name=tz_name,
    )
    served = _read_current_capture_serving(
        conn, city=str(scope["city"]), metric=metric, target_date=str(scope["target_date"]),
        source_cycle_time_iso=source_cycle_time_iso, decision_time_iso=decision_time_iso,
        day0_remaining_from_iso=day0_remaining_from_iso, lat=float(city.lat),
        lon=float(city.lon), lead_days=lead_days, configured=configured,
    )
    return zero_extras_item(
        city=str(scope["city"]), metric=metric, target_date=str(scope["target_date"]),
        source_cycle_time_iso=source_cycle_time_iso, decision_time_iso=decision_time_iso,
        day0_remaining_from_iso=day0_remaining_from_iso, latitude=float(city.lat),
        longitude=float(city.lon), timezone_name=tz_name, lead_days=lead_days,
        scheme=scheme, served=served,
    )


# ------------------------------------------------------------- verification


def _well_formed(evidence: object, prospective: Mapping[str, object] | None) -> bool:
    """The discriminated schema: a supported reason, exactly its required items,
    DB-dependent kinds have a clock naming exactly the two roles; a prospective
    request must match their scope/roles. Original missing-input proof is exact only."""
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
    if reason == DAY0_REQUIRED:
        # SCOPE: only the exact failed original request and its consumed SHA.
        # DRAIN: normal terminal queue move releases its owner. RESET: every
        # prospective request is independently admitted, including new real
        # observations and existing typed zero input; this proof never fences it.
        return prospective is None
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
    if reason == CERT_SUPERSEDED:
        return False  # exact failed request only, never a prospective family fence
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


def _cohort_empty(conn, scope: Mapping[str, object], at: datetime, window_hours: float,
                  day0_remaining_from_iso: str | None = None) -> bool:
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
        day0_remaining_from_iso=day0_remaining_from_iso,
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
    if evidence["reason"] == CERT_SUPERSEDED:
        incumbent = _incumbent_key(conn, item)
        incoming = tuple(_utc(value, "incoming_key") for value in item["incoming_key"])
        if incumbent is None or tuple(value.isoformat() for value in incumbent) != tuple(item["incumbent_key"]):
            return False
        scope = evidence["scope"]
        for posterior_id, expected_key in ((item["incumbent_posterior_id"], incumbent),
                                            (item["incoming_posterior_id"], incoming)):
            row = conn.execute(
                "SELECT source_cycle_time,computed_at,source_id,runtime_layer,city,target_date,temperature_metric"
                " FROM forecast_posteriors WHERE posterior_id=?", (posterior_id,),
            ).fetchone()
            if row is None or tuple(row[2:]) != ("openmeteo_ecmwf_ifs9_bayes_fusion", "live",
                    scope["city"], scope["target_date"], scope["temperature_metric"]):
                return False
            if (_utc(row[0], "source_cycle_time"), _utc(row[1], "computed_at")) != expected_key:
                return False
        if scope["temperature_metric"] == "low":
            basis = current_low_incumbent_basis(conn, int(item["incumbent_posterior_id"]))
            if basis is None or basis != item.get("current_low_incumbent_basis"):
                return False
        elif scope["temperature_metric"] != "high":
            return False
        return _serving_key_strictly_newer(incumbent, incoming)
    if evidence["reason"] == CERT_REGRESSION:
        incumbent = _incumbent_key(conn, item)
        incoming = tuple(_utc(value, "incoming_key") for value in item["incoming_key"])
        return incumbent is not None and _serving_key_strictly_newer(incumbent, incoming)
    if evidence["reason"] == NO_COHERENT_COHORT:
        return _cohort_empty(
            conn, evidence["scope"], _utc(item["decision_time_iso"], "decision_time_iso"),
            item["window_hours"], item.get("day0_remaining_from_iso"),
        )
    if evidence["reason"] == ZERO_EXTRAS:
        return _current_zero_extras_item(
            conn, evidence["scope"], source_cycle_time_iso=item["source_cycle_time_iso"],
            decision_time_iso=item["decision_time_iso"],
            day0_remaining_from_iso=item["day0_remaining_from_iso"],
        ) == dict(item)
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
    from src.data.forecast_target_contract import day0_remaining_from_iso_of  # noqa: PLC0415

    if evidence["reason"] == ZERO_EXTRAS:
        # First bind the complete recorded predicate. A changed config/proof
        # reopens even when the new predicate also happens to select zero extras.
        if not _recorded_facts_hold(conn, evidence):
            return False
        current = _current_zero_extras_item(
            conn, payload, source_cycle_time_iso=_utc(payload["source_cycle_time"], "source_cycle_time").isoformat(),
            decision_time_iso=effective.isoformat(),
            day0_remaining_from_iso=day0_remaining_from_iso_of(payload.get("day0_observed_extreme_observation_time")),
        )
        return current is not None and current["configuration"] == item["configuration"]
    return _cohort_empty(conn, payload, effective, item["window_hours"],
        day0_remaining_from_iso_of(payload.get("day0_observed_extreme_observation_time")))


def evidence_holds(
    conn: sqlite3.Connection,
    evidence: object,
    prospective: Mapping[str, object] | None = None,
    *,
    exact_request: Mapping[str, object] | None = None,
) -> bool:
    """See the module doc. Malformed, unsupported, unbound or unreadable never holds."""
    if not _well_formed(evidence, prospective):
        return False
    if evidence["reason"] == DAY0_REQUIRED:
        try:
            return _day0_missing_input_holds(evidence, exact_request)
        except (KeyError, TypeError, ValueError, AttributeError, RuntimeError):
            return False
    owns = not conn.in_transaction
    try:
        if owns:
            conn.execute("BEGIN")
        if evidence["reason"] == CERT_SUPERSEDED:
            if exact_request is None or prospective is not None:
                return False
            if any(str(evidence["scope"].get(field) or "") != str(exact_request.get(field) or "")
                   for field in _SCOPE_FIELDS):
                return False
            if any(run.get("source_run_id") != (exact_request.get(_ROLES[run["role"]][0]) or None)
                   for run in evidence["items"][0]["source_runs"]):
                return False
            request_key = (_utc(exact_request["source_cycle_time"], "source_cycle_time"),
                           effective_computed_at(conn, exact_request))
            incoming = tuple(_utc(value, "incoming_key") for value in evidence["items"][-1]["incoming_key"])
            return request_key == incoming and _recorded_facts_hold(conn, evidence)
        if prospective is None:
            if evidence["reason"] == ZERO_EXTRAS:
                if exact_request is None or not _well_formed(evidence, exact_request):
                    return False
                item = evidence["items"][-1]
                from src.data.forecast_target_contract import day0_remaining_from_iso_of
                if (
                    _utc(exact_request["source_cycle_time"], "source_cycle_time").isoformat() != item["source_cycle_time_iso"]
                    or effective_computed_at(conn, exact_request).isoformat() != item["decision_time_iso"]
                    or day0_remaining_from_iso_of(exact_request.get("day0_observed_extreme_observation_time")) != item["day0_remaining_from_iso"]
                ):
                    return False
            return _recorded_facts_hold(conn, evidence)
        return _prospective_blocks(conn, evidence, prospective)
    except (EvidenceUnavailable, sqlite3.Error, OSError, KeyError, TypeError, ValueError, AttributeError, RuntimeError):
        return False
    finally:
        if owns and conn.in_transaction:
            conn.rollback()
