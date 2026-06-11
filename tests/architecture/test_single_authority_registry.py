# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 ~13:30Z "现在就处理本session发现的架构上
#   的严重缺陷" + the session's measured defect census: FIVE twin-authority instances in
#   one day (run-selection x2 lanes, freshness clock x2, tradeable-grade coverage applied
#   at 1 of 3 sites, buy_no evidence gate evaluated at 2 sites with the second starved of
#   inputs, suppression duty orphaned by the redeem removal). One rule living in two
#   places is THE dominant failure category of this codebase.
"""SINGLE-AUTHORITY REGISTRY — the antibody-of-antibodies.

Each entry below pins ONE rule to ONE authority (or pins that every legally-required
site carries the SAME clause). This file is the index: deleting or weakening a member
antibody breaks the registry, so the category cannot silently reopen. Add an entry
WHENEVER a new rule acquires a second site.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


# ---------------------------------------------------------------------------------
# 1. RUN SELECTION — which provider run to fetch has ONE authority: provider probes.
#    (2026-06-11 incident: now-lag guess in two lanes; nightly refusal crash + frozen
#    extras high-water.) Member antibody file must exist with its key pins.
# ---------------------------------------------------------------------------------
def test_member_run_selection_single_authority() -> None:
    member = ROOT / "tests" / "data" / "test_run_selection_single_authority.py"
    assert member.exists(), "member antibody deleted: run-selection single authority"
    source = _read("src/data/replacement_forecast_production.py")
    assert "_parse_cycle" not in source
    assert "_probe_resolved_available_cycle" in source


# ---------------------------------------------------------------------------------
# 2. FRESHNESS / STALENESS — the bound is derived ONCE (cycle_policy); readers BRAND,
#    never block (operator law 没有新的就用老的); readiness expiry derives from the
#    same constant. (Two clocks incident + the 12:00Z dark-scope incident.)
# ---------------------------------------------------------------------------------
def test_member_staleness_single_authority() -> None:
    assert (ROOT / "tests" / "data" / "test_cycle_staleness_derivation.py").exists()
    assert (ROOT / "tests" / "data" / "test_serve_freshest_available.py").exists()
    reader = _read("src/data/replacement_forecast_bundle_reader.py")
    assert reader.count('"BLOCKED", "REPLACEMENT_0_1_LIVE_AUTHORITY_READINESS_EXPIRED"') == 0
    assert "staleness_violations" in reader


# ---------------------------------------------------------------------------------
# 3. TRADEABLE-GRADE COVERAGE — "a covering posterior carries q_lcb" must hold at ALL
#    THREE coverage sites (queue _already_covered, plan builder, seed-discovery skip).
#    (2026-06-11 self-mask incident: the clause lived at one site; capture-missing rows
#    marked their own scopes covered and blocked their own repair.)
# ---------------------------------------------------------------------------------
def test_tradeable_grade_clause_present_at_all_three_coverage_sites() -> None:
    queue = _read("src/data/replacement_forecast_shadow_materialization_queue.py")
    plan = _read("src/data/replacement_forecast_current_target_plan.py")
    discovery = _read("src/data/replacement_forecast_seed_discovery.py")
    assert "q_lcb_json IS NOT NULL" in queue, "coverage site 1 (queue) lost the clause"
    assert "q_lcb_json IS NOT NULL" in plan, "coverage site 2 (plan builder) lost the clause"
    assert "q_lcb_json IS NOT NULL" in discovery, "coverage site 3 (seed discovery) lost the clause"


# ---------------------------------------------------------------------------------
# 4. ANCHOR AVAILABILITY PROBE mirrors EVERY downloader transport rung — a rung the
#    probe cannot see is a rung the run-selection authority starves. (Bucket rung
#    incident: 00Z bucket-only for hours while probes said unavailable.)
# ---------------------------------------------------------------------------------
def test_member_probe_mirrors_transport_ladder() -> None:
    availability = _read("src/data/replacement_cycle_availability.py")
    assert "probe_bucket_run_declared" in availability
    downloader = _read("scripts/download_replacement_forecast_current_targets.py")
    # every rung name referenced by the downloader has a probe-side mirror
    assert "_try_bucket_rung_three" in downloader
    assert "fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped" in downloader


# ---------------------------------------------------------------------------------
# 5. SETTLED-EXTERNAL SUPPRESSION DUTY — re-homed in the reconciler after the redeem
#    subsystem removal orphaned it (HK 06-09 latch freeze). The duty's member antibody
#    and the condition-id bridge (NO-side reachability) must stay.
# ---------------------------------------------------------------------------------
def test_member_settled_external_absorber() -> None:
    assert (ROOT / "tests" / "execution" / "test_settled_external_absorber.py").exists()
    reconcile = _read("src/execution/exchange_reconcile.py")
    assert "_market_calendar_terminal_evidence" in reconcile
    assert "_condition_ids_for_tokens" in reconcile  # NO-side bridge


# ---------------------------------------------------------------------------------
# 6. SEED BUDGET — discovery budget counts only WRITTEN seeds, nearest-target first.
#    (Head-of-line starvation: ten permanently-failing far-date targets ate the whole
#    5-min budget while tradeable scopes starved.)
# ---------------------------------------------------------------------------------
def test_member_seed_budget_counts_written_only() -> None:
    discovery = _read("src/data/replacement_forecast_seed_discovery.py")
    assert "len(written) >= max(1, int(limit))" in discovery
    assert "key=lambda row: (" in discovery  # nearest-target-date ASC sort


# ---------------------------------------------------------------------------------
# 7. CHUNKED-DURABLE CAPTURE PERSIST — multi-minute fetch passes persist per chunk,
#    never once-at-the-end. (Three whole-pass losses in one morning.)
# ---------------------------------------------------------------------------------
def test_member_chunked_durable_persist() -> None:
    dl = _read("src/data/u0r_multimodel_download.py")
    assert "_persist_chunk_with_lock_retry" in dl
    assert dl.count("_persist_chunk_with_lock_retry(") >= 3  # def + per-target + final prune


# ---------------------------------------------------------------------------------
# 8. DOWNLOAD OWNERSHIP — downloads live in the data-ingest daemon (its own daemon),
#    never scheduled on the restart-heavy forecast-live/trading daemons. (Operator,
#    repeatedly.)
# ---------------------------------------------------------------------------------
def test_member_downloads_owned_by_ingest_daemon() -> None:
    registry = _read("src/data/source_job_registry.py")
    assert "ingest_replacement_availability_poll" in registry
    daemon = _read("src/ingest/forecast_live_daemon.py")
    assert "MOVED to the data-ingest daemon" in daemon


# ---------------------------------------------------------------------------------
# 9. FUSION-UPGRADE INSTRUMENT-SET COMPARISON — "does a scope need re-materialization
#    because a strictly-larger decorrelated-provider set is now capturable" is computed
#    in exactly ONE function (replacement_fusion_upgrade_trigger), and the model->provider
#    FAMILY mapping it uses is the SAME object the materializer's served/missing check uses
#    (no parallel re-derivation). (Task #32: PARTIAL fusions never upgraded when late
#    instruments published — the missing-instrument dimension must not acquire a second site.)
# ---------------------------------------------------------------------------------
def test_member_fusion_upgrade_comparison_single_authority() -> None:
    member = ROOT / "tests" / "data" / "test_fusion_upgrade_trigger.py"
    assert member.exists(), "member antibody deleted: fusion-upgrade comparison single authority"
    trigger = _read("src/data/replacement_fusion_upgrade_trigger.py")
    # The comparison + the provider-family map live in ONE module.
    assert "def scope_capture_offers_larger_provider_set" in trigger
    assert "DECORRELATED_PROVIDER_FAMILIES" in trigger
    # The materializer's served/missing determination IMPORTS that same map — it must not keep a
    # parallel inline provider list (the old per-provider `if ... in used_models` ladder).
    materializer = _read("src/data/replacement_forecast_materializer.py")
    assert "from src.data.replacement_fusion_upgrade_trigger import" in materializer
    assert "decorrelated_provider_families_of" in materializer
    assert 'JMA/jma_seamless' not in materializer, (
        "materializer re-derived the provider family map inline — the model->family mapping must "
        "live ONLY in replacement_fusion_upgrade_trigger.DECORRELATED_PROVIDER_FAMILIES"
    )
    # The enqueue rides the EXISTING availability-poll lane (no new daemon) and writes into the
    # SAME seed surface the materialize cycle drains.
    production = _read("src/data/replacement_forecast_production.py")
    assert "_enqueue_fusion_upgrade_reseeds_if_needed" in production


# ---------------------------------------------------------------------------------
# 10. CURRENT-VALUE SERVING — "which endpoint serves each instrument's CURRENT value"
#     (single_runs always wins; a provider absent from single_runs at the cycle serves its
#     previous_runs row at the same natural key, branded — the generalized 没有新的就用老的
#     rule that superseded the gem-only exception) is decided by exactly ONE function,
#     consumed by BOTH the materializer's q path and the upgrade trigger's capturable set.
#     (Task #32 follow-up 2026-06-11: JMA publishes 00/12Z only, so at 06Z-cadence cycles it
#     could never appear in single_runs and the fusion silently ran served=4/5.)
# ---------------------------------------------------------------------------------
def test_member_current_value_serving_single_authority() -> None:
    member = ROOT / "tests" / "data" / "test_previous_runs_substitution.py"
    assert member.exists(), "member antibody deleted: previous-runs substitution serving"
    serving = _read("src/data/replacement_current_value_serving.py")
    assert "def read_current_instrument_values" in serving
    assert "PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS" in serving
    # The materializer's q path consumes the authority and keeps NO inline serving rule: the old
    # gem-only previous_runs query (the pre-generalization second site) must be dead.
    materializer = _read("src/data/replacement_forecast_materializer.py")
    assert "read_current_instrument_values" in materializer
    assert "AND model = 'gem_global'" not in materializer, (
        "materializer regrew an inline gem serving ladder — the current-value serving rule "
        "lives ONLY in replacement_current_value_serving.read_current_instrument_values"
    )


# ---------------------------------------------------------------------------------
# 11. SETTLEMENT-COVERAGE LICENSING SET — "which coverage verdict statuses license a
#     fused-bootstrap q_lcb for live" has ONE home (live_admission.SETTLEMENT_COVERAGE_
#     LICENSING_STATUSES), read by BOTH the buy-NO admission gate and the adapter's cert
#     credential. The admission gate and the cert layer read the SAME family verdict
#     (computed once on the replacement path, threaded — never recomputed). (Twin-
#     authority #7, 2026-06-11: the admission gate used the OLD source-brand vocabulary
#     and never saw the verdict — 12 positive-EV families killed per burst; category
#     inversion: record-BACKED bootstrap rejected, record-REFUTED-then-shrunk accepted.)
# ---------------------------------------------------------------------------------
def test_member_settlement_coverage_licensing_single_authority() -> None:
    member = ROOT / "tests" / "strategy" / "test_settlement_coverage_admission_licensing.py"
    assert member.exists(), "member antibody deleted: coverage-licensing single authority"
    admission = _read("src/strategy/live_inference/live_admission.py")
    assert "SETTLEMENT_COVERAGE_LICENSING_STATUSES" in admission
    adapter = _read("src/engine/event_reactor_adapter.py")
    # The cert-layer alias must POINT at the one home, never regrow an inline literal.
    assert (
        "_FUSED_BOOTSTRAP_COVERAGE_LICENSING_STATUSES = SETTLEMENT_COVERAGE_LICENSING_STATUSES"
        in adapter
    ), "cert licensing set must alias live_admission's single home"
    assert '_FUSED_BOOTSTRAP_COVERAGE_LICENSING_STATUSES = frozenset(' not in adapter, (
        "cert layer regrew an inline licensing frozenset — the set lives ONLY in "
        "live_admission.SETTLEMENT_COVERAGE_LICENSING_STATUSES"
    )
    # BOTH twin gate sites thread the verdict status (proof-generation + receipt-level).
    assert adapter.count("settlement_coverage_status=settlement_coverage_status") >= 1, (
        "proof-generation gate site lost the verdict threading"
    )
    reactor = _read("src/events/reactor.py")
    assert "settlement_coverage_status=receipt.settlement_coverage_status" in reactor, (
        "receipt-level twin gate site lost the verdict threading (21a4c14ee2 lesson)"
    )
    # The trigger's capturable set is the SAME function's key set — no endpoint SQL of its own.
    trigger = _read("src/data/replacement_fusion_upgrade_trigger.py")
    assert "read_current_instrument_values" in trigger
    assert "endpoint = 'single_runs'" not in trigger and "endpoint = 'previous_runs'" not in trigger, (
        "the upgrade trigger regrew its own endpoint-serving SQL — capturable must derive from "
        "the single serving authority"
    )
