"""P0 ETL: TIGGE ENS vectors → calibration_pairs + ensemble_snapshots.

Unlocks non-MAM Platt buckets by refitting every configured cluster × season
bucket from the canonical config taxonomy.

Source: ~/.openclaw/workspace-venus/51 source data/raw/tigge_ecmwf_ens/{city}/{date}/
Target: zeus.db:calibration_pairs + ensemble_snapshots
"""

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.manager import bucket_key, season_from_date
from src.calibration.store import add_calibration_pair, get_pairs_count
from src.config import calibration_clusters, calibration_seasons, cities_by_name
from src.contracts import SettlementSemantics
from src.data.market_scanner import _parse_temp_range
from src.state.db import get_shared_connection as get_connection, init_schema


TIGGE_ROOT = Path.home() / ".openclaw/workspace-venus/51 source data/raw/tigge_ecmwf_ens"

# TIGGE dir name → Zeus canonical name
CITY_MAP = {
    "nyc": "NYC", "chicago": "Chicago", "atlanta": "Atlanta",
    "seattle": "Seattle", "dallas": "Dallas", "miami": "Miami",
    "los-angeles": "Los Angeles", "san-francisco": "San Francisco",
    "london": "London", "paris": "Paris", "austin": "Austin",
    "denver": "Denver", "houston": "Houston",
    "seoul": "Seoul", "shanghai": "Shanghai", "tokyo": "Tokyo",
    "buenos-aires": "Buenos Aires", "hong-kong": "Hong Kong",
    "munich": "Munich", "shenzhen": "Shenzhen", "wellington": "Wellington",
}


def run_etl() -> dict:
    conn = get_connection()
    init_schema(conn)

    pairs_before = conn.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()[0]
    snapshots_before = conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()[0]

    processed = 0
    matched = 0
    pairs_added = 0
    skipped_no_city = 0
    skipped_no_settlement = 0
    skipped_no_bins = 0

    for city_dir in sorted(TIGGE_ROOT.iterdir()):
        if not city_dir.is_dir():
            continue

        city_name = CITY_MAP.get(city_dir.name)
        if city_name is None:
            skipped_no_city += 1
            continue

        city = cities_by_name.get(city_name)
        if city is None:
            skipped_no_city += 1
            continue

        for date_dir in sorted(city_dir.iterdir()):
            if not date_dir.is_dir():
                continue

            # Find ALL member JSONs (step_024, step_048, ..., step_168)
            member_files = []
            for f in date_dir.iterdir():
                if "members" in f.name and f.suffix == ".json":
                    # Extract step from filename: ...step_024.json → 24
                    import re
                    m = re.search(r"step_(\d+)", f.name)
                    if m:
                        step = int(m.group(1))
                        member_files.append((f, step))

            if not member_files:
                continue

            # Parse init date
            dname = date_dir.name
            init_date_str = f"{dname[:4]}-{dname[4:6]}-{dname[6:8]}"

            # Process each step file — target_date = init_date + step_hours/24
            from datetime import timedelta
            init_date = date.fromisoformat(init_date_str)

            for member_file, step_hours in member_files:
                target_d = init_date + timedelta(days=step_hours // 24)
                target_date = target_d.isoformat()
                lead_days = step_hours / 24.0
                processed += 1

                # Check if THIS SPECIFIC step already imported
                dv = f"tigge_cal_v3_step{step_hours:03d}"
                existing = conn.execute("""
                    SELECT COUNT(*) FROM ensemble_snapshots
                    WHERE city = ? AND target_date = ? AND data_version = ?
                """, (city_name, target_date, dv)).fetchone()[0]
                if existing > 0:
                    continue

                # Load members
                try:
                    with open(member_file) as f:
                        data = json.load(f)
                except Exception:
                    continue

                members = data.get("members", [])
                if len(members) != 51:
                    continue

                values = np.array([m["value_native_unit"] for m in members], dtype=np.float64)
                values_measured = SettlementSemantics.for_city(city).round_values(values)

                # Store ensemble snapshot
                conn.execute("""
                    INSERT OR IGNORE INTO ensemble_snapshots
                    (city, target_date, issue_time, valid_time, available_at, fetch_time,
                     lead_hours, members_json, spread, is_bimodal,
                     model_version, data_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    city_name, target_date,
                    f"{target_date}T00:00:00Z",
                    f"{target_date}T00:00:00Z",
                    f"{target_date}T08:00:00Z",
                    data.get("generated_at", ""),
                    float(step_hours),
                    json.dumps(values.tolist()),
                    float(np.std(values)),
                    0,
                    "ecmwf_tigge",
                    f"tigge_cal_v3_step{step_hours:03d}",
                ))

                # Match settlement
                settlement = conn.execute("""
                    SELECT winning_bin FROM settlements
                    WHERE city = ? AND target_date = ?
                """, (city_name, target_date)).fetchone()

                if settlement is None:
                    skipped_no_settlement += 1
                    continue

                winning_bin = settlement["winning_bin"]
                matched += 1

                # Get bin structure: try market_events first, then synthesize from ENS
                bins = _get_bins(conn, city_name, target_date)
                if not bins:
                    # No market_events → synthesize standard 11-bin structure from ENS
                    bins = _synthesize_bins(values, city.settlement_unit)

                # Generate calibration pairs
                season = season_from_date(target_date)
                for label, low, high in bins:
                    # Compute P_raw for this bin
                    if low is None and high is not None:
                        p_raw = float(np.sum(values_measured <= high)) / 51
                    elif high is None and low is not None:
                        p_raw = float(np.sum(values_measured >= low)) / 51
                    elif low is not None and high is not None:
                        p_raw = float(np.sum((values_measured >= low) & (values_measured <= high))) / 51
                    else:
                        continue

                    # Match outcome: parse winning temp from winning_bin, check if it falls in this bin
                    outcome = 1 if _temp_in_bin(winning_bin, low, high) else 0

                    try:
                        add_calibration_pair(
                            conn,
                            city=city_name,
                            target_date=target_date,
                            range_label=label,
                            p_raw=p_raw,
                            outcome=outcome,
                            lead_days=lead_days,
                            season=season,
                            cluster=city.cluster,
                            forecast_available_at=f"{target_date}T08:00:00Z",
                        )
                        pairs_added += 1
                    except Exception:
                        pass  # Duplicate — INSERT OR IGNORE handles this

    conn.commit()

    pairs_after = conn.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()[0]
    snapshots_after = conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()[0]

    # Refit all Platt models
    print("\nRefitting Platt models...")
    from src.calibration.manager import _fit_from_pairs
    fitted = 0
    for cluster in calibration_clusters():
        for season in calibration_seasons():
            bk = bucket_key(cluster, season)
            n = get_pairs_count(conn, cluster, season)
            if n >= 15:
                cal = _fit_from_pairs(conn, cluster, season)
                if cal is not None:
                    status = f"n={cal.n_samples}, A={cal.A:.3f}"
                    fitted += 1
                else:
                    status = "fit failed"
            else:
                status = f"n={n} (< 15)"
            print(f"  {bk:25s}: {status}")

    conn.close()

    report = {
        "vectors_processed": processed,
        "settlements_matched": matched,
        "pairs_before": pairs_before,
        "pairs_after": pairs_after,
        "new_pairs": pairs_after - pairs_before,
        "snapshots_before": snapshots_before,
        "snapshots_after": snapshots_after,
        "platt_models_fitted": fitted,
        "skipped_no_city": skipped_no_city,
        "skipped_no_settlement": skipped_no_settlement,
        "skipped_no_bins": skipped_no_bins,
    }

    print(f"\nTIGGE ETL complete:")
    print(f"  Vectors processed: {processed}")
    print(f"  Settlements matched: {matched} ({matched*100//max(processed,1)}%)")
    print(f"  Calibration pairs: {pairs_before} → {pairs_after} (+{pairs_after - pairs_before})")
    print(f"  Ensemble snapshots: {snapshots_before} → {snapshots_after}")
    print(f"  Platt models fitted: {fitted}")

    return report


def _get_bins(conn, city: str, target_date: str) -> list[tuple]:
    """Get (label, low, high) tuples from market_events. Try city aliases."""
    # City name aliases (market_events uses LA/SF, settlements use full names)
    aliases = {"Los Angeles": ["LA", "Los Angeles"], "San Francisco": ["SF", "San Francisco"]}
    names_to_try = aliases.get(city, [city])

    for name in names_to_try:
        rows = conn.execute("""
            SELECT range_label FROM market_events
            WHERE city = ? AND target_date = ?
        """, (name, target_date)).fetchall()
        if rows:
            bins = []
            for r in rows:
                label = r["range_label"]
                low, high = _parse_temp_range(label)
                if low is None and high is None:
                    continue
                sort_key = low if low is not None else (high - 1000 if high is not None else -2000)
                bins.append((label, low, high, sort_key))
            bins.sort(key=lambda x: x[3])
            return [(b[0], b[1], b[2]) for b in bins]

    return []


def _synthesize_bins(values: np.ndarray, unit: str) -> list[tuple]:
    """Synthesize standard 11-bin structure from ENS member distribution.

    Used when no market_events exist for this city-date.
    US (°F): 2°F wide bins. Europe (°C): 1°C wide bins.
    """
    median = int(np.round(np.median(values)))
    width = 1 if unit == "C" else 2
    n_center = 9  # 9 center bins + 2 shoulders = 11

    # Center bins around median
    half = n_center // 2
    start = median - half * width

    bins = []
    # Shoulder low
    low_bound = start
    label = f"{low_bound}{'°C' if unit == 'C' else '°F'} or below"
    bins.append((label, None, float(low_bound - 1)))

    # Center bins
    for i in range(n_center):
        lo = start + i * width
        hi = lo + width - 1
        if unit == "C":
            label = f"{lo}°C" if width == 1 else f"{lo}-{hi}°C"
        else:
            label = f"{lo}-{hi}°F"
        bins.append((label, float(lo), float(hi)))

    # Shoulder high
    high_bound = start + n_center * width
    label = f"{high_bound}{'°C' if unit == 'C' else '°F'} or higher"
    bins.append((label, float(high_bound), None))

    return bins


def _temp_in_bin(winning_range: str, low, high) -> bool:
    """Check if the settlement winning temperature falls in this bin.

    winning_range: "39-40", "-999-30", "51-999"
    Uses the midpoint of the winning range as the settlement temperature.
    """
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

    # Settlement temperature is approximately the midpoint of the winning range
    # For shoulder bins: use the boundary value
    if w_low <= -998:
        win_temp = w_high  # "below X" → X is the boundary
    elif w_high >= 998:
        win_temp = w_low  # "above X" → X is the boundary
    else:
        win_temp = (w_low + w_high) / 2.0

    # Check if this temperature falls in our bin
    if low is None and high is not None:
        return win_temp <= high
    elif high is None and low is not None:
        return win_temp >= low
    elif low is not None and high is not None:
        return low <= win_temp <= high
    return False


def _bin_matches_winning(label, low, high, winning_range: str) -> bool:
    """Check if a bin matches the winning_range from settlements."""
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

    if low is None and w_low <= -998:
        return high is not None and abs(w_high - high) < 1.0
    elif high is None and w_high >= 998:
        return low is not None and abs(w_low - low) < 1.0
    elif low is not None and high is not None:
        return abs(w_low - low) < 1.0 and abs(w_high - high) < 1.0
    return False


if __name__ == "__main__":
    run_etl()
