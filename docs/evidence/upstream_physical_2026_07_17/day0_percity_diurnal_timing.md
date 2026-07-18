# Per-city empirical diurnal extreme-timing — and a bin-parser bug that overturns §6

Date: 2026-07-18
Scope: replace §6's fixed diurnal windows (HIGH peak assumed solar 12–16, LOW trough
assumed solar 5–8) with each city's own empirically measured extreme-timing
distribution, using true civil local time (no longitude/solar approximation), then
re-test §6's stratified calibration conclusions per city. Read-only.

**Headline: while building the per-city re-test, the same integer-bin-pivot parser
that §6 used was found to silently drop every interior bin for every C-unit
market (roughly half the fleet: Paris, London, Shanghai, Tokyo, Seoul, Wuhan,
Beijing, …). Fixing that one regex reverses §6's conclusion: T0-1 (whole-day
sigma, no elapsed-time narrowing) is NOT refuted — it is CONFIRMED, and worse
than the original audit's illustrative failure scenario, for both HIGH and LOW.**

---

## 1. The bug

`t01_overdispersion.py` / `t01_low.py` (§6's scripts, in the shared scratchpad)
classify each `q_json` bin label with three regexes: `between A-B°[FC]`,
`or higher`, `or below`. **There is no case for a plain single-value interior
bin** (`"Will the highest temperature in Wuhan be 29°C on July 1?"`). F-unit
markets always phrase interior bins as `between A-B°F` (2°F pair width), so F
cities are unaffected. **Every C-unit market's interior bins use the plain
single-value form** (1°C point width) — confirmed by direct enumeration:
122,751 plain `°C` bin labels across the full history, zero plain `°F` labels.
`bin_label()` returns `None` for all of them, and the `if lab is not None`
guard in both scripts silently drops that bin's probability from both
`p_above`/`p_below` sums.

Concrete confirmation (`Wuhan 2026-06-20`, obs=24°C, settle=32°C — a real
8-degree post-observation rise): the served posterior puts 99.6% of mass on
interior bins above 24, but the unpatched parser returns `None` for every one
of them and reports `p_above ≈ 0`, i.e. it reports the model as having
predicted **zero** further rise on a market that in fact rose 8°C and the
model in fact anticipated. This is not a rare edge case — it fires on every
C-unit market, in both directions, for the entire dataset.

Net effect: §6's `p_served` numbers are compiled from a mix of correctly-parsed
F-city rows and near-uniformly-zeroed C-city rows. Because roughly half the
fleet is C-unit, this drags every pooled `p_served` mean down substantially,
and — as shown below — flips the sign of the HIGH post-peak and LOW evening
conclusions.

Fix: add `PLAIN = re.compile(r"be\s+(-?\d+(?:\.\d+)?)°[FC]\s+on\b")` as a
fourth case. Scripts: `t01_fixed_corrected.py` (fixed-window baseline, for
comparison), `percity_calibration.py` (per-city civil-time re-test),
`percity_peaktrough.py` (Part 2 below). All three scripts, plus
`percity_windows.json` (the per-city quartile table), are in the shared
scratchpad next to the original `t01_*.py` for direct diffing.

### Corrected fixed-window baseline (same solar-hour buckets as §6, bug fixed)

HIGH:
| solar bucket | n | served | realized | ratio | §6 published ratio |
|---|---|---|---|---|---|
| pre (<12) | 57 | 0.707 | 0.842 | 0.84 | 0.64 |
| peak (12–16) | 164 | 0.395 | 0.256 | 1.54 | 1.09 |
| post (>16) | 574 | **0.314** | 0.070 | **4.50** | 0.65 |

LOW:
| solar bucket | n | served | realized | ratio | §6 published ratio |
|---|---|---|---|---|---|
| overnight (<5) | 3 | 0.667 | 0.667 | 1.00 | 0.90 |
| day (8–18) | 38 | 0.762 | 0.211 | 3.62 | 2.88 |
| eve (>18) | 71 | **0.409** | 0.000 | **∞** | ~0 (§6: "correct") |

n differs slightly from §6 (795 vs 786 HIGH, 112 vs 110 LOW) only because one
more day of markets settled between 2026-07-17 and this run — not the fix.

HIGH post-peak goes from "slightly under-dispersed" (0.65) to **4.5x
over-dispersed**. LOW evening goes from "the one bucket that's correct" to the
single worst cell in the whole table (served 0.41 vs realized 0.00). Manual
spot checks (below) confirm these are genuine, not a residual artifact:
`Chicago 2026-06-24` (F-unit, unaffected by the bug) at local hour 17 —
obs=settle=71°F, served `p_above=0.991`. The severe post-peak/post-trough
over-dispersion is real and present in F cities too; the bug just masked its
true pooled magnitude by zeroing out the C-city half of the data.

---

## 2. Per-city peak/trough timing (civil local hour, no solar approximation)

Method: `observation_instants`, `source='wu_icao_history'`, `authority='VERIFIED'`,
`causality_status='OK'`, days with ≥20 of 24 hourly rows. Peak/trough hour = the
first `local_hour` at which that day's final `running_max`/`running_min` is
reached (rows are NOT pre-aggregated cumulative — each row is that hour's own
reading, so the day's max/min must be taken as `MAX`/`MIN` over all rows, matching
the H-3/audit note that ingest rows are per-reading, not running totals).

**Hong Kong, Istanbul, Moscow, Tel Aviv have zero `wu_icao_history` rows** (they
settle off `ogimet_metar`/HKO sources per H-3) — excluded from this table and from
the per-city stratification below; this is expected, not a data gap.

50 cities, ~900 days each (Jinan/Qingdao/Zhengzhou are new cities with only
38–77 days — flagged low-confidence):

| city | n_days | peak Q1/med/Q3 | trough Q1/med/Q3 | peak OUT? | trough OUT? |
|---|---|---|---|---|---|
| Amsterdam | 927 | 12/13/15 | 2/4/8 | | OUT |
| Ankara | 929 | 13/14/15 | 4/5/6 | | |
| Atlanta | 923 | 14/15/16 | 4/6/7 | | |
| Auckland | 928 | 12/13/14 | 2/5/7 | | |
| Austin | 903 | 14/15/15 | 3/5/6 | | |
| Beijing | 930 | 13/14/15 | 3/5/6 | | |
| Buenos Aires | 927 | 13/14/15 | 3/6/7 | | |
| Busan | 929 | 12/13/14 | 2/4/6 | | OUT |
| Cape Town | 859 | 12/13/14 | 2/4/6 | | OUT |
| Chengdu | 930 | 13/14/16 | 3/5/6 | | |
| Chicago | 923 | 12/14/15 | 3/5/9 | | |
| Chongqing | 930 | 12/14/15 | 3/5/6 | | |
| Dallas | 912 | 14/15/16 | 4/5/7 | | |
| Denver | 885 | 12/14/15 | 3/5/6 | | |
| Guangzhou | 930 | 12/14/15 | 2/4/6 | | OUT |
| Helsinki | 928 | 11/13/15 | 1/4/14 | | OUT |
| Houston | 901 | 13/14/15 | 3/5/6 | | |
| Jakarta | 756 | 11/12/13 | 3/4/5 | | OUT |
| Jeddah | 930 | 12/12/13 | 4/5/6 | | |
| Jinan (n=38, low conf.) | 38 | 13/15/15 | 3/4/5 | | OUT |
| Karachi | 925 | 12/13/14 | 2/5/6 | | |
| Kuala Lumpur | 930 | 12/13/14 | 2/4/6 | | OUT |
| Lagos | 707 | 13/14/15 | 3/5/7 | | |
| London | 927 | 12/13/15 | 2/4/6 | | OUT |
| Los Angeles | 900 | 11/12/13 | 2/4/5 | | OUT |
| Lucknow | 837 | 12/13/14 | 3/4/5 | | OUT |
| Madrid | 927 | 15/16/17 | 5/6/7 | | |
| Manila | 930 | 12/13/14 | 2/3/5 | | OUT |
| Mexico City | 927 | 13/14/14 | 4/5/6 | | |
| Miami | 907 | 11/13/14 | 2/4/6 | | OUT |
| Milan | 927 | 13/14/15 | 2/5/6 | | |
| Munich | 927 | 12/14/15 | 2/4/6 | | OUT |
| NYC | 922 | 12/14/15 | 3/5/7 | | |
| Panama City | 891 | 12/12/14 | 1/3/5 | | OUT |
| Paris | 918 | 13/15/16 | 4/5/7 | | |
| Qingdao (n=77, low conf.) | 77 | 11/13/14 | 1/3/5 | | OUT |
| San Francisco | 915 | 12/13/14 | 2/4/5 | | OUT |
| Sao Paulo | 927 | 13/14/15 | 2/4/6 | | OUT |
| Seattle | 905 | 13/14/16 | 3/4/6 | | OUT |
| Seoul | 930 | 12/13/14 | 2/4/7 | | OUT |
| Shanghai | 930 | 11/12/13 | 1/3/6 | | OUT |
| Shenzhen | 592 | 12/13/14 | 1/4/6 | | OUT |
| Singapore | 930 | 12/13/14 | 1/4/6 | | OUT |
| Taipei | 927 | 10/11/12 | 1/4/7 | **OUT** | OUT |
| Tokyo | 930 | 11/12/14 | 1/3/6 | | OUT |
| Toronto | 917 | 12/14/15 | 3/5/7 | | |
| Warsaw | 927 | 12/13/15 | 2/4/6 | | OUT |
| Wellington | 928 | 11/12/14 | 1/4/9 | | OUT |
| Wuhan | 930 | 13/14/15 | 2/5/7 | | |
| Zhengzhou (n=53, low conf.) | 53 | 13/14/15 | 4/5/6 | | |

**HIGH peak-hour window (12–16) largely survives**: 49/50 cities have median
peak in [12,16]. Only **Taipei** deviates (median 11, IQR 10–12) — a genuinely
earlier peak, plausibly a coastal/maritime effect capping afternoon heating.

**LOW trough-hour window (5–8) does NOT survive**: **27/50 cities** have median
trough strictly outside [5,8], and the true median across nearly every city
sits at **3–5am local**, not 6–7am. This is systematic, not a handful of
outliers — cities as different as Shanghai, Tokyo, Manila, Panama City, London,
Miami, LA, Singapore, Seoul, Munich, Guangzhou, Cape Town all cluster at
median trough hour 3–4. The audit's assumed 5–8 window is shifted ~1–3 hours
too late almost everywhere; it looks like it was picked to bracket a
"post-dawn" trough rather than the true pre-dawn one. No bimodal/tropical
oddity stands out beyond this uniform early shift — Helsinki and Wellington
have unusually wide trough IQRs (1–14, 1–9) worth a note but not a different
shape.

---

## 3. Re-stratified calibration: per-city civil-time windows (bug fixed)

Same join/dedup/integer-bin-pivot as §6 (one posterior per market, latest
`computed_at`, `day0_conditioning.active`, settled `VERIFIED` outcome), but each
posterior's `observation_time` (UTC) is converted to **true civil local time**
via `zoneinfo(city_timezone)` (no longitude approximation), and classified
against **that city's own** peak/trough quartiles from §2: `pre` = before Q1,
`in` (peak/trough) = [Q1,Q3], `post` = after Q3. Hong Kong/Istanbul/Moscow/Tel
Aviv have no per-city window (§2) and are excluded (not pooled under a fixed
fallback — there is no settlement-faithful WU-ICAO basis for one).

**No individual city reaches n≥20 in any single stratum** — settled Day0
markets per city over the trading history run ~15–20 total, split three ways.
Per-city cells cannot be resolved at this sample size; only the
pooled-across-cities-per-own-window numbers below are reportable at n≥20.

HIGH (n=735 after excluding the 4 no-window cities):
| stratum | n | served | realized | ratio |
|---|---|---|---|---|
| pre (< city Q1) | 33 | 0.877 | 0.970 | 0.90 |
| in (city [Q1,Q3]) | 109 | 0.483 | 0.523 | 0.92 |
| post (> city Q3) | 593 | 0.313 | 0.061 | **5.16** |

LOW, 3-bucket (n=109, excludes Hong Kong only):
| stratum | n | served | realized | ratio |
|---|---|---|---|---|
| pre | 1 | 0.420 | 1.000 | 0.42 |
| in | 2 | 0.791 | 0.500 | 1.58 |
| post | 106 | 0.519 | 0.066 | **7.86** |

LOW, 4-bucket refinement (splits `post` at civil 18:00 — needs no empirical
estimate, same clock for every city — to test whether §6's mid-day-specific
signal is really mid-day-only or general post-trough):
| bucket | n | served | realized | ratio |
|---|---|---|---|---|
| pre | 1 | 0.420 | 1.000 | 0.42 |
| trough | 2 | 0.791 | 0.500 | 1.58 |
| day (trough→18:00) | 23 | 0.763 | 0.174 | 4.39 |
| eve (≥18:00) | 83 | 0.452 | 0.036 | **12.50** |

Manual spot-check of the eve cell confirms it is not a residual artifact:
`London 2026-07-13`, observation at 23:00 local (11pm, ~19h after the city's
own median trough of 4am) — obs=16°C, settle=16°C (the low already locked in),
served posterior: 92% on 15°C (a new low), 2% on 16°C (holds), 0% above.
`NYC 2026-07-14`, 18:00 local, obs=settle=74°F, served `p(below)=1.00`. These
are markets observed **well past their own city's empirical trough**, where a
new low essentially never happens (realized 3.6%), yet the served posterior
still assigns 40–100% probability to one. This is the M-6 gap ("LOW has no
trough-confidence analogue of `post_peak_confidence`") made concrete and
large, not a second-order risk.

---

## 4. Verdict

**§6's "T0-1 REFUTED" does not survive.** The refutation was built on a parser
that silently zeroed every C-unit interior bin's contribution to `p_served`,
which dragged the pooled post-peak/post-trough numbers down to
"well-calibrated" or "under-dispersed." With that one regex fixed:

- **HIGH does show over-dispersion**, concentrated post-peak (ratio 4.5–5.2x
  depending on windowing), not the "no over-dispersion anywhere" §6 claimed.
  Pre/peak strata are still mildly conservative (ratio 0.84–0.92) — the
  asymmetry T0-1's failure scenario predicted (over-confident late,
  appropriately cautious early) is present, just later than the illustrative
  numbers in the original audit text.
- **LOW over-dispersion is not a small-sample mid-day curiosity.** It is
  present, and larger, in both the mid-day cell (ratio 3.6–4.4, n=23–38) and
  — contrary to §6's explicit claim that "the bulk evening bucket … is
  correct" — the **evening cell is the worst in the table** (ratio 12.5,
  n=83, a much larger sample than §6's n=36 mid-day figure). Once true civil
  local time replaces the solar-hour approximation, "correct in the evening"
  reverses to "worst in the evening."
- Moving from the fixed solar-hour windows to true per-city civil-time windows
  changes ratios further (HIGH post: 4.50→5.16; LOW post: →7.86/12.50 in the
  4-bucket split) but the direction and rough magnitude were already visible
  in the corrected fixed-window baseline — **the parser bug, not the
  windowing choice, is what flipped the verdict.** The per-city/civil-time
  refinement sharpens the picture (and is methodologically the more correct
  choice, since Day0 settlement runs on the civil local day, not a
  longitude-derived solar clock) but is not the dominant correction.
- No individual city is statistically resolvable as "the one driving this"
  (no cell reaches n≥20 per city); the pattern is broad-based across both
  units and many cities (F: Chicago, Miami, NYC; C: Wuhan, Warsaw, Madrid,
  Guangzhou, Kuala Lumpur, London, Seoul, Shanghai, Tokyo, Helsinki, Milan,
  Paris, Amsterdam, Ankara, Shenzhen all contribute qualifying rows), so this
  reads as a mechanism-level effect, not a city-specific data-quality issue.
- The LOW trough-window finding in §2 (true median trough 3–5am local, not
  5–8am) independently corroborates the mechanism-level read: if the
  serving code's remaining-window/confidence structure has no trough-timing
  awareness at all (M-6), it would not matter that the *true* trough is
  earlier than assumed — the code isn't consulting any trough-hour prior
  either way, which is consistent with a flat, un-narrowing whole-day sigma
  persisting long past the point (any point) where the trough already happened.

**Recommendation:** this needs to go back to whoever owns T0-1/§6 before any
further reliance on "T0-1 downgraded, no fix needed." The corrected numbers
argue T0-1 is real for both HIGH and LOW, and that M-6 (no LOW
trough-confidence) is a live contributor rather than a second-order risk.
Re-run `t01_overdispersion.py`/`t01_low.py` in place with the `PLAIN` regex
added before drawing any further conclusion from those scripts.
