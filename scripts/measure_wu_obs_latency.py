# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: day0 first-principles review 2026-06-10 (operator charge #1:
#   WU publication latency is city-specific and large; measure it from our own
#   stored obs). Reads zeus-world.db observation_instants (source=wu_icao_history,
#   raw METAR timestamps in provenance_json) and zeus_trades.db
#   settlement_day_observation_authority (live wu_api poll ages).
"""Measure per-city WU station-observation cadence and pipeline latency.

Produces config/wu_obs_latency.json (loadable by src/signal/day0_obs_latency.py)
and a markdown table for docs/evidence.

Three measured surfaces (all from Zeus's own stored data — no new downloads):

1. STATION REPORT CADENCE (strong, ~10 days of data): distinct raw METAR
   timestamps per city from observation_instants.provenance_json
   (hour_max_raw_ts / hour_min_raw_ts). Median inter-report interval and the
   characteristic report minute-marks (e.g. KMIA reports at :53).

2. PIPELINE FIRST-SEEN DELAY (what the EDLI DAY0_EXTREME_UPDATED lane actually
   sees): MIN(imported_at) - raw METAR ts per observation. The obs_live_tick
   job imports hourly at :15, so this is dominated by the import grid, NOT by
   WU publication. This is the operative staleness of the persisted
   observation_instants surface that the day0 catch-up scanner reads.

3. LIVE-LANE OBS AGE (thin sample): settlement_day_observation_authority
   wu_api rows, decision_time_utc - observation_time_utc. This is the direct
   measurement of "how old is the freshest WU obs when we poll the live API"
   = WU publication delay + within-cadence phase.

Usage:
    PYTHONSAFEPATH=1 PYTHONPATH=. .venv/bin/python scripts/measure_wu_obs_latency.py \
        [--since 2026-05-27] [--out config/wu_obs_latency.json] [--md <path>]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Conservative global defaults when a city has no measurement (fail-closed:
# assume the slow end of the observed range; operator: ~30-40 min delay,
# cadence 30-60 min by city).
DEFAULT_MEDIAN_INTERVAL_MIN = 60.0
DEFAULT_PUBLICATION_DELAY_MIN = 40.0


def _parse(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def measure_station_cadence(world_db: Path, since: str) -> dict[str, dict]:
    conn = sqlite3.connect(f"file:{world_db}?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT city, station_id, provenance_json, imported_at
            FROM observation_instants
            WHERE source = 'wu_icao_history' AND utc_timestamp >= ?
            """,
            (since,),
        ).fetchall()
    finally:
        conn.close()

    raw_ts: dict[str, set[datetime]] = defaultdict(set)
    first_seen: dict[str, dict[datetime, datetime]] = defaultdict(dict)
    stations: dict[str, str] = {}
    for r in rows:
        try:
            prov = json.loads(r["provenance_json"] or "{}")
        except json.JSONDecodeError:
            continue
        imported = _parse(r["imported_at"])
        stations.setdefault(r["city"], str(prov.get("station_id") or r["station_id"] or ""))
        for key in ("hour_max_raw_ts", "hour_min_raw_ts"):
            t = _parse(prov.get(key))
            if t is None:
                continue
            raw_ts[r["city"]].add(t)
            if imported is not None:
                prev = first_seen[r["city"]].get(t)
                if prev is None or imported < prev:
                    first_seen[r["city"]][t] = imported

    out: dict[str, dict] = {}
    for city, times in raw_ts.items():
        ts = sorted(times)
        intervals = [
            (b - a).total_seconds() / 60.0
            for a, b in zip(ts, ts[1:])
            if 0.0 < (b - a).total_seconds() <= 6 * 3600
        ]
        minute_marks = Counter(t.minute for t in ts)
        delays = [
            (seen - t).total_seconds() / 60.0
            for t, seen in first_seen[city].items()
            if 0.0 <= (seen - t).total_seconds() <= 12 * 3600
        ]
        delays.sort()
        entry: dict = {
            "station_id": stations.get(city, ""),
            "n_raw_reports": len(ts),
            "median_report_interval_min": round(statistics.median(intervals), 1) if intervals else None,
            "report_minute_marks": [m for m, _ in minute_marks.most_common(4)],
            "pipeline_first_seen_delay_med_min": round(statistics.median(delays), 1) if delays else None,
            "pipeline_first_seen_delay_p90_min": round(delays[int(0.9 * len(delays))], 1) if delays else None,
        }
        out[city] = entry
    return out


def measure_live_lane_age(trades_db: Path) -> dict[str, dict]:
    conn = sqlite3.connect(f"file:{trades_db}?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT city, decision_time_utc, observation_time_utc
            FROM settlement_day_observation_authority
            WHERE source = 'wu_api' AND observation_time_utc IS NOT NULL
            """
        ).fetchall()
    finally:
        conn.close()
    ages: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        d, o = _parse(r["decision_time_utc"]), _parse(r["observation_time_utc"])
        if d is None or o is None:
            continue
        a = (d - o).total_seconds() / 60.0
        if 0.0 <= a <= 360.0:
            ages[r["city"]].append(a)
    out: dict[str, dict] = {}
    for city, a in ages.items():
        if len(a) < 3:
            continue
        a.sort()
        out[city] = {
            "live_polls": len(a),
            "live_obs_age_min_min": round(a[0], 1),
            "live_obs_age_med_min": round(statistics.median(a), 1),
            "live_obs_age_p90_min": round(a[int(0.9 * len(a))], 1),
        }
    return out


def build_model(cadence: dict[str, dict], live: dict[str, dict]) -> dict:
    cities: dict[str, dict] = {}
    for city, c in sorted(cadence.items()):
        entry = dict(c)
        entry.update(live.get(city, {}))
        interval = entry.get("median_report_interval_min") or DEFAULT_MEDIAN_INTERVAL_MIN
        # Expected worst-case "honest" age of a fresh running extreme at any
        # instant: one full report interval (phase) + publication delay. An obs
        # snapshot older than this budget means reports are MISSING — the
        # running extreme is a stale lower bound and boundary decisions must
        # widen. live_obs_age_med ≈ delay + interval/2 ⇒ delay ≈ med - interval/2.
        med_age = entry.get("live_obs_age_med_min")
        if med_age is not None:
            publication_delay = max(0.0, round(med_age - interval / 2.0, 1))
        else:
            publication_delay = DEFAULT_PUBLICATION_DELAY_MIN
        entry["publication_delay_est_min"] = publication_delay
        entry["staleness_budget_min"] = round(interval + publication_delay, 1)
        cities[city] = entry
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "authority": "measured: zeus-world.db observation_instants(wu_icao_history) raw METAR ts"
                     " + zeus_trades.db settlement_day_observation_authority(wu_api)",
        "defaults": {
            "median_report_interval_min": DEFAULT_MEDIAN_INTERVAL_MIN,
            "publication_delay_est_min": DEFAULT_PUBLICATION_DELAY_MIN,
            "staleness_budget_min": DEFAULT_MEDIAN_INTERVAL_MIN + DEFAULT_PUBLICATION_DELAY_MIN,
        },
        "cities": cities,
    }


def render_md(model: dict) -> str:
    lines = [
        "# Per-city WU observation latency (measured from Zeus's own stored obs)",
        "",
        f"Generated: {model['generated_at']}",
        "",
        "| city | station | med report interval (min) | report minute-marks | live obs age med/p90 (min) | est. publication delay (min) | staleness budget (min) | pipeline first-seen delay med/p90 (min) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for city, e in model["cities"].items():
        live = (
            f"{e.get('live_obs_age_med_min', '—')}/{e.get('live_obs_age_p90_min', '—')}"
            if e.get("live_obs_age_med_min") is not None
            else "—"
        )
        pipe = (
            f"{e.get('pipeline_first_seen_delay_med_min', '—')}/{e.get('pipeline_first_seen_delay_p90_min', '—')}"
        )
        lines.append(
            f"| {city} | {e.get('station_id','')} | {e.get('median_report_interval_min','—')} "
            f"| {':'+',:'.join(f'{m:02d}' for m in e.get('report_minute_marks', [])) if e.get('report_minute_marks') else '—'} "
            f"| {live} | {e.get('publication_delay_est_min','—')} | {e.get('staleness_budget_min','—')} | {pipe} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-05-27")
    ap.add_argument("--world-db", default=str(REPO_ROOT / "state" / "zeus-world.db"))
    ap.add_argument("--trades-db", default=str(REPO_ROOT / "state" / "zeus_trades.db"))
    ap.add_argument("--out", default=str(REPO_ROOT / "config" / "wu_obs_latency.json"))
    ap.add_argument("--md", default=str(
        REPO_ROOT / "docs" / "evidence" / "2026_06_10_day0_first_principles" / "wu_obs_latency_table.md"
    ))
    args = ap.parse_args()

    cadence = measure_station_cadence(Path(args.world_db), args.since)
    live = measure_live_lane_age(Path(args.trades_db))
    model = build_model(cadence, live)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(model, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    md = Path(args.md)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(render_md(model), encoding="utf-8")
    print(f"wrote {out} ({len(model['cities'])} cities) and {md}")


if __name__ == "__main__":
    main()
