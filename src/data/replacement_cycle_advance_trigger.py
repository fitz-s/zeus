# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: U5 step 2a (operator regime-unification + freshness investigation 2026-06-12,
#   docs/authority/regime_unification_2026-06-12.md §U2 + docs/evidence/freshness/
#   2026-06-12_forecast_freshness_truth.md §Q4(b)). The U2 root fix's first half: re-materialize a
#   HELD/active family's posterior the moment a NEWER provider cycle has been ingested than the
#   cycle the posterior consumed — NOT on a wall clock. Belief decay is a STEP function on missed
#   model cycles (measured: new-cycle ingest moves posterior TV 0.319 / center 0.7°C mean, 1.9°C
#   p90; same-cycle recompute Δμ≈0), so re-materialization is worthwhile EXACTLY when a fresher
#   cycle exists and worthless otherwise. Born-stale (14.1% measured) + backward thrashing (78
#   transitions / 267 live families) are the diseases this kills together with the materializer's
#   monotone-advance refusal (_cycle_monotone_block_reasons).
"""SINGLE-AUTHORITY newer-cycle comparison + idempotent re-materialization enqueue.

Sibling of replacement_fusion_upgrade_trigger (Task #32): SAME availability-poll lane, SAME seed
builder, SAME seed_dir the materialize cycle drains, SAME plan + day0 guard + nearest-target-first
ordering — the ONLY difference is the verdict. The fusion-upgrade trigger fires on instrument-set
expansion at the SAME cycle; this trigger fires on a NEWER cycle becoming materializable.

THE single comparison (`scope_needs_cycle_advance`): a scope needs re-materialization iff its latest
posterior consumed a model cycle STRICTLY OLDER than the freshest in-universe cycle that is now
materializable (both raw legs — AIFS + OM9 anchor — present in raw_forecast_artifacts at that
cycle). The freshest materializable cycle is MIN over both legs of MAX(source_cycle_time): a
half-published cycle (one leg lagging) is NOT yet materializable, exactly as the downloader's
high-water mark treats it.

Prioritization (operator directive 2026-06-12): (i) families with HELD positions (zeus_trades
position_current, read-only) first, then (ii) families with markets in their active trading window
(the current-target plan already restricts to token-bearing markets with target_date >= today).
Bounded per tick by the fair-cursor budget (Wave1B precedent — count only WRITTEN seeds, never a
numeric drop-cap on the candidate set).

Idempotency: cycle_advance_enqueues UNIQUE(city, target_date, metric, target_cycle_time). A scope is
re-enqueued AT MOST ONCE per target-cycle advance — a still-unmaterialized seed (manifest absent,
subprocess pending) never loops; the NEXT fresher cycle gets its own distinct marker. Fail-soft
throughout: any per-scope error is logged and skipped; the function never raises into the poll.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_LOG = logging.getLogger("zeus.replacement_cycle_advance_trigger")

UTC = timezone.utc

SOURCE_ID = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"

# The two raw-artifact legs the materialization manifests are built from. The freshest
# MATERIALIZABLE cycle is the MIN over both legs of MAX(source_cycle_time): a cycle with only one
# leg published cannot be fused (the seed builder requires BOTH manifests), so it is not yet a
# materialization opportunity. Mirrors _max_downloaded_current_target_cycle in
# replacement_forecast_production (single high-water-mark definition).
_AIFS_LEG_SOURCE_ID = "ecmwf_aifs_ens"
_ANCHOR_LEG_SOURCE_ID = "openmeteo_ecmwf_ifs_9km"


def _parse_cycle(value: object) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        return None


def _per_leg_max_cycle(conn: sqlite3.Connection, source_id: str) -> datetime | None:
    """MAX(source_cycle_time) ingested for one raw-artifact leg (None when absent). Fail-soft."""
    try:
        row = conn.execute(
            "SELECT MAX(source_cycle_time) FROM raw_forecast_artifacts WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return _parse_cycle(row[0])


def freshest_materializable_cycle(conn: sqlite3.Connection) -> datetime | None:
    """The freshest in-universe cycle for which BOTH legs' raw artifacts are ingested.

    = MIN(MAX(aifs cycle), MAX(anchor cycle)). None when either leg is empty (nothing to advance
    onto). This is the universe-wide ceiling; per-scope manifest presence is still checked when the
    seed is built (a scope whose city/date lacks a manifest at this cycle is recorded
    manifest_missing and retried next tick, never enqueued blindly).
    """
    aifs = _per_leg_max_cycle(conn, _AIFS_LEG_SOURCE_ID)
    anchor = _per_leg_max_cycle(conn, _ANCHOR_LEG_SOURCE_ID)
    if aifs is None or anchor is None:
        return None
    return min(aifs, anchor)


def _latest_posterior_consumed_cycle(
    conn: sqlite3.Connection, *, city: str, target_date: str, metric: str
) -> datetime | None:
    """The model cycle the LATEST posterior of this scope consumed (its source_cycle_time), or
    None when there is no posterior. Fail-soft: any read/parse error -> None."""
    try:
        row = conn.execute(
            """
            SELECT source_cycle_time
            FROM forecast_posteriors
            WHERE source_id = ? AND city = ? AND target_date = ? AND temperature_metric = ?
            ORDER BY computed_at DESC
            LIMIT 1
            """,
            (SOURCE_ID, city, target_date, metric),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return _parse_cycle(row[0] if not hasattr(row, "keys") else row["source_cycle_time"])


def scope_needs_cycle_advance(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    freshest_cycle: datetime,
) -> dict[str, object]:
    """THE single comparison: does this scope's latest posterior need re-materialization because a
    STRICTLY NEWER materializable cycle now exists?

    Returns {needs_advance, consumed_cycle, target_cycle}. needs_advance is True iff the scope has a
    posterior AND its consumed cycle is strictly older than ``freshest_cycle``. A scope with no
    posterior is NOT advanced here (it is a fresh-seed case the seed discovery owns). Fail-soft.
    """
    consumed = _latest_posterior_consumed_cycle(
        conn, city=city, target_date=target_date, metric=metric
    )
    if consumed is None:
        return {"needs_advance": False, "consumed_cycle": None, "target_cycle": None}
    needs = consumed < freshest_cycle
    return {
        "needs_advance": needs,
        "consumed_cycle": consumed.isoformat(),
        "target_cycle": freshest_cycle.isoformat(),
    }


def _held_position_families(conn_trades: sqlite3.Connection) -> set[tuple[str, str, str]]:
    """The (city, target_date, temperature_metric) families with a HELD position right now.

    Read-only from zeus_trades.position_current. A family is HELD when it has an OPEN position with
    real exposure (shares > 0) that is not in a terminal phase (settled/voided/closed). These are the
    families whose stale belief most directly risks money (the exit monitor reads their posterior),
    so they get re-materialization priority. Fail-soft: any read/schema error -> empty set (no
    prioritization, never a crash).
    """
    try:
        rows = conn_trades.execute(
            """
            SELECT DISTINCT city, target_date, temperature_metric
            FROM position_current
            WHERE COALESCE(shares, 0) > 0
              AND COALESCE(phase, '') NOT IN ('settled', 'voided', 'closed', 'exited')
              AND city IS NOT NULL AND target_date IS NOT NULL
              AND temperature_metric IS NOT NULL
            """
        ).fetchall()
    except Exception:
        return set()
    held: set[tuple[str, str, str]] = set()
    for r in rows:
        try:
            held.add((str(r[0]), str(r[1]), str(r[2])))
        except Exception:
            continue
    return held


def _already_enqueued(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    target_cycle_iso: str,
) -> bool:
    """True iff a re-materialization was already enqueued for this exact (scope, target-cycle). The
    marker is the idempotency bound. Fail-open toward NOT-enqueued only on read error (the UNIQUE
    index still prevents a duplicate physical row)."""
    try:
        row = conn.execute(
            """
            SELECT 1 FROM cycle_advance_enqueues
            WHERE city = ? AND target_date = ? AND metric = ? AND target_cycle_time = ?
            LIMIT 1
            """,
            (city, target_date, metric, target_cycle_iso),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _record_enqueue(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    consumed_cycle_iso: str,
    target_cycle_iso: str,
    held_position: bool,
    seed_file: str,
) -> bool:
    """Write the idempotency marker. Returns True iff this call inserted the row (False = a
    concurrent/prior enqueue already recorded it, via the UNIQUE index INSERT OR IGNORE)."""
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO cycle_advance_enqueues
            (enqueued_at, city, target_date, metric, consumed_cycle_time, target_cycle_time,
             held_position, seed_file)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(tz=UTC).isoformat(),
            city,
            target_date,
            metric,
            consumed_cycle_iso,
            target_cycle_iso,
            1 if held_position else 0,
            seed_file,
        ),
    )
    return conn.total_changes > before


def enqueue_cycle_advance_reseeds(
    *,
    forecast_db: Path | str,
    seed_dir: Path | str,
    raw_manifest_dir: Path | str,
    trades_db: Path | str | None = None,
    computed_at: datetime | None = None,
    limit: int = 50,
) -> dict[str, object]:
    """For every active-window target whose latest posterior consumed a STRICTLY OLDER cycle than
    the freshest materializable in-universe cycle, enqueue exactly one re-materialization seed
    (reusing the existing seed builder + seed_dir the materialize cycle drains). HELD-position
    families are processed FIRST. Idempotent per (scope, target-cycle) via cycle_advance_enqueues.

    Belongs in the EXISTING availability-poll lane (no new daemon). Fail-soft: any per-scope error
    is logged and skipped; the function never raises into the poll. Returns a compact report.
    """
    from src.data.replacement_forecast_current_target_plan import (  # noqa: PLC0415
        build_replacement_forecast_current_target_plan,
    )
    from src.data.replacement_forecast_materialization_seed_builder import (  # noqa: PLC0415
        build_replacement_forecast_materialization_seed,
        latest_baseline_coverage_for_replacement_seed,
        market_bins_for_replacement_seed,
        write_seed,
    )
    from src.data.replacement_forecast_seed_discovery import (  # noqa: PLC0415
        _latest_manifest,
        _load_manifests,
        _manifest_base_dir,
        _manifest_path_value,
        _resolve_path,
        _seed_name,
    )
    from src.data.replacement_forecast_source_run_identity import (  # noqa: PLC0415
        expected_replacement_dependency_identity_by_role,
    )
    from src.state.db import _connect  # noqa: PLC0415
    from src.state.schema.v2_schema import (  # noqa: PLC0415
        ensure_replacement_forecast_shadow_schema,
    )

    now = (computed_at or datetime.now(tz=UTC)).astimezone(UTC)
    forecast_db = Path(forecast_db)
    seed_path = Path(seed_dir)
    raw_dir = Path(raw_manifest_dir)
    report: dict[str, object] = {
        "status": "CYCLE_ADVANCE_TRIGGER",
        "freshest_materializable_cycle": None,
        "scopes_checked": 0,
        "advances_detected": 0,
        "held_advances_detected": 0,
        "seeds_enqueued": 0,
        "held_seeds_enqueued": 0,
        "already_enqueued": 0,
        "manifest_missing": 0,
        "day0_skipped": 0,
        "enqueued": [],
    }
    if not forecast_db.exists():
        report["status"] = "CYCLE_ADVANCE_FORECAST_DB_MISSING"
        return report

    plan = build_replacement_forecast_current_target_plan(
        forecast_db,
        min_target_date=now.date().isoformat(),
        require_raw_artifacts=False,
        now_utc=now,
    )
    if plan.status == "BLOCKED":
        report["status"] = "CYCLE_ADVANCE_PLAN_BLOCKED"
        report["reason_codes"] = list(plan.reason_codes)
        return report

    manifests = _load_manifests(raw_dir, computed_at=now)

    # HELD-position families (priority tier i). Read-only on the trades DB (mode=ro — the trigger
    # NEVER writes zeus_trades; K1 DB split). Fail-soft to empty: prioritization is best-effort.
    held: set[tuple[str, str, str]] = set()
    if trades_db is not None and Path(trades_db).exists():
        try:
            conn_t = sqlite3.connect(f"file:{Path(trades_db)}?mode=ro", uri=True, timeout=5.0)
            try:
                held = _held_position_families(conn_t)
            finally:
                conn_t.close()
        except Exception as exc:  # noqa: BLE001 — prioritization is best-effort, never fatal
            _LOG.debug("cycle-advance held-position read failed (no prioritization): %s", exc)

    conn = _connect(forecast_db, write_class="live")
    conn.row_factory = sqlite3.Row
    try:
        ensure_replacement_forecast_shadow_schema(conn)
        freshest = freshest_materializable_cycle(conn)
        if freshest is None:
            report["status"] = "CYCLE_ADVANCE_NO_MATERIALIZABLE_CYCLE"
            return report
        report["freshest_materializable_cycle"] = freshest.isoformat()

        # PRIORITY ORDER: HELD families first (tier i), then nearest-target-first (mirrors the
        # seed-budget K-decision — far-date shadow scopes must not starve the tradeable day0/day1
        # money scopes of the per-tick enqueue budget). A single sort key encodes both tiers.
        def _priority_key(r) -> tuple:
            scope = (str(r.city), str(r.target_date), str(r.temperature_metric))
            is_held = scope in held
            return (0 if is_held else 1, str(r.target_date), str(r.city), str(r.temperature_metric))

        enqueued = 0
        for row in sorted(plan.rows, key=_priority_key):
            if enqueued >= max(1, int(limit)):
                break
            city = str(row.city)
            target_date = str(row.target_date)
            metric = str(row.temperature_metric)
            scope = (city, target_date, metric)
            is_held = scope in held
            # DAY0 GUARD (mirrors seed discovery + fusion-upgrade trigger): a started local day's
            # scope needs the observed-extreme path, not a plain re-materialization.
            if bool(getattr(row, "day0_observed_extreme_required", False)):
                report["day0_skipped"] = int(report["day0_skipped"]) + 1
                continue
            report["scopes_checked"] = int(report["scopes_checked"]) + 1
            try:
                verdict = scope_needs_cycle_advance(
                    conn, city=city, target_date=target_date, metric=metric, freshest_cycle=freshest
                )
            except Exception as exc:  # noqa: BLE001 — per-scope fail-soft
                _LOG.debug("cycle-advance comparison failed for %s/%s/%s: %s", city, target_date, metric, exc)
                continue
            if not verdict["needs_advance"]:
                continue
            report["advances_detected"] = int(report["advances_detected"]) + 1
            if is_held:
                report["held_advances_detected"] = int(report["held_advances_detected"]) + 1
            consumed_cycle_iso = str(verdict["consumed_cycle"])
            target_cycle_iso = str(verdict["target_cycle"])
            if _already_enqueued(
                conn, city=city, target_date=target_date, metric=metric, target_cycle_iso=target_cycle_iso
            ):
                report["already_enqueued"] = int(report["already_enqueued"]) + 1
                continue
            try:
                seed_file = _build_and_write_advance_seed(
                    conn,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    manifests=manifests,
                    raw_dir=raw_dir,
                    seed_path=seed_path,
                    computed_at=now,
                    build_seed=build_replacement_forecast_materialization_seed,
                    latest_baseline_coverage=latest_baseline_coverage_for_replacement_seed,
                    market_bins=market_bins_for_replacement_seed,
                    write_seed=write_seed,
                    latest_manifest=_latest_manifest,
                    manifest_path_value=_manifest_path_value,
                    manifest_base_dir=_manifest_base_dir,
                    resolve_path=_resolve_path,
                    seed_name=_seed_name,
                    expected_identity=expected_replacement_dependency_identity_by_role,
                )
            except Exception as exc:  # noqa: BLE001 — per-scope fail-soft
                _LOG.debug("cycle-advance seed build failed for %s/%s/%s: %s", city, target_date, metric, exc)
                continue
            if seed_file is None:
                report["manifest_missing"] = int(report["manifest_missing"]) + 1
                continue
            inserted = _record_enqueue(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                consumed_cycle_iso=consumed_cycle_iso,
                target_cycle_iso=target_cycle_iso,
                held_position=is_held,
                seed_file=str(seed_file),
            )
            conn.commit()
            if inserted:
                enqueued += 1
                report["seeds_enqueued"] = int(report["seeds_enqueued"]) + 1
                if is_held:
                    report["held_seeds_enqueued"] = int(report["held_seeds_enqueued"]) + 1
                report["enqueued"].append(
                    {
                        "city": city,
                        "target_date": target_date,
                        "metric": metric,
                        "held_position": is_held,
                        "consumed_cycle": consumed_cycle_iso,
                        "target_cycle": target_cycle_iso,
                        "seed_file": str(seed_file),
                    }
                )
            else:
                report["already_enqueued"] = int(report["already_enqueued"]) + 1
    finally:
        conn.close()
    return report


def _build_and_write_advance_seed(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    manifests,
    raw_dir: Path,
    seed_path: Path,
    computed_at: datetime,
    build_seed,
    latest_baseline_coverage,
    market_bins,
    write_seed,
    latest_manifest,
    manifest_path_value,
    manifest_base_dir,
    resolve_path,
    seed_name,
    expected_identity,
) -> Path | None:
    """Build one re-materialization seed for a scope using the existing seed-builder pieces and
    write it into seed_dir. Returns the seed Path, or None when the required manifests/context are
    absent (the scope's raw inputs for the fresh cycle are not yet on disk — recorded as
    manifest_missing, retried next tick once they land). The seed builder pins source_cycle_time to
    the LATEST manifest cycle, so the re-materialized posterior advances onto the fresh cycle and the
    materializer's monotone guard admits it (request cycle >= current posterior cycle). Mirrors the
    fusion-upgrade trigger's _build_and_write_upgrade_seed (single seed-build shape)."""
    expected = expected_identity(metric)
    aifs = latest_manifest(
        manifests,
        source_id=expected["aifs_sampled_2t"].source_id,
        data_version=expected["aifs_sampled_2t"].data_version,
        city=city,
        target_date=target_date,
    )
    openmeteo = latest_manifest(
        manifests,
        source_id=expected["openmeteo_ifs9_anchor"].source_id,
        data_version=expected["openmeteo_ifs9_anchor"].data_version,
        city=city,
        target_date=target_date,
    )
    if aifs is None or openmeteo is None:
        return None
    aifs_samples = manifest_path_value(aifs, "aifs_samples_json") or manifest_path_value(aifs, "sample_points_json")
    aifs_grib = None if aifs_samples else aifs.artifact_path
    openmeteo_payload = manifest_path_value(openmeteo, "openmeteo_payload_json") or openmeteo.artifact_path
    precision_metadata = manifest_path_value(openmeteo, "precision_metadata_json")
    if not (aifs_samples or aifs_grib) or not openmeteo_payload or not precision_metadata:
        return None
    coverage = latest_baseline_coverage(conn, city=city, target_date=target_date, temperature_metric=metric)
    bins = market_bins(conn, city=city, target_date=target_date, temperature_metric=metric)
    if coverage is None or not bins:
        return None
    aifs_base_dir = manifest_base_dir(aifs, fallback=raw_dir)
    openmeteo_base_dir = manifest_base_dir(openmeteo, fallback=raw_dir)
    seed_result = build_seed(
        city=city,
        target_date=target_date,
        temperature_metric=metric,
        market_bins=bins,
        baseline_coverage=coverage,
        aifs_manifest=aifs,
        openmeteo_manifest=openmeteo,
        openmeteo_payload_json=resolve_path(openmeteo_payload, base_dir=openmeteo_base_dir),
        precision_metadata_json=resolve_path(precision_metadata, base_dir=openmeteo_base_dir),
        computed_at=computed_at,
        base_dir=seed_path,
        aifs_samples_json=None if aifs_samples is None else resolve_path(aifs_samples, base_dir=aifs_base_dir),
        aifs_grib_path=None if aifs_grib is None else resolve_path(aifs_grib, base_dir=aifs_base_dir),
    )
    if not seed_result.ok or seed_result.seed is None:
        return None
    # Honest re-materialization provenance: this seed exists because a NEWER cycle landed, not a
    # fresh first materialization. Threaded into provenance_json so the posterior records WHY.
    seed_payload: dict[str, object] = dict(seed_result.seed)
    seed_payload["upgrade_trigger"] = "newer_cycle_ingested"
    seed_file = seed_path / seed_name(
        {"city": city, "target_date": target_date, "temperature_metric": metric},
        computed_at=computed_at,
    )
    write_seed(seed_file, seed_payload)
    return seed_file
