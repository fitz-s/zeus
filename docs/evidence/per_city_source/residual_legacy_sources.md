# Residual Cold/Legacy Source Inventory

```
# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: read-only investigation against HEAD (live/iteration-2026-06-13 @ c62d53b190)
#   Two held cleanup commits: 95995f7100 (bias-maze strip) on claude/agent-a6cdcbb28b5c8e7ce
#                             494ba9a03a (carrier decouple) on claude/agent-a2a825fa1f2114550
```

---

## Part A: Held-Cleanup Summary and Git Presence Verdict

### (1) Bias-Maze Strip — commit 95995f7100

**Branch:** `claude/agent-a6cdcbb28b5c8e7ce` (NOT merged into `live/iteration-2026-06-13`).
**Status:** `git merge-base --is-ancestor 95995f7100 HEAD` → exit 1 (NOT an ancestor of HEAD).

The commit's declared removals (confirmed via `git show --format=%B`):

| Item | What was REMOVED | What was KEPT |
|---|---|---|
| Spine settlement-residual de-bias | `ZEUS_SPINE_SETTLE_RESID_DEBIAS` env flag, `_live_settlement_residual_provider`, `_LIVE_SETTLE_RESID_PROVIDER` cache, deletion of `src/forecast/settlement_residual_debias.py` | `debias_authority.py` (identity type CONTRACT), `_NoOpDebiasAuthority` (spine bridge), `_spine_debias_authority` (now unconditional no-op in that commit) |
| `ens_bias_model` / `ens_bias_repo` | NOT removed (load-bearing for schema init, coverage guard, evaluator, grid-repr sigma) | Both kept |
| EMOS-CI override + shadow ledger + license | `_maybe_override_lcb_with_emos_ci`, `_write_emos_shadow_ledger`, main.py boot guard `_assert_emos_ci_license_seasonal_coverage`, deleted `emos_ci_license.py` + `emos_ci_shadow.py` | — |
| Bias correction haircut + flags | `_maybe_bias_decay_kelly_haircut`, `_maybe_apply_edli_bias_correction` + call sites, flags `edli_bias_correction_enabled`, `edli_emos_sole_calibrator_enabled`, `bias_decay_kelly_haircut_enabled/*_threshold*/*_factor` | EMOS/honest-raw calibrator logic made unconditional |
| Docs created | `docs/evidence/legacy_strip/bias_maze_strip.md` (evidence report in the branch; absent on HEAD) | — |

**Pending (what it did NOT remove):** `settlement_residual_debias.py` stays in the branch's deletion scope; `ens_bias_model/ens_bias_repo` retained as non-bias load-bearing. The `anchor_representativeness_debias.py` / `get_city_debias_c` seam in `replacement_forecast_materializer.py` was NOT part of this strip (it is a per-city representativeness correction on the materializer, not the maze).

---

### (2) mx2t3 Carrier Decouple — commit 494ba9a03a

**Branch:** `claude/agent-a2a825fa1f2114550` (built on top of 95995f7100, merged in via 6248560eec).
**Status:** `git merge-base --is-ancestor 494ba9a03a HEAD` → exit 1 (NOT an ancestor of HEAD).

What the commit implemented (confirmed via `git show --format=%B`):

| Seam | Change |
|---|---|
| A1–A4 `forecast_snapshot_ready.py` | Forked `scan_committed_snapshots` on `_replacement_trade_authority_enabled()`: replacement lane uses `ranked_posterior` over `forecast_posteriors` (neutral `rmf-<city>\|<target>\|<metric>\|<cycle>` snapshot id). `_FORECAST_TABLES` guard forks to `('forecast_posteriors',)` on that flag. `classify_forecast_snapshot` posterior-backed `COMPLETE` short-circuit. `members_json` → NULL (spine overrides). |
| B1 `event_reactor_adapter.py` `_spine_multimodel_members_for_event` | Parse causal cycle from the `rmf-...\|<date>` id; keep `_bound_forecast_snapshot_row_for_spine` only for legacy integer ensemble ids; raw_model_forecasts MAX-cycle fallback (B2). |
| C1–C2 `event_reactor_adapter.py` `_forecast_authority_payload_and_clock` | Forks to new `_forecast_authority_payload_from_posterior` for replacement+non-day0 events: builds cert payload from `forecast_posteriors`+`raw_model_forecasts`, `members_json_source=raw_model_forecasts.multimodel`, `source_run_id=posterior_identity_hash`. DAY0 explicitly excluded (keeps ensemble base). |
| D `verifier.py` | Widened no-submit cert `members_json_source` allow-list to accept `raw_model_forecasts.multimodel`. |
| Tests | `tests/engine/test_mx2t3_carrier_decouple.py` (7 tests; red-on-revert for flag-OFF). |

**Pending (what it did NOT change):** Day0 lane intentionally excluded — it still reads `ensemble_snapshots.members_json` via `_market_analysis_from_event_snapshot`. mx2t3 ingest/download untouched. `consumer_classification.md` and `carrier_decouple_plan.md` created as evidence docs.

**Git relationship:** 6248560eec is the merge of 95995f7100 into the carrier-decouple branch, so 494ba9a03a sits on top of (and includes) the bias-strip changes. Neither branch has been merged into `live/iteration-2026-06-13`.

---

## Part B: Live-Reachable Cold-Source Inventory on HEAD

HEAD = `c62d53b190` (`live/iteration-2026-06-13`). Neither cleanup commit is present. Settings verified from `config/settings.json`:

```
feature_flags.openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled = True
feature_flags.qkernel_spine_enabled = True
edli.edli_live_scope = forecast_plus_day0          ← DAY0 IS LIVE
edli.day0_remaining_day_q_enabled = True
edli.edli_bias_correction_enabled = False
edli.edli_emos_sole_calibrator_enabled = True
edli.real_order_submit_enabled = True
ZEUS_SPINE_SETTLE_RESID_DEBIAS (env) = absent → False
edli_emos_ci_live_enabled = absent → False
edli_emos_shadow_ledger_enabled = absent → False
bias_decay_kelly_haircut_enabled = True              ← STILL ON HEAD
```

### B.1 ensemble_snapshots.members_json (Cold mx2t3 ENS members, ~1-2°C cold)

**Still LIVE on HEAD — DAY0 lane only.**

Call chain on HEAD:
- `event_reactor_adapter.py:9844` `_live_yes_probabilities` for `DAY0_EXTREME_UPDATED` calls `_canonical_probability_and_fdr_proof(allow_latest=True)` directly (no replacement gate).
- `event_reactor_adapter.py:10863` `_canonical_probability_and_fdr_proof` → `event_reactor_adapter.py:11366` `_forecast_snapshot_row_for_event(allow_latest=True)` → reads `ensemble_snapshots` row.
- `event_reactor_adapter.py:11583` `_market_analysis_from_event_snapshot` → `event_reactor_adapter.py:11596` `_snapshot_members(snapshot)` reads cold `members_json` as the seed pool for `_day0_remaining_day_members`.
- Day0 is live (`edli_live_scope=forecast_plus_day0`), `day0_remaining_day_q_enabled=True`.

**FORECAST lane: NOT reachable.** `_replacement_authority_enabled()` = True; `_replacement_authority_probability_and_fdr_proof` never returns None on the forecast lane → `_canonical_probability_and_fdr_proof` unreachable (adapter lines ~9808-9821).

**Also live: `_forecast_authority_payload_and_clock`** (`event_reactor_adapter.py:6492`) — called for EVERY decision including the spine lane. On HEAD this still reads `ensemble_snapshots` rows via `_forecast_snapshot_row_for_event` (line 6500) and `_read_executable_forecast_bundle_result` → `read_executable_forecast` which reads `members_json`. If an `ensemble_snapshots` row is absent/stale, this raises `FORECAST_AUTHORITY_EVIDENCE_MISSING:snapshot` and kills the candidate — even on the spine lane. This is the third severed wire identified by `carrier_decouple_plan.md` §3C, NOT yet patched on HEAD.

**Also live: `_bound_forecast_snapshot_row_for_spine`** (`event_reactor_adapter.py:11348`) — pins `causal_snapshot_id` against `ensemble_snapshots` to extract `source_cycle_time`. Still on HEAD unpatched (carrier decouple B1 not landed). If no ensemble row, returns None → `SPINE_INPUTS_UNAVAILABLE`.

**Family-readiness** (`forecast_snapshot_ready.py:492-492`): still guarded by `_FORECAST_TABLES = ("source_run", "source_run_coverage", "ensemble_snapshots")` (line 847). The `ranked_posterior` fork (carrier decouple A1) is NOT present on HEAD; only an `EXISTS` `replacement_filter` appended as a WHERE clause filter (lines 512-523). The JOIN itself still goes through `ensemble_snapshots`.

### B.2 AIFS-sampled-2t Prior (build_openmeteo_ifs9_aifs_soft_anchor_result)

**Still live on HEAD as the materializer that writes `forecast_posteriors`.** This is NOT a cold source — it is the per-city best near-airport precise forecast (the single truth target). Specifically:

- `src/data/replacement_forecast_materializer.py:65` imports `build_openmeteo_ifs9_aifs_soft_anchor_result` from `src/strategy/ecmwf_aifs_sampled_2t_probabilities.py`.
- `replacement_forecast_materializer.py:1564` calls it to materialize `forecast_posteriors` rows with `product_id=openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1`.
- The spine reads those posteriors (via `raw_model_forecasts` multi-model fusion) as the live belief center.

The `build_openmeteo_ifs9_aifs_soft_anchor_result` function **is** the correct replacement source; it is not legacy. However, its input in `replacement_forecast_materializer.py:1546-1548` applies `get_city_debias_c` from `src/calibration/anchor_representativeness_debias.py` as a per-city representativeness correction. This is artifact-gated (returns None when `state/anchor_representativeness_debias.json` is absent → byte-identical to today). This seam is NOT part of the bias-maze strip scope per the strip commit message.

### B.3 Settlement-Residual De-Bias (ZEUS_SPINE_SETTLE_RESID_DEBIAS)

**Present on HEAD, flag-OFF, `_NoOpDebiasAuthority` is the live path.** The seam (`qkernel_spine_bridge.py:188-230`) exists on HEAD but:
- `_settlement_residual_debias_enabled()` reads `os.environ.get("ZEUS_SPINE_SETTLE_RESID_DEBIAS") == "1"` → False (env unset on HEAD).
- `src/forecast/settlement_residual_debias.py` **still exists on HEAD** (6742 Jun 16 19:34 — the bias-strip commit that would delete it has not landed).
- `qkernel_spine_bridge.py:230` has a lazy `from src.forecast.settlement_residual_debias import (...)` inside the `if _settlement_residual_debias_enabled()` branch → unreachable at runtime when flag is OFF.
- **Net: INERT on live path, but the file and seam are present on HEAD.**

### B.4 Bias-Correction Maze Remnants — All Present on HEAD

The bias-strip commit (95995f7100) has NOT landed on HEAD. All of the following remain in live code at HEAD:

| Seam | File:Line | Live-Reachability |
|---|---|---|
| `_maybe_bias_decay_kelly_haircut` | `event_reactor_adapter.py:12325` (def), called at `:3069` | Called on the live spine path pre-submit. `bias_decay_kelly_haircut_enabled=True` in settings. REACHABLE but `q_source=qkernel_spine` → early-return (the commit note says "early-returned for q_source=qkernel_spine"). Fires on day0 lane (non-spine q_source). |
| `_maybe_apply_edli_bias_correction` | `event_reactor_adapter.py:12467` (def), called at `:11898` and `:13535` | Two live call sites: day0 path (`:11898`) and exit monitor path (`:13535`). Flag `edli_bias_correction_enabled=False` → both are no-ops. |
| `_maybe_override_lcb_with_emos_ci` | `event_reactor_adapter.py:13058` (def), called at `:11173` | Called on the day0+canonical path. Flag `edli_emos_ci_live_enabled` absent→False → function returns immediately at top of body. INERT but present. |
| `_write_emos_shadow_ledger` | `event_reactor_adapter.py:12802` (def), called at `:11254` | Call site gated `edli_emos_shadow_ledger_enabled=False` (absent from settings). INERT. Reads `emos_ci_shadow.py` (`calibration/emos_ci_shadow.py` still exists on HEAD). |
| `src/calibration/emos_ci_license.py` | `src/calibration/emos_ci_license.py` | File exists on HEAD. Imported lazily from `_maybe_override_lcb_with_emos_ci` (line ~13109) and from `src/main.py:1028`. Boot guard `_assert_emos_ci_license_seasonal_coverage` at `main.py:9454` — but gated `if not bool(edli_cfg.get("edli_emos_ci_live_enabled", False)): return` → no-op. |
| `src/calibration/emos_ci_shadow.py` | `src/calibration/emos_ci_shadow.py` | File exists on HEAD. Imported by `_write_emos_shadow_ledger` lazily. INERT. |
| `src/forecast/settlement_residual_debias.py` | `src/forecast/settlement_residual_debias.py` | File exists on HEAD. Lazily imported by `qkernel_spine_bridge.py:230` behind `ZEUS_SPINE_SETTLE_RESID_DEBIAS=1`. INERT at runtime. |

---

## Part C: Concrete Removal/Repoint Worklist

The following is the exact seam list ordered by landing dependency. Each item is tagged with its dependency gate.

### GATE 0 — Already safe (bias-strip, can land now; no day0 interaction)

These items were verified and stripped in commit 95995f7100 but are still present on HEAD because that branch is unmerged.

| # | File:Line | Seam | Action | Repoint To |
|---|---|---|---|---|
| S1 | `src/engine/qkernel_spine_bridge.py:185-230` (`_settlement_residual_debias_enabled`, `_spine_debias_authority`, provider cache, `_live_settlement_residual_provider`) | Settlement-residual de-bias seam + env flag | REMOVE seam; make `_spine_debias_authority` return `_NoOpDebiasAuthority()` unconditionally | `_NoOpDebiasAuthority` (already the live path) |
| S2 | `src/forecast/settlement_residual_debias.py` | Settlement-residual de-bias provider module | DELETE file | N/A (seam gone) |
| S3 | `src/engine/event_reactor_adapter.py:12325` `_maybe_bias_decay_kelly_haircut` def + call at `:3069` | Bias-decay Kelly haircut | REMOVE def + call site + settings keys `bias_decay_kelly_haircut_enabled/*_threshold*/*_factor` | No haircut (raw Kelly, honest gates stay) |
| S4 | `src/engine/event_reactor_adapter.py:12467` `_maybe_apply_edli_bias_correction` def + calls at `:11898` and `:13535` | EDLI bias-correction | REMOVE def + both call sites + settings key `edli_bias_correction_enabled` | Unconditional raw members (day0 + exit monitor keep `_day0_remaining_day_members`) |
| S5 | `src/engine/event_reactor_adapter.py:13058` `_maybe_override_lcb_with_emos_ci` def + call at `:11173` | EMOS-CI LCB override | REMOVE def + call site | MC lcb (already the live value — override is a no-op) |
| S6 | `src/engine/event_reactor_adapter.py:12802` `_write_emos_shadow_ledger` def + call at `:11254` (with gating `if` block) | EMOS shadow ledger | REMOVE def + call site + settings key `edli_emos_shadow_ledger_enabled` | N/A (observability only) |
| S7 | `src/calibration/emos_ci_license.py` | EMOS-CI license module | DELETE file | N/A (seam gone) |
| S8 | `src/calibration/emos_ci_shadow.py` | EMOS-CI shadow module | DELETE file | N/A (seam gone) |
| S9 | `src/main.py:1007-1058` `_assert_emos_ci_license_seasonal_coverage` + call at `:9454` | EMOS-CI boot guard | REMOVE function + call site | N/A |
| S10 | `config/settings.json` | Remove keys: `edli_bias_correction_enabled`, `edli_emos_sole_calibrator_enabled`, `bias_decay_kelly_haircut_enabled`, `bias_decay_threshold_c`, `bias_decay_threshold_f`, `bias_decay_kelly_factor` + their `_note` twins | DELETE keys | Replace with `_legacy_bias_maze_removed_note` marker (as done in 95995f7100) |

**Prerequisite for S3:** Confirm `_maybe_bias_decay_kelly_haircut` truly does early-return for `q_source=qkernel_spine` (spine lane) — the commit note asserts this. For the day0 lane (non-spine q_source), removal means no haircut on day0 sizing; acceptable per no-caps law.

---

### GATE 1 — Carrier decouple (depends on GATE 0 landing; day0 live scope must be checked first)

These items were implemented in commit 494ba9a03a. All fork on `_replacement_trade_authority_enabled()` = True, so the legacy ensemble path stays intact for flag OFF. Day0 lane explicitly excluded from the fork.

**Pre-condition check:** `edli_live_scope=forecast_plus_day0` (day0 IS live-submitting on HEAD). The carrier_decouple_plan.md §4 day0 edge-case says: if day0 is live-submitting, the decouple is still safe because the C1/C2 fork explicitly excludes `DAY0_EXTREME_UPDATED` events — day0 retains its `ensemble_snapshots` base. **The decouple can land without a day0 base.** Risk: if mx2t3 ingest stops, day0 base goes stale; operator must decide before stopping ingest.

| # | File:Lines | Seam | Action | Repoint To |
|---|---|---|---|---|
| C-A1 | `src/events/triggers/forecast_snapshot_ready.py:552-604` (`_select_sql_base` core) | `source_run_coverage`→`source_run`→`ensemble_snapshots` JOIN | Replace with `ranked_posterior` CTE over `forecast_posteriors` (exact SQL in carrier_decouple_plan.md §3A); mint neutral `rmf-<city>\|<target>\|<metric>\|<cycle>` `snapshot_id` | `forecast_posteriors` (already materialized, mx2t3-independent) |
| C-A2 | `src/events/triggers/forecast_snapshot_ready.py:847` `_FORECAST_TABLES` | `("source_run","source_run_coverage","ensemble_snapshots")` table-existence guard | Fork: under replacement flag require `("forecast_posteriors",)` | `forecast_posteriors` |
| C-A3 | `src/events/triggers/forecast_snapshot_ready.py:240-336` `classify_forecast_snapshot` | Member/step floor checks would fail (`0 >= 51`) | Add posterior-backed `COMPLETE` short-circuit when `members_json=NULL` and `readiness_status='LIVE_ELIGIBLE'` | Return `COMPLETE_POSTERIOR_BACKED` when posterior-sourced |
| C-A4 | `src/events/triggers/forecast_snapshot_ready.py:917-931` `_snapshot_from_join` / lines 539-548 `_snapshot_latest_join` | `members_json` threading into FSR dict | `members_json` → `[]` (empty); drop `_snapshot_latest_join` on posterior lane | Spine overrides anyway |
| C-B1 | `src/engine/event_reactor_adapter.py:11348` `_bound_forecast_snapshot_row_for_spine` call inside `_spine_multimodel_members_for_event` | Pins `causal_snapshot_id` against `ensemble_snapshots` | Fork: if `causal_snapshot_id.startswith("rmf-")` parse cycle from id tail; else keep `_bound_...` for legacy ids; add raw_model_forecasts MAX-cycle fallback (B2) | Parse from neutral id / raw_model_forecasts |
| C-C1 | `src/engine/event_reactor_adapter.py:6492` `_forecast_authority_payload_and_clock` | Reads `ensemble_snapshots` row + `read_executable_forecast(members_json)` for EVERY decision | Fork for replacement+non-day0: route to new `_forecast_authority_payload_from_posterior` | `forecast_posteriors` + `raw_model_forecasts` |
| C-C2 | `src/engine/event_reactor_adapter.py` (new function) | `_forecast_authority_payload_from_posterior` | NEW: build identical cert payload keys + EvidenceClock from `forecast_posteriors`+`raw_model_forecasts`; `members_json_source='raw_model_forecasts.multimodel'`; `source_run_id=posterior_identity_hash` | — |
| C-D | `src/decision_kernel/verifier.py` | `members_json_source` allow-list | Widen to accept `raw_model_forecasts.multimodel` alongside `ensemble_snapshots.daily_extrema` | — |

---

### GATE 2 — Day0 q re-sourcing (future; blocks full mx2t3 ingest stop)

Until this gate lands, `ensemble_snapshots.members_json` remains a live cold source for the day0 lane.

| # | File:Lines | Seam | Action | Repoint To |
|---|---|---|---|---|
| D1 | `src/engine/event_reactor_adapter.py:11583` `_market_analysis_from_event_snapshot` + `:11596` `_snapshot_members` | Day0 cold-member seed pool | Re-source day0 members off `raw_model_forecasts` (analogous to spine's `_spine_multimodel_members_for_event`) | `raw_model_forecasts` multi-model member set for the causal cycle date |
| D2 | `src/engine/event_reactor_adapter.py:10863` `_canonical_probability_and_fdr_proof` (day0 call at `:9844`) | Entire canonical forecast path for day0 | After D1, the cold-members read is eliminated; the rest of `_canonical_...` can be shared or simplified | — |
| D3 | `src/events/triggers/forecast_snapshot_ready.py:284/366/588/918` `members_json` length reads | `observed_members` completeness floor | After D1+D2, `members_json` is vestigial in the FSR dict for day0 too; can null out | N/A |

---

### NOT in strip scope (confirmed)

- `src/calibration/anchor_representativeness_debias.py` / `get_city_debias_c` in `replacement_forecast_materializer.py:1546-1548`: this is a per-city representativeness correction on the precision source, not the bias maze. Artifact-gated (INERT when `state/anchor_representativeness_debias.json` absent). Operator decision required separately.
- `src/calibration/ens_bias_repo.py` (load_bucket_residuals, _forecast_means): offline training-time reads, script-only callers, not live decision consumers.
- `src/data/tigge_db_fetcher.py:115/173` (legacy EnsembleSignal bundle): QUARANTINED per consumer_classification.md — strip after confirming no live `tigge_client` forecast-bundle caller and repointing ingest.
- Ingest writers (`ecmwf_open_data`, `ingest_main.py`), schema (`v2_schema.py`), readiness CARRIERs, `ensemble_snapshot_provenance.py`: producer/guard surfaces, out of strip scope.
