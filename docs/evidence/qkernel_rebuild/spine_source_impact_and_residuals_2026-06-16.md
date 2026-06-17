# Q-Kernel Spine — Source-Divergence Impact / Blast Radius + Confirmed Same-Class Residuals

Created: 2026-06-16
Last audited: 2026-06-16
Authority basis: fix commit `9ee1936148` (spine source fix); AGENTS.md "Probability Authority";
`docs/rebuild/consult_build_spec.md` [BLOCKER]; settlement evidence
`cold_center_bias_fix_2026-06-16.md`, `settlement_ev_verdict_2026-06-16.md`,
`modal_buyyes_drag_rootcause_2026-06-16.md`. READ-ONLY investigation; no edits.

The fixed defect: the LIVE Q-Kernel spine (`qkernel_spine_enabled=true`, the live decision
authority) built its forecast center `mu*` and dispersion `sigma` from `ensemble_snapshots`
(51 ECMWF-ENS perturbations of ONE model) instead of `raw_model_forecasts` (the multi-model
deterministic fusion over ~7-13 decorrelated providers). The Probability Authority mandates
`mu*` = T2 Bayesian precision fusion over decorrelated providers; `sigma_pred` = fusion variance.
Every co-traveler (strategy-of-record fusion, de-bias provider, Day0 lane, ARM-replay harness)
reads `raw_model_forecasts`; only the spine deviated, via accidental inheritance during a
`MU_SIGMA_NOT_STASHED` bug-chase. Fixed at `9ee1936148` by a new accessor
`_spine_multimodel_members_for_event` sourcing `raw_model_forecasts` latest-per-model at the
causal cycle.

---

# 1. IMPACT / BLAST RADIUS

## 1.a — The live window the spine ran the wrong ensemble center

The live spine has only ONE source-of-record file in `event_reactor_adapter.py`:
`raw_model_forecasts` first appears in that file ONLY at the fix commit `9ee1936148`
(`git log --first-parent -S "raw_model_forecasts" -- src/engine/event_reactor_adapter.py`
returns exactly one commit). Before the fix, every member-envelope read in the reactor —
including the canonical producer `_market_analysis_from_event_snapshot` (member source stamped
`"ensemble_snapshots.daily_extrema"`, `event_reactor_adapter.py:6566`) — drew from
`ensemble_snapshots`. Ensemble-sourcing was the *original and only* state of the live spine;
it was never a switch *from* `raw_model_forecasts`, it was an inheritance.

**Regression chain (first-parent mainline; the `MU_SIGMA_NOT_STASHED` bug-chase):**

| # | Commit | Date (-0500) | Role |
|---|--------|--------------|------|
| 1 | `067ba81693` | 2026-06-14 18:38:18 | "qkernel stage0: decision-receipt spine (observability before behavior change)" — READ-ONLY receipt scaffolding (`_edli_spine_*`), "ZERO decision/sizing/submit change." |
| 2 | `12218af479` | 2026-06-14 23:54:58 | W5B cutover flag `qkernel_spine_enabled` added, **default false**. |
| 3 | `febc328c30` | **2026-06-15 01:50:15** | **Flag flipped true.** Spine on the live decision path. Producer not yet stashing members → spine emitted `SPINE_INPUTS_UNAVAILABLE:MU_SIGMA_NOT_STASHED` universally; every family fell back to legacy → zero harvest. At this instant `raw_model_forecasts` count in the reactor file = 0; `ensemble_snapshots` count = 9. |
| 4 | `d07ed592fd` | 2026-06-15 07:14:13 | **The inheriting commit.** Wires the canonical ensemble-sourced `_market_analysis_from_event_snapshot` into `_generate_candidate_proofs` to stash `_edli_spine_*` members. The spine now prices live on the ensemble center. |
| 5 | `f237314fb6` (cherry-pick of `57316b3a76`) | 2026-06-16 00:57:38 | Introduces `_bound_forecast_snapshot_row_for_spine` (`event_reactor_adapter.py:11156`, reads `_authority_table_ref(conn,"ensemble_snapshots")` at :11187). Hardens the ensemble path past the reader-block gate; does NOT change the source. |
| 6 | `9ee1936148` | 2026-06-16 21:15:27 | **FIX** → `_spine_multimodel_members_for_event`, `raw_model_forecasts`. |

**Load-bearing admission (`d07ed592fd` commit message):**
"the live q path is the replacement provider-fused chain ... which carries an anchor-fused
posterior with NO debiased ensemble member envelope. So the rebuilt spine ... got
`SPINE_INPUTS_UNAVAILABLE:MU_SIGMA_NOT_STASHED` universally ... **The offline ARM replay
passed because it ran on member envelopes the live lane does not produce.**" — defect class (e):
"get any member envelope to the spine" silently inherited the wrong table; defect class (d):
the validation harness certified the live path while consuming a different source.

**False-provenance docstrings (defect class c), removed by the fix from `qkernel_spine_bridge.py`,
claimed:** "the reactor's chain-of-record-DEBIASED members ... This is the ARM-replay-validated
center+sigma" / "identical to the ARM-replay-validated path." The fix's replacement text states
reality: "those members are the RAW MULTI-MODEL member envelope sourced from
raw_model_forecasts ... They are RAW, NOT chain-of-record-debiased."

**The wrong-center window:**
- **Outer bound:** the live spine ran on the ECMWF-ensemble center from `febc328c30`
  (2026-06-15 01:50:15 -0500) to `9ee1936148` (2026-06-16 21:15:27 -0500) — ~1d 19h 25m.
- **Effective pricing window:** for the first ~5.4h after the flip (`febc328c30` → `d07ed592fd`)
  the member-stashing producer was absent, so the spine emitted `MU_SIGMA_NOT_STASHED` and fell
  back to legacy (zero harvest, no ensemble-priced trades). The spine actually *priced live on
  the ensemble center* from `d07ed592fd` (2026-06-15 07:14:13) to the fix at `9ee1936148`
  (2026-06-16 21:15:27): an effective wrong-center pricing window of **~1d 14h 1m**. The
  `f237314fb6` accessor (06-16 00:57) hardened that path mid-window without changing the source.

**Member-equivalence audit (213 settlement families):** 0/213 live-vs-replay member sets equal;
mean `|delta mu*|` = 1.14 deg C (max 5.35); live `mu*-realized` -0.64 cold vs validated -0.39.

## 1.b — Per-stage downstream corruption ledger

The corruption propagates through `mu*` (the center); everything center-dependent inherits the
cold shift. `sigma` is insulated in the live branch (pure leftward translation of the mass
profile, dispersion unchanged).

**Producer → center (the single corrupted input):**

| Stage | file:line | What the cold center does |
|---|---|---|
| Producer | `event_reactor_adapter.py::_generate_candidate_proofs` ~7723-7780 → `_bound_forecast_snapshot_row_for_spine` (pre-fix read `_authority_table_ref(conn,"ensemble_snapshots")` :11187) | Threads the ECMWF-ENS member envelope onto the payload (`_edli_spine_*_members_native`). SOLE injection point of the wrong source. Fixed by `_spine_multimodel_members_for_event` ~11174-11272 (now reads `raw_model_forecasts` latest-per-model at causal cycle). |
| Bridge lift | `qkernel_spine_bridge.py::_served_predictive_inputs:463`; `build_fresh_model_set:575` (`values = served["debiased_members_native"]`) | Wraps the threaded envelope into `FreshModelSet`. The `[min,max]` carried here is the ECMWF-ENS spread, which becomes the center's hard envelope bound. |
| Center (mu*) | `src/forecast/center.py::build_center:318` — `mu_consensus = weighted_huber_location(...)` (376-383); envelope `lo,hi = min/max(debiased_values)` (387-388); `assert lo <= mu_candidate <= hi` (342) | **PRIMARY CORRUPTION.** mu* is a convex combination LOCKED inside `[min(members), max(members)]`. Wrong (ECMWF-ENS) members → wrong envelope → mu* cannot escape to the warmer multi-model consensus. Single-model ENS daily-extreme members run cold vs realized highs; de-bias is no-op (`_NoOpDebiasAuthority`), so nothing rescues it. Center sits ~0.5-0.64 deg C cold; drag concentrated on metric=high. |

**Center → q → q_band → q_lcb:**

| Stage | file:line | How the cold center distorts it |
|---|---|---|
| joint_q (point q) | `src/probability/joint_q.py::build_joint_q:217` — `p = bin_probability_settlement(mu, sigma, lo_i, hi_i)`, Normal CDF folded with `(mu,sigma)` (257-279); clip>=0 + single renorm | Mass shifts cold. Each bin mass = `Phi((hi_i-mu)/sigma) - Phi((lo_i-mu)/sigma)`. Cold mu moves the Normal left: cold-side bins GAIN mass, warm/actual-settlement bins LOSE mass. sigma unchanged → pure leftward translation. |
| modal bin (forecast bin) | `family_decision_engine.py::forecast_bin_id:396` — `i = argmax(q)` | **MODAL BIN MIS-PLACED COLD.** argmax(q) lands one+ bins colder than the realized-settlement bin. Load-bearing distortion: the modal bin is the direction-law anchor; the true warm bin is no longer "the forecast bin." |
| joint_q_band / q_lcb | `joint_q_band.py::build_joint_q_band:363` — `mu_k = draw_mu(pd)` around the cold mu (416), `q_lcb = quantile(samples, alpha)` (429) | q_lcb inherits the cold shift. Every band draw is centered on the cold mu; q_lcb on cold bins inflated, on the true warm bin depressed. Band widens around the wrong center. |

**q_lcb → edge → direction → selection → sizing:** the cold-shifted q_lcb depresses edge on the
true warm bin and inflates it on cold bins, mis-placing the modal direction anchor →
buy_yes suppression + a 100%-buy_no favorites-losing book. Kelly binary sizing
(`qkernel_spine_bridge.py:1247-1287`) restamps `q_posterior`, `q_lcb_5pct`, `trade_score`,
and position size off the cold belief — the wrong center flows all the way into notional.

## 1.c — Realized settled P&L + loss attribution

Settlement-graded after-cost EV (`scripts/qkernel_settlement_ev_replay.py`, decision-time
snapshot cost, strict joins; grading window 2026-06-09..06-15, n=104-108). The BEFORE column is
the spine running its uncorrected cold center (the legacy empty-authority baseline reproducing
the live verdict); AFTER is the de-bias correction applied.

| metric | BEFORE (cold center) | AFTER (corrected) | move |
|---|---|---|---|
| aggregate mean after-cost EV | **+0.0180** | +0.0297 | +0.0117 |
| aggregate 95% CI | [-0.0530, +0.0854] | [-0.0450, +0.1034] | lower bound -0.053 → -0.045 |
| **modal EV** | **-0.0462** | +0.0523 | **flipped POSITIVE** |
| **buy_yes (neg_risk) EV** | **-0.0107** | -0.0004 | → ~0 |
| neg_risk_buy_no EV (core) | +0.0335 | +0.0430 | stayed positive, improved |
| tail EV | -0.0224 | +0.1511 | up |

Center-reconstruction (walk-forward, n=748 settled families 06-01..06-15): mean `mu*-realized`
= **-0.481 deg C** (cold), median -0.551, 66.2% of cells cold, 47 cold / 10 warm cells (n>=3),
metric=high mean -0.523 (drag concentrated on highs).

**Loss attribution:** the cold center directly produced the two negative drag classes —
modal EV -0.046 and buy_yes EV -0.011 — by mis-placing the modal bin cold and suppressing
buy_yes. The aggregate after-cost EV was driven to an INDETERMINATE +0.018 (CI [-0.053, +0.085]
spanning 0) instead of the corrected +0.030. The verdict over the window is INDETERMINATE in
both BEFORE and AFTER (CI spans 0 at n~104), so the dollar loss is bounded by the negative drag
on the modal + buy_yes legs rather than a proven aggregate negative; the realized harm is the
favorites-losing book composition (100% buy_no, buy_yes win-rate 0.031) the cold center forced.

---

# 2. CONFIRMED SAME-CLASS RESIDUAL DEFECTS

`git show 9ee1936148 --name-only` = exactly 3 files (`event_reactor_adapter.py`,
`qkernel_spine_bridge.py`, `tests/integration/test_qkernel_spine_sources_multimodel.py`).
The exit/monitor lane and the canonical Stage-0 stash were NOT migrated. Both confirmed below
are defect_class = **source_divergence** (the headline class).

| Severity | Title | file:line | Live-impact | Status |
|---|---|---|---|---|
| **HIGH** | EXIT/monitor belief lane builds exit q from `ensemble_snapshots` while ENTRY spine now uses `raw_model_forecasts` | `executable_forecast_reader.py:855` (+ SQL 568/586/642); `monitor_refresh.py:818,237` | Live | LIVE-AFFECTING |
| **MED** | Dead canonical spine stash derives `_edli_spine_*` from `ensemble_snapshots.members_json`; pre-empts the fix when the replacement-authority flag is OFF | `event_reactor_adapter.py:~11897-11978` (stash), 7723-7726 (skipped guard), 10464-10465 (flag-off fall-through) | Latent / flag-gated | LATENT, RE-ARMS ON FLAG FLIP |

## defect_class: source_divergence

### [HIGH] EXIT/monitor belief lane reads ensemble_snapshots while entry uses raw_model_forecasts

- **file:line:** `src/data/executable_forecast_reader.py:855` (`members = _members(row["members_json"])`,
  SQL `FROM ensemble_snapshots` at 568/586/642, `SOURCE_TABLES` literal "ensemble_snapshots" 43/49,
  `to_ens_result:134`); `src/engine/monitor_refresh.py:818`
  (`_refresh_ens_member_counting` → `_monitor_ens_from_executable_reader:585` →
  `read_executable_forecast`), `_build_monitor_one_calibrator_q:237` (`build_emos_q(members_native=member_extrema)`).
- **why same class:** Same root pattern as the fixed spine bug — a LIVE decision belief path
  (here the exit/hold/sell q + sigma) consumes `ensemble_snapshots` (single-provider ECMWF, 51
  ecmwf_ens members) where the strategy-of-record AND the now-fixed entry path use
  `raw_model_forecasts` multi-provider fusion.
- **live-impact:** In EDLI live mode the exit lane is `main.py:9066 _execute_monitoring_phase`,
  named at `main.py:8977-8979` as "the ONLY path that fires exit monitoring" in edli_live.
  Its belief flows `_refresh_ens_member_counting` (818) → `period_extrema_members` (825-826) from
  `read_executable_forecast` → `to_ens_result:134` → members from `members_json` (855, ensemble) →
  `build_emos_q` (237) → `mu_native/sigma_native/q` → `_monitor_normal_bootstrap_sampler` → the
  exit/hold decision belief. A held position is ENTERED on the corrected multi-model center but
  its exit belief is re-derived every cycle from the systematically-colder ECMWF ensemble center
  (audit: 0/213 member sets equal, mean |delta mu*| 1.14 deg C) — twin-authority entry/exit
  divergence on a live money path; the cold-center bias the entry fix removed is re-introduced on
  the held side. **False provenance:** `monitor_refresh.py:565` docstring "Read monitor
  probability from the same executable forecast authority as entry" and the D2 header goal
  "EXIT belief matches ENTRY belief" are now FALSE — entry no longer reads that authority.
  Severity HIGH not CRITICAL: entry (the larger lever) is fixed; impact is on exit-timing/hold
  quality of currently-held positions, partly mitigated by adverse-only divergence
  (`monitor_refresh.py:97-106` treats positive edge as non-exit).
- **recommended fix:** Migrate the monitor/exit member-envelope source to `raw_model_forecasts`
  via the SAME accessor the entry spine now uses (`_spine_multimodel_members_for_event` /
  `fresh_members_at_cycle` latest-per-model at the position's causal cycle), so exit belief is
  byte-equivalent to the entry belief the position was sized under. Until migrated, mark the
  entry-parity docstring STALE and treat exit q as not-fresh when its source != the entry spine
  source (the same not-fresh guard the floor-parity path uses).

### [MED] Dead canonical spine stash sources ensemble_snapshots; pre-empts the fix on flag-off fall-through

- **file:line:** `src/engine/event_reactor_adapter.py` — canonical stash ~11897-11978
  (`raw_members = _snapshot_members(snapshot)` :11585 → `_snapshot_members` :12171 reads
  `snapshot["members_json"]` = ensemble_snapshots via `_forecast_snapshot_row_for_event` reading
  `_authority_table_ref(conn,"ensemble_snapshots")` :11355/11369; stashes
  `payload["_edli_spine_mu_native"]` :11956 etc.); skipped guard 7723-7726
  (`if ... and "_edli_spine_mu_native" not in payload`); fall-through trigger
  `_live_yes_probabilities:9810` → `_canonical_probability_and_fdr_proof:10880` →
  `_market_analysis_from_event_snapshot`; replacement return-None 10464-10465.
- **why same class:** Identical to the headline bug — the live spine decision (when this branch
  is taken) reads `ensemble_snapshots` members instead of `raw_model_forecasts`, and it is the
  SAME `MU_SIGMA`-style "stash any member envelope" producer that originally caused the
  regression.
- **live-impact:** For `_FORECAST_DECISION_EVENT_TYPES`, `_live_yes_probabilities` (9793) calls
  `_replacement_authority_probability_and_fdr_proof` FIRST (9797) and only falls to
  `_canonical_probability_and_fdr_proof` (9810) when replacement returns None. The ONLY in-body
  `return None` of the replacement fn is the flag-off guard (10465:
  `if not _replacement_authority_enabled(): return None`, reading
  `openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled`); every other exit RAISES and
  propagates. With that flag currently TRUE (`config/settings.json:287`) and
  `qkernel_spine_enabled` TRUE (:270), the canonical/ensemble branch is UNREACHABLE for spine
  decisions — the spine currently decides on the `raw_model_forecasts` producer output, so the
  defect does NOT affect a live decision in the deployed config. It re-arms ONLY if the
  trade-authority flag flips OFF while the spine flag stays ON (a documented rollback per the
  flag's own docstring at 10473; a realistic single-flag-flip event). On that fall-through, the
  canonical stash pre-empts the new `raw_model_forecasts` producer (guarded by
  `"_edli_spine_mu_native" not in payload` at 7723-7726, which the canonical stash has already
  populated) and the spine decides on the ECMWF-ensemble center again, silently, with no
  `SPINE_INPUTS_UNAVAILABLE`. (The always-canonical Day0 path at 9833 writes the ensemble stash
  but Day0 is excluded from the spine — `2507/2509 not _is_day0_event` + bridge lead-bucket
  reject :863 — so it never feeds a spine decision.)
- **recommended fix:** Make the canonical Stage-0 stash either (a) source its `_edli_spine_*`
  members from `raw_model_forecasts` via `_spine_multimodel_members_for_event` (same as the live
  producer), or (b) explicitly NOT write the `_edli_spine_*` keys (leave them for the 7759
  producer) so the `raw_model_forecasts` accessor is the single spine source on every lane. Add a
  RED-on-revert assertion that no spine decision can be built from `ensemble_snapshots` members.

---

# 3. RULED-OUT / FALSE POSITIVES

`ensemble_snapshots` is read in 242 src lines. The investigation confirmed **2** residuals from
the candidate pool; the remainder are NOT defects. The main false-positive classes:

- **Contract / schema / provenance / identity reads** — most of the 242 lines are legitimate
  identity (provenance stamping, schema definitions, SOURCE_TABLES literals used for labelling,
  contract assertions). These reference `ensemble_snapshots` by name but do not build a live
  decision center/q/sigma from it.
- **Day0-only ensemble reads** — the canonical Day0 stash writes ensemble members, but Day0 is
  structurally excluded from the spine (`not _is_day0_event` + bridge lead-bucket reject), so no
  spine decision consumes it.
- **Flag-gated-dead branches** — the canonical Stage-0 stash (residual #2) is real but currently
  unreachable in the deployed config (trade-authority flag TRUE), hence MED/latent, not a live
  CRITICAL.

Distinguishing rule applied: a candidate is a defect only if it is on the LIVE
decision/q/sigma/center path AND reachable in the deployed flag config (or trivially re-armed by
a single documented flag flip). Pure identity/contract reads and Day0-excluded reads were ruled
out.

---

# 4. PRIORITIZED NEXT ACTIONS

Ordered by live-decision risk:

1. **[HIGH — live now] Migrate the EXIT/monitor belief lane to `raw_model_forecasts`.**
   Repoint `monitor_refresh.py` / `executable_forecast_reader.py` exit-q member envelope to the
   entry spine accessor (`_spine_multimodel_members_for_event` / `fresh_members_at_cycle` at the
   position's causal cycle) so exit belief is byte-equivalent to the entry belief the position
   was sized under. Mark the `monitor_refresh.py:565` entry-parity docstring STALE until done;
   in the interim treat exit q as not-fresh when its source != the entry spine source. This is
   the only confirmed residual that corrupts a live money decision (exit/hold) in the current
   config.

2. **[MED — latent, re-arms on flag flip] Close the canonical Stage-0 ensemble stash.**
   Either source `_edli_spine_*` from `raw_model_forecasts` in `_market_analysis_from_event_snapshot`
   (~11897-11978) or stop it writing the `_edli_spine_*` keys, so the `raw_model_forecasts`
   accessor is the single spine source on every lane. Add a RED-on-revert assertion barring any
   spine decision built from `ensemble_snapshots` members — this also locks in the headline fix
   against the same regression recurring.

3. **[Process — prevents the whole defect class] Add a source-parity antibody.**
   Assert that the live entry spine, the exit/monitor lane, and the ARM-replay / settlement-EV
   harness all read the SAME table (`raw_model_forecasts`) for the member envelope, and that no
   `qkernel_spine_bridge.py` docstring claims "ARM-validated"/"chain-of-record"/"identical to
   replay" parity that a test does not enforce. This closes defect classes (a), (c), and (d) —
   source divergence, false provenance, and harness-vs-live source mismatch — at the structural
   level rather than one residual at a time.
