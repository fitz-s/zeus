"""Sampling-effect control: re-score a high-cadence candidate using ONLY readings stamped at the resolver's own
observation minutes (the METAR/WRH grid in WORLD: EFHK/EDDM :20,:50 ; WSSS :00,:30 ; CYYZ/EPWA hourly :00).

Why: settlements since 2026-08-24 come from the NOAA WRH timeseries (half-hourly integer-C METAR). A 10-minute or
1-minute source has more chances to see a transient peak/trough, so its day max is >= and its day min is <= the
half-hourly truth even with identical instruments. This script removes that sampling gap: any disagreement that
remains at resolver instants is an instrument/rounding/identity difference, not a cadence effect.
Coverage rule is unchanged (>= 80% of the 48 half-hourly slots, 24 for hourly grids).
Uses raw files already fetched by the per-provider scripts; makes no network call.
"""
from dae_common import LAST_SETTLED, compare, day_extremes, first_settled, read_raw, save_candidate, settled

SPECS = [  # name, raw file, city, tz, minutes on the resolver grid, slots per hour on that grid
    ("fmi_efhk_at_resolver_instants", "fmi_efhk", "Helsinki", "Europe/Helsinki", {20, 50}, 2),
    ("dwd_eddm_at_resolver_instants", "dwd_eddm", "Munich", "Europe/Berlin", {20, 50}, 2),
    ("nea_s24_at_resolver_instants", "nea_s24_wsss", "Singapore", "Asia/Singapore", {0, 30}, 2),
]


def main():
    for name, raw, city, tz, minutes, per_hour in SPECS:
        try:
            pts = [(t, v) for t, v in read_raw(raw) if t.minute in minutes and t.second == 0]
        except FileNotFoundError:
            print(name, "raw missing; skipped")
            continue
        first = first_settled(city)
        days = day_extremes(pts, tz, 3600 // per_hour, first)
        truth = settled(city)
        res = {"daily": days}
        for metric in ("high", "low"):
            rows, agg = compare(days, truth, metric)
            res[metric] = dict(agg=agg, rows=rows)
            print(f"{name} {metric}: n={agg['n_days']} dropped={agg['dropped_coverage']} eq={agg['eq']} "
                  f"{agg['dangerous_name']}={agg['dangerous']} {agg['opposite_name']}={agg['opposite']} hist={agg['diff_hist']}")
        save_candidate(name, dict(source_raw=raw, city=city, tz=tz, minutes=sorted(minutes), expected_per_day=24 * per_hour,
                                  first_day=first, last_day=LAST_SETTLED, rounding="floor(v+0.5)",
                                  note="control for cadence: readings restricted to resolver observation minutes"), res)


if __name__ == "__main__":
    main()
