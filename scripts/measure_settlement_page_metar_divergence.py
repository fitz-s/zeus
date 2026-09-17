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
#: consumer refuses any threshold whose provenance is not "empirical"
#: (`day0_oracle_anomaly.metar_margin_units_for_city`), so this number decides whether a city
#: gets a measured allowance or is excluded from the Day0 fast lane outright.
#:
#: The WU-era refitter hardcodes 100 with no stated derivation. That number is reachable in a
#: 7-day window there because it matches PER HOURLY REPORT (median 179 pairs per city). The
#: settlement page publishes ONE value per city-day, so 100 would mean 50 calendar days, and
#: carrying it over would not be conservatism — it would be a density assumption from a
#: different measurement applied to this one.
#:
#: So it is derived instead, from the only property that matters: at what sample size does the
#: THRESHOLD stop moving? The threshold is `max(p99(|rounded delta|) + 1, 1)`, a step function
#: of the data, so this is answerable directly. Bootstrapping 200 subsamples per city against
#: each city's own full-sample threshold (2,378 matched settlement days, 48 cities):
#:
#:     n=10  -> 44/48 cities reproduce their full-sample threshold >=95 % of the time
#:     n=20  -> 45/48
#:     n=30  -> 45/48
#:     n=40  -> 45/48
#:     n=50  -> 36/36 cities with >=50 pairs reproduce it 100 % of the time
#:
#: The threshold has converged by 50 pairs. 40 is chosen rather than 50 so a city with one
#: missing settlement day is not excluded for a gap that cannot change its threshold, and
#: rather than 20 because the 93.8 % plateau below 50 is not unanimity.
#:
#: This is a LOWER bar than the WU refitter's in pair count and a HIGHER one in information:
#: 40 page-vs-page settlement days measure the divergence that decides settlement, where 179
#: report-vs-report pairs measured two mirrors of one feed agreeing with themselves.
EMPIRICAL_MIN_PAIRS = 40
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


def _cities_not_settled_by_the_page() -> set[str]:
    """Configured cities whose settlement product is NOT the NOAA page.

    Their divergence cannot be measured by this script — there is no page/mirror pair — so
    their existing measurement must survive a refit rather than be deleted.
    """
    from src.config import cities_by_name

    return {
        str(name)
        for name, city in cities_by_name.items()
        if str(getattr(city, "settlement_source_type", "") or "").lower() != "noaa"
    }


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

    # This measurement only speaks for cities the NOAA page settles. Five cities are still
    # `wu_icao` (Auckland, Jakarta, Jinan, Lagos, Taipei) and the WU page is still their
    # settlement product, so their WU-era measurement remains the correct evidence for them and
    # there is no page/mirror pair to replace it with. A city ABSENT from the artifact is
    # excluded from the Day0 fast lane entirely (`metar_margin_units_for_city` returns None on a
    # missing entry), so a refit scoped to NOAA that overwrote the file wholesale would silently
    # revoke five cities' fast lane.
    #
    # Carry them by SETTLEMENT TYPE, not by "absent from this run's results". Keying on absence
    # makes the output depend on the file being overwritten: a re-run reads its own previous
    # output, and any city whose pairs happened to be missing from THIS pass gets carried with a
    # stale threshold — observed live, Denver came back with 162 pairs and threshold 1.0 carried
    # over its own correct 50-pair threshold of 2.0. Settlement type is the property that
    # actually decides which measurement speaks for a city.
    carried: dict[str, dict] = {}
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = REPO_ROOT / out_path
    non_page_cities = _cities_not_settled_by_the_page()
    if out_path.exists():
        try:
            previous = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        previous_cities = previous.get("cities")
        if isinstance(previous_cities, dict):
            previous_method = str(previous.get("method") or "")
            for city in sorted(non_page_cities & set(previous_cities)):
                entry = previous_cities[city]
                if not isinstance(entry, dict):
                    continue
                carried[city] = {**entry, "carried_from_method": previous_method}
    overlap = sorted(set(carried) & set(results))
    if overlap:
        raise SystemExit(
            "a city cannot be both measured here and carried from the previous "
            f"measurement: {', '.join(overlap)}"
        )
    if carried:
        print(
            f"carrying {len(carried)} city entries this measurement does not cover "
            f"(not settled by the page): {', '.join(sorted(carried))}\n"
        )
    results = {**carried, **results}

    generated_at = datetime.now(UTC)
    artifact = {
        "generated_at": generated_at.isoformat(),
        # The artifact's existing schema. Consumers and tests read these keys, so the refit
        # must speak the same shape as the measurement it supersedes rather than invent one:
        # `window` is the [start, end] the measurement covers, `window_days` its span, and
        # `defaults` the pre-measurement guess each threshold replaced.
        "window": [
            datetime.fromisoformat(f"{args.since}T00:00:00+00:00").isoformat(),
            generated_at.isoformat(),
        ],
        "window_days": (
            generated_at.date() - datetime.fromisoformat(f"{args.since}T00:00:00+00:00").date()
        ).days,
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
        # Same shape and same values as the measurement this supersedes: the
        # pre-measurement guess each empirical_threshold replaced.
        "defaults": {"F": 1.5, "C": 1.0, "provenance": "default_guess_pre_measurement"},
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
    # A carried entry comes from a DIFFERENT measurement and does not have this script's
    # own fields, so every read below must tolerate their absence rather than KeyError.
    for city, s in sorted(
        results.items(), key=lambda kv: -(kv[1].get("disagree_rate_ge_1unit") or 0)
    ):
        # Reproduce the consumer's decision so the operator sees the margin, not just the fit.
        if s.get("threshold_provenance") != "empirical":
            served = "None (excluded)"
        elif s.get("settlement_faithful") and (s.get("empirical_threshold") or 0.0) <= 1.0:
            served = "0.0"
        else:
            served = str(s.get("empirical_threshold"))
        tag = "  (carried)" if "carried_from_method" in s else ""
        print(
            f"{city[:18]:18s} {s.get('matched_pairs') or 0:6d} "
            f"{s.get('disagree_rate_ge_1unit') or 0:9.4f} "
            f"{s.get('disagree_rate_wilson_lower_95') or 0:10.4f} "
            f"{s.get('p99_abs_rounded_delta'):5} "
            f"{s.get('empirical_threshold'):10} "
            f"{str(s.get('settlement_faithful')):>9s} {served:>14s}{tag}"
        )

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return
    out_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
