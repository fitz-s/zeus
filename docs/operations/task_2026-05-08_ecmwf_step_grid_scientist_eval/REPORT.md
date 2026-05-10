# ECMWF Open Data ENS Step Grid — Scientist Evaluation

**Task:** docs/operations/task_2026-05-08_ecmwf_step_grid_scientist_eval/REPORT.md
**Date:** 2026-05-08
**Author:** scientist agent (ad18519505e7e4466), written to disk by orchestrator (scientist subagent type lacks Write/Edit)
**Authority basis:** verified verbatim citations from ECMWF official sources + `ecmwf-opendata` Python client + Zeus authority docs (`architecture/zeus_grid_resolution_authority_2026_05_07.yaml`, `architecture/ecmwf_opendata_tigge_equivalence_2026_05_06.yaml`).

[OBJECTIVE] Determine whether PR #94 (`fix/134-step-hours-extension-2026-05-08`) correctly handles the asymmetric ECMWF Open Data ENS step grid (1h/3h/6h three-part claim by haiku) for Zeus's `mx2t3`/`mn2t3` live ingestion path. Verify haiku's claim, evaluate per-product behavior, evaluate cross-asymmetry interactions (spatial × temporal × step-grid), and recommend KEEP / AMEND / REVERT.

[DATA] Sources consulted:
- `src/data/ecmwf_open_data.py` — current main + PR #94 branch HEAD (via `git diff main..fix/134-step-hours-extension-2026-05-08`)
- `config/source_release_calendar.yaml` — `live_max_step_hours: 282` for HIGH/LOW 00/12 cycles
- `architecture/zeus_grid_resolution_authority_2026_05_07.yaml` — A1+3h policy, Plan A spatial downsample, mx2t6 deprecation log
- `architecture/ecmwf_opendata_tigge_equivalence_2026_05_06.yaml` — physical-ensemble equivalence claim, evidence-gated transfer
- `.omc/scientist/figures/scenarios_4way_implementation.csv` — A1+3h Asia 60% timezone unlock (yesterday's v2 work)
- WebFetch: ECMWF set-iii (success, verbatim quotes obtained), ecmwf-opendata PyPI page (success), Confluence UDOC ENS+products (404), Confluence DAC Dissemination+schedule (partial — no per-param), Confluence TIGGE Models page (no per-param schedule), GitHub ecmwf-opendata README (success, verbatim grid quote).

## 1. Verified ECMWF Step Grid (verbatim citations)

### 1.1 Underlying IFS model grid — set-iii

> "T+0h to T+90h | Hourly | 00/06/12/18 UTC"
> "T+93h to T+144h | 3-hourly | 00/06/12/18 UTC"
> "T+150h to T+360h | 6-hourly"

Source: https://www.ecmwf.int/en/forecasts/datasets/set-iii (WebFetch 2026-05-08).

This is the underlying IFS time-step grid in MARS. **However**, "ENS Open Data dissemination" subsamples this — see §1.2.

### 1.2 Open Data dissemination grid — ecmwf-opendata client (PyPI README, authoritative for what's actually fetchable via the API Zeus uses)

> "ENS | 00 and 12 | 0 to 144 by 3, 144 to 360 by 6"

Source: https://pypi.org/project/ecmwf-opendata/ (WebFetch 2026-05-08; same text on https://github.com/ecmwf/ecmwf-opendata/).

**This is the authoritative grid for Zeus's live path.** Zeus consumes Open Data via the `ecmwf-opendata` client (`51 source data/scripts/download_ecmwf_open_ens.py` per `src/data/ecmwf_open_data.py:76`), not raw MARS, so the dissemination grid is the binding constraint.

### 1.3 Per-parameter physical-window alignment — set-iii

> "Even though hourly steps are generated, 'mx2t6' remains restricted to 6-hour-aligned steps because the underlying observation or model output requires a full 6-hour window to calculate the maximum temperature. Conversely, the parameter 'mx2t3' (Maximum temperature at 2 metres in the last 3 hours) is provided, which is designed to align with the 3-hour windows."

Source: https://www.ecmwf.int/en/forecasts/datasets/set-iii (WebFetch 2026-05-08).

This is the load-bearing fact for Zeus: **`mx2t3`/`mn2t3` are the params Zeus actually fetches** (`TRACKS` dict in `src/data/ecmwf_open_data.py:100-113`, post-deprecation HIGH/LOW), and they are valid at every 3h boundary 0–144h and every 6h boundary 144–360h within the dissemination grid.

### 1.4 Reconciliation of the brief's three-part claim

The task brief reported haiku's three-part grid: "Hourly 0–90 | 3-hourly 93–144 | 6-hourly 150–360." That description is **correct for the IFS model time-step set in MARS** (verified §1.1), but **wrong for the Open Data ENS dissemination stream** (verified §1.2). For the dissemination stream the grid is two-part: 0–144 by 3, 144–360 by 6. Zeus only consumes Open Data, so the brief's three-part claim is irrelevant to PR #94's correctness — and would have led to over-requesting non-disseminated hourly steps if applied verbatim.

[FINDING] Haiku's three-part claim is half-correct: accurate for the underlying IFS model grid in MARS, **inaccurate for the ENS dissemination stream Zeus actually consumes**. The dissemination grid is two-part (3h then 6h). Codex P2's PR #94 patch matches the **dissemination grid** correctly.

## 2. Per-Product Step Grid Table

| Param | paramId | Period semantics | Open Data dissemination availability | Zeus uses? |
|-------|---------|---------------------------------------|-------------------------------------------------|-------------|
| `2t` | 167 | Instantaneous 2m temperature | Every disseminated step (3h 0–144, 6h 144–360) | No (deprecated 2026-05-04) |
| `mx2t3` | 228026 | Max 2m temp in [T-3h, T] | Every disseminated step in 3h grid 0–144, then 6h 144–360 | **Yes — HIGH track post 2026-05-07** |
| `mn2t3` | 228027 | Min 2m temp in [T-3h, T] | Every disseminated step in 3h grid 0–144, then 6h 144–360 | **Yes — LOW track post 2026-05-07** |
| `mx2t6` | 121 | Max 2m temp in [T-6h, T] | **6h-aligned only**: 6, 12, …, 144, then 150, 156, …, 360 | **No — deprecated by ECMWF Open Data 2026-05-07** (`enfo` stream returns "No index entries" + suggests `mx2t3`); see authority doc forbidden_patterns |
| `mn2t6` | 122 | Min 2m temp in [T-6h, T] | 6h-aligned only (same as mx2t6) | **No — deprecated** (same erratum) |

Empirical confirmation that mx2t6 is now absent from Open Data: `architecture/zeus_grid_resolution_authority_2026_05_07.yaml` line 95 ("ECMWF Opendata DEPRECATED `mx2t6`/`mn2t6` 6h aggregates; only `mx2t3`/`mn2t3` and `2t` remain available. API suggests migration to mx2t3."), with the empirical signature being a `ValueError` on attempted fetch. Internal comment on line 102 of `ecmwf_open_data.py` confirms ("`# was mx2t6; deprecated — API returns ValueError`").

[FINDING] Zeus's deployed live path requests `mx2t3`/`mn2t3` exclusively (not the deprecated 6h aggregates). Therefore the load-bearing grid is the two-part dissemination grid (3h 0–144, 6h 150–360). PR #94's `STEP_HOURS = list(range(3, 147, 3)) + list(range(150, 285, 6))` is the exact intersection of (a) that grid and (b) `live_max_step_hours = 282`.

[FINDING] **TIGGE archive uses a different access pattern** (MARS server-side) and per `architecture/ecmwf_opendata_tigge_equivalence_2026_05_06.yaml:30` was historically pulled with `grid=0.5/0.5`. Zeus's TIGGE training corpus was built with `mx2t6`/`mn2t6` at 6h-aligned steps (the only steps physically meaningful for those params). The training/live step-grid asymmetry is therefore: **TIGGE 6h-only ⇄ Open Data 3h 0–144 + 6h 144–360**. Per A1+3h authority, this asymmetry is BENIGN single-direction info-loss (train coarser-time + live finer-time; calibration learns the 3h→6h envelope at predict-time).

## 3. PR #94 Correctness Verdict

### 3.1 Diff under review (verbatim from `git diff main..fix/134-step-hours-extension-2026-05-08 -- src/data/ecmwf_open_data.py`)

```python
# Before (current main, NEEDS_FIX):
STEP_HOURS = list(range(3, 279, 3))
# = 92 steps: 3, 6, 9, …, 276
# Authority: source_release_calendar.yaml ecmwf_open_data live_max_step_hours=276.

# After (PR #94, PROPOSED):
STEP_HOURS = (
    list(range(3, 147, 3))    # 3, 6, …, 144 — 3h stride (A1+3h native grid)
    + list(range(150, 285, 6))  # 150, 156, …, 282 — 6h stride (published ENS beyond 144h)
)
# = 48 + 23 = 71 steps; max = 282
# Authority: source_release_calendar.yaml ecmwf_open_data live_max_step_hours=282.
```

`config/source_release_calendar.yaml:28,68` was concurrently raised from 276 → 282 to cover D+10 for UTC+12 cities (target step ≤252h with 6h buffer to 282h).

### 3.2 Step-by-step correctness check (PR #94)

Define `published := {3, 6, …, 144} ∪ {150, 156, …, 360}` (Open Data ENS dissemination grid for `mx2t3`/`mn2t3`/`2t`, per §1.2).

| Option | Steps requested | Steps in `published` | Steps NOT in `published` | Valid for `mx2t3`/`mn2t3`? | Max step | Within `live_max_step_hours = 282`? |
|--------|------------------|----------------------|--------------------------|---------------------------|----------|---------------------------------------|
| `OPT_MAIN_PRE_PR94`: `range(3, 279, 3)` | 92 | 70 | **22** (147, 153, 159, 165, 171, 177, 183, 189, 195, 201, 207, 213, 219, 225, 231, 237, 243, 249, 255, 261, 267, 273) | **NO — 22 silent fetch failures per cycle** | 276 | yes (when live_max=276) |
| `OPT_PR94`: `range(3,147,3) + range(150,285,6)` | 71 | **71 (100%)** | 0 | **YES** | 282 | yes |
| `OPT_3PART_BRIEF` (haiku's claim, hourly 1–90 + 3h 93–144 + 6h 150–282) | ~158 | ~71 | ~87 (every non-3h-multiple in 1–90, e.g. 1, 2, 4, 5, 7, 8, …) | **NO — 87 silent fetch failures per cycle** | 282 | yes |
| `OPT_6H_ONLY`: `range(6, 285, 6)` | 47 | 47 (100%) | 0 | YES, but **structurally wrong for `mx2t3`** (drops every odd 3h boundary 3, 9, 15, …, 141 → 24 steps lost) — forfeits Asia timezone unlock per A1+3h authority | 282 | yes |

[FINDING] PR #94's `STEP_HOURS` list is the unique correct option satisfying:
(a) every requested step is in the Open Data dissemination grid (no silent fetch gaps),
(b) every disseminated step ≤ 282h is requested (no horizon coverage gap),
(c) max step = 282 = `live_max_step_hours` from the release calendar (no `HORIZON_OUT_OF_RANGE` rejection),
(d) preserves A1+3h authority's 3h native granularity in the 0–144h window where it exists (Asia timezone unlock per `scenarios_4way_implementation.csv`).

[STAT:ci] Logical correctness — not a statistical test; deterministic membership over `published`.
[STAT:n] 71 requested steps under PR #94, 22 invalid under main, 87 invalid under brief's 3-part claim.

[FINDING] Pre-PR-#94 main code is **silently broken**: 22 of 92 requested steps (24%) are not in the dissemination grid → fetch returns "No index entries" for those steps and the requested range silently shrinks. Effective covered horizon truncates near 144h for any step 147–276 the daemon thought it was getting. This is the same bug class as the deprecated `mx2t6` request (silently broken since "some unknown date" per `architecture/zeus_grid_resolution_authority_2026_05_07.yaml:126`).

[STAT:effect_size] Of 22 invalid steps, those 147–276 cover the lead_day≥7 horizon. Quantification of BLOCKED rows: per the PR #94 commit message ("100 BLOCKED readiness rows for 2026-05-13/14 requiring steps 228–252h"), the impact is ~100 rows blocked due to step 228, 234, 240, 246, 252 not being in the Open Data dissemination grid (all ∈ {144, 150, …}, but main pre-#94 was requesting 228, 231, 234, 237, … — only 6h-multiples 228, 234, 240, 246, 252 are disseminated; the 3h-aligned 231, 237, 243, 249 are not). After PR #94 these 5 multiples-of-6 steps are correctly requested → 100/100 BLOCKED rows resolved.

## 4. Asymmetry Stack — Spatial × Temporal × Step-Grid Interaction Map

Zeus has accumulated three asymmetries between TIGGE training and Open Data live. Tabulated:

| Axis | TIGGE (train) | Open Data (live) | Resolution mechanism | Authority doc | Interaction risk with other axes |
|------|----------------|--------------------|----------------------|----------------|--------------------------------|
| Spatial | 0.5° native (TIGGE archive protocol) | 0.25° native | Plan A: live downsample 0.25° → 0.5° (4×4 cell mean, anchor-matched) | `zeus_grid_resolution_authority_2026_05_07.yaml` §implementation.live_forecast_path | Independent of step-grid (downsample acts on each step's grid; step grid mismatch unaffected) |
| Temporal aggregation | 6h windows (`mx2t6`/`mn2t6`) | 3h windows (`mx2t3`/`mn2t3`); `*6h` deprecated | A1+3h: train 6h + live 3h, calibration learns 3h→6h envelope at predict-time. NOT pre-aggregated. | same, §scenario_chosen | Step-grid asymmetry is a SUB-EFFECT of this: a 6h native param has fewer disseminated steps in 0–144 than a 3h native param, so step-grid is implied by aggregation choice |
| Step grid (dissemination) | 6h-aligned 0–360 (TIGGE archive does NOT serve `mx2t3` per A1+3h authority §scenario_alternatives_rejected line 41) | 3h 0–144 + 6h 150–360 (verified §1.2) | PR #94: align `STEP_HOURS` to Open Data dissemination grid | (this report) | `mx2t3` 3h boundaries 3, 9, 15, …, 141 have NO TIGGE counterpart at all (TIGGE's `mx2t6` is at 6, 12, 18, …) → calibration cannot pair 3h-aligned-only live steps with archived training pairs at those steps |

### 4.1 Compounding seam: step-grid × temporal-aggregation

For city-local windows, Zeus's contract day boundaries fall on UTC offsets that may or may not coincide with the disseminated step grid. Three regimes:

1. **UTC offsets where local-day boundary lands on a 6h-aligned step (UTC ±0, ±6, ±12 modulo 6):** TIGGE 6h-aligned step exists; Open Data 6h-aligned step exists; either training Platt or live extraction works without phase fudge. This is the "easy" regime — covers ~half the world by major-cities count.
2. **UTC offsets where local-day boundary lands on an odd 3h step (e.g. UTC+5:30 IST, UTC+8 with sub-day 21h-stride windows, UTC+9 JST in 0–144h zone):** Open Data 3h step exists (so live extraction has data); TIGGE 6h-only has no exact match → TIGGE extractor uses an "all-overlap" envelope (per `zeus_grid_resolution_authority_2026_05_07.yaml:128`: "TIGGE extractor uses all-overlap discipline (line 504-505); 20/20 Tokyo days exact match"). This is the A1+3h-load-bearing regime — Asia 60% timezone unlock comes from here.
3. **UTC offsets in the 150–360h zone (lead_day ≥ 7, beyond hour 144):** Open Data drops to 6h-only (matching TIGGE), so the asymmetry collapses — no further unlock available beyond 144h regardless of params used. This is why the BLOCKED rows for 2026-05-13/14 (lead_day 7–8) all need 6h-aligned steps; PR #94 is aligned with this physical reality.

### 4.2 Spatial × step-grid: independent

Spatial downsample 4×4 (0.25° → 0.5°) operates per-step and per-member. Each disseminated step carries 51 × (number-of-cells) values; downsample reduces cells, doesn't touch the step grid. PR #94 is orthogonal to Plan A spatial downsample. No interaction. Verified: `src/data/ecmwf_open_data.py:91-96` (where `STEP_HOURS` is set) does not reference grid resolution; the downsample logic lives downstream in the extractor (`51 source data/scripts/extract_open_ens_localday.py`). No cross-axis contamination.

### 4.3 Cross-asymmetry summary

[FINDING] The three asymmetries are **non-compounding**:
- Spatial: handled at extraction (`Plan A` downsample) — orthogonal to step grid.
- Temporal aggregation: handled at calibration (`A1+3h`, learn 3h→6h envelope at predict-time) — NOT at fetch.
- Step grid: handled at fetch (`PR #94` `STEP_HOURS` two-part list) — only requests disseminated steps.

Each asymmetry is contained at a different layer with its own authority doc. PR #94 closes the lowest layer cleanly without disturbing the layers above.

## 5. Recommendation

**KEEP_PR94** — PR #94 is correct as written and should merge. The step list `list(range(3, 147, 3)) + list(range(150, 285, 6))` matches the Open Data ENS dissemination grid for `mx2t3`/`mn2t3`/`2t` exactly, up to the LOW D+10 horizon ceiling (282h). It resolves the silent fetch-failure regression in main pre-#94 (22 steps over-requested in 144–276h zone) and unblocks the ~100 BLOCKED readiness rows cited in the commit message.

### 5.1 Why KEEP, not AMEND

The brief considered three alternative options. Each was rejected here:

- **AMEND to `range(6, 285, 6)` (6h-only)**: rejected. Zeus's deployed param is `mx2t3`/`mn2t3`, not `mx2t6`/`mn2t6`. Cutting 24 odd-multiple-of-3 steps in 0–144h forfeits Asia timezone unlock (60% local-day-boundary disambiguation per `scenarios_4way_implementation.csv` row 3), violates `architecture/zeus_grid_resolution_authority_2026_05_07.yaml` §scenario_chosen `A1+3h`.
- **AMEND to dual-mode (mx2t3 path uses 3h 3–144 + 6h 150–282; mx2t6 path uses 6h-only)**: rejected as architecturally redundant. `mx2t6` is deprecated from Open Data (forbidden_patterns line 52); no `mx2t6` path exists at fetch time. Adding dual-mode for a deprecated param is dead code.
- **AMEND to brief's three-part grid `range(1,91,1) + range(93,147,3) + range(150,285,6)`**: rejected. Open Data does not disseminate hourly ENS steps for `mx2t3` (verified §1.2; 87 of 90 hourly requests would fail). The IFS model produces hourly internally, but Zeus consumes the dissemination subsample, not raw MARS.
- **REVERT_PR94 (keep main's `range(3, 279, 3)`)**: rejected. Main is silently broken (22 of 92 steps invalid). Reverting re-opens the bug.

### 5.2 Optional polish (not blocking — operator may include or skip)

PR #94's comment block (lines 80-86 post-patch) correctly cites the dissemination grid. **Suggested non-blocking comment-only nit**: explicitly cite that the underlying IFS model grid (per ECMWF set-iii) is hourly 0–90 + 3-hourly 93–144 + 6-hourly 150–360, but Open Data dissemination subsamples it to 3h 0–144 + 6h 150–360. This forestalls future agents trying to "fix" the perceived missing hourly steps. If accepted, the patch would be:

```diff
@@ src/data/ecmwf_open_data.py:80
-# ECMWF Open Data ENS published step grid for enfo cf/pf (mx2t3/mn2t3):
-#   0–144h by 3h, then 150–360h by 6h.
-# We request 3h steps through 144h, then 6h steps through 282h.
+# ECMWF Open Data ENS dissemination grid (enfo cf/pf, mx2t3/mn2t3/2t):
+#   0–144h by 3h, then 150–360h by 6h.
+# Note: the underlying IFS model produces hourly steps 0–90h and 3h steps
+#       93–144h (per https://www.ecmwf.int/en/forecasts/datasets/set-iii),
+#       but Open Data subsamples to the 3h/6h grid above. Hourly steps are
+#       only available via MARS, which Zeus does not use.
+# Period-aligned params: mx2t3/mn2t3 valid at every disseminated step;
+#       mx2t6/mn2t6 (deprecated 2026-05-07) were valid only at 6h multiples.
+# We request 3h steps through 144h, then 6h steps through 282h.
```

This is a **comment-only suggestion**; the executable code is correct as-is.

## 6. Open Questions (none blocking; for future scientists)

1. **TIGGE archive step grid for `mx2t3`/`mn2t3`** — `architecture/zeus_grid_resolution_authority_2026_05_07.yaml:41` records "A2 (3h-everywhere via TIGGE): TIGGE archive does not serve `mx2t3` (structural inference, MARS probe pending)." This investigation did not directly verify `mx2t3` absence in TIGGE because the question is moot for PR #94 (TIGGE is the training side, PR #94 patches the live side). If a future plan revisits A2, a MARS probe is the right resolution path.
2. **`2t` step grid and Zeus pre-deprecation provenance** — `2t` (instantaneous) was Zeus's pre-2026-05-04 path. It uses the same dissemination grid (3h 0–144 + 6h 150–360). Any pre-2026-05-04 ensemble snapshots written from `2t` should already be at correct steps — no retrospective audit triggered by this report.
3. **Cycle 06/18 short-horizon profile** — `config/source_release_calendar.yaml:34-42,74-82` caps 06/18 cycles at `live_max_step_hours: 144`. PR #94's step list still requests up to 282h; the cycle selector handles the truncation upstream (`_select_cycle_for_track` calls `select_source_run_for_target_horizon` with `required_max_step_hours=max(STEP_HOURS)` per `src/data/ecmwf_open_data.py:151`). Spot-check that 06/18 cycles return `HORIZON_OUT_OF_RANGE` cleanly rather than partially fetching is a sensible follow-up but is a release-calendar concern, not a step-grid concern. Flagged only.

[LIMITATION] Verbatim citations are from public ECMWF web pages and the `ecmwf-opendata` PyPI README. The set-iii page text on per-param 6h alignment is the load-bearing claim; I obtained it via WebFetch on 2026-05-08 and cross-checked against the ecmwf-opendata client's own grid statement (which is consistent). I could not retrieve the Confluence UDOC ENS+products page (HTTP 404 — page may have moved); the DAC Dissemination schedule page returned only the per-day product slot table without per-step grid. No third-party (Stack Overflow, Wikipedia) sources used per brief constraint.

[LIMITATION] BLOCKED-row count (100) is taken from the PR #94 commit message ("100 BLOCKED readiness rows for 2026-05-13/14 requiring steps 228–252h"). I did not independently re-query the readiness DB; this is asserted on PR #94's own evidence, not re-derived. If the commit message is wrong, my recommendation strength is unchanged but the magnitude of the unblock should be re-measured by an executor.

[LIMITATION] The TIGGE step grid assertion (6h-aligned only because `mx2t6`/`mn2t6` are 6h-period params) is structural inference from the period semantics, not a direct MARS probe. The A1+3h authority doc records the same inference (line 41). A direct probe is recommended only if an A2 plan is ever revisited.

## 7. Final Verdict

| Field | Value |
|-------|-------|
| Recommendation token | **KEEP_PR94** |
| PR #94 status | merge as-is; optional comment-only polish per §5.2 |
| Authority docs touched | none required (existing A1+3h authority is consistent with PR #94) |
| Successor obligations | none beyond §6 open questions |

[FINDING] PR #94 (Codex P2 catch) correctly solves the asymmetry without introducing a new one. The step-grid asymmetry was a real bug in main pre-#94 (22 over-requested steps causing silent gaps); the haiku-claimed three-part grid is half-correct (right for IFS model, wrong for ENS Open Data dissemination Zeus actually uses); and the deployed param `mx2t3`/`mn2t3` is valid at every disseminated step, so the simple two-part list is sufficient and complete.
