# Created: 2026-06-11
# Last reused or audited: 2026-08-01  (typed SQLite read-unavailable hotfix)
# Authority basis: Task #32 follow-up (operator 2026-06-11) — generalize the gem_global
#   previous_runs exception (edc598b440 / K2 2026-06-09) into the operator law 没有新的就用老的
#   applied to fusion membership: a provider absent from single_runs at the selected cycle serves
#   its previous_runs row at the SAME natural key instead of being dropped. Live evidence: JMA
#   publishes 00/12Z only, so at every 06Z-cadence cycle jma_seamless can NEVER appear in
#   single_runs (06Z: 0/49 cities) while its previous_runs leg is complete (49/49) — the fusion
#   ran served=4/5 and the whole city lost its conservative edge (Beijing 06-12: max q_lcb 0.068).
"""SINGLE-AUTHORITY current-value serving for the replacement multi-model fusion.

``read_current_instrument_values`` is the ONE function that decides, per provider, whether its
CURRENT value for a (city, metric, target_date, selected source_cycle_time) scope is served from
its ``single_runs`` row (the forward live capture — always preferred) or from the newest
persisted ``previous_runs`` row. Carrier-bound callers use rows no later than the selected cycle;
the source-clock live route instead uses each provider's newest row possessed by decision time.
Both the materializer's q path
(``_read_persisted_current_capture`` is a thin shape-adapter over this function) and the
fusion-upgrade trigger's capturable-set computation call it, so "what can be fused" can never
drift between the two sites (single-builder; registry member #10).

THE GENERALIZED RULE (supersedes the gem-only exception, which becomes one instance of it):

  1. On carrier-bound calls, a model's single_runs row at the selected cycle ALWAYS wins.
  2. A model with NO single_runs row at the selected cycle may be served from the newest
     persisted row for the same model/city/metric/target_date whose ``source_cycle_time`` is not
     after the selected cycle, BRANDED by its real ``served_via`` and ``served_cycle`` — never
     silently. The
     substituted value is the SAME physical product the model's walk-forward de-bias history is
     fit on (previous_runs at this lead), so the de-bias and the lead-bucket residual variance
     already price the older run honestly: NO manual down-weighting exists anywhere — a
     substituted instrument's precision weight derives from its own lead-bucket history exactly
     like a forward-captured one.
  3. A model absent from BOTH endpoints at or before the selected cycle stays dropped.
  4. On the source-clock live route, the decision instant replaces the carrier as the
     deterministic-provider ceiling: each provider serves its newest possessed run, while the
     carrier continues to govern ENS shape.

K-DECISION on the eligibility guard (task constraint 3, judged + documented): the substitution
does NOT try to distinguish "structurally unpublished at this cycle" (JMA at 06Z) from
"transient mid-capture failure at a cycle the provider normally publishes" (gfs HTTP 400 at
00Z). Building that distinction would require a per-provider publication-cadence table — a new
guessed-constant authority of exactly the class the 2026-06-11 run-selection rework killed.
Instead the freshness horizon admits both: a carrier-bound row must not be newer than the
selected cycle, and a source-clock row must have been possessed by the decision instant.  The
selected-cycle row wins when present; otherwise the newest eligible prior cycle may serve, and
its capture must be recent relative to its own served cycle
(``PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS``). A transiently-failed provider is therefore
served from its freshest eligible possessed run too — 没有新的就用老的: serving the one-run-older value
of the SAME de-biased product beats dropping the instrument and inflating sigma, and the honest
``served_via`` provenance + the lead-bucketed residual variance carry the cost. The horizon is
belt-and-suspenders against anomalous stale-keyed rows (e.g. a backfill captured a day after
its cycle); every live capture lands within hours of its cycle.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime

# Freshness horizon for a previous_runs substitution: the row's captured_at may be at most this
# many hours after its served source_cycle_time. Live extras captures land 0-9h after the cycle
# (e.g. Beijing 06Z captured 14:06Z = 8.1h); anything beyond 24h is an anomalous stale-keyed row,
# not a live capture, and is rejected. Cycles themselves are bounded at 30h by
# replacement_source_cycle_max_age_hours, so 24h post-cycle capture recency is strictly tighter.
PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS = 24.0

SERVED_VIA_SINGLE_RUNS = "single_runs"
SERVED_VIA_PREVIOUS_RUNS = "previous_runs"


class CurrentValueServingReadUnavailable(sqlite3.OperationalError):
    """The current-value SQLite read was interrupted or otherwise unavailable."""


def _is_transient_sqlite_read_error(exc: sqlite3.OperationalError) -> bool:
    transient_codes = {
        code
        for code in (
            getattr(sqlite3, "SQLITE_BUSY", None),
            getattr(sqlite3, "SQLITE_LOCKED", None),
            getattr(sqlite3, "SQLITE_INTERRUPT", None),
        )
        if isinstance(code, int)
    }
    error_code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(error_code, int) and (error_code & 0xFF) in transient_codes:
        return True
    if getattr(exc, "sqlite_errorname", None) in {
        "SQLITE_BUSY",
        "SQLITE_LOCKED",
        "SQLITE_INTERRUPT",
    }:
        return True

    message = str(exc).strip().lower()
    if message in {
        "interrupted",
        "database is locked",
        "database table is locked",
        "database schema is locked",
        "database is busy",
        "sqlite_read_deadline_exceeded",
        "sqlite_read_cancelled",
        "sqlite_read_canceled",
    }:
        return True
    return any(
        message.startswith(prefix) and bool(message.removeprefix(prefix).strip())
        for prefix in (
            "database table is locked:",
            "database schema is locked:",
        )
    )


def _raise_typed_read_unavailable(exc: sqlite3.OperationalError) -> None:
    if _is_transient_sqlite_read_error(exc):
        raise CurrentValueServingReadUnavailable(str(exc)) from exc
    raise exc


def _parse_forecast_value_and_lead(
    forecast_value: object,
    lead_days: object,
) -> tuple[float, int | None] | None:
    """Apply the one row-validity rule shared by serving and frontier witnesses."""

    if forecast_value is None:
        return None
    try:
        value = float(forecast_value)
        if not math.isfinite(value):
            return None
        lead = None if lead_days is None else int(lead_days)
    except (TypeError, ValueError, OverflowError):
        return None
    return value, lead


# 删了0.25 (2026-07-01): a model whose previous_runs product is a DIFFERENT (coarser) physical product
# than its live single_runs — NOT just an older run of the same product. ECMWF's OM previous-runs feed
# serves ecmwf_ifs025 (0.25° grid) while single_runs serves ecmwf_ifs (9km). The substitution law
# (没有新的就用老的) is correct for same-product models (an older run of the SAME product) but WRONG here:
# substituting ifs025 injects a coarse-grid representativeness artifact into the served center (measured
# ifs025↔ifs9 per-city gap sd 1.52C, e.g. Jeddah +2.2C; Jeddah's whole apparent −1.44 bias was this
# artifact — +0.08 on ifs9). So when the fresh 9km value is missing, DROP the model (the scheme
# renormalizes over present sources) rather than serve the 0.25° coarse product.
_PRODUCT_MISMATCHED_PREVIOUS_RUNS = frozenset({"ecmwf_ifs"})


@dataclass(frozen=True)
class ServedInstrumentValue:
    """One instrument's served CURRENT value + the honest serving provenance (brand law)."""

    value_c: float
    raw_model_forecast_id: int
    served_via: str            # SERVED_VIA_SINGLE_RUNS | SERVED_VIA_PREVIOUS_RUNS
    served_cycle: str          # provider cycle; may exceed the ENS/anchor carrier on source-clock
    captured_at: str | None    # the served row's capture timestamp (None on stripped schemas)
    age_hours: float           # captured_at − source_cycle_time, hours (0.0 when unknowable)
    lead_days: int | None      # the served row's lead bucket — the SAME bucket its history uses

    def as_provenance(self) -> dict[str, object]:
        """The per-instrument provenance payload recorded in bayes_precision_fusion.current_value_serving."""
        return {
            "served_via": self.served_via,
            "previous_run_substitution": self.served_via == SERVED_VIA_PREVIOUS_RUNS,
            "raw_model_forecast_id": int(self.raw_model_forecast_id),
            "served_cycle": self.served_cycle,
            "captured_at": self.captured_at,
            "age_hours": round(float(self.age_hours), 3),
            "lead_days": self.lead_days,
        }


@dataclass(frozen=True)
class CurrentValueServingSchema:
    """Schema facts captured before a final writer lock is acquired."""

    has_captured_at: bool
    has_source_available_at: bool


def current_value_serving_schema(
    conn: sqlite3.Connection,
) -> CurrentValueServingSchema:
    """Inspect the provider table outside latency-sensitive writer locks."""

    try:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(raw_model_forecasts)")
        }
    except sqlite3.OperationalError as exc:
        _raise_typed_read_unavailable(exc)
    return CurrentValueServingSchema(
        has_captured_at="captured_at" in columns,
        has_source_available_at="source_available_at" in columns,
    )


def read_current_instrument_family_latest_id(
    conn: sqlite3.Connection,
    *,
    city: str,
    metric: str,
    target_date: str,
) -> int | None:
    """Return the append-only exact-target provider high-water row id."""

    try:
        row = conn.execute(
            """
            SELECT raw_model_forecast_id
              FROM raw_model_forecasts
             WHERE city = ? AND target_date = ? AND metric = ?
             ORDER BY raw_model_forecast_id DESC
             LIMIT 1
            """,
            (city, target_date, metric),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        _raise_typed_read_unavailable(exc)
        raise AssertionError("unreachable")
    return None if row is None else int(row[0])


def _read_source_clock_rows(
    conn: sqlite3.Connection,
    *,
    city: str,
    metric: str,
    target_date: str,
    decision_iso: str,
    schema: CurrentValueServingSchema,
    max_substitution_age_hours: float,
) -> list[sqlite3.Row]:
    """Read the complete production target-family candidate stream."""

    sql, params = _source_clock_rows_query(
        city=city,
        metric=metric,
        target_date=target_date,
        decision_iso=decision_iso,
        schema=schema,
        max_substitution_age_hours=max_substitution_age_hours,
    )
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        _raise_typed_read_unavailable(exc)
        raise AssertionError("unreachable")


def _source_clock_rows_query(
    *,
    city: str,
    metric: str,
    target_date: str,
    decision_iso: str,
    schema: CurrentValueServingSchema,
    max_substitution_age_hours: float,
) -> tuple[str, tuple[object, ...]]:
    """Build the complete production ordering used only before the final lock."""

    captured_select = ", captured_at" if schema.has_captured_at else ""
    possession_predicate = (
        "captured_at IS NOT NULL AND datetime(captured_at) <= datetime(?)"
        if schema.has_captured_at
        else "source_available_at IS NOT NULL "
        "AND datetime(source_available_at) <= datetime(?)"
    )
    source_available_guard = (
        "AND (source_available_at IS NULL "
        "OR datetime(source_available_at) <= datetime(?))"
        if schema.has_source_available_at and schema.has_captured_at
        else ""
    )
    previous_age_guard = ""
    if schema.has_captured_at:
        previous_age_guard = """
          AND (
                endpoint != ?
                OR captured_at IS NULL
                OR julianday(captured_at) IS NULL
                OR julianday(source_cycle_time) IS NULL
                OR (julianday(captured_at) - julianday(source_cycle_time)) * 24.0 <= ?
              )
        """
    order_clause = (
        "captured_at DESC NULLS LAST, raw_model_forecast_id DESC"
        if schema.has_captured_at
        else "raw_model_forecast_id DESC"
    )
    params: list[object] = [city, target_date, metric]
    params.extend((decision_iso, decision_iso))
    if source_available_guard:
        params.append(decision_iso)
    if previous_age_guard:
        params.extend((SERVED_VIA_PREVIOUS_RUNS, max_substitution_age_hours))
    params.extend((SERVED_VIA_SINGLE_RUNS, SERVED_VIA_PREVIOUS_RUNS))
    return (
        f"""
        SELECT raw_model_forecast_id, model, forecast_value_c, lead_days,
               source_cycle_time, endpoint{captured_select}
         FROM raw_model_forecasts
         WHERE city = ? AND target_date = ? AND metric = ?
           AND datetime(source_cycle_time) <= datetime(?)
           AND {possession_predicate}
           {source_available_guard}
           {previous_age_guard}
           AND endpoint IN (?, ?)
         ORDER BY model,
                  datetime(source_cycle_time) DESC,
                  CASE endpoint WHEN 'single_runs' THEN 0 ELSE 1 END,
                  lead_days,
                  {order_clause}
        """,
        tuple(params),
    )


def _served_source_clock_row(
    row: sqlite3.Row | tuple[object, ...],
    *,
    schema: CurrentValueServingSchema,
    max_substitution_age_hours: float,
) -> tuple[str, ServedInstrumentValue] | None:
    """Parse one ordered row with the production serving validity rules."""

    try:
        raw_id = int(row[0])
        model = str(row[1])
        parsed = _parse_forecast_value_and_lead(row[2], row[3])
        if parsed is None:
            return None
        value, lead = parsed
        served_cycle = str(row[4])
        endpoint = str(row[5])
        captured = (
            str(row[6])
            if schema.has_captured_at and row[6] is not None
            else None
        )
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        endpoint == SERVED_VIA_PREVIOUS_RUNS
        and model in _PRODUCT_MISMATCHED_PREVIOUS_RUNS
    ):
        return None
    age = _age_hours_or_none(captured, served_cycle)
    if (
        endpoint == SERVED_VIA_PREVIOUS_RUNS
        and age is not None
        and age > float(max_substitution_age_hours)
    ):
        return None
    return model, ServedInstrumentValue(
        value_c=value,
        raw_model_forecast_id=raw_id,
        served_via=endpoint,
        served_cycle=served_cycle,
        captured_at=captured,
        age_hours=0.0 if age is None else age,
        lead_days=lead,
    )


def read_current_instrument_frontier_identity(
    conn: sqlite3.Connection,
    *,
    city: str,
    metric: str,
    target_date: str,
    decision_time_iso: str,
    models: tuple[str, ...] | None,
    schema: CurrentValueServingSchema,
    max_substitution_age_hours: float = PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS,
) -> tuple[tuple[str, int | None], ...]:
    """Run the complete production selector in prepare and return winner IDs."""

    try:
        decision_time = datetime.fromisoformat(
            str(decision_time_iso).replace("Z", "+00:00")
        )
        if decision_time.tzinfo is None:
            raise ValueError("decision_time_iso must be timezone-aware")
        decision_iso = decision_time.isoformat()
    except Exception:
        return tuple((model, None) for model in sorted(set(models or ())))

    if not schema.has_captured_at and not schema.has_source_available_at:
        return tuple((model, None) for model in sorted(set(models or ())))

    requested = None if models is None else set(models)
    out: dict[str, int] = {}
    for row in _read_source_clock_rows(
        conn,
        city=city,
        metric=metric,
        target_date=target_date,
        decision_iso=decision_iso,
        schema=schema,
        max_substitution_age_hours=max_substitution_age_hours,
    ):
        model = str(row[1])
        if requested is not None and model not in requested:
            continue
        served = _served_source_clock_row(
            row,
            schema=schema,
            max_substitution_age_hours=max_substitution_age_hours,
        )
        if served is not None:
            out.setdefault(model, served[1].raw_model_forecast_id)
    if requested is None:
        return tuple(sorted(out.items()))
    return tuple((model, out.get(model)) for model in sorted(requested))


def read_current_instrument_frontier_sentinel_ids(
    conn: sqlite3.Connection,
    *,
    city: str,
    metric: str,
    target_date: str,
    decision_time_iso: str,
    schema: CurrentValueServingSchema,
    max_substitution_age_hours: float = PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS,
) -> tuple[tuple[str, int], ...]:
    """Freeze each model's selector-first raw candidate during prepare."""

    try:
        decision_time = datetime.fromisoformat(
            str(decision_time_iso).replace("Z", "+00:00")
        )
        if decision_time.tzinfo is None:
            raise ValueError("decision_time_iso must be timezone-aware")
    except Exception:
        return ()
    sentinels: dict[str, int] = {}
    for row in _read_source_clock_rows(
        conn,
        city=city,
        metric=metric,
        target_date=target_date,
        decision_iso=decision_time.isoformat(),
        schema=schema,
        max_substitution_age_hours=max_substitution_age_hours,
    ):
        try:
            sentinels.setdefault(str(row[1]), int(row[0]))
        except (TypeError, ValueError, OverflowError):
            continue
    return tuple(sorted(sentinels.items()))


def _age_hours_or_none(captured_at: str | None, source_cycle_time_iso: str) -> float | None:
    """Hours from the cycle to the row's capture; None when unknowable (stripped schema /
    unparseable stamp). Unknowable FAILS OPEN to admission with age 0.0 — the same-natural-key
    cycle match is the primary freshness anchor; the parsed age is belt-and-suspenders only.
    Negative values (capture stamped before the cycle — the downloader stamps max(now, cycle),
    so this is defensive) clamp to 0.0."""
    if not captured_at:
        return None
    try:
        cap = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
        cyc = datetime.fromisoformat(str(source_cycle_time_iso).replace("Z", "+00:00"))
    except Exception:
        return None
    try:
        return max(0.0, (cap - cyc).total_seconds() / 3600.0)
    except Exception:
        return None


def read_current_instrument_values(
    conn: sqlite3.Connection,
    *,
    city: str,
    metric: str,
    target_date: str,
    source_cycle_time_iso: str,
    max_substitution_age_hours: float = PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS,
    include_station_sources: bool = False,
    decision_time_iso: str | None = None,
) -> dict[str, ServedInstrumentValue]:
    """THE single authority: per-model served CURRENT value for one (scope, cycle).

    Returns {model: ServedInstrumentValue}. single_runs rows win; models without one are
    substituted from their previous_runs row at the SAME natural key when the freshness horizon
    admits it; models absent from both stay absent (dropped by the fusion exactly as today).

    When ``decision_time_iso`` is supplied, every provider independently serves its newest row
    provably possessed by that instant. This is the source-clock law: the carrier still bounds
    ENS shape, but a faster deterministic provider must not be hidden until the carrier advances.
    Without it, the historical carrier-bound behavior is unchanged.

    LEAD_DAYS IS NOT A FILTER: the served row reports its real lead bucket, which names the
    history residual variance for that value. Every SQLite read failure propagates because
    UNKNOWN truth is not an empty family; only a successful empty selection returns ``{}``.
    """
    schema = current_value_serving_schema(conn)
    has_captured_at = schema.has_captured_at
    has_source_available_at = schema.has_source_available_at
    captured_select = ", captured_at" if has_captured_at else ""

    # ORDER suffix depends on whether captured_at is present in the schema:
    #   With captured_at: ORDER BY captured_at DESC NULLS LAST, raw_model_forecast_id DESC
    #     (1) Freshest-row-per-natural-key: a later corrected row (higher captured_at or
    #         higher raw_model_forecast_id as tiebreak) wins — `if model in out: continue`
    #         takes the FIRST row seen per model, so DESC order means freshest arrives first.
    #     (2) NULL captured_at fails CLOSED: NULLS LAST puts unstamped rows after all stamped
    #         siblings — a stamped sibling always outranks a NULL-captured_at row. A solo
    #         NULL-captured_at row (no stamped sibling) still serves, branded age_hours=0.0.
    #   Without captured_at (stripped schema): deterministic by raw_model_forecast_id DESC
    #     only — still freshest-by-id, fail-open on stripped schema (same as before the fix).
    if has_captured_at:
        order_clause = "captured_at DESC NULLS LAST, raw_model_forecast_id DESC"
    else:
        order_clause = "raw_model_forecast_id DESC"

    def _rows(endpoint: str, *, exact_cycle: bool) -> list:
        try:
            cycle_predicate = "source_cycle_time = ?" if exact_cycle else "source_cycle_time < ?"
            return conn.execute(
                f"""
                SELECT raw_model_forecast_id, model, forecast_value_c, lead_days,
                       source_cycle_time{captured_select}
                FROM raw_model_forecasts
                WHERE city = ? AND metric = ? AND target_date = ?
                  AND {cycle_predicate} AND endpoint = ?
                ORDER BY model,
                         source_cycle_time DESC,
                         lead_days,
                         {order_clause}
                """,
                (city, metric, target_date, source_cycle_time_iso, endpoint),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            _raise_typed_read_unavailable(exc)

    out: dict[str, ServedInstrumentValue] = {}

    if decision_time_iso is not None:
        try:
            decision_time = datetime.fromisoformat(
                str(decision_time_iso).replace("Z", "+00:00")
            )
            if decision_time.tzinfo is None:
                raise ValueError("decision_time_iso must be timezone-aware")
            decision_iso = decision_time.isoformat()
        except Exception:
            return {}
        possession_predicate = None
        if has_captured_at:
            possession_predicate = (
                "captured_at IS NOT NULL AND datetime(captured_at) <= datetime(?)"
            )
        elif has_source_available_at:
            possession_predicate = (
                "source_available_at IS NOT NULL "
                "AND datetime(source_available_at) <= datetime(?)"
            )
        if possession_predicate is None:
            return {}
        rows = _read_source_clock_rows(
            conn,
            city=city,
            metric=metric,
            target_date=target_date,
            decision_iso=decision_iso,
            schema=schema,
            max_substitution_age_hours=max_substitution_age_hours,
        )
        for row in rows:
            served = _served_source_clock_row(
                row,
                schema=schema,
                max_substitution_age_hours=max_substitution_age_hours,
            )
            if served is None:
                continue
            model, value = served
            if model in out:
                continue
            if (
                model.startswith(("cwa_", "hko_"))
                and not include_station_sources
            ):
                continue
            out[model] = value
        return out

    def _serve(endpoint: str, *, exact_cycle: bool) -> None:
        for row in _rows(endpoint, exact_cycle=exact_cycle):
            try:
                rid = int(row[0])
                model = str(row[1])
                parsed = _parse_forecast_value_and_lead(row[2], row[3])
                if parsed is None:
                    continue
                value, lead = parsed
                served_cycle = str(row[4])
                captured = str(row[5]) if has_captured_at and row[5] is not None else None
            except Exception:
                continue
            if model in out:
                continue
            age = _age_hours_or_none(captured, served_cycle)
            if endpoint == SERVED_VIA_PREVIOUS_RUNS and age is not None and age > float(max_substitution_age_hours):
                continue
            # 删了0.25: never substitute a product-mismatched previous_runs (ECMWF ifs025 0.25° coarse)
            # for the live 9km center — drop it, let the scheme renormalize over the present sources.
            if endpoint == SERVED_VIA_PREVIOUS_RUNS and model in _PRODUCT_MISMATCHED_PREVIOUS_RUNS:
                continue
            out[model] = ServedInstrumentValue(
                value_c=value, raw_model_forecast_id=rid, served_via=endpoint,
                served_cycle=served_cycle, captured_at=captured,
                age_hours=0.0 if age is None else age, lead_days=lead,
            )

    # Priority is about possession time first, then endpoint quality:
    # exact-cycle single_runs > exact-cycle previous_runs > newest prior single_runs > newest prior previous_runs.
    _serve(SERVED_VIA_SINGLE_RUNS, exact_cycle=True)
    _serve(SERVED_VIA_PREVIOUS_RUNS, exact_cycle=True)
    _serve(SERVED_VIA_SINGLE_RUNS, exact_cycle=False)
    _serve(SERVED_VIA_PREVIOUS_RUNS, exact_cycle=False)

    # Station-calibrated sources (cwa_*/hko_*) carry their OWN provider cycle clock, independent of
    # the gridded freshness ceiling: their latest captured single_runs row IS the current value and
    # must not be excluded just because its cycle is newer/older than the selected gridded cycle (the
    # gridded passes above serve source_cycle_time <= ceiling, which drops a station row issued after
    # the gridded cycle). OPT-IN: the gridded passes are the unchanged default contract for every
    # existing consumer (seed_discovery, completeness, upgrade-trigger); only the materializer center
    # path opts in, so a station source enters the precision fusion at its initial-precision weight
    # (raw_second_moment_weights) — DATA PRECISION, never a frozen-scheme hard weight.
    if include_station_sources:
        try:
            station_rows = conn.execute(
                f"""
                SELECT raw_model_forecast_id, model, forecast_value_c, lead_days,
                       source_cycle_time{captured_select}
                FROM raw_model_forecasts
                WHERE city = ? AND metric = ? AND target_date = ? AND endpoint = ?
                  AND (model LIKE 'cwa%' OR model LIKE 'hko%')
                ORDER BY model, source_cycle_time DESC, {order_clause}
                """,
                (city, metric, target_date, SERVED_VIA_SINGLE_RUNS),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            _raise_typed_read_unavailable(exc)
        # This is an OVERRIDE tier, not a first-match-wins fallback: a station model's own-cycle
        # freshest row must ALWAYS replace whatever the ceiling-bound passes above already parked
        # in `out` (even a stale <= ceiling row) — gating on `model in out` here was the steady-
        # state bug (2026-07 silent no-op): once any ceiling-bound row existed for the model, the
        # override could never fire again. `_station_served` instead guards ONLY within this loop,
        # so an older row for the SAME model later in the (freshest-first-ordered) result set can't
        # clobber the freshest one already applied.
        _station_served: set[str] = set()
        for row in station_rows:
            try:
                rid = int(row[0])
                model = str(row[1])
                parsed = _parse_forecast_value_and_lead(row[2], row[3])
                if parsed is None:
                    continue
                value, lead = parsed
                served_cycle = str(row[4])
                captured = str(row[5]) if has_captured_at and row[5] is not None else None
            except Exception:
                continue
            # Match the materializer's station-family convention exactly (cwa_/hko_ prefixes); the
            # broad SQL LIKE is narrowed here so a hypothetical non-station "cwa…"/"hko…" name cannot
            # leak in.
            if not model.startswith(("cwa_", "hko_")) or model in _station_served:
                continue
            _station_served.add(model)
            _age = _age_hours_or_none(captured, served_cycle)
            out[model] = ServedInstrumentValue(
                value_c=value, raw_model_forecast_id=rid, served_via=SERVED_VIA_SINGLE_RUNS,
                served_cycle=served_cycle, captured_at=captured,
                age_hours=0.0 if _age is None else _age, lead_days=lead,
            )
    return out


def read_freshest_coherent_instrument_values(
    conn: sqlite3.Connection,
    *,
    city: str,
    metric: str,
    target_date: str,
    decision_time_iso: str,
    models: tuple[str, ...],
    cohort_window_hours: float,
    max_substitution_age_hours: float = PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS,
    include_station_sources: bool = False,
) -> dict[str, ServedInstrumentValue]:
    """Return the newest causal multi-family provider cohort.

    This selector is intentionally distinct from ``read_current_instrument_values``:
    the latter serves each provider's newest possessed value for the center, while
    this function may select an immediately prior run for one provider so the
    between-provider spread remains simultaneous. A newer asynchronous run therefore
    cannot erase an already possessed coherent cohort.
    """

    try:
        decision_time = datetime.fromisoformat(
            str(decision_time_iso).replace("Z", "+00:00")
        )
        if decision_time.tzinfo is None:
            raise ValueError("decision_time_iso must be timezone-aware")
        window_hours = float(cohort_window_hours)
        if not math.isfinite(window_hours) or window_hours < 0.0:
            raise ValueError("cohort_window_hours must be finite and non-negative")
    except (TypeError, ValueError, OverflowError):
        return {}

    from src.data.replacement_forecast_cycle_policy import (  # noqa: PLC0415
        replacement_source_cycle_max_age_hours,
    )
    from src.strategy.live_inference.source_clock_vnext import (  # noqa: PLC0415
        provider_family_for_source,
    )

    schema = current_value_serving_schema(conn)
    if not schema.has_captured_at and not schema.has_source_available_at:
        return {}

    requested = set(models)
    by_model_cycle: dict[tuple[str, datetime], ServedInstrumentValue] = {}
    max_cycle_age = replacement_source_cycle_max_age_hours()
    for row in _read_source_clock_rows(
        conn,
        city=city,
        metric=metric,
        target_date=target_date,
        decision_iso=decision_time.isoformat(),
        schema=schema,
        max_substitution_age_hours=max_substitution_age_hours,
    ):
        served = _served_source_clock_row(
            row,
            schema=schema,
            max_substitution_age_hours=max_substitution_age_hours,
        )
        if served is None:
            continue
        model, value = served
        if model not in requested:
            continue
        if model.startswith(("cwa_", "hko_")) and not include_station_sources:
            continue
        try:
            cycle = datetime.fromisoformat(
                value.served_cycle.replace("Z", "+00:00")
            )
            if cycle.tzinfo is None:
                continue
            age_hours = (decision_time - cycle).total_seconds() / 3600.0
        except (TypeError, ValueError, OverflowError):
            continue
        if age_hours < 0.0 or age_hours > max_cycle_age:
            continue
        # The source-clock query orders endpoint quality and correction receipt so
        # first-valid wins for one model/cycle, exactly like the center selector.
        by_model_cycle.setdefault((model, cycle), value)

    if not by_model_cycle:
        return {}
    cycles = tuple(sorted({cycle for _, cycle in by_model_cycle}, reverse=True))
    for cohort_cycle in cycles:
        cohort: dict[str, ServedInstrumentValue] = {}
        for model in requested:
            eligible = [
                (cycle, value)
                for (candidate_model, cycle), value in by_model_cycle.items()
                if candidate_model == model
                and 0.0
                <= (cohort_cycle - cycle).total_seconds() / 3600.0
                <= window_hours
            ]
            if eligible:
                cohort[model] = max(eligible, key=lambda item: item[0])[1]
        if len({provider_family_for_source(model) for model in cohort}) >= 2:
            return cohort
    return {}
