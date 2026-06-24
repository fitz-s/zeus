# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: Task #32 (operator 2026-06-11) — PARTIAL fusions never upgrade when late
#   instruments publish. The materializer reads CURRENT values from the persisted single_runs
#   capture (gem via previous_runs exception) at the OM9 anchor cycle; a provider whose
#   single_runs row was not yet persisted at materialize time is dropped, and the resulting
#   served<5 posterior is then marked "covered" (q_lcb NOT NULL) by all three coverage gates —
#   which key coverage on the baseline_b0 (ecmwf_open_data) run, BLIND to the bayes_precision_fusion decorrelated
#   instrument set. So the scope never re-materializes even after its 5th provider lands.
#   K-decision: ONE comparison — the latest posterior's served decorrelated-provider FAMILY set
#   vs the family set CAPTURABLE NOW at the SAME source_cycle_time. A strict superset = an
#   upgrade is available; enqueue exactly one re-materialization seed, idempotent per
#   (city, target, metric, cycle, capturable-family-superset).
"""SINGLE-AUTHORITY comparison + idempotent enqueue for the PARTIAL-fusion upgrade trigger.

The decorrelated-provider FAMILY mapping (`decorrelated_provider_families_of`) is the SOLE
authority for "which model belongs to which of the 5 decorrelated provider families". The
materializer's served/missing-provider determination imports it (single-builder), so the
trigger and the fusion can never disagree on what "served 5/5" means.

The comparison (`scope_capture_offers_larger_provider_set`) is the SOLE authority for
"does a scope's latest posterior need re-materialization because a new provider family is now
capturable". The seed-discovery / queue / plan coverage gates remain keyed on baseline_b0 +
q_lcb (their job is freshness/tradeable-grade, NOT instrument completeness); this module is the
ONE place the instrument-set dimension is evaluated, so the rule lives at exactly one site.

The enqueue (`enqueue_fusion_upgrade_reseeds`) writes a re-materialization seed via the EXISTING
seed builder + write_seed into the SAME seed_dir the materialize cycle already drains — no new
daemon, no parallel materialization path. Idempotency is a marker row in fusion_upgrade_enqueues
UNIQUE on (city, target_date, metric, source_cycle_time, capturable_family_set): a scope is
re-enqueued AT MOST ONCE per (cycle, capturable-family-superset) transition, so a still-missing
5th provider (gfs HTTP 400, jma off-cadence) never loops the queue.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from src.data.replacement_forecast_readiness import SOURCE_ID

_LOG = logging.getLogger("zeus.replacement_fusion_upgrade_trigger")

UTC = timezone.utc

# THE single authority mapping model -> decorrelated provider family. Mirrors exactly the
# materializer's per-provider check (replacement_forecast_materializer lines ~1012-1024): the
# physical providers each contribute ONE representative to the fusion, and a family is "served"
# when ANY of its members is in the fused set. The ECMWF anchor is intentionally NOT here: it is
# the PRIOR (not a decorrelated likelihood provider). icon_seamless was also NOT here and has since
# been removed from the candidate set entirely (2026-06-17 — it was the alias-dedup probe, not a
# provider). The materializer imports DECORRELATED_PROVIDER_FAMILIES so the two sites can never
# drift on what counts as a provider.
# 2026-06-17 COARSE-GLOBAL REMOVAL: the 0.25°/25km gfs_global and ~15km gem_global are dropped
# from the fusion (model_selection.DECORR_GLOBALS), so they are no longer family members here.
# NCEP is now repped ONLY by its CONUS nests (gfs_hrrr 3km / ncep_nbm ~13km) and CMC ONLY by the
# HRDPS 2.5km North-America nest — both DOMAIN-GATED. OUTSIDE those nest domains NCEP/CMC have no
# servable member and are STRUCTURALLY ABSENT for that city; the flat 5-family count would then
# false-flag them as "missing". `expected_provider_families_for_city` below is the per-city
# domain-aware expected set that replaces the flat count at the materializer's completeness gate.
# 2026-06-17 JMA DROP (operator, settlement-graded): jma_seamless (the only JMA member) is the
# coldest/least-precise global (lead-1 raw bias -1.46, MAE 2.124) and was dropped from the fusion
# (model_selection.DECORR_GLOBALS). The JMA family is therefore REMOVED entirely here — no member
# can ever serve, so it must never be expected anywhere. The contract is now {NCEP, DWD, CMC, UKMO}.
DECORRELATED_PROVIDER_FAMILIES: dict[str, tuple[str, ...]] = {
    "NCEP": ("gfs_hrrr", "ncep_nbm_conus"),
    "DWD": ("icon_d2", "icon_eu", "icon_global"),
    "CMC": ("gem_hrdps_continental",),
    "UKMO": ("ukmo_global_deterministic_10km", "ukmo_uk_deterministic_2km"),
}

# The GLOBAL maximum family count (every family servable). Retained as the fail-open fallback for
# expected_provider_families_for_city when a city's coords cannot be resolved; the LIVE
# completeness gate uses the per-city expected set, never this flat count.
EXPECTED_DECORRELATED_PROVIDER_COUNT = len(DECORRELATED_PROVIDER_FAMILIES)


def expected_provider_families_for_city(lat: float, lon: float, lead_days: int) -> frozenset[str]:
    """THE per-city, per-LEAD domain-aware expected provider-family set (2026-06-17 removal).

    A decorrelated provider family is EXPECTED for a city AT THIS LEAD only if it has a member
    that is SERVABLE there-and-then: a pure-global member (available worldwide at any lead) OR a
    domain-gated member whose polygon covers (lat, lon) AND whose max_lead_days cap is not
    exceeded at ``lead_days``. After the coarse-global drop, NCEP (gfs_hrrr / ncep_nbm, both
    CONUS) and CMC (gem_hrdps, N-America) are nest-only; outside those domains — OR at a lead
    PAST the nest's max_lead_days cap (gfs_hrrr=2, ncep_nbm=3, gem_hrdps=2) — they are NOT
    expected, so a non-CONUS/non-NA city, AND a CONUS/NA city at far lead, is COMPLETE on the
    pure globals {DWD, UKMO} (+ anchor) with no phantom PARTIAL flag and no upgrade re-enqueue.

    LEAD-AWARENESS IS LOAD-BEARING (2026-06-17 critic fix): lead 0 is NOT "the most permissive
    lead" — it is the OPPOSITE. A nest eligible at lead 0 becomes INELIGIBLE past its cap, so a
    lead-0 expected set over-expects NCEP/CMC at far lead (CONUS lead>=4 / NA lead>=3) and
    re-fires the exact phantom-PARTIAL + upgrade loop this contract exists to kill. The expected
    set MUST be evaluated at the lead the fusion actually serves (the city-local lead).

    The "is this member domain-gated" test is `_REGIONAL_DOMAIN_KEY` membership — the SAME gate
    `regional_eligible` itself keys on. A member NOT in `_REGIONAL_DOMAIN_KEY` is a pure global
    (icon_global / ukmo_global) servable at any lead; this correctly treats the domain-gated
    global `ncep_nbm_conus` (CONUS-only, NOT in REGIONAL_MODELS) as gated, which a
    `REGIONAL_MODELS`-only test would miss. Fail-soft: any error -> all families expected (the
    conservative pre-removal behavior; never silently under-reports completeness).
    """
    try:
        from src.forecast.model_selection import (  # noqa: PLC0415
            _REGIONAL_DOMAIN_KEY,
            regional_eligible,
        )

        def _member_servable(member: str) -> bool:
            if member in _REGIONAL_DOMAIN_KEY:
                return regional_eligible(member, lat=lat, lon=lon, lead_days=int(lead_days))
            return True  # pure global member: servable worldwide at any lead

        expected: set[str] = set()
        for family, members in DECORRELATED_PROVIDER_FAMILIES.items():
            if any(_member_servable(m) for m in members):
                expected.add(family)
        return frozenset(expected)
    except Exception:
        return frozenset(DECORRELATED_PROVIDER_FAMILIES)


def decorrelated_provider_families_of(models: "set[str] | frozenset[str] | tuple[str, ...]") -> frozenset[str]:
    """Return the set of decorrelated provider families REPRESENTED by ``models``.

    A family is present iff ANY of its member models is in ``models``. The ECMWF anchor
    contributes no family (prior), and icon_seamless was removed from the candidate set
    (2026-06-17 — alias-dedup probe), so stray icon_seamless values never inflate the count.
    """
    present: set[str] = set()
    for family, members in DECORRELATED_PROVIDER_FAMILIES.items():
        if any(m in models for m in members):
            present.add(family)
    return frozenset(present)


def _family_set_key(families: "frozenset[str] | set[str]") -> str:
    """Canonical, order-independent string key for a family set (marker uniqueness)."""
    return ",".join(sorted(families))


def _capturable_models_for_scope(
    conn: sqlite3.Connection, *, city: str, target_date: str, metric: str, source_cycle_iso: str
) -> set[str]:
    """Models whose CURRENT value the materializer COULD fuse for this (scope, cycle) RIGHT NOW.

    Delegates ENTIRELY to the single serving authority
    (replacement_current_value_serving.read_current_instrument_values) — the SAME function the
    materializer's q path consumes — so "capturable" and "what the fusion will actually serve"
    can never drift (registry member #10). This includes the generalized previous-runs
    substitution (没有新的就用老的): a provider structurally unpublished on this cycle's
    single_runs leg (JMA at 06Z-cadence cycles) counts as capturable via its previous-runs row.
    Fail-soft: any read error -> empty set (nothing newly capturable).
    """
    from src.data.replacement_current_value_serving import (  # noqa: PLC0415
        read_current_instrument_values,
    )

    try:
        return set(
            read_current_instrument_values(
                conn, city=city, metric=metric, target_date=target_date,
                source_cycle_time_iso=source_cycle_iso,
            ).keys()
        )
    except Exception:
        return set()


def _latest_posterior_served(
    conn: sqlite3.Connection, *, city: str, target_date: str, metric: str
) -> tuple[str | None, frozenset[str]]:
    """Return (source_cycle_time_iso, served_provider_family_set) for the LATEST soft-anchor
    posterior of this scope. The served set is derived from provenance_json.bayes_precision_fusion.used_models
    (the SAME field the fusion records). (None, empty) when there is no posterior or it carries no
    used_models (a single-anchor / pre-fusion row — nothing to upgrade from). Fail-soft: any read
    or JSON error -> (None, empty)."""
    try:
        row = conn.execute(
            """
            SELECT source_cycle_time, provenance_json
            FROM forecast_posteriors
            WHERE source_id = ? AND city = ? AND target_date = ? AND temperature_metric = ?
            ORDER BY computed_at DESC
            LIMIT 1
            """,
            (SOURCE_ID, city, target_date, metric),
        ).fetchone()
    except Exception:
        return None, frozenset()
    if row is None:
        return None, frozenset()
    source_cycle_iso = str(row[0]) if row[0] is not None else None
    try:
        prov = json.loads(row[1]) if row[1] else {}
    except Exception:
        return source_cycle_iso, frozenset()
    used = (prov.get("bayes_precision_fusion", {}) or {}).get("used_models") or []
    if not isinstance(used, (list, tuple)):
        return source_cycle_iso, frozenset()
    return source_cycle_iso, decorrelated_provider_families_of(set(str(m) for m in used))


def _city_latlon(city: str) -> tuple[float, float] | None:
    """Resolve a city's (lat, lon) from the live runtime city map. None when the city is unknown
    or the map cannot be read (caller fails OPEN to all-families-expected). Single source of
    truth for coords — the SAME runtime_cities_by_name the materializer's q path reads, so the
    upgrade trigger and the fusion can never disagree on where a city is."""
    try:
        from src.config import runtime_cities_by_name  # noqa: PLC0415

        city_obj = runtime_cities_by_name().get(city)
        if city_obj is None:
            return None
        return float(getattr(city_obj, "lat")), float(getattr(city_obj, "lon"))
    except Exception:
        return None


def _scope_lead_days(city: str, target_date: str, cycle_iso: str) -> int:
    """City-LOCAL lead (days) from the posterior's cycle to the target date — the lead at which
    the fusion serves this scope. Used to evaluate the per-city expected set at the REAL lead
    (the nests are lead-capped: gfs_hrrr=2, ncep_nbm=3, gem_hrdps=2), so a far-lead CONUS/NA scope
    does NOT over-expect NCEP/CMC. Fail-soft to lead 0 (the MOST-expecting / loudest direction:
    over-expect -> PARTIAL/upgrade, never a silent false-COMPLETE)."""
    try:
        from datetime import date as _date, datetime as _dt  # noqa: PLC0415
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        from src.config import runtime_cities_by_name  # noqa: PLC0415

        cycle_dt = _dt.fromisoformat(str(cycle_iso).replace("Z", "+00:00"))
        city_obj = runtime_cities_by_name().get(city)
        tz = getattr(city_obj, "timezone", None) if city_obj is not None else None
        cycle_local = cycle_dt.astimezone(ZoneInfo(tz)).date() if tz else cycle_dt.date()
        return max(0, (_date.fromisoformat(str(target_date)) - cycle_local).days)
    except Exception:
        return 0


def scope_capture_offers_larger_provider_set(
    conn: sqlite3.Connection, *, city: str, target_date: str, metric: str
) -> dict[str, object]:
    """THE single comparison: does this scope's latest posterior need re-materialization because a
    STRICTLY LARGER decorrelated-provider FAMILY set is now capturable at the SAME cycle?

    Returns a dict:
      {is_upgrade, source_cycle_time, served_families, capturable_families, new_families}.
    is_upgrade is True iff the posterior has a fusion served set AND the capturable family set is a
    STRICT SUPERSET of it (at least one new provider family the posterior did not use). Equal sets
    (already maximal for what is published) or a posterior without fusion -> is_upgrade False. This
    is the ONLY place the instrument-set completeness dimension is evaluated. Fail-soft throughout.
    """
    source_cycle_iso, served = _latest_posterior_served(
        conn, city=city, target_date=target_date, metric=metric
    )
    if source_cycle_iso is None:
        return {
            "is_upgrade": False,
            "source_cycle_time": None,
            "served_families": [],
            "capturable_families": [],
            "new_families": [],
        }
    capturable_models = _capturable_models_for_scope(
        conn, city=city, target_date=target_date, metric=metric, source_cycle_iso=source_cycle_iso
    )
    capturable = decorrelated_provider_families_of(capturable_models)
    # DOMAIN-AWARE gate (2026-06-17 coarse-global removal): a family that is STRUCTURALLY ABSENT
    # for this city (NCEP/CMC outside their nest domains, now that the global fallbacks are gone)
    # must NEVER trigger an upgrade re-enqueue — there is no provider that can ever land, so the
    # chase would loop forever. Intersect capturable with the per-city expected set so only a
    # family that is BOTH capturable AND expected-here can count as a growth target. Fail-open
    # (expected = all families) when coords are missing, which preserves the exact pre-removal
    # comparison (capturable already excludes structurally-absent families via missing rows).
    _latlon = _city_latlon(city)
    _lead = _scope_lead_days(city, target_date, source_cycle_iso)
    expected = (
        expected_provider_families_for_city(_latlon[0], _latlon[1], _lead)
        if _latlon is not None
        else frozenset(DECORRELATED_PROVIDER_FAMILIES)
    )
    capturable_expected = capturable & expected
    new_families = capturable_expected - served
    # STRICT superset: the capturable-and-expected set must add a family the served set lacks. A
    # served set with no fusion (empty) is NOT upgraded here — there is no smaller-set posterior
    # to grow (the single-anchor fallback is a separate concern handled by the missing-capture gate).
    is_upgrade = bool(served) and bool(new_families) and served.issubset(capturable_expected)
    return {
        "is_upgrade": is_upgrade,
        "source_cycle_time": source_cycle_iso,
        "served_families": sorted(served),
        "capturable_families": sorted(capturable_expected),
        "new_families": sorted(new_families),
    }


def _already_enqueued(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    source_cycle_iso: str,
    capturable_family_key: str,
) -> bool:
    """True iff a re-materialization was already enqueued for this exact (scope, cycle,
    capturable-family-superset). The marker is the idempotency bound: at most ONE enqueue per
    (cycle, capturable-family-set) transition. Fail-open toward NOT-enqueued only on read error
    (the UNIQUE index still prevents a duplicate physical row)."""
    try:
        row = conn.execute(
            """
            SELECT 1 FROM fusion_upgrade_enqueues
            WHERE city = ? AND target_date = ? AND metric = ?
              AND source_cycle_time = ? AND capturable_family_set = ?
            LIMIT 1
            """,
            (city, target_date, metric, source_cycle_iso, capturable_family_key),
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
    source_cycle_iso: str,
    served_family_key: str,
    capturable_family_key: str,
    seed_file: str,
) -> bool:
    """Write the idempotency marker. Returns True iff this call inserted the row (False = a
    concurrent/prior enqueue already recorded it, via the UNIQUE index INSERT OR IGNORE)."""
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO fusion_upgrade_enqueues
            (enqueued_at, city, target_date, metric, source_cycle_time,
             served_family_set, capturable_family_set, seed_file)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(tz=UTC).isoformat(),
            city,
            target_date,
            metric,
            source_cycle_iso,
            served_family_key,
            capturable_family_key,
            seed_file,
        ),
    )
    return conn.total_changes > before


def enqueue_fusion_upgrade_reseeds(
    *,
    forecast_db: Path | str,
    seed_dir: Path | str,
    raw_manifest_dir: Path | str,
    computed_at: datetime | None = None,
    limit: int = 50,
) -> dict[str, object]:
    """For every current target whose latest posterior was fused from a STRICTLY SMALLER
    decorrelated-provider family set than is now capturable at its cycle, enqueue exactly one
    re-materialization seed (reusing the existing seed builder + seed_dir the materialize cycle
    drains). Idempotent per (scope, cycle, capturable-family-superset) via fusion_upgrade_enqueues.

    Belongs in the EXISTING data-ingest availability-poll lane (no new daemon). Fail-soft: any
    per-scope error is logged and skipped; the function never raises into the poll. Returns a
    compact report.
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
        "status": "FUSION_UPGRADE_TRIGGER",
        "scopes_checked": 0,
        "upgrades_detected": 0,
        "seeds_enqueued": 0,
        "already_enqueued": 0,
        "manifest_missing": 0,
        "enqueued": [],
    }
    if not forecast_db.exists():
        report["status"] = "FUSION_UPGRADE_FORECAST_DB_MISSING"
        return report

    # The current targets (same authority the seed discovery uses). require_raw_artifacts=False:
    # the per-scope manifest is checked below, mirroring seed discovery.
    plan = build_replacement_forecast_current_target_plan(
        forecast_db,
        min_target_date=now.date().isoformat(),
        require_raw_artifacts=False,
        now_utc=now,
    )
    if plan.status == "BLOCKED":
        report["status"] = "FUSION_UPGRADE_PLAN_BLOCKED"
        report["reason_codes"] = list(plan.reason_codes)
        return report

    manifests = _load_manifests(raw_dir, computed_at=now)

    conn = _connect(forecast_db, write_class="live")
    conn.row_factory = sqlite3.Row
    try:
        ensure_replacement_forecast_live_schema(conn)
        enqueued = 0
        # NEAREST-TARGET-FIRST (mirrors the seed-budget K-decision, registry member #6): the
        # plan's native order is target_date DESC, which would spend the per-tick enqueue budget
        # on far-date shadow scopes while the tradeable day0/day1 money scopes starve.
        for row in sorted(
            plan.rows,
            key=lambda r: (str(r.target_date), str(r.city), str(r.temperature_metric)),
        ):
            if enqueued >= max(1, int(limit)):
                break
            city = str(row.city)
            target_date = str(row.target_date)
            metric = str(row.temperature_metric)
            # DAY0 GUARD (live-run finding 2026-06-11): a started local day's scope needs the
            # observed-extreme path, not a plain re-materialization — the seed discovery's
            # can_seed excludes these and the upgrade re-seed must too (same plan flag, same
            # reason). Without it the first live enqueue burned 18 budget slots on day0 scopes.
            if bool(getattr(row, "day0_observed_extreme_required", False)):
                report["day0_skipped"] = int(report.get("day0_skipped", 0)) + 1  # type: ignore[arg-type]
                continue
            report["scopes_checked"] = int(report["scopes_checked"]) + 1
            try:
                verdict = scope_capture_offers_larger_provider_set(
                    conn, city=city, target_date=target_date, metric=metric
                )
            except Exception as exc:  # noqa: BLE001 — per-scope fail-soft
                _LOG.debug("fusion-upgrade comparison failed for %s/%s/%s: %s", city, target_date, metric, exc)
                continue
            if not verdict["is_upgrade"]:
                continue
            report["upgrades_detected"] = int(report["upgrades_detected"]) + 1
            source_cycle_iso = str(verdict["source_cycle_time"])
            # CYCLE-AGE GUARD (live-run finding 2026-06-11): the materializer refuses a request
            # whose cycle exceeds the staleness bound (cycle_age_exceeds_bound -> CYCLE_TOO_OLD),
            # so enqueueing an upgrade for a posterior stuck on an over-age cycle only spawns a
            # guaranteed-failure subprocess. The SAME policy function decides here (single
            # authority: replacement_forecast_cycle_policy) — such a scope heals on the next
            # fresh-cycle materialization instead.
            try:
                from src.data.replacement_forecast_cycle_policy import (  # noqa: PLC0415
                    cycle_age_exceeds_bound,
                )

                _cycle_dt = datetime.fromisoformat(source_cycle_iso.replace("Z", "+00:00"))
                if cycle_age_exceeds_bound(now, _cycle_dt):
                    report["cycle_too_old_skipped"] = int(report.get("cycle_too_old_skipped", 0)) + 1  # type: ignore[arg-type]
                    continue
            except Exception:  # noqa: BLE001 — unparseable cycle: let the materializer decide
                pass
            capturable_key = _family_set_key(set(verdict["capturable_families"]))  # type: ignore[arg-type]
            served_key = _family_set_key(set(verdict["served_families"]))  # type: ignore[arg-type]
            if _already_enqueued(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                source_cycle_iso=source_cycle_iso,
                capturable_family_key=capturable_key,
            ):
                report["already_enqueued"] = int(report["already_enqueued"]) + 1
                continue
            # Build the seed from the SAME manifests/coverage/bins the seed discovery uses, then
            # write it into seed_dir so the existing materialize cycle drains it. A re-seed at the
            # same cycle re-materializes the scope; the materializer re-reads the (now larger)
            # persisted capture and produces a served=larger posterior.
            try:
                seed_file = _build_and_write_upgrade_seed(
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
                _LOG.debug("fusion-upgrade seed build failed for %s/%s/%s: %s", city, target_date, metric, exc)
                continue
            if seed_file is None:
                report["manifest_missing"] = int(report["manifest_missing"]) + 1
                continue
            inserted = _record_enqueue(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                source_cycle_iso=source_cycle_iso,
                served_family_key=served_key,
                capturable_family_key=capturable_key,
                seed_file=str(seed_file),
            )
            conn.commit()
            if inserted:
                enqueued += 1
                report["seeds_enqueued"] = int(report["seeds_enqueued"]) + 1
                report["enqueued"].append(  # type: ignore[union-attr]
                    {
                        "city": city,
                        "target_date": target_date,
                        "metric": metric,
                        "source_cycle_time": source_cycle_iso,
                        "served_families": verdict["served_families"],
                        "capturable_families": verdict["capturable_families"],
                        "new_families": verdict["new_families"],
                        "seed_file": str(seed_file),
                    }
                )
            else:
                report["already_enqueued"] = int(report["already_enqueued"]) + 1
    finally:
        conn.close()
    return report


def _build_and_write_upgrade_seed(
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
    absent (the scope's raw inputs are not on disk — recorded as manifest_missing, retried next
    tick once they land). Kept separate so the enqueue loop stays readable."""
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
    )
    if not seed_result.ok or seed_result.seed is None:
        return None
    # Thread the honest upgrade-trigger provenance note into the seed so the re-materialized
    # posterior records WHY it was produced (instrument-set expansion, not a fresh cycle).
    seed_payload: dict[str, object] = dict(seed_result.seed)
    seed_payload["upgrade_trigger"] = "instrument_set_expansion"
    seed_file = seed_path / seed_name(
        {"city": city, "target_date": target_date, "temperature_metric": metric},
        computed_at=computed_at,
    )
    write_seed(seed_file, seed_payload)
    return seed_file
