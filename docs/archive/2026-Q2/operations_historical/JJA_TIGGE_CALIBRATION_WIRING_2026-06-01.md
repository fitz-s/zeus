# JJA / TIGGE Calibration Wiring — Root + Correct-Wiring Design
# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: READ-ONLY investigation. Ground truth = state/zeus-forecasts.db
#   (platt_models_v2, calibration_pairs), state/zeus-world.db (model_bias_ens),
#   src/engine/event_reactor_adapter.py (_snapshot_p_cal/_snapshot_p_raw),
#   docs/reference/zeus_math_spec.md, architecture/ecmwf_opendata_tigge_equivalence_2026_05_06.yaml,
#   src/calibration/platt_oos_resolver.py, src/data/calibration_transfer_policy.py.

## 0. Premise correction (resolves the conflict between the two briefs)

The operator's mid-task correction is **CONFIRMED by the data and the spec**, with one
refinement. The first brief's claim "calibration_pairs are OpenData-sourced and TIGGE was
purged as contamination (#58, the right call)" is **FALSE on its facts**:

- `calibration_pairs` source split (state/zeus-forecasts.db, 48,157,324 rows):
  - `tigge_mars` = **47,544,020 rows (98.7%)**
  - `ecmwf_open_data` = 613,304 rows (1.3%)
  - **TIGGE was NOT purged. It is the calibration substrate.** The OpenData rows are the
    thin ~2-week post-launch live accrual, not the training corpus.
- Every `platt_models_v2` row (137 total) is `source_id='tigge_mars'`,
  `data_version='tigge_mx2t6_local_calendar_day_max'`. **There is no OpenData-sourced Platt.**
  TIGGE IS the calibrator for live OpenData serving — by design.

**Refinement to the operator's framing:** the math spec and the equivalence YAML do NOT say
"TIGGE and OpenData are byte-identical / proven the same." They say TIGGE and OpenData are the
**same ECMWF IFS ensemble product on two delivery channels** (archive vs live), which is the
load-bearing point — it makes a TIGGE-trained Platt **train/serve-CONSISTENT** for OpenData
serving. The "not byte-identical yet" caveat is about a *separate* evidence-gating flag, not
about product identity. So: there was no legitimate "TIGGE ≠ OpenData product" purge, and the
serving wiring (TIGGE Platt → OpenData live) is the intended, correct design.

### Spec authority that TIGGE == OpenData product

`docs/reference/zeus_math_spec.md` line 16 (the canonical X-side definition):
> "2. Forecast data (X side: **TIGGE ECMWF ensemble**)"
line 106: "**Canonical**: ECMWF TIGGE GRIB 51-member ensemble … 1 control + 50 perturbed."
line 107: live path is the same 51-member ECMWF ensemble via Open-Meteo/OpenData "**Same
51-member structure**".

`architecture/ecmwf_opendata_tigge_equivalence_2026_05_06.yaml` §`same_physical_ensemble.claim`
(lines 66-75):
> "ECMWF Opendata (source_id='ecmwf_open_data') and the TIGGE archive (source_id='tigge_mars')
> **name the same ECMWF IFS ensemble forecast family** … TIGGE is the delayed archive channel,
> while OpenData is the live dissemination channel."

`release_channels` (lines 76-82): tigge_mars `purpose: reanalysis / training`; ecmwf_open_data
`purpose: live serving`. `key_implication` (83-88): "A Platt model trained on TIGGE pairs **can
become the canonical calibrator for ECMWF OpenData live forecasts**."

Serving wiring proof: `src/data/calibration_transfer_policy.py:55-66`
`_TRANSFER_SOURCE_BY_OPENDATA_VERSION` maps OpenData data_version → TIGGE Platt data_version,
with the inline note "physical identity is identical (same IFS ensemble member temperature
extraction)." This is the mechanism by which live OpenData forecasts resolve a TIGGE-fit Platt.

**Verdict on the "purge" thesis:** there is no evidence in the live DBs that the TIGGE
calibration corpus was deleted. #58's premise ("TIGGE is a wrong data product for OpenData
calibration") would have been wrong had it been executed — but the calibration_pairs table shows
it was NOT executed against the pairs. The real defect is **not a purge**; it is a **Platt-fit
coverage gap** (next section). Treat any doc asserting "TIGGE purged / TIGGE ≠ OpenData product"
as legacy-untrusted and overturned by the spec + the 47.5M surviving TIGGE rows.

---

## VERDICT PART 1 — June/JJA calibration: **GAP, not "resolved by identity"**

For a live JJA market (NH-summer cities: Shanghai / Tokyo / Singapore / Hong Kong high), the
exact authority that calibrates the live q, traced through
`src/engine/event_reactor_adapter.py::_snapshot_p_cal` (lines 3480-3571) with
`edli_bias_correction_enabled=true` (config/settings.json:92):

**Path A — VERIFIED bias row exists (Shanghai, Tokyo, Singapore, Tel Aviv, SF, Wuhan, …):**
1. `_snapshot_p_raw` (3464-3469) subtracts `model_bias_ens.effective_bias_c` from member maxes.
2. `_snapshot_p_cal` (3498-3503) sees `_edli_bias_corrected=True` → **FORCES identity-Platt**
   (`p_cal = normalized p_raw`). The fitted Platt is deliberately bypassed (train/serve lockstep:
   pairs were fit on uncorrected p_raw).
   → **Live q = identity calibration applied to a bias-shifted p_raw.**

**Path B — no VERIFIED bias row (Hong Kong JJA has none):**
1. Bias correction fail-closes (3464-3469 → `_bias_corrected=False`), raw members used.
2. `_snapshot_p_cal` looks up a Platt for (city, JJA, high) → **none fitted** → identity-Platt
   fallback (3538-3562, log tag `calibration_identity_fallback_no_platt_bucket`).
   → **Live q = identity calibration on raw (cold-biased) p_raw — uncorrected AND uncalibrated.**

### Per-city JJA authority (live NH-summer cities), evidence

| City | JJA Platt model? | JJA pairs available (TIGGE) | VERIFIED JJA bias (°C, n_live) | Live JJA authority |
|------|------------------|------------------------------|-------------------------------|--------------------|
| Shanghai | NO | 150,144 (1,472 DGs) | −0.97, n=14 | identity + bias-shift |
| Tokyo | NO | 150,144 | −3.45, n=14 | identity + bias-shift |
| Singapore | NO | 150,144 | −1.58, n=14 | identity + bias-shift |
| Tel Aviv | NO | 150,144 | −4.00, n=13 | identity + bias-shift |
| San Francisco | NO | 135,424 | −4.68, n=15 | identity + bias-shift |
| Wuhan | NO | (present) | +0.41, n=14 | identity + bias-shift |
| **Hong Kong** | NO | 150,144 | **none** | **identity on RAW (Path B) — uncorrected + uncalibrated** |
| NYC | YES (fitted) | 135,424 | — | still identity at serve (see note) |
| London | YES (fitted) | 150,144 | — | still identity at serve (see note) |

JJA `platt_models_v2` rows total = **8**, and they cover only Buenos Aires, London, NYC,
Sao Paulo, Wellington — i.e. SH-winter cities + two NH cities. **Zero** of the East-Asia /
tropical live-summer cities have a JJA Platt, despite ~150k JJA pairs each.

**Note — even the fitted Platt is not live-active.** `src/calibration/platt_oos_resolver.py`
(lines 4-13, 40-52): identity-Platt is the **fail-closed live DEFAULT**; a fitted Platt is a
**CANDIDATE** that reaches selection only via a PROMOTE row in an OOS decision table matched by
`p_raw_domain_hash`. **No OOS-promote / platt_decision table exists in the live DB** (verified:
`SELECT name … LIKE '%oos%'/'%promote%'` returns nothing). So **no city — JJA or otherwise —
serves a non-identity Platt live right now.** The whole live book prices on identity calibration.

**Is "June has no authoritative calibration" resolved?** **NO — it is a GAP.** Identity is the
intended *default*, but a default is not an *authority*. The bias-correction that is supposed to
make identity acceptable is itself thin and structurally degenerate (Part 3). HK JJA has neither
Platt nor bias → pure raw cold-biased identity. The June book is uncalibrated in the meaningful
sense.

---

## VERDICT PART 2 — TIGGE MC disconnect: **NOT a purge. A Platt-fit coverage gap.**

The disconnect the operator senses is real but its cause is the opposite of "deliberate purge":

- The 2-year TIGGE Monte-Carlo data **IS connected** at the pair layer: 47.5M `tigge_mars`
  rows, ~150k JJA-HIGH pairs per live city, all `training_allowed=1`, all `authority='VERIFIED'`.
- It is **disconnected at the Platt-fit layer**: the fit job (`scripts/refit_platt.py`, season-
  filtered via `season_filter`) ran heavily for MAM (91 models) but produced only 8 JJA models,
  none for the East-Asia/tropical live-summer cities. The data is present; **the fit was never
  run/persisted for those (city, JJA) buckets.**
- It is **further disconnected at the serve layer** by two intentional gates that both collapse
  to identity: (a) the A4 bias-correction lockstep forces identity whenever a bias row exists,
  and (b) the OOS-promote requirement means an unpromoted Platt never serves. So even if the JJA
  Platts were fitted, they would not serve until an OOS-promote pipeline exists.

**So: (a) deliberate product-mismatch purge — NO** (the TIGGE pairs are present and are the
canonical training product per spec; train/serve is consistent by product identity).
**(b) unwired/abandoned pipeline — YES, partially:** the JJA Platt-fit was never completed for
live-summer cities, and the OOS-promote serving path that would let any fitted Platt go live is
absent. The TIGGE MC is the right product; the fit+promote wiring on top of it is incomplete.

No `data_version`/`source_id` allowlist *excludes* TIGGE — `tigge_mars` /
`tigge_mx2t6_local_calendar_day_max` is the **canonical** tag in both platt_models_v2 and
calibration_pairs. There is no contamination gate discarding valid TIGGE data. (If a doc claims
one, it is stale; the live tables prove TIGGE is canonical.)

---

## VERDICT PART 3 — Are live JJA trades calibration-trustworthy? **NO — pre-arm blocker.**

The June book prices on identity calibration backed by a bias-correction that is
**data-insufficient and structurally degenerate**, evidenced from `model_bias_ens`
(state/zeus-world.db, the 28 VERIFIED rows):

1. **Thin n.** Every VERIFIED bias row has `n_live = 13–16` (≈2 weeks of settled observations).
   `n_prior = 0`, `n_paired = NULL` on all 28 rows. The design (zeus_math_spec.md §"ENS bias
   correction", line 798: empirical-Bayes shrink of the TIGGE prior toward live OpenData settled
   residuals) is **not actually running as designed** — with `n_prior=0` and no paired delta,
   `effective_bias_c` is just a raw 2-week live-residual mean, not a shrunk posterior. This is
   exactly the data-insufficiency the `bias_decay_kelly_haircut` interim note flags
   (config/settings.json:91).
2. **Season-degenerate.** MAM and JJA carry **identical** `effective_bias_c` per city (e.g.
   Shanghai −0.97 for both, Tokyo −3.45 for both, SF −4.68 for both). The "season-keyed"
   correction is in practice a single city-level constant copied across seasons — it does not
   resolve June-specific bias.
3. **Magnitudes are trade-moving.** Tokyo −3.45 °C, Tel Aviv −4.0 °C, SF −4.68 °C shifts on
   member maxes move p_raw across bin boundaries materially, yet rest on n≈14 with no paired
   evidence. A wrong-sign or over/under-shoot here directly mis-prices the edge.
4. **HK JJA = no correction at all.** Pure raw cold-biased identity; the known ensemble cold
   bias (MEMORY: A4 cold-bias, Tokyo/TelAviv/Shanghai cold) flows straight into q untreated.

The system is already in **SHADOW** (`real_order_submit_enabled=false`) precisely because of
this — the A4 config note (settings.json:92) states the unshadow gate is per-city #24-vs-SETTLED
and capital is OFF. **This investigation confirms SHADOW is the correct posture: live JJA q is
NOT trustworthy enough to arm.** The "edge" on June markets is computed on identity calibration +
a 2-week city-constant bias shift — a pre-arm blocker.

---

## CORRECT WIRING / FIX (train/serve-consistency principle)

The governing principle: **the distribution we calibrate on must match the live forecast
product.** Here TIGGE and OpenData are the same IFS ensemble product (spec-proven), so a
TIGGE-fit JJA Platt IS train/serve-consistent for OpenData serving. The fix is therefore
"fit + promote on the existing TIGGE pairs," NOT "connect a separate TIGGE pipeline" and NOT
"leave identity."

**Option (i) — REFIT JJA Platt on the existing TIGGE pairs. CORRECT primary fix.**
- The data exists (≥135k JJA pairs/city, training_allowed=1, VERIFIED). Run
  `scripts/refit_platt.py --season JJA` across all live NH-summer + tropical cities to fill the
  ~8→full JJA coverage gap. This is task #54 (LOW) + #89 (HIGH refit) and is the right move.
- Pros: same product (no train/serve mismatch by definition); uses 2 years of real data instead
  of 2 weeks; produces a genuine season-resolved calibrator. Cons: must pass the OOS accept-gate
  (`src/calibration/oos_gate.py`); requires building the missing OOS-promote serving table so a
  fitted Platt can actually reach selection (today none is promoted, so fitting alone is inert).

**Option (ii) — connect a SEPARATE TIGGE-product calibration. REJECT.**
- There is no separate product. TIGGE already *is* the calibration product wired via
  `_TRANSFER_SOURCE_BY_OPENDATA_VERSION`. "Connecting TIGGE" as if new = re-doing what exists.

**Option (iii) — "identity + bias-correction is the intended JJA authority, resolved." REJECT
as a standalone answer.**
- Identity is the intended *fail-closed default*, not the intended *terminal authority*. The
  bias rows backing it are n≈14, n_paired=NULL, season-degenerate — not a trustworthy authority.
  Leaving it is the current SHADOW state, acceptable only because capital is OFF.

### Recommended sequence (decision-grade)

1. **Resolve the A4 lockstep contradiction first.** Today bias-correction FORCES identity even
   if a JJA Platt were fitted (event_reactor_adapter.py:3498) — because pairs were fit on
   *uncorrected* p_raw. To use a refit JJA Platt with bias-correction on, you must **refit the
   JJA Platt on the bias-CORRECTED p_raw domain** (matching the corrected serve domain), or run
   the two layers mutually exclusively. Pick one calibration domain and fit/serve in lockstep.
2. **Refit JJA Platt (option i)** on the chosen p_raw domain for all live cities, gated by
   `oos_gate.py`.
3. **Build the OOS-promote path** (the `platt_decision`/promote table the resolver expects) so a
   passing JJA Platt actually serves — otherwise fitting is inert and identity persists.
4. **Replace the degenerate bias layer** or fix it to run as the designed empirical-Bayes shrink
   (n_prior>0, paired delta populated, true per-season fit) so `effective_bias_c` stops being a
   2-week city constant.
5. **Keep SHADOW until** per-city JJA q-vs-SETTLED proves out (existing unshadow gate).

---

## Key file:line references

- `config/settings.json:92` — `edli_bias_correction_enabled=true`; A4 lockstep note (forces identity).
- `config/settings.json:91` — `bias_decay_kelly_haircut`; explicit data-insufficiency interim.
- `src/engine/event_reactor_adapter.py:3498-3503` — A4 lockstep FORCES identity-Platt on bias-corrected p_raw.
- `src/engine/event_reactor_adapter.py:3538-3562` — identity-Platt fallback when no fitted Platt (Path B / HK).
- `src/engine/event_reactor_adapter.py:3361-3420` — `_maybe_apply_edli_bias_correction` reads `model_bias_ens` VERIFIED weight_live>0, fail-closed.
- `src/calibration/platt_oos_resolver.py:4-13,40-52` — identity = live DEFAULT; fitted Platt is a CANDIDATE needing OOS PROMOTE.
- `src/data/calibration_transfer_policy.py:55-66` — `_TRANSFER_SOURCE_BY_OPENDATA_VERSION` (OpenData→TIGGE Platt; "physical identity identical").
- `docs/reference/zeus_math_spec.md:16,106-107,798` — X-side = TIGGE ECMWF ensemble; live = same 51-member; ENS bias = shrink TIGGE prior toward live residuals.
- `architecture/ecmwf_opendata_tigge_equivalence_2026_05_06.yaml:66-88` — same IFS ensemble, archive vs live channels; TIGGE Platt can calibrate OpenData.
- DB ground truth: `state/zeus-forecasts.db` calibration_pairs (47.5M tigge_mars / 613k ecmwf_open_data), platt_models_v2 (137 rows, all tigge_mars; JJA=8); `state/zeus-world.db` model_bias_ens (28 VERIFIED, n_live≈14, n_paired=NULL, MAM≡JJA per city).
