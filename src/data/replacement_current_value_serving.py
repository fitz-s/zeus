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

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

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


def _is_station_model(model: str) -> bool:
    return model.startswith(("cwa_", "hko_"))


def _station_model_has_entry_authority(model: str) -> bool:
    """Read station entry authority from the source registry, never a name prefix alone."""
    if not _is_station_model(model):
        return True
    from src.data.forecast_source_registry import SOURCES

    spec = SOURCES.get(model)
    return spec is not None and "entry_primary" in spec.allowed_roles


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
    physical_response: Mapping[str, object] | None = None

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
            "physical_response": dict(self.physical_response) if self.physical_response else None,
        }


@dataclass(frozen=True)
class CurrentValueServingSchema:
    """Schema facts captured before a final writer lock is acquired."""

    has_captured_at: bool
    has_source_available_at: bool
    has_recorded_at: bool
    has_coverage_status: bool
    product_identity_columns: tuple[str, ...] = ()
    has_artifacts: bool = False


_PRODUCT_IDENTITY_COLUMNS = (
    "raw_model_forecast_id",
    "model", "city", "target_date", "endpoint", "endpoint_mode", "source_cycle_time",
    "source_id", "source_family", "product_id", "provider", "model_name",
    "request_params_json", "request_url_hash", "latitude_requested", "longitude_requested",
    "timezone_requested", "cell_selection", "elevation_param", "downscaling_policy",
    "model_domain_hash", "metric", "forecast_value_c", "artifact_id", "raw_sha256", "captured_at", "lead_days",
    "source_available_at", "recorded_at",
)


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
        has_recorded_at="recorded_at" in columns,
        has_coverage_status="coverage_status" in columns,
        product_identity_columns=tuple(name for name in _PRODUCT_IDENTITY_COLUMNS if name in columns),
        has_artifacts=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_forecast_artifacts'").fetchone() is not None,
    )


def _product_identity_select(schema: CurrentValueServingSchema, *, decision_iso: str | None = None) -> str:
    fields = ", ".join(
        f"'{name}', {name if name in schema.product_identity_columns else 'NULL'}"
        for name in _PRODUCT_IDENTITY_COLUMNS
    )
    artifact = "NULL"
    if schema.has_artifacts and "artifact_id" in schema.product_identity_columns:
        # A derived proof is an append-only, same-issued capture. Only causal
        # readers may use it; carrier-only callers cannot invent its cutoff.
        derived = ""
        cutoff = "NULL" if decision_iso is None else "'" + decision_iso.replace("'", "''") + "'"
        if decision_iso is not None:
            derived = f""" OR (raw_model_forecasts.artifact_id IS NULL
                AND raw_model_forecasts.elevation_param='requested'
                AND raw_model_forecasts.downscaling_policy='none'
                AND raw_model_forecasts.endpoint_mode='single_runs'
                AND a.source_id=raw_model_forecasts.source_id
                AND a.product_id=raw_model_forecasts.product_id
                AND a.source_cycle_time=raw_model_forecasts.source_cycle_time
                AND a.data_version='openmeteo_single_model_entity_body_v1'
                AND datetime(a.captured_at)<=datetime({cutoff})
                AND datetime(a.source_available_at)<=datetime({cutoff})
                AND datetime(a.recorded_at)<=datetime({cutoff})
                AND EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(a.artifact_metadata_json)
                    THEN json_extract(a.artifact_metadata_json,'$.physical_response.locations') END) loc
                    WHERE json_extract(loc.value,'$.requested_latitude')=raw_model_forecasts.latitude_requested
                      AND json_extract(loc.value,'$.requested_longitude')=raw_model_forecasts.longitude_requested
                      AND json_extract(loc.value,'$.timezone')=raw_model_forecasts.timezone_requested))"""
        artifact = f"""(SELECT json_object('artifact_id',a.artifact_id,'source_id',a.source_id,'product_id',a.product_id,
            'source_cycle_time',a.source_cycle_time,'captured_at',a.captured_at,
            'source_available_at',a.source_available_at,'recorded_at',a.recorded_at,'data_version',a.data_version,
            'artifact_path',a.artifact_path,'sha256',a.sha256,'byte_size',a.byte_size,
            'request_url',a.request_url,'request_params_json',a.request_params_json,
            'metadata',a.artifact_metadata_json) FROM raw_forecast_artifacts a
            WHERE a.artifact_id=raw_model_forecasts.artifact_id {derived}
            ORDER BY a.artifact_id DESC LIMIT 1)"""
    cutoff = "NULL" if decision_iso is None else "'" + decision_iso.replace("'", "''") + "'"
    return f"json_object({fields}, 'physical_proof_cutoff', {cutoff}, 'physical_artifact', json({artifact}))"


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
    single_runs_only: bool = False,
) -> list[sqlite3.Row]:
    """Read the complete production target-family candidate stream."""

    sql, params = _source_clock_rows_query(
        city=city,
        metric=metric,
        target_date=target_date,
        decision_iso=decision_iso,
        schema=schema,
        max_substitution_age_hours=max_substitution_age_hours,
        single_runs_only=single_runs_only,
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
    single_runs_only: bool = False,
) -> tuple[str, tuple[object, ...]]:
    """Build the complete production ordering used only before the final lock."""

    captured_select = ", captured_at" if schema.has_captured_at else ""
    if single_runs_only:
        captured_select += ", source_available_at, recorded_at"
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
    recorded_guard = "AND recorded_at IS NOT NULL AND datetime(recorded_at)<=datetime(?)" if schema.has_recorded_at else ""
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
    strict_guard = ""
    if single_runs_only:
        strict_guard = """
          AND source_available_at IS NOT NULL
          AND recorded_at IS NOT NULL
          AND coverage_status = 'COVERED'
          AND datetime(source_available_at) <= datetime(?)
          AND datetime(recorded_at) <= datetime(?)
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
    if recorded_guard:
        params.append(decision_iso)
    if previous_age_guard:
        params.extend((SERVED_VIA_PREVIOUS_RUNS, max_substitution_age_hours))
    if single_runs_only:
        params.extend((decision_iso, decision_iso, SERVED_VIA_SINGLE_RUNS))
    else:
        params.extend((SERVED_VIA_SINGLE_RUNS, SERVED_VIA_PREVIOUS_RUNS))
    # Missing physical proof remains NULL, including stripped/legacy schemas.
    product_select = _product_identity_select(schema, decision_iso=decision_iso)
    return (
        f"""
        SELECT raw_model_forecast_id, model, forecast_value_c, lead_days,
               source_cycle_time, endpoint{captured_select}, {product_select}
         FROM raw_model_forecasts
         WHERE city = ? AND target_date = ? AND metric = ?
           AND datetime(source_cycle_time) <= datetime(?)
           AND {possession_predicate}
           {source_available_guard}
           {recorded_guard}
           {previous_age_guard}
           {strict_guard}
           AND endpoint {'= ?' if single_runs_only else 'IN (?, ?)'}
         ORDER BY model,
                  datetime(source_cycle_time) DESC,
                  CASE endpoint WHEN 'single_runs' THEN 0 ELSE 1 END,
                  lead_days,
                  {order_clause}
        """,
        tuple(params),
    )


def _source_clock_product_has_authority(raw: object, *, lead_days: int | None) -> bool:
    """Bind one current provider row to its actual runtime physical product.

    SCOPE: this city/date/provider candidate, shared by current, cohort and
    frontier winners. DRAIN: ordinary producer captures a new provider cycle;
    the existing seed loop recomputes its family. Same-cycle INSERT OR IGNORE
    cannot repair old labels. RESET: a possessed row with the exact current
    product identity. Missing held evidence remains read-only until then.
    """
    try:
        row = json.loads(str(raw))
        if not isinstance(row, dict):
            return False
        model = str(row["model"] or "")
        if _is_station_model(model):
            # Agency forecasts have their own physical product, never DEM
            # correction. A known prefix alone is not a source exemption.
            providers = {
                "hko_fnd": "hong_kong_observatory",
                "cwa_township": "cwa_taiwan",
                "cwa_township_hourly_high": "cwa_taiwan",
                "cwa_township_hourly_low": "cwa_taiwan",
            }
            typed = bool(
                _station_model_has_entry_authority(model)
                and row["provider"] == providers.get(model)
                and row["source_family"] == "station_official_forecast"
                and row["source_id"] == f"{model}_single_runs"
                and row["model_name"] == model
                and row["product_id"] == f"{model}::single_runs"
                and row["endpoint"] == "single_runs"
                and row["endpoint_mode"] == "single_runs"
                and (model != "cwa_township_hourly_high" or row.get("metric") == "high")
                and (model != "cwa_township_hourly_low" or row.get("metric") == "low")
            )
            return typed and _station_response_has_authority(row)
        from src.config import runtime_cities_by_name
        from src.data.bayes_precision_fusion_history_provider import raw_product_matches_live_source

        city = runtime_cities_by_name().get(str(row["city"] or ""))
        if city is None or lead_days is None:
            return False
        row = _revalidated_legacy_product_row(row)
        if row is None or not raw_product_matches_live_source(row, city, lead_days=lead_days):
            return False
        return _physical_response_has_authority(row)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _revalidated_legacy_product_row(row: Mapping[str, object]) -> dict[str, object] | None:
    """A typed view over immutable raw, never a legacy-label compatibility gate."""
    result = dict(row)
    if row.get("artifact_id") is not None:
        return result
    artifact = row.get("physical_artifact")
    if not isinstance(artifact, dict) or row.get("physical_proof_cutoff") is None:
        return None
    from src.data.bayes_precision_fusion_download import (
        BAYES_PRECISION_FUSION_ELEVATION_PARAM, BAYES_PRECISION_FUSION_DOWNSCALING_POLICY,
        _model_domain_hash,
    )
    if row.get("endpoint_mode") != "single_runs" or row.get("elevation_param") != "requested" or row.get("downscaling_policy") != "none":
        return None
    basis = dict(provider=str(row["provider"]), model_name=str(row["model_name"]),
        cell_selection=str(row["cell_selection"]), endpoint_mode="single_runs")
    if row.get("model_domain_hash") != _model_domain_hash(**basis, elevation_param="requested", downscaling_policy="none"):
        return None
    result.update(elevation_param=BAYES_PRECISION_FUSION_ELEVATION_PARAM,
        downscaling_policy=BAYES_PRECISION_FUSION_DOWNSCALING_POLICY,
        model_domain_hash=_model_domain_hash(**basis, elevation_param=BAYES_PRECISION_FUSION_ELEVATION_PARAM,
            downscaling_policy=BAYES_PRECISION_FUSION_DOWNSCALING_POLICY),
        artifact_id=artifact["artifact_id"], raw_sha256=artifact["sha256"],
        revalidated_legacy_product=True,
        recorded_product_policy={key: row[key] for key in ("elevation_param", "downscaling_policy", "model_domain_hash")})
    return result


def _physical_proof_clocks_have_authority(row: Mapping[str, object], artifact: Mapping[str, object]) -> bool:
    cutoff = row.get("physical_proof_cutoff")
    if cutoff is None:
        return not row.get("revalidated_legacy_product", False)
    try:
        decision = datetime.fromisoformat(str(cutoff).replace("Z", "+00:00"))
        clocks = [datetime.fromisoformat(str(artifact[key]).replace("Z", "+00:00"))
            for key in ("source_cycle_time", "source_available_at", "captured_at", "recorded_at")]
        if any(stamp.tzinfo is None or stamp > decision for stamp in clocks):
            return False
        if not clocks[0] <= clocks[1] <= clocks[2] <= clocks[3]:
            return False
        if row.get("revalidated_legacy_product", False):
            from src.data.replacement_forecast_cycle_policy import cycle_age_outside_bound
            if cycle_age_outside_bound(decision, clocks[0]):
                return False
            for key in ("source_available_at", "captured_at", "recorded_at"):
                raw_clock = datetime.fromisoformat(str(row[key]).replace("Z", "+00:00"))
                if raw_clock.tzinfo is None and key == "recorded_at":
                    raw_clock = raw_clock.replace(tzinfo=timezone.utc)
                if raw_clock.tzinfo is None or raw_clock > decision:
                    return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def _station_response_has_authority(row: Mapping[str, object]) -> bool:
    try:
        import hashlib
        from pathlib import Path
        from src.config import runtime_cities_by_name
        from src.data.station_forecast_adapter import reextract_station_response_value
        artifact = row.get("physical_artifact")
        if not isinstance(artifact, dict) or artifact["sha256"] != row["raw_sha256"]:
            return False
        if not _physical_proof_clocks_have_authority(row, artifact):
            return False
        if any(artifact[key] != row[key] for key in ("source_id", "product_id", "source_cycle_time", "source_available_at", "captured_at")):
            return False
        from urllib.parse import parse_qs, urlsplit
        endpoint = urlsplit(str(artifact["request_url"]))
        if endpoint.scheme != "https" or endpoint.username is not None or endpoint.password is not None:
            return False
        params = json.loads(str(artifact["request_params_json"]))
        if row["provider"] == "hong_kong_observatory":
            query = {**{key: values[-1] for key, values in parse_qs(endpoint.query).items()}, **params}
            if endpoint.hostname != "data.weather.gov.hk" or endpoint.path != "/weatherAPI/opendata/weather.php" or query.get("dataType") != "fnd" or query.get("lang") != "en":
                return False
        elif row["provider"] == "cwa_taiwan":
            if endpoint.hostname != "opendata.cwa.gov.tw" or endpoint.path != "/fileapi/v1/opendataapi/F-D0047-061":
                return False
        else:
            return False
        body = Path(str(artifact["artifact_path"])).read_bytes()
        if len(body) != artifact["byte_size"] or hashlib.sha256(body).hexdigest() != artifact["sha256"]:
            return False
        evidence = json.loads(str(artifact["metadata"]))["station_response"]
        if evidence["revision"] != "station_forecast_entity_body_v1":
            return False
        matching = [proof for proof in evidence["items"] if all(proof.get(key) == row[key]
            for key in ("model", "city", "metric", "target_date", "source_cycle_time", "source_available_at", "captured_at", "provider"))]
        if len(matching) != 1:
            return False
        city = runtime_cities_by_name().get(str(row["city"]))
        if city is None:
            return False
        station = "HKO" if str(getattr(city, "settlement_source_type", "")) == "hko" else str(getattr(city, "wu_station", "") or "").upper()
        if matching[0].get("station_id") != station:
            return False
        value = reextract_station_response_value(body, matching[0])
        return value is not None and math.isfinite(float(value)) and math.isclose(float(value), float(row["forecast_value_c"]), abs_tol=1e-9)
    except (ImportError, KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return False


def _physical_response_has_authority(row: Mapping[str, object]) -> bool:
    """Verify actual single-model product and exact hourly/local-day value, not request intention."""
    try:
        from pathlib import Path
        import hashlib
        from src.data.bayes_precision_fusion_download import _parse_batched_single_runs_payload
        from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
        from src.data.openmeteo_ecmwf_ifs9_anchor import SINGLE_RUNS_FORECAST_URL, STANDARD_FORECAST_URL
        from src.data.openmeteo_client import PREVIOUS_RUNS_URL
        artifact = row["physical_artifact"]
        if not isinstance(artifact, dict) or artifact["sha256"] != row["raw_sha256"]:
            return False
        if not _physical_proof_clocks_have_authority(row, artifact):
            return False
        expected_url = {"single_runs": SINGLE_RUNS_FORECAST_URL,
            "standard_api_meta_stamped": STANDARD_FORECAST_URL,
            "previous_runs": PREVIOUS_RUNS_URL}.get(str(row["endpoint_mode"]))
        if artifact.get("data_version") != "openmeteo_single_model_entity_body_v1" or artifact["request_url"] != expected_url:
            return False
        if any(artifact[key] != row[key] for key in ("source_id", "product_id", "source_cycle_time")):
            return False
        metadata = json.loads(str(artifact["metadata"]))["physical_response"]
        params = json.loads(str(artifact["request_params_json"]))
        if metadata["revision"] != "openmeteo_single_model_entity_body_v1" or params != metadata["request_params"]:
            return False
        model = str(row["model"])
        if metadata["model"] != model or params["models"] != OPENMETEO_MODEL_IDS.get(model, model):
            return False
        if params.get("elevation") is not None or params.get("cell_selection", "land") != "land":
            return False
        variable = ("temperature_2m" if row["endpoint"] != "previous_runs" or int(row.get("lead_days") or 0) == 0
                    else f"temperature_2m_previous_day{int(row['lead_days'])}")
        if params.get("temperature_unit") != "celsius" or params.get("hourly") != variable:
            return False
        indices = [index for index, (latitude, longitude, tz) in enumerate(zip(
            str(params["latitude"]).split(","), str(params["longitude"]).split(","),
            str(params["timezone"]).split(","), strict=True))
            if math.isclose(float(latitude), float(row["latitude_requested"]), abs_tol=1e-6)
            and math.isclose(float(longitude), float(row["longitude_requested"]), abs_tol=1e-6)
            and tz == row["timezone_requested"]]
        if len(indices) != 1:
            return False
        index = indices[0]
        geometry = metadata["locations"][index]
        for key, expected in (("latitude", row["latitude_requested"]), ("longitude", row["longitude_requested"])):
            if not math.isclose(float(str(params[key]).split(",")[index]), float(expected), abs_tol=1e-6):
                return False
        if str(params["timezone"]).split(",")[index] != row["timezone_requested"]:
            return False
        if row["endpoint_mode"] == "single_runs":
            if datetime.fromisoformat(str(params["run"])) != datetime.fromisoformat(str(row["source_cycle_time"])).replace(tzinfo=None):
                return False
        body = Path(str(artifact["artifact_path"])).read_bytes()
        if len(body) != artifact["byte_size"] or hashlib.sha256(body).hexdigest() != artifact["sha256"]:
            return False
        decoded = json.loads(body)
        payload = decoded if isinstance(decoded, dict) and index == 0 else decoded[index]
        if payload["timezone"] != row["timezone_requested"]:
            return False
        for key, proofkey, low, high in (
            ("latitude", "selected_latitude", -90, 90),
            ("longitude", "selected_longitude", -180, 180),
            ("elevation", "target_dem_elevation_m", -500, 9000),
        ):
            actual = float(payload[key])
            if not math.isfinite(actual) or not low <= actual <= high or actual != float(geometry[proofkey]):
                return False
        # Official Gridable/GaussianGrid land search is <50km; registered
        # temperature domains' nearest-center half diagonal is also <50km.
        # This is a wrong-site transport bound, NOT surface/representativeness proof.
        lat1, lat2 = math.radians(float(row["latitude_requested"])), math.radians(float(payload["latitude"]))
        dlat = lat2 - lat1
        dlon = math.radians(float(payload["longitude"]) - float(row["longitude_requested"]))
        hav = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
        if 2 * 6371.0088 * math.asin(math.sqrt(min(1.0, hav))) >= 50.0:
            return False
        keyed = f"{variable}_{OPENMETEO_MODEL_IDS.get(model, model)}"
        units = payload["hourly_units"]
        if units.get(keyed, units.get(variable)) != "°C":
            return False
        if metadata["aggregation"] != "max_min_of_local_day_hourly_samples":
            return False
        if row["endpoint"] == "previous_runs":
            payload = {**payload, "hourly": {**payload["hourly"], "temperature_2m": payload["hourly"].get(variable) or payload["hourly"].get(keyed)},
                       "hourly_units": {**payload["hourly_units"], "temperature_2m": "°C"}}
        values = _parse_batched_single_runs_payload(payload, [model],
            datetime.fromisoformat(str(row["target_date"])).date(), str(row["timezone_requested"]),
            decision_at=str(row.get("physical_proof_cutoff") or row["captured_at"]))
        high_c, low_c = values[model]
        expected = high_c if row["metric"] == "high" else low_c if row["metric"] == "low" else None
        return expected is not None and math.isclose(float(expected), float(row["forecast_value_c"]), abs_tol=1e-9)
    except (KeyError, IndexError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return False


def _physical_response_provenance(row: Mapping[str, object]) -> Mapping[str, object] | None:
    if not _is_station_model(str(row["model"])):
        row = _revalidated_legacy_product_row(row)
        if row is None:
            return None
    artifact = row.get("physical_artifact")
    if not isinstance(artifact, dict):
        return None
    if _is_station_model(str(row["model"])):
        evidence = json.loads(str(artifact["metadata"]))["station_response"]
        proof = next(proof for proof in evidence["items"] if all(proof.get(key) == row[key]
            for key in ("model", "city", "metric", "target_date", "source_cycle_time", "provider")))
        return {**proof, "artifact_id": row["artifact_id"], "entity_body_sha256": artifact["sha256"]}
    metadata = json.loads(str(artifact["metadata"]))["physical_response"]
    params = metadata["request_params"]
    index = next(i for i, (lat, lon, tz) in enumerate(zip(
        str(params["latitude"]).split(","), str(params["longitude"]).split(","),
        str(params["timezone"]).split(","), strict=True))
        if math.isclose(float(lat), float(row["latitude_requested"]), abs_tol=1e-6)
        and math.isclose(float(lon), float(row["longitude_requested"]), abs_tol=1e-6)
        and tz == row["timezone_requested"])
    return {"revision": metadata["revision"], "model": row["model"],
        "product_id": row["product_id"], "artifact_id": row["artifact_id"],
        "entity_body_sha256": artifact["sha256"],
        "raw_model_forecast_id": row["raw_model_forecast_id"],
        "revalidated_legacy_product": bool(row.get("revalidated_legacy_product", False)),
        "recorded_product_policy": row.get("recorded_product_policy"),
        "proof_captured_at": artifact["captured_at"],
        "proof_available_at": artifact["source_available_at"],
        "proof_recorded_at": artifact["recorded_at"],
        "requested_latitude": row["latitude_requested"],
        "requested_longitude": row["longitude_requested"],
        "timezone": row["timezone_requested"],
        "cell_selection": row["cell_selection"], "elevation_param": row["elevation_param"],
        "downscaling_policy": row["downscaling_policy"],
        "native_variable": metadata["native_variable"],
        "variable_role": metadata["variable_role"],
        "native_file_variable": metadata["native_file_variable"],
        "temporal_resolution": metadata["temporal_resolution"],
        "temperature_unit": metadata["temperature_unit"], "aggregation": metadata["aggregation"],
        "native_grid_elevation_m": None, "native_surface": "UNKNOWN",
        "representativeness_status": "UNPROVEN", **metadata["locations"][index]}


def _served_source_clock_row(
    row: sqlite3.Row | tuple[object, ...],
    *,
    schema: CurrentValueServingSchema,
    max_substitution_age_hours: float,
    single_runs_decision_time: datetime | None = None,
) -> tuple[str, ServedInstrumentValue] | None:
    """Parse one ordered row with the production serving validity rules."""

    try:
        raw_id = int(row[0])
        model = str(row[1])
        parsed = _parse_forecast_value_and_lead(row[2], row[3])
        if parsed is None:
            return None
        value, lead = parsed
        if not _source_clock_product_has_authority(row[-1], lead_days=lead):
            return None
        served_cycle = str(row[4])
        endpoint = str(row[5])
        captured = (
            str(row[6])
            if schema.has_captured_at and row[6] is not None
            else None
        )
    except (TypeError, ValueError, OverflowError):
        return None
    if single_runs_decision_time is not None:
        if endpoint != SERVED_VIA_SINGLE_RUNS or not captured:
            return None
        for raw, allow_naive_utc in (
            (served_cycle, False), (captured, False),
            (row[7], False), (row[8], True),
        ):
            try:
                stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    if not allow_naive_utc:
                        return None
                    stamp = stamp.replace(tzinfo=timezone.utc)
                if stamp > single_runs_decision_time:
                    return None
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
        physical_response=_physical_response_provenance(json.loads(str(row[-1]))),
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
                       source_cycle_time{captured_select}, {_product_identity_select(schema)}
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
            if _is_station_model(model) and (
                not include_station_sources
                or not _station_model_has_entry_authority(model)
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
                if not _source_clock_product_has_authority(row[-1], lead_days=lead):
                    continue
                served_cycle = str(row[4])
                captured = str(row[5]) if has_captured_at and row[5] is not None else None
            except Exception:
                continue
            if _is_station_model(model) and not _station_model_has_entry_authority(model):
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
                physical_response=_physical_response_provenance(json.loads(str(row[-1]))),
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
                       source_cycle_time{captured_select}, {_product_identity_select(schema)}
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
                if not _source_clock_product_has_authority(row[-1], lead_days=lead):
                    continue
                served_cycle = str(row[4])
                captured = str(row[5]) if has_captured_at and row[5] is not None else None
            except Exception:
                continue
            # The broad SQL LIKE is narrowed through the registry: a retained raw row for a
            # retired station product cannot regain entry authority through its name prefix.
            if (
                not _is_station_model(model)
                or not _station_model_has_entry_authority(model)
                or model in _station_served
            ):
                continue
            _station_served.add(model)
            _age = _age_hours_or_none(captured, served_cycle)
            out[model] = ServedInstrumentValue(
                value_c=value, raw_model_forecast_id=rid, served_via=SERVED_VIA_SINGLE_RUNS,
                served_cycle=served_cycle, captured_at=captured,
                age_hours=0.0 if _age is None else _age, lead_days=lead,
                physical_response=_physical_response_provenance(json.loads(str(row[-1]))),
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
    single_runs_only: bool = False,
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
    if single_runs_only and not (
        schema.has_captured_at and schema.has_source_available_at
        and schema.has_recorded_at and schema.has_coverage_status
    ):
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
        single_runs_only=single_runs_only,
    ):
        served = _served_source_clock_row(
            row,
            schema=schema,
            max_substitution_age_hours=max_substitution_age_hours,
            single_runs_decision_time=decision_time if single_runs_only else None,
        )
        if served is None:
            continue
        model, value = served
        if model not in requested:
            continue
        if _is_station_model(model) and (
            not include_station_sources
            or not _station_model_has_entry_authority(model)
        ):
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
