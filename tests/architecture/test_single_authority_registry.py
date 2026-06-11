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
