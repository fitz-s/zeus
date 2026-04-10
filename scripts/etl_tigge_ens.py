"""ETL: TIGGE ENS member vectors → ensemble_snapshots + calibration_pairs.

Source: 51 source data/raw/tigge_ecmwf_ens/{city}/{date}/members.json (122 files)
Target: zeus.db:ensemble_snapshots + calibration_pairs

CAVEAT: TIGGE parameter 167 (2t) = instantaneous T+24h temperature, NOT daily max.
- US cities (step_024 at 00Z init): valid ~4-7 PM local → reasonable daily max proxy
- European cities: valid at midnight local → NOT daily max, lower quality for calibration
This is documented in data_version = "tigge_step024_v1".
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.manager import season_from_date
from src.calibration.store import add_calibration_pair
from src.config import cities_by_name, cities_by_alias
from src.contracts import SettlementSemantics
from src.data.market_scanner import _parse_temp_range
from src.state.db import get_shared_connection as get_connection, init_schema
from src.types import Bin


TIGGE_BASE = Path.home() / ".openclaw/workspace-venus/51 source data/raw/tigge_ecmwf_ens"

# Cities where T+24h at 00Z is a poor proxy for daily max
OVERNIGHT_CITIES = {
    "Ankara", "Beijing", "Buenos Aires", "Chengdu", "Chongqing",
    "Hong Kong", "Istanbul", "London", "Lucknow", "Madrid",
    "Mexico City", "Milan", "Moscow", "Munich", "Paris", "Sao Paulo",
    "Seoul", "Shanghai", "Shenzhen", "Singapore", "Taipei",
    "Tel Aviv", "Tokyo", "Toronto", "Warsaw", "Wellington", "Wuhan",
}


def _resolve_city_name(dirname: str) -> str | None:
    """Map TIGGE directory name to Zeus city name."""
    name_map = {
        "ankara": "Ankara", "atlanta": "Atlanta", "austin": "Austin",
        "beijing": "Beijing", "buenos-aires": "Buenos Aires",
        "chengdu": "Chengdu", "chicago": "Chicago", "chongqing": "Chongqing",
        "dallas": "Dallas", "denver": "Denver",
        "hong-kong": "Hong Kong", "houston": "Houston", "istanbul": "Istanbul",
        "london": "London", "los-angeles": "Los Angeles", "lucknow": "Lucknow",
        "madrid": "Madrid", "mexico-city": "Mexico City", "miami": "Miami",
        "milan": "Milan", "moscow": "Moscow", "munich": "Munich",
        "nyc": "NYC", "paris": "Paris",
        "san-francisco": "San Francisco", "sao-paulo": "Sao Paulo",
        "seattle": "Seattle", "seoul": "Seoul", "shanghai": "Shanghai",
        "shenzhen": "Shenzhen", "singapore": "Singapore",
        "taipei": "Taipei", "tel-aviv": "Tel Aviv", "tokyo": "Tokyo",
        "toronto": "Toronto", "warsaw": "Warsaw",
        "wellington": "Wellington", "wuhan": "Wuhan",
    }
    return name_map.get(dirname)


_SUPPORTED_TIGGE_CITY_NAMES = frozenset({
    "Ankara", "Atlanta", "Austin", "Beijing", "Buenos Aires",
    "Chengdu", "Chicago", "Chongqing", "Dallas", "Denver",
    "Hong Kong", "Houston", "Istanbul", "London", "Los Angeles", "Lucknow",
    "Madrid", "Mexico City", "Miami", "Milan", "Moscow", "Munich",
    "NYC", "Paris", "San Francisco", "Sao Paulo",
    "Seattle", "Seoul", "Shanghai", "Shenzhen", "Singapore",
    "Taipei", "Tel Aviv", "Tokyo", "Toronto", "Warsaw",
    "Wellington", "Wuhan",
})


def _unsupported_configured_cities() -> list[str]:
    """Return configured cities for which TIGGE data is not available."""
    return sorted(set(cities_by_name) - _SUPPORTED_TIGGE_CITY_NAMES)


def run_etl() -> dict:
    conn = get_connection()
    init_schema(conn)

    imported = 0
    skipped = 0
    cal_pairs = 0

    if not TIGGE_BASE.exists():
        print(f"TIGGE base not found: {TIGGE_BASE}")
        return {"imported": 0}

    for city_dir in sorted(TIGGE_BASE.iterdir()):
        if not city_dir.is_dir():
            continue

        city_name = _resolve_city_name(city_dir.name)
        if city_name is None:
            print(f"  SKIP unknown city dir: {city_dir.name}")
            skipped += 1
            continue

        city = cities_by_name.get(city_name)

        for date_dir in sorted(city_dir.iterdir()):
            if not date_dir.is_dir():
                continue

            # Find the step_024 members JSON
            members_file = None
            for f in date_dir.iterdir():
                if f.name.endswith("step_024.json") and "members" in f.name:
                    members_file = f
                    break

            if members_file is None:
                skipped += 1
                continue

            target_date = f"{date_dir.name[:4]}-{date_dir.name[4:6]}-{date_dir.name[6:8]}"

            # Check if already imported
            existing = conn.execute("""
                SELECT COUNT(*) FROM ensemble_snapshots
                WHERE city = ? AND target_date = ? AND data_version LIKE 'tigge%'
            """, (city_name, target_date)).fetchone()[0]
            if existing > 0:
                continue

            try:
                with open(members_file) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"  ERROR reading {members_file}: {e}")
                skipped += 1
                continue

            members = data.get("members", [])
            if len(members) != 51:
                print(f"  SKIP {city_name} {target_date}: {len(members)} members (expected 51)")
                skipped += 1
                continue

            # Extract member values
            values = np.array([m["value_native_unit"] for m in members], dtype=np.float64)

            # Reconstruct timestamps
            data_date = str(members[0].get("data_date", ""))
            issue_time = f"{data_date[:4]}-{data_date[4:6]}-{data_date[6:8]}T00:00:00Z"
            valid_time = (datetime.fromisoformat(issue_time.replace("Z", "+00:00"))
                          + timedelta(hours=24)).isoformat()
            available_at = f"{data_date[:4]}-{data_date[4:6]}-{data_date[6:8]}T08:00:00Z"
            fetch_time = data.get("generated_at", datetime.now(timezone.utc).isoformat())

            # Compute P_raw if we have bin structure
            p_raw_json = None
            bins = _get_bins_for_settlement(conn, city_name, target_date)
            if bins:
                # Simple member counting (no MC noise — TIGGE values are already single-point)
                p_raw = np.zeros(len(bins))
                member_values = SettlementSemantics.for_city(city).round_values(values)
                for i, b in enumerate(bins):
                    if b.is_open_low:
                        p_raw[i] = np.mean(member_values <= b.high)
                    elif b.is_open_high:
                        p_raw[i] = np.mean(member_values >= b.low)
                    else:
                        p_raw[i] = np.mean((member_values >= b.low) & (member_values <= b.high))

                total = p_raw.sum()
                if total > 0:
                    p_raw = p_raw / total
                p_raw_json = json.dumps(p_raw.tolist())

            # Flag quality for European cities
            quality_note = "overnight_snapshot" if city_name in OVERNIGHT_CITIES else "near_peak"

            conn.execute("""
                INSERT OR IGNORE INTO ensemble_snapshots
                (city, target_date, issue_time, valid_time, available_at, fetch_time,
                 lead_hours, members_json, p_raw_json, spread, is_bimodal,
                 model_version, data_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                city_name, target_date, issue_time, valid_time,
                available_at, fetch_time, 24.0,
                json.dumps(values.tolist()), p_raw_json,
                float(np.std(values)), 0,
                "ecmwf_tigge", f"tigge_step024_v1_{quality_note}",
            ))
            imported += 1

            # Generate calibration pairs if we have both P_raw and settlement
            if p_raw_json is not None:
                n = _generate_cal_pairs(conn, city, city_name, target_date, bins, p_raw)
                cal_pairs += n

            print(f"  OK {city_name:15s} {target_date} spread={np.std(values):.2f} "
                  f"quality={quality_note} bins={len(bins) if bins else 0}")

    conn.commit()
    conn.close()

    print(f"\nImported: {imported} ENS snapshots")
    print(f"Calibration pairs: {cal_pairs}")
    print(f"Skipped: {skipped}")

    return {"imported": imported, "cal_pairs": cal_pairs, "skipped": skipped}


def _get_bins_for_settlement(conn, city: str, target_date: str) -> list[Bin]:
    """Get bin structure if this city-date has a settlement with market_events."""
    rows = conn.execute("""
        SELECT me.range_label
        FROM market_events me
        INNER JOIN settlements s ON me.city = s.city AND me.target_date = s.target_date
        WHERE me.city = ? AND me.target_date = ?
    """, (city, target_date)).fetchall()

    if not rows:
        return []

    # Resolve city name to City object for settlement_unit
    city_obj = cities_by_name.get(city)
    default_unit = city_obj.settlement_unit if city_obj else "F"

    bins = []
    for r in rows:
        low, high = _parse_temp_range(r["range_label"])
        if low is None and high is None:
            continue
        # Infer unit from label text: market labels carry °F or °C suffix
        label = r["range_label"]
        if "°C" in label or "°c" in label:
            label_unit = "C"
        elif "°F" in label or "°f" in label:
            label_unit = "F"
        else:
            label_unit = default_unit
        bins.append(Bin(low=low, high=high, label=label, unit=label_unit))

    # Sort by boundary
    def sort_key(b):
        if b.is_open_low:
            return -1e9
        return b.low if b.low is not None else -1e9
    bins.sort(key=sort_key)
    return bins


def _generate_cal_pairs(conn, city, city_name, target_date, bins, p_raw) -> int:
    """Generate calibration pairs from TIGGE data + settlement outcome."""
    settlement = conn.execute("""
        SELECT winning_bin FROM settlements
        WHERE city = ? AND target_date = ?
    """, (city_name, target_date)).fetchone()

    if settlement is None:
        return 0

    winning = settlement["winning_bin"]
    season = season_from_date(target_date, lat=city.lat if city else 90.0)
    cluster = city.cluster if city else "Other"

    count = 0
    for i, b in enumerate(bins):
        if i >= len(p_raw):
            break
        # Match winning bin by label comparison
        outcome = 1 if b.label and _bin_matches_winning(b, winning) else 0

        add_calibration_pair(
            conn, city=city_name, target_date=target_date,
            range_label=b.label, p_raw=float(p_raw[i]),
            outcome=outcome, lead_days=1.0,
            season=season, cluster=cluster,
            forecast_available_at=f"{target_date}T08:00:00Z",
        )
        count += 1

    return count


def _bin_matches_winning(b: Bin, winning_range: str) -> bool:
    """Check if bin matches the winning_range from settlements."""
    parts = winning_range.replace(" ", "").split("-")
    try:
        if len(parts) == 2:
            w_low, w_high = float(parts[0]), float(parts[1])
        elif len(parts) == 3 and parts[0] == "":
            w_low, w_high = -float(parts[1]), float(parts[2])
        else:
            return False
    except ValueError:
        return False

    if b.is_open_low and w_low <= -998:
        return b.high is not None and abs(w_high - b.high) < 1.0
    elif b.is_open_high and w_high >= 998:
        return b.low is not None and abs(w_low - b.low) < 1.0
    elif b.low is not None and b.high is not None:
        return abs(w_low - b.low) < 1.0 and abs(w_high - b.high) < 1.0
    return False


if __name__ == "__main__":
    result = run_etl()
    print(f"\nDone: {result}")
