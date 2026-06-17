All load-bearing claims confirmed against source:
- bias_decay haircut: short-circuits `return ... "one_calibrator_regime"` at line ~11413 **before** the flag check at ~11416 — dead-on-live when q_source ∈ {emos, raw_honest}. Verified.
- emos sole-calibrator routing at 10946-10948 confirmed: non-day0 cells route to EMOS, never `_maybe_apply_edli_bias_correction`. Verified.
- buy_no LIVE reject at cycle_runtime.py:5663-5674 and the LIVE-requires-SHADOW coupling at family_exclusive_dedup.py:1110-1114 confirmed.

The audits are accurate where it matters. Writing the authoritative plan.

---

# Zeus `config/settings.json` Cleanup — Authoritative Plan

*Audit date 2026-06-14. Live state: `real_order_submit_enabled=TRUE` (real capital), `edli_emos_sole_calibrator_enabled=TRUE`, `edli_bias_correction_enabled=FALSE` (set this session), scope=`forecast_plus_day0`. Source-verified the 5 load-bearing claims before writing.*

## 1. SUMMARY

**Verdict counts** (≈236 scalar keys + notes audited across 9 block-audits):

| Verdict | Count | Meaning |
|---|---|---|
| CORRECT_AS_IS | ~165 | Live consumer, value correct — no change |
| **REMOVE_DEAD** | **24** (18 keys + 6 paired `_note`s) | Zero live consumers / superseded alias / forever-shadow |
| **UPDATE_VALUE** | **8** (7 stale `_note`s + 1 data refresh) | Doc says "DEFAULT FALSE/SHADOW" while flag is live TRUE; one stale pin date |
| **FLIP_ON** | **0** | No useful flag is wrongly OFF that is safe to flip unilaterally |
| **FLIP_OFF** | **0** | The one contaminated flag (`edli_bias_correction_enabled`) is *already* FALSE |
| **NEEDS_OPERATOR_DECISION** | **6** | Change puts unverified behavior on real capital (ARM-gated) |

**Biggest theme:** This is a **dead-key + stale-note graveyard, not a wrongly-OFF-flag problem.** The dominant pattern is **doc debt from the "shadow → promote" lifecycle**: 7 `_note` strings still say *"DEFAULT FALSE / SHADOW FLAG"* for flags that were promoted to live TRUE, directly contradicting live state and misleading the next session. The second theme is **superseded plumbing**: 18 keys are read-then-discarded, parsed-but-never-gated, or pure declared-only telemetry — the live value lives in a hardcoded constant or a different config block. **No flag is both useful and safely-flippable** — every behavior-changing candidate is ARM-gated and lands in §3.

The single most important *finding* (not a cleanup item) is that the live `buy_no` strategy of record is suppressed by two OFF flags (`NATIVE_MULTIBIN_BUY_NO_LIVE/SHADOW`) — but that is an operator decision, not a flip.

---

## 2. THE EDIT PLAN

### (a) FLIP_ON — useful flags wrongly OFF

**NONE.** Every OFF flag examined is either correctly OFF (contaminated/legacy fallback) or its flip is ARM-gated (→ §3). Do not flip anything in this category unilaterally.

### (b) FLIP_OFF — broken/contaminated/garbage currently ON

**NONE to apply.** The contaminated flag `edli.edli_bias_correction_enabled` was **already set FALSE this session** (it applied a −2..−4.85 °C warm bias corrupting the live center +2.8 °C). It is correct as FALSE; keep it. No other ON flag is contaminated. (`replacement_q_market_anchor_enabled=TRUE` is a banned-class throttle but retiring it moves live q → §3, not a unilateral flip.)

### (c) UPDATE_VALUE — stale notes + one data refresh (doc-only, no behavior change unless noted)

| Dotted key | Current | Exact change | Risk | Evidence |
|---|---|---|---|---|
| `edli._edli_emos_sole_calibrator_enabled_note` | "#110 … DEFAULT FALSE / SHADOW-prove before promote" | Rewrite: flag is **live TRUE**; restate as the active single-calibrator regime; drop "default false/shadow" | LOW | flag=TRUE verified `event_reactor_adapter.py:10946-10948`; note contradicts live state |
| `edli._q_lcb_settlement_coverage_gate_note` (edli-A) | "Phase-2 K3 SHADOW FLAG, DEFAULT FALSE" | Update to live TRUE; keep ARM-coupling/k_cov explanation | LOW | flag=TRUE applied `event_reactor_adapter.py:9504,9607` |
| `edli._q_lcb_settlement_coverage_gate_note` (edli-B, same key dupe in range) | "…DEFAULT FALSE…K2 COUPLING edli_bias_correction forces identity Platt" | Update to live TRUE; **drop the K2-coupling clause** (bias_correction is now FALSE → moot) | LOW | enabled 2026-06-09 mass-enable; bias_correction=FALSE this session |
| `edli._edli_bias_correction_enabled_note` | "SHADOW ONLY, real_order_submit stays false, no capital / moves p_raw toward truth" | Record the **CONTAMINATION verdict**: set FALSE this session (−2..−4.85 °C warm bias corrupted live center); premise refuted (real_order_submit=TRUE now). Keep train/serve-lockstep mechanics | LOW | refuted-on-live this session; real_order_submit=TRUE |
| `edli._replacement_q_market_anchor_note` (older, undated) | "DEFAULT FALSE … Flip only on operator word AFTER forward fills license it" | Fold into / reconcile with the `_note_2026_06_12` (flag is TRUE) — two notes, conflicting status | LOW | contradicts live TRUE + newer dated note |
| `edli._edli_live_scope_note` (interim) | "day0 held for review; returns to forecast_plus_day0 when fixes land" | Mark stale/resolved — scope **is** forecast_plus_day0; defer to `_note_2026_06_12` | LOW | session scope = forecast_plus_day0; day0 fixes landed |
| `feature_flags._exit_bias_family_unify_enabled_note` | "D2 … DEFAULT OFF (shadow)" | Drop "DEFAULT OFF (shadow)"; flag live ON since 2026-06-12 | LOW | flag=TRUE; sibling `_FLIP_2026_06_12` records promotion |
| `feature_flags._calibration_bin_source_v2_fit_enabled_note` | "Daemon is SHADOW (real_order_submit=false) … set to true after review" | Drop the shadow-era promotion-gate clause; flag already TRUE, real_order_submit=TRUE | LOW | note contradicts verified live state |
| `probability_edge_bin_sanity._note_2026_05_24` | (doc) | If §d removes `apply_to_strategies` + `log_only_until_replay_fp_zero`, prune the two sentences referencing them; keep GATE/BIMODAL/apply_to_metrics semantics | LOW | the two referenced keys are removed in §d |

**Data-currency refresh (not a config edit — pipeline/operator):**
- `calibration.pin.frozen_as_of = "2026-06-06T17:44:44Z"` — now ~8 days old; WARN at 10d, FATAL at 21d (`main.py:296,338,418`). Refresh to a recent pin before it trips. **MED** (data refresh, not a flag edit).

### (d) REMOVE_DEAD — directly applyable (each verified zero live consumers)

All LOW risk, no ARM-condition, no operator sign-off needed. Remove the key **and** its paired `_note` together.

| # | Dotted key (+ paired note) | Current value | Evidence (zero live consumer) |
|---|---|---|---|
| 1 | `edli._edli_live_scope_note_2026_06_09` | doc | Superseded by `_note_2026_06_12` ("third and final word"); duplicate provenance |
| 2 | `edli.bias_decay_kelly_haircut_enabled` + `_bias_decay_kelly_haircut_note` | true | Short-circuits `"one_calibrator_regime"` at `event_reactor_adapter.py:11413` **before** flag check at :11416 — dead on live lane; **banned Kelly haircut** per no-throttle law (VERIFIED in source) |
| 3 | `edli.bias_decay_threshold_c` | 2.0 | read only inside short-circuited helper `:11470` |
| 4 | `edli.bias_decay_threshold_f` | 3.0 | read only inside short-circuited helper `:11467` |
| 5 | `edli.bias_decay_kelly_factor` | 0.5 | `:11458` apply / `:3315` telemetry, both gated by always-False `_bias_decay_applied` |
| 6 | `edli.edli_live_max_unresolved_unknowns` | 0 | no `settings['edli'].get(...)` in src/; only reader is offline CI `check_live_release_gate.py:648` via argparse default |
| 7 | `edli.edli_live_min_realized_edge_bps` | 0 | same — `check_live_release_gate.py:649` argparse, never config |
| 8 | `exit.reversal_confirmations` + `_reversal_confirmations_note` | 2 | own note "use consecutive_confirmations"; zero consumers; live reads `consecutive_confirmations()` (same value) |
| 9 | `exit.expiry_hours` + `_expiry_hours_note` | 4 | own note "use near_settlement_hours"; zero consumers; live reads `near_settlement_hours()` (same value) |
| 10 | `edge.opening_alpha_bonus` | 0.15 | grep `opening_alpha_bonus`/`alpha_bonus` across src/ = 0 hits |
| 11 | `sizing.max_city_pct` | 0.2 | **VERIFIED**: `config.py:629` loads into dataclass, `risk_limits.py:19` declared-only field; `risk_limits.py:11-15,40-42` "intentionally not hard gates … telemetry only"; zero numeric read |
| 12 | `calibration.method` | "platt" | never read; method hardcoded — `manager.py:1105` reads per-MODEL DB column `calibration_method`, not this key |
| 13 | `calibration.refit_every_n` | 50 | grep = 0; cadence is drift-triggered (`drift_refit_arm.py`), not fixed-N |
| 14 | `calibration.seasonal_dates` | ["03-20",…] | grep = 0; season routing uses `bucket_key` in manager.py |
| 15 | `ensemble.boundary_window` | 0.5 | loaded into `BOUNDARY_WINDOW` `ensemble_signal.py:106` then **referenced nowhere**; superseded by `sigma_instrument_for_city()` |
| 16 | `ensemble.unimodal_range_epsilon` | 0.5 | loaded into `UNIMODAL_RANGE_EPSILON` `ensemble_signal.py:107`, referenced nowhere in logic |
| 17 | `ensemble.conflict_kl_threshold` | 0.15 | **VERIFIED** grep src/ = 0; no accessor, no import |
| 18 | `riskguard.accuracy_orange` | 0.45 | never in `thresholds[...]` access set; `accuracy_orange` grep = 0 |
| 19 | `riskguard.win_rate_yellow` | 0.4 | grep = 0; win_rate is a DB column only, never thresholded |
| 20 | `riskguard.win_rate_orange` | 0.35 | grep = 0; same |
| 21 | `riskguard.max_drawdown_pct` | 0.2 | **VERIFIED**: never accessed off `settings['riskguard']`; the only `max_drawdown_pct` reader is `governor.py` CapPolicy YAML (separate config) |
| 22 | `riskguard.staleness_hours` | 6 | grep = 0; riskguard freshness uses hardcoded 300s literal `freshness_registry.py:100` |
| 23 | `execution.order_type` | "limit_only" | only `settings['execution']` config read is `limit_offset_pct`; runtime order type from `select_global_order_type` (governor.py:389); string "limit_only" absent in src/ |
| 24 | `execution.fill_timeout_seconds` | 600 | grep = 0 |
| 25 | `execution.cancel_if_not_filled` | true | grep = 0 (companion to dead fill_timeout) |
| 26 | `discovery.ecmwf_open_data_times_utc` | [01:30,13:30] | grep src/+scripts/ = 0; cadence from `replacement_forecast_shadow.*` |
| 27 | `discovery.max_lead_days` | 7 | grep = 0; horizon enforced in `cycle_runner._DISCOVERY_MODE_PARAMS` |
| 28 | `discovery.preferred_lead_days` | [3,4,5] | only prose mentions in scripts; no src/ read |
| 29 | `discovery.min_hours_to_resolution` | 6 | grep = 0; value hardcoded in `_DISCOVERY_MODE_PARAMS` + arg defaults |
| 30 | `probability_edge_bin_sanity.min_neighbor_support` | 0.05 | parsed into thresholds `probability_sanity.py:502-503` but gate body never reads it |
| 31 | `probability_edge_bin_sanity.log_only_until_replay_fp_zero` | true | grep = 0; FP=0 already proven (per note), mode already "hard" |
| 32 | `probability_edge_bin_sanity.apply_to_strategies` | [list] | **MED** — advisory metadata only (`probability_sanity.py:470-471` docstring "NOT used to downgrade mode"); stale (omits highest-volume `opening_inertia`). Confirm no dashboard/DB column maps it before removing |

**Orphaned code to flag for the code-owner (out of this config-audit's write scope — these are `.py` edits, do NOT include in the settings.json commit):**
- `src/config.py`: remove orphaned accessors `ensemble_boundary_window()` (:616), `ensemble_unimodal_range_epsilon()` (:620), and **dead-and-would-KeyError** `correlation_matrix()` (:643, reads already-removed `settings['correlation']['matrix']`, zero callers).
- `src/signal/ensemble_signal.py`: remove imports :25,:29 and dead constants `BOUNDARY_WINDOW` :106 / `UNIMODAL_RANGE_EPSILON` :107.
- `src/signal/probability_sanity.py`: remove `_DEFAULT_EDGE_BIN_MIN_NEIGHBOR_SUPPORT` + parse :502-503.

---

## 3. NEEDS_OPERATOR_DECISION (genuine real-capital behavior changes — do NOT touch as cleanup)

| # | Key | Current | Decision the operator must make | Recommendation |
|---|---|---|---|---|
| 1 | `feature_flags.NATIVE_MULTIBIN_BUY_NO_LIVE` | false | **THE #1 ITEM.** This OFF flag rejects **every live buy_no order** at preflight (`cycle_runtime.py:5663-5674`, reason `NATIVE_MULTIBIN_BUY_NO_LIVE_DISABLED` — VERIFIED). buy_no is the strategy of record → this is very likely the live buy_no suppressor. Flipping ON authorizes native-NO-token submission on real capital. Code enforces LIVE-requires-SHADOW (`family_exclusive_dedup.py:1110-1114` — VERIFIED). | **Surface, do not flip.** Promote only with shadow-first ON + operator promotion evidence per the `_note`. Highest-priority operator conversation. |
| 2 | `feature_flags.NATIVE_MULTIBIN_BUY_NO_SHADOW` | false | Prerequisite for #1: enabling it makes the evaluator probe native-NO quotes for buy_no (`evaluator.py:4727`). | **Surface.** Turn ON first (shadow) to gather promotion evidence before #1. |
| 3 | `edli.replacement_q_market_anchor_enabled` | true | Live one-sided q_lcb cap. Own replay evidence: "blocks none of 4 C3 losses, shrinks edge 3-63% (Beijing −63%)". Tension with no-caps/no-haircuts law (task #91). Retiring it raises live tradable q → ARM. | **Recommend retire** (it's a banned-class throttle that demonstrably destroys edge without blocking losses) — but operator-gated because it moves live q. |
| 4 | `edli.edli_bias_correction_enabled` | false | Keep FALSE this session (correct). Separate decision: **remove the whole legacy lane** once `edli_emos_sole_calibrator_enabled` is ratified as the permanent single calibrator. | **Keep FALSE now; defer removal.** Removing now narrows the rollback path if sole-calibrator is ever turned off. Re-audit when sole-calibrator ratified. |
| 5 | `exit.correlation_crowding_rate` | 0.0 | Plumbed-but-no-op shadow cost frozen at 0.0. No-shadow law says promote-or-remove. | **Recommend remove the plumbing** (cleanest); alternatively operator sets a real rate. Do NOT silently flip nonzero — adds exit pressure on real capital. |
| 6 | `baseline_bias_correction_enabled` (top-level) | false | LEGACY baseline ECMWF bias chain (distinct from edli's). Flipping ON re-introduces a second uncalibrated warm-bias chain; `main.py:5168` warns it requires recompute-bias + Platt-refit + operator sign-off. | **Keep FALSE.** Correct as-is; flip-on is explicitly ARM-gated. |

---

## 4. COUPLINGS / ORDER-OF-OPERATIONS

1. **emos-sole-calibrator ⟂ bias_decay ⟂ edli_bias_correction (the de-bias collapse).** With `edli_emos_sole_calibrator_enabled=TRUE` (live), both legacy lanes are *already dormant*: bias_decay short-circuits `"one_calibrator_regime"` **before** its flag check (VERIFIED :11413), and bias_correction is unreachable on the EMOS branch (VERIFIED :10946-10948). **Therefore removing the bias_decay flag+3 params+note (§d items 2-5) is safe in any order** — it changes nothing live. Do **not** remove `edli_bias_correction_enabled` (keep as rollback fallback, §3.4). **Order: bias_decay removals are independent and unconditional; do them first.**

2. **NATIVE_MULTIBIN_BUY_NO_SHADOW → _LIVE (hard code-enforced lockstep).** `family_exclusive_dedup.py:1110-1114` raises if `_LIVE=true` while `_SHADOW=false` (VERIFIED). If operator ever promotes (§3.1/3.2): **SHADOW first, gather evidence, then LIVE** — never LIVE alone or the daemon errors at boot.

3. **q_lcb_settlement_coverage_gate note ⟂ bias_correction.** The edli-B note's "K2 COUPLING: edli_bias_correction forces identity Platt" clause is now moot (bias_correction=FALSE). When updating that note (§c), drop the clause — don't preserve a stale lockstep.

4. **probability_edge_bin_sanity removals ⟂ its note.** Remove `apply_to_strategies` + `log_only_until_replay_fp_zero` (§d 31-32) **before/with** pruning the note's two referencing sentences (§c). Single atomic edit to that block.

5. **No emos-sole / q_lcb-gate value changes** — both are live ON and correct; only their *notes* change. The ladder `feature_flags.openmeteo_*_soft_anchor_*` (all 5 ON) and the armed trio (`real_order_submit / durable_submit_outbox / edli_live_operator_authorized`) are **untouched** — any value change there is ARM.

**Apply order:** (1) all §d REMOVE_DEAD (independent, unconditional, no live behavior) → (2) all §c note UPDATE_VALUE (doc-only) → (3) read-back + atomic git commit per the "every flip = atomic edit + read-back + git commit" law (the `_intermediate_cycle` note records a prior silent-revert incident). §3 items are **not** in this commit.

---

## 5. POST-EDIT VERIFICATION

Since §d + §c are **dead-key + doc-only** edits (no live behavior change), verification is lightweight — but the daemon parses settings.json strictly, so a JSON or schema break is the real risk:

1. **JSON + schema validity (mandatory, before commit):** `python -c "import json; json.load(open('config/settings.json'))"` and run the settings-schema/boot-guard self-check. A trailing-comma break from removing 18 keys is the #1 failure mode.

2. **Boot-guard dry parse:** start the daemon (or its config-load path) once and confirm no `KeyError` / no `assert_*` boot guard fires. Specifically confirm:
   - `EntryForecastConfig` still parses (you removed nothing in entry_forecast).
   - `sizing_defaults()` still builds after `max_city_pct` removal (it's loaded at config.py:629 — **the loader line must be removed in lockstep or it KeyErrors**; this is the one removal that touches a live `dict[...]` access, flag for the code-owner).
   - riskguard `thresholds` dict builds (you removed only never-accessed keys; the 5 live keys remain).

3. **No behavior delta (the proof it was dead):** diff one live decision cycle's receipts before/after. For genuinely-dead keys the receipts must be **byte-identical** (same q, same sizing, same gate verdicts). Any delta means a key was NOT dead → revert and re-audit.

4. **Note-only edits:** zero runtime effect — confirm only that the next session reads the corrected notes (no "DEFAULT FALSE" on a live-TRUE flag).

5. **For the §3 items if/when the operator acts** (out of this cleanup): `NATIVE_MULTIBIN_BUY_NO_*` flip requires watching live `edli_no_submit_receipts` / preflight rejection telemetry to confirm buy_no orders stop being rejected with `NATIVE_MULTIBIN_BUY_NO_LIVE_DISABLED` and actually reach the venue — live-fire the order path, don't infer from belief.

**Key files for the applier:** `config/settings.json` (all edits); lockstep `.py` cleanup (separate commit, code-owner): `src/config.py:616,620,629,643`, `src/signal/ensemble_signal.py:25,29,106,107`, `src/signal/probability_sanity.py:502-503`.