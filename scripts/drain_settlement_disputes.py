# Created: 2026-07-04
# Last reused or audited: 2026-07-04
# Lifecycle: created=2026-07-04; last_reviewed=2026-07-04; last_reused=never
#
# Purpose: Drain the settlement_outcomes DISPUTED backlog in zeus-forecasts.db so the
#   chain-mirror reconciler (src/state/chain_mirror_reconciler.py::load_settlement_lookup,
#   10-min lane) — which grades legacy positions ONLY against authority='VERIFIED' — can
#   close every gradable legacy position. As of 2026-07-04 (audit finding): 383 of 8,527
#   settlement_outcomes rows are stuck DISPUTED; two markets (Hong Kong 2026-06-25 low,
#   Helsinki 2026-07-02 high) have NO row at all.
#
# AUTHORITY MODEL (operator correction 2026-07-04 — v1 of this script graded from persisted
# local observations only and recovered 0 of 383 rows; that constraint was wrong):
#
#   The venue's RESOLVED OUTCOME is the PAYMENT FACT. A market that has resolved on Polymarket
#   has already paid out real money on a specific winning bin; that is not awaiting any
#   operator preference or local-observation agreement. This script's grading authority is
#   therefore the venue resolution (Gamma API, read-only), fetched per market — NOT a local
#   recomputation from persisted `observations` rows.
#
#   Two DISTINCT facts are tracked and never conflated:
#     1. PAYMENT FACT = venue resolution (source='venue_resolution' in provenance). A
#        venue-resolved market -> authority='VERIFIED', winning_bin = the venue's resolved
#        bin. Position accounting / realized-P&L grading (what the chain-mirror reconciler
#        consumes) follows this fact alone.
#     2. DECLARED-SOURCE TRUTH = the observation from the city's declared settlement source
#        (config/cities.json `settlement_source_type`), re-derived from already-persisted
#        `observations` rows via the same lookup the live harvester write path uses. This is
#        recorded in provenance as `declared_source_type` / `declared_source_observed_value`
#        for the FORECAST-SKILL calibration lane (grades model accuracy vs the physically
#        reported temperature, not vs who got paid). It NEVER blocks a VERIFIED write.
#
#   When the two facts disagree (the declared-source value is not contained in the venue's
#   resolved bin — e.g. Hong Kong 2026-06-26 low: HKO's own hourly-accumulated reading gives
#   27°C but Polymarket resolved the 26°C bin), provenance stamps
#   `resolution_conflict='venue_vs_declared_source'` with both values recorded side by side.
#   Money-calibration (win-rate vs price) reads `winning_bin`/`settlement_value` (payment);
#   forecast-skill calibration reads `declared_source_observed_value` — two explicit fields,
#   neither lane re-derives the other's fact.
#
# Method:
#   1. Classify every DISPUTED row by provenance_json.dispute_reason.
#   2. `pc_audit_*`-prefixed reasons carry PRIOR HUMAN AUDIT judgment (a named operator
#      investigation already concluded non-reproducible / needs-collector / station-drift for
#      these specific rows — see docs/evidence/timing_audit/fallback_outcome_quality_2026-06-16.md
#      §4). This script NEVER auto-reactivates them even when the venue has since resolved —
#      that would silently overrule a documented human judgment call without the same human
#      reviewing the new evidence. If venue resolution now covers one of these rows it is
#      reported separately as `operator_review_venue_resolved` (report-only; never written).
#   3. All other (mechanically-disputed) rows, plus the two known MISSING markets, are
#      re-resolved from venue evidence:
#        a. Resolve the market's Gamma slug: use the row's own persisted `market_slug` when
#           present; otherwise derive candidate slugs from `city.slug_names` + the standard
#           "{highest|lowest}-temperature-in-{slug}-on-{month}-{day}-{year}" pattern (same
#           convention `src/data/market_scanner.py` slug-pattern discovery uses).
#        b. GET /events?slug=<slug> from the Gamma API (src.data.market_scanner._gamma_get —
#           the same retrying HTTP helper the live scanner/harvester use). Read-only; this
#           script never places, cancels, or redeems anything on any venue.
#        c. classify via src.execution.harvester._find_winning_market_outcome (the same
#           umaResolutionStatus + binary-outcomePrices typed gate the live harvester write
#           path uses — fail-closed on ambiguous data, never assumes a winner).
#        d. No event found / not yet resolved / ambiguous winner -> `venue_unresolved` — stays
#           DISPUTED (or absent for a missing market); nothing invented.
#        e. A network fault (Gamma unreachable after retries, non-JSON response) ->
#           `venue_fetch_error` — FAILS CLOSED: the row is left exactly as it was, no partial
#           write, the error is reported. A transient outage must never be recorded as
#           "unresolved".
#        f. Exactly one resolved winner -> VERIFY: winning_bin from the venue's resolved bin
#           (F-on-C-city label conversion applied, same as harvester_truth_writer.py fix
#           #262/#264). settlement_value ALWAYS reflects the PAYMENT FACT — the declared-source
#           value is used only when it is CONTAINED in the venue's winning bin (source-correct
#           rounding via SettlementSemantics.for_city() may itself dissolve an apparent
#           disagreement, e.g. an HKO oracle_truncate value that a naive wmo_half_up rounding
#           would have placed outside the bin); when it disagrees, settlement_value falls back
#           to the bin's own point value (point bins only) or stays NULL (range/shoulder bins
#           have no other way to know the exact temperature) — NEVER a value contradicting its
#           own winning bin, even though the disagreeing declared value is preserved verbatim
#           in provenance for the forecast-skill lane.
#   4. A VERIFY write is a narrow UPDATE/INSERT (authority, settlement_value, winning_bin,
#      settlement_unit, provenance_json only — settled_at/market_slug/settlement_source are
#      NOT touched on existing rows). provenance_json is never overwritten wholesale: the
#      prior authority + dispute_reason + full prior provenance are preserved under
#      `prior_provenance`, and `reactivated_by`/`reactivated_at`/`drain_script` are stamped
#      (mirrors the `settlements.authority` monotonic-trigger discipline: DISPUTED->VERIFIED
#      requires a non-empty text `reactivated_by`, even though settlement_outcomes has no such
#      trigger).
#
# Scope: zeus-forecasts.db writes ONLY (settlement_outcomes). Never touches zeus_trades.db or
#   zeus-world.db. Network reads are READ-ONLY Gamma market facts (no venue mutation of any
#   kind — no orders, no cancels, no redeems). Dry-run by DEFAULT (SAVEPOINT + rollback);
#   --apply commits.
#
# Run:
#   dry-run (default):  python scripts/drain_settlement_disputes.py [--json]
#   apply:               python scripts/drain_settlement_disputes.py --apply
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DB_DEFAULT = _REPO_ROOT / "state" / "zeus-forecasts.db"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import httpx  # noqa: E402

from src.config import City, load_cities  # noqa: E402
from src.contracts.exceptions import SettlementPrecisionError  # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402
from src.data import market_scanner as market_scanner_mod  # noqa: E402
from src.execution import harvester as harvester_mod  # noqa: E402

DRAIN_SCRIPT_ID = "scripts.drain_settlement_disputes"

_DECLARED_SOURCE_LABEL = {"wu_icao": "WU", "hko": "HKO", "noaa": "NOAA", "cwa_station": "CWA"}

# Markets known (2026-07-04 audit) to be entirely absent from settlement_outcomes.
# This is an explicit, named list rather than a generic market_events-vs-settlement_outcomes
# scan: 557 (city, target_date, metric) combinations exist in market_events with no
# settlement_outcomes row, but almost all of those are markets that have simply not settled
# yet (future/open target_date) — a generic scan would be overwhelmingly false positives.
# Add further known-missing markets here as they are discovered by audit, not by inventing
# a resolved-vs-open classifier this task does not need.
DEFAULT_MISSING_MARKETS: tuple[tuple[str, str, str], ...] = (
    ("Hong Kong", "2026-06-25", "low"),
    ("Helsinki", "2026-07-02", "high"),
)


class VenueFetchError(RuntimeError):
    """Gamma API unreachable/unparseable after retries. Callers MUST fail closed on this —
    never treat a network fault as 'unresolved'."""


def _f_to_c(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def _effective_bin(
    lo: Optional[float], hi: Optional[float], label_unit: Optional[str], city: City
) -> tuple[Optional[float], Optional[float]]:
    """Convert + WMO-snap a Gamma-label-derived bin into the city's settlement unit.

    Mirrors src/ingest/harvester_truth_writer.py:490-527 (fix #262/#264): pre-2026 London
    markets were posed in F while London settles in C; the only live case is F-bin-on-C-city.
    """
    eff_lo, eff_hi = lo, hi
    if label_unit == "F" and city.settlement_unit == "C":
        if eff_lo is not None:
            eff_lo = math.floor(_f_to_c(eff_lo) + 0.5)
        if eff_hi is not None:
            eff_hi = math.floor(_f_to_c(eff_hi) + 0.5)
    return eff_lo, eff_hi


def _is_contained(rounded: float, eff_lo: Optional[float], eff_hi: Optional[float]) -> bool:
    if eff_lo is not None and eff_hi is not None:
        return eff_lo <= rounded <= eff_hi
    if eff_lo is None and eff_hi is not None:
        return rounded <= eff_hi
    if eff_hi is None and eff_lo is not None:
        return rounded >= eff_lo
    return False


def _is_manual_audit_reason(reason: str) -> bool:
    return reason.startswith("pc_audit_")


# ---------------------------------------------------------------------------
# Declared-source truth (secondary, calibration-only fact; never blocks VERIFY)
# ---------------------------------------------------------------------------

def declared_source_fact(
    conn: sqlite3.Connection, city: City, target_date: str, metric: str
) -> dict:
    """Re-derive the city's DECLARED settlement source's observed value from already-persisted
    `observations` rows (src.execution.harvester._lookup_settlement_obs — the same
    source-family-routed lookup the live harvester write path uses).

    Always returns a dict; never raises. This is provenance-only — it does not gate VERIFY.
    """
    declared_source_type = _DECLARED_SOURCE_LABEL.get(
        city.settlement_source_type, city.settlement_source_type.upper()
    )
    obs = harvester_mod._lookup_settlement_obs(conn, city, target_date, temperature_metric=metric)
    if obs is None:
        return {
            "declared_source_type": declared_source_type,
            "declared_source_observed_value": None,
            "declared_source_detail": "no persisted observation from the declared source family",
        }
    sem = SettlementSemantics.for_city(city)
    try:
        rounded = sem.assert_settlement_value(
            float(obs["observed_temp"]), context=f"{DRAIN_SCRIPT_ID}/{city.name}/{target_date}"
        )
    except SettlementPrecisionError as exc:
        return {
            "declared_source_type": declared_source_type,
            "declared_source_observed_value": None,
            "declared_source_detail": f"settlement_precision_error: {exc}",
        }
    return {
        "declared_source_type": declared_source_type,
        "declared_source_observed_value": rounded,
        "declared_source_detail": f"obs_id={obs.get('id')}",
    }


# ---------------------------------------------------------------------------
# Venue resolution (PAYMENT FACT) — read-only Gamma market lookup
# ---------------------------------------------------------------------------

def _looks_like_real_gamma_slug(market_slug: str) -> bool:
    """Real Gamma slugs are hyphen-only kebab-case (e.g.
    "lowest-temperature-in-hong-kong-on-june-25-2026"). `uma_backfill_*` is a SYNTHETIC
    placeholder some reconstruction passes stamped into market_slug when the real Gamma slug
    was never recorded (2,077 rows repo-wide as of 2026-07-04, all underscore-delimited) — it
    is not a queryable Gamma slug and must not be trusted as one."""
    return "_" not in market_slug


def _slug_candidates(city: City, target_date: str, metric: str, market_slug: Optional[str]) -> list[str]:
    """Candidate Gamma event slugs for a (city, target_date, metric) market.

    The row's own persisted market_slug is authoritative and returned alone ONLY when it looks
    like a real Gamma slug (see _looks_like_real_gamma_slug) — a synthetic placeholder is
    ignored in favor of derivation. Derive from city.slug_names using the same
    "{highest|lowest}-temperature-in-{slug}-on-{month}-{day}-{year}" convention
    src/data/market_scanner.py SLUG_DISCOVERY_PREFIXES uses for slug-pattern discovery.
    """
    if market_slug and _looks_like_real_gamma_slug(market_slug):
        return [market_slug]
    prefix = (
        "highest-temperature-in-{city}-on-{date}"
        if metric == "high"
        else "lowest-temperature-in-{city}-on-{date}"
    )
    try:
        date_fragment = date.fromisoformat(target_date).strftime("%B-%-d-%Y").lower()
    except ValueError:
        return []
    slug_names = city.slug_names or (city.name.lower().replace(" ", "-"),)
    return [prefix.format(city=slug_name, date=date_fragment) for slug_name in slug_names]


def _fetch_venue_event_by_slug(slug: str) -> Optional[dict]:
    """GET the Gamma event for one slug. Returns None when no such event exists.
    Raises VenueFetchError on network/parse failure — callers MUST fail closed on this.
    """
    try:
        resp = market_scanner_mod._gamma_get("/events", params={"slug": slug}, timeout=15.0, retries=3)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise VenueFetchError(f"Gamma API fetch failed for slug={slug!r}: {exc}") from exc
    try:
        payload = resp.json()
    except ValueError as exc:
        raise VenueFetchError(f"Gamma API returned non-JSON for slug={slug!r}: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        return None
    for event in payload:
        if str(event.get("slug") or "") == slug:
            return event
    return payload[0]


def fetch_venue_resolution(
    city: City, target_date: str, metric: str, market_slug: Optional[str]
) -> dict:
    """Resolve the PAYMENT FACT for one market from the venue's own resolution.

    Returns {"status": ..., "outcome": ResolvedMarketOutcome|None, "event": dict|None,
    "event_slug": str|None, "error": str|None}. status is one of:
      no_candidate_slug | fetch_error | unresolved | resolved
    """
    candidates = _slug_candidates(city, target_date, metric, market_slug)
    if not candidates:
        return {"status": "no_candidate_slug", "outcome": None, "event": None, "event_slug": None, "error": None}

    last_event: Optional[dict] = None
    last_slug: Optional[str] = None
    for slug in candidates:
        try:
            event = _fetch_venue_event_by_slug(slug)
        except VenueFetchError as exc:
            return {"status": "fetch_error", "outcome": None, "event": None, "event_slug": slug, "error": str(exc)}
        if event is not None:
            last_event, last_slug = event, slug
            outcome = harvester_mod._find_winning_market_outcome(event)
            if outcome is not None:
                return {
                    "status": "resolved", "outcome": outcome, "event": event,
                    "event_slug": str(event.get("slug") or slug), "error": None,
                }
    return {
        "status": "unresolved", "outcome": None, "event": last_event,
        "event_slug": (str(last_event.get("slug") or last_slug) if last_event else last_slug),
        "error": None,
    }


def _raw_resolution_evidence(venue: dict) -> dict:
    outcome = venue["outcome"]
    event = venue.get("event") or {}
    # umaResolutionStatus lives on the individual MARKET (Gamma nests markets under the
    # event), not the event object itself — find the winning market by condition_id so the
    # stamped evidence reflects the market that actually resolved, not a top-level miss.
    uma_status = None
    for market in event.get("markets", []) or []:
        market_condition_id = str(
            market.get("conditionId") or market.get("condition_id") or market.get("id") or ""
        ).strip()
        if market_condition_id == outcome.condition_id:
            uma_status = market.get("umaResolutionStatus")
            break
    return {
        "condition_id": outcome.condition_id,
        "yes_token_id": outcome.yes_token_id,
        "range_label": outcome.range_label,
        "range_low": outcome.range_low,
        "range_high": outcome.range_high,
        "event_slug": venue.get("event_slug"),
        "umaResolutionStatus": uma_status,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _venue_winning_bin(city: City, venue: dict) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Effective (city-unit, WMO-snapped) bin bounds + canonical label for a resolved venue
    outcome. Returns (eff_lo, eff_hi, winning_bin_label); winning_bin_label is None when the
    resolved market's own label could not be parsed into bounds (fail closed, never invented)."""
    outcome = venue["outcome"]
    label_unit = harvester_mod._label_temperature_unit(outcome.range_label) or city.settlement_unit
    eff_lo, eff_hi = _effective_bin(outcome.range_low, outcome.range_high, label_unit, city)
    if eff_lo is None and eff_hi is None:
        return None, None, None
    winning_bin = harvester_mod._canonical_bin_label(eff_lo, eff_hi, city.settlement_unit)
    return eff_lo, eff_hi, winning_bin


def _settlement_value_and_conflict(
    declared_value: Optional[float], eff_lo: Optional[float], eff_hi: Optional[float]
) -> tuple[Optional[float], Optional[str]]:
    """settlement_value ALWAYS reflects the PAYMENT FACT (the venue-resolved bin) — never a
    declared-source value that contradicts its own winning bin, even though that declared
    value is preserved verbatim in provenance for the forecast-skill lane. Returns
    (settlement_value, resolution_conflict)."""
    point_value = eff_lo if (eff_lo is not None and eff_lo == eff_hi) else None
    if declared_value is not None and _is_contained(declared_value, eff_lo, eff_hi):
        return declared_value, None
    if declared_value is not None:
        # Disagreement: the declared-source value is preserved in provenance
        # (declared_source_observed_value) but never written into settlement_value —
        # a range/shoulder bin has no other way to know the exact temperature, and a point
        # bin already knows its own value regardless of what the declared source claims.
        return point_value, "venue_vs_declared_source"
    return point_value, None


def resolve_disputed_row(
    conn: sqlite3.Connection, city_map: dict[str, City], row: sqlite3.Row
) -> dict:
    """Decide the fate of one DISPUTED settlement_outcomes row.

    Grading authority is the venue's resolved outcome (PAYMENT FACT), fetched read-only from
    the Gamma API. The declared-source observation (DECLARED-SOURCE TRUTH) is recorded
    alongside for the forecast-skill calibration lane but never blocks VERIFY.

    disposition is one of: unfillable_no_city | manual_audit_reserved |
    operator_review_venue_resolved | venue_fetch_error | venue_unresolved | verify
    """
    settlement_id = row["settlement_id"]
    city_name = row["city"]
    target_date = row["target_date"]
    metric = row["temperature_metric"]
    market_slug = row["market_slug"] if "market_slug" in row.keys() else None
    try:
        provenance = json.loads(row["provenance_json"] or "{}")
        if not isinstance(provenance, dict):
            provenance = {}
    except (json.JSONDecodeError, TypeError):
        provenance = {}
    reason = provenance.get("dispute_reason", "")

    base = {
        "settlement_id": settlement_id,
        "city": city_name,
        "target_date": target_date,
        "temperature_metric": metric,
        "dispute_reason": reason,
    }

    city = city_map.get(city_name)
    if city is None:
        return {**base, "disposition": "unfillable_no_city", "detail": f"city {city_name!r} not in current cities.json"}

    manual_audit = _is_manual_audit_reason(reason)

    venue = fetch_venue_resolution(city, target_date, metric, market_slug)
    if venue["status"] == "fetch_error":
        return {**base, "disposition": "venue_fetch_error", "detail": venue["error"]}

    if manual_audit:
        if venue["status"] == "resolved":
            return {
                **base, "disposition": "operator_review_venue_resolved",
                "detail": "prior human audit judgment on record, but venue resolution now exists — "
                          "reported for operator review, not auto-reactivated",
                "venue_resolution": _raw_resolution_evidence(venue),
            }
        return {**base, "disposition": "manual_audit_reserved",
                "detail": "prior human audit judgment on record; not auto-reactivated"}

    declared = declared_source_fact(conn, city, target_date, metric)

    if venue["status"] != "resolved":
        return {**base, "disposition": "venue_unresolved",
                "detail": f"venue status={venue['status']}", **declared}

    eff_lo, eff_hi, winning_bin = _venue_winning_bin(city, venue)
    if winning_bin is None:
        return {**base, "disposition": "venue_unresolved",
                "detail": "venue resolved but the winning market's own label could not be parsed into bounds",
                **declared}

    declared_value = declared.get("declared_source_observed_value")
    settlement_value, resolution_conflict = _settlement_value_and_conflict(declared_value, eff_lo, eff_hi)

    return {
        **base,
        "disposition": "verify",
        "detail": f"venue resolved winning_bin={winning_bin}",
        "rounded_value": settlement_value,
        "winning_bin": winning_bin,
        "venue_resolution": _raw_resolution_evidence(venue),
        "resolution_conflict": resolution_conflict,
        **declared,
    }


def resolve_missing_market(
    conn: sqlite3.Connection, city_map: dict[str, City], city_name: str, target_date: str, metric: str
) -> dict:
    """Same venue-resolution-authoritative re-resolution as resolve_disputed_row, for a
    market with NO settlement_outcomes row at all — the market knows its own outcome even
    though nothing was ever persisted locally for it."""
    base = {"city": city_name, "target_date": target_date, "temperature_metric": metric}
    city = city_map.get(city_name)
    if city is None:
        return {**base, "disposition": "unfillable_no_city", "detail": f"city {city_name!r} not in current cities.json"}

    venue = fetch_venue_resolution(city, target_date, metric, None)
    if venue["status"] == "fetch_error":
        return {**base, "disposition": "venue_fetch_error", "detail": venue["error"]}

    declared = declared_source_fact(conn, city, target_date, metric)

    if venue["status"] != "resolved":
        return {**base, "disposition": "venue_unresolved", "detail": f"venue status={venue['status']}", **declared}

    eff_lo, eff_hi, winning_bin = _venue_winning_bin(city, venue)
    if winning_bin is None:
        return {**base, "disposition": "venue_unresolved",
                "detail": "venue resolved but the winning market's own label could not be parsed into bounds",
                **declared}

    declared_value = declared.get("declared_source_observed_value")
    settlement_value, resolution_conflict = _settlement_value_and_conflict(declared_value, eff_lo, eff_hi)

    return {
        **base,
        "disposition": "verify",
        "detail": f"venue resolved winning_bin={winning_bin}",
        "rounded_value": settlement_value,
        "winning_bin": winning_bin,
        "venue_resolution": _raw_resolution_evidence(venue),
        "resolution_conflict": resolution_conflict,
        **declared,
    }


def _verify_provenance(prior_provenance: dict, decision: dict, *, now: str) -> dict:
    new_provenance = dict(prior_provenance)
    new_provenance["prior_provenance"] = prior_provenance
    new_provenance["prior_authority"] = "DISPUTED"
    new_provenance["prior_dispute_reason"] = prior_provenance.get("dispute_reason")
    new_provenance["source"] = "venue_resolution"
    new_provenance["venue_resolution"] = decision["venue_resolution"]
    new_provenance["declared_source_type"] = decision.get("declared_source_type")
    new_provenance["declared_source_observed_value"] = decision.get("declared_source_observed_value")
    new_provenance["declared_source_detail"] = decision.get("declared_source_detail")
    if decision.get("resolution_conflict"):
        new_provenance["resolution_conflict"] = decision["resolution_conflict"]
    new_provenance["reactivated_by"] = DRAIN_SCRIPT_ID
    new_provenance["reactivated_at"] = now
    new_provenance["drain_script"] = "scripts/drain_settlement_disputes.py"
    return new_provenance


def _apply_verify(conn: sqlite3.Connection, row: sqlite3.Row, decision: dict, city: City, *, now: str) -> None:
    prior_provenance = json.loads(row["provenance_json"] or "{}")
    if not isinstance(prior_provenance, dict):
        prior_provenance = {}
    new_provenance = _verify_provenance(prior_provenance, decision, now=now)

    # settlement_unit is authoritative city config, never the raw observation's reported
    # unit — the two coincide today but city config is the single source of truth
    # (_settlement_outcomes_verified_unit_check* triggers require it non-null on VERIFIED).
    conn.execute(
        """
        UPDATE settlement_outcomes
           SET authority='VERIFIED',
               settlement_value=?,
               winning_bin=?,
               settlement_unit=?,
               provenance_json=?
         WHERE settlement_id=? AND authority='DISPUTED'
        """,
        (
            decision["rounded_value"],
            decision["winning_bin"],
            city.settlement_unit,
            json.dumps(new_provenance, sort_keys=True, default=str),
            row["settlement_id"],
        ),
    )


def _insert_missing_verified(
    conn: sqlite3.Connection, city: City, target_date: str, metric: str, decision: dict, *, now: str
) -> None:
    provenance = _verify_provenance({}, decision, now=now)
    provenance["writer"] = DRAIN_SCRIPT_ID
    provenance["writer_script"] = "scripts/drain_settlement_disputes.py"
    provenance["reconstruction_method"] = "drain_missing_market_backfill"
    conn.execute(
        """
        INSERT INTO settlement_outcomes
            (city, target_date, temperature_metric, authority, settlement_value,
             winning_bin, settlement_unit, provenance_json, recorded_at)
        VALUES (?, ?, ?, 'VERIFIED', ?, ?, ?, ?, ?)
        """,
        (
            city.name, target_date, metric, decision["rounded_value"],
            decision.get("winning_bin"), city.settlement_unit,
            json.dumps(provenance, sort_keys=True, default=str), now,
        ),
    )


def drain(
    conn: sqlite3.Connection,
    *,
    apply: bool,
    missing_markets: tuple[tuple[str, str, str], ...] = DEFAULT_MISSING_MARKETS,
    city_map: dict[str, City] | None = None,
    max_rows: int | None = None,
    skip_missing_markets: bool = False,
) -> dict:
    """Diagnose + (dry-run or apply) drain the DISPUTED backlog. Never touches VERIFIED rows.

    Always runs inside a SAVEPOINT; rolls back unless apply=True. Idempotent: a second run
    over an already-drained DB reclassifies verified rows as no longer DISPUTED (they are
    simply absent from the WHERE authority='DISPUTED' scan) and touches nothing. Makes
    read-only Gamma network calls (per-row venue resolution lookup); never mutates any venue.

    city_map defaults to the real `src.config.load_cities()` universe; tests inject a
    synthetic map instead of touching config/cities.json.

    max_rows (T2b consult condition (a), 2026-07-11): when set, bounds the pass to the
    OLDEST `max_rows` DISPUTED rows by `recorded_at ASC` — the natural last-attempt cadence
    (no new schema column needed). Used by src.execution.harvester's cycle-integrated
    rediscovery pass so drain is no longer a manual-script-only mechanism; the standalone
    operator invocation leaves this None (full sweep, unbounded, existing behavior).
    skip_missing_markets: when True, skip the two known-fully-missing-market backfill (an
    operator-triage concern, not part of the bounded per-cycle row budget).
    """
    if city_map is None:
        city_map = {c.name: c for c in load_cities()}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if max_rows is not None:
        rows = conn.execute(
            "SELECT * FROM settlement_outcomes WHERE authority='DISPUTED' "
            "ORDER BY recorded_at ASC LIMIT ?",
            (max_rows,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM settlement_outcomes WHERE authority='DISPUTED'"
        ).fetchall()

    disposition_counts: Counter[str] = Counter()
    reason_breakdown: Counter[str] = Counter()
    verified_ids: list[int] = []
    decisions: list[dict] = []

    conn.execute("SAVEPOINT drain_settlement_disputes")
    try:
        for row in rows:
            decision = resolve_disputed_row(conn, city_map, row)
            disposition_counts[decision["disposition"]] += 1
            reason_breakdown[decision.get("dispute_reason") or "NO_REASON_FIELD"] += 1
            decisions.append(decision)
            if decision["disposition"] == "verify":
                _apply_verify(conn, row, decision, city_map[row["city"]], now=now)
                verified_ids.append(decision["settlement_id"])

        missing_decisions: list[dict] = []
        for city_name, target_date, metric in (() if skip_missing_markets else missing_markets):
            existing = conn.execute(
                "SELECT 1 FROM settlement_outcomes WHERE city=? AND target_date=? AND temperature_metric=?",
                (city_name, target_date, metric),
            ).fetchone()
            if existing is not None:
                missing_decisions.append({
                    "city": city_name, "target_date": target_date, "temperature_metric": metric,
                    "disposition": "already_present", "detail": "row now exists; not backfilled by this script",
                })
                continue
            decision = resolve_missing_market(conn, city_map, city_name, target_date, metric)
            missing_decisions.append(decision)
            if decision["disposition"] == "verify":
                _insert_missing_verified(conn, city_map[city_name], target_date, metric, decision, now=now)

        verified_after = conn.execute(
            "SELECT COUNT(*) FROM settlement_outcomes WHERE authority='VERIFIED'"
        ).fetchone()[0]
        disputed_after = conn.execute(
            "SELECT COUNT(*) FROM settlement_outcomes WHERE authority='DISPUTED'"
        ).fetchone()[0]

        report = {
            "db_path": None,
            "disputed_before": len(rows),
            "dispute_reason_distribution": dict(reason_breakdown),
            "disposition_counts": dict(disposition_counts),
            "verified_settlement_ids": verified_ids,
            "missing_markets": missing_decisions,
            "verified_total_in_txn": int(verified_after),
            "disputed_total_in_txn": int(disputed_after),
            "applied": False,
            # Per-row detail for operator audit (--json only prints this; the compact
            # human-readable summary sticks to the aggregated keys above). Every value here
            # is already JSON-plain (dicts/str/float/None) — no dataclass payloads leak
            # through (see _raw_resolution_evidence).
            "row_decisions": decisions,
        }

        if apply:
            conn.execute("RELEASE SAVEPOINT drain_settlement_disputes")
            conn.commit()
            report["applied"] = True
        else:
            conn.execute("ROLLBACK TO SAVEPOINT drain_settlement_disputes")
            conn.execute("RELEASE SAVEPOINT drain_settlement_disputes")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT drain_settlement_disputes")
        conn.execute("RELEASE SAVEPOINT drain_settlement_disputes")
        raise

    return report


def _standalone(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(_DB_DEFAULT), help=f"Path to zeus-forecasts.db (default: {_DB_DEFAULT}).")
    parser.add_argument("--apply", action="store_true", help="Commit the drain (default: dry-run + rollback).")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON instead of a human summary.")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(args.db)  # standalone operator-invoked script; daemon lock intentionally not taken (dry-run default)
    conn.row_factory = sqlite3.Row
    try:
        report = drain(conn, apply=args.apply)
    finally:
        conn.close()
    report["db_path"] = args.db

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print("settlement_outcomes DISPUTE drain — REPORT")
        for key in (
            "db_path", "disputed_before", "dispute_reason_distribution",
            "disposition_counts", "verified_settlement_ids", "missing_markets",
            "verified_total_in_txn", "disputed_total_in_txn", "applied",
        ):
            print(f"  {key}: {report[key]}")
        if not args.apply:
            print("\nDRY-RUN (no changes applied). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_standalone())
