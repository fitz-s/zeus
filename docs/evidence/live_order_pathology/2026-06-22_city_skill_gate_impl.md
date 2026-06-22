<!--
Created: 2026-06-22
Last audited: 2026-06-22
Authority basis: per-city historical settlement-skill gate
  (team-lead approved (a) 2026-06-22; live_order_pathology 2026-06-22)
-->
# Per-city historical settlement-skill gate — implementation + walk-forward

## 1. Why this, not the data-precision / grid-distance gate (settlement-verified falsification)

The proposed "station-precision edge gate" and the "σ-repr root fix" were both BUILT ON a hypothesis
that FAILED against settlement:

- `nearest_grid_distance_km` (live provenance, `openmeteo_precision_guard.metadata`) = **0.0 for every
  city** (OpenMeteo serves a grid cell AT the requested coord). It cannot rank anything.
- `config/grid_representativeness.json` `d_eff_m` (real grid→station distance):
  **corr(per-city forecast Brier ERROR, d_eff_ifs) = −0.52** (strong WRONG sign). Karachi (worst city,
  error 0.78) has the CLOSEST grid (d_eff=1603m); Milan/Tokyo/London (best, error 0.01–0.03) have HIGH
  d_eff (5845/7452/4033m). Fitting σ_repr from d_eff_m would WIDEN σ on our best cities and NARROW it
  on our worst — the exact opposite of intent. **Wiring the σ-repr stub from d_eff_m would harm.**
- `station_representativeness_sigma` (`src/forecast/sigma_authority.py:361`) IS a real stub returning
  0.0 (zero data-precision widening is applied) — a true gap, but `d_eff_m` is the wrong driver.

What DOES separate the +edge from −edge cities is forecast **ACCURACY** (per-city Brier-vs-market:
London −0.13, Milan −0.09, Tokyo −0.06 beat the market; Karachi +0.26, Houston +0.21 lose). Accuracy
is post-settlement; its only pre-trade form is a per-city **historical settlement-skill track record**.
This gate consumes that track record.

## 2. Files added

| File | Role |
|---|---|
| `src/decision/city_skill_gate.py` | Runtime serving rule. `apply_city_skill_gate(city, artifact)` → `CitySkillVerdict`. Admit iff `prior_n >= min_track_record` AND `prior_skill > skill_floor`; else BLOCK (negative skill) / ABSTAIN (thin track / below floor). Fail-closed (absent/malformed/stale/unknown-city). `city_skill_gate_admits` seam helper (flag `ZEUS_CITY_SKILL_GATE_LIVE`, default OFF = no-op). |
| `scripts/fit_city_skill_gate.py` | Walk-forward fitter (artifact's only writer). `SettledCityBet`, `prior_skill` (no-leak), `learn_hyperparameters` (inner walk-forward prequential admitted-EV over the (min_track, floor) grid — LEARNED, not hard-coded), `build_rows`, `fit_city_skill_gate`. |
| `scripts/city_skill_gate_forward_validation.py` | Walk-forward harness: as-of-T gate (learn hyperparams on prior-resolved only), admitted/blocked cities, after-cost EV forward, early/late stability, look-ahead contrast. |
| `tests/decision/test_city_skill_gate.py` (10) + `tests/test_fit_city_skill_gate.py` (4) | Runtime + fitter no-leak/sign/learned-hyperparam/schema contracts. |
| `src/state/db_writer_lock.py` | +2 read-only `?mode=ro` allowlist lines. |
| `docs/evidence/live_order_pathology/2026-06-22_city_skill_gate_forward_validation.json` | Walk-forward report (real data). |

The gate PAIRS with the selection-calibrator: skill-gate = WHICH cities; calibrator = block the toxic
tail WITHIN them. Both apply at the admission seam before edge/FDR/Kelly, both flag-gated default OFF.

## 3. Method + fail-closed

- **Signal:** per-city `prior_skill = mean(market_Brier − our_Brier)` over that city's rows with
  `target_date` STRICTLY before the decision (walk-forward, no leak; `prior_skill` test asserts the
  strict boundary).
- **Gate:** admit iff `prior_n >= min_track_record` AND `prior_skill > skill_floor`. Negative-skill
  cities BLOCK (`CITY_SKILL_BLOCKED_NEGATIVE`); positive-but-thin ABSTAIN (`CITY_SKILL_THIN_TRACK`);
  positive-but-below-floor ABSTAIN (`CITY_SKILL_BELOW_FLOOR`). Decides from SKILL ONLY — never alters
  q, never uses price.
- **Learned hyperparameters:** `(min_track_record, skill_floor)` chosen by inner walk-forward
  prequential admitted-EV over grids `{3,4,5,6,8} × {0,.01,.02,.05,.1}` — never hard-coded.
- **Fail-closed:** absent/malformed/stale-version/unknown-city → no admit. Versioned to the posterior.

## 4. Test results

```
tests/decision/test_city_skill_gate.py ..........  [10 passed]
tests/test_fit_city_skill_gate.py ....            [ 4 passed]
+ calibrator suites + db_writer_lock antibody:    66 passed in 16.28s
```

## 5. WALK-FORWARD validation (THE GATE — no look-ahead) on real settled data (n=91)

Invocation: `python3 -m scripts.city_skill_gate_forward_validation --world state/zeus-world.db`.
At each bet T, `(min_track, floor)` is LEARNED on rows resolved before T, then the bet's city prior
skill is computed on prior rows and gated.

| Gate | admit_n | admitted EV/bet | admit cities |
|---|---|---|---|
| **WALK-FORWARD (valid)** | 2 | **−0.15** | Hong Kong (−0.72, LOST), Tokyo (+0.42, won) |
| LOOK-AHEAD (INVALID) | 35 | +0.303 | (full-sample-skill>0 — the in-sample +10.1% artifact) |

**Per-city early/late sign-stability: only 3/7 cities are stable** — Karachi (reliably bad), London &
Tokyo (reliably good). Hong Kong / Milan / Seoul / Wuhan FLIP sign (the noisy middle).

## 6. Verdict (HONEST — per the team-lead's "if even the extremes don't hold EV>0, say so")

**The per-city skill gate is CORRECTLY BUILT and HONEST, but does NOT demonstrate EV>0 walk-forward at
n=91.** Specifics:
- The look-ahead "+10.1%" (+0.303/bet) is reproduced and confirmed to be a **look-ahead artifact** — it
  requires knowing each city's full-window skill in advance.
- With proper double-walk-forward (learn the threshold on prior data, apply forward), the gate admits
  only **2 bets** and one (Hong Kong, prior_skill +0.014, n=4) is a noisy-middle false positive that
  then LOST −0.72 → net **−0.15/bet**.
- Only **Tokyo and London** are genuinely sign-stable winners, but they are too sparse to clear EV>0
  forward; the noisy middle still leaks the occasional toxic admit.

This is the **same 91-bet thin-data wall** that bounded the per-bin EB calibrator. The city edge is real
IN-SAMPLE but not yet forward-predictable.

**Recommendation:** the gate is SAFE in **block-only mode** (it reliably BLOCKS the stable losers —
Karachi/Houston/Shanghai — which IS forward-valid), NOT as a +EV revenue gate. Deploy flag-gated
(`ZEUS_CITY_SKILL_GATE_LIVE`, default OFF) in block-only posture alongside the selection-calibrator;
**shadow-log** per-city skill so the admit side can be promoted once the track records deepen and the
sign-stability holds across more settled days (re-run §5 to promote). The +10.1% revenue capture is NOT
walk-forward-real on the current data and is not claimed.
