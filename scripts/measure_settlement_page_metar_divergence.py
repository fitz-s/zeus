# Created: 2026-09-17
# Purpose: Offline measurement: NOAA settlement PAGE vs METAR mirror divergence per city ->
#   config/wu_metar_divergence.json (the same artifact, measured against the source that now
#   settles). Read-only against the live DBs (mode=ro); writes the config artifact only.
# Authority basis: operator directive 2026-09-17 — "确保系统切换到新的结算逻辑后优势始终存在
#   并且比wu时代更加精准". The settlement product for the 48 NOAA cities became the WRH page at
#   4f48d461e; the margin artifact still carries the WU-era measurement.
"""Measure NOAA-settlement-page vs METAR-mirror divergence on the settlement grid.

Why this exists alongside ``measure_wu_metar_divergence.py``
-----------------------------------------------------------
That script measures WU's METAR mirror against IEM's METAR archive. Both are mirrors of the
same METAR feed, so they agree byte-for-byte: it reported ``p99_abs_rounded_delta = 0.0`` and
``settlement_faithful = true`` for 49 of 50 cities, which is why 47 of 54 cities currently
admit a raw METAR reading into the Day0 belief with a margin of exactly 0.0.

That measurement was correct for the WU era, when a METAR-derived daily extreme WAS the
settlement value. It is no longer the right pair. Since 4f48d461e the settlement product is the
NOAA WRH page, and page-vs-mirror is a genuinely different measurement: the page publishes the
station's own daily summary, the mirror publishes hourly reports we aggregate ourselves, and
the two round to different settlement integers often enough to matter.

This script measures THAT pair, with the same formula, the same thresholds, and the same output
schema, so the artifact's consumers (``day0_oracle_anomaly.metar_margin_units_for_city``, the
hard-fact exit lane) need no change at all — only correct numbers.

Method
------
- Both sides come from ``observations`` in the forecasts DB, which is where daily settlement
  truth lives (the ``observation_instants`` tables are a different feed and a different lane;
  comparing across them compares different products).
- Page side: ``source LIKE 'noaa_wrh_%'`` — the settlement product.
- Mirror side: ``source LIKE 'ogimet_metar_%'`` — the METAR daily aggregate.
- A pair is one (city, target_date, metric) where both sides published in the same unit.
- Deltas are taken AFTER WMO half-up on both sides, because a settlement value is an integer:
  the quantity that matters is whether the two sources name the same settlement integer, not
  whether they agree to a tenth.
- Per city and per metric the script reports the same fields ``city_stats`` reports, and the
  city verdict is the WORST of its two metrics — a station that settles HIGH faithfully and
  LOW unfaithfully is not a faithful station for a belief that trades both.

Usage:
  PYTHONSAFEPATH=1 PYTHONPATH=. .venv/bin/python \\
      scripts/measure_settlement_page_metar_divergence.py \\
      [--since 2026-08-23] [--out config/wu_metar_divergence.json] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc

# Identical to measure_wu_metar_divergence.py — one margin mechanism, one formula.
QUANTUM = 1.0
FLOOR = 1.0
FAITHFUL_P99_MAX = 1.0
FAITHFUL_RATE_MAX = 0.02
#: A city needs this many matched pairs before its threshold is executable evidence. The
#: consumer refuses any threshold whose provenance is not "empirical", and the WU-era refitter
#: set that bar at 100 pairs — reachable in 7 days there because it matched PER HOURLY REPORT
#: (median 179 pairs per city from a 7-day window). The settlement page publishes ONE value per
#: city-day, so 100 pairs is 50 calendar days of page history, and the page only began
#: 2026-08-23. Keeping 100 is therefore the honest choice and it has a consequence worth stating
#: plainly rather than tuning away: until the page has 50 days, EVERY city reads as thin_sample
#: and METAR is excluded from the Day0 belief entirely.
#:
#: That consequence is not symmetric, and the asymmetry is the point (see --verdict output):
#: at n=50 a Wilson 95 % lower bound on the observed disagreement rate already EXCEEDS the
#: 2 % faithfulness ceiling for the 11 worst cities (Denver 0.385, SF 0.312, Chicago 0.294,
#: Houston 0.276, NYC 0.259, LA 0.241, Austin 0.191, Atlanta 0.175, Dallas, Seattle, Miami
#: 0.143), so the sample is ALREADY decisive that those cities are not settlement-faithful.
#: The same n cannot prove the converse: a city observing zero disagreements in 50 pairs has a
#: Wilson lower bound of 0.0 and has proven nothing about its faithfulness. Evidence can revoke
#: a permission at this sample size; it cannot grant one.
EMPIRICAL_MIN_PAIRS = 100
#: Confidence level for the revocation test above. Same 95 % basis as the OOF_WILSON_95 bounds
#: used elsewhere in the decision path.
REVOCATION_WILSON_Z = 1.959963984540054


def wilson_lower_bound(successes: int, trials: int) -> float:
    """Wilson 95 % lower bound on a proportion."""
    if trials <= 0 or successes <= 0:
        # Zero observed disagreements bound at exactly zero. Computing it through the Wilson
        # expression instead leaves float residue (~7e-18 at n=50), which is not a lower bound
        # on anything and would read as a non-zero rate to anyone comparing against 0.
        return 0.0
    z = REVOCATION_WILSON_Z
    p = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (p + z * z / (2.0 * trials)) / denominator
    margin = (
        z * math.sqrt(p * (1.0 - p) / trials + z * z / (4.0 * trials * trials))
    ) / denominator
    lower = centre - margin
    return 0.0 if not math.isfinite(lower) or lower <= 0.0 else lower


def wmo_half_up(value: float) -> float:
    """The settlement rounding law: floor(x + 0.5), correct for negative halves."""
    return float(math.floor(float(value) + 0.5))


def _percentile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    return round(sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))], 3)


def city_stats(matched: list[tuple[float, float]]) -> dict:
    """Same statistics and threshold formula as the WU-era refitter.

    ``matched`` is a list of (page_value, mirror_value) pairs in the city's settlement unit.
    """
    raw = [page - mirror for page, mirror in matched]
    rounded = [wmo_half_up(page) - wmo_half_up(mirror) for page, mirror in matched]
    abs_raw = sorted(abs(d) for d in raw)
    abs_rounded = sorted(abs(d) for d in rounded)
    n = len(matched)
    disagree = sum(1 for d in abs_rounded if d >= 1.0)
    p99_rounded = _percentile(abs_rounded, 0.99) if n else None
    rate = round(disagree / n, 5) if n else None
    threshold = max((p99_rounded or 0.0) + QUANTUM, FLOOR) if n else None
    faithful = (
        bool(
            p99_rounded is not None
            and p99_rounded <= FAITHFUL_P99_MAX
            and rate is not None
            and rate <= FAITHFUL_RATE_MAX
        )
        if n
        else None
    )
    return {
        "matched_pairs": n,
        "median_abs_raw_delta": round(statistics.median(abs_raw), 3) if n else None,
        "p95_abs_raw_delta": _percentile(abs_raw, 0.95),
        "p99_abs_raw_delta": _percentile(abs_raw, 0.99),
        "max_abs_raw_delta": round(abs_raw[-1], 3) if n else None,
        "p95_abs_rounded_delta": _percentile(abs_rounded, 0.95),
        "p99_abs_rounded_delta": p99_rounded,
        "max_abs_rounded_delta": round(abs_rounded[-1], 3) if n else None,
        "disagree_rate_ge_1unit": rate,
        "empirical_threshold": round(threshold, 2) if threshold is not None else None,
        "threshold_provenance": (
            "empirical" if n >= EMPIRICAL_MIN_PAIRS else ("thin_sample" if n else "no_data")
        ),
        "settlement_faithful": faithful,
        # Decisive-against-faithfulness even at a thin sample: the disagreement rate's own
        # 95 % lower bound clears the faithfulness ceiling, so no amount of further data can
        # make this city faithful at the observed rate. Reported separately from
        # settlement_faithful because it answers a different question — that field states what
        # the full-sample verdict WOULD be, this one states what the current sample already
        # PROVES. Consumers ignore this key; it exists for the operator's decision.
        "unfaithful_proven_at_95": (
            bool(wilson_lower_bound(disagree, n) > FAITHFUL_RATE_MAX) if n else None
        ),
        "disagree_rate_wilson_lower_95": (
            round(wilson_lower_bound(disagree, n), 5) if n else None
        ),
    }


def collect_pairs(forecasts_db: Path, *, since: str) -> dict[str, list[tuple[float, float]]]:
    """One (page, mirror) pair per city/date/metric, in the city's settlement unit."""
    conn = sqlite3.connect(f"file:{forecasts_db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """
            SELECT city, target_date, source, high_temp, low_temp, unit
              FROM observations
             WHERE (source LIKE 'noaa_wrh_%' OR source LIKE 'ogimet_metar_%')
               AND target_date >= ?
            """,
            (since,),
        ).fetchall()
    finally:
        conn.close()

    page: dict[tuple[str, str], tuple[object, object, str]] = {}
    mirror: dict[tuple[str, str], tuple[object, object, str]] = {}
    for city, target_date, source, high, low, unit in rows:
        table = page if str(source).startswith("noaa_wrh_") else mirror
        table[(str(city), str(target_date))] = (high, low, str(unit or "").strip().upper())

    pairs: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for key, (page_high, page_low, page_unit) in page.items():
        other = mirror.get(key)
        if other is None:
            continue
        mirror_high, mirror_low, mirror_unit = other
        if not page_unit or page_unit != mirror_unit:
            # A unit mismatch is a different product, not a divergence. Skipping keeps the
            # measurement about the feeds rather than about a conversion.
            continue
        for page_value, mirror_value in ((page_high, mirror_high), (page_low, mirror_low)):
            if page_value is None or mirror_value is None:
                continue
            pairs[key[0]].append((float(page_value), float(mirror_value)))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2026-08-23", help="earliest target_date to match")
    parser.add_argument("--out", default="config/wu_metar_divergence.json")
    parser.add_argument(
        "--forecasts-db",
        default=str(REPO_ROOT / "state" / "zeus-forecasts.db"),
        help="read-only path to the forecasts DB holding daily observations",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the artifact and the margin each city would serve; write nothing",
    )
    args = parser.parse_args()

    pairs = collect_pairs(Path(args.forecasts_db), since=args.since)
    if not pairs:
        raise SystemExit(
            "no matched page/mirror settlement days — nothing to measure; "
            "check --since against the page backfill window"
        )

    results = {city: city_stats(matched) for city, matched in sorted(pairs.items())}
    total = sum(s["matched_pairs"] for s in results.values())
    artifact = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window_since": args.since,
        "method": (
            "same-city same-target_date NOAA WRH settlement page vs Ogimet METAR daily "
            "aggregate, both from observations in the forecasts DB; rounded_delta after "
            "WMO half-up on BOTH sides (a settlement value is an integer)"
        ),
        "threshold_formula": (
            "max(p99(|rounded_delta|) + 1.0, 1.0) per settlement unit — unchanged from the "
            "WU-era refitter"
        ),
        "window_note": (
            "Supersedes the WU-vs-IEM measurement, which compared two mirrors of the same "
            "METAR feed and therefore reported byte-identical agreement. The settlement "
            "product has been the WRH page since 4f48d461e."
        ),
        "cities": results,
    }

    print(f"matched settlement days: {total} across {len(results)} cities\n")
    proven = [c for c, s_ in results.items() if s_.get("unfaithful_proven_at_95")]
    print(
        f"cities PROVEN not settlement-faithful at 95 % even on this sample: "
        f"{len(proven)} of {len(results)}"
    )
    print(f"  {', '.join(sorted(proven))}\n" if proven else "")
    print(
        f"{'city':18s} {'pairs':>6s} {'disagree':>9s} {'wilson_lo':>10s} {'p99':>5s} "
        f"{'threshold':>10s} {'faithful':>9s} {'served margin':>14s}"
    )
    for city, s in sorted(results.items(), key=lambda kv: -(kv[1]["disagree_rate_ge_1unit"] or 0)):
        # Reproduce the consumer's decision so the operator sees the margin, not just the fit.
        if s["threshold_provenance"] != "empirical":
            served = "None (excluded)"
        elif s["settlement_faithful"] and (s["empirical_threshold"] or 0.0) <= 1.0:
            served = "0.0"
        else:
            served = str(s["empirical_threshold"])
        print(
            f"{city[:18]:18s} {s['matched_pairs']:6d} "
            f"{s['disagree_rate_ge_1unit'] or 0:9.4f} "
            f"{s['disagree_rate_wilson_lower_95'] or 0:10.4f} "
            f"{s['p99_abs_rounded_delta']:5} "
            f"{s['empirical_threshold']:10} {str(s['settlement_faithful']):>9s} {served:>14s}"
        )

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return
    out = Path(args.out)
    if not out.is_absolute():
        out = REPO_ROOT / out
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
