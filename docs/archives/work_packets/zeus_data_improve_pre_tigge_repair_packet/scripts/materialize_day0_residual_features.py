#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.signal.day0_residual_features import (
    Day0ResidualFact,
    EnsembleContext,
    daylight_progress,
    ensemble_remaining_quantiles,
    latest_ensemble_before,
    obs_age_minutes,
)


def load_solar(conn: sqlite3.Connection) -> dict[tuple[str, str], tuple[str, str]]:
    return {
        (city, target_date): (sunrise_local, sunset_local)
        for city, target_date, sunrise_local, sunset_local in conn.execute(
            "SELECT city, target_date, sunrise_local, sunset_local FROM solar_daily"
        )
    }



def load_peak_prob(conn: sqlite3.Connection) -> dict[tuple[str, int, int], float]:
    return {
        (city, int(month), int(hour)): float(p_high_set)
        for city, month, hour, p_high_set in conn.execute(
            "SELECT city, month, hour, p_high_set FROM diurnal_peak_prob"
        )
    }



def load_settlements(conn: sqlite3.Connection) -> dict[tuple[str, str], float]:
    return {
        (city, target_date): float(settlement_value)
        for city, target_date, settlement_value in conn.execute(
            "SELECT city, target_date, settlement_value FROM settlements WHERE settlement_value IS NOT NULL"
        )
    }



def load_ensemble_lookup(conn: sqlite3.Connection) -> dict[tuple[str, str], list[EnsembleContext]]:
    lookup: dict[tuple[str, str], list[EnsembleContext]] = defaultdict(list)
    for city, target_date, available_at, members_json, spread in conn.execute(
        """
        SELECT city, target_date, available_at, members_json, spread
        FROM ensemble_snapshots
        WHERE members_json IS NOT NULL AND available_at IS NOT NULL
        ORDER BY city, target_date, available_at
        """
    ):
        try:
            members = [float(v) for v in json.loads(members_json)]
        except Exception:
            continue
        lookup[(city, target_date)].append(
            EnsembleContext(available_at=available_at, members=members, spread=None if spread is None else float(spread))
        )
    return lookup



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    solar = load_solar(conn)
    peak_prob = load_peak_prob(conn)
    settlements = load_settlements(conn)
    ens_lookup = load_ensemble_lookup(conn)

    sql = """
        SELECT
            id,
            city,
            target_date,
            source,
            local_timestamp,
            utc_timestamp,
            local_hour,
            temp_current,
            running_max,
            delta_rate_per_h,
            imported_at
        FROM observation_instants
        WHERE 1=1
    """
    params: list[object] = []
    if args.start_date:
        sql += " AND target_date >= ?"
        params.append(args.start_date)
    if args.end_date:
        sql += " AND target_date <= ?"
        params.append(args.end_date)
    sql += " ORDER BY target_date, city, utc_timestamp"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"

    inserted = 0
    complete = 0
    missing = 0
    for (
        obs_id,
        city,
        target_date,
        source,
        local_timestamp,
        utc_timestamp,
        local_hour,
        temp_current,
        running_max,
        delta_rate_per_h,
        imported_at,
    ) in conn.execute(sql, params):
        reasons: list[str] = []
        solar_pair = solar.get((city, target_date))
        if solar_pair is None:
            reasons.append("solar_daily_missing")
            sunrise_local = sunset_local = None
        else:
            sunrise_local, sunset_local = solar_pair

        daylight = daylight_progress(local_timestamp, sunrise_local, sunset_local)
        if daylight is None:
            reasons.append("daylight_progress_unavailable")

        age = obs_age_minutes(utc_timestamp, imported_at)
        if age is None:
            reasons.append("obs_age_unavailable")

        month = int(target_date[5:7])
        peak_key = (city, month, int(local_hour or 0))
        peak = peak_prob.get(peak_key)
        if peak is None:
            reasons.append("post_peak_confidence_unavailable")

        ens = latest_ensemble_before(utc_timestamp, ens_lookup.get((city, target_date), []))
        q50 = q90 = ens_spread = None
        if ens is None:
            reasons.append("ensemble_context_missing")
        else:
            q50, q90, ens_spread = ensemble_remaining_quantiles(running_max, ens.members)
            if q50 is None:
                reasons.append("ensemble_remaining_quantiles_unavailable")

        settlement_value = settlements.get((city, target_date))
        residual_upside = None
        has_upside = None
        if settlement_value is not None and running_max is not None:
            residual_upside = max(0.0, float(settlement_value) - float(running_max))
            has_upside = 1 if residual_upside > 0 else 0
        else:
            reasons.append("residual_target_unavailable")

        fact_status = "complete" if not reasons else "missing_inputs"
        complete += int(fact_status == "complete")
        missing += int(fact_status != "complete")
        conn.execute(
            """
            INSERT OR REPLACE INTO day0_residual_fact (
                fact_id, city, target_date, source, local_timestamp, utc_timestamp,
                local_hour, temp_current, running_max, delta_rate_per_h,
                daylight_progress, obs_age_minutes, post_peak_confidence,
                ens_q50_remaining, ens_q90_remaining, ens_spread,
                settlement_value, residual_upside, has_upside,
                fact_status, missing_reason_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                str(uuid.uuid4()), city, target_date, source, local_timestamp, utc_timestamp,
                local_hour, temp_current, running_max, delta_rate_per_h,
                daylight, age, peak, q50, q90, ens_spread,
                settlement_value, residual_upside, has_upside,
                fact_status, json.dumps(reasons, ensure_ascii=False, sort_keys=True),
            ),
        )
        inserted += 1
    conn.commit()
    conn.close()
    print({"inserted": inserted, "complete": complete, "missing_inputs": missing})


if __name__ == "__main__":
    main()
