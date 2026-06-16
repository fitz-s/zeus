# Dead Replacement-Forecast Promotion / Go-Live Apparatus — Consumer Audit & Surgical Removal Plan

- Created: 2026-06-16
- Last audited: 2026-06-16
- Authority basis: operator severance commit `b646f99339` (2026-06-08 — promotion/capital-objective evidence gate REMOVED from BOTH live-authority sites; live authority is FLAG-ONLY) + `54a53334a9` ("stop advertising the removed evidence gate"). Live authority = flag ladder `shadow -> veto -> trade_authority` only.
- Scope: READ-ONLY audit. No edits/deletes/git performed. Plan only.

## TL;DR

The promotion/go-live **readiness-verdict** apparatus is genuinely dead and removable, but it is NOT a clean island: one live tie remains. `main.py` calls **two JSON-parser functions** (`replacement_forecast_promotion_evidence_from_payload`, `replacement_forecast_capital_objective_evidence_from_payload`) from `go_live_report.py`, and the parsed objects thread through the LIVE `event_reactor_adapter.py` into `ReplacementForecastHookFactoryInput`. **Proven inert**: the runtime-policy resolver and the switch-decision evaluator both IGNORE these objects post-severance (resolver derives status from flags only; switch-decision only type-checks the field). So the decouple is behavior-identical: drop the two parsers in `main.py` and pass `None` at the two adapter call sites (already the default).

Three modules the prompt listed as candidate-dead are actually LIVE-FUSED or operationally live and MUST be removed from the deletion set: **`live_switch_surface`** (KEEP — live), **`switch_decision`** (KEEP — live), **`config_switch`** (KEEP — it is the operator's live settings-flag mutator, not a readiness verdict).

No standalone `replacement_forecast_capital_objective*` or `replacement_forecast_capital_replay*` files exist — those concepts live as functions inside `go_live_report.py`. No cron/runbook/CI/Makefile references any candidate script (manual one-shot tools only).

## Live-fused boundary (already-verified KEEP set — for reference)

- `replacement_forecast_runtime_policy` — OWNS the `ReplacementForecastPromotionEvidence` + `ReplacementForecastCapitalObjectiveEvidence` dataclasses (runtime_policy.py:35,106) + `resolve_replacement_forecast_runtime_policy`. 13 src importers incl. event_reactor_adapter.py:200-201, hook_factory, reactor_hook, production.
- `replacement_forecast_bundle_reader` — live q read (event_reactor_adapter).
- `replacement_forecast_refit_gate` — reactor adapter + hook factory.
- `replacement_forecast_production` / shadow materialize — produces live-consumed forecast_posteriors. Imports NONE of the candidate-dead cluster (verified empty grep).
- `replacement_forecast_readiness`, `guardrail_report`, `finetune_artifact`, `refit_handoff(_install)`, `current_fact_patch` — imported by live reactor_hook/hook_factory/refit chain. KEEP (not in candidate set).

## Reclassified OUT of the dead set (prompt listed as candidate-dead; they are NOT)

### replacement_forecast_live_switch_surface -> KEEP (LIVE-FUSED)
- hook_factory.py:14 imports REQUIRED_* constants + ReplacementForecastLiveSwitchInput + build_replacement_forecast_live_switch_report, and CALLS build_...(...) at hook_factory.py:501 inside the live _hook.
- switch_decision.py:7 imports ReplacementForecastLiveSwitchReport (KEEP module).
- main.py:6015 imports CURRENT_DATA_FACT_FILE, CURRENT_SOURCE_FACT_FILE (live fact-status read).
- current_fact_patch.py:10, refit_handoff_install.py:12 import REFIT_HANDOFF_FILE.
- Self-contained: imports only runtime_policy (KEEP). Verdict: KEEP.

### replacement_forecast_switch_decision -> KEEP (LIVE-FUSED)
- hook_factory.py:38 imports + CALLS evaluate_replacement_forecast_switch_decision(...) at hook_factory.py:524 inside the live _hook.
- reactor_hook.py:37 imports ReplacementForecastSwitchDecision and branches can-trade/veto on switch_decision.status (reactor_hook.py:283-307).
- Body (switch_decision.py:95-200) post-severance honest: verdict from policy.status + live_switch.reason_codes + readiness; capital_objective_evidence only type-checked (51-52), never gated. Verdict: KEEP.

### replacement_forecast_config_switch -> KEEP (operationally LIVE — settings mutator, not a verdict)
- apply_replacement_forecast_config_switch(settings_path) (config_switch.py:224) WRITES config/settings.json — the mechanism that flips the live flag ladder (drives the ARM).
- Driven by operator tool scripts/apply_replacement_forecast_shadow_veto_switch.py:19.
- Imported by NO live engine/production module, but deleting it removes the live flag-flip path. Distinct from the dead "not-ready/SHADOW verdict" surface. Verdict: KEEP (out of scope for this removal).

## Candidate-dead apparatus — classification table

| Module | src consumers | scripts consumers | tests | Classification | Exact action |
|---|---|---|---|---|---|
| replacement_forecast_go_live_report | main.py:5607,5635 (2 parser fns); live_dry_run.py:505 (fn-local back-ref) | report_..._go_live.py, plan_..._live_authority_switch.py, replay_downloaded_replacement_economic.py | test_..._go_live_report.py, test_availability_time_law.py, engine/test_replacement_0_1_authority_evidence_gate.py | NEEDS_DECOUPLE | Decouple the 2 main.py parser fns FIRST (Step 1), then delete the module. live_dry_run.py:505 back-ref dies with live_dry_run (same batch). |
| replacement_forecast_promotion_evidence (MODULE) | go_live_report.py:45 ONLY. (event_reactor_adapter.py:200-201 + main.py import the same-named DATACLASS from runtime_policy, NOT this module — verified false-positive.) | none | test_..._promotion_evidence.py | SAFE_DELETE | Delete file + test after go_live_report removed. |
| replacement_forecast_before_after_report | go_live_report.py:15; promotion_evidence.py:8 | none | test_..._before_after_report.py | SAFE_DELETE | Delete file + test (both consumers in same dead batch). |
| replacement_forecast_live_dry_run | go_live_report.py:36; runtime_wiring_audit.py:10; simple_switch_bundle.py:18. (db_writer_lock.py:660 = RO-URI allowlist STRING, not import.) | check_..._live_dry_run.py, rehearse_..._simple_switch.py, apply_..._shadow_veto_switch.py | test_..._live_dry_run.py | SAFE_DELETE (after batch) — BUT see <90% #1 | Delete file IFF veto-switch dry-run preflight is stripped. Remove RO-URI string at db_writer_lock.py:660. |
| replacement_forecast_simple_switch_evidence | none (src) | build_..._simple_switch_evidence.py | test_..._simple_switch_evidence.py, test_..._simple_switch_rehearsal.py | SAFE_DELETE | Delete file + test + driver. |
| replacement_forecast_simple_switch_bundle | none (src) | plan_..._simple_switch_bundle.py | test_..._simple_switch_bundle.py, test_..._simple_switch_rehearsal.py, test_..._shadow_veto_switch_apply.py | SAFE_DELETE | Delete file + test + driver; trim REPLACEMENT_SHADOW_TABLES use in shadow_veto_switch_apply test. |
| replacement_forecast_switch_decision | LIVE (hook_factory + reactor_hook) | none | several | KEEP | none |
| replacement_forecast_config_switch | only dead-cluster in src, but LIVE settings mutator via operator script | apply_..._shadow_veto_switch.py, plan_..._shadow_veto_config.py, rehearse_..., plan_..._live_authority_switch.py | several | KEEP | none (operational) |
| replacement_forecast_rollback_plan | go_live_report.py:57 ONLY | none | test_..._rollback_plan.py | SAFE_DELETE | Delete file + test. |
| replacement_forecast_live_switch_surface | LIVE (hook_factory etc.) | several | several | KEEP | none |
| replacement_forecast_runtime_wiring_audit | none (src) | audit_..._runtime_wiring.py | test_..._runtime_wiring_audit.py | SAFE_DELETE | Delete file + test + driver. Imports live_dry_run+live_switch_surface+config_switch; dies cleanly. |

## CRITICAL ENTANGLEMENT CHECK

Q: main.py:5605-5658 parses promotion_evidence + capital_objective_evidence INTO runtime_policy. Does runtime_policy USE them post-severance?

A: NO. Proven inert end-to-end.
1. resolve_replacement_forecast_runtime_policy(flags, *, promotion_evidence=None, capital_objective_evidence=None) (runtime_policy.py:234-311): body reads ONLY the 5 flags. The two evidence params are NEVER referenced in the body. Status from flag ladder. Default None.
2. The legacy gate replacement_live_authority_evidence_gate (runtime_policy.py:146-178, the ONLY place *_EVIDENCE_REQUIRED strings exist) is defined but never called by the resolver — documented "REMOVED and is NO LONGER a precondition... retained for shadow observability/receipts" (runtime_policy.py:270, 293-300).
3. evaluate_replacement_forecast_switch_decision (switch_decision.py:95-200): consumes policy.status, live_switch, readiness. capital_objective_evidence input field only TYPE-CHECKED (switch_decision.py:51-52), never used in any branch.
4. Live path: event_reactor_adapter.py threads promotion_evidence/capital_objective_evidence -> ReplacementForecastHookFactoryInput (event_reactor_adapter.py:1139-1146) -> resolve_...runtime_policy(...) (hook_factory.py:484-488, inert params) and into ReplacementForecastSwitchDecisionInput(capital_objective_evidence=...) (hook_factory.py:530, type-check only).

THEREFORE the main.py parsers are SAFE to remove: passing None (existing default) at the adapter call sites is behavior-identical. The parsers are the ONLY runtime tie from the dead cluster into live code.

Honesty test note: tests/test_switch_decision_live_policy_requires_capital_evidence_object.py constructs a genuine ReplacementForecastCapitalObjectiveEvidence and asserts LIVE_AUTHORITY_STATUS (passes on flags alone). Imports ONLY KEEP modules (switch_decision, live_switch_surface, runtime_policy, readiness, refit_gate) — does NOT depend on any SAFE_DELETE module, will not break. It pins the dataclass TYPE on the switch input, so the ReplacementForecastCapitalObjectiveEvidence dataclass (in runtime_policy, KEEP) must remain.

## ORDERED deletion plan (provably does not touch live arm/reactor/materialization/q-read)

### Step 0 — Pre-flight
- git grep -nE "replacement_forecast_go_live_report|_promotion_evidence_from_payload|_capital_objective_evidence_from_payload" matches only: src/main.py:5605-5658, go_live_report.py, live_dry_run.py:505, listed scripts/tests.
- Confirm resolver body still ignores evidence params (no regression since 2026-06-08).

### Step 1 — DECOUPLE the live tie (must precede any deletion)
In src/main.py:
1. Delete _replacement_forecast_promotion_evidence_from_settings (~5605-5630) and _replacement_forecast_capital_objective_evidence_from_settings (~5633-5658) — the only `from ...go_live_report import` statements (5607, 5635).
2. At the two live-adapter call sites — event_bound_final_intent_submit_adapter_from_trade_conn(...) (~6089-6090) and event_bound_no_submit_adapter_from_trade_conn(...) (~6129-6130) — replace the promotion/capital kwargs with literal None or delete them (adapter defaults both to None: event_reactor_adapter.py:1344-1345, 1459-1460).
3. Delete the dead local assignments at main.py:6009-6010.
- Leave _replacement_forecast_refit_decision_from_settings (5579) + 6008 assignment + 6088/6128 kwargs UNTOUCHED — refit_decision is live (refit_handoff KEEP).
- Behavior delta: zero.

### Step 2 — Delete leaf dead modules (no remaining src importer after Step 1)
- src/data/replacement_forecast_go_live_report.py
- src/data/replacement_forecast_promotion_evidence.py
- src/data/replacement_forecast_before_after_report.py
- src/data/replacement_forecast_rollback_plan.py
- src/data/replacement_forecast_runtime_wiring_audit.py
- src/data/replacement_forecast_simple_switch_bundle.py
- src/data/replacement_forecast_simple_switch_evidence.py
- src/data/replacement_forecast_live_dry_run.py  (only IF <90% #1 resolved to "strip preflight")

### Step 3 — Delete orphaned tests
- test_replacement_forecast_{go_live_report,promotion_evidence,before_after_report,rollback_plan,runtime_wiring_audit,simple_switch_bundle,simple_switch_evidence,live_dry_run}.py
- test_replacement_forecast_simple_switch_rehearsal.py (rehearsal of dead simple-switch flow)
- DO NOT blind-delete: tests/test_availability_time_law.py, tests/engine/test_replacement_0_1_authority_evidence_gate.py (PR399 honesty pins) — trim go_live_report import, see <90% #2.
- DO NOT delete: tests/test_replacement_forecast_shadow_veto_switch_apply.py (LIVE apply path) — trim only simple_switch_bundle.REPLACEMENT_SHADOW_TABLES, see <90% #3.

### Step 4 — Delete/repair driver scripts
Pure dead (delete): report_..._go_live.py, check_..._live_dry_run.py, build_..._simple_switch_evidence.py, plan_..._simple_switch_bundle.py, audit_..._runtime_wiring.py.
Flag before delete: plan_..._live_authority_switch.py (go-live planner, touches KEEP config_switch), replay_downloaded_replacement_economic.py (economic replay?), rehearse_..._simple_switch.py (touches KEEP modules).
MUST SURVIVE w/ repair: scripts/apply_replacement_forecast_shadow_veto_switch.py — operator LIVE flag-flip tool. Imports live_dry_run for a pre-apply dry-run preflight AND KEEP config_switch/live_switch_surface/current_fact_patch/refit_handoff_install/runtime_policy. Removing live_dry_run BREAKS this tool. Decision: keep live_dry_run for ops OR strip the preflight block. See <90% #1.

### Step 5 — Registry/manifest hygiene (non-code)
- Remove src/state/db_writer_lock.py:660 RO-URI allowlist string for replacement_forecast_live_dry_run.py (only if module deleted).
- Update source_rationale / test_topology / script_manifest registries for deleted files (run topology_doctor / map-maintenance before commit per Zeus governance law).

## <90% confidence — STAY pending human decision

1. replacement_forecast_live_dry_run is the load-bearing ambiguity. Dead from the readiness-verdict angle, BUT the LIVE operator flag-flip tool scripts/apply_replacement_forecast_shadow_veto_switch.py imports it for a pre-apply dry-run preflight. Decision: keep the preflight (then live_dry_run = KEEP-for-ops; runtime_wiring_audit + simple_switch_bundle can still die) or strip it. Until decided, live_dry_run.py STAYS.
2. tests/test_availability_time_law.py + tests/engine/test_replacement_0_1_authority_evidence_gate.py import go_live_report — PR399 authority/availability honesty pins, may assert absence/non-callability of the removed gate (antibody). Inspect; likely trim import, not delete.
3. tests/test_replacement_forecast_shadow_veto_switch_apply.py tests the LIVE apply path; keep, trim only simple_switch_bundle symbol.
4. scripts/replay_downloaded_replacement_economic.py — may still be an analysis utility; imports go_live_report so will break at Step 2 regardless; confirm repair-vs-delete.

## Provably SAFE now (>=90%), assuming decision #1 = "strip the preflight"
- Step 1 decouple (main.py parsers -> None): zero behavior change.
- Delete: go_live_report.py, promotion_evidence.py, before_after_report.py, rollback_plan.py, runtime_wiring_audit.py, simple_switch_bundle.py, simple_switch_evidence.py + their direct tests + their pure driver scripts.
- These touch NO live arm/reactor/materialization/q-read: confirmed production.py, event_reactor_adapter.py (imports dataclasses from runtime_policy, not these modules), hook_factory.py, reactor_hook.py, bundle_reader, refit_gate have zero import dependency on the SAFE_DELETE set.
