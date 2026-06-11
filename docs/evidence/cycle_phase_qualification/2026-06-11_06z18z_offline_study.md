# 06Z/18Z Cycle-Phase Qualification — Offline Settlement-Graded Study
<!-- Created: 2026-06-11 -->
<!-- Authority basis: operator directive 2026-06-11 ~05:00Z (cycle policy + 06/18Z deep offline
     investigation); docs/operations/consolidated_systemic_overhaul_2026-06-11.md
     §OPERATOR DIRECTIVES + K4.0b(d); docs/evidence/rule1_audits/2026-06-10_post_restart_stall_audit.md -->
<!-- Method: PURE OFFLINE. Live DBs READ-ONLY. No daemon/flag/src-live edits. Scratch DB only
     (state/cycle_phase_study.db). Producer: scripts/cycle_phase_offline_study.py
     (hydrate -> backfill -> materialize -> grade). Grade artifact: /tmp/grade_final2.json -->

## Question

Do the intermediate model cycles (06Z, 18Z) produce forecasts good enough to ever be admitted to
live trading alongside the synoptic cycles (00Z, 12Z)? The live cycle-phase gate currently holds
06/18Z posteriors SHADOW_ONLY (`replacement_forecast_cycle_policy.classify_cycle_phase`), and the
operator's standing rule is **live = 00Z + 12Z only** until evidence licenses otherwise.

Answered from backfilled history, settlement-graded, paired on the identical family-day — **no
live shadow-wiring** (operator: "不要再 shadow 验证…极易导致接线断裂").

## TL;DR verdict

**No skill defect found in 06Z or 18Z.** On identical settled cells of the one gradeable
family-day (target 2026-06-09, lead-1, full live fusion pipeline):

- **06Z vs 00Z** (n=19 paired Asia-Pacific cells): LogLoss 1.648 vs 1.664 (06Z marginally better;
  per-cell wins 12–7 for 06Z), modal-hit 0.316 vs 0.263, residual bias −0.05 °C vs +0.16 °C.
- **18Z vs 12Z** (n=18 paired Americas cells): LogLoss 1.610 vs 1.686 (18Z marginally better;
  wins 11–7 for 18Z), modal-hit tied 0.556, residual MAD-σ 0.67 vs 0.74 °C.
- Certified-bounds honesty and simulated after-cost buy_no economics show **parity** everywhere.

**The binding 06/18Z problem is NOT forecast quality — it is publication timing.** At the
lead-1 pre-day decision regime with the true ~+8 h publication lag:
06Z is the **most** pre-day-usable cycle of all (55/56 cells), while 18Z (publishes ~02:00Z on
the target day) is pre-day-usable for only 18/56 cells (the Americas). 12Z itself loses 22/56
(intraday for Asia), and 00Z loses 3/56 to the 30 h staleness law (far-west-UTC cities).

**Overall: QUALIFIED-AT-PARITY on every skill metric, but on a single family-day** — sufficient
to rule out the "intermediate cycles are systematically degraded" hypothesis at this n, NOT yet
sufficient as the sole basis for standing live admission. The backfill machinery now exists; the
cheap path to a decision-grade sample is to re-run `backfill+materialize+grade` for each new
settled day.

| Metric | 06Z vs 00Z | 18Z vs 12Z | Verdict |
|---|---|---|---|
| (a) certified-bounds honesty | band straddles reality (220 bin-rows) | band straddles reality (198 vs 374 bin-rows) | **QUALIFIED** (no dishonesty; weakly differentiating — see note) |
| (b) LogLoss (settled bin) | 1.648 vs 1.664, Δ=−0.016, n=19 | 1.610 vs 1.686, Δ=−0.076, n=18 | **QUALIFIED** (parity; CI includes 0) |
| (c) modal-bin hit rate | 0.316 vs 0.263, n=19 | 0.556 vs 0.556, n=18 | **QUALIFIED** (parity) |
| (d) buy_no after-cost win-rate | 0.682 (n=22 trades) vs 0.667 (n=24) | 0.722 (n=18) vs 0.730 (n=37) | **QUALIFIED** (parity) |
| (e) fused-center residual | −0.05±1.22 °C vs +0.16±1.17 °C | −0.21±0.67 °C vs −0.07±0.74 °C | **QUALIFIED** (no de-bias pathology) |
| sample breadth | 1 family-day, 1 weather regime | 1 family-day | **INSUFFICIENT_DATA** for standing admission |

## Method

### Faithfulness

Posteriors were re-materialized through the **exact live code path**
(`src.data.replacement_forecast_materializer.materialize_replacement_forecast_shadow`, imported and
pointed at a scratch SQLite DB) under the **live config flags** (`replacement_0_1_u0r_fusion_enabled=True`,
`replacement_0_1_fused_q_shape_enabled=True`, `edli_settlement_sigma_floor_enabled=True`,
`replacement_0_1_member_vote_smoothing_enabled=True`, EB-bias OFF). The scratch DB
(`state/cycle_phase_study.db`) carries verbatim copies (IDs preserved) of the live
`raw_model_forecasts` (the multi-model U0R-fusion substrate), `raw_forecast_artifacts`,
`source_run_coverage`, `source_run`, `market_events`, and `settlement_outcomes`, so a scratch
posterior is **byte-faithful** to what the live pipeline would have produced from the same cycle's
inputs. EB-bias / settlement-sigma-floor lookups read the live world DB read-only (flag-gated).
All materializer gates (artifact identity, AIFS 51-member/step coverage, OM9 local-day coverage,
DAY0/staleness) ran un-weakened.

### Backfill transport (zero new suppliers)

- **AIFS-ENS GRIB**: ECMWF open-data mirror failover (azure/ecmwf/aws) via
  `retrieve_aifs_ens_open_data_request` with explicit `forecast_date`/`cycle_hour`. Mirror
  retention confirmed at study time: the 2026-06-08 cycles (3 days back) still served.
- **OM9 9 km anchors**: run-pinned single-runs API via `fetch_openmeteo_ecmwf_ifs9_anchor_payload`
  with explicit `run=`. Confirmed serving 06-08T00/06/12Z runs.
- 174 artifacts backfilled (6 AIFS GRIBs ≈1.5 GB + 168 OM9 payload/precision files) under
  `state/cycle_phase_study_raw/`, each registered in the scratch `raw_forecast_artifacts` with
  cycle provenance + sha256 via the canonical manifest writer.

### Decision-time model (faithful, pre-day)

For each (settled target 2026-06-09 × phase × lead-1 cycle 2026-06-08 × city × metric), the
decision instant is pinned to **one minute before the target local-day window opens** (the
"evening before" lead-1 regime), with dependency availability set to the **true publication lag**
(cycle + 8 h; K4.0b "AIFS-ENS publishes ~+8h" — NOT the late backfill-capture timestamp; the
scratch AIFS artifact row is re-stamped to match, scratch-only). A phase whose data publishes
AFTER the window opens is **honestly an intraday decision**: the materializer's
`DEPENDENCY_AFTER_COMPUTED_AT` gate blocks it, and that block is itself a phase-quality result
(the timing penalty, reported below). The 30 h bounded-staleness law forces lead-1 cycles — the
same regime live trades under.

### Metrics

For the settled bin (the market bin containing the VERIFIED `settlement_value`):
(a) certified-bounds coverage + aggregate bound-honesty; (b) LogLoss `−ln q(settled)`;
(c) modal-bin hit rate; (d) simulated after-cost buy_no win-rate — for each off-modal bin whose
certified NO edge (`no_lcb = 1 − q_ucb(bin)` minus executable NO ask `1 − YES_ask`) cleared
`ts = 0.03`; fee `0.05·p·(1−p)·shares` (wallet-history fee law); DIRECTION LAW enforced;
(e) fused-center residual `(settled°C − μ*)` mean + MAD-σ. Cells with n<10 are INSUFFICIENT.

## Sample reality (the binding limitation)

Settlement truth covers targets 2026-06-07/08/09 (~47–49 high + 7–8 low cities each; 06-10 still
settling at study time). **The lead-1 multi-model fusion substrate (`raw_model_forecasts`
single_runs) exists ONLY for target 2026-06-09** (00Z: 18 cities, 06Z: 19, 12Z: 49, 18Z: 49 —
high). Targets 06-07/08 have no lead-1 substrate (live persistence began 06-08), so their
posteriors degrade to single-anchor (no fused-q, no bounds) — ungradeable for the certified
metrics. **The settlement-gradeable fusion-bearing family-day is 2026-06-09 only.**

## Results

### Materialization outcome (target 2026-06-09, lead-1 cycle 2026-06-08, 56 settled cells/phase)

| Phase | attempts | READY | fused (bounds) | blocked: intraday | blocked: staleness |
|---|---|---|---|---|---|
| 00Z | 56 | 53 | 20 | 0 | 3 |
| 06Z | 56 | 55 | 20 | 1 | 0 |
| 12Z | 56 | 34 | 34 | 22 | 0 |
| 18Z | 56 | 18 | 18 | 38 (19 of these also lacked full OM9 local-day cover) | 0 |

Timing reading: at lead-1 pre-day decisions, **06Z is the most usable cycle** (publishes ~14:00Z,
before every city's local midnight). 18Z (publishes ~02:00Z target-day) reaches only the Americas
pre-day. 12Z loses Asia. 00Z loses far-west-UTC cities to the 30 h staleness law. Fused-cell
counts for 00/06Z are capped at ~20 by the historical single_runs substrate, not by the phase.

### Per-phase metrics — all READY cells (same family-day; city subsets differ by honest gates)

| Phase | n_all | n_fused | LogLoss(all) | LogLoss(fused) | modal(fused) | buy_no n / WR / PnL | resid mean / MAD-σ (°C) |
|---|---|---|---|---|---|---|---|
| 00Z | 53 | 20 | 2.243 | 1.643 | 0.300 | 24 / 0.667 / +0.43 | +0.157 / 1.166 |
| 06Z | 55 | 20 | 2.288 | 1.646 | 0.300 | 22 / 0.682 / +0.90 | −0.045 / 1.223 |
| 12Z | 34 | 34 | 1.586 | 1.586 | 0.559 | 37 / 0.730 / +4.51 | −0.074 / 0.738 |
| 18Z | 18 | 18 | 1.610 | 1.610 | 0.556 | 18 / 0.722 / +1.28 | −0.205 / 0.671 |

(buy_no PnL = sum over 1-share simulated trades, after fee. The 12/18Z-vs-00/06Z LogLoss(all) gap
is a substrate artifact: 00/06Z's extra 33–35 cells are single-anchor fallback (no fusion), which
is exactly the q-mode the live gate refuses anyway — the fused columns are the live-relevant ones.)

### Decision-grade pairwise comparison — fused vs fused on IDENTICAL cells

The all-4-phase strict intersection (15 cells) would compare 00/06Z *single-anchor* q against
12/18Z *fused* q — a substrate confound, not a phase property. The fair comparisons are each
intermediate against its neighboring synoptic cycle on common fused cells:

**06Z vs 00Z — n=19 identical cells (Asia-Pacific: Beijing, Tokyo×2, Seoul×2, Shanghai×2, Busan,
Chengdu, Chongqing, Guangzhou, Kuala Lumpur, Lucknow, Manila, Qingdao, Shenzhen, Singapore,
Taipei, Wuhan):**

| metric | 00Z | 06Z | Δ (06Z−00Z) |
|---|---|---|---|
| LogLoss | 1.664 | 1.648 | **−0.016** (06Z better; per-cell wins 12–7) |
| modal hit | 0.263 | 0.316 | +0.053 |

**18Z vs 12Z — n=18 identical cells (Americas: NYC×2, Miami×2, Atlanta, Austin, Buenos Aires,
Chicago, Dallas, Denver, Houston, LA, Mexico City, Panama City, SF, São Paulo, Seattle, Toronto):**

| metric | 12Z | 18Z | Δ (18Z−12Z) |
|---|---|---|---|
| LogLoss | 1.686 | 1.610 | **−0.076** (18Z better; per-cell wins 11–7) |
| modal hit | 0.556 | 0.556 | 0.000 |

Neither delta is statistically distinguishable from zero at these n (sign test p≈0.18 / 0.24
one-sided) — the claim is **parity**, with point estimates mildly favoring the intermediates.
(06Z vs 12Z common cells: n=1 — INSUFFICIENT, not reported.)

### Certified-bounds honesty

The per-cell "settled bin's q within [q_lcb,q_ucb]" rate is 1.0 at every phase but is
**vacuous-by-construction** (the materializer clips q_lcb ≤ q_point ≤ q_ucb). The meaningful
aggregate check — the mean certified band must straddle realized frequency over all (cell,bin)
rows — **passes at every phase**: mean(y)=0.0909 inside [mean lcb, mean ucb] =
[0.0221, 0.171] (00Z, 220 rows), [0.0234, 0.170] (06Z, 220), [0.0240, 0.169] (12Z, 374),
[0.0227, 0.171] (18Z, 198). No intermediate-phase bound dishonesty; the check is honest but
weakly phase-differentiating at this n.

### Simulated buy_no economics (certified edge ≥ ts=0.03, fee law, direction-law enforced)

| Phase | trades | wins | win-rate | after-cost PnL (1 share/trade) |
|---|---|---|---|---|
| 00Z | 24 | 16 | 0.667 | +0.43 |
| 06Z | 22 | 15 | 0.682 | +0.90 |
| 12Z | 37 | 27 | 0.730 | +4.51 |
| 18Z | 18 | 13 | 0.722 | +1.28 |

All four phases ≥10 trades; intermediate phases sit at parity with their synoptic neighbors
(the 12Z PnL outlier is cohort composition — EU+Americas books — not phase skill).

## Per-metric verdicts

- **(a) certified-bounds coverage/honesty: QUALIFIED** — aggregate band straddles reality at both
  intermediate phases, indistinguishable from synoptic. (Per-cell variant vacuous; noted.)
- **(b) LogLoss: QUALIFIED (parity)** — 06Z −0.016 vs 00Z (n=19), 18Z −0.076 vs 12Z (n=18); both
  point-favor the intermediate; neither significant.
- **(c) modal-bin hit: QUALIFIED (parity)** — 06Z +5pp vs 00Z; 18Z tied with 12Z.
- **(d) buy_no after-cost win-rate: QUALIFIED (parity)** — 0.682/0.722 vs 0.667/0.730, all n≥18.
- **(e) fused-center residual: QUALIFIED** — no intermediate-phase bias shift (06Z |bias| 0.05 °C
  < 00Z's 0.16 °C; 18Z −0.21 °C within noise at n=18); MAD-σ comparable. The cycle-policy concern
  ("de-bias trained on ~99% 00Z history misapplies across phase") is **not detectable** at this n.
- **Sample breadth: INSUFFICIENT_DATA** — one family-day, one weather regime, n=18–19 paired
  cells per comparison. This study rules out a gross intermediate-cycle defect; it cannot, alone,
  power a standing live admission.

## Recommendation (operator decision; no flag was flipped)

1. Evidence supports **no skill disqualifier** for 06Z/18Z. The dominant real difference is
   **publication timing**, which already self-enforces through the materializer gates: 06Z would
   add the most usable pre-day refreshes (it covers exactly the cycle dead-zone the RULE-1 audit
   identified); 18Z is structurally an Americas-only lead-1 cycle.
2. To reach decision-grade n: re-run `scripts/cycle_phase_offline_study.py backfill+materialize+grade`
   per newly settled day (mirror retention ~4 days requires running within ~2 days of the cycle).
   ~10–15 settled days gives n≈200+ paired cells per comparison — enough for a CI-gated verdict.
3. If/when admitted, 06Z is the natural first candidate (near-universal pre-day usability,
   parity skill); the cycle-phase gate flip remains the operator's call on the accumulated sample.

## Limitations

- **Single family-day (2026-06-09)** — the only target with lead-1 fusion substrate across all
  phases (live single_runs persistence began 2026-06-08). All deltas are one-day point estimates.
- **Geographic confound is structural, not sampled**: 06Z↔00Z is graded on Asia-Pacific cells,
  18Z↔12Z on Americas cells — each pair like-for-like internally, but the two pairs are not
  comparable to each other.
- **Backfill vs live timing** — recorded `source_available_at` is late-capture; the study
  substitutes the measured ~+8 h publication lag uniformly across phases. Live would also pay
  download/queue latency; per-leg measured lags (K4.0b-b) would refine the usability table.
- **Provider completeness** — backfilled cycles fused at 4/5 decorrelated providers (UKMO absent
  from the historical single_runs capture) → all fused cells are `FUSED_NORMAL_PARTIAL`; live
  current cycles often reach 5/5, so live bounds would be marginally tighter than studied.
- **buy_no price proxy** — NO ask reconstructed as `1 − YES_ask` from the latest executable YES
  snapshot before `target_date T00:00Z` (phase-consistent cutoff); depth/min-order not enforced
  (skill grading, not a fill simulation).
- **low-metric n** — only 2–4 low cells per phase carry fusion; the tables are high-dominated.
- **Per-cell coverage metric vacuous** (materializer clip) — replaced by the aggregate
  band-straddle check; a sharper per-cell calibration check needs a different bound construction
  and is out of scope for this offline study.

## Reproduction

```
.venv/bin/python scripts/cycle_phase_offline_study.py hydrate --force
.venv/bin/python scripts/cycle_phase_offline_study.py backfill --target-date <D> --phases 0 6 12
.venv/bin/python scripts/cycle_phase_offline_study.py materialize --targets <D> --metrics high low
.venv/bin/python scripts/cycle_phase_offline_study.py grade --output-json <out>
```

Scratch DB: `state/cycle_phase_study.db` (never the live DBs). Raw backfill artifacts:
`state/cycle_phase_study_raw/`. Grade JSON used for this report regenerable via the commands above.
