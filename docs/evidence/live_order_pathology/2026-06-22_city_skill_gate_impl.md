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

## 5. WALK-FORWARD validation (no look-ahead) on real settled data (n=91)

Invocation: `python3 -m scripts.city_skill_gate_forward_validation --world state/zeus-world.db`.

### 5a. ADMIT side (revenue) — NOT licensable

| Gate | admit_n | admitted EV/bet | admit cities |
|---|---|---|---|
| WALK-FORWARD (valid) | 2 | **−0.15** | Hong Kong (−0.72, LOST), Tokyo (+0.42, won) |
| LOOK-AHEAD (INVALID) | 35 | +0.303 | full-sample-skill>0 — the in-sample +10.1% artifact |

The "+10.1%" (+0.303/bet) is reproduced and **confirmed a LOOK-AHEAD artifact** — it requires knowing
each city's full-window skill in advance. Proper double-walk-forward admits only 2 bets, one a
noisy-middle false positive (Hong Kong, prior_skill +0.014, n=4 → then LOST −0.72) → net −0.15/bet.

### 5b. BLOCK side (loss reduction) — the deployable result

Block ONLY cities confirmed negative-skill in BOTH time halves (temporally-stable losers), walk-forward.

- **Confirmed stable-bad (block list): `["Karachi"]`** — skill −0.27 early / −0.25 late; realized
  −0.72/bet, −3.60 EV sum over n=5. Blocking Karachi's FUTURE bets removes that loss stream.
- **Confirmed stable-good (never blocked): `["London", "Tokyo"]`** — `wrongly_blocked_stable_good = 0`
  (the gate does NOT block a genuine-edge city).
- Walk-forward retroactive blocks = 0 (a city only earns its block once it has a two-half record
  BEFORE the bet — no look-ahead). So the loss-reduction activates GOING FORWARD as history accrues;
  the end-of-window artifact correctly carries Karachi as the block, protecting London/Tokyo.

**Per-city early/late sign-stability: 3/7 stable** (Karachi bad; London, Tokyo good; HK/Milan/Seoul/Wuhan flip).

## 6. Shadow-logger (the path to revenue — accrue, then license)

`src/decision/shadow_admit_logger.py` (+7 tests) records every evaluated side-candidate's would-admit
decision + features (admit0 = native_quote_available AND quote_fresh AND q_lcb_side_old > own_side_cost;
raw_side_prob, q_lcb_side, own_side_cost, admission_margin, city, target_date, side, posterior_version,
city_skill_admit, selection_calibrator_q_safe). Append-only JSONL, flag-gated `ZEUS_SHADOW_ADMIT_LOG`
default OFF, fail-soft (a write error never breaks trading), NEVER read back into any gate. This
accrues the current-regime (bayes_fusion) would-admit population that STEP-1 found ABSENT — so the
selection-calibrator's full would-admit EB and a forward-positive city gate can be validated once a few
hundred labelled rows accrue.

## 7. Final verdict (HONEST)

The in-sample city edge is REAL but **NOT forward-licensable at 91 bets** — confirmed three independent
ways (per-bin EB calibrator, σ-repr-from-d_eff_m root fix, per-city skill gate), all blocked by the
same thin-data wall and the absence of a pre-trade signal that predicts forward edge (grid distance was
falsified: corr(error, d_eff_m) = −0.52). The +10.1% is look-ahead.

**Deployable today = loss reduction + accrual, NOT a +EV revenue gate:**
1. **City-skill gate in block-only mode** (`require_stable_bad_to_block`): hard-blocks confirmed
   two-half stable losers (Karachi today; the list grows as history accrues), protects stable-good
   cities (0 wrongly blocked). Forward-valid loss reduction.
2. **Selection-calibrator in block-only/shadow mode**: blocks the toxic-NO adverse-selection tail,
   fail-closed.
3. **Shadow-logger ON**: accrue the would-admit population.
All three flag-gated default OFF; the orchestrator owns the flips. Revenue licensing waits on settled
evidence: accrue via the shadow log, then re-run the walk-forward gates to promote the admit side when
the track records deepen and sign-stability holds. No look-ahead +EV is claimed.
