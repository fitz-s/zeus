# Created: 2026-06-12
# Last reused or audited: 2026-06-12 (external review FINDING 2: per-family materializable-cycle
#   gate + typed leg-artifact-missing reason + loud held-family read failure)
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
materializable under the current live dependency identity. After the AIFS removal, that live
materialization leg is the OM9 anchor; the previous two-leg AIFS+OM9 high-water mark is not allowed
to gate live redecision.

Prioritization (operator directive 2026-06-12): (i) families with HELD positions (zeus_trades
position_current, read-only) first, then (ii) families with markets in their active trading window
(the current-target plan already restricts to token-bearing markets with target_date >= today).
Bounded per tick by the fair-cursor budget (Wave1B precedent — count only WRITTEN seeds, never a
numeric drop-cap on the candidate set).

Idempotency: cycle_advance_enqueues UNIQUE(city, target_date, metric, target_cycle_time). A scope is
re-enqueued AT MOST ONCE per target-cycle advance once a real seed exists. A typed gap row
(manifest absent) is healable: when the same target cycle's artifact later appears, the row is
updated with the seed file instead of blocking the repair. Fail-soft throughout: any per-scope error
is logged and skipped; the function never raises into the poll.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.data.replacement_forecast_readiness import SOURCE_ID

_LOG = logging.getLogger("zeus.replacement_cycle_advance_trigger")

UTC = timezone.utc

_ANCHOR_LEG_SOURCE_ID = "openmeteo_ecmwf_ifs_9km"
_HELD_REHEAL_COOLDOWN = timedelta(minutes=30)


def _parse_cycle(value: object) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        return None


def normalize_observation_version(value: object) -> str | None:
    """Canonicalize an observation-version timestamp to a fixed-width UTC ISO string
    ``YYYY-MM-DDTHH:MM:SS+00:00`` so lexicographic comparison equals INSTANT comparison across
    timezone offsets and ``Z``/``+00:00``/fractional-second spellings (consult REQ-20260623-184115
    HIGH: raw-ISO string compare can churn on equal instants and SUPPRESS truly-newer offset
    instants). Returns None when unparseable — callers treat that as "no recorded version" so a
    pre-normalization marker heals to the normalized form on the next write."""
    parsed = _parse_cycle(value)
    if parsed is None:
        return None
    return parsed.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def consumed_cycle_dt(value: str) -> datetime:
    """Parse a consumed/target cycle ISO string back to a UTC datetime for comparison. The verdict
    serializes cycles to ISO; the family-scope gate compares against them, so we round-trip here.
    Raises on an unparseable value (the verdict produced it, so it must parse — fail-loud)."""
    parsed = _parse_cycle(value)
    if parsed is None:
        raise ValueError(f"unparseable consumed cycle: {value!r}")
    return parsed


def _fresh_enough_to_retry_held_reheal(enqueued_at: object, *, now: datetime | None = None) -> bool:
    """Bound same-scope held re-heal retries so one failed materialization cannot flood the queue."""
    parsed = _parse_cycle(enqueued_at)
    if parsed is None:
        return True
    return (now or datetime.now(tz=UTC)).astimezone(UTC) - parsed >= _HELD_REHEAL_COOLDOWN


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
    """The freshest in-universe cycle for which the current live raw artifact leg is ingested."""

    return _per_leg_max_cycle(conn, _ANCHOR_LEG_SOURCE_ID)


def family_materializable_cycle(
    manifests,
    *,
    city: str,
    target_date: str,
    metric: str,
    city_timezone: str | None = None,
    expected_identity,
    latest_manifest,
) -> tuple[datetime | None, tuple[tuple[str, str], ...]]:
    """FINDING 2 (external review 2026-06-12) — the materializable cycle AT FAMILY SCOPE.

    This is the SAME authority, narrowed to a scope: a cycle is materializable for THIS family iff
    the current live dependency identity's raw artifact leg has a manifest for THIS
    (city, target_date). After the AIFS removal, that is the OM9 anchor leg. Returns
    (cycle, missing_legs). cycle is None when the live leg's manifest is absent for the family;
    missing_legs is the tuple of (role, source_id) legs that were absent.
    """
    expected = expected_identity(metric)
    legs = (("openmeteo_ifs9_anchor", expected["openmeteo_ifs9_anchor"]),)
    leg_cycles: list[datetime] = []
    missing: list[tuple[str, str]] = []
    for role, identity in legs:
        man = latest_manifest(
            manifests,
            source_id=identity.source_id,
            data_version=identity.data_version,
            city=city,
            target_date=target_date,
            city_timezone=city_timezone,
        )
        if man is None:
            missing.append((role, str(identity.source_id)))
            continue
        cyc = man.source_cycle_time
        if not isinstance(cyc, datetime):
            cyc = _parse_cycle(cyc)
        if cyc is None:
            missing.append((role, str(identity.source_id)))
            continue
        leg_cycles.append(cyc.astimezone(UTC) if cyc.tzinfo else cyc.replace(tzinfo=UTC))
    if missing or not leg_cycles:
        return None, tuple(missing)
    return min(leg_cycles), ()


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

    Read-only from zeus_trades.position_current. A family is HELD only when it has chain-confirmed
    economic exposure. Pending entries, local-only rows, and open-row ghosts are deliberately
    excluded: new-money redecision admission comes from the positive-edge screen, while held-family
    admission is reserved for money already at risk. Fail-soft: any read/schema error -> empty set
    (no prioritization, never a crash).
    """
    try:
        cols = {
            str(row[1])
            for row in conn_trades.execute("PRAGMA table_info(position_current)").fetchall()
        }
        if "position_current" not in {
            str(row[0])
            for row in conn_trades.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='position_current'"
            ).fetchall()
        }:
            return set()
        required_chain_cols = {"chain_state", "chain_shares", "chain_cost_basis_usd"}
        if not required_chain_cols.issubset(cols):
            return set()
        rows = conn_trades.execute(
            """
            SELECT DISTINCT city, target_date, temperature_metric
            FROM position_current
            WHERE COALESCE(phase, '') IN ('active', 'day0_window', 'pending_exit')
              AND COALESCE(chain_state, '') = 'synced'
              AND COALESCE(chain_shares, 0) > 0
              AND COALESCE(chain_cost_basis_usd, 0) > 0
              AND city IS NOT NULL AND target_date IS NOT NULL
              AND temperature_metric IS NOT NULL
            """
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        # FINDING 2 / MEDIUM (external review 2026-06-12): a held-family read FAILURE silently
        # dropped held-position priority — the families whose stale belief most directly risks
        # money would be processed as if NO position were held (nearest-target-first only),
        # losing their re-materialization priority WITHOUT any signal. The poll must NOT crash on
        # this (prioritization is best-effort), but the consequence must be LOUD so the dropped
        # priority is diagnosable, not invisible.
        _LOG.error(
            "cycle-advance HELD-position read FAILED — held families lose re-materialization "
            "PRIORITY this tick (processed as non-held, nearest-target-first only); stale held "
            "belief may not be refreshed first: %s",
            exc,
        )
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
    allow_missing_seed_file_reenqueue: bool = False,
    day0_observed_extreme_observation_time: str | None = None,
) -> bool:
    """True iff a real re-materialization seed already exists for this exact target cycle.

    A ``CYCLE_LEG_ARTIFACT_MISSING`` row is a visible, typed gap marker, not a terminal enqueue.
    Returning False for that row lets the next tick heal it when the same target cycle's artifact
    finally lands; ``_record_enqueue`` updates the marker in place under the UNIQUE bound.

    OBSERVATION-VERSION RE-ENQUEUE (same-day exit-blindness fix 2026-06-23): for a held/day0
    reseed, ``day0_observed_extreme_observation_time`` is the fresh observed running-max version.
    The model cycle (``target_cycle_time``) does NOT advance intraday on the settlement day, so
    keying idempotency on it alone freezes the day0-conditioned posterior while the observed
    extreme climbs/plateaus (Toronto NO@24 -98.94% incident). When the supplied observation
    version is strictly NEWER than (or the marker has no recorded) observation version, return
    False so the fresh observation re-materializes the posterior — even though the model-cycle
    seed still exists. Supplying no observation version (non-day0 / future-date reseed) preserves
    the original model-cycle idempotency unchanged.
    """
    try:
        row = conn.execute(
            """
            SELECT seed_file, reason, held_position, day0_observed_extreme_observation_time, enqueued_at
            FROM cycle_advance_enqueues
            WHERE city = ? AND target_date = ? AND metric = ? AND target_cycle_time = ?
            LIMIT 1
            """,
            (city, target_date, metric, target_cycle_iso),
        ).fetchone()
    except Exception:
        return False
    if row is None:
        return False
    seed_file = str((row["seed_file"] if hasattr(row, "keys") else row[0]) or "")
    reason = str((row["reason"] if hasattr(row, "keys") else row[1]) or "")
    if not seed_file and reason.startswith("CYCLE_LEG_ARTIFACT_MISSING:"):
        return False
    if day0_observed_extreme_observation_time is not None:
        incoming_version = normalize_observation_version(day0_observed_extreme_observation_time)
        recorded_version = normalize_observation_version(
            row["day0_observed_extreme_observation_time"] if hasattr(row, "keys") else row[3]
        )
        # Both normalized to fixed-width UTC ISO => lexicographic compare == instant compare.
        if incoming_version is not None and (
            recorded_version is None or incoming_version > recorded_version
        ):
            return False
    # HELD-POSITION RE-HEAL (live freeze fix 2026-06-21): a held (money-at-risk) marker whose seed
    # was built then processed/moved out of the live queue but produced NO posterior — the
    # single_runs serving race materializes BLOCKED on REQUIREMENTS_NOT_MET — must NOT suppress
    # re-enqueue forever, else the held belief freezes (Panama City 2026-06-22 stuck 13h+) ->
    # BELIEF_AUTHORITY_FAULT fail-closed HOLD -> reversal exit starved ("observe but not act").
    # Auto-enable the missing-seed re-enqueue for held rows, mirroring the day0 escape hatch.
    # Bounded by the upstream needs_advance/coverage gate, so a successfully materialized cycle
    # (posterior present) never reaches here to churn; a still-PRESENT pending seed also suppresses.
    held = bool((row["held_position"] if hasattr(row, "keys") else row[2]) or 0)
    if (allow_missing_seed_file_reenqueue or held) and seed_file and not Path(seed_file).exists():
        # A moved seed file is normal after the queue processed it. Re-enqueueing immediately every
        # poll tick creates a live backlog of identical failed work. Only Day0 observation-version
        # advancement bypasses this cooldown above; otherwise retry the same scope/cycle after the
        # cooling period or when a newer model cycle changes the idempotency key.
        if held and not allow_missing_seed_file_reenqueue:
            enqueued_at = row["enqueued_at"] if hasattr(row, "keys") else row[4]
            if not _fresh_enough_to_retry_held_reheal(enqueued_at):
                return True
        return False
    return True


def _promote_existing_enqueue_to_held(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    target_cycle_iso: str,
) -> bool:
    """Upgrade an existing enqueue row into the held-position priority tier.

    Monitor-triggered single-family reseeds are money-at-risk work even when a
    broad cycle scanner already wrote the idempotency row first. The unique
    enqueue key must prevent duplicate seeds, not permanently freeze the row in
    the non-held tier.
    """
    before = conn.total_changes
    conn.execute(
        """
        UPDATE cycle_advance_enqueues
           SET held_position = 1,
               enqueued_at = ?
         WHERE city = ?
           AND target_date = ?
           AND metric = ?
           AND target_cycle_time = ?
           AND COALESCE(held_position, 0) != 1
        """,
        (
            datetime.now(tz=UTC).isoformat(),
            city,
            target_date,
            metric,
            target_cycle_iso,
        ),
    )
    return conn.total_changes > before


def _record_enqueue(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    consumed_cycle_iso: str,
    target_cycle_iso: str,
    held_position: bool,
    seed_file: str | None,
    reason: str | None = None,
    replace_existing_seed_file: bool = False,
    day0_observed_extreme_observation_time: str | None = None,
) -> bool:
    """Write the idempotency marker. Returns True iff this call inserted the row (False = a
    concurrent/prior enqueue already recorded it, via the UNIQUE index INSERT OR IGNORE).

    ``day0_observed_extreme_observation_time`` records the OBSERVATION VERSION this enqueue was
    built at (same-day exit-blindness fix 2026-06-23). It advances the marker so a later held/day0
    reseed at the SAME model cycle but a NEWER observed running-max version is re-enqueued by
    ``_already_enqueued`` instead of frozen.

    ``reason`` carries a typed status for the row. None for a normal successful enqueue (the
    presence of seed_file is the success signal); a typed string (FINDING 2) when the row instead
    records a per-family leg-artifact gap (CYCLE_LEG_ARTIFACT_MISSING:<source>:<cycle>) so the
    blocked family is VISIBLE in the queue rather than an invisible manifest_missing skip. Both
    share the SAME UNIQUE(scope, target_cycle) bound, so a gap row and a later success row for the
    same (scope, target-cycle) cannot both exist — the gap heals into the success on the next tick."""
    # Persist the observation version in canonical fixed-width UTC ISO so the marker comparison in
    # _already_enqueued (and the monotone replacement guard below) is instant-accurate, not
    # spelling-sensitive (consult REQ-20260623-184115 HIGH).
    day0_observed_extreme_observation_time = normalize_observation_version(
        day0_observed_extreme_observation_time
    )
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO cycle_advance_enqueues
            (enqueued_at, city, target_date, metric, consumed_cycle_time, target_cycle_time,
             held_position, seed_file, reason, day0_observed_extreme_observation_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            reason,
            day0_observed_extreme_observation_time,
        ),
    )
    if conn.total_changes > before:
        return True
    if seed_file:
        update_before = conn.total_changes
        # HELD-POSITION RE-HEAL (live freeze fix 2026-06-21): a held re-enqueue must REPLACE an
        # existing seed-built marker (the moved/BLOCKED row), pairing with the _already_enqueued
        # held re-heal above. Without this, INSERT OR IGNORE no-ops and the default NULL-seed gap
        # UPDATE below cannot rewrite a seed-bearing held row, so the re-heal never completes and
        # the held belief stays frozen.
        if replace_existing_seed_file or held_position:
            conn.execute(
                """
                UPDATE cycle_advance_enqueues
                   SET enqueued_at = ?,
                       consumed_cycle_time = ?,
                       held_position = ?,
                       seed_file = ?,
                       reason = ?,
                       day0_observed_extreme_observation_time = COALESCE(?, day0_observed_extreme_observation_time)
                 WHERE city = ?
                   AND target_date = ?
                   AND metric = ?
                   AND target_cycle_time = ?
                   -- MONOTONE day0 version guard (consult REQ-20260623-184115 HIGH): an out-of-order
                   -- OLDER observation-version writer must NOT regress the marker/seed after a newer
                   -- one won. Non-day0 writers (version NULL) keep the held re-heal behaviour.
                   AND (
                       ? IS NULL
                       OR day0_observed_extreme_observation_time IS NULL
                       OR ? > day0_observed_extreme_observation_time
                   )
                """,
                (
                    datetime.now(tz=UTC).isoformat(),
                    consumed_cycle_iso,
                    1 if held_position else 0,
                    seed_file,
                    reason,
                    day0_observed_extreme_observation_time,
                    city,
                    target_date,
                    metric,
                    target_cycle_iso,
                    day0_observed_extreme_observation_time,
                    day0_observed_extreme_observation_time,
                ),
            )
            return conn.total_changes > update_before
        conn.execute(
            """
            UPDATE cycle_advance_enqueues
               SET enqueued_at = ?,
                   consumed_cycle_time = ?,
                   held_position = ?,
                   seed_file = ?,
                   reason = ?,
                   day0_observed_extreme_observation_time = COALESCE(?, day0_observed_extreme_observation_time)
             WHERE city = ?
               AND target_date = ?
               AND metric = ?
               AND target_cycle_time = ?
               AND seed_file IS NULL
               AND COALESCE(reason, '') LIKE 'CYCLE_LEG_ARTIFACT_MISSING:%'
            """,
            (
                datetime.now(tz=UTC).isoformat(),
                consumed_cycle_iso,
                1 if held_position else 0,
                seed_file,
                reason,
                day0_observed_extreme_observation_time,
                city,
                target_date,
                metric,
                target_cycle_iso,
            ),
        )
        return conn.total_changes > update_before
    return False


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
        ensure_replacement_forecast_live_schema,
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
        "leg_artifact_missing": 0,
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
        ensure_replacement_forecast_live_schema(conn)
        freshest = freshest_materializable_cycle(conn)
        if freshest is None:
            report["status"] = "CYCLE_ADVANCE_NO_MATERIALIZABLE_CYCLE"
            return report
        report["freshest_materializable_cycle"] = freshest.isoformat()

        # PRIORITY ORDER: HELD families first (tier i), then nearest-target-first (mirrors the
        # seed-budget K-decision — far-date non-tradeable scopes must not starve the tradeable day0/day1
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
            # FINDING 2 (external review 2026-06-12): the verdict above used the UNIVERSE-wide
            # freshest cycle, which can be a FALSE advance signal when a leg's raw artifact is
            # missing for THIS family at that cycle. Re-check materializability AT FAMILY SCOPE.
            try:
                from src.config import cities_by_name  # noqa: PLC0415

                city_cfg = cities_by_name.get(city)
                city_timezone = str(getattr(city_cfg, "timezone", "") or "") or None
                family_cycle, missing_legs = family_materializable_cycle(
                    manifests,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    city_timezone=city_timezone,
                    expected_identity=expected_replacement_dependency_identity_by_role,
                    latest_manifest=_latest_manifest,
                )
            except Exception as exc:  # noqa: BLE001 — per-scope fail-soft
                _LOG.debug("cycle-advance family-scope check failed for %s/%s/%s: %s", city, target_date, metric, exc)
                continue
            if missing_legs:
                # A held/active family lacks one leg's raw artifact at the freshest cycle. Do NOT
                # silently increment manifest_missing — record a typed, idempotent reason row so the
                # ALWAYS-DECIDABLE gap is VISIBLE in the queue and a fetch-repair lane can act on it.
                reason = "CYCLE_LEG_ARTIFACT_MISSING:" + ",".join(
                    f"{src}@{target_cycle_iso}" for _role, src in missing_legs
                )
                report["leg_artifact_missing"] = int(report.get("leg_artifact_missing", 0)) + 1
                _LOG.error(
                    "cycle-advance LEG ARTIFACT MISSING for %s/%s/%s at cycle %s — held=%s family "
                    "cannot advance (missing legs: %s); recording typed gap (no silent skip)",
                    city, target_date, metric, target_cycle_iso, is_held,
                    [src for _role, src in missing_legs],
                )
                if not _already_enqueued(
                    conn, city=city, target_date=target_date, metric=metric,
                    target_cycle_iso=target_cycle_iso,
                ):
                    _record_enqueue(
                        conn, city=city, target_date=target_date, metric=metric,
                        consumed_cycle_iso=consumed_cycle_iso, target_cycle_iso=target_cycle_iso,
                        held_position=is_held, seed_file=None, reason=reason,
                    )
                    conn.commit()
                continue
            # Both legs present for the family: the family-scoped cycle is the authoritative target.
            # If it is NOT strictly newer than the consumed cycle, the global verdict was a false
            # positive for this family (the fresher universe cycle was carried by OTHER cities) —
            # honest no-op, not an advance.
            if family_cycle is None or family_cycle <= consumed_cycle_dt(consumed_cycle_iso):
                continue
            target_cycle_iso = family_cycle.isoformat()
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


def enqueue_single_family_cycle_advance_reseed(
    *,
    forecast_db: Path | str,
    seed_dir: Path | str,
    raw_manifest_dir: Path | str,
    city: str,
    target_date: str,
    metric: str,
    computed_at: datetime | None = None,
    day0_observed_extreme_c: float | None = None,
    day0_observed_extreme_source: str | None = None,
    day0_observed_extreme_observation_time: str | None = None,
    day0_observed_extreme_sample_count: int | None = None,
    day0_observed_extreme_unit: str | None = None,
    held_position: bool = False,
) -> dict[str, object]:
    """ALWAYS-DECIDABLE invariant — Build 2 (operator law 2026-06-12). Single-family variant of
    ``enqueue_cycle_advance_reseeds``: when the reactor/monitor finds ONE family blocked on a
    STALE or ABSENT replacement posterior, materialize THAT family's posterior onto the freshest
    materializable cycle — no plan scan, no fan-out. Same seed builder, same idempotency marker
    (``cycle_advance_enqueues`` UNIQUE(scope, target_cycle)) as the poll-lane batch variant, so a
    family already enqueued by the poll never double-enqueues here and vice-versa.

    Fail-soft throughout: any error returns a status dict, never raises into the reactor cycle.
    Returns a compact report ({status, enqueued, seed_file, ...}).
    """
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
        ensure_replacement_forecast_live_schema,
    )

    now = (computed_at or datetime.now(tz=UTC)).astimezone(UTC)
    forecast_db = Path(forecast_db)
    seed_path = Path(seed_dir)
    raw_dir = Path(raw_manifest_dir)
    city = str(city)
    target_date = str(target_date)
    metric = str(metric)
    has_day0_observed_extreme = day0_observed_extreme_c is not None
    report: dict[str, object] = {
        "status": "SINGLE_FAMILY_CYCLE_ADVANCE",
        "city": city,
        "target_date": target_date,
        "metric": metric,
        "held_position": bool(held_position),
        "enqueued": False,
    }
    if not forecast_db.exists():
        report["status"] = "CYCLE_ADVANCE_FORECAST_DB_MISSING"
        return report
    if metric not in {"high", "low"}:
        report["status"] = "CYCLE_ADVANCE_METRIC_INVALID"
        return report

    manifests = _load_manifests(raw_dir, computed_at=now)
    conn = _connect(forecast_db, write_class="live")
    conn.row_factory = sqlite3.Row
    try:
        ensure_replacement_forecast_live_schema(conn)
        freshest = freshest_materializable_cycle(conn)
        if freshest is None:
            report["status"] = "CYCLE_ADVANCE_NO_MATERIALIZABLE_CYCLE"
            return report
        report["freshest_materializable_cycle"] = freshest.isoformat()
        verdict = scope_needs_cycle_advance(
            conn, city=city, target_date=target_date, metric=metric, freshest_cycle=freshest
        )
        consumed_cycle_iso = (
            str(verdict["consumed_cycle"])
            if verdict.get("consumed_cycle") is not None
            else "NO_LIVE_POSTERIOR"
        )
        target_cycle_iso = str(verdict["target_cycle"] or freshest.isoformat())
        family_cycle, missing_legs = family_materializable_cycle(
            manifests,
            city=city,
            target_date=target_date,
            metric=metric,
            expected_identity=expected_replacement_dependency_identity_by_role,
            latest_manifest=_latest_manifest,
        )
        if missing_legs:
            # Record a typed, idempotent gap row instead of a silent manifest_missing skip.
            reason = "CYCLE_LEG_ARTIFACT_MISSING:" + ",".join(
                f"{src}@{target_cycle_iso}" for _role, src in missing_legs
            )
            _LOG.error(
                "single-family cycle-advance LEG ARTIFACT MISSING for %s/%s/%s at cycle %s "
                "(missing legs: %s) — recording typed gap (no silent skip)",
                city, target_date, metric, target_cycle_iso, [src for _role, src in missing_legs],
            )
            if not _already_enqueued(
                conn, city=city, target_date=target_date, metric=metric, target_cycle_iso=target_cycle_iso
            ):
                _record_enqueue(
                    conn, city=city, target_date=target_date, metric=metric,
                    consumed_cycle_iso=consumed_cycle_iso, target_cycle_iso=target_cycle_iso,
                    held_position=held_position, seed_file=None, reason=reason,
                )
                conn.commit()
            elif held_position:
                report["held_priority_promoted"] = _promote_existing_enqueue_to_held(
                    conn,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    target_cycle_iso=target_cycle_iso,
                )
                conn.commit()
            report["status"] = "CYCLE_ADVANCE_LEG_ARTIFACT_MISSING"
            report["reason"] = reason
            report["consumed_cycle"] = consumed_cycle_iso
            report["target_cycle"] = target_cycle_iso
            return report
        if not verdict["needs_advance"]:
            if verdict.get("consumed_cycle") is not None:
                # No newer cycle than the one the posterior already consumed: the staleness is not a
                # missed-cycle gap this lane can cure. Honest no-op.
                report["status"] = "CYCLE_ADVANCE_NOT_NEEDED"
                report["consumed_cycle"] = verdict["consumed_cycle"]
                return report
            if family_cycle is None:
                report["status"] = "CYCLE_ADVANCE_MANIFEST_MISSING"
                report["consumed_cycle"] = None
                return report
            target_cycle_iso = family_cycle.isoformat()
            if _already_enqueued(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                target_cycle_iso=target_cycle_iso,
                allow_missing_seed_file_reenqueue=has_day0_observed_extreme,
                day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
            ):
                if held_position:
                    report["held_priority_promoted"] = _promote_existing_enqueue_to_held(
                        conn,
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        target_cycle_iso=target_cycle_iso,
                    )
                    conn.commit()
                report["status"] = "CYCLE_ADVANCE_ALREADY_ENQUEUED"
                return report
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
                upgrade_trigger="missing_live_posterior_reseed",
                day0_observed_extreme_c=day0_observed_extreme_c,
                day0_observed_extreme_source=day0_observed_extreme_source,
                day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
                day0_observed_extreme_sample_count=day0_observed_extreme_sample_count,
                day0_observed_extreme_unit=day0_observed_extreme_unit,
            )
            if seed_file is None:
                report["status"] = "CYCLE_ADVANCE_MANIFEST_MISSING"
                return report
            inserted = _record_enqueue(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                consumed_cycle_iso="NO_LIVE_POSTERIOR",
                target_cycle_iso=target_cycle_iso,
                held_position=held_position,
                seed_file=str(seed_file),
                reason="MISSING_LIVE_POSTERIOR",
                replace_existing_seed_file=has_day0_observed_extreme,
                day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
            )
            conn.commit()
            report["enqueued"] = bool(inserted)
            report["status"] = (
                "CYCLE_ADVANCE_FIRST_MATERIALIZATION_ENQUEUED"
                if inserted
                else "CYCLE_ADVANCE_ALREADY_ENQUEUED"
            )
            report["seed_file"] = str(seed_file)
            report["consumed_cycle"] = None
            report["target_cycle"] = target_cycle_iso
            return report
        if family_cycle is None or family_cycle <= consumed_cycle_dt(consumed_cycle_iso):
            # Global verdict was a false positive for this family (fresher cycle carried by other
            # cities). Honest no-op.
            report["status"] = "CYCLE_ADVANCE_NOT_NEEDED"
            report["consumed_cycle"] = consumed_cycle_iso
            return report
        target_cycle_iso = family_cycle.isoformat()
        if _already_enqueued(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            target_cycle_iso=target_cycle_iso,
            allow_missing_seed_file_reenqueue=has_day0_observed_extreme,
            day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
        ):
            if held_position:
                report["held_priority_promoted"] = _promote_existing_enqueue_to_held(
                    conn,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    target_cycle_iso=target_cycle_iso,
                )
                conn.commit()
            report["status"] = "CYCLE_ADVANCE_ALREADY_ENQUEUED"
            return report
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
            upgrade_trigger="newer_cycle_ingested",
            day0_observed_extreme_c=day0_observed_extreme_c,
            day0_observed_extreme_source=day0_observed_extreme_source,
            day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
            day0_observed_extreme_sample_count=day0_observed_extreme_sample_count,
            day0_observed_extreme_unit=day0_observed_extreme_unit,
        )
        if seed_file is None:
            report["status"] = "CYCLE_ADVANCE_MANIFEST_MISSING"
            return report
        inserted = _record_enqueue(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            consumed_cycle_iso=consumed_cycle_iso,
            target_cycle_iso=target_cycle_iso,
            held_position=held_position,
            seed_file=str(seed_file),
            replace_existing_seed_file=has_day0_observed_extreme,
            day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
        )
        conn.commit()
        report["enqueued"] = bool(inserted)
        report["status"] = "CYCLE_ADVANCE_ENQUEUED" if inserted else "CYCLE_ADVANCE_ALREADY_ENQUEUED"
        report["seed_file"] = str(seed_file)
        report["consumed_cycle"] = consumed_cycle_iso
        report["target_cycle"] = target_cycle_iso
    except Exception as exc:  # noqa: BLE001 — fail-soft: never raise into the reactor cycle
        _LOG.debug(
            "single-family cycle-advance failed for %s/%s/%s: %s", city, target_date, metric, exc
        )
        report["status"] = "CYCLE_ADVANCE_FAILSOFT_SKIPPED"
        report["error"] = str(exc)
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
    upgrade_trigger: str = "newer_cycle_ingested",
    day0_observed_extreme_c: float | None = None,
    day0_observed_extreme_source: str | None = None,
    day0_observed_extreme_observation_time: str | None = None,
    day0_observed_extreme_sample_count: int | None = None,
    day0_observed_extreme_unit: str | None = None,
) -> Path | None:
    """Build one re-materialization seed for a scope using the existing seed-builder pieces and
    write it into seed_dir. Returns the seed Path, or None when the required manifests/context are
    absent (the scope's raw inputs for the fresh cycle are not yet on disk — recorded as
    manifest_missing, retried next tick once they land). The seed builder pins source_cycle_time to
    the LATEST manifest cycle, so the re-materialized posterior advances onto the fresh cycle and the
    materializer's monotone guard admits it (request cycle >= current posterior cycle). Mirrors the
    fusion-upgrade trigger's _build_and_write_upgrade_seed (single seed-build shape)."""
    expected = expected_identity(metric)
    from src.config import cities_by_name  # noqa: PLC0415

    city_cfg = cities_by_name.get(city)
    city_timezone = str(getattr(city_cfg, "timezone", "") or "") or None
    openmeteo = latest_manifest(
        manifests,
        source_id=expected["openmeteo_ifs9_anchor"].source_id,
        data_version=expected["openmeteo_ifs9_anchor"].data_version,
        city=city,
        target_date=target_date,
        city_timezone=city_timezone,
    )
    if openmeteo is None:
        return None
    openmeteo_payload = manifest_path_value(openmeteo, "openmeteo_payload_json") or openmeteo.artifact_path
    precision_metadata = manifest_path_value(openmeteo, "precision_metadata_json")
    if not openmeteo_payload or not precision_metadata:
        return None
    coverage = latest_baseline_coverage(conn, city=city, target_date=target_date, temperature_metric=metric)
    bins = market_bins(conn, city=city, target_date=target_date, temperature_metric=metric)
    if coverage is None or not bins:
        return None
    openmeteo_base_dir = manifest_base_dir(openmeteo, fallback=raw_dir)
    seed_result = build_seed(
        city=city,
        target_date=target_date,
        temperature_metric=metric,
        market_bins=bins,
        baseline_coverage=coverage,
        openmeteo_manifest=openmeteo,
        openmeteo_payload_json=resolve_path(openmeteo_payload, base_dir=openmeteo_base_dir),
        precision_metadata_json=resolve_path(precision_metadata, base_dir=openmeteo_base_dir),
        computed_at=computed_at,
        base_dir=seed_path,
        day0_observed_extreme_c=day0_observed_extreme_c,
        day0_observed_extreme_source=day0_observed_extreme_source,
        day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
        day0_observed_extreme_sample_count=day0_observed_extreme_sample_count,
        day0_observed_extreme_unit=day0_observed_extreme_unit,
    )
    if not seed_result.ok or seed_result.seed is None:
        return None
    # Honest re-materialization provenance: this seed exists because a NEWER cycle landed, not a
    # fresh first materialization. Threaded into provenance_json so the posterior records WHY.
    seed_payload: dict[str, object] = dict(seed_result.seed)
    seed_payload["upgrade_trigger"] = upgrade_trigger
    seed_file = seed_path / seed_name(
        {"city": city, "target_date": target_date, "temperature_metric": metric},
        computed_at=computed_at,
    )
    write_seed(seed_file, seed_payload)
    return seed_file
