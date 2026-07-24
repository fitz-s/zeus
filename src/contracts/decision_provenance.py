# Created: 2026-06-11
# Last reused or audited: 2026-06-11 (row_factory isolation fix, Task #42)
# Authority basis: docs/evidence/settlement_guard/2026-06-11_decision_provenance_plan.md
#   — OPERATOR LAW 2026-06-11 ~13:20Z (verbatim): "我要每一个下单决定receipt都有来自那些
#   数据组合，距离发布多久，距离结算多久等等所有的详细数据全部被记录，每一个被拒绝的具体原因
#   都要写出来，每一个做的决策为什么都需要被查阅。我需要一切可被溯源" — every order-decision
#   receipt (ACCEPTED and REJECTED, every stage) carries a complete, queryable provenance envelope.
"""ONE decision-provenance envelope, assembled at decision time from truths that ALREADY exist.

`build_decision_provenance_envelope` is PURE assembly: it READS the served forecast bundle, the
two canonical connections (forecast / trade, read-only), and the executable-market snapshot row,
and returns a single dict that answers the operator's four questions for ANY decision receipt:

  1. WHICH DATA COMBINATION produced it  -> posterior_id, q_mode, fusion_instruments,
     anchor_transport, dependency_source_run_ids.
  2. HOW OLD is every input at decision time -> per_input_ages {cycle_age_h, available_age_h,
     capture_age_h}, posterior_computed_age_h, staleness_violations.
  3. HOW LONG until settlement -> time_to_settlement {local_day_end_utc, hours_to_local_day_end,
     market_end_at, hours_to_market_end}.
  4. WHY this decision (economics + book + the FULL rejection reason) -> economics, book,
     direction, rejection.

CONTRACT (plan §invariants):
  - NO network. Reads only the passed-in connections / objects.
  - Fail-soft PER FIELD: a missing or unparseable sub-truth records {"<key>": "UNAVAILABLE: <why>"}
    and NEVER raises — a provenance gap must never crash or alter a decision.
  - Observability ONLY: this envelope is attached to receipts; it is never read back into a gate.
    The money path is byte-identical whether or not it is built.
  - Full reason: rejection.reason is stored verbatim (no truncation). Storage never truncates.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Mapping

UTC = timezone.utc

_logger = logging.getLogger("zeus.contracts.decision_provenance")

# The settlement-fee model is fixed across the engine (0.05 * p * (1-p) * shares). Recorded as a
# string label so a queried receipt is self-describing without re-deriving the formula.
FEE_MODEL = "0.05*p*(1-p)*shares"


def _unavailable(why: str) -> str:
    return f"UNAVAILABLE: {why}"


def _parse_utc(value: Any) -> datetime | None:
    """Parse an ISO timestamp to aware-UTC, or None (fail-soft — never raises)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    return dt.astimezone(UTC)


def _age_hours(reference: datetime, value: Any) -> Any:
    parsed = _parse_utc(value)
    if parsed is None:
        return _unavailable(f"unparseable timestamp {value!r}")
    return round((reference - parsed).total_seconds() / 3600.0, 4)


def _age_seconds(reference: datetime, value: Any) -> Any:
    parsed = _parse_utc(value)
    if parsed is None:
        return _unavailable(f"unparseable timestamp {value!r}")
    return round((reference - parsed).total_seconds(), 2)


def _hours_until(reference: datetime, value: Any) -> Any:
    parsed = _parse_utc(value)
    if parsed is None:
        return _unavailable(f"unparseable timestamp {value!r}")
    return round((parsed - reference).total_seconds() / 3600.0, 4)


def _json_obj(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if value is None or value == "":
        return {}
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _row_get(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        try:
            return row[key]
        except (IndexError, KeyError):
            return None
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _opt_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _city_timezone(city: str | None) -> str | None:
    if not city:
        return None
    try:
        from src.config import cities_by_name  # noqa: PLC0415

        city_cfg = cities_by_name.get(city)
        tz = getattr(city_cfg, "timezone", None) if city_cfg is not None else None
        return tz or None
    except Exception:  # noqa: BLE001 — provenance assembly is fail-soft
        return None


def _fusion_instruments(provenance: Mapping[str, Any]) -> Any:
    """The F4 fusion selection (used / dropped / excluded), straight from the posterior prov."""
    fusion = provenance.get("bayes_precision_fusion")
    if not isinstance(fusion, Mapping):
        return _unavailable("provenance.bayes_precision_fusion absent (non-fused / capture-missing posterior)")
    return {
        "method": fusion.get("method"),
        "used_models": list(fusion.get("used_models") or []),
        "dropped_models": list(fusion.get("dropped_models") or []),
        "excluded_regionals": list(fusion.get("excluded_regionals") or []),
        "dropped_aliases": list(fusion.get("dropped_aliases") or []),
        "raw_model_forecast_ids": list(fusion.get("raw_model_forecast_ids") or []),
        "lead_bucket": fusion.get("lead_bucket"),
        "anchor_bridge": fusion.get("anchor_bridge"),
        "predictive_sigma_c": fusion.get("predictive_sigma_c"),
        "anchor_value_c": fusion.get("anchor_value_c"),
        "anchor_sigma_c": fusion.get("anchor_sigma_c"),
        "decorrelated_providers_served": fusion.get("decorrelated_providers_served"),
        "decorrelated_providers_expected": fusion.get("decorrelated_providers_expected"),
        "decorrelated_providers_complete": fusion.get("decorrelated_providers_complete"),
    }


def _anchor_transport(
    forecast_conn: sqlite3.Connection | None,
    dependency_source_run_ids: Mapping[str, Any],
) -> Any:
    """Anchor-transport run_authority from the anchor artifact's metadata.

    Locates the anchor's raw_forecast_artifacts row(s) by the dependency source_run_id and reports
    each input's run_authority + endpoint. Read-only; fail-soft if the table / column is absent.
    """
    if forecast_conn is None:
        return _unavailable("forecast_conn not provided")
    if not dependency_source_run_ids:
        return _unavailable("dependency_source_run_ids absent")
    transports: dict[str, Any] = {}
    # Use a cursor-local row_factory (Python 3.14 sqlite3) so the shared connection's
    # row_factory is never mutated — eliminates the save/restore concurrency footgun.
    cur = forecast_conn.cursor()
    cur.row_factory = sqlite3.Row
    for role, source_run_id in dependency_source_run_ids.items():
        if not source_run_id:
            continue
        try:
            cur.execute(
                """
                SELECT source_id, artifact_metadata_json
                FROM raw_forecast_artifacts
                WHERE json_extract(artifact_metadata_json, '$.source_run_id') = ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (str(source_run_id),),
            )
            rows = cur.fetchall()
        except sqlite3.Error as exc:
            transports[role] = _unavailable(f"artifact query failed: {exc}")
            continue
        if not rows:
            transports[role] = _unavailable(f"no raw_forecast_artifacts for run {source_run_id}")
            continue
        md = _json_obj(_row_get(rows[0], "artifact_metadata_json"))
        transports[role] = {
            "source_id": _row_get(rows[0], "source_id"),
            "run_authority": md.get("run_authority"),
            "openmeteo_endpoint": md.get("openmeteo_endpoint"),
        }
    return transports or _unavailable("no anchor artifacts matched any dependency run id")


def _per_input_ages(
    decision_utc: datetime,
    forecast_conn: sqlite3.Connection | None,
    dependency_source_run_ids: Mapping[str, Any],
    bundle_cycle_time: Any,
    bundle_available_at: Any,
) -> Any:
    """age_since_cycle / age_since_available / age_since_capture for every named input.

    Per-input rows come from raw_forecast_artifacts (each input's own cycle/available/capture
    timestamps). The bundle's own source_cycle_time / source_available_at are always recorded as
    the `posterior` row so the ages exist even when the artifact table cannot be reached.
    """
    ages: dict[str, Any] = {
        "posterior": {
            "cycle_age_h": _age_hours(decision_utc, bundle_cycle_time),
            "available_age_h": _age_hours(decision_utc, bundle_available_at),
            "capture_age_h": _unavailable("posterior has no distinct capture timestamp"),
        }
    }
    if forecast_conn is None or not dependency_source_run_ids:
        return ages
    # Use a cursor-local row_factory (Python 3.14 sqlite3) so the shared connection's
    # row_factory is never mutated — eliminates the save/restore concurrency footgun.
    cur = forecast_conn.cursor()
    cur.row_factory = sqlite3.Row
    for role, source_run_id in dependency_source_run_ids.items():
        if not source_run_id:
            continue
        try:
            cur.execute(
                """
                SELECT source_cycle_time, source_available_at, captured_at
                FROM raw_forecast_artifacts
                WHERE json_extract(artifact_metadata_json, '$.source_run_id') = ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (str(source_run_id),),
            )
            row = cur.fetchone()
        except sqlite3.Error as exc:
            ages[role] = _unavailable(f"artifact query failed: {exc}")
            continue
        if row is None:
            ages[role] = _unavailable(f"no raw_forecast_artifacts for run {source_run_id}")
            continue
        ages[role] = {
            "cycle_age_h": _age_hours(decision_utc, _row_get(row, "source_cycle_time")),
            "available_age_h": _age_hours(decision_utc, _row_get(row, "source_available_at")),
            "capture_age_h": _age_hours(decision_utc, _row_get(row, "captured_at")),
        }
    return ages


def _time_to_settlement(
    decision_utc: datetime,
    *,
    city: str | None,
    target_date: Any,
    executable_snapshot_row: Any,
) -> Mapping[str, Any]:
    """local_day_end_utc + hours_to_local_day_end (calendar geometry) and market_end_at deltas."""
    out: dict[str, Any] = {}
    city_tz = _city_timezone(city)
    if city_tz is None:
        out["local_day_end_utc"] = _unavailable(f"no timezone for city {city!r}")
        out["hours_to_local_day_end"] = _unavailable("local_day_end unresolved")
    else:
        try:
            from src.strategy.market_phase import settlement_day_entry_utc  # noqa: PLC0415

            td = target_date if isinstance(target_date, date) else date.fromisoformat(str(target_date))
            # Local day END = the SETTLEMENT_DAY entry of the FOLLOWING local date (local midnight
            # of target_date + 1), which equals the end of target_date's local calendar day.
            from datetime import timedelta  # noqa: PLC0415

            local_day_end = settlement_day_entry_utc(
                target_local_date=td + timedelta(days=1),
                city_timezone=city_tz,
            )
            out["local_day_end_utc"] = local_day_end.isoformat()
            out["hours_to_local_day_end"] = round(
                (local_day_end - decision_utc).total_seconds() / 3600.0, 4
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft per field
            out["local_day_end_utc"] = _unavailable(f"geometry failed: {exc}")
            out["hours_to_local_day_end"] = _unavailable("local_day_end unresolved")
    market_end_at = _row_get(executable_snapshot_row, "market_end_at")
    if market_end_at is None:
        out["market_end_at"] = _unavailable("snapshot.market_end_at absent")
        out["hours_to_market_end"] = _unavailable("market_end_at absent")
    else:
        out["market_end_at"] = str(market_end_at)
        out["hours_to_market_end"] = _hours_until(decision_utc, market_end_at)
    return out


def _book(decision_utc: datetime, executable_snapshot_row: Any) -> Mapping[str, Any]:
    if executable_snapshot_row is None:
        return {"status": _unavailable("no executable_snapshot_row provided")}
    captured_at = _row_get(executable_snapshot_row, "captured_at")
    return {
        "snapshot_id": _row_get(executable_snapshot_row, "snapshot_id"),
        "captured_at": str(captured_at) if captured_at is not None else _unavailable("captured_at absent"),
        "best_bid": _opt_float(_row_get(executable_snapshot_row, "orderbook_top_bid")),
        "best_ask": _opt_float(_row_get(executable_snapshot_row, "orderbook_top_ask")),
        "age_s": _age_seconds(decision_utc, captured_at) if captured_at is not None else _unavailable("captured_at absent"),
    }


def _economics(economics: Mapping[str, Any] | None) -> Mapping[str, Any]:
    economics = economics or {}
    q_live = _opt_float(economics.get("q_live"))
    price = _opt_float(economics.get("price"))
    if price is None:
        price = _opt_float(economics.get("c_fee_adjusted"))
    edge = economics.get("edge")
    if edge is None and q_live is not None and price is not None:
        edge = round(q_live - price, 6)
    return {
        "q_live": q_live,
        "q_lcb": _opt_float(economics.get("q_lcb", economics.get("q_lcb_5pct"))),
        "price": price,
        "fee_model": FEE_MODEL,
        "edge": edge,
        "trade_score": _opt_float(economics.get("trade_score")),
        "kelly_size_usd": _opt_float(economics.get("kelly_size_usd")),
    }


def build_decision_provenance_envelope(
    forecast_conn: sqlite3.Connection | None,
    trade_conn: sqlite3.Connection | None,
    *,
    bundle: Any | None,
    decision_time: datetime | str,
    condition_id: str | None = None,
    token_id: str | None = None,
    executable_snapshot_row: Any | None = None,
    economics: Mapping[str, Any] | None = None,
    direction: str | None = None,
    direction_law_verdict: str | None = None,
    rejection: Mapping[str, Any] | None = None,
    city: str | None = None,
    target_date: Any | None = None,
) -> dict[str, Any]:
    """Assemble the complete decision-provenance envelope (pure, fail-soft, no network).

    `bundle` is a ReplacementForecastPosteriorBundle (or any object exposing posterior_id /
    provenance_json / dependency_json / source_cycle_time / source_available_at / computed_at).
    `executable_snapshot_row` is an executable_market_snapshots row (sqlite3.Row / Mapping).
    `economics` carries the decision economics (q_live / q_lcb / price / trade_score / kelly_size_usd).
    `rejection` carries {"stage": ..., "reason": <FULL text, never truncated>} for REJECTED receipts;
    None / absent for ACCEPTED receipts.

    Returns a dict. NEVER raises: any sub-truth that cannot be assembled becomes an
    "UNAVAILABLE: <why>" string in its slot.
    """
    decision_utc = _parse_utc(decision_time)
    if decision_utc is None:
        # decision_time is the one truly required anchor; without it every age/delta is undefined.
        decision_utc = datetime.now(UTC)
        decision_time_field: Any = _unavailable(f"unparseable decision_time {decision_time!r}; used now()")
    else:
        decision_time_field = decision_utc.isoformat()

    provenance: Mapping[str, Any] = {}
    dependency: Mapping[str, Any] = {}
    posterior_id: Any = _unavailable("bundle not provided")
    q_mode: Any = _unavailable("bundle not provided")
    bundle_cycle_time: Any = None
    bundle_available_at: Any = None
    computed_at: Any = None
    # Explicit city/target_date (passed from the event payload for early-stage rejections, where no
    # bundle exists yet) take precedence; otherwise inherit from the served bundle. Either way the
    # time-to-settlement geometry is computable — an early rejection still carries it (operator law).
    if bundle is not None:
        try:
            provenance = _json_obj(getattr(bundle, "provenance_json", None))
            dependency = _json_obj(getattr(bundle, "dependency_json", None))
            posterior_id = getattr(bundle, "posterior_id", _unavailable("bundle.posterior_id absent"))
            q_mode = provenance.get("replacement_q_mode") or _unavailable("provenance.replacement_q_mode absent")
            bundle_cycle_time = getattr(bundle, "source_cycle_time", None)
            bundle_available_at = getattr(bundle, "source_available_at", None)
            computed_at = getattr(bundle, "computed_at", None)
            if city is None:
                city = getattr(bundle, "city", None)
            if target_date is None:
                target_date = getattr(bundle, "target_date", None)
        except Exception as exc:  # noqa: BLE001 — fail-soft
            _logger.warning("decision_provenance: bundle read failed (fail-soft): %s", exc)

    rejection_field: Any
    if rejection is None:
        rejection_field = None  # ACCEPTED receipt — no rejection
    else:
        # FULL TEXT — never truncated at the storage layer (operator law).
        rejection_field = {
            "stage": rejection.get("stage"),
            "reason": rejection.get("reason"),
        }

    envelope: dict[str, Any] = {
        "envelope_version": 1,
        "decision_time": decision_time_field,
        "condition_id": condition_id,
        "token_id": token_id,
        "posterior_id": posterior_id,
        "q_mode": q_mode,
        "data_version": getattr(bundle, "data_version", None) if bundle is not None else None,
        "fusion_instruments": _fusion_instruments(provenance) if provenance else _unavailable("no posterior provenance"),
        "anchor_transport": _anchor_transport(forecast_conn, dependency),
        "dependency_source_run_ids": dict(dependency) if dependency else _unavailable("bundle.dependency_json absent"),
        "per_input_ages": _per_input_ages(
            decision_utc, forecast_conn, dependency, bundle_cycle_time, bundle_available_at
        ),
        "staleness_violations": list(provenance.get("staleness_violations") or [])
        if isinstance(provenance.get("staleness_violations"), list)
        else [],
        "posterior_computed_age_h": _age_hours(decision_utc, computed_at)
        if computed_at is not None
        else _unavailable("bundle.computed_at absent"),
        "time_to_settlement": _time_to_settlement(
            decision_utc, city=city, target_date=target_date, executable_snapshot_row=executable_snapshot_row
        ),
        "book": _book(decision_utc, executable_snapshot_row),
        "economics": _economics(economics),
        "direction": direction,
        "direction_law_verdict": direction_law_verdict,
        "rejection": rejection_field,
    }
    return envelope


def envelope_to_json(envelope: Mapping[str, Any]) -> str:
    """Canonical JSON for storage (sorted keys, compact). Storage never truncates."""
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), default=str)


def pretty_envelope(envelope: Mapping[str, Any]) -> str:
    """Human-readable, colon-free-safe multi-line render for the walker / query script."""
    return json.dumps(envelope, indent=2, sort_keys=True, default=str)
