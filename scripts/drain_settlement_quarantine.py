# Created: 2026-07-04
# Last reused or audited: 2026-07-04
# Lifecycle: created=2026-07-04; last_reviewed=2026-07-04; last_reused=never
#
# Purpose: Drain the settlement_outcomes QUARANTINED backlog in zeus-forecasts.db so the
#   chain-mirror reconciler (src/state/chain_mirror_reconciler.py::load_settlement_lookup,
#   10-min lane) — which grades legacy positions ONLY against authority='VERIFIED' — can
#   close every gradable legacy position. As of 2026-07-04 (audit finding): 383 of 8,527
#   settlement_outcomes rows are stuck QUARANTINED; two markets (Hong Kong 2026-06-25 low,
#   Helsinki 2026-07-02 high) have NO row at all.
#
# Method (RE-RESOLVE FROM PERSISTED EVIDENCE ONLY — never fetches the network):
#   1. Classify every QUARANTINED row by provenance_json.quarantine_reason.
#   2. `pc_audit_*`-prefixed reasons carry PRIOR HUMAN AUDIT judgment (a named operator
#      investigation already concluded non-reproducible / needs-collector / station-drift
#      for these specific rows — see docs/evidence/timing_audit/fallback_outcome_quality_2026-06-16.md
#      §4). This script NEVER auto-reactivates them, even when a mechanical re-check would
#      say "contained" — that would silently overrule a documented human judgment call.
#      They are reported separately as `manual_audit_reserved`.
#   3. All other (mechanically-quarantined) rows are re-resolved:
#        a. Look up the CURRENT persisted observation for the row's (city, target_date,
#           temperature_metric) from the city's CURRENTLY authorized settlement source
#           (src.execution.harvester._lookup_settlement_obs — the same source-family-routed
#           lookup the live harvester write path uses). Absent -> `unfillable_no_persisted_observation`.
#        b. Round it via SettlementSemantics.for_city(city).assert_settlement_value() (same
#           contract every settlement DB write must pass). Non-finite -> `settlement_precision_error`.
#        c. Recover the market's actual bin bounds from the row's own provenance_json
#           (pm_bin_lo/pm_bin_hi, or nested under provenance_json.v1_extra for rows migrated
#           by scripts/backfill_settlement_outcomes_canonical_2026_06_02.py). Absent (both
#           None) -> `unfillable_no_bin_info` — never invented.
#        d. Re-check containment (point/finite_range/open_shoulder aware, with the same
#           F-bin-on-C-city conversion + WMO edge-snap harvester_truth_writer.py applies).
#           Contained -> VERIFY. Not contained -> `conflicting_evidence_not_contained`
#           (the row's own recomputed value genuinely disagrees with the market's resolved
#           bin; this is real ambiguity, not a bug, per fallback_outcome_quality_2026-06-16.md).
#   4. The two known MISSING markets are backfilled the same way if persisted evidence
#      exists; live-DB check on 2026-07-04 found NEITHER `observations` NOR
#      `observation_instants` carries a row for either — both report `unfillable_no_persisted_observation`.
#   5. A VERIFY write is a narrow UPDATE (authority, settlement_value, winning_bin,
#      settlement_unit, provenance_json only — settled_at/market_slug/settlement_source are
#      NOT touched). provenance_json is never overwritten wholesale: the prior authority +
#      quarantine_reason + full prior provenance are preserved under `prior_provenance`, and
#      `reactivated_by`/`reactivated_at`/`drain_script` are stamped (mirrors the
#      `settlements.authority` monotonic-trigger discipline: QUARANTINED->VERIFIED requires
#      a non-empty text `reactivated_by`, even though settlement_outcomes has no such trigger).
#
# Scope: zeus-forecasts.db ONLY (settlement_outcomes). Never touches zeus_trades.db or
#   zeus-world.db. Never fetches network data — every value comes from already-persisted
#   `observations` rows. Dry-run by DEFAULT (SAVEPOINT + rollback); --apply commits.
#
# Root cause of the ever-growing backlog: NOT that the harvester's containment/tolerance
#   logic is too strict (fallback_outcome_quality_2026-06-16.md already showed the two
#   biggest reasons — obs_outside_bin 85%, source_disagreement 12% — are genuine boundary
#   ambiguity, not bugs) but that NO LANE EVER RE-CHECKS a QUARANTINED row once written, even
#   after later ingest corrects/backfills the underlying observation. This script IS that
#   lane; it is intentionally a repeatable drain, not a one-shot migration (rerun it after any
#   ingest backfill touching quarantined dates).
#
# Run:
#   dry-run (default):  python scripts/drain_settlement_quarantine.py [--json]
#   apply:               python scripts/drain_settlement_quarantine.py --apply
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DB_DEFAULT = _REPO_ROOT / "state" / "zeus-forecasts.db"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import City, load_cities  # noqa: E402
from src.contracts.exceptions import SettlementPrecisionError  # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402
from src.execution import harvester as harvester_mod  # noqa: E402

DRAIN_SCRIPT_ID = "scripts.drain_settlement_quarantine"

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


def _f_to_c(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def _extract_bin(provenance: dict) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Recover (pm_bin_lo, pm_bin_hi, pm_bin_unit) from a settlement_outcomes provenance blob.

    Live-written rows (harvester.py / harvester_truth_writer.py) carry these at the top
    level. Rows migrated by backfill_settlement_outcomes_canonical_2026_06_02.py nest the
    v1 `settlements` table columns under provenance_json['v1_extra'] instead — fall back
    there when the top level has neither bound.
    """
    lo = provenance.get("pm_bin_lo")
    hi = provenance.get("pm_bin_hi")
    unit = provenance.get("pm_bin_unit")
    if lo is None and hi is None and isinstance(provenance.get("v1_extra"), dict):
        v1_extra = provenance["v1_extra"]
        lo = v1_extra.get("pm_bin_lo")
        hi = v1_extra.get("pm_bin_hi")
        unit = v1_extra.get("unit") or unit
    return lo, hi, unit


def _effective_bin(
    lo: Optional[float], hi: Optional[float], pm_bin_unit: Optional[str], city: City
) -> tuple[Optional[float], Optional[float]]:
    """Convert + WMO-snap the recovered bin into the city's settlement unit.

    Mirrors src/ingest/harvester_truth_writer.py:490-527 (fix #262/#264): pre-2026 London
    markets were posed in F while London settles in C; the only live case is F-bin-on-C-city.
    """
    eff_lo, eff_hi = lo, hi
    if pm_bin_unit == "F" and city.settlement_unit == "C":
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


def resolve_quarantined_row(
    conn: sqlite3.Connection, city_map: dict[str, City], row: sqlite3.Row
) -> dict:
    """Decide the fate of one QUARANTINED settlement_outcomes row from persisted evidence only.

    Returns a dict with at least {settlement_id, city, target_date, temperature_metric,
    disposition, detail}. disposition is one of:
      manual_audit_reserved | unfillable_no_city | unfillable_no_persisted_observation |
      unfillable_no_bin_info | settlement_precision_error | conflicting_evidence_not_contained |
      verify
    'verify' additionally carries {rounded_value, winning_bin, obs}.
    """
    settlement_id = row["settlement_id"]
    city_name = row["city"]
    target_date = row["target_date"]
    metric = row["temperature_metric"]
    try:
        provenance = json.loads(row["provenance_json"] or "{}")
        if not isinstance(provenance, dict):
            provenance = {}
    except (json.JSONDecodeError, TypeError):
        provenance = {}
    reason = provenance.get("quarantine_reason", "")

    base = {
        "settlement_id": settlement_id,
        "city": city_name,
        "target_date": target_date,
        "temperature_metric": metric,
        "quarantine_reason": reason,
    }

    if _is_manual_audit_reason(reason):
        return {**base, "disposition": "manual_audit_reserved",
                "detail": "prior human audit judgment on record; not auto-reactivated"}

    city = city_map.get(city_name)
    if city is None:
        return {**base, "disposition": "unfillable_no_city",
                "detail": f"city {city_name!r} not in current cities.json"}

    obs = harvester_mod._lookup_settlement_obs(conn, city, target_date, temperature_metric=metric)
    if obs is None:
        return {**base, "disposition": "unfillable_no_persisted_observation",
                "detail": "no observations row from the currently authorized source family"}

    sem = SettlementSemantics.for_city(city)
    try:
        rounded = sem.assert_settlement_value(
            float(obs["observed_temp"]), context=f"{DRAIN_SCRIPT_ID}/{city_name}/{target_date}"
        )
    except SettlementPrecisionError as exc:
        return {**base, "disposition": "settlement_precision_error", "detail": str(exc)}

    lo, hi, pm_bin_unit = _extract_bin(provenance)
    if lo is None and hi is None:
        return {**base, "disposition": "unfillable_no_bin_info",
                "detail": "provenance carries no pm_bin_lo/pm_bin_hi (top-level or v1_extra)"}

    eff_lo, eff_hi = _effective_bin(lo, hi, pm_bin_unit, city)
    if not _is_contained(rounded, eff_lo, eff_hi):
        return {**base, "disposition": "conflicting_evidence_not_contained",
                "detail": f"recomputed {rounded} not in bin [{eff_lo}, {eff_hi}]",
                "rounded_value": rounded, "effective_bin": [eff_lo, eff_hi]}

    winning_bin = harvester_mod._canonical_bin_label(eff_lo, eff_hi, city.settlement_unit)
    return {
        **base,
        "disposition": "verify",
        "detail": f"recomputed {rounded} contained in bin [{eff_lo}, {eff_hi}]",
        "rounded_value": rounded,
        "winning_bin": winning_bin,
        "obs": obs,
    }


def resolve_missing_market(
    conn: sqlite3.Connection, city_map: dict[str, City], city_name: str, target_date: str, metric: str
) -> dict:
    """Same evidence-only re-resolution as resolve_quarantined_row, for a market with NO
    settlement_outcomes row at all. Never invents a bin — a market with no row also carries
    no provenance to recover pm_bin_lo/pm_bin_hi from, so these can only ever report
    unfillable_no_bin_info (once an observation exists) or unfillable_no_persisted_observation.
    """
    base = {"city": city_name, "target_date": target_date, "temperature_metric": metric}
    city = city_map.get(city_name)
    if city is None:
        return {**base, "disposition": "unfillable_no_city", "detail": f"city {city_name!r} not in current cities.json"}
    obs = harvester_mod._lookup_settlement_obs(conn, city, target_date, temperature_metric=metric)
    if obs is None:
        return {**base, "disposition": "unfillable_no_persisted_observation",
                "detail": "no observations row from the currently authorized source family; "
                          "no market_events / bin evidence exists to check containment against either"}
    # An observation exists but this script has no persisted market bin to check it against
    # (no settlement_outcomes row => no provenance to read pm_bin_lo/pm_bin_hi from). Report
    # rather than guess.
    return {**base, "disposition": "unfillable_no_bin_info",
            "detail": "observation now present but no persisted market bin to verify containment",
            "rounded_value": obs["observed_temp"]}


def _apply_verify(conn: sqlite3.Connection, row: sqlite3.Row, decision: dict, city: City, *, now: str) -> None:
    provenance = json.loads(row["provenance_json"] or "{}")
    if not isinstance(provenance, dict):
        provenance = {}
    new_provenance = dict(provenance)
    new_provenance["prior_provenance"] = provenance
    new_provenance["prior_authority"] = "QUARANTINED"
    new_provenance["prior_quarantine_reason"] = provenance.get("quarantine_reason")
    new_provenance["reactivated_by"] = DRAIN_SCRIPT_ID
    new_provenance["reactivated_at"] = now
    new_provenance["drain_script"] = "scripts/drain_settlement_quarantine.py"
    new_provenance["drain_reconfirmed_obs_id"] = decision["obs"].get("id")
    new_provenance["drain_reconfirmed_value"] = decision["rounded_value"]

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
         WHERE settlement_id=? AND authority='QUARANTINED'
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
) -> None:  # pragma: no cover - no live-DB case currently reaches this path (see report)
    provenance = {
        "writer": DRAIN_SCRIPT_ID,
        "writer_script": "scripts/drain_settlement_quarantine.py",
        "reconstruction_method": "drain_missing_market_backfill",
        "reactivated_by": DRAIN_SCRIPT_ID,
        "reactivated_at": now,
        "drain_reconfirmed_value": decision["rounded_value"],
    }
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
) -> dict:
    """Diagnose + (dry-run or apply) drain the QUARANTINED backlog. Never touches VERIFIED rows.

    Always runs inside a SAVEPOINT; rolls back unless apply=True. Idempotent: a second run
    over an already-drained DB reclassifies verified rows as no longer QUARANTINED (they are
    simply absent from the WHERE authority='QUARANTINED' scan) and touches nothing.

    city_map defaults to the real `src.config.load_cities()` universe; tests inject a
    synthetic map instead of touching config/cities.json.
    """
    if city_map is None:
        city_map = {c.name: c for c in load_cities()}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    rows = conn.execute(
        "SELECT * FROM settlement_outcomes WHERE authority='QUARANTINED'"
    ).fetchall()

    disposition_counts: Counter[str] = Counter()
    reason_breakdown: Counter[str] = Counter()
    verified_ids: list[int] = []
    decisions: list[dict] = []

    conn.execute("SAVEPOINT drain_settlement_quarantine")
    try:
        for row in rows:
            decision = resolve_quarantined_row(conn, city_map, row)
            disposition_counts[decision["disposition"]] += 1
            reason_breakdown[decision.get("quarantine_reason") or "NO_REASON_FIELD"] += 1
            decisions.append(decision)
            if decision["disposition"] == "verify":
                _apply_verify(conn, row, decision, city_map[row["city"]], now=now)
                verified_ids.append(decision["settlement_id"])

        missing_decisions: list[dict] = []
        for city_name, target_date, metric in missing_markets:
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
        quarantined_after = conn.execute(
            "SELECT COUNT(*) FROM settlement_outcomes WHERE authority='QUARANTINED'"
        ).fetchone()[0]

        report = {
            "db_path": None,
            "quarantined_before": len(rows),
            "quarantine_reason_distribution": dict(reason_breakdown),
            "disposition_counts": dict(disposition_counts),
            "verified_settlement_ids": verified_ids,
            "missing_markets": missing_decisions,
            "verified_total_in_txn": int(verified_after),
            "quarantined_total_in_txn": int(quarantined_after),
            "applied": False,
            # Per-row detail for operator audit (--json only prints this; the compact
            # human-readable summary sticks to the aggregated keys above). Drop the
            # non-serializable 'obs' payload from verify decisions — obs_id is already
            # captured in the written provenance_json.drain_reconfirmed_obs_id.
            "row_decisions": [
                {k: v for k, v in d.items() if k != "obs"} for d in decisions
            ],
        }

        if apply:
            conn.execute("RELEASE SAVEPOINT drain_settlement_quarantine")
            conn.commit()
            report["applied"] = True
        else:
            conn.execute("ROLLBACK TO SAVEPOINT drain_settlement_quarantine")
            conn.execute("RELEASE SAVEPOINT drain_settlement_quarantine")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT drain_settlement_quarantine")
        conn.execute("RELEASE SAVEPOINT drain_settlement_quarantine")
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
        print("settlement_outcomes QUARANTINE drain — REPORT")
        for key in (
            "db_path", "quarantined_before", "quarantine_reason_distribution",
            "disposition_counts", "verified_settlement_ids", "missing_markets",
            "verified_total_in_txn", "quarantined_total_in_txn", "applied",
        ):
            print(f"  {key}: {report[key]}")
        if not args.apply:
            print("\nDRY-RUN (no changes applied). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_standalone())
