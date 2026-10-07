"""A0 / A / B score table and per-city verdicts, recomputed from per_city_ss/<city>_decisions.csv.gz (no inference).

Source-incompatible days: the settled value is impossible given the METAR/SPECI record already received (at the day's
last decision the received-boundary model gives the truth probability < 1e-3 in arm A). These are days where the
settlement page dropped rows the METAR feed carried (e.g. Singapore 2026-09-20: METARs show 32 at 5 instants absent from
the WRH capture; settled 31). No observation model can score them; they are reported and excluded from the gate.

Gate per city and metric, on NOAA-era test days minus source-incompatible days:
  B_USABLE  iff  (1) mean log-loss B < A with the day-blocked 95 % upper bound of (B - A) < 0;
                 (2) zero semantic violations in A and B (mass on bins the received METAR extreme already excludes);
                 (3) high-confidence calibration: B's errors among decisions with top probability >= 0.95 do not exceed
                     the one-sided 95 % Poisson bound of B's own expected error count sum(1 - q).
  STRICT = the operator's literal rule on the same days: B's false-certainty rate (top >= 0.95 and wrong) <= A's.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

import run_state_space as rss

HERE = Path(__file__).resolve().parent
D = HERE / "per_city_ss"
ORDER = ("Helsinki", "Munich", "Tokyo", "Singapore", "Toronto", "Warsaw", "Amsterdam", "Madrid")


def incompatible_days(df):
    a = df[df.arm == "A"].sort_values("t0").groupby(["date", "side"]).last()
    bad = a[a.pt < 1e-3]
    return {(d, s) for d, s in bad.index}


def gate(res, side):
    by, pd_ = res.get("by_arm_side", {}), res.get("paired_day_block", {})
    ba, bb, ci = by.get(f"A|{side}"), by.get(f"B|{side}"), pd_.get(f"{side}|ll|B-A")
    if not (ba and bb and ci):
        return dict(verdict="NO_DATA")
    ll_ok = ci["mean"] < 0 and ci["hi"] < 0
    sem_ok = ba["semantic_violation_decisions"] == 0 and bb["semantic_violation_decisions"] == 0
    hc = bb["hc95"]
    bound = int(stats.poisson.ppf(0.95, max(hc["expected_errors"], 1e-9)))
    cal_ok = hc["errors"] <= bound
    strict = bb["false_certainty_rate_all"] <= ba["false_certainty_rate_all"]
    v = ("B_USABLE" if ll_ok and sem_ok and cal_ok else
         "B_BETTER_NOT_SIGNIFICANT" if ci["mean"] < 0 and sem_ok and cal_ok else "B_NOT_USABLE")
    return dict(verdict=v, strict="PASS" if strict else "FAIL", ll_ok=ll_ok, sem_ok=sem_ok, cal_ok=cal_ok, cal_bound=bound, ci=ci,
                hc_A=ba["hc95"], hc_B=hc, fc_A=ba["false_certainty_rate_all"], fc_B=bb["false_certainty_rate_all"])


def f3(*v):
    return " / ".join(f"{x:.3f}" for x in v)


def main():
    head = ["| city | metric | n test days (NOAA, excl. incompatible) | logloss A0 / A / B | Brier A0 / A / B | B−A logloss [95 % day-block] | "
            "lead B−A min p10 / p50 / p90 (n days; B-only / A-only / neither) | false-certainty A / B: rate (errors, Σ(1−q)) | semantic viol. A / B | "
            "verdict (STRICT) |", "|---|---|---|---|---|---|---|---|---|---|"]
    prm = ["| city | train / test days | picked model | τ_slow min | s_slow °C | τ_fast min | s_fast °C | b by 3-h block °C | σ Gauss °C | "
           "dense core / outlier sd, outlier w | drift a, τ_e min | forecast shrink β | lag METAR / dense p50 min |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    oth = ["| city | metric | WU-era test days: n, logloss A0 / A / B, B−A [95 %] | all VERIFIED test days: n, B−A [95 %] | "
           "approximation arms on NOAA days: B_tn−A_tn, B_g12−A_g12, A_tn−A, A_g12−A (mean logloss) | source-incompatible test days |",
           "|---|---|---|---|---|---|"]
    verdicts = {}
    for city in ORDER:
        p = D / f"{city.lower()}.json"
        if not p.exists():
            continue
        r = json.loads(p.read_text())
        if "skipped" in r:
            head.append(f"| {city} | – | – | – | – | – | – | – | – | SKIPPED: {r['skipped']} |")
            verdicts[city] = dict(skipped=r["skipped"])
            continue
        df = pd.read_csv(D / f"{city.lower()}_decisions.csv.gz")
        bad = incompatible_days(df)
        keep = ~df.apply(lambda x: (x.date, x.side) in bad, axis=1)
        noaa = rss.summarize(df[(df.era == "noaa") & keep], "NOAA-era test days excl. source-incompatible")
        wu = rss.summarize(df[(df.era == "wu") & keep], "WU-era test days excl. source-incompatible")
        alld = rss.summarize(df[keep], "all VERIFIED test days excl. source-incompatible")
        q = r["params"]
        lu = q["latent_used"]
        tau_s = "∞ (day-level offset)" if lu["tau_s"] > 1e5 else f"{lu['tau_s']:.0f}"
        prm.append(f"| {city} | {r['split']['train_days']} / {r['split']['test_days']} | {q['selection']['picked']} | {tau_s} | "
                   f"{lu['s_s']:.2f} | {lu['tau_f']:.0f} | {lu['s_f']:.2f} | {', '.join(f'{v:+.2f}' for v in q['b_block_3h'])} | "
                   f"{q['sigma_gauss']:.3f} | {q['dense_white_core_sd']:.3f} / {q['dense_white_outlier_sd']:.3f}, {q['dense_outlier_weight']:.2f} | "
                   f"{q['drift']['a']:.2f}, {q['drift']['tau_e']:.0f} | {q['mean']['beta']:+.2f} | {r['lags_min']['metar_awc_p50']:.1f} / "
                   f"{r['lags_min']['dense_p50']:.1f} |")
        verdicts[city] = dict(source_incompatible=sorted(f"{d} {s}" for d, s in bad))
        for side in ("high", "low"):
            g = gate(noaa, side)
            verdicts[city][side] = g
            if g["verdict"] == "NO_DATA":
                head.append(f"| {city} | {side} | 0 | – | – | – | – | – | – | NO_DATA |")
                continue
            by = noaa["by_arm_side"]
            a0, a, b = by[f"A0|{side}"], by[f"A|{side}"], by[f"B|{side}"]
            ld = noaa["lead_B_minus_A_min"][side]
            dd = ld.get("days", {})
            lead = (f"{ld.get('p10', '–')} / {ld.get('p50', '–')} / {ld.get('p90', '–')} ({ld.get('n', 0)}; {dd.get('only_B', 0)} / "
                    f"{dd.get('only_A', 0)} / {dd.get('neither', 0)})")
            fc = (f"{a['false_certainty_rate_all']:.4f} ({a['hc95']['errors']}, {a['hc95']['expected_errors']:.1f}) / "
                  f"{b['false_certainty_rate_all']:.4f} ({b['hc95']['errors']}, {b['hc95']['expected_errors']:.1f})")
            ci = g["ci"]
            head.append(f"| {city} | {side} | {a['n_days']} | {f3(a0['logloss'], a['logloss'], b['logloss'])} | "
                        f"{f3(a0['brier'], a['brier'], b['brier'])} | {ci['mean']:+.4f} [{ci['lo']:+.4f}, {ci['hi']:+.4f}] | {lead} | {fc} | "
                        f"{a['semantic_violation_decisions']} / {b['semantic_violation_decisions']} | {g['verdict']} ({g['strict']}) |")
            wb = wu.get("by_arm_side", {})
            wci = wu.get("paired_day_block", {}).get(f"{side}|ll|B-A")
            wtxt = (f"{wb[f'A|{side}']['n_days']}, {f3(wb[f'A0|{side}']['logloss'], wb[f'A|{side}']['logloss'], wb[f'B|{side}']['logloss'])}, "
                    f"{wci['mean']:+.4f} [{wci['lo']:+.4f}, {wci['hi']:+.4f}]") if wci else "–"
            aci = alld["paired_day_block"].get(f"{side}|ll|B-A")
            atxt = f"{aci['days']}, {aci['mean']:+.4f} [{aci['lo']:+.4f}, {aci['hi']:+.4f}]" if aci else "–"
            pdn = noaa["paired_day_block"]
            ap = ", ".join(f"{pdn[k]['mean']:+.4f}" if k in pdn else "–" for k in
                           (f"{side}|ll|B_tn-A_tn", f"{side}|ll|B_g12-A_g12", f"{side}|ll|A_tn-A", f"{side}|ll|A_g12-A"))
            inc = ", ".join(d for d, s in sorted(bad) if s == side) or "none"
            oth.append(f"| {city} | {side} | {wtxt} | {atxt} | {ap} | {inc} |")
        r["results_gate"] = dict(noaa_excl_incompatible=noaa, wu_excl_incompatible=wu, all_excl_incompatible=alld,
                                 source_incompatible=sorted(f"{d} {s}" for d, s in bad))
        r["verdict_gate"] = {s: verdicts[city][s] for s in ("high", "low")}
        p.write_text(json.dumps(r, indent=1, default=str))
        # mirror the state-space summary into the items-1-4 per-city JSON
        pc = HERE / "per_city" / f"{city.lower()}.json"
        if pc.exists():
            j = json.loads(pc.read_text())
            j["state_space"] = dict(params=q, split=r["split"], lags_min=r["lags_min"], verdict=r["verdict_gate"],
                                    source_incompatible=r["results_gate"]["source_incompatible"],
                                    detail="per_city_ss/" + city.lower() + ".json")
            pc.write_text(json.dumps(j, indent=1, default=str))
    out = "\n".join(head) + "\n\n" + "\n".join(prm) + "\n\n" + "\n".join(oth) + "\n"
    (HERE / "ss_summary_table.md").write_text(out)
    (HERE / "ss_verdicts.json").write_text(json.dumps(verdicts, indent=1, default=str))
    print(out)


if __name__ == "__main__":
    main()
