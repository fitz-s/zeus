# Created: 2026-10-01
# Authority basis: REQ-20261001-184803-0b7474; last validated whole-posterior serving.
"""Input continuity and invalidity are different predicates; no production DBs."""
from __future__ import annotations
import ast
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
from src.data import replacement_input_hwm as H
from src.data import replacement_current_value_serving as C
from src.data import replacement_forecast_bundle_reader as B
from tests import test_replacement_forecast_bundle_reader as F
from tests.test_replacement_forecast_bundle_reader import _shanghai_reader_current_certificate

UTC=timezone.utc
CYCLE=datetime(2026,10,1,tzinfo=UTC)
COMPUTED=CYCLE+timedelta(hours=8)
NOW=COMPUTED+timedelta(hours=1)

def _component(monkeypatch, *, scenario="ens", fault=None):
    """Typed producer-I/O boundary doubles; the actual HWM control flow runs."""
    conn=sqlite3.connect(":memory:")
    proof={"artifact_id":1,"entity_body_sha256":"a"*64,
           "capture_receipt_artifact_id":2,"capture_receipt_sha256":"b"*64}
    old=C.ServedInstrumentValue(value_c=20,raw_model_forecast_id=1,
        served_via="single_runs",served_cycle=CYCLE.isoformat(),
        captured_at=(CYCLE+timedelta(hours=7)).isoformat(),age_hours=7,
        lead_days=1,physical_response=proof)
    new=old
    if scenario in {"raw","proof"}:
        new=replace(old,raw_model_forecast_id=3,served_cycle=(CYCLE+timedelta(hours=6)).isoformat(),
                    physical_response={**proof,"artifact_id":3})
    elif scenario=="late":
        new=replace(old,raw_model_forecast_id=3,captured_at=(COMPUTED+timedelta(minutes=10)).isoformat())
    provenance={"openmeteo_anchor_artifact_id":9,"bayes_precision_fusion":{
        "used_models":["icon_global"],"current_evidence_shape":{"source_cycle_time":CYCLE.isoformat()},
        "current_value_serving":{"icon_global":{"raw_model_forecast_id":1,
            "served_cycle":old.served_cycle,"captured_at":old.captured_at,"physical_response":proof}}}}
    if fault=="causality":provenance["bayes_precision_fusion"]["current_value_serving"]["icon_global"]["captured_at"]=NOW.isoformat()
    if fault=="clock":provenance["bayes_precision_fusion"]["current_value_serving"]["icon_global"]["served_cycle"]="not-a-clock"
    frozen={} if fault=="missing" else {1:replace(old,physical_response={**proof,"artifact_id":999})} if fault=="proof" else {1:old}
    monkeypatch.setattr(C,"read_consumed_instrument_values",lambda *a,**k:frozen,raising=False)
    monkeypatch.setattr(C,"read_current_instrument_values",lambda *a,**k:{} if scenario=="absent_successor" else {"icon_global":new})
    monkeypatch.setattr(H,"_latest_eligible_ensemble_input_mark",lambda *a,**k:(10,CYCLE+timedelta(hours=6)) if scenario=="ens" else (1,CYCLE))
    monkeypatch.setattr(H,"latest_raw_artifact_input_cycle",lambda *a,**k:CYCLE+timedelta(hours=6) if scenario=="anchor" else CYCLE)
    monkeypatch.setattr(H,"_exact_consumed_anchor_artifact_cycle",lambda *a,**k:("basis=openmeteo_anchor_artifact_payload_identity_mismatch",None) if fault=="anchor" else (None,CYCLE))
    context=dict(city="City",target_date="2026-10-03",metric="high",decision_time=NOW,
        posterior_source_cycle_time=CYCLE,posterior_computed_at=COMPUTED,posterior_provenance=provenance)
    return conn,context

@pytest.mark.parametrize("scenario,basis",[("ens","current_ensemble_snapshot_superseded"),
    ("raw","current_value_serving_physical_proof_dependency_changed"),
    ("late","used_raw_model_forecasts_same_cycle_late_input"),
    ("anchor","source_cycle_time_raw_forecast_artifacts_lag"),
    ("absent_successor","current_value_serving_successor_unavailable")])
@pytest.mark.parametrize("held",[False,True])
def test_input_continuity_same_served_law_keeps_builder_debt(monkeypatch,scenario,basis,held):
    conn,context=_component(monkeypatch,scenario=scenario)
    try:
        assert H.replacement_live_input_lag_reason(conn,**context,held_redecision=held) is None
        assert basis in H.replacement_input_refresh_reason(conn,**context)
    finally:conn.close()

@pytest.mark.parametrize("fault,basis",[("missing","consumed_proof_unverifiable"),
    ("proof","consumed_physical_proof_invalid"),("causality","consumed_input_after_posterior"),
    ("clock","current_value_serving_provenance_unverifiable"),
    ("anchor","openmeteo_anchor_artifact_payload_identity_mismatch")])
def test_input_continuity_new_ens_never_masks_intrinsic_invalidity(monkeypatch,fault,basis):
    conn,context=_component(monkeypatch,scenario="ens",fault=fault)
    try:
        assert basis in H.replacement_live_input_lag_reason(conn,**context)
    finally:conn.close()

def test_input_continuity_witness_preserves_consumed_clocks(monkeypatch):
    conn,context=_component(monkeypatch)
    try:
        out={}
        assert H.replacement_live_input_lag_reason(conn,**context,input_witness_out=out) is None
        assert out["consumed_source_cycle_time"]==CYCLE.isoformat()
        assert out["posterior_computed_at"]==COMPUTED.isoformat()
        assert out["consumed_model_cycles"]=={"icon_global":CYCLE.isoformat()}
        assert out["ensemble_cycle_lag_hours"]==6
        assert out["source_cycle_age_hours"]==9
        assert out["blocking_reason"] is None and out["refresh_reasons"]
    finally:conn.close()

@pytest.mark.parametrize("purpose",tuple(B.ReplacementForecastAuthorityPurpose))
def test_input_continuity_blocked_optional_successor_retains_q(_shanghai_reader_current_certificate,monkeypatch,purpose):
    normal=_shanghai_reader_current_certificate
    before=B.read_replacement_forecast_bundle(normal.conn,**normal.kwargs,authority_purpose=purpose)
    old_ready=tuple(tuple(r) for r in normal.conn.execute("SELECT * FROM readiness_state WHERE track='soft_anchor_posterior'"))
    newer_context=F._reader_next_native_cycle(normal,monkeypatch)
    newer=next(newer_context)
    try:
        from src.data import replacement_forecast_materializer as M
        def blocked(*a,**k):raise ValueError("DAY0_NOAA_PRELIMINARY_CARRIER_VECTOR_MISSING")
        monkeypatch.setattr(M,"_compute_posterior_payload",blocked)
        attempted=M.prepare_replacement_forecast_live(normal.conn,normal.request)
        assert attempted.status=="BLOCKED" and attempted.readiness_id is None
        assert tuple(tuple(r) for r in normal.conn.execute("SELECT * FROM readiness_state WHERE track='soft_anchor_posterior'"))==old_ready
        out=B.read_replacement_forecast_bundle(normal.conn,**{**newer.kwargs,"authority_purpose":purpose})
        assert out.ok,out.reason_code
        assert out.bundle.q==before.bundle.q and out.bundle.q_lcb==before.bundle.q_lcb
        assert out.bundle.posterior_identity_hash==before.bundle.posterior_identity_hash
        assert out.bundle.source_cycle_time==before.bundle.source_cycle_time
        assert out.bundle.computed_at==before.bundle.computed_at
        assert out.bundle.input_hwm_witness["ensemble_cycle_lag_hours"]==6
    finally:next(newer_context,None)

def _c3_q(normal,cut):
    """Live C3's readiness-bound q identity for the certificate's family."""
    from src.execution.staleness_cancel import read_current_family_q_versions
    family=(normal.row["city"],normal.row["target_date"],normal.row["temperature_metric"])
    return read_current_family_q_versions(normal.conn,[family],now=cut)[family]

def test_input_continuity_new_ens_does_not_change_c3_q_identity(_shanghai_reader_current_certificate,monkeypatch):
    normal=_shanghai_reader_current_certificate
    assert _c3_q(normal,normal.request.computed_at)==normal.row["posterior_identity_hash"]
    newer_context=F._reader_next_native_cycle(normal,monkeypatch);newer=next(newer_context)
    try:
        # The landing is refresh debt for the builders, not C3 authority: the
        # standing rest keeps the same posterior q-version.
        assert H._latest_eligible_ensemble_input_mark(normal.conn,city=normal.row["city"],
            target_date=normal.row["target_date"],metric=normal.row["temperature_metric"],
            decision_time=newer.cut)[1]>datetime.fromisoformat(normal.row["source_cycle_time"])
        assert _c3_q(normal,newer.cut)==normal.row["posterior_identity_hash"]
        assert "current_ensemble_snapshot_superseded" in H.replacement_input_refresh_reason(normal.conn,
            city=normal.row["city"],target_date=normal.row["target_date"],metric=normal.row["temperature_metric"],
            decision_time=newer.cut,posterior_source_cycle_time=normal.row["source_cycle_time"],
            posterior_computed_at=normal.row["computed_at"],posterior_provenance=json.loads(normal.row["provenance_json"]))
    finally:next(newer_context,None)

@pytest.mark.parametrize("fault,basis",[("unknown","replacement_input_hwm_read_unavailable:OperationalError"),
    ("invalid","current_value_serving_consumed_proof_unverifiable")])
def test_input_continuity_c3_unknown_or_invalid_consumed_authority_is_protective(
        _shanghai_reader_current_certificate,monkeypatch,fault,basis):
    normal=_shanghai_reader_current_certificate
    if fault=="unknown":
        def unknown(*a,**k):raise sqlite3.OperationalError("interrupted")
        monkeypatch.setattr(H,"replacement_live_input_lag_reason",unknown)
    else:
        monkeypatch.setattr(C,"read_consumed_instrument_values",lambda *a,**k:{},raising=False)
    q=_c3_q(normal,normal.request.computed_at)
    assert q.startswith(f"__Q_AUTHORITY_BLOCKED__:{normal.row['posterior_identity_hash']}:") and basis in q

def test_input_continuity_unknown_active_readiness_is_not_last_good_fallback(_shanghai_reader_current_certificate):
    normal=_shanghai_reader_current_certificate
    bad=replace(normal.readiness,status="BLOCKED")
    out=B.read_replacement_forecast_bundle(normal.conn,**{**normal.kwargs,"readiness":bad})
    assert not out.ok and out.reason_code=="REPLACEMENT_READINESS_NOT_READY"

@pytest.mark.parametrize("relative",["src/data/replacement_forecast_current_target_plan.py",
    "src/data/replacement_forecast_live_materialization_queue.py"])
def test_input_continuity_builder_consumer_uses_refresh_projection(relative):
    root=Path(H.__file__).resolve().parents[2]
    tree=ast.parse((root/relative).read_text())
    calls={n.func.id for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
    assert "replacement_input_refresh_reason" in calls
    assert "replacement_live_input_lag_reason" not in calls

@pytest.mark.parametrize("metric",["high","low"])
def test_input_continuity_exact_consumed_reader_binds_model_and_metric(_shanghai_reader_current_certificate,metric):
    normal=_shanghai_reader_current_certificate
    provenance=json.loads(normal.row["provenance_json"])
    item=provenance["bayes_precision_fusion"]["current_value_serving"]["icon_global"]
    raw_id=item["raw_model_forecast_id"]
    context=dict(city=normal.row["city"],metric=metric,target_date=normal.row["target_date"],
                 materialized_at_iso=normal.row["computed_at"])
    out=C.read_consumed_instrument_values(normal.conn,**context,consumed_models={raw_id:"icon_global"})
    assert (raw_id in out) is (metric=="high")
    assert not C.read_consumed_instrument_values(normal.conn,**context,consumed_models={raw_id:"gfs_hrrr"})

@pytest.mark.parametrize("field,value",[("posterior_computed_at",None),
    ("posterior_computed_at",NOW+timedelta(hours=1)),("metric","unknown")])
def test_input_continuity_unknown_original_clock_and_metric_fail_closed(monkeypatch,field,value):
    conn,context=_component(monkeypatch)
    try:
        context[field]=value
        reason=H.replacement_live_input_lag_reason(conn,**context)
        assert "unverifiable" in reason
    finally:conn.close()

def test_input_continuity_coverage_clock_admits_a_later_posterior(monkeypatch):
    """Coverage asks whether a posterior at/after the requested clock exists;
    serving asks whether it was possessed at decision time."""
    conn,context=_component(monkeypatch,scenario="none")
    try:
        context["decision_time"]=COMPUTED-timedelta(minutes=1)
        assert H.replacement_input_refresh_reason(conn,**context) is None
        assert "unverifiable" in H.replacement_live_input_lag_reason(conn,**context)
    finally:conn.close()

@pytest.mark.parametrize("direction",["buy_yes","buy_no"])
def test_input_continuity_held_loader_serves_same_q_and_lag(_shanghai_reader_current_certificate,monkeypatch,direction):
    from src.engine.position_belief import load_replacement_belief
    normal=_shanghai_reader_current_certificate
    newer_context=F._reader_next_native_cycle(normal,monkeypatch);newer=next(newer_context)
    try:
        path=next(r[2] for r in normal.conn.execute("PRAGMA database_list") if r[1]=="main")
        q=json.loads(normal.row["q_json"]);label=next(iter(q))
        belief=load_replacement_belief(city=normal.row["city"],target_date=normal.row["target_date"],
            temperature_metric=normal.row["temperature_metric"],bin_label=label,direction=direction,
            now=newer.cut,db_path=path)
        assert belief is not None and belief.fresh
        assert belief.q_yes_bin==pytest.approx(q[label])
        assert belief.posterior_id==str(normal.row["posterior_id"])
        assert belief.input_hwm_witness["ensemble_cycle_lag_hours"]==6
    finally:next(newer_context,None)

def test_input_continuity_held_loader_unknown_hwm_is_not_fresh(_shanghai_reader_current_certificate,monkeypatch):
    from src.engine.position_belief import load_replacement_belief
    normal=_shanghai_reader_current_certificate
    path=next(r[2] for r in normal.conn.execute("PRAGMA database_list") if r[1]=="main")
    def fail(*a,**k):raise sqlite3.OperationalError("interrupted")
    monkeypatch.setattr(H,"replacement_live_input_lag_reason",fail)
    label=next(iter(json.loads(normal.row["q_json"])))
    belief=load_replacement_belief(city=normal.row["city"],target_date=normal.row["target_date"],
        temperature_metric=normal.row["temperature_metric"],bin_label=label,direction="buy_yes",
        now=normal.request.computed_at,db_path=path)
    assert belief is not None and not belief.fresh
    assert "read_unavailable" in belief.raw_input_lag_reason

def test_input_continuity_held_belief_keeps_its_posterior_across_a_newer_icon_proof(_shanghai_reader_current_certificate,monkeypatch):
    # Live 10-02: held beliefs went stale on icon_global
    # physical_proof_dependency_changed. A newer provider proof is successor
    # debt; only the consumed proof failing to read makes the held belief stale.
    from src.engine.position_belief import load_replacement_belief
    normal=_shanghai_reader_current_certificate
    path=next(r[2] for r in normal.conn.execute("PRAGMA database_list") if r[1]=="main")
    label=next(iter(json.loads(normal.row["q_json"])))
    def belief():
        return load_replacement_belief(city=normal.row["city"],target_date=normal.row["target_date"],
            temperature_metric=normal.row["temperature_metric"],bin_label=label,direction="buy_yes",
            now=normal.request.computed_at,db_path=path)
    before=belief()
    assert before is not None and before.fresh
    F._reader_new_icon_cycle(normal)
    normal.conn.commit()
    assert "current_value_serving_physical_proof_dependency_changed:model=icon_global" in H.replacement_input_refresh_reason(
        normal.conn,city=normal.row["city"],target_date=normal.row["target_date"],metric=normal.row["temperature_metric"],
        decision_time=normal.request.computed_at,posterior_source_cycle_time=normal.row["source_cycle_time"],
        posterior_computed_at=normal.row["computed_at"],posterior_provenance=json.loads(normal.row["provenance_json"]))
    after=belief()
    assert after is not None and after.fresh and after.raw_input_lag_reason is None
    assert (after.posterior_id,after.q_yes_bin)==(before.posterior_id,before.q_yes_bin)
    def unknown(*a,**k):raise sqlite3.OperationalError("interrupted")
    monkeypatch.setattr(C,"read_consumed_instrument_values",unknown)
    blocked=belief()
    assert blocked is not None and not blocked.fresh
    assert blocked.raw_input_lag_reason.startswith("basis=consumed_physical_proof_read_unavailable")

def test_input_continuity_executor_component_does_not_veto_successor(_shanghai_reader_current_certificate,monkeypatch):
    from src.state import db
    normal=_shanghai_reader_current_certificate
    root=Path(H.__file__).resolve().parents[2]
    text=(root/"src/execution/executor.py").read_text()
    node=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)
              and n.name=="_entry_replacement_input_hwm_component")
    family=(normal.row["city"],normal.row["target_date"],normal.row["temperature_metric"])
    env={"_entry_replacement_family_from_snapshot":lambda *a:family,
         "_parse_sqlite_timestamp":lambda value:datetime.fromisoformat(str(value)),
         "_capability_component":lambda name,allowed=True,**kw:{"allowed":allowed,**kw}}
    exec(compile("from __future__ import annotations\n"+ast.get_source_segment(text,node),
                 "exact_executor_hwm_source","exec"),env)
    class Borrowed:
        def close(self):pass
        def __getattr__(self,key):return getattr(normal.conn,key)
    monkeypatch.setattr(db,"get_forecasts_connection_read_only",lambda:Borrowed())
    newer_context=F._reader_next_native_cycle(normal,monkeypatch);newer=next(newer_context)
    try:
        intent=SimpleNamespace(executable_snapshot_id="bound-fixture",decision_source_context=SimpleNamespace(
            source_id=normal.row["source_id"],forecast_issue_time=normal.row["source_cycle_time"],
            forecast_fetch_time=normal.row["computed_at"],decision_time=newer.cut.isoformat()))
        out=env[node.name](normal.conn,intent)
        assert out["allowed"],out
        assert out["input_hwm_witness"]["ensemble_cycle_lag_hours"]==6
    finally:next(newer_context,None)

def test_input_continuity_actual_queue_and_target_plan_keep_successor_work(_shanghai_reader_current_certificate,monkeypatch):
    from src.data import replacement_forecast_current_target_plan as P
    from src.data import replacement_forecast_live_materialization_queue as Q
    normal=_shanghai_reader_current_certificate
    newer_context=F._reader_next_native_cycle(normal,monkeypatch);newer=next(newer_context)
    try:
        assert B.read_replacement_forecast_bundle(normal.conn,**newer.kwargs).ok
        args=dict(city=normal.row["city"],target_date=normal.row["target_date"],
            temperature_metric=normal.row["temperature_metric"],decision_time=newer.cut,
            baseline_source_run_id=normal.request.baseline_source_run_id,
            openmeteo_source_run_id=normal.request.openmeteo_source_run_id,
            posterior_tradeable_grade_clause="")
        reason=P._covering_posterior_input_lag_reason(normal.conn,**args)
        assert "current_ensemble_snapshot_superseded" in reason
        path=next(r[2] for r in normal.conn.execute("PRAGMA database_list") if r[1]=="main")
        seed={k:v for k,v in args.items() if k not in {"decision_time","posterior_tradeable_grade_clause"}}
        seed["computed_at"]=newer.cut.isoformat()
        assert Q._seed_already_covered(forecast_db=path,forecast_conn=normal.conn,seed=seed) is False
    finally:next(newer_context,None)


def test_input_continuity_uncertified_candidate_cannot_replace_active_pointer(_shanghai_reader_current_certificate,monkeypatch):
    from src.data import replacement_forecast_materializer as M
    normal=_shanghai_reader_current_certificate
    prepared=M.prepare_replacement_forecast_live(normal.conn,normal.request)
    assert isinstance(prepared,M.PreparedReplacementForecastMaterialization)
    original=tuple(tuple(r) for r in normal.conn.execute("SELECT * FROM readiness_state WHERE track='soft_anchor_posterior'"))
    real=M._build_readiness
    monkeypatch.setattr(M,"_build_readiness",lambda *a,**kw:replace(real(*a,**kw),status="BLOCKED",reason_codes=("TEST_UNCERTIFIED_SUCCESSOR",)))
    normal.conn.execute("SAVEPOINT blocked_successor")
    try:
        result=M.write_prepared_replacement_forecast_live(normal.conn,prepared)
        assert result.status=="BLOCKED" and result.readiness_id is None
        assert tuple(tuple(r) for r in normal.conn.execute("SELECT * FROM readiness_state WHERE track='soft_anchor_posterior'"))==original
    finally:
        normal.conn.execute("ROLLBACK TO blocked_successor");normal.conn.execute("RELEASE blocked_successor")


def test_input_continuity_changes_no_selection_or_probability_revision():
    # Continuity changes which certified posterior is available, an input to
    # the selector, not its feasible-set, comparison or sizing laws; physical
    # q semantics are unchanged too. Fills stay distinguishable by posterior
    # identity and the input_hwm_witness.
    from src.contracts.global_auction_receipt import CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
    from src.data.replacement_forecast_cycle_policy import CURRENT_EVIDENCE_SEMANTICS_REVISION
    assert CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION == "global_single_order_terminal_gain_settlement_hold_maker_menu_v7"
    assert CURRENT_EVIDENCE_SEMANTICS_REVISION == "ensemble_center_scenarios_v6"


@pytest.mark.parametrize("which",["ensemble","current_value","anchor"])
def test_input_continuity_unknown_successor_does_not_revoke_proven_consumed_inputs(monkeypatch,which):
    conn,context=_component(monkeypatch)
    def unavailable(*a,**k):raise sqlite3.OperationalError("interrupted")
    target,name={"ensemble":(H,"_latest_eligible_ensemble_input_mark"),
                 "current_value":(C,"read_current_instrument_values"),
                 "anchor":(H,"latest_raw_artifact_input_cycle")}[which]
    monkeypatch.setattr(target,name,unavailable)
    try:
        out={}
        assert H.replacement_live_input_lag_reason(conn,**context,input_witness_out=out) is None
        assert any("successor_" in r and "unavailable" in r for r in out["refresh_reasons"])
        assert H.replacement_input_refresh_reason(conn,**context)
    finally:conn.close()

def test_input_continuity_unknown_consumed_proof_always_fails_closed(monkeypatch):
    conn,context=_component(monkeypatch)
    def unavailable(*a,**k):raise sqlite3.OperationalError("interrupted")
    monkeypatch.setattr(C,"read_consumed_instrument_values",unavailable)
    try:
        assert "consumed_physical_proof_read_unavailable" in H.replacement_live_input_lag_reason(conn,**context)
    finally:conn.close()
