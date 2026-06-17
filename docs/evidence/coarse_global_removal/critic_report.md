# CRITIC REPORT — coarse-global removal (gfs_global / gem_global drop from T2 fusion)

- Reviewer role: adversarial CRITIC (writer != reviewer), read-only.
- Date: 2026-06-17
- Scope: the 12 files named in the brief. Working-tree diff vs HEAD (7017b398d7). The change is
  UNCOMMITTED in the working tree (not commit ffdeaaa749 — that was the earlier "add nests" step).
- Mode: started THOROUGH, escalated to ADVERSARIAL after confirming the primary HIGH finding.
- Test state: all 59 tests across the 6 changed test files PASS. The new RED-on-revert test was
  empirically verified SHARP (reverting the per-city intersection flips is_upgrade True -> RED).

NOTE ON WORKING-TREE FLUX: a concurrent agent is editing this tree. My FIRST `git diff` snapshot of
model_selection.py showed `# DELIBERATE BREAK FOR REVERT TEST` markers with gfs_global still in
DECORR_GLOBALS/NCEP_FAMILY; a re-read seconds later showed the CLEAN removal. I reviewed the CLEAN
state (gfs_global + gem_global fully removed from DECORR_GLOBALS, NCEP_FAMILY, GEM_FAMILY). If the
"DELIBERATE BREAK" variant is ever the committed state, it is a half-applied contract (comments +
completeness contract say dropped; selection vocabulary still contains gfs_global) and must be
rejected outright. CONFIRM the committed tree matches the clean state before merge.

---

## VERDICT: REVISE

The core design (domain-aware per-city completeness contract replacing the flat /5 count) is sound,
the dead-surface deletions are safe and verified, the test re-points are honest and several are
STRENGTHENED, and the fail-open direction is correctly conservative (toward PARTIAL/loud, never
toward silently-COMPLETE). One real defect blocks a clean accept: the expected-set is computed at
lead 0 while the fusion serves at the city-local lead, so CONUS/N-America cities at far lead
(>=3 for CMC, >=4 for NCEP) get a phantom PARTIAL flag AND re-fire the exact upgrade loop this
change exists to kill. It does not lose money or admit a degraded fusion, hence HIGH not CRITICAL,
but it reintroduces the failure mode in a different quadrant and must be fixed.

---

## HIGH — src/data/replacement_fusion_upgrade_trigger.py:102 (and materializer:1366) — lead-0 expected-set vs real-lead served-set => phantom PARTIAL + revived upgrade loop for CONUS/NA far-lead scopes

PROBLEM. `expected_provider_families_for_city` evaluates membership with `regional_eligible(member,
lead_days=0)`. The docstring claims lead 0 is "the most permissive lead — eligible at any longer
lead too." That is FALSE. Every regional/domain-gated nest has a `max_lead_days` CAP in
config/model_domain_polygons.yaml: gfs_hrrr=2, ncep_nbm_conus=3, gem_hrdps_continental=2. Lead-0
eligibility does NOT imply longer-lead eligibility — it implies the OPPOSITE (a model eligible at
lead 0 becomes INELIGIBLE past its cap). So for an in-domain (CONUS / N-America) city:

  lead | NCEP served? | CMC served? | expected (always, lead0) | verdict
   0-2 |  yes         |  yes        | {NCEP,CMC,...}            | OK
   3   |  yes (nbm)   |  NO         | expects CMC               | expected>served -> PARTIAL+upgrade
   >=4 |  NO          |  NO         | expects NCEP,CMC          | expected>served -> PARTIAL+upgrade

EVIDENCE (empirically run, not asserted):
- `expected_provider_families_for_city(Atlanta)` = `['CMC','DWD','JMA','NCEP','UKMO']` (lead-0 based).
- The change's OWN test `test_conus_beyond_nbm_lead_horizon_has_no_ncep_rep` (candidate_accrual, Atlanta
  lead_days=5) asserts `ncep == []` in used_models — i.e. selection legitimately drops NCEP at lead 5.
- So at Atlanta lead 5: `_expected_families` contains NCEP & CMC; `_served_families` (from real-lead
  used_models) contains neither -> `_missing_providers = [NCEP/..., CMC/...]` ->
  `_decorrelated_complete = False` -> `REPLACEMENT_Q_MODE_FUSED_NORMAL_PARTIAL`
  (replacement_forecast_materializer.py:2233-2236). Pre-change the same scope was FUSED_NORMAL_FULL
  (gfs_global, a pure global, served NCEP at any lead). This is a behavior REGRESSION, not a no-op.
- The upgrade loop: `read_current_instrument_values` is NOT lead-gated (serving.py:124 — "lead_days is
  derived and only REPORTED"), and the download fetches in-domain nests at ALL leads
  (_model_in_domain uses lead_days=0). So gfs_hrrr/gem_hrdps rows ARE persisted for Atlanta even at
  lead 5 -> `capturable` includes NCEP & CMC -> `capturable_expected = capturable & expected` keeps
  them -> `new_families = {NCEP,CMC} - served = {NCEP,CMC}` -> `is_upgrade = True`
  (replacement_fusion_upgrade_trigger.py:256-260). The re-materialized posterior at lead 5 AGAIN drops
  NCEP/CMC (over-horizon) -> served never catches expected -> the upgrade re-fires every cycle. This
  is precisely the "chase loops forever" failure the change's own comment (trigger.py:241) says it
  prevents — displaced from the non-CONUS quadrant to the CONUS-far-lead quadrant.

REACHABILITY / REALIST CHECK (why HIGH not CRITICAL, and why not downgraded below HIGH):
- Trade money: NOT blocked. Both FUSED_NORMAL_FULL and FUSED_NORMAL_PARTIAL grant live trade
  authority (`_replacement_trade_authority_status`/`live_q_carrier`, materializer:250). So a phantom
  PARTIAL does not stop a CONUS far-lead trade.
- Silent degradation: NONE. Fail-open is toward over-large expected = MORE likely PARTIAL = loud, never
  toward falsely-COMPLETE. Direction is correct.
- Bounded, not infinite-tight: the `fusion_upgrade_enqueues` UNIQUE marker (trigger.py:_record_enqueue)
  caps it at ONE re-seed per (scope, cycle, capturable-family-set). But `source_cycle_time` changes each
  cycle -> a fresh marker -> one wasted re-seed per cycle per affected scope, indefinitely.
- The cost that keeps this at HIGH: (a) permanent mislabel of CONUS/NA far-lead fusions as PARTIAL
  (corrupts the completeness telemetry the antibody exists to provide); (b) a wasted re-materialization
  seed every cycle for every affected far-lead CONUS/NA scope, drawn from the SHARED `limit=50`
  nearest-first budget (enqueue_fusion_upgrade_reseeds) — far-date CONUS PARTIAL scopes can crowd out
  the budget that day1/day2/day3 (lead 1-3) MONEY scopes need (the day0 guard only protects lead 0).
  Reachable in production: weather markets routinely list several days out; the current-target plan
  has no upper lead cap.

FIX. Thread the real materialization lead into the expected-set so expected and served use the SAME
lead gate:
  `def expected_provider_families_for_city(lat, lon, *, lead_days: int) -> frozenset[str]:`
  and call `regional_eligible(member, lat=lat, lon=lon, lead_days=lead_days)`.
  - Materializer (replacement_forecast_materializer.py:1366): pass the already-computed `lead_days`.
  - Upgrade trigger (scope_capture_offers_larger_provider_set): the comparison must use the lead the
    posterior was/will be served at. Simplest correct option: derive the served posterior's lead (the
    `_latest_posterior_served` row carries it / the seed scope knows target_date->lead), pass it in.
    If a single representative lead is impractical there, gate `capturable_expected` so a family is only
    an upgrade target when it is BOTH capturable AND servable-at-that-scope's-lead — never merely
    lead-0-servable.
  Keep the fail-open default (all families) only for the truly-coords-missing case. Delete the false
  "eligible at any longer lead too" sentence from the docstring.

ADD TEST (the coverage gap that let this through): a CONUS city (Atlanta) at lead 5 must be
COMPLETE-on-{served} with is_upgrade=False — the completeness analogue of the existing
selection-only `test_conus_beyond_nbm_lead_horizon_has_no_ncep_rep`. Today only the non-CONUS Tokyo
case is covered; the in-domain far-lead case (the regression) is untested.

---

## MEDIUM — src/data/bayes_precision_fusion_download.py:295,302,117,120 + capture.py:111-114 — now-dead residual surface for the dropped globals (inert, not yet misleading-as-false)

The following still name gfs_global / gem_global after the models left the fetch set
(`BAYES_PRECISION_FUSION_EXTRA_MODELS` = anchor + GLOBAL_LIKELIHOOD_MODELS + REGIONAL_MODELS +
icon_seamless; the loop at download.py:1004 never iterates the dropped globals — VERIFIED):
- `SINGLE_RUNS_UNSERVABLE_MODELS = ("gem_global",)` (download.py:295) — consulted only for fetched
  models; now never reached.
- `MODEL_PUBLISH_CYCLE_HOURS["gem_global"]` (download.py:~308) — same, dead.
- `OPENMETEO_PREVIOUS_RUNS_SOURCE_ID["gfs_global"]/["gem_global"]` (download.py:117,120) — dead `.get`
  keys.
- `OPENMETEO_MODEL_IDS["gfs_global"]/["gem_global"]` (capture.py:111,113) — dead id-translation keys.
- forecast_source_registry OPENMETEO_PREVIOUS_RUNS_MODEL_SOURCE_MAP still maps gfs_global/gem_global
  (registry.py:156,164).

These are INERT (consumed via `.get` only for models in the active fetch set), not actively false —
the OM id / source-id mappings they assert remain technically correct. So this is dead surface, not a
correctness bug. It is MEDIUM because dead model-identity surface in the money-path download module is
exactly the "live names what it doesn't use = bug risk" class the operator law targets, and a future
reader can mistake these for live config. RECOMMEND deleting the gfs_global/gem_global entries from
all five maps in the SAME change (the registry de-bias-history specs gfs_previous_runs/gem_previous_runs
are correctly KEPT — those interpret still-persisted history rows as they age out). If kept, they must
be kept everywhere consistently; right now the forward registry specs were deleted (good) but these
sibling maps were not, which is asymmetric.

---

## LOW — replacement_fusion_upgrade_trigger.py docstring (expected_provider_families_for_city) — incorrect rationale string

The "(the most permissive lead — eligible at any longer lead too)" claim is the conceptual error that
produced the HIGH finding. Even after the HIGH fix, scrub this sentence; it will mislead the next
editor into re-introducing a lead-0 shortcut. Tie the docstring to the explicit max_lead_days caps.

---

## What's verified SAFE (hazards handled)

1. Fail-direction (hazard 1): CORRECT. Materializer calls expected_provider_families_for_city directly
   with already-validated lat/lon (override returns None earlier if city unknown). Any internal
   exception fails open to ALL families -> over-large expected -> PARTIAL/loud, never falsely-COMPLETE.
   `_city_latlon`->None in the trigger fails open to all-families = pre-removal comparison. No silent
   degraded-fusion admission. (The HIGH finding is a phantom-PARTIAL/false-positive, the SAFE direction.)
2. Domain-gate authority agreement (hazard 2, geographic part): VERIFIED. All three sites key on the
   same gate. Download `_DOMAIN_GATED_MODELS` = REGIONAL_MODELS u {icon_eu} u {ncep_nbm,ukmo_uk} ==
   selection/expected `_REGIONAL_DOMAIN_KEY` (same 7 models: icon_d2, arome, icon_eu, ncep_nbm, ukmo_uk,
   gfs_hrrr, gem_hrdps). All route through the single `regional_eligible` loading the single
   model_domain_polygons.yaml. All 7 have polygon entries (no fail-CLOSED-to-None). The LEAD part is the
   HIGH finding.
3. served subset expected (hazard 3): SAFE. served = families(used_models@real-lead); expected =
   families servable@lead0. Since lead0 is the minimum and regional_eligible only tightens with lead,
   and pure globals are in both, served ⊆ expected always. `_decorrelated_served = expected - missing`
   cannot go negative; it is a recording-only provenance field (no gate consumes it).
4. Registry trim (hazard 4): SAFE. The removed forward specs openmeteo_gfs_global/openmeteo_gem_global
   were vestigial — the live forward source_id is `<model>_single_runs`, never `openmeteo_<model>`; no
   importer of those two SOURCES keys exists. gfs_previous_runs/gem_previous_runs (de-bias history)
   correctly RETAINED.
5. Dead duplicate deletion in bayes_precision_fusion.py: SAFE and GOOD. The module-local
   DECORR_GLOBALS/ICON_EU_MODEL/REGIONAL_MODELS were genuinely dead (no importer anywhere, no in-file
   use — grep VERIFIED) AND stale (still listed gfs_global/gem_global). Deleting them removes a real
   drift trap. Single authority (model_selection) now holds the model-set vocabulary.
6. Test integrity (hazard 6): HONEST. The enum fixture/assertion flips SHADOW_ONLY -> DIAGNOSTIC_ONLY
   are corrections-to-truth: the forecast_posteriors CHECK was migrated to ('DIAGNOSTIC_ONLY',
   'LIVE_AUTHORITY') (materializer:304-330) and raw_model_forecasts writes DIAGNOSTIC_ONLY — SHADOW_ONLY
   is no longer a valid value, so the old assertions were stale, not stricter. No assertion was loosened:
   the download "loud-not-silent" test kept its full 3-part structure (in dropped, NOT in
   domain_excluded, in global_models_unavailable) and only re-pointed the model gfs_global->icon_global.
   The gem/gfs single+previous-runs test was STRENGTHENED (both legs, both models, + positive control).
   The model_selection_gate test added `gfs_global/gem_global not in used_models`. The new RED-on-revert
   `test_non_conus_city_excludes_absent_ncep_cmc_no_phantom_upgrade` REACHES its assertions and is SHARP
   (empirically: reverting the intersection -> is_upgrade True, new_families ['NCEP'] -> RED). The one
   genuinely deleted coverage is the CMC-via-previous_runs capturability path (test_gem_previous_runs_
   counts_as_capturable), which is acceptable since gem_hrdps now serves via single_runs; the
   previous_runs-substitution mechanism itself remains covered by the JMA case and the
   materializer_wiring previous_runs-substitution test.
7. Out-of-scope behavior (hazard 7): the anchor de-bias (ecmwf_ifs / ecmwf_ifs025 / ecmwf_previous_runs
   / openmeteo_ecmwf_ifs_9km) is UNTOUCHED. The trigger SOURCE_id and the anchor's NON-provider status
   are unchanged. CONUS/NA cities at lead<=2 are unaffected (nests serve as before); the only changed
   behavior is the intended global drop plus the unintended HIGH far-lead phantom-PARTIAL.

## Upgrade path
Fix the HIGH (thread real lead into expected-set on BOTH the materializer and the upgrade-trigger sites;
add the CONUS far-lead completeness test). Clean the MEDIUM dead surface in the same change. Then ACCEPT.
