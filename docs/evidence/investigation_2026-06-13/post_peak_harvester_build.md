# Post-Peak Microstructure Harvester — build evidence

- Created: 2026-06-14
- Authority basis: post-peak repricing-latency edge proven live 2026-06-13
  (London "22°C" bin BUY NO, filled 58 @ 0.4667, +20¢ MTM).
- Status: PREPARED IN WORKTREE. Not deployed, not auto-trading. Produces a
  SCANNER that surfaces + sizes ranked opportunities; execution stays a
  separate verified gated step.

## 1. The edge (what we are exploiting)

Once a city passes its daily temperature peak — the settlement-station METAR
max-so-far is locked (flat/declining for ≥ ~1h) — Polymarket is SLOW to reprice
the bins the observed max has made near-impossible. Their NO still trades cheap.
We BUY NO on those. We do NOT out-forecast; we exploit **repricing latency**.

- The settlement truth is the REAL ICAO airport METAR (e.g. London EGLC), with
  `wmo_half_up` rounding per the settlement contract. The city-grid Open-Meteo
  forecast fabricates fake mispricings and is NOT used.
- The end-of-day window is already efficient (the market tracks obs instantly).
  The edge lives in the **post-peak-but-pre-settlement** window only.

## 2. Files

| File | Role |
|---|---|
| `src/strategy/post_peak_harvester.py` (779 lines) | The scanner. Surfaces + sizes ranked BUY-NO opportunities. No order placement. |
| `src/strategy/post_peak_backtest.py` (153 lines) | Settlement back-test harness: grades realized NO win-rate as positions settle (the edge proof). |
| `tests/test_post_peak_harvester.py` (444 lines, 9 tests) | RED-on-revert relationship tests. |

### Reuse provenance (audited 2026-06-14 — all CURRENT_REUSABLE)

The harvester reinvents nothing; it composes the existing, verified surfaces:

- `src/data/day0_fast_obs.py`
  - `fetch_metar_reports(stations, hours)` — free aviationweather.gov METAR feed,
    same physical ICAO settlement station as WU (3-6 min behind obs).
  - `running_extremes_for_local_day(...)` → `FastObsExtremes.high_so_far` — the
    REAL settlement-station running max, DST-correct local-day membership, unit
    law (F cities require the tenths-C T-group), plausibility quarantine.
  - `fast_obs_source_for_city(city)` — settlement-faithfulness gate (excludes
    Seoul/RKSI-class stations whose METAR integer ≠ WU's settlement integer).
- `src/contracts/settlement_semantics.py`
  - `SettlementSemantics.for_city(city)` + `.round_single` — `wmo_half_up`
    rounding (HKO truncation handled by the contract; we never construct it).
  - `settlement_preimage_offsets(rule)` — the single declarative bin→preimage
    convention used for P(bin) integration.
- `src/signal/ensemble_signal.py::sigma_instrument_for_city(city)` — per-city
  sensor σ (London C = 0.28°C) that drives the remaining-day upside tail.
- `src/strategy/fees.py::venue_fee_rate()` — canonical Polymarket taker fee rate.
- `src/strategy/kelly.py` — fractional-Kelly basis (f* = (p−ask)/(1−ask)).
- `src/data/polymarket_client.py::PolymarketClient.get_best_ask(token_id)` — live
  NO ask + book depth at best ask. Read-only; no order placement.
- `src/data/market_scanner.py`
  - `find_weather_markets(...)` — active city/date market discovery.
  - `build_market_support_topology(event, unit)` — MECE bin partition + the
    per-bin executable NO `no_token_id`/`condition_id` payloads.

## 3. The algorithm (file:line)

### 3.1 Post-peak window — `determine_post_peak_window` (harvester.py:199)

POST-PEAK requires BOTH:
- **(A)** local time ≥ `city.historical_peak_hour` (`+ PEAK_HOUR_MARGIN_HOURS`).
- **(B)** the running max has NOT advanced within the trailing `PEAK_LOCK_WINDOW`
  (default 1h). `_minutes_since_max_advance` (harvester.py:288) walks the
  city-local-day settlement-unit reports for the configured ICAO station
  (`city.wu_station`) and records when the running max last strictly increased.

Both conditions use the REAL ICAO station METAR, never a forecast grid.

### 3.2 Obs-conditioned P(bin is day's max) — `bin_probabilities_post_peak` (harvester.py:344)

Conditioned on the **observed running max** `H` (a hard lower bound on the
settled max `M`), not a forecast. The remaining-day upside `R = M − H` is a
one-sided Gaussian tail in settlement units:

```
P(M ≥ v) = 1                              for v ≤ H        (already reached)
P(M ≥ v) = 2·(1 − Φ((v − H)/σ))           for v > H        (one-sided |N(0,σ)|)
```

A bin covering integer settlement values {a..b} has continuous preimage
`[a + low_off, b + high_off)` from `settlement_preimage_offsets(rule)`
(wmo_half_up ⇒ (−0.5, +0.5)). Its probability telescopes:

```
P(bin) = P(M ≥ preimage_low) − P(M ≥ preimage_high)
```

Bins strictly below the rounded max are P = 0 (the observed max already exceeded
them — settlement cannot land below H). **That zero is the source of the
near-certain NO.**

### 3.3 The paranoid spike-resilience guard (the math)

The unfair re-price (harvester.py:344, `paranoid=True` branch) does TWO things:

```
eff_H  = H + PARANOID_FREE_RISE       # PARANOID_FREE_RISE = 1.0  (grant a free +1 notch)
eff_σ  = σ × PARANOID_SIGMA_MULT       # PARANOID_SIGMA_MULT = 2.0  (widen the tail)
```

i.e. it pretends a plausible **+1 single-notch future rise has already happened**
AND widens the remaining tail. The fair NO value under this unfair model is
`1 − p_paranoid(bin is max)`. An opportunity is kept ONLY if it is still +EV
here (harvester.py:463, gate G3):

```
paranoid_edge_cents = (1 − p_paranoid(bin is max) − (no_ask + fee_at_ask)) · 100 > 0
```

**Why this separated London from Paris/Munich (2026-06-13):** the one-notch bin
(+1°C above the locked max) is already rejected by the honest model — with the
one-sided tail its P(it is the max) exceeds the 5% near-impossible threshold, so
G1 catches it. The guard's distinguishing job is the **two-notch bin** (the
"≥ +1.5°C two-notch spike" the brief calls out): it is near-impossible under the
honest model (P ≈ 0, passes G1) but the +1-notch paranoid shift gives it real
mass (London 24°C: p_paranoid ≈ 0.48 at σ=0.5). London's NO ask was cheap enough
(0.4667) that the paranoid edge stayed positive → surfaced. At a marginal ask
(0.55) the paranoid edge flips negative → **rejected**, exactly the Paris/Munich
outcome. Verified numerically in
`test_paranoid_guard_rejects_marginal_two_notch_spike_bin`.

Note: the redundant explicit `if paranoid_edge_cents <= 0: return None` line is
defense-in-depth — the paranoid-Kelly sizing (below) is computed on the SAME
paranoid `p_no`, so a non-positive paranoid edge already yields f* ≤ 0. The two
gates are mathematically coupled by construction; removing the paranoid MODEL
(not just the line) is what breaks the contract, and the test proves that.

### 3.4 Gates + sizing — `evaluate_bin_opportunity` (harvester.py:463)

- **G1** near-impossible under the honest model: `p_obs(bin is max) ≤ 0.05`.
- **G2** cheap post-cost edge: `((1 − fee_at_ask) − no_ask)·100 > 3¢`
  (`fee_at_ask = fee_rate·ask·(1−ask)`, the per-share taker fee in price units —
  the same cheapness form the live London trade cleared).
- **G3** paranoid guard (§3.3).
- **Size** = fractional-Kelly (`KELLY_FRACTION = 0.25`) on the PARANOID p_no,
  mapped onto the `$25–40` envelope, then **capped by live book depth at the NO
  ask** (`depth_capped` flag records when the cap bound the size).

### 3.5 Output — `scan_event_for_opportunities` (harvester.py:576) / `scan_active_markets` (harvester.py:712)

Each `HarvestOpportunity` (harvester.py:165) records: city, target_date, bin
label + bounds, NO `token_id`, condition_id, live `no_ask`, obs-conditioned and
paranoid P(NO), honest + paranoid edge ¢, size $, Kelly fraction, the
`spike_break_threshold` (the temperature the city must still reach to put
settlement into the bin), `depth_shares_at_ask`, and the locked
`rounded_max_bin_value`. `scan_active_markets` ranks all opportunities by
paranoid edge descending. Daily-LOW markets are excluded (the post-peak max edge
is daily-HIGH only).

## 4. Tests (RED-on-revert) — `tests/test_post_peak_harvester.py`

9 tests, all passing. Synthetic London (EGLC, C-settled):

| Test | Contract |
|---|---|
| `test_locked_max_is_post_peak` | locked 22°C max, 17:00 local → post_peak, rounded_max=22 |
| `test_still_climbing_is_not_post_peak` | max advanced within the lock window → NOT post_peak |
| `test_impossible_lower_bin_surfaces_cheap_no` | a 3-notch-below bin (impossible) surfaces at the live 0.4667 ask, size ∈ [25,40] |
| `test_paranoid_guard_rejects_marginal_two_notch_spike_bin` | two-notch bin passes G1, surfaces at a cheap ask, REJECTED by the paranoid guard at a marginal ask (proves the guard fires, not G1) |
| `test_scan_event_surfaces_only_impossible_lower_bins` | end-to-end: 19/20/21°C surfaced; 22°C (the max) and 23°C (one notch, guard) not |
| `test_scan_event_still_climbing_surfaces_nothing` | a still-climbing city surfaces nothing |
| `test_scan_active_markets_ranks_by_paranoid_edge` | top-level scan ranks by paranoid edge descending |
| `test_backtest_grades_no_win_and_pnl` | settles at 22°C → every below-max NO wins, win-rate 1.0, P&L > 0, edge_gap ≥ 0 |
| `test_backtest_grades_no_loss_when_settles_in_bin` | a NO on the bin the day settled into is graded a loss with negative P&L |

**Mutation verification** (RED-on-revert confirmed):
- Breaking the post-peak gate (`is_post_peak=True` always) → 2 tests RED.
- Corrupting the telescoping sign (`p_hi − p_lo`) → 3 tests RED.
- Removing the below-max certainty (`return 1.0` for `v ≤ H`) → 3 tests RED.
- Neutering the paranoid model (`eff_H = H`, `eff_σ = σ`) → guard test RED.

Run: `python3 -m pytest tests/test_post_peak_harvester.py -q` → `9 passed`.

## 5. Settlement back-test harness — `src/strategy/post_peak_backtest.py`

The EDGE PROOF. `run_backtest(opportunities, settlement_lookup)` (backtest.py:121)
grades each recorded `HarvestOpportunity` once the city/date settles:

- NO on a bin WINS iff the settled rounded max does NOT fall in that bin
  (`_bin_contains` on the recorded `bin_low`/`bin_high` — exact, no heuristic).
- P&L per share: win → `+(1 − ask − fee)·100¢`; loss → `−(ask + fee)·100¢`.
- Reports `realized_no_win_rate`, `mean_predicted_p_no`, and the **edge_gap**
  (`realized − predicted`; ≥ 0 confirms the latency edge), plus weighted P&L in $.

`settlement_lookup(city, target_date) → settled_rounded_max | None` is injected,
so the harness is DB-agnostic. Live wiring passes a reader over the harvester
settlement truth (`src/ingest/harvester_truth_writer.py` writes the settled
`settlement_value`); tests pass a dict. As the surfaced positions settle over the
coming days, this harness produces the realized-NO-win-rate proof.

## 6. Deploy / run procedure (NOT yet wired live)

The scanner is a pure surface — it places no orders. To run a recorded scan:

```python
from src.strategy.post_peak_harvester import scan_active_markets
opps = scan_active_markets()          # discovers high-temp markets, fetches METAR,
                                       # surfaces + sizes ranked BUY-NO opportunities
for o in opps:
    print(o.as_record())               # record into a ledger for the back-test
```

`scan_active_markets` injects a live `PolymarketClient` (NO ask + depth, read-only)
and `venue_fee_rate()` by default; tests/back-test replay inject stubs via
`scan_event_for_opportunities`.

## 7. How execution stays a verified gated step

This build produces a SCANNER ONLY. `scan_*` returns ranked
`HarvestOpportunity` data; it imports no order-submission path and calls only the
read-only `PolymarketClient.get_best_ask`. Acting on an opportunity requires the
operator's SEPARATE, already-verified execution lane (the same path that placed
the live London order under its own arm/verification gates). The harvester never
auto-trades; it surfaces, sizes, and records. The realized NO win-rate from §5 is
the gate that justifies promoting any opportunity to that execution step.
