"""G5 proof counts: the daily NOAA page product vs settled chain cells, per degC NOAA city.

Read-only (``?mode=ro`` + ``PRAGMA query_only``) over state/zeus-forecasts.db.
For every degC city whose settlement_source_type is noaa, pair each
settlement_outcomes cell (high/low) whose settlement source is the weather.gov
page with the daily ``observations`` row ``source = noaa_wrh_<icao>`` for the
same city/date, rounded through ``SettlementSemantics``.

Two counts are kept apart on purpose:
- ``verified``: cells the truth writer marked VERIFIED; ``n_exact`` counts
  round(page) == settlement_value. VERIFIED already requires the page value to
  sit in the chain bin, so this count proves the writer's bookkeeping, not an
  independent agreement.
- ``chain_bin``: every cell carrying a chain bin (VERIFIED and DISPUTED); a cell
  is exact when round(page) lies in [pm_bin_lo, pm_bin_hi]. Each exception is
  listed with the writer's dispute reason.
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.config import cities_by_name  # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402

DB = "file:/Users/leofitz/zeus/state/zeus-forecasts.db?mode=ro"
OUT = Path(__file__).with_suffix(".json")


def main() -> None:
    conn = sqlite3.connect(DB, uri=True)
    conn.execute("PRAGMA query_only=1")
    report = {"computed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "database": "state/zeus-forecasts.db (read-only)", "cities": []}
    for name, city in sorted(cities_by_name.items()):
        if city.settlement_source_type != "noaa" or city.settlement_unit != "C":
            continue
        station = city.wu_station.upper()
        source = "noaa_wrh_" + station.lower()
        semantics = SettlementSemantics.for_city(city)
        daily = {date: (high, low, unit) for date, high, low, unit in conn.execute(
            "SELECT target_date, high_temp, low_temp, unit FROM observations "
            "WHERE city = ? AND source = ?", (name, source))}
        verified = {"n_pairs": 0, "n_exact": 0, "mismatches": []}
        chain_bin = {"n_pairs": 0, "n_exact": 0, "exceptions": []}
        for date, metric, authority, value, provenance in conn.execute(
                "SELECT target_date, temperature_metric, authority, settlement_value, provenance_json "
                "FROM settlement_outcomes WHERE city = ? "
                "AND settlement_source LIKE 'https://www.weather.gov/wrh/timeseries%' "
                "ORDER BY target_date, temperature_metric", (name,)):
            row = daily.get(date)
            if row is None or row[2] != "C":
                continue
            page = row[0] if metric == "high" else row[1]
            if page is None:
                continue
            rounded = semantics.round_single(float(page))
            meta = json.loads(provenance or "{}")
            if authority == "VERIFIED":
                verified["n_pairs"] += 1
                if rounded == value:
                    verified["n_exact"] += 1
                else:
                    verified["mismatches"].append({"date": date, "metric": metric,
                                                   "page": page, "settlement": value})
            lo, hi = meta.get("pm_bin_lo"), meta.get("pm_bin_hi")
            if (lo is None and hi is None) or meta.get("pm_bin_unit") not in (None, "C"):
                continue
            chain_bin["n_pairs"] += 1
            if (lo is None or rounded >= lo) and (hi is None or rounded <= hi):
                chain_bin["n_exact"] += 1
            else:
                chain_bin["exceptions"].append({
                    "date": date, "metric": metric, "page": page, "page_rounded": rounded,
                    "pm_bin_lo": lo, "pm_bin_hi": hi, "authority": authority,
                    "dispute_reason": meta.get("dispute_reason")})
        dates = sorted(daily)
        report["cities"].append({
            "city": name, "station": station, "channel": source,
            "daily_rows": len(dates), "first_date": dates[0] if dates else None,
            "last_date": dates[-1] if dates else None,
            "verified": verified, "chain_bin": chain_bin})
    OUT.write_text(json.dumps(report, indent=1) + "\n")
    for row in report["cities"]:
        v, b = row["verified"], row["chain_bin"]
        print(f"{row['city']:14s} {row['station']} verified {v['n_exact']}/{v['n_pairs']} "
              f"chain_bin {b['n_exact']}/{b['n_pairs']} days {row['daily_rows']}")


if __name__ == "__main__":
    main()
