"""Print the summary tables (MAIN, VERDICTS, LIVE, CONTROLS, AWC) as markdown from per_candidate/*.json.
No network, no DB. Every number is read from the data files, none typed by hand.
Usage: python3 build_summary.py > tables.md
VERDICTS applies the stated rule: eq/n >= 95 % and dangerous == 0 over >= 30 days, and live reveal-lead p50 > 0.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
P = HERE / "per_candidate"
ERAS = json.load(open(P / "_truth_eras.json"))

# main table rows: (label, source type, file stem, city, live-lead stem or None)
MAIN = [
    ("Helsinki EFHK - FMI WFS 10-min (fmi_airport_temperature)", "archive 10 min", "fmi_efhk", "Helsinki", None),
    ("Munich EDDM - DWD CDC TT_10 10-min (dwd_cdc_temperature)", "archive 10 min", "dwd_eddm", "Munich", None),
    ("Warsaw EPWA - IMGW synop hourly (imgw_synop_temperature)", "archive hourly", "imgw_epwa", "Warsaw", None),
    ("Amsterdam EHAM - KNMI 10-min files, Tx12/Tn12 true extreme", "archive extreme", "knmi_eham", "Amsterdam", None),
    ("London EGLC - Met Office gcpvj0 hourly spot, 48 h window", "api 48 h", "metoffice_london_gcpvj0", "London", None),
    ("Tokyo RJTT - JMA daily table Haneda 44166", "archive daily", "jma_haneda_daily", "Tokyo", None),
    ("Tokyo RJTT - JMA AMeDAS 10-min files (live endpoint, 10 d)", "live endpoint 10 min", "jma_amedas_44166_10min", "Tokyo", None),
    ("Toronto CYYZ - ECCC hourly archive (METAR-grade)", "archive hourly", "eccc_cyyz_hourly", "Toronto", None),
    ("Toronto CYYZ - ECCC daily Max/Min (LST day)", "archive daily", "eccc_cyyz_daily", "Toronto", None),
    ("Madrid LEMD - AEMET public XML diario (7 d)", "web 7 d", "aemet_lemd", "Madrid", None),
    ("Singapore WSSS - NEA S24 1-min (data.gov.sg)", "archive 1 min", "nea_s24_wsss", "Singapore", None),
]
LIVE_ONLY = [  # every live WORLD channel scored on its own live window, with its own reveal lead
    ("Helsinki EFHK - fmi_airport_temperature (live window)", "world_fmi_airport_temperature_EFHK", "Helsinki"),
    ("Munich EDDM - dwd_cdc_temperature (live window)", "world_dwd_cdc_temperature_EDDM", "Munich"),
    ("Warsaw EPWA - imgw_synop_temperature (live window)", "world_imgw_synop_temperature_EPWA", "Warsaw"),
    ("Tokyo RJTT - jma_amedas_temperature (live window)", "world_jma_amedas_temperature_RJTT", "Tokyo"),
    ("Toronto CYYZ - eccc_swob_temperature (live window)", "world_eccc_swob_temperature_CYYZ", "Toronto"),
    ("Lucknow VILK - imd_olbs_metar_temperature (live window)", "world_imd_olbs_metar_temperature_VILK", "Lucknow"),
    ("Ankara LTAC - mgm_metar_temperature (live window)", "world_mgm_metar_temperature_LTAC", "Ankara"),
    ("Istanbul LTFM - mgm_metar_temperature (live window)", "world_mgm_metar_temperature_LTFM", "Istanbul"),
    ("Moscow UUWW - metaviatelecom_metar_temperature (live window)", "world_metaviatelecom_metar_temperature_UUWW", "Moscow"),
]
LIVE_DETAIL = [
    ("Helsinki EFHK fmi_airport_temperature", "world_fmi_airport_temperature_EFHK", "world_awc_baseline_EFHK"),
    ("Munich EDDM dwd_cdc_temperature", "world_dwd_cdc_temperature_EDDM", "world_awc_baseline_EDDM"),
    ("Warsaw EPWA imgw_synop_temperature", "world_imgw_synop_temperature_EPWA", "world_awc_baseline_EPWA"),
    ("Tokyo RJTT jma_amedas_temperature", "world_jma_amedas_temperature_RJTT", "world_awc_baseline_RJTT"),
    ("Toronto CYYZ eccc_swob_temperature", "world_eccc_swob_temperature_CYYZ", "world_awc_baseline_CYYZ"),
    ("Lucknow VILK imd_olbs_metar_temperature", "world_imd_olbs_metar_temperature_VILK", "world_awc_baseline_VILK"),
    ("Ankara LTAC mgm_metar_temperature", "world_mgm_metar_temperature_LTAC", "world_awc_baseline_LTAC"),
    ("Istanbul LTFM mgm_metar_temperature", "world_mgm_metar_temperature_LTFM", "world_awc_baseline_LTFM"),
    ("Moscow UUWW metaviatelecom_metar_temperature", "world_metaviatelecom_metar_temperature_UUWW", None),
]
CONTROLS = [
    ("Helsinki FMI 10-min, only :20/:50 readings", "fmi_efhk_at_resolver_instants"),
    ("Munich DWD TT_10, only :20/:50 readings", "dwd_eddm_at_resolver_instants"),
    ("Munich DWD TX_10/TN_10 (10-min extrema columns)", "dwd_eddm_extrema"),
    ("Singapore NEA S24, only :00/:30 readings", "nea_s24_at_resolver_instants"),
]
AWC = [("Helsinki", "EFHK"), ("Munich", "EDDM"), ("Warsaw", "EPWA"), ("Amsterdam", "EHAM"), ("London", "EGLC"), ("Tokyo", "RJTT"),
       ("Toronto", "CYYZ"), ("Ankara", "LTAC"), ("Istanbul", "LTFM"), ("Lucknow", "VILK"), ("Moscow", "UUWW"), ("Madrid", "LEMD"),
       ("Singapore", "WSSS"), ("Sao Paulo", "SBGR")]


def load(stem):
    f = P / f"{stem}.json"
    return json.load(open(f)) if stem and f.exists() else None


def era(rows, city):
    s = ERAS[city]["wrh_start"]
    u = [r for r in rows if r.get("used") and r["date"] >= s]
    return f"{len(u)} / {sum(r['diff'] == 0 for r in u)} / {sum(bool(r.get('dangerous')) for r in u)}"


def hist(h):
    return " ".join(f"{k}:{v}" for k, v in h.items()) or "-"


def lead(a):
    if not a or not a.get("n"):
        return "-"
    f = lambda x: f"{round(x) + 0:+d}" if round(x) else "0"
    return f"{f(a['p10'])} / {f(a['p50'])} / {f(a['p90'])} (n={a['n']}, earlier {a['earlier']})"


def pct(e, n):
    return f"{100 * e / n:.1f}%" if n else "-"


def cells(label, kind, d, city, live):
    out = []
    for m in ("high", "low"):
        a = d[m]["agg"]
        ld = lead(live[m]["agg"].get("reveal_lead_min_all")) if live and "high" in live else "-"
        out.append(f"| {label} | {kind} | {m} | {a['n_days']} | {a['dropped_coverage']} | {a['eq']} | {pct(a['eq'], a['n_days'])} | "
                   f"**{a['dangerous_name']}={a['dangerous']}** | {a['opposite_name']}={a['opposite']} | {hist(a['diff_hist'])} | "
                   f"{era(d[m]['rows'], city)} | {ld} |")
    return out


def main_table():
    out = ["| Candidate / source | type | metric | n_days | dropped (coverage) | eq | eq % | dangerous side (high: over, low: under) | other side | diff hist (cand - settled) | WRH-era n / eq / dangerous | reveal lead vs AWC, min p10 / p50 / p90 (live window; + = earlier) |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for label, kind, stem, city, live_stem in MAIN:
        d = load(stem)
        if not d:
            out.append(f"| {label} | {kind} | - | **NOT RUN / FETCH FAILED** | | | | | | | | |")
            continue
        out += cells(label, kind, d, city, load(live_stem))
    for label, stem, city in LIVE_ONLY:
        d = load(stem)
        if not d or "high" not in d:
            out.append(f"| {label} | live window | - | no scorable day | | | | | | | | |")
            continue
        out += cells(label, "live window", d, city, d)
    return "\n".join(out)


def live_table():
    out = ["| Live WORLD channel | metric | n_days | dropped | eq | dangerous | other side | diff hist | same-window AWC eq/n (dangerous) | lead all fetches p10/p50/p90 | lead no-backfill p10/p50/p90 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for label, stem, base in LIVE_DETAIL:
        d, b = load(stem), load(base)
        if not d or "high" not in d:
            out.append(f"| {label} | - | no scorable live day (see prose) | | | | | | | | |")
            continue
        for m in ("high", "low"):
            a, ba = d[m]["agg"], (b[m]["agg"] if b else None)
            out.append(f"| {label} | {m} | {a['n_days']} | {a['dropped_coverage']} | {a['eq']} | **{a['dangerous_name']}={a['dangerous']}** | "
                       f"{a['opposite_name']}={a['opposite']} | {hist(a['diff_hist'])} | "
                       f"{(str(ba['eq']) + '/' + str(ba['n_days']) + ' (' + str(ba['dangerous']) + ')') if ba else '-'} | "
                       f"{lead(a.get('reveal_lead_min_all'))} | {lead(a.get('reveal_lead_min_nobackfill'))} |")
    return "\n".join(out)


def control_table():
    out = ["| Control | metric | n_days | dropped | eq | eq % | dangerous | other side | diff hist |", "|---|---|---|---|---|---|---|---|---|"]
    for label, stem in CONTROLS:
        d = load(stem)
        if not d:
            out.append(f"| {label} | - | NOT RUN | | | | | | |")
            continue
        for m in ("high", "low"):
            a = d[m]["agg"]
            out.append(f"| {label} | {m} | {a['n_days']} | {a['dropped_coverage']} | {a['eq']} | {pct(a['eq'], a['n_days'])} | "
                       f"**{a['dangerous_name']}={a['dangerous']}** | {a['opposite_name']}={a['opposite']} | {hist(a['diff_hist'])} |")
    return "\n".join(out)


def awc_table():
    out = ["| City / station | metric | n_days | dropped | eq | dangerous | other side | diff hist | WRH-era n / eq / dangerous | pre-WRH n / eq / dangerous |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for city, stn in AWC:
        d = load(f"awc_metar_{stn}")
        for m in ("high", "low"):
            a = d[m]["agg"]
            w, p = a["wrh_era"], a["pre_wrh_era"]
            out.append(f"| {city} {stn} | {m} | {a['n_days']} | {a['dropped_coverage']} | {a['eq']} | **{a['dangerous_name']}={a['dangerous']}** | "
                       f"{a['opposite_name']}={a['opposite']} | {hist(a['diff_hist'])} | {w['n']} / {w['eq']} / {w['dangerous']} | "
                       f"{p['n']} / {p['eq']} / {p['dangerous']} |")
    return "\n".join(out)


def one_sided_upper(k, n, conf=0.95):
    """Clopper-Pearson one-sided upper bound on a rate with k events in n trials (exact binomial)."""
    from math import comb
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        cdf = sum(comb(n, i) * mid ** i * (1 - mid) ** (n - i) for i in range(k + 1))
        lo, hi = (mid, hi) if cdf > 1 - conf else (lo, mid)
    return hi


def verdicts():
    """Interpretation rule: eq/n >= 95 % AND dangerous == 0 over >= 30 days AND live reveal lead > 0 (p50 of all-fetch lead)."""
    out = ["| Candidate | metric | all VERIFIED days: n / eq% / dangerous | WRH-era only: n / eq% / dangerous | live lead p50 (min) | 95 % upper bound on dangerous rate (all days) | verdict |",
           "|---|---|---|---|---|---|---|"]
    rows = [(l, s, c, ls) for l, _k, s, c, ls in MAIN] + [(l, s, c, s) for l, s, c in LIVE_ONLY]
    for label, stem, city, live_stem in rows:
        d = load(stem)
        if not d:
            out.append(f"| {label} | - | NOT RUN | | | | FAIL (no data) |")
            continue
        lv = load(live_stem)
        for m in ("high", "low"):
            a = d[m]["agg"]
            n, eq, dg = a["n_days"], a["eq"], a["dangerous"]
            wn, we, wd = [int(x) for x in era(d[m]["rows"], city).split(" / ")]
            ld = (lv[m]["agg"].get("reveal_lead_min_all") or {}) if lv and m in lv else {}
            p50 = ld.get("p50") if ld.get("n") else None
            ok_all = n >= 30 and eq / max(n, 1) >= 0.95 and dg == 0
            ok_wrh = wn >= 30 and we / max(wn, 1) >= 0.95 and wd == 0
            if p50 is None:
                lead_txt, lead_ok = "n/a (no live prints)", None
            else:
                lead_txt, lead_ok = f"{p50:+.0f}", p50 > 0
            if ok_wrh and lead_ok:
                v = "PASS (WRH era)" if not ok_all else "PASS"
            elif ok_wrh and lead_ok is None:
                v = "accuracy PASS (WRH era), lead unmeasured"
            elif ok_wrh:
                v = "accuracy PASS (WRH era), lead NOT positive"
            else:
                v = "FAIL" if (n >= 30 or wn >= 30) else f"INSUFFICIENT n ({n} d)"
            ub = f"{100 * one_sided_upper(dg, n):.1f}%" if n else "-"
            out.append(f"| {label} | {m} | {n} / {pct(eq, n)} / {dg} | {wn} / {pct(we, wn)} / {wd} | {lead_txt} | {ub} | {v} |")
    return "\n".join(out)


def main():
    """Print the markdown tables to stdout (generated from per_candidate/*.json; nothing typed by hand)."""
    for key, fn in (("MAIN", main_table), ("VERDICTS", verdicts), ("LIVE", live_table), ("CONTROLS", control_table), ("AWC", awc_table)):
        print(f"\n### {key}\n")
        print(fn())


if __name__ == "__main__":
    main()
