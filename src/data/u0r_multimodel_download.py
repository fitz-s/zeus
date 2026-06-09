# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: U0R_BAYES_SPEC.md §6 F1 (raw capture: previous_runs + single_runs ->
#   raw_model_forecasts), §3 (causality: previous-runs fixed-lead; single-runs live capture;
#   run_time != source_available_at), §5 (~6mo retention); §7 antibodies (C/F unit mix ->
#   force celsius; fail-soft drop). CONTINUITY_AND_WIRING.md §4 steps 2-3, 9 (forward+history
#   daily download/persist + 180d prune). IRON RULE #4 (ONE-BUILDER: REUSE the existing OM
#   fetchers + OPENMETEO_MODEL_IDS; no parallel fetcher). INV-37: a SINGLE zeus-forecasts.db
#   connection, single BEGIN/commit; no cross-DB write.
"""F1 step-2/3/9 — the FORWARD + walk-forward U0R multi-model download/persist job.

For each current-target (city, metric, target_date, lead) x the 8 extra Open-Meteo models
(decorrelated globals gfs_global/icon_global/gem_global/jma_seamless + icon_eu + in-domain
regionals icon_d2/arome + icon_seamless for alias-dedup), this job:

  (1) FORWARD single_runs fetch  — today's current-target value at the fixed cycle (live capture
      for replay; SPEC §3 single-runs identity). REUSES u0r_multimodel_capture._default_live_fetch.
  (2) fixed-lead previous_runs fetch — the no-leak walk-forward train value via the OM
      previous-runs API temperature_2m_previous_dayN hourly var (SPEC §3 fixed-lead). Forces
      temperature_unit=celsius (forecast_value_c is ALWAYS degC -> SPEC §7 C/F unit-mix antibody).
  (3) INSERTs the surviving rows into raw_model_forecasts (SHADOW_ONLY, training_allowed=0),
      on a SINGLE zeus-forecasts.db connection (INV-37), UNIQUE-idempotent per cycle.
  (4) PRUNES rows older than the retention cutoff (~180d, SPEC §5) in the same transaction.

FAIL-SOFT IS STRUCTURAL: a per-model fetch failure (raise OR None) DROPS that model's row and
the job proceeds with the survivors — a dropped model is simply absent (the fusion handles
missing sources by construction). The job is a pure side-effect on raw_model_forecasts: it
writes NOTHING into forecast_posteriors and touches NO posterior/q/center/spread/order, so the
money path is byte-identical whether or not this job runs (gated by the SEPARATE capture flag).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Sequence

from src.data.u0r_multimodel_capture import (
    OPENMETEO_MODEL_IDS,
    OPENMETEO_PREVIOUS_RUNS_ANCHOR_MODEL_NAME,
    _default_live_fetch,
)
from src.forecast.model_selection import (
    ANCHOR_MODEL,
    GLOBAL_LIKELIHOOD_MODELS,
    REGIONAL_MODELS,
)

_LOG = logging.getLogger("zeus.u0r_multimodel_download")

# SPEC §5: ~6 months retention on the shadow capture table.
RETENTION_DAYS = 180


class RawModelForecastRequestConflict(RuntimeError):
    """BLOCKER 4 (operator-sharpened) — a same-logical-key insert arrived with a DIFFERENT
    physical request identity (a corrected request: changed timezone / cell_selection / elevation
    / product_id / request_url_hash).

    The pre-fix UNIQUE(model,city,target_date,metric,source_cycle_time,endpoint) + INSERT OR
    IGNORE SILENTLY discarded such a Run-2, leaving a STALE forecast_value_c to contaminate
    bias/MAE/sigma/covariance/q in the walk-forward history JOIN (u0r_history_provider keys on
    model/city/metric/lead/endpoint/target_date — NOT on the request hash, so a stale row poisons
    the residual series). This exception replaces the silent drop with a LOUD, attributable fault:
    the persist writes an audit row to raw_model_forecast_request_conflicts and raises, so an
    operator re-pins the request identity rather than a wrong value silently training a live-money
    posterior. The logical key cannot bind two physical requests."""


# The logical-key columns that, together, identified a row under the PRE-fix UNIQUE. Two rows that
# match on ALL of these but DIFFER on (product_id, request_url_hash) are a request conflict.
_RMF_LOGICAL_KEY_COLUMNS = (
    "model", "city", "target_date", "metric", "source_cycle_time", "endpoint",
)

# BLOCKER 4 (product identity): the provider + per-endpoint physical-product constants that the
# download stamps onto every raw_model_forecasts row so a stored forecast_value_c is
# reconstructable to its exact Open-Meteo product. cell_selection / elevation / downscaling are
# the OM grid choices the U0R fetchers use (the single-runs anchor pattern: nearest gridpoint,
# requested elevation, no extra downscaling). endpoint_mode is the physical endpoint family.
OPENMETEO_PROVIDER = "open-meteo"
SINGLE_RUNS_SOURCE_FAMILY = "openmeteo_single_runs"
PREVIOUS_RUNS_SOURCE_FAMILY = "openmeteo_previous_runs"
U0R_CELL_SELECTION = "nearest"          # OM default cell pick (nearest gridpoint to requested).
U0R_ELEVATION_PARAM = "requested"       # OM elevation = requested point (no override).
U0R_DOWNSCALING_POLICY = "none"         # no statistical downscaling applied to the raw value.

# Per-model OM previous-runs source_id (the WHICH-feed identity). Keyed by STORED model identity.
# The anchor is stored model='ecmwf_ifs' but its OM previous-runs source is ecmwf_previous_runs
# serving model_name='ecmwf_ifs025' (0.25 product) — see OPENMETEO_PREVIOUS_RUNS_MODEL_IDS below
# and BLOCKER 3 (the ifs025->ifs9 bridge). Falls back to '<model>_previous_runs'.
OPENMETEO_PREVIOUS_RUNS_SOURCE_ID: dict[str, str] = {
    ANCHOR_MODEL: "ecmwf_previous_runs",
    "gfs_global": "gfs_previous_runs",
    "icon_global": "icon_previous_runs",
    "icon_eu": "icon_previous_runs",
    "gem_global": "gem_previous_runs",
    "jma_seamless": "jma_previous_runs",
    "icon_d2": "icon_d2_previous_runs",
    "meteofrance_arome_france_hd": "arome_previous_runs",
    "icon_seamless": "icon_d2_previous_runs",
}


def _model_domain_hash(
    *,
    provider: str,
    model_name: str,
    cell_selection: str,
    elevation_param: str,
    downscaling_policy: str,
    endpoint_mode: str,
) -> str:
    """BLOCKER 4 — fingerprint binding the physical-product identity of a captured value.

    Two captures that differ in ANY of (provider, model_name, cell_selection, elevation_param,
    downscaling_policy, endpoint_mode) are DIFFERENT physical products and get different hashes,
    so a residual history can never silently mix two cells / two model resolutions under one id.
    """
    payload = json.dumps(
        {
            "provider": provider,
            "model_name": model_name,
            "cell_selection": cell_selection,
            "elevation_param": elevation_param,
            "downscaling_policy": downscaling_policy,
            "endpoint_mode": endpoint_mode,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _u0r_product_identity(model: str, endpoint: str, target: "U0RDownloadTarget") -> dict:
    """BLOCKER 4 — the full product-identity payload for one (model, endpoint, target) capture.

    Resolves the OM model id actually addressed (model_name), the source_id/source_family/
    product_id/provider, the requested coordinates+timezone, the cell/elevation/downscaling
    choices, the endpoint_mode, the request params (the reconstructable request) + its url hash,
    and the model_domain_hash. The anchor's model_name is the OM previous-runs id 'ecmwf_ifs025'
    even though the stored `model` column stays the fusion identity 'ecmwf_ifs' (BLOCKER 3).
    """
    if endpoint == "previous_runs":
        from src.data.openmeteo_client import PREVIOUS_RUNS_URL  # noqa: PLC0415

        model_name = OPENMETEO_PREVIOUS_RUNS_MODEL_IDS.get(
            model, OPENMETEO_MODEL_IDS.get(model, model)
        )
        source_family = PREVIOUS_RUNS_SOURCE_FAMILY
        source_id = OPENMETEO_PREVIOUS_RUNS_SOURCE_ID.get(model, f"{model}_previous_runs")
        base_url = PREVIOUS_RUNS_URL
        lead = max(0, int(target.lead_days))
        hourly_var = "temperature_2m" if lead == 0 else f"temperature_2m_previous_day{lead}"
        request_params = {
            "latitude": target.latitude,
            "longitude": target.longitude,
            "start_date": target.target_date,
            "end_date": target.target_date,
            "hourly": hourly_var,
            "models": model_name,
            "temperature_unit": "celsius",
            "timezone": target.timezone_name,
            "cell_selection": U0R_CELL_SELECTION,
        }
    else:  # single_runs
        from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: PLC0415
            SINGLE_RUNS_FORECAST_URL,
        )

        model_name = OPENMETEO_MODEL_IDS.get(model, model)
        source_family = SINGLE_RUNS_SOURCE_FAMILY
        source_id = f"{model}_single_runs"
        base_url = SINGLE_RUNS_FORECAST_URL
        request_params = {
            "latitude": target.latitude,
            "longitude": target.longitude,
            "hourly": "temperature_2m",
            "models": model_name,
            "temperature_unit": "celsius",
            "timezone": target.timezone_name,
            "cell_selection": U0R_CELL_SELECTION,
        }
    request_params_json = json.dumps(request_params, sort_keys=True, separators=(",", ":"))
    request_url_hash = hashlib.sha256(
        f"{base_url}?{request_params_json}".encode("utf-8")
    ).hexdigest()
    product_id = f"{model_name}::{endpoint}"
    model_domain_hash = _model_domain_hash(
        provider=OPENMETEO_PROVIDER,
        model_name=model_name,
        cell_selection=U0R_CELL_SELECTION,
        elevation_param=U0R_ELEVATION_PARAM,
        downscaling_policy=U0R_DOWNSCALING_POLICY,
        endpoint_mode=endpoint,
    )
    return {
        "source_id": source_id,
        "source_family": source_family,
        "product_id": product_id,
        "provider": OPENMETEO_PROVIDER,
        "model_name": model_name,
        "request_params_json": request_params_json,
        "request_url_hash": request_url_hash,
        "latitude_requested": float(target.latitude),
        "longitude_requested": float(target.longitude),
        "timezone_requested": target.timezone_name,
        "cell_selection": U0R_CELL_SELECTION,
        "elevation_param": U0R_ELEVATION_PARAM,
        "downscaling_policy": U0R_DOWNSCALING_POLICY,
        "endpoint_mode": endpoint,
        "model_domain_hash": model_domain_hash,
        "coverage_status": "COVERED",
    }

# FIX 1 (live-money correctness): the ANCHOR (ecmwf_ifs) MUST be captured alongside the
# likelihood instruments. Without it, raw_model_forecasts NEVER accrues anchor previous_runs
# rows -> U0RHistoryProvider returns no anchor history -> the fusion's have_anchor is False ->
# the posterior is stuck at EQUAL_WEIGHT forever (the prior is never formed). The anchor is the
# FIRST element so its row provenance is unambiguous in the candidate ordering.
#
# The full capture set: anchor (prior) + globals + icon_eu (likelihood) + in-domain regionals
# + the alias-dedup probe (icon_seamless). icon_seamless is captured only so the fusion's
# alias-dedup test has both series; it is dropped from the fused Sigma downstream (never
# double-counts icon_d2).
U0R_EXTRA_MODELS: tuple[str, ...] = (
    (ANCHOR_MODEL,)
    + tuple(GLOBAL_LIKELIHOOD_MODELS)
    + tuple(REGIONAL_MODELS)
    + ("icon_seamless",)
)

# Open-Meteo PREVIOUS-RUNS model ids keyed by the STORED model identity. The previous-runs API
# model id can differ from both the stored identity AND the single-runs id: the anchor is stored
# under its fusion identity ANCHOR_MODEL ("ecmwf_ifs", the U0RHistoryProvider join key) but the
# OM previous-runs API addresses the ECMWF deterministic feed as "ecmwf_ifs025" (the proven id
# in forecast_source_registry.OPENMETEO_PREVIOUS_RUNS_MODEL_SOURCE_MAP / forecasts_append). The
# fetch translates store-id -> OM-previous-runs-id here; the stored `model` column is ALWAYS the
# fusion identity. Non-anchor models fall back to OPENMETEO_MODEL_IDS (their OM id == store id).
OPENMETEO_PREVIOUS_RUNS_MODEL_IDS: dict[str, str] = {
    # OM previous-runs ECMWF id; stored model col stays "ecmwf_ifs" (the fusion identity). The
    # value is the SINGLE source of truth in u0r_multimodel_capture (BLOCKER 3 bridge gate reads
    # the same constant) so the download and the bridge can never drift on which product served
    # the anchor history.
    ANCHOR_MODEL: OPENMETEO_PREVIOUS_RUNS_ANCHOR_MODEL_NAME,
}

# A single-runs (forward) fetch: today's local-day extremum (degC) for the metric, or None.
SingleRunsFetchFn = Callable[..., float | None]
# A previous-runs (fixed-lead) fetch: the fixed-lead local-day extremum (degC), or None.
PreviousRunsFetchFn = Callable[..., float | None]


@dataclass(frozen=True)
class U0RDownloadTarget:
    """One current-target the extra models are captured for."""

    city: str
    metric: str
    target_date: str
    lead_days: int
    latitude: float
    longitude: float
    timezone_name: str


def _default_previous_runs_fetch(
    *,
    model: str,
    latitude: float,
    longitude: float,
    timezone_name: str,
    target_date: str,
    lead_days: int,
    metric: str,
) -> float | None:
    """Default fixed-lead previous-runs fetch via the OM previous-runs API. FORCES celsius
    (forecast_value_c is always degC; SPEC §7 C/F unit-mix antibody). FAIL-SOFT: returns None
    on ANY error so the model is dropped, never crashing the cycle.

    Uses the temperature_2m_previous_dayN hourly var (fixed-lead, no-leak; SPEC §3): the value
    valid on target_date as forecast lead_days ago. lead_days==0 falls back to temperature_2m.
    """
    try:
        from src.data.openmeteo_client import PREVIOUS_RUNS_URL, fetch  # noqa: PLC0415

        # Translate the STORED model identity -> OM previous-runs model id. The anchor is
        # stored as ANCHOR_MODEL ("ecmwf_ifs") but the OM previous-runs API id is
        # "ecmwf_ifs025"; every other model's OM id equals its store id (OPENMETEO_MODEL_IDS).
        om_model = OPENMETEO_PREVIOUS_RUNS_MODEL_IDS.get(
            model, OPENMETEO_MODEL_IDS.get(model, model)
        )
        lead = max(0, int(lead_days))
        hourly_var = "temperature_2m" if lead == 0 else f"temperature_2m_previous_day{lead}"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": target_date,
            "end_date": target_date,
            "hourly": hourly_var,
            "models": om_model,
            "temperature_unit": "celsius",  # NEVER the settlement unit (C/F mix antibody)
            "timezone": timezone_name,
        }
        payload = fetch(
            PREVIOUS_RUNS_URL,
            params,
            endpoint_label=f"u0r_{model}_previous_runs",
        )
        return _extract_localday_extremum_c(payload, hourly_var, metric)
    except Exception as exc:  # FAIL-SOFT: drop this model, never block the cycle.
        _LOG.warning("U0R previous-runs fetch dropped model %s (fail-soft): %s", model, exc)
        return None


def _extract_localday_extremum_c(payload: object, hourly_var: str, metric: str) -> float | None:
    """Local-day high/low (degC) from a previous-runs hourly payload over hourly_var. The
    previous-runs API returns the target_date already in the requested timezone, so every
    sample in the (single-day) window belongs to the local day. Returns None if empty."""
    if not isinstance(payload, dict):
        return None
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return None
    values = hourly.get(hourly_var)
    if not isinstance(values, (list, tuple)):
        return None
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    if not nums:
        return None
    return max(nums) if metric == "high" else min(nums)


# BLOCKER 4: the persisted column order for one raw_model_forecasts row. The first 10 are the
# original capture columns; the rest are the product-identity columns the download stamps.
_RMF_INSERT_COLUMNS = (
    "model", "city", "target_date", "metric", "source_cycle_time", "source_available_at",
    "captured_at", "lead_days", "forecast_value_c", "endpoint",
    "source_id", "source_family", "product_id", "provider", "model_name",
    "request_params_json", "request_url_hash", "latitude_requested", "longitude_requested",
    "timezone_requested", "cell_selection", "elevation_param", "downscaling_policy",
    "endpoint_mode", "model_domain_hash", "coverage_status",
)


def _detect_request_conflict(conn, row: dict) -> dict | None:
    """BLOCKER 4 — return the EXISTING row's identity if *row* shares the logical key of a stored
    row but carries a DIFFERENT physical request identity (product_id OR request_url_hash);
    else None.

    This is the antibody for the operator-sharpened B4: the logical key
    (model,city,target_date,metric,source_cycle_time,endpoint) must bind EXACTLY ONE physical
    request. A same-logical-key/different-request insert is a corrected request that the pre-fix
    INSERT OR IGNORE would have silently dropped, leaving a stale value to contaminate the history.
    """
    where = " AND ".join(f"{c} = ?" for c in _RMF_LOGICAL_KEY_COLUMNS)
    existing = conn.execute(
        f"""SELECT product_id, request_url_hash, forecast_value_c, cell_selection
            FROM raw_model_forecasts WHERE {where}""",
        tuple(row[c] for c in _RMF_LOGICAL_KEY_COLUMNS),
    ).fetchone()
    if existing is None:
        return None
    existing_product_id, existing_hash, existing_value, existing_cell = existing
    # LANDMINE #1 (operator-flagged, the_path PR review 2026-06-08): a STORED row whose
    # request_url_hash IS NULL is a PRE-IDENTITY / legacy-backfill row (it was seeded before the
    # product-identity columns existed). It carries NO physical request to conflict WITH, so a
    # live populated-identity insert on the same logical key is ENRICHABLE (update-in-place), NOT a
    # corrected-request conflict. Treating it as a conflict would make the FIRST live download after
    # ANY legacy backfill falsely raise. A genuine conflict requires a POPULATED stored hash that
    # DIFFERS from a populated incoming hash (handled below). NOTE: the up-to-date backfill stamps
    # full identity, so this path only fires for rows seeded by an OLD identity-less backfill.
    if existing_hash is None:
        return None
    # Same logical key + same physical request identity == the normal idempotent re-run: not a
    # conflict (INSERT OR IGNORE will collapse it). Only a CHANGED request identity is a conflict.
    if (existing_product_id == row.get("product_id")
            and existing_hash == row.get("request_url_hash")):
        return None
    return {
        "existing_product_id": existing_product_id,
        "existing_request_url_hash": existing_hash,
        "existing_forecast_value_c": existing_value,
        "existing_cell_selection": existing_cell,
    }


def _write_request_conflict_audit(conn, row: dict, existing: dict) -> None:
    """Persist one raw_model_forecast_request_conflicts row recording BOTH the existing and the
    incoming request identity (operator directive: 'write an audit row'). Forensically
    attributable: an operator can see exactly which request changed and the two values in play."""
    conn.execute(
        """INSERT INTO raw_model_forecast_request_conflicts
               (model, city, target_date, metric, source_cycle_time, endpoint,
                existing_product_id, incoming_product_id,
                existing_request_url_hash, incoming_request_url_hash,
                existing_forecast_value_c, incoming_forecast_value_c,
                existing_cell_selection, incoming_cell_selection)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row["model"], row["city"], row["target_date"], row["metric"],
            row["source_cycle_time"], row["endpoint"],
            existing["existing_product_id"], row.get("product_id"),
            existing["existing_request_url_hash"], row.get("request_url_hash"),
            existing["existing_forecast_value_c"], row.get("forecast_value_c"),
            existing["existing_cell_selection"], row.get("cell_selection"),
        ),
    )


def _request_conflict_error(row: dict, conflict: dict) -> "RawModelForecastRequestConflict":
    """Build the RawModelForecastRequestConflict for a same-logical-key/different-request row."""
    return RawModelForecastRequestConflict(
        "raw_model_forecasts request conflict: logical key "
        f"({'/'.join(str(row[c]) for c in _RMF_LOGICAL_KEY_COLUMNS)}) already bound to "
        f"product_id={conflict['existing_product_id']!r} "
        f"request_url_hash={conflict['existing_request_url_hash']!r}; incoming "
        f"product_id={row.get('product_id')!r} "
        f"request_url_hash={row.get('request_url_hash')!r} differs. A corrected request "
        "must NOT silently overwrite/ignore the stored value (B4 contamination guard)."
    )


def _scan_and_audit_request_conflicts(conn, rows: Sequence[dict]) -> None:
    """BLOCKER 4 production pre-scan — detect same-logical-key/different-request conflicts and
    DURABLY write their audit rows on autocommit BEFORE the caller opens its insert transaction.

    download_u0r_extra_raw_inputs wraps the insert in a BEGIN it ROLLS BACK on any error. If the
    conflict audit were written inside that BEGIN it would be rolled back along with everything
    else — the operator would lose the forensic trail of the silent-drop-that-wasn't. So the
    production path calls THIS first, with NO open transaction. If ANY conflict is found the audit
    rows are written and EXPLICITLY COMMITTED here (the connection uses sqlite3's default
    isolation_level="", so a DML opens an implicit deferred transaction — we commit it so the
    audit is durable even though the cycle's BEGIN below will be rolled back), then
    RawModelForecastRequestConflict is raised before BEGIN: the cycle's capture rows are never
    inserted (the corrected request must be operator-resolved)."""
    if conn.in_transaction:
        raise RuntimeError(
            "_scan_and_audit_request_conflicts must run with no open transaction so its audit "
            "rows can be committed independently of a caller's later ROLLBACK; an open "
            "transaction was found."
        )
    first_conflict: tuple[dict, dict] | None = None
    for row in rows:
        conflict = _detect_request_conflict(conn, row)
        if conflict is not None:
            _write_request_conflict_audit(conn, row, conflict)
            if first_conflict is None:
                first_conflict = (row, conflict)
    if first_conflict is not None:
        conn.commit()  # DURABLE: survives the caller's later ROLLBACK of the cycle BEGIN
        raise _request_conflict_error(*first_conflict)


def _persist_rows(
    conn,
    rows: Sequence[dict],
) -> int:
    """Persist the captured rows, idempotent on the FULL identity (logical key + product_id +
    request_url_hash). Returns rows actually written.

    BLOCKER 4 (operator-sharpened): BEFORE inserting, each row is checked against any stored row
    sharing its logical key. If a stored row exists with the SAME logical key but a DIFFERENT
    physical request identity (product_id or request_url_hash changed), this is a corrected
    request the pre-fix INSERT OR IGNORE would have SILENTLY dropped — leaving a stale value to
    contaminate bias/MAE/sigma. Instead an audit row is written (on THIS connection's current
    transaction state) and RawModelForecastRequestConflict is raised; no capture row is inserted.
    A same-logical-key + same-request re-run is the normal idempotent case and is INSERT-OR-IGNORE
    collapsed (the widened UNIQUE makes a genuinely changed request a NEW row when its logical key
    already differs).

    AUDIT DURABILITY CONTRACT: this function writes the audit row and raises, but does NOT itself
    guarantee the audit survives a caller's surrounding ROLLBACK. Callers that wrap this in a
    transaction they roll back on error (e.g. download_u0r_extra_raw_inputs) MUST pre-scan with
    _scan_and_audit_request_conflicts on autocommit BEFORE opening that transaction, so the audit
    is already durable. When called directly on an autocommit connection (the operator-named test
    API), each statement self-commits and the audit is durable on its own.

    Each row is a dict keyed by _RMF_INSERT_COLUMNS (capture columns + BLOCKER 4 product
    identity). raw_sha256 / artifact_id stay NULL here (capture precedes artifact persistence)."""
    # Conflict pass FIRST — a single conflicting row fails the whole batch (the corrected request
    # must be resolved by an operator, not partially applied).
    for row in rows:
        conflict = _detect_request_conflict(conn, row)
        if conflict is not None:
            _write_request_conflict_audit(conn, row, conflict)
            raise _request_conflict_error(row, conflict)
    before = conn.total_changes
    placeholders = ",".join("?" for _ in _RMF_INSERT_COLUMNS)
    cols = ",".join(_RMF_INSERT_COLUMNS)
    conn.executemany(
        f"INSERT OR IGNORE INTO raw_model_forecasts ({cols}) VALUES ({placeholders})",
        [tuple(row[c] for c in _RMF_INSERT_COLUMNS) for row in rows],
    )
    return conn.total_changes - before


def _prune_old(conn, *, cutoff_iso: str) -> int:
    """DELETE rows captured before the retention cutoff (~180d). Returns rows deleted."""
    cur = conn.execute(
        "DELETE FROM raw_model_forecasts WHERE captured_at < ?", (cutoff_iso,)
    )
    return int(cur.rowcount or 0)


def download_u0r_extra_raw_inputs(
    *,
    forecast_db: Path,
    cycle: datetime,
    targets: Iterable[U0RDownloadTarget],
    single_runs_fetch: SingleRunsFetchFn | None = None,
    previous_runs_fetch: PreviousRunsFetchFn | None = None,
    release_lag_hours: float = 14.0,
    forecast_hours: int = 120,
    retention_days: int = RETENTION_DAYS,
) -> dict[str, object]:
    """Capture (forward single_runs + fixed-lead previous_runs) the 8 extra OM models for each
    current target and persist into raw_model_forecasts on a SINGLE zeus-forecasts.db connection
    (INV-37). Fail-soft per model. Prunes rows older than the retention cutoff in the same
    transaction. Returns a provenance report. Reuses the existing OM fetchers (IRON RULE #4)."""
    single_fetch = single_runs_fetch or _default_live_fetch
    prev_fetch = previous_runs_fetch or _default_previous_runs_fetch

    cycle_utc = cycle.astimezone(UTC)
    cycle_iso = cycle_utc.isoformat()
    source_available_iso = (cycle_utc + timedelta(hours=release_lag_hours)).isoformat()
    captured_at = datetime.now(tz=UTC)
    captured_iso = max(captured_at, cycle_utc).isoformat()
    cutoff_iso = (captured_at - timedelta(days=int(retention_days))).isoformat()

    target_list = list(targets)
    rows: list[tuple] = []
    dropped: list[str] = []

    for t in target_list:
        target_local_date = date.fromisoformat(t.target_date)
        for model in U0R_EXTRA_MODELS:
            # (1) FORWARD single_runs (live capture).
            try:
                sv = single_fetch(
                    model=model, latitude=t.latitude, longitude=t.longitude,
                    timezone_name=t.timezone_name, run=cycle_utc,
                    target_local_date=target_local_date, metric=t.metric,
                    forecast_hours=forecast_hours,
                )
            except Exception as exc:
                _LOG.warning("U0R single_runs dropped %s (fail-soft): %s", model, exc)
                sv = None
            if sv is None:
                dropped.append(f"{model}:single_runs")
            else:
                rows.append({
                    "model": model, "city": t.city, "target_date": t.target_date,
                    "metric": t.metric, "source_cycle_time": cycle_iso,
                    "source_available_at": source_available_iso, "captured_at": captured_iso,
                    "lead_days": int(t.lead_days), "forecast_value_c": float(sv),
                    "endpoint": "single_runs",
                    **_u0r_product_identity(model, "single_runs", t),
                })

            # (2) fixed-lead previous_runs (walk-forward train).
            try:
                pv = prev_fetch(
                    model=model, latitude=t.latitude, longitude=t.longitude,
                    timezone_name=t.timezone_name, target_date=t.target_date,
                    lead_days=int(t.lead_days), metric=t.metric,
                )
            except Exception as exc:
                _LOG.warning("U0R previous_runs dropped %s (fail-soft): %s", model, exc)
                pv = None
            if pv is None:
                dropped.append(f"{model}:previous_runs")
            else:
                rows.append({
                    "model": model, "city": t.city, "target_date": t.target_date,
                    "metric": t.metric, "source_cycle_time": cycle_iso,
                    "source_available_at": source_available_iso, "captured_at": captured_iso,
                    "lead_days": int(t.lead_days), "forecast_value_c": float(pv),
                    "endpoint": "previous_runs",
                    **_u0r_product_identity(model, "previous_runs", t),
                })

    # ---- single-connection / single-DB persist + prune (INV-37) ----
    from src.state.db import _connect  # noqa: PLC0415
    from src.state.schema.v2_schema import (  # noqa: PLC0415
        ensure_replacement_forecast_shadow_schema,
    )

    written = 0
    pruned = 0
    conn = _connect(Path(forecast_db), write_class="live")
    try:
        ensure_replacement_forecast_shadow_schema(conn)
        # BLOCKER 4 — DURABLE conflict audit BEFORE the rollback-on-error BEGIN. A corrected
        # request (same logical key, different product_id/request_url_hash) writes its audit row
        # on autocommit here and raises, so the forensic trail survives even though the capture
        # rows are never inserted. Running this inside the BEGIN below would let the ROLLBACK
        # erase the audit — the very silent-drop the operator demanded we make loud.
        if rows:
            _scan_and_audit_request_conflicts(conn, rows)
        conn.execute("BEGIN")
        try:
            if rows:
                written = _persist_rows(conn, rows)
            pruned = _prune_old(conn, cutoff_iso=cutoff_iso)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    return {
        "status": "U0R_EXTRA_RAW_INPUTS_DOWNLOADED",
        "cycle": cycle_iso,
        "forecast_db": str(forecast_db),
        "target_count": len(target_list),
        "candidate_row_count": len(rows),
        "written_row_count": written,
        "pruned_row_count": pruned,
        "dropped": tuple(dropped),
    }
