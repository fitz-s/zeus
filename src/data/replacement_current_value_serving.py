# Created: 2026-06-11
# Last reused or audited: 2026-10-02 (capture family keyed by request target; CURRENT_REUSABLE)
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
from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import math
import sqlite3
import threading
import time
from dataclasses import dataclass, field
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


_ARTIFACT_IDENTITY_JSON_SQL = """json_object('artifact_id',a.artifact_id,'source_id',a.source_id,'product_id',a.product_id,
    'source_cycle_time',a.source_cycle_time,'captured_at',a.captured_at,
    'source_available_at',a.source_available_at,'recorded_at',a.recorded_at,'data_version',a.data_version,
    'artifact_path',a.artifact_path,'sha256',a.sha256,'byte_size',a.byte_size,
    'request_url',a.request_url,'request_params_json',a.request_params_json,'metadata',a.artifact_metadata_json,
    'body_artifact',json((SELECT json_object('artifact_id',b.artifact_id,'source_id',b.source_id,
        'product_id',b.product_id,'data_version',b.data_version,'source_cycle_time',b.source_cycle_time,
        'source_available_at',b.source_available_at,'captured_at',b.captured_at,'recorded_at',b.recorded_at,
        'artifact_path',b.artifact_path,'sha256',b.sha256,'byte_size',b.byte_size,
        'request_url',b.request_url,'request_params_json',b.request_params_json,'metadata',b.artifact_metadata_json)
        FROM raw_forecast_artifacts b WHERE b.artifact_id=json_extract(CASE WHEN json_valid(a.artifact_metadata_json)
            THEN a.artifact_metadata_json ELSE '{}' END,'$.physical_http_capture_receipt.body_artifact_id'))))"""
_PHYSICAL_CAPTURE_SCAN_BUDGET_SECONDS = 2.0


def _product_identity_select(schema: CurrentValueServingSchema, *, decision_iso: str | None = None,
        day0_remaining_from_iso: str | None = None) -> str:
    fields = ", ".join(
        f"'{name}', {name if name in schema.product_identity_columns else 'NULL'}"
        for name in _PRODUCT_IDENTITY_COLUMNS
    )
    artifact = "NULL"
    if schema.has_artifacts and "artifact_id" in schema.product_identity_columns:
        artifact = f"(SELECT {_ARTIFACT_IDENTITY_JSON_SQL} FROM raw_forecast_artifacts a WHERE a.artifact_id=raw_model_forecasts.artifact_id)"
    cutoff = "NULL" if decision_iso is None else "'" + decision_iso.replace("'", "''") + "'"
    # The family's last authorized observation (Day0 tau) rides beside the cut,
    # so every proof re-parse of this row reads one boundary. Without tau the
    # identity text is exactly what it was.
    tau = ("" if day0_remaining_from_iso is None
           else ", 'day0_remaining_from', '" + str(day0_remaining_from_iso).replace("'", "''") + "'")
    return f"json_object({fields}, 'physical_proof_cutoff', {cutoff}{tau}, 'physical_artifact', json({artifact}))"


def _receipt_canonical_recorded_bound(artifact: Mapping[str,object]) -> datetime | None:
    """An own hash-sealed receipt can bound a damaged DB clock for selection/cost only.

    Its source clocks are NOT substituted into the artifact or q authority. A
    truly future receipt remains outside the old decision, while a later real
    network capture can supersede this invalid append's known possession bound.
    """
    try:
        import hashlib
        from pathlib import Path
        if artifact["data_version"]!="openmeteo_single_model_http_capture_receipt_v1":
            return None
        path=Path(str(artifact["artifact_path"]))
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=artifact["byte_size"]:
            return None
        encoded=path.read_bytes()
        if len(encoded)!=artifact["byte_size"] or hashlib.sha256(encoded).hexdigest()!=artifact["sha256"]:
            return None
        receipt=json.loads(encoded)
        if receipt["revision"]!=artifact["data_version"] or any(receipt[key]!=artifact[key] for key in
            ("source_id","product_id","source_cycle_time","request_url")) or receipt["request_params"]!=json.loads(str(artifact["request_params_json"])):
            return None
        clocks=[datetime.fromisoformat(str(receipt[key]).replace("Z","+00:00")) for key in
                ("source_cycle_time","source_available_at","captured_at","recorded_at")]
        if any(value.tzinfo is None for value in clocks) or not clocks[0]<=clocks[1]<=clocks[2]<=clocks[3]:
            return None
        return clocks[3].astimezone(timezone.utc)
    except (KeyError,TypeError,ValueError,OSError):
        return None


def _physical_artifact_at_cutoff(row: Mapping[str, object], candidates=None) -> dict[str, object]:
    """Choose the latest possessed event without rounding clocks in SQLite.

    SQL only narrows the request family. A malformed latest clock remains a
    candidate for strict rejection, rather than silently exposing an older
    body. Known future possession is excluded with full datetime precision.
    """
    if candidates is None:
        candidates = row.get("physical_artifact")
        if not isinstance(candidates, list):
            return dict(row)

    def clock(value: object) -> datetime | None:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
        except (TypeError, ValueError):
            return None

    cutoff = clock(row.get("physical_proof_cutoff"))
    best = None
    for artifact in candidates:
        if not isinstance(artifact, dict):
            continue
        captured = clock(artifact.get("captured_at"))
        available = clock(artifact.get("source_available_at"))
        recorded = clock(artifact.get("recorded_at"))
        recorded_bound=recorded
        invalid_recorded=False
        if recorded is None or (cutoff is not None and recorded>cutoff and (captured is None or captured<=cutoff)):
            own_bound=_receipt_canonical_recorded_bound(artifact)
            if own_bound is not None:
                recorded_bound=own_bound
                invalid_recorded=recorded!=own_bound
        if cutoff is None:
            if artifact.get("artifact_id") != row.get("artifact_id"):
                continue
        elif (available is not None and available > cutoff
                and ((available_bound := _receipt_canonical_recorded_bound(artifact)) is None or available_bound > cutoff)):
            continue
        elif recorded_bound is not None and recorded_bound > cutoff:
            continue
        elif recorded_bound is None and captured is not None and captured > cutoff:
            continue
        # Unknown event order fails closed instead of hiding malformed proof.
        invalid_capture = captured is None or invalid_recorded or (recorded_bound is not None and captured > recorded_bound)
        order = (recorded_bound if invalid_capture else captured) or recorded_bound or datetime.max.replace(tzinfo=timezone.utc)
        candidate = (order, invalid_capture, recorded_bound or order, int(artifact["artifact_id"]), artifact)
        if best is None or candidate[:4] > best[:4]:
            best = candidate
    latest = best[-1] if best is not None else None
    return {**row, "physical_artifact": latest}


def _request_coordinates_sql(key: str) -> str:
    """One comma-split request parameter as json_each rows (a batched request names many cells)."""
    return (f"json_each(replace(json_array(CAST(json_extract(CASE WHEN json_valid(a.request_params_json) "
            f"THEN a.request_params_json ELSE '{{}}' END,'$.{key}') AS TEXT)), ',', '\",\"'))")


# The request family of every physical capture a raw row may cite: same issued
# product cycle and a receipt (or, for legacy rows, entity-body) kind.
_CAPTURE_GROUP_WHERE = """a.source_id=? AND a.product_id=? AND a.source_cycle_time=?
      AND (a.data_version='openmeteo_single_model_http_capture_receipt_v1'
          OR (? AND a.data_version='openmeteo_single_model_entity_body_v1'))"""
_CAPTURE_GROUP_SQL = f"SELECT a.artifact_id, {_ARTIFACT_IDENTITY_JSON_SQL} FROM raw_forecast_artifacts a WHERE {_CAPTURE_GROUP_WHERE}"
# One row per requested cell of each family capture; SQLite performs the casts.
_CAPTURE_GROUP_CELLS_SQL = f"""SELECT a.artifact_id, CAST(lat.value AS REAL), CAST(lon.value AS REAL), tz.value
    FROM raw_forecast_artifacts a
    JOIN {_request_coordinates_sql('latitude')} lat
    JOIN {_request_coordinates_sql('longitude')} lon ON lon.key=lat.key
    JOIN {_request_coordinates_sql('timezone')} tz ON tz.key=lat.key
    WHERE {_CAPTURE_GROUP_WHERE}"""

_CAPTURE_BY_ID_SQL = f"SELECT a.artifact_id, {_ARTIFACT_IDENTITY_JSON_SQL} FROM raw_forecast_artifacts a WHERE a.artifact_id=?"

# Within one issued product and cell, captures still differ by the target they
# asked for: a previous_runs request names its own date window and lagged
# variable. A raw row may cite only a capture of its own target request.
_REQUEST_TARGET_KEYS = ("hourly", "start_date", "end_date")


def _request_target(params_json: object) -> tuple[object, ...]:
    """The request's target selector, read as SQLite's json_extract over '{}' would."""
    try:
        params = json.loads(str(params_json))
    except (TypeError, ValueError):
        params = None
    if not isinstance(params, dict):
        params = {}
    return tuple(params.get(key) for key in _REQUEST_TARGET_KEYS)


_REQUEST_TARGET_SQL = " AND ".join(
    f"json_extract(CASE WHEN json_valid(a.request_params_json) THEN a.request_params_json "
    f"ELSE '{{}}' END,'$.{key}') IS ?" for key in _REQUEST_TARGET_KEYS)


@dataclass
class _SnapshotMemo:
    key: tuple[object, ...] | None = None
    groups: dict[tuple[object, ...], list[tuple[int, str, frozenset[tuple[object, object, object]]]]] = field(default_factory=dict)
    by_id: dict[object, tuple[int, str] | None] = field(default_factory=dict)


_SNAPSHOT_MEMO = threading.local()


def _snapshot_memo(conn: sqlite3.Connection) -> _SnapshotMemo:
    """Per-thread capture reads of one never-written connection's visible database.

    Every schema's PRAGMA data_version moves when another connection commits
    to it, and stays put inside an open read transaction, exactly as the rows
    this connection can see. A connection that ever wrote (total_changes) could
    roll its own rows back without moving either, so it gets a one-call memo.
    """
    if conn.total_changes:
        return _SnapshotMemo()
    versions = tuple(
        (str(item[1]), conn.execute(f'PRAGMA "{str(item[1]).replace(chr(34), chr(34) * 2)}".data_version').fetchone()[0])
        for item in conn.execute("PRAGMA database_list").fetchall()
    )
    memo = getattr(_SNAPSHOT_MEMO, "memo", None)
    if memo is None or memo.key[0] is not conn or memo.key[1] != versions:
        memo = _SNAPSHOT_MEMO.memo = _SnapshotMemo(key=(conn, versions))
    return memo


def _fetch_complete(cursor: sqlite3.Cursor, *, deadline: float) -> list[tuple]:
    rows: list[tuple] = []
    try:
        while True:
            if time.monotonic() >= deadline:
                raise CurrentValueServingReadUnavailable("physical_capture_scan_budget_exceeded")
            batch = cursor.fetchmany(256)
            if not batch:
                return rows
            rows.extend(batch)
    finally:
        cursor.close()


def _physical_artifact_candidates(conn: sqlite3.Connection, row: Mapping[str, object], *, deadline: float):
    """Every capture a row may cite: its own artifact, or any same-family capture of its exact cell.

    One request-family read serves every row of that family in the current
    read snapshot; loader cost is round-trips, not rows.
    """
    legacy = (row.get("artifact_id") is None and row.get("elevation_param") == "requested"
        and row.get("downscaling_policy") == "none" and row.get("endpoint_mode") == "single_runs")
    cell = (row["latitude_requested"], row["longitude_requested"], row["timezone_requested"])
    if not (all(type(value) in (int, float) for value in cell[:2]) and type(cell[2]) is str):
        # Only exact JSON numbers/text are matched in Python; any other bound
        # type keeps SQLite's own affinity comparison in the per-row query.
        yield from _physical_artifact_candidates_by_row(conn, row, legacy=legacy, deadline=deadline)
        return
    memo = _snapshot_memo(conn)
    group_key = (row["source_id"], row["product_id"], row["source_cycle_time"], int(legacy))
    group = memo.groups.get(group_key)
    if group is None:
        # Both reads see the same snapshot only inside a read transaction;
        # outside one, a capture inserted between them is simply not served
        # from this group (its identity row is required), never mis-served.
        texts = {int(artifact_id): str(text) for artifact_id, text in
                 _fetch_complete(conn.execute(_CAPTURE_GROUP_SQL, group_key), deadline=deadline)}
        cells: dict[int, set[tuple[object, object, object]]] = {}
        for artifact_id, lat, lon, tz in _fetch_complete(conn.execute(_CAPTURE_GROUP_CELLS_SQL, group_key), deadline=deadline):
            cells.setdefault(int(artifact_id), set()).add((lat, lon, tz))
        group = [(artifact_id, texts[artifact_id], frozenset(found))
                 for artifact_id, found in cells.items() if artifact_id in texts]
        memo.groups[group_key] = group
    own_id = row.get("artifact_id")
    if own_id not in memo.by_id:
        found = _fetch_complete(conn.execute(_CAPTURE_BY_ID_SQL, (own_id,)), deadline=deadline)
        memo.by_id[own_id] = (int(found[0][0]), str(found[0][1])) if found else None
    # SQLite's `CAST(lat AS REAL)=? AND CAST(lon AS REAL)=? AND tz=?` for a
    # float/int and text binding: REAL compares by value, text compares exactly.
    target = _request_target(row.get("request_params_json"))
    matched = {artifact_id: text for artifact_id, text, found in group if any(
        isinstance(lat, float) and isinstance(lon, float) and isinstance(tz, str)
        and lat == cell[0] and lon == cell[1] and tz == cell[2] for lat, lon, tz in found)
        and _request_target(json.loads(text).get("request_params_json")) == target}
    if memo.by_id[own_id] is not None:
        matched.setdefault(*memo.by_id[own_id])
    for artifact_id in sorted(matched, reverse=True):
        if time.monotonic() >= deadline:
            raise CurrentValueServingReadUnavailable("physical_capture_scan_budget_exceeded")
        yield json.loads(matched[artifact_id])


def _physical_artifact_candidates_by_row(conn: sqlite3.Connection, row: Mapping[str, object], *,
        legacy: bool, deadline: float):
    sql = f"""SELECT {_ARTIFACT_IDENTITY_JSON_SQL} FROM raw_forecast_artifacts a
        WHERE a.artifact_id=? OR (a.source_id=? AND a.product_id=? AND a.source_cycle_time=?
            AND (a.data_version='openmeteo_single_model_http_capture_receipt_v1'
                OR (? AND a.data_version='openmeteo_single_model_entity_body_v1'))
            AND EXISTS (SELECT 1 FROM {_request_coordinates_sql('latitude')} lat
                JOIN {_request_coordinates_sql('longitude')} lon ON lon.key=lat.key
                JOIN {_request_coordinates_sql('timezone')} tz ON tz.key=lat.key
                WHERE CAST(lat.value AS REAL)=? AND CAST(lon.value AS REAL)=? AND tz.value=?)
            AND {_REQUEST_TARGET_SQL})
        ORDER BY a.artifact_id DESC"""
    cursor = conn.execute(sql, (row.get("artifact_id"), row["source_id"], row["product_id"], row["source_cycle_time"],
        int(legacy), row["latitude_requested"], row["longitude_requested"], row["timezone_requested"],
        *_request_target(row.get("request_params_json"))))
    try:
        while True:
            if time.monotonic() >= deadline:
                raise CurrentValueServingReadUnavailable("physical_capture_scan_budget_exceeded")
            batch = cursor.fetchmany(32)
            if not batch:
                return
            for item in batch:
                if time.monotonic() >= deadline:
                    raise CurrentValueServingReadUnavailable("physical_capture_scan_budget_exceeded")
                yield json.loads(str(item[0]))
    finally:
        cursor.close()


_PHYSICAL_READ_PASS: ContextVar[dict[tuple[object, ...], str] | None] = ContextVar(
    "physical_read_pass", default=None,
)


@contextmanager
def physical_read_pass(*, include_model_surface: bool = True):
    """One pass's physical-proof reads, each answered once.

    A pass (the healer's coverage judgement, one target plan) re-reads the same
    row identity at the same cutoff once per caller (11,742 reads, 2,076
    distinct on live 10-02). Inside this scope an identical read on the same
    connection and visible snapshot returns the first answer; the scope dies
    with the pass, so new captures are always seen by the next one. A read that
    raises is never stored. Nested scopes share the outer pass.
    """
    from src.data.openmeteo_model_surface import model_surface_read_pass  # noqa: PLC0415

    if _PHYSICAL_READ_PASS.get() is not None:
        yield
        return
    token = _PHYSICAL_READ_PASS.set({})
    try:
        with model_surface_read_pass() if include_model_surface else nullcontext():
            yield
    finally:
        _PHYSICAL_READ_PASS.reset(token)


def _read_product_identity_at_cutoff(conn: sqlite3.Connection, raw: object, *, deadline_monotonic: float | None = None) -> str:
    """Complete same-issued scan; an observed repair covers only its old unknown-bad prefix."""
    memo = _PHYSICAL_READ_PASS.get()
    if memo is None or (deadline_monotonic is not None and time.monotonic() >= deadline_monotonic):
        return _read_product_identity_at_cutoff_uncached(conn, raw, deadline_monotonic=deadline_monotonic)
    try:
        hash(conn)
    except TypeError:
        return _read_product_identity_at_cutoff_uncached(conn, raw, deadline_monotonic=deadline_monotonic)
    # The visible snapshot of this exact connection, as _snapshot_memo keys it:
    # a written connection or any other connection's commit is a new key.
    snapshot = _snapshot_memo(conn).key
    if snapshot is None:
        return _read_product_identity_at_cutoff_uncached(conn, raw, deadline_monotonic=deadline_monotonic)
    key = (conn, snapshot[1], str(raw))
    hit = memo.get(key)
    if hit is None:
        hit = memo[key] = _read_product_identity_at_cutoff_uncached(
            conn, raw, deadline_monotonic=deadline_monotonic,
        )
    return hit


def _read_product_identity_at_cutoff_uncached(conn: sqlite3.Connection, raw: object, *, deadline_monotonic: float | None = None) -> str:
    row = json.loads(str(raw))
    if row.get("physical_proof_cutoff") is not None and _legacy_hko_context(row):
        deadline = time.monotonic() + _PHYSICAL_CAPTURE_SCAN_BUDGET_SECONDS
        if deadline_monotonic is not None:
            deadline = min(deadline, deadline_monotonic)
        candidates = conn.execute(f"SELECT {_ARTIFACT_IDENTITY_JSON_SQL} FROM raw_forecast_artifacts a "
            "WHERE source_id=? AND product_id=? AND source_cycle_time=? AND data_version=?",
            (row["source_id"], row["product_id"], row["source_cycle_time"], "station_forecast_entity_body_v1"))
        def captured_entities():
            try:
                for item in candidates:
                    if time.monotonic() >= deadline:
                        raise CurrentValueServingReadUnavailable("physical_capture_scan_budget_exceeded")
                    yield json.loads(item[0])
            finally:
                candidates.close()
        return json.dumps(_physical_artifact_at_cutoff(row, captured_entities()), separators=(",", ":"))
    if row.get("physical_proof_cutoff") is None or not all(row.get(key) is not None for key in (
        "source_id", "product_id", "source_cycle_time", "latitude_requested", "longitude_requested", "timezone_requested"
    )):
        return str(raw)
    deadline = time.monotonic() + _PHYSICAL_CAPTURE_SCAN_BUDGET_SECONDS
    if deadline_monotonic is not None:
        deadline = min(deadline, deadline_monotonic)
    # One streamed scan serves selection and repair discovery. Only receipts that
    # name this row as a repair basis are retained; the catalog stays streamed.
    repairs: list[tuple[dict[str, object], Mapping[str, object]]] = []
    def scanned():
        for artifact in _physical_artifact_candidates(conn, row, deadline=deadline):
            basis = _repair_basis_for_row(artifact, row)
            if basis is not None:
                repairs.append((artifact, basis))
            yield artifact
    selected = _physical_artifact_at_cutoff(row, scanned())
    return json.dumps(_select_observed_receipt_repair(conn, row, selected, deadline=deadline, repairs=repairs), separators=(",", ":"))


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
    day0_remaining_from_iso: str | None = None,
) -> list[sqlite3.Row]:
    """Read the complete production target-family candidate stream."""

    rows, deadline = _read_source_clock_candidates(
        conn,
        city=city,
        metric=metric,
        target_date=target_date,
        decision_iso=decision_iso,
        schema=schema,
        max_substitution_age_hours=max_substitution_age_hours,
        single_runs_only=single_runs_only,
        day0_remaining_from_iso=day0_remaining_from_iso,
    )
    try:
        return [(*row[:-1], _read_product_identity_at_cutoff(conn, row[-1], deadline_monotonic=deadline)) for row in rows]
    except sqlite3.OperationalError as exc:
        _raise_typed_read_unavailable(exc)
        raise AssertionError("unreachable")


def _read_source_clock_candidates(
    conn: sqlite3.Connection,
    *,
    city: str,
    metric: str,
    target_date: str,
    decision_iso: str,
    schema: CurrentValueServingSchema,
    max_substitution_age_hours: float,
    single_runs_only: bool = False,
    day0_remaining_from_iso: str | None = None,
) -> tuple[list[sqlite3.Row], float]:
    """Read ordered, unproved candidates with their one family scan deadline."""

    sql, params = _source_clock_rows_query(
        city=city,
        metric=metric,
        target_date=target_date,
        decision_iso=decision_iso,
        schema=schema,
        max_substitution_age_hours=max_substitution_age_hours,
        single_runs_only=single_runs_only,
        day0_remaining_from_iso=day0_remaining_from_iso,
    )
    try:
        deadline = time.monotonic() + _PHYSICAL_CAPTURE_SCAN_BUDGET_SECONDS
        rows = conn.execute(sql, params).fetchall()
        return rows, deadline
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
    day0_remaining_from_iso: str | None = None,
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
    product_select = _product_identity_select(schema, decision_iso=decision_iso,
        day0_remaining_from_iso=day0_remaining_from_iso)
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
        row = _physical_artifact_at_cutoff(row)
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
            view = _station_capture_view(row) if typed else None
            return view is not None and _station_response_has_authority(view)
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


def _resolve_http_capture_receipt(row: Mapping[str, object]) -> dict[str, object] | None:
    """Bind a network event to canonical immutable body, without renewing raw clocks."""
    artifact = row.get("physical_artifact")
    if not isinstance(artifact, dict) or artifact.get("data_version") != "openmeteo_single_model_http_capture_receipt_v1":
        return dict(row)
    try:
        import hashlib
        from pathlib import Path
        path = Path(str(artifact["artifact_path"]))
        metadata = json.loads(str(artifact["metadata"]))
        is_new_protocol = isinstance(metadata, Mapping) and "canonical_recording" in metadata
        if is_new_protocol and (type(artifact["byte_size"]) is not int or not 0 < artifact["byte_size"] <= _HTTP_REPAIR_MAX_BYTES
                or path.stat().st_size != artifact["byte_size"]):
            return None
        encoded = path.read_bytes()
        if len(encoded) != artifact["byte_size"] or hashlib.sha256(encoded).hexdigest() != artifact["sha256"]:
            return None
        receipt = json.loads(encoded)
        repair_bases = receipt.get("repair_bases")
        if repair_bases is None:
            if json.loads(str(artifact["metadata"])) != {"physical_http_capture_receipt": receipt}:
                return None
        else:
            receipt = json.loads(encoded, object_pairs_hook=_repair_unique_json_fields)
            repair_bases = receipt["repair_bases"]
            if not isinstance(repair_bases, dict) or not repair_bases or "recorded_at" in receipt:
                return None
            own_basis = repair_bases.get(str(row["raw_model_forecast_id"]))
            for raw_id, basis in repair_bases.items():
                if (not isinstance(raw_id, str) or not raw_id.isascii() or not raw_id.isdigit() or raw_id != str(int(raw_id)) or int(raw_id) <= 0
                        or type(basis["raw_identity"]["raw_model_forecast_id"]) is not int
                        or str(basis["raw_identity"]["raw_model_forecast_id"]) != raw_id
                        or basis["revision"] != _HTTP_REPAIR_BASIS_REVISION or basis["clock_resolution"] != "milliseconds"):
                    return None
            if own_basis is not None and own_basis["raw_identity"] != _repair_raw_identity(row):
                return None
            prepared = _repair_stamp(receipt["prepared_at"])
            captured, recorded = _repair_stamp(artifact["captured_at"]), _repair_stamp(artifact["recorded_at"])
            descriptor = {"clock_role": "actual_sqlite_insert", "prepared_at": receipt["prepared_at"],
                "receipt_sha256": artifact["sha256"], "body_artifact_id": receipt["body_artifact_id"],
                "repair_bases_sha256": _repair_sha(repair_bases), "recorded_at": artifact["recorded_at"]}
            observed = [_repair_stamp(basis["observed_at"]) for basis in repair_bases.values()]
            if (any(stamp.microsecond % 1000 or not stamp <= captured <= recorded or not stamp <= prepared <= recorded for stamp in observed)
                    or prepared.microsecond % 1000 or recorded.microsecond % 1000 or prepared > recorded
                    or json.loads(str(artifact["metadata"])) != {"physical_http_capture_receipt": receipt, "canonical_recording": descriptor}):
                return None
        body = artifact["body_artifact"]
        if not isinstance(body, dict) or body.get("data_version") != "openmeteo_single_model_entity_body_v1":
            return None
        if receipt["revision"] != artifact["data_version"] or receipt["body_artifact_id"] != body["artifact_id"] or receipt["body_sha256"] != body["sha256"] or receipt["body_byte_size"] != body["byte_size"]:
            return None
        for key in ("source_id", "product_id", "source_cycle_time", "request_url"):
            if receipt[key] != artifact[key] or receipt[key] != body[key]:
                return None
        if receipt["request_params"] != json.loads(str(artifact["request_params_json"])) or receipt["request_params"] != json.loads(str(body["request_params_json"])):
            return None
        for key in ("captured_at", "source_available_at", "recorded_at"):
            if key == "recorded_at" and repair_bases is not None:
                continue
            if receipt[key] != artifact[key]:
                return None
        proof = receipt["physical_response"]
        if proof["sha256"] != body["sha256"] or proof["byte_size"] != body["byte_size"] or proof["request_params"] != receipt["request_params"] or proof["request_url"] != receipt["request_url"] or proof["captured_at"] != receipt["captured_at"]:
            return None
        if proof["network_capture"] != {"captured_at": receipt["captured_at"], "response_headers": receipt["response_headers"]}:
            return None
        for key in ("source_cycle_time", "source_available_at", "captured_at", "recorded_at"):
            if datetime.fromisoformat(str(body[key]).replace("Z", "+00:00")) > datetime.fromisoformat(str(artifact["recorded_at"]).replace("Z", "+00:00")):
                return None
        normalized = {**body, **{key: artifact[key] for key in ("source_available_at", "captured_at", "recorded_at")},
            "metadata": json.dumps({"physical_response": proof}),
            "capture_receipt_artifact_id": artifact["artifact_id"], "capture_receipt_sha256": artifact["sha256"]}
        result = {**row, "physical_artifact": normalized, "revalidated_physical_capture": True,
            "frozen_http_capture_receipt":artifact,
            "recorded_body_artifact_id": row.get("artifact_id"), "recorded_raw_sha256": row.get("raw_sha256")}
        if row.get("artifact_id") is not None or row.get("elevation_param") == "default_90m_dem":
            result.update(artifact_id=body["artifact_id"], raw_sha256=body["sha256"])
        return result
    except (KeyError, TypeError, ValueError, OSError):
        return None


def _revalidated_legacy_product_row(row: Mapping[str, object]) -> dict[str, object] | None:
    """A typed view over immutable raw, never a legacy-label compatibility gate."""
    resolved = _resolve_http_capture_receipt(row)
    if resolved is None:
        return None
    row = resolved
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
        if row.get("revalidated_legacy_product", False) or row.get("revalidated_physical_capture", False):
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


def _legacy_hko_context(row: Mapping[str, object]) -> bool:
    """Only the former metric-local HKO transport, never generic NULL-coordinate rows."""
    try:
        import hashlib
        from src.config import runtime_cities_by_name
        from src.data.station_forecast_adapter import _HKO_ENDPOINT
        city = runtime_cities_by_name().get(str(row["city"]))
        if (city is None or city.settlement_source_type != "hko" or row["metric"] not in ("high", "low")
                or row.get("artifact_id") is not None or row.get("raw_sha256") is not None
                or any(row[key] != value for key, value in {
                    "model": "hko_fnd", "provider": "hong_kong_observatory",
                    "source_id": "hko_fnd_single_runs", "product_id": "hko_fnd::single_runs",
                    "source_family": "station_official_forecast", "model_name": "hko_fnd",
                    "endpoint": "single_runs", "endpoint_mode": "single_runs",
                    "cell_selection": "station_official_forecast", "elevation_param": "station",
                    "downscaling_policy": "agency_mos", "timezone_requested": city.timezone,
                }.items())):
            return False
        params = {"city": row["city"], "dataType": "fnd", "lang": "en",
                  "metric": row["metric"], "timezone": city.timezone}
        encoded = json.dumps(params, sort_keys=True, separators=(",", ":"))
        if json.loads(str(row["request_params_json"])) != params or row["request_url_hash"] != hashlib.sha256(f"{_HKO_ENDPOINT}?{encoded}".encode()).hexdigest():
            return False
        domain = {"provider": "hong_kong_observatory", "model_name": "hko_fnd", "city": row["city"],
                  "endpoint_mode": "single_runs", "cell_selection": "station_official_forecast"}
        if row["model_domain_hash"] != hashlib.sha256(json.dumps(domain, sort_keys=True, separators=(",", ":")).encode()).hexdigest():
            return False
        lat, lon = row["latitude_requested"], row["longitude_requested"]
        return (lat is None and lon is None) or (lat == city.lat and lon == city.lon)
    except (KeyError, TypeError, ValueError):
        return False


def _station_capture_view(row: Mapping[str, object]) -> dict[str, object] | None:
    if row.get("artifact_id") is not None:
        return dict(row)
    if not _legacy_hko_context(row):
        return None
    try:
        from datetime import timedelta
        from src.data.station_forecast_adapter import _HKO_ENDPOINT
        artifact = row["physical_artifact"]
        if (not isinstance(artifact, Mapping) or artifact["data_version"] != "station_forecast_entity_body_v1"
                or artifact["request_url"] != _HKO_ENDPOINT):
            return None
        params = json.loads(str(artifact["request_params_json"]))
        if params != {"city": row["city"], "dataType": "fnd", "lang": "en", "timezone": row["timezone_requested"]}:
            return None
        decision = datetime.fromisoformat(str(row["physical_proof_cutoff"]).replace("Z", "+00:00"))
        clocks = []
        recorded_upper = None
        for key in ("source_cycle_time", "source_available_at", "captured_at", "recorded_at"):
            stamp = datetime.fromisoformat(str(row[key]).replace("Z", "+00:00"))
            if key == "recorded_at" and stamp.tzinfo is None:
                if stamp.strftime("%Y-%m-%d %H:%M:%S") != row[key]:
                    return None
                stamp = stamp.replace(tzinfo=timezone.utc)
                # The former writer's SQLite CURRENT_TIMESTAMP is UTC seconds,
                # not an exact instant preceding its own microsecond capture.
                recorded_upper = stamp + timedelta(seconds=1)
                if recorded_upper > decision:
                    return None
            if stamp.tzinfo is None or stamp > decision:
                return None
            clocks.append(stamp)
        if (not clocks[0] <= clocks[1] <= clocks[2]
                or (clocks[2] >= recorded_upper if recorded_upper is not None else clocks[2] > clocks[3])):
            return None
        return {**row, "artifact_id": artifact["artifact_id"], "raw_sha256": artifact["sha256"],
            **{key: artifact[key] for key in ("source_available_at", "captured_at", "recorded_at")},
            "recorded_station_product_identity": {key: row[key] for key in _PRODUCT_IDENTITY_COLUMNS}}
    except (KeyError, TypeError, ValueError):
        return None


def _remaining_window_boundary(row: Mapping[str, object]) -> str:
    """The causal boundary of a stored body's local-day coverage re-parse.

    Day0 law: the remaining window is keyed on the LAST OBSERVATION, never the
    decision clock. The decision cut stands in for it only while the decision is
    inside the local day, where the two own the same suffix. Once the decision
    passes local-day end, the family's last authorized observation tau, when it
    lies inside the day and was possessed by the cut, is the boundary: the run
    owns [tau, day end), which it could prove at tau. No tau, or tau at/after day
    end, keeps the decision cut (whole-day law after day end).
    """
    cut = str(row.get("physical_proof_cutoff") or row["captured_at"])
    tau_raw = row.get("day0_remaining_from")
    if tau_raw is None:
        return cut
    try:
        from datetime import date
        from src.data.forecast_target_contract import compute_target_local_day_window_utc
        decision = datetime.fromisoformat(cut.replace("Z", "+00:00"))
        tau = datetime.fromisoformat(str(tau_raw).replace("Z", "+00:00"))
        if decision.tzinfo is None or tau.tzinfo is None:
            return cut
        day = compute_target_local_day_window_utc(city_timezone=str(row["timezone_requested"]),
            target_local_date=date.fromisoformat(str(row["target_date"])))
        if decision < day.end_utc or not day.start_utc <= tau < day.end_utc or tau > decision:
            return cut
        return tau.isoformat()
    except (KeyError, TypeError, ValueError):
        return cut


def recorded_openmeteo_identity_has_authority(row: Mapping[str, object], artifact: object) -> bool:
    """The body-free half of the Open-Meteo proof: the row's recorded product.

    The row is bound to its recorded entity body (sha, source, product, cycle),
    fetched from its endpoint mode's URL; that capture recorded this row's model
    and requested the matching Open-Meteo model, which is the row's model_name.
    Only DB-recorded fields are read: no body bytes, no clocks against a
    decision, no current live request policy.
    """
    try:
        from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
        from src.data.openmeteo_ecmwf_ifs9_anchor import SINGLE_RUNS_FORECAST_URL, STANDARD_FORECAST_URL
        from src.data.openmeteo_client import PREVIOUS_RUNS_URL
        if not isinstance(artifact, dict) or artifact["sha256"] != row["raw_sha256"]:
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
        model = str(row["model"])
        return (metadata["revision"] == "openmeteo_single_model_entity_body_v1" and params == metadata["request_params"]
                and metadata["model"] == model and params["models"] == OPENMETEO_MODEL_IDS.get(model, model)
                and row.get("model_name", params["models"]) == params["models"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _physical_response_has_authority(row: Mapping[str, object], *, _require_surface: bool = True) -> bool:
    """Verify actual single-model product and exact hourly/local-day value, not request intention."""
    try:
        from pathlib import Path
        import hashlib
        from src.data.bayes_precision_fusion_download import _parse_batched_single_runs_payload
        from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
        artifact = row["physical_artifact"]
        if not recorded_openmeteo_identity_has_authority(row, artifact):
            return False
        if not _physical_proof_clocks_have_authority(row, artifact):
            return False
        metadata = json.loads(str(artifact["metadata"]))["physical_response"]
        params = json.loads(str(artifact["request_params_json"]))
        model = str(row["model"])
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
        if not math.isclose(float(geometry["requested_latitude"]), float(row["latitude_requested"]), abs_tol=1e-6) or not math.isclose(float(geometry["requested_longitude"]), float(row["longitude_requested"]), abs_tol=1e-6) or geometry["timezone"] != row["timezone_requested"]:
            return False
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
        payloads = [decoded] if isinstance(decoded, dict) else decoded
        if not isinstance(payloads, list) or len(metadata["locations"]) != len(payloads) or len(str(params["latitude"]).split(",")) != len(payloads):
            return False
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
            decision_at=_remaining_window_boundary(row))
        high_c, low_c = values[model]
        expected = high_c if row["metric"] == "high" else low_c if row["metric"] == "low" else None
        return (expected is not None and math.isclose(float(expected), float(row["forecast_value_c"]), abs_tol=1e-9)
            and (not _require_surface or _current_model_surface_witness(row, geometry, artifact) is not None))
    except (KeyError, IndexError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return False


def station_ground_target_coverage_for_city(evidence: object, *, city: str, target_date: object,
        decision_at: object) -> dict[str, object]:
    """Derive the physical window from the settlement city's real local calendar."""
    from datetime import date
    from src.config import runtime_cities_by_name
    from src.data.forecast_target_contract import compute_target_local_day_window_utc
    from src.data.station_ground_evidence import station_ground_target_coverage
    city_object = runtime_cities_by_name().get(city)
    if city_object is None:
        raise ValueError("station ground target city unavailable")
    window = compute_target_local_day_window_utc(city_timezone=str(city_object.timezone),
        target_local_date=date.fromisoformat(str(target_date)))
    return station_ground_target_coverage(evidence,decision_at=decision_at,
        target_start_utc=window.start_utc,target_end_utc=window.end_utc)


_HTTP_REPAIR_BASIS_REVISION = "openmeteo_observed_http_receipt_repair_v1"
_HTTP_REPAIR_SQL_CLOCK = "strftime('%Y-%m-%dT%H:%M:%f+00:00','now')"
_HTTP_REPAIR_MAX_BYTES = 1024 * 1024


def _repair_stamp(value: object) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("physical_capture_repair:unproven_clock")
    return result.astimezone(timezone.utc)


def _repair_sql_now(conn: sqlite3.Connection) -> str:
    return str(conn.execute(f"SELECT {_HTTP_REPAIR_SQL_CLOCK}").fetchone()[0])


def _repair_sha(value: object) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _repair_unique_json_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("physical_capture_repair:duplicate_JSON_key")
        result[key] = value
    return result


def _repair_raw_identity(row: Mapping[str, object]) -> dict[str, object]:
    return {key: row.get(key) for key in _PRODUCT_IDENTITY_COLUMNS}


@dataclass(frozen=True)
class PhysicalCaptureRepairBasis:
    """Producer-observed cost basis. It grants no HTTP, source-age or q authority by itself."""

    raw_identity: Mapping[str, object]
    original_body_artifact: Mapping[str, object]
    observed_at: str
    receipt_prefix: Mapping[str, object]
    unknown_bad_prefix: Mapping[str, object]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"revision": _HTTP_REPAIR_BASIS_REVISION,
            "raw_identity": dict(self.raw_identity), "original_body_artifact": dict(self.original_body_artifact),
            "observed_at": self.observed_at, "clock_resolution": "milliseconds",
            "receipt_prefix": dict(self.receipt_prefix), "unknown_bad_prefix": dict(self.unknown_bad_prefix),
            "reason": self.reason}


def _unbounded_bad_http_receipt(row: Mapping[str, object], artifact: Mapping[str, object], observed_at: datetime) -> bool:
    """Only a registered, unorderable bad receipt; never a known future or valid causal event."""
    try:
        from src.data.openmeteo_ecmwf_ifs9_anchor import SINGLE_RUNS_FORECAST_URL
        from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
        if artifact["data_version"] != "openmeteo_single_model_http_capture_receipt_v1" or artifact["request_url"] != SINGLE_RUNS_FORECAST_URL:
            return False
        params = json.loads(str(artifact["request_params_json"]))
        if (params.get("models") != OPENMETEO_MODEL_IDS.get(str(row["model"]), row["model"])
                or params.get("temperature_unit") != "celsius" or params.get("cell_selection") != "land"):
            return False
        run = datetime.fromisoformat(str(params["run"]))
        if run.tzinfo is None:
            run = run.replace(tzinfo=timezone.utc)
        if run != _repair_stamp(row["source_cycle_time"]):
            return False
        # Each independently known future clock keeps its causal boundary even
        # when recording is unknown and the sealed receipt file has been lost.
        for key in ("source_available_at", "captured_at"):
            try:
                if _repair_stamp(artifact[key]) > observed_at:
                    return False
            except (TypeError, ValueError):
                pass
        # A parseable recording clock keeps its original causal/future ordering.
        try:
            _repair_stamp(artifact["recorded_at"])
            return False
        except (TypeError, ValueError):
            pass
        own_bound = _receipt_canonical_recorded_bound(artifact)
        return own_bound is None or own_bound <= observed_at
    except (KeyError, TypeError, ValueError):
        return False


def _repair_frontier(conn: sqlite3.Connection, row: Mapping[str, object], *, observed_at: datetime,
        deadline: float, max_artifact_id: int | None = None,
        _same_event_receipt_id: int | None = None) -> tuple[dict[str, object], dict[str, object]]:
    import hashlib
    full, unknown = hashlib.sha256(), hashlib.sha256()
    count = bad_count = maximum = 0
    for artifact in _physical_artifact_candidates(conn, row, deadline=deadline):
        if artifact["data_version"] != "openmeteo_single_model_http_capture_receipt_v1":
            continue
        aid = int(artifact["artifact_id"])
        if aid == _same_event_receipt_id:
            continue
        if max_artifact_id is not None and aid > max_artifact_id:
            continue
        encoded = json.dumps({"artifact_id": aid, "descriptor_sha256": _repair_sha(artifact)},
            sort_keys=True, separators=(",", ":")).encode() + b"\n"
        full.update(encoded)
        count += 1
        maximum = max(maximum, aid)
        if _unbounded_bad_http_receipt(row, artifact, observed_at):
            unknown.update(encoded)
            bad_count += 1
    if time.monotonic() >= deadline:
        raise CurrentValueServingReadUnavailable("physical_capture_scan_budget_exceeded")
    return ({"row_count": count, "max_artifact_id": maximum, "descriptor_sha256": full.hexdigest()},
        {"row_count": bad_count, "descriptor_sha256": unknown.hexdigest()})


def _repair_original_row(conn: sqlite3.Connection, raw_id: int, cut: str) -> dict[str, object] | None:
    if type(raw_id) is not int or raw_id <= 0:
        return None
    schema = current_value_serving_schema(conn)
    cursor = conn.execute(f"SELECT {_product_identity_select(schema, decision_iso=cut)}"
        " FROM raw_model_forecasts WHERE raw_model_forecast_id=? AND coverage_status='COVERED' AND training_allowed=0", (raw_id,))
    item = cursor.fetchone()
    return None if item is None else json.loads(str(item[0]))


def observe_physical_capture_repair_basis(conn: sqlite3.Connection, *, raw_model_forecast_id: int,
        decision_time_iso: str, deadline_monotonic: float | None = None) -> PhysicalCaptureRepairBasis | None:
    """Independently observe an exact old unknown-bad frontier at actual SQL now.

    SCOPE one immutable raw request/target; DRAIN quota-bound actual 200; RESET
    only a durable new receipt accepted at its new cut. Historical caller cuts
    never renew raw age; no caller supplies the observation clock or frontier.
    """
    deadline = time.monotonic() + _PHYSICAL_CAPTURE_SCAN_BUDGET_SECONDS
    if deadline_monotonic is not None:
        deadline = min(deadline, deadline_monotonic)
    try:
        knowledge = _repair_stamp(decision_time_iso)
        row = _repair_original_row(conn, raw_model_forecast_id, decision_time_iso)
        if row is None or not isinstance(row.get("physical_artifact"), Mapping):
            return None
        if isinstance(row.get("forecast_value_c"), bool) or not isinstance(row.get("forecast_value_c"), (int, float)) or not math.isfinite(row["forecast_value_c"]):
            return None
        if any(_repair_stamp(row[key]) > knowledge for key in ("source_cycle_time", "source_available_at", "captured_at", "recorded_at")):
            return None
        # SQL now, not decision_time_iso, is the eligibility/age clock.
        eligibility_now = _repair_sql_now(conn)
        row = _repair_original_row(conn, raw_model_forecast_id, eligibility_now)
        current = json.loads(_read_product_identity_at_cutoff(conn, json.dumps(row), deadline_monotonic=deadline))
        if _source_clock_product_has_authority(json.dumps(current), lead_days=int(row["lead_days"])):
            return None
        canonical = _physical_artifact_at_cutoff(row, _physical_artifact_candidates(conn, row, deadline=deadline))
        if (not isinstance(canonical.get("physical_artifact"), Mapping)
                or _receipt_canonical_recorded_bound(canonical["physical_artifact"]) is not None
                or not _unbounded_bad_http_receipt(row, canonical["physical_artifact"], _repair_stamp(eligibility_now))):
            return None
        reason = _physical_capture_debt_reason(conn, raw_model_forecast_id=raw_model_forecast_id,
            decision_time_iso=eligibility_now, deadline_monotonic=deadline, _allow_unknown_receipt=True)
        if reason not in {"HTTP_CAPTURE_RECEIPT_MISSING", "ENTITY_BODY_MISSING"}:
            return None
        frontier, bad = _repair_frontier(conn, row, observed_at=_repair_stamp(eligibility_now), deadline=deadline)
        if not bad["row_count"]:
            return None
        # Verification and complete canonical observation precede this SQL-ms sample.
        observed = _repair_sql_now(conn)
        basis = PhysicalCaptureRepairBasis(_repair_raw_identity(row), row["physical_artifact"], observed, frontier, bad, reason)
        validate_physical_capture_repair_basis(conn, basis, deadline_monotonic=deadline)
        return basis
    except (KeyError, TypeError, ValueError, OSError):
        return None


def validate_physical_capture_repair_basis(conn: sqlite3.Connection, basis: PhysicalCaptureRepairBasis,
        *, deadline_monotonic: float | None = None, _same_event_receipt_id: int | None = None) -> None:
    """Recheck canonical facts, never accept a payload's self-reported cost permission."""
    if not isinstance(basis, PhysicalCaptureRepairBasis):
        raise ValueError("physical_capture_repair:producer_basis_required")
    deadline = time.monotonic() + _PHYSICAL_CAPTURE_SCAN_BUDGET_SECONDS
    if deadline_monotonic is not None:
        deadline = min(deadline, deadline_monotonic)
    now = _repair_sql_now(conn)
    row = _repair_original_row(conn, int(basis.raw_identity["raw_model_forecast_id"]), now)
    if (row is None or _repair_raw_identity(row) != basis.raw_identity
            or row.get("physical_artifact") != basis.original_body_artifact
            or _repair_stamp(basis.observed_at) > _repair_stamp(now)):
        raise ValueError("physical_capture_repair:original_identity_changed")
    reason = _physical_capture_debt_reason(conn, raw_model_forecast_id=int(basis.raw_identity["raw_model_forecast_id"]),
        decision_time_iso=now, deadline_monotonic=deadline, _allow_unknown_receipt=True)
    if reason not in {"HTTP_CAPTURE_RECEIPT_MISSING", "ENTITY_BODY_MISSING"}:
        raise ValueError("physical_capture_repair:current_eligibility_lost")
    if _same_event_receipt_id is not None:
        item = conn.execute(f"SELECT {_ARTIFACT_IDENTITY_JSON_SQL} FROM raw_forecast_artifacts a WHERE artifact_id=?", (_same_event_receipt_id,)).fetchone()
        artifact = None if item is None else json.loads(str(item[0]))
        doc = {} if artifact is None else json.loads(str(artifact["metadata"])).get("physical_http_capture_receipt", {})
        resolved = None if artifact is None else _resolve_http_capture_receipt({**row, "physical_artifact": artifact})
        if (doc.get("repair_bases", {}).get(str(basis.raw_identity["raw_model_forecast_id"])) != basis.to_dict()
                or resolved is None or not _source_clock_product_has_authority(json.dumps(resolved), lead_days=int(row["lead_days"]))):
            raise ValueError("physical_capture_repair:unproved_same_event_receipt")
    frontier, bad = _repair_frontier(conn, row, observed_at=_repair_stamp(basis.observed_at), deadline=deadline,
        _same_event_receipt_id=_same_event_receipt_id)
    if frontier != basis.receipt_prefix or bad != basis.unknown_bad_prefix:
        raise ValueError("physical_capture_repair:frontier_changed")


def _repair_basis_for_row(artifact: Mapping[str, object], row: Mapping[str, object]) -> Mapping[str, object] | None:
    """The repair basis an HTTP capture receipt declares for this raw row, if any."""
    try:
        if artifact["data_version"] != "openmeteo_single_model_http_capture_receipt_v1":
            return None
        metadata = str(artifact["metadata"])
        if '"repair_bases"' not in metadata and "\\u" not in metadata:
            return None  # Without escapes, a repair_bases key can only appear verbatim.
        receipt = json.loads(metadata).get("physical_http_capture_receipt")
        if not isinstance(receipt, Mapping):
            return None
        bases = receipt.get("repair_bases")
        basis = bases.get(str(row["raw_model_forecast_id"])) if isinstance(bases, Mapping) else None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return basis if isinstance(basis, Mapping) else None


def _select_observed_receipt_repair(conn: sqlite3.Connection, row: Mapping[str, object], selected: dict[str, object], *,
        deadline: float, repairs: list[tuple[dict[str, object], Mapping[str, object]]]) -> dict[str, object]:
    """A new lawful event can dominate its observed unknowns, not other causal evidence.

    ``repairs`` holds, in scan order, every candidate receipt naming this row as
    a repair basis, collected during the selection scan.
    """
    selected_artifact = selected.get("physical_artifact")
    cut = _repair_stamp(row["physical_proof_cutoff"])
    needs_repair = isinstance(selected_artifact, Mapping) and _unbounded_bad_http_receipt(row, selected_artifact, cut)
    choice = None
    for artifact, basis in repairs:
        try:
            recorded = _repair_stamp(artifact["recorded_at"])
            if recorded > cut:
                continue
            if basis["raw_identity"] != _repair_raw_identity(row) or basis["original_body_artifact"] != row.get("physical_artifact"):
                raise ValueError("basis identity differs")
            frontier, bad = _repair_frontier(conn, row, observed_at=_repair_stamp(basis["observed_at"]),
                max_artifact_id=int(basis["receipt_prefix"]["max_artifact_id"]), deadline=deadline)
            if frontier != basis["receipt_prefix"] or bad != basis["unknown_bad_prefix"]:
                raise ValueError("basis prefix differs")
            resolved = _resolve_http_capture_receipt({**row, "physical_artifact": artifact})
            if resolved is None or not _source_clock_product_has_authority(json.dumps(resolved), lead_days=int(row["lead_days"])):
                raise ValueError("new repair event lacks authority")
            from pathlib import Path
            from src.data.station_ground_evidence import read_current_station_ground_evidence
            db_path = next((str(item[2]) for item in conn.execute("PRAGMA database_list") if item[1] == "main"), "")
            ground = read_current_station_ground_evidence(Path(db_path), city=str(row["city"]), decision_at=cut)
            if ground is None or station_ground_target_coverage_for_city(ground, city=str(row["city"]), target_date=row["target_date"], decision_at=cut)["status"] != "VERIFIED":
                raise ValueError("new repair event lacks ground")
            if choice is None or _physical_artifact_at_cutoff(row, (choice[0], artifact))["physical_artifact"]["artifact_id"] == artifact["artifact_id"]:
                choice = (artifact, basis)
        except (KeyError, TypeError, ValueError, OSError):
            if isinstance(selected_artifact, Mapping) and selected_artifact.get("artifact_id") == artifact.get("artifact_id"):
                return {**row, "physical_artifact": None}
    if choice is None:
        return selected
    # There is no second authority regime: retain the existing causal winner
    # rule, removing only independently rederived old unknown-bad candidates.
    newest, basis = choice
    if not needs_repair and not (isinstance(selected_artifact, Mapping) and selected_artifact.get("artifact_id") == newest["artifact_id"]):
        return selected
    observed = _repair_stamp(basis["observed_at"])
    def retained():
        for artifact in _physical_artifact_candidates(conn, row, deadline=deadline):
            if int(artifact["artifact_id"]) <= int(basis["receipt_prefix"]["max_artifact_id"]) and _unbounded_bad_http_receipt(row, artifact, observed):
                continue
            yield artifact
    return _physical_artifact_at_cutoff(row, retained())


def physical_capture_debt_reason(conn: sqlite3.Connection, *, raw_model_forecast_id: int,
        decision_time_iso: str, deadline_monotonic: float | None = None) -> str | None:
    reason = _physical_capture_debt_reason(conn, raw_model_forecast_id=raw_model_forecast_id,
        decision_time_iso=decision_time_iso, deadline_monotonic=deadline_monotonic)
    basis = observe_physical_capture_repair_basis(conn, raw_model_forecast_id=raw_model_forecast_id,
        decision_time_iso=decision_time_iso, deadline_monotonic=deadline_monotonic)
    if basis is not None:
        return basis.reason
    # An unknown current frontier cannot inherit a cost grant from an older
    # caller cut when actual-now observation rejected its eligibility.
    row = _repair_original_row(conn, raw_model_forecast_id, _repair_sql_now(conn))
    if row is not None:
        deadline = time.monotonic() + _PHYSICAL_CAPTURE_SCAN_BUDGET_SECONDS
        if deadline_monotonic is not None:
            deadline = min(deadline, deadline_monotonic)
        canonical = _physical_artifact_at_cutoff(row, _physical_artifact_candidates(conn, row, deadline=deadline))
        if (isinstance(canonical.get("physical_artifact"), Mapping)
                and _receipt_canonical_recorded_bound(canonical["physical_artifact"]) is None
                and _unbounded_bad_http_receipt(row, canonical["physical_artifact"], _repair_stamp(row["physical_proof_cutoff"]))):
            return None
    return reason


def _physical_capture_debt_reason(
    conn: sqlite3.Connection, *, raw_model_forecast_id: int,
    decision_time_iso: str, deadline_monotonic: float | None = None, _allow_unknown_receipt: bool = False,
) -> str | None:
    """Classify one recoverable same-issued producer debt, never source authority.

    SCOPE: this immutable raw ID and its exact model/run/request family. DRAIN:
    the ordinary quota-bound producer obtains a real 200 entity. RESET requires
    normal serving to accept that entity; a returned reason grants no q authority.
    Ground, unsupported/sea cells, malformed products/clocks and intrinsic Day0
    suffixes are not network-repairable and must not authorize forced polling.
    """
    from pathlib import Path
    from zoneinfo import ZoneInfo
    from src.config import runtime_cities_by_name
    from src.data.bayes_precision_fusion_history_provider import raw_product_matches_live_source
    from src.data.replacement_forecast_cycle_policy import cycle_age_outside_bound
    from src.data.station_ground_evidence import read_current_station_ground_evidence
    from src.data.openmeteo_model_surface import (
        read_model_surface_capture, model_surface_witness, validate_model_surface_witness,
    )

    def stamp(value: object, *, sqlite_utc: bool = False) -> datetime:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None and sqlite_utc:
            result = result.replace(tzinfo=timezone.utc)
        if result.tzinfo is None:
            raise ValueError("unproven source clock")
        return result.astimezone(timezone.utc)

    if isinstance(raw_model_forecast_id, bool) or not isinstance(raw_model_forecast_id, int) or raw_model_forecast_id <= 0:
        return None
    try:
        decision = stamp(decision_time_iso)
        schema = current_value_serving_schema(conn)
        if not schema.has_artifacts or set(_PRODUCT_IDENTITY_COLUMNS) - set(schema.product_identity_columns):
            return None
        cursor = conn.execute(
            f"SELECT {_product_identity_select(schema, decision_iso=decision.isoformat())},coverage_status,training_allowed"
            " FROM raw_model_forecasts WHERE raw_model_forecast_id=?", (raw_model_forecast_id,),
        )
        item = cursor.fetchone()
        if item is None or item[1] != "COVERED" or item[2] != 0:
            return None
        raw = json.loads(str(item[0]))
        model = str(raw["model"])
        city = runtime_cities_by_name().get(str(raw["city"]))
        if city is None or _is_station_model(model) or raw["metric"] not in ("high", "low") or raw["endpoint_mode"] != "single_runs":
            return None
        clocks = [stamp(raw[key], sqlite_utc=key == "recorded_at") for key in
                  ("source_cycle_time", "source_available_at", "captured_at", "recorded_at")]
        if not clocks[0] <= clocks[1] <= clocks[2] <= clocks[3] <= decision or cycle_age_outside_bound(decision, clocks[0]):
            return None
        # A source-clock suffix is not a full-day scalar, even if its body is
        # now missing. Re-fetching the same late run cannot repair that product.
        day_start = datetime.fromisoformat(str(raw["target_date"])).replace(tzinfo=ZoneInfo(str(city.timezone)))
        if clocks[0] > day_start.astimezone(timezone.utc):
            return None
        view = dict(raw)
        if raw["elevation_param"] == "requested" and raw["downscaling_policy"] == "none":
            from src.data.bayes_precision_fusion_download import (
                BAYES_PRECISION_FUSION_ELEVATION_PARAM, BAYES_PRECISION_FUSION_DOWNSCALING_POLICY, _model_domain_hash,
            )
            basis = dict(provider=str(raw["provider"]), model_name=str(raw["model_name"]),
                         cell_selection=str(raw["cell_selection"]), endpoint_mode="single_runs")
            if raw["model_domain_hash"] != _model_domain_hash(**basis, elevation_param="requested", downscaling_policy="none"):
                return None
            view.update(elevation_param=BAYES_PRECISION_FUSION_ELEVATION_PARAM,
                downscaling_policy=BAYES_PRECISION_FUSION_DOWNSCALING_POLICY,
                model_domain_hash=_model_domain_hash(**basis, elevation_param=BAYES_PRECISION_FUSION_ELEVATION_PARAM,
                    downscaling_policy=BAYES_PRECISION_FUSION_DOWNSCALING_POLICY))
        if not raw_product_matches_live_source(view, city, lead_days=int(raw["lead_days"])):
            return None
        db_path = next((str(row[2]) for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
        ground_entity = None if not db_path else read_current_station_ground_evidence(
            Path(db_path), city=str(raw["city"]), decision_at=decision)
        if ground_entity is None or station_ground_target_coverage_for_city(ground_entity,
            city=str(raw["city"]),target_date=raw["target_date"],decision_at=decision)["status"] != "VERIFIED":
            return None
        row = json.loads(_read_product_identity_at_cutoff(conn, item[0], deadline_monotonic=deadline_monotonic))
        if _allow_unknown_receipt:
            deadline = time.monotonic() + _PHYSICAL_CAPTURE_SCAN_BUDGET_SECONDS
            if deadline_monotonic is not None:
                deadline = min(deadline, deadline_monotonic)
            row = _physical_artifact_at_cutoff(raw, _physical_artifact_candidates(conn, raw, deadline=deadline))
        artifact = row.get("physical_artifact")
        if not isinstance(artifact, Mapping):
            # Only explicit supported domains may incur this one acquisition;
            # absence is not a guessed grid/surface permission.
            if model == "ecmwf_ifs":
                from src.data.openmeteo_ecmwf_ifs9_bucket_transport import source_geometry_static_prerequisite_reason
                return "ENTITY_BODY_MISSING" if source_geometry_static_prerequisite_reason() is None else None
            asset = read_model_surface_capture(model, decision_at=decision)
            return "ENTITY_BODY_MISSING" if asset.status == "READY" else None
        selected = row
        row = _revalidated_legacy_product_row(selected)
        invalid_http_receipt = False
        if row is None and artifact.get("data_version") == "openmeteo_single_model_http_capture_receipt_v1":
            # This is a force-acquisition basis, NOT permission to serve the
            # older body. A corrupt latest append must still block current q.
            # Independent canonical possession and exact recorded request scope
            # bound the repair; the immutable raw issue/product remain strict.
            try:
                acquisition_bound=stamp(artifact["recorded_at"])
            except (TypeError,ValueError):
                acquisition_bound=_receipt_canonical_recorded_bound(artifact)
            if acquisition_bound is not None and acquisition_bound>decision:
                acquisition_bound=_receipt_canonical_recorded_bound(artifact)
            if (acquisition_bound is None and not _allow_unknown_receipt) or (acquisition_bound is not None and acquisition_bound > decision):
                return None
            row = _revalidated_legacy_product_row(raw)
            invalid_http_receipt = row is not None
        if row is None:
            return None
        artifact = row["physical_artifact"]
        if not _physical_proof_clocks_have_authority(row, artifact):
            return None
        metadata = json.loads(str(artifact["metadata"]))["physical_response"]
        params = json.loads(str(artifact["request_params_json"]))
        from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
        from src.data.openmeteo_ecmwf_ifs9_anchor import SINGLE_RUNS_FORECAST_URL
        if (artifact["data_version"] != "openmeteo_single_model_entity_body_v1"
            or artifact["request_url"] != SINGLE_RUNS_FORECAST_URL
            or metadata["model"] != model or metadata["request_params"] != params
            or params["models"] != OPENMETEO_MODEL_IDS.get(model, model)
            or any(artifact[key] != row[key] for key in ("source_id", "product_id", "source_cycle_time"))):
            return None
        indices = [i for i, (lat, lon, tz) in enumerate(zip(str(params["latitude"]).split(","),
            str(params["longitude"]).split(","), str(params["timezone"]).split(","), strict=True))
            if math.isclose(float(lat), float(city.lat), abs_tol=1e-6)
            and math.isclose(float(lon), float(city.lon), abs_tol=1e-6) and tz == city.timezone]
        if len(indices) != 1:
            return None
        geometry = metadata["locations"][indices[0]]
        captured = stamp(artifact["captured_at"])
        epoch_after_body = False
        if model == "ecmwf_ifs":
            if _current_model_surface_witness(row, geometry, artifact) is None:
                return None
        else:
            asset = read_model_surface_capture(model, decision_at=decision)
            if asset.status != "READY":
                return None
            epoch = stamp(asset.asset["last_modified"])
            epoch_after_body = epoch > captured
            # Verify this model's exact actual selected cell against the newly
            # possessed version even when its epoch requires a fresh body.
            prospective_capture = max(epoch, captured)
            witness = model_surface_witness(model, selected_latitude=float(geometry["selected_latitude"]),
                selected_longitude=float(geometry["selected_longitude"]), body_captured_at=prospective_capture,
                asset_capture=asset)
            if validate_model_surface_witness(witness, model=model,
                selected_latitude=float(geometry["selected_latitude"]), selected_longitude=float(geometry["selected_longitude"]),
                body_captured_at=prospective_capture, decision_at=decision) is not None:
                return None
        if not Path(str(artifact["artifact_path"])).exists():
            return "ENTITY_BODY_MISSING"
        if not _physical_response_has_authority(row, _require_surface=False):
            return None
        if epoch_after_body:
            return "MODEL_SURFACE_EPOCH_AFTER_BODY"
        if invalid_http_receipt or artifact.get("capture_receipt_artifact_id") is None:
            return "HTTP_CAPTURE_RECEIPT_MISSING"
        return None
    except (KeyError, IndexError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return None


def day0_remaining_from_provenance(
    provenance: Mapping[str, object], *, city: str, target_date: object,
    metric: str, posterior_computed_at: object,
) -> tuple[str | None, str | None]:
    """Recover the exact source window; absent and malformed are distinct.

    SCOPE: one immutable family/certificate. DRAIN: normal recomputation records
    valid conditioning geometry. RESET: a replacement valid certificate, never
    a requirement that frozen provenance equal an always-newest observation.
    """
    from datetime import date
    from src.data.forecast_target_contract import (
        compute_target_local_day_window_utc, day0_remaining_from_iso_of,
    )

    invalid = (None, "basis=current_value_serving_day0_window_unverifiable")
    taus: list[str] = []
    for key in ("day0_conditioning", "day0_provisional_observation"):
        if key not in provenance:
            continue
        context = provenance[key]
        if (isinstance(context, Mapping) and context.get("active") is False
                and context.get("observation_time") is None):
            continue  # No remaining-window declaration on an inactive proposal.
        if (not isinstance(context, Mapping) or context.get("active") is not True
                or context.get("metric") != metric):
            return invalid
        tau = day0_remaining_from_iso_of(context.get("observation_time"))
        if tau is None:
            return invalid
        taus.append(tau)
    fusion = provenance.get("bayes_precision_fusion")
    serving = fusion.get("current_value_serving") if isinstance(fusion, Mapping) else None
    if isinstance(serving, Mapping):
        ifs = serving.get("ecmwf_ifs")
        physical = ifs.get("physical_response") if isinstance(ifs, Mapping) else None
        frozen = physical.get("frozen_product_identity") if isinstance(physical, Mapping) else None
        # A frozen body without the key made no remaining-window claim (a full-target
        # body captured before the local day); it is not a conflict with the posterior tau.
        if isinstance(frozen, Mapping) and "day0_remaining_from" in frozen:
            frozen_tau = day0_remaining_from_iso_of(frozen.get("day0_remaining_from"))
            if frozen_tau is None or not taus:
                return invalid
            taus.append(frozen_tau)
    if not taus:
        return None, None  # Ordinary fulltarget semantics stay unchanged.
    if len(set(taus)) != 1:
        return invalid
    try:
        from src.config import runtime_cities_by_name
        city_config = runtime_cities_by_name().get(city)
        cut = day0_remaining_from_iso_of(posterior_computed_at)
        if city_config is None or cut is None or metric not in ("high", "low"):
            return invalid
        window = compute_target_local_day_window_utc(
            city_timezone=city_config.timezone, target_local_date=date.fromisoformat(str(target_date)),
        )
        tau_time = datetime.fromisoformat(taus[0])
        if not window.start_utc <= tau_time < window.end_utc or tau_time > datetime.fromisoformat(cut):
            return invalid
    except (KeyError, TypeError, ValueError):
        return invalid
    return taus[0], None


def physical_source_proof_dependency(proof: object) -> Mapping[str, object] | None:
    """Immutable possession dependencies, separate from stable physical geometry."""
    if not isinstance(proof, Mapping):
        return None
    result = {key: proof.get(key) for key in ("artifact_id", "entity_body_sha256",
        "capture_receipt_artifact_id", "capture_receipt_sha256")}
    surface = proof.get("model_surface_witness")
    if isinstance(surface, Mapping) and isinstance(surface.get("asset_audit"), Mapping):
        result["model_surface_asset"] = {key: surface["asset_audit"].get(key) for key in
            ("whole_sha256", "manifest_sha256", "etag", "last_modified", "s3_version_id")}
    if isinstance(surface, Mapping) and isinstance(surface.get("geometry"), Mapping):
        audit = surface["geometry"].get("static_asset_audit")
        if isinstance(audit, Mapping):
            result["ifs9_static_asset"] = {key:audit.get(key) for key in ("whole_sha256","manifest_sha256")}
    return result


def _current_model_surface_witness(row: Mapping[str, object], geometry: Mapping[str, object], artifact: Mapping[str, object]) -> Mapping[str, object] | None:
    model = str(row["model"])
    if model == "ecmwf_ifs":
        # Preserve the existing exact O1280 witness; no other provider may
        # borrow its surface or grid identity.
        proof = geometry.get("source_cell_geometry_proof")
        from src.data.openmeteo_ecmwf_ifs9_bucket_transport import validate_source_cell_geometry_proof
        # Cell admission (o1280_selected_cell_admissible) runs inside the
        # validator on facts replayed from the frozen static, never on the claim.
        if not isinstance(proof, Mapping) or proof.get("revision") != "openmeteo_ifs9_o1280_source_cell_v1":
            return None
        try:
            if row.get("physical_proof_cutoff") is None or validate_source_cell_geometry_proof(proof,
                latitude=float(geometry["selected_latitude"]), longitude=float(geometry["selected_longitude"]),
                target_elevation_m=float(geometry["target_dem_elevation_m"]),
                requested_latitude=float(row["latitude_requested"]),requested_longitude=float(row["longitude_requested"]),
                decision_at=row["physical_proof_cutoff"]) is not None:
                return None
            return {"revision": "openmeteo_ifs9_o1280_source_cell_v1", "status": "VERIFIED",
                "geometry":{**proof,"native_surface":"SEA" if proof["cell_is_sea"] else "LAND",
                    "native_grid_elevation_m":proof["raw_grid_elevation_m"]}}
        except (OSError, ValueError, TypeError):
            return None
    cutoff = row.get("physical_proof_cutoff")
    if cutoff is None:
        return None  # A carrier cycle is not a proof possession decision cutoff.
    from src.data.openmeteo_model_surface import (
        read_model_surface_capture, model_surface_witness, validate_model_surface_witness,
    )
    asset = read_model_surface_capture(model, decision_at=str(cutoff))
    proof = model_surface_witness(model, selected_latitude=float(geometry["selected_latitude"]),
        selected_longitude=float(geometry["selected_longitude"]), body_captured_at=str(artifact["captured_at"]), asset_capture=asset)
    if validate_model_surface_witness(proof, model=model, selected_latitude=float(geometry["selected_latitude"]),
        selected_longitude=float(geometry["selected_longitude"]), body_captured_at=str(artifact["captured_at"]), decision_at=str(cutoff)) is not None:
        return None
    return proof


def _physical_response_provenance(row: Mapping[str, object]) -> Mapping[str, object] | None:
    row = _physical_artifact_at_cutoff(row)
    original_identity = {key: row[key] for key in _PRODUCT_IDENTITY_COLUMNS}
    if row.get("day0_remaining_from") is not None:
        # The replay at the commit/held boundary re-proves the same body over the
        # same remaining window; absent tau, the frozen identity is unchanged.
        original_identity["day0_remaining_from"] = row["day0_remaining_from"]
    if not _is_station_model(str(row["model"])):
        row = _revalidated_legacy_product_row(row)
        if row is None:
            return None
    else:
        row = _station_capture_view(row)
        if row is None:
            return None
    artifact = row.get("physical_artifact")
    if not isinstance(artifact, dict):
        return None
    if _is_station_model(str(row["model"])):
        evidence = json.loads(str(artifact["metadata"]))["station_response"]
        proof = next(proof for proof in evidence["items"] if all(proof.get(key) == row[key]
            for key in ("model", "city", "metric", "target_date", "source_cycle_time", "provider")))
        return {**proof, "artifact_id": row["artifact_id"], "entity_body_sha256": artifact["sha256"],
                "proof_recorded_at": artifact["recorded_at"],
                **({"recorded_station_product_identity": row["recorded_station_product_identity"]}
                   if "recorded_station_product_identity" in row else {})}
    metadata = json.loads(str(artifact["metadata"]))["physical_response"]
    params = metadata["request_params"]
    index = next(i for i, (lat, lon, tz) in enumerate(zip(
        str(params["latitude"]).split(","), str(params["longitude"]).split(","),
        str(params["timezone"]).split(","), strict=True))
        if math.isclose(float(lat), float(row["latitude_requested"]), abs_tol=1e-6)
        and math.isclose(float(lon), float(row["longitude_requested"]), abs_tol=1e-6)
        and tz == row["timezone_requested"])
    surface = _current_model_surface_witness(row, metadata["locations"][index], artifact)
    surface_geometry = surface.get("geometry", {}) if isinstance(surface, Mapping) else {}
    return {"revision": metadata["revision"], "model": row["model"],
        "product_id": row["product_id"], "artifact_id": row["artifact_id"],
        "entity_body_sha256": artifact["sha256"],
        "capture_receipt_artifact_id": artifact.get("capture_receipt_artifact_id"),
        "capture_receipt_sha256": artifact.get("capture_receipt_sha256"),
        "recorded_body_artifact_id": row.get("recorded_body_artifact_id"),
        "recorded_raw_sha256": row.get("recorded_raw_sha256"),
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
        "representativeness_status": "UNPROVEN", **metadata["locations"][index],
        "model_surface_witness": surface,
        **({"frozen_product_identity":original_identity,
            "frozen_entity_body":dict(row.get("frozen_http_capture_receipt") or artifact)} if row["model"]=="ecmwf_ifs" else {}),
        **{key: surface_geometry[key] for key in ("native_surface", "native_grid_elevation_m") if key in surface_geometry}}


def provider_geometry_projection(proof: Mapping[str, object]) -> dict[str, object]:
    """Local physical facts only; immutable entity/event hashes remain audit dependencies."""
    keys = (
        "revision", "model", "product_id", "requested_latitude", "requested_longitude",
        "timezone", "cell_selection", "elevation_param", "downscaling_policy",
        "native_variable", "temperature_unit", "aggregation", "selected_latitude",
        "selected_longitude", "target_dem_elevation_m", "native_grid_elevation_m",
        "native_surface", "representativeness_status", "source_cell_geometry_proof",
        "city", "station_id", "quantity", "selection",
    )
    stable = {key: proof[key] for key in keys if key in proof}
    if isinstance(stable.get("source_cell_geometry_proof"), Mapping):
        stable["source_cell_geometry_proof"] = {key: value for key, value in stable["source_cell_geometry_proof"].items()
            if key not in ("static_hsurf_sha256", "static_asset_audit")}
    stable["product_id"] = str(stable.get("product_id", "")).split("::run=")[0]
    witness = proof.get("model_surface_witness")
    if isinstance(witness, Mapping) and isinstance(witness.get("geometry"), Mapping):
        stable["model_surface_geometry"] = {key: value for key, value in witness["geometry"].items()
            if key not in ("static_hsurf_sha256", "static_asset_audit")}
    if isinstance(stable.get("selection"), Mapping):
        stable["selection"] = {key: value for key, value in stable["selection"].items() if key != "forecast_date"}
    return stable


def frozen_ifs9_response_has_authority(physical: Mapping[str,object], geometry: Mapping[str,object], *, decision_at: object) -> bool:
    """The used IFS instrument must replay its own request/body/cell, not its anchor's cell."""
    try:
        row = {**physical["frozen_product_identity"], "physical_artifact":physical["frozen_entity_body"],
               "physical_proof_cutoff":str(decision_at)}
        if row["model"]!="ecmwf_ifs" or physical["model"]!="ecmwf_ifs" or row["raw_model_forecast_id"]!=physical["raw_model_forecast_id"]:
            return False
        if geometry != provider_geometry_projection(physical):
            return False
        resolved = _revalidated_legacy_product_row(row)
        if resolved is None:
            return False
        metadata = json.loads(str(resolved["physical_artifact"]["metadata"]))["physical_response"]
        params = metadata["request_params"]
        matches = [i for i,(lat,lon,tz) in enumerate(zip(str(params["latitude"]).split(","),
            str(params["longitude"]).split(","),str(params["timezone"]).split(","),strict=True))
            if float(lat)==float(row["latitude_requested"]) and float(lon)==float(row["longitude_requested"]) and tz==row["timezone_requested"]]
        if len(matches)!=1:
            return False
        location = metadata["locations"][matches[0]]
        proof = physical["source_cell_geometry_proof"]
        if proof!=location["source_cell_geometry_proof"]:
            return False
        stable = {key:value for key,value in proof.items() if key not in ("static_hsurf_sha256","static_asset_audit")}
        if geometry["source_cell_geometry_proof"]!=stable:
            return False
        if any(geometry[key]!=location[key] for key in ("selected_latitude","selected_longitude","target_dem_elevation_m")):
            return False
        if physical != _physical_response_provenance(row):
            return False
        return _source_clock_product_has_authority(json.dumps(row),lead_days=int(row["lead_days"]))
    except (KeyError,IndexError,TypeError,ValueError,OSError):
        return False


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
    day0_remaining_from_iso: str | None = None,
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
        day0_remaining_from_iso=day0_remaining_from_iso,
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


def read_consumed_instrument_values(
    conn: sqlite3.Connection,
    *,
    city: str,
    metric: str,
    target_date: str,
    consumed_models: Mapping[int, str],
    materialized_at_iso: str,
    day0_remaining_from_iso: str | None = None,
) -> dict[int, ServedInstrumentValue]:
    """Revalidate exact consumed rows, never substitute a newer row for an old proof.

    The same product, body, receipt and surface validator used by the producer
    runs at its original possession cutoff and, for a post-day family, at the
    Day0 tau the rows were proven under (``day0_remaining_from_iso``, from
    their frozen identity). The family/metric/id predicates are bound
    parameters. New inputs cannot change which rows are checked.
    """
    consumed_ids = tuple(consumed_models)
    if not consumed_ids or any(type(i) is not int or i <= 0 or not consumed_models[i] for i in consumed_ids):
        raise ValueError("consumed raw identities must be positive integers")
    cutoff = datetime.fromisoformat(materialized_at_iso.replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        raise ValueError("consumed proof cutoff must be timezone-aware")
    schema = current_value_serving_schema(conn)
    sql, params = _source_clock_rows_query(
        city=city, metric=metric, target_date=target_date,
        decision_iso=cutoff.isoformat(), schema=schema,
        max_substitution_age_hours=PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS,
        day0_remaining_from_iso=day0_remaining_from_iso,
    )
    marker = "ORDER BY model,"
    if sql.count(marker) != 1:
        raise ValueError("consumed source query ordering changed")
    sql = sql.replace(marker, "AND raw_model_forecast_id IN (" +
        ",".join("?" for _ in consumed_ids) + ") " + marker)
    try:
        rows = conn.execute(sql, (*params, *consumed_ids)).fetchall()
        deadline = time.monotonic() + _PHYSICAL_CAPTURE_SCAN_BUDGET_SECONDS
        out = {}
        for row in rows:
            enriched = (*row[:-1], _read_product_identity_at_cutoff(
                conn, row[-1], deadline_monotonic=deadline))
            parsed = _served_source_clock_row(enriched, schema=schema,
                max_substitution_age_hours=PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS)
            if parsed is not None:
                model, value = parsed
                if model == consumed_models.get(value.raw_model_forecast_id):
                    out[value.raw_model_forecast_id] = value
        return out
    except sqlite3.OperationalError as exc:
        _raise_typed_read_unavailable(exc)
        raise AssertionError("unreachable")


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
    day0_remaining_from_iso: str | None = None,
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
        rows, deadline = _read_source_clock_candidates(
            conn,
            city=city,
            metric=metric,
            target_date=target_date,
            decision_iso=decision_iso,
            schema=schema,
            max_substitution_age_hours=max_substitution_age_hours,
            day0_remaining_from_iso=day0_remaining_from_iso,
        )
        try:
            for row in rows:
                if str(row[1]) in out:
                    continue
                # A winner has passed the full current-cut physical receipt and
                # serving rules. Invalid candidates never suppress later rows.
                proved = (*row[:-1], _read_product_identity_at_cutoff(
                    conn, row[-1], deadline_monotonic=deadline,
                ))
                served = _served_source_clock_row(
                    proved,
                    schema=schema,
                    max_substitution_age_hours=max_substitution_age_hours,
                )
                if served is None:
                    continue
                model, value = served
                if _is_station_model(model) and (
                    not include_station_sources
                    or not _station_model_has_entry_authority(model)
                ):
                    continue
                out[model] = value
        except sqlite3.OperationalError as exc:
            _raise_typed_read_unavailable(exc)
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
    day0_remaining_from_iso: str | None = None,
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
        day0_remaining_from_iso=day0_remaining_from_iso,
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
