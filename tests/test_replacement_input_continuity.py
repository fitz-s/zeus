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
        assert H.replacement_live_input_lag_reason(conn,**context,input_witness_out=out,
            successor_census=True) is None
        assert out["consumed_source_cycle_time"]==CYCLE.isoformat()
        assert out["posterior_computed_at"]==COMPUTED.isoformat()
        assert out["consumed_model_cycles"]=={"icon_global":CYCLE.isoformat()}
        assert out["ensemble_cycle_lag_hours"]==6
        assert out["source_cycle_age_hours"]==9
        assert out["blocking_reason"] is None and out["refresh_reasons"]
        # Serving alone records the consumed clocks, never the successor census.
        served={}
        assert H.replacement_live_input_lag_reason(conn,**context,input_witness_out=served) is None
        assert served["posterior_age_hours"]==1 and "refresh_reasons" not in served
        assert "ensemble_cycle_lag_hours" not in served
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
        assert out.bundle.input_hwm_witness["posterior_computed_at"]==before.bundle.input_hwm_witness["posterior_computed_at"]
        # The successor stays owed to the builders.
        assert "current_ensemble_snapshot_superseded" in H.replacement_input_refresh_reason(normal.conn,
            city=normal.row["city"],target_date=normal.row["target_date"],metric=normal.row["temperature_metric"],
            decision_time=newer.cut,posterior_source_cycle_time=normal.row["source_cycle_time"],
            posterior_computed_at=normal.row["computed_at"],posterior_provenance=json.loads(normal.row["provenance_json"]))
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
        # A faked reader is not a changed read: start from a cold memo.
        H.clear_consumed_proof_memo()
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
        assert belief.input_hwm_witness["blocking_reason"] is None
        assert belief.input_hwm_witness["posterior_computed_at"]==datetime.fromisoformat(normal.row["computed_at"]).isoformat()
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
    H.clear_consumed_proof_memo()
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
        assert H.replacement_live_input_lag_reason(conn,**context,input_witness_out=out,
            successor_census=True) is None
        assert any("successor_" in r and "unavailable" in r for r in out["refresh_reasons"])
        assert H.replacement_input_refresh_reason(conn,**context)
    finally:conn.close()

TAU="2026-10-01T05:05:56+00:00"  # in Paris's 2026-10-01, before the cut COMPUTED

def _with_frozen_tau(context,tau):
    """The family's Day0 window as the posterior recorded it (None: no window)."""
    provenance=json.loads(json.dumps(context["posterior_provenance"]))
    if tau is not None:
        provenance["day0_conditioning"]={"active":True,"metric":"high","observation_time":tau}
    return {**context,"city":"Paris","target_date":"2026-10-01","posterior_provenance":provenance}

def _post_day_reader(monkeypatch):
    """The consumed reader as the producer proves it: a post-day remaining-window
    row covers its suffix only at an in-day tau, and is rejected at the cut."""
    asked=[]
    conn,context=_component(monkeypatch,scenario="none")
    proven=C.read_consumed_instrument_values()  # the component's verified consumed row
    def consumed(*a,day0_remaining_from_iso=None,**k):
        asked.append(day0_remaining_from_iso)
        return proven if day0_remaining_from_iso==TAU else {}
    monkeypatch.setattr(C,"read_consumed_instrument_values",consumed,raising=False)
    return conn,context,asked

def test_post_day_consumed_rows_reverify_at_their_own_frozen_tau(monkeypatch):
    conn,context,asked=_post_day_reader(monkeypatch)
    try:
        with_tau=_with_frozen_tau(context,TAU)
        assert H.replacement_live_input_lag_reason(conn,**with_tau,use_memo=False) is None
        assert asked==[TAU]
        # The same rows judged without their tau take the whole-day law and reject.
        assert "consumed_proof_unverifiable" in H.replacement_live_input_lag_reason(
            conn,**_with_frozen_tau(context,None),use_memo=False)
        assert asked[-1] is None
        # A later decision (and any later observation) never moves the tau the
        # consumed rows are judged at: it is read from the row, not the clock.
        later={**with_tau,"decision_time":NOW+timedelta(hours=5)}
        assert H.replacement_live_input_lag_reason(conn,**later,use_memo=False) is None
        assert asked[-1]==TAU
    finally:conn.close()

def test_post_day_successor_census_reads_the_rows_frozen_tau(monkeypatch):
    conn,context,_asked=_post_day_reader(monkeypatch)
    marks=[];currents=[]
    monkeypatch.setattr(H,"_latest_eligible_ensemble_input_mark",
        lambda *a,day0_remaining_from_iso=None,**k:marks.append(day0_remaining_from_iso) or (1,CYCLE))
    monkeypatch.setattr(C,"read_current_instrument_values",
        lambda *a,day0_remaining_from_iso=None,**k:currents.append(day0_remaining_from_iso) or {})
    try:
        out={}
        assert H.replacement_live_input_lag_reason(conn,**_with_frozen_tau(context,TAU),input_witness_out=out,
            successor_census=True,use_memo=False) is None
        assert marks==[TAU] and currents==[TAU]
    finally:conn.close()

def test_pinned_held_continuity_keeps_a_carrier_whose_consumed_proof_verifies(_shanghai_reader_current_certificate,monkeypatch):
    # Live 10-02: REPLACEMENT_PINNED_RAW_INPUT_HWM on 8 held positions. The
    # pinned reader's only HWM site is _latest_complete_held_continuity; a newer
    # icon_global proof is refresh debt there, never a block.
    normal=_shanghai_reader_current_certificate
    provenance=json.loads(normal.row["provenance_json"])
    F._reader_new_icon_cycle(normal);normal.conn.commit()
    context=dict(row=normal.row,provenance=provenance,city=normal.row["city"],
        target_date=normal.row["target_date"],metric=normal.row["temperature_metric"],
        decision_time=normal.request.computed_at)
    later=normal.request.source_cycle_time+timedelta(hours=6)
    monkeypatch.setattr(B,"latest_live_input_cycle",lambda *a,**k:(later,"newer-icon"))
    assert "current_value_serving_physical_proof_dependency_changed" in H.replacement_input_refresh_reason(
        normal.conn,city=normal.row["city"],target_date=normal.row["target_date"],metric="high",
        decision_time=normal.request.computed_at,posterior_source_cycle_time=normal.row["source_cycle_time"],
        posterior_computed_at=normal.row["computed_at"],posterior_provenance=provenance)
    status,reason=B._latest_complete_held_continuity(normal.conn,**context)
    assert (status,reason)==(B._HeldContinuityStatus.READY,None)
    # Only consumed-proof invalidity blocks the pinned carrier.
    H.clear_consumed_proof_memo()
    monkeypatch.setattr(C,"read_consumed_instrument_values",lambda *a,**k:{},raising=False)
    status,reason=B._latest_complete_held_continuity(normal.conn,**context)
    assert status is B._HeldContinuityStatus.BLOCKED
    assert reason.startswith("REPLACEMENT_PINNED_RAW_INPUT_HWM:basis=current_value_serving_consumed_proof_unverifiable")

def _locked(needle):
    def raise_locked(*a,**k):
        raise H.ReplacementInputHwmReadUnavailable("database is locked",basis=needle)
    return raise_locked

@pytest.mark.parametrize("frontier",["absent","raw_read_locked","ens_newer"])
def test_pinned_held_continuity_unknown_successor_frontier_keeps_the_carrier(
        _shanghai_reader_current_certificate,monkeypatch,frontier):
    """Round-2 blocker 2: the successor frontier is witness, never exclusion.

    An absent or unreadable raw frontier is unknown successor state and a newer
    ENS cycle is refresh debt; with the consumed proof valid the carrier stays
    READY. Each case was BLOCKED before (RAW_FRONTIER_UNAVAILABLE,
    raw_model_input_hwm_read_unavailable, ELIGIBLE_ENS_HWM_UNAVAILABLE).
    """
    normal=_shanghai_reader_current_certificate
    provenance=json.loads(normal.row["provenance_json"])
    context=dict(row=normal.row,provenance=provenance,city=normal.row["city"],
        target_date=normal.row["target_date"],metric=normal.row["temperature_metric"],
        decision_time=normal.request.computed_at)
    later=normal.request.source_cycle_time+timedelta(hours=6)
    if frontier=="absent":
        monkeypatch.setattr(B,"latest_live_input_cycle",lambda *a,**k:(None,None))
    elif frontier=="raw_read_locked":
        monkeypatch.setattr(B,"latest_live_input_cycle",_locked("raw_model_input_hwm_read_unavailable"))
    else:
        monkeypatch.setattr(B,"latest_live_input_cycle",lambda *a,**k:(later,"newer-raw"))
        monkeypatch.setattr(H,"latest_eligible_ensemble_input_cycle",lambda *a,**k:later)
        monkeypatch.setattr(H,"_latest_eligible_ensemble_input_mark",lambda *a,**k:(999,later))
    H.clear_consumed_proof_memo()
    assert B._latest_complete_held_continuity(normal.conn,**context)==(B._HeldContinuityStatus.READY,None)
    # The serving projection carries no successor reason for a held carrier.
    assert H.replacement_live_input_lag_reason(normal.conn,city=normal.row["city"],
        target_date=normal.row["target_date"],metric="high",decision_time=normal.request.computed_at,
        posterior_source_cycle_time=normal.row["source_cycle_time"],posterior_computed_at=normal.row["computed_at"],
        posterior_provenance=provenance,held_redecision=True,use_memo=False) is None
    # A known frontier at the posterior's own cycle resets to the ordinary path.
    monkeypatch.setattr(B,"latest_live_input_cycle",
        lambda *a,**k:(normal.request.source_cycle_time,"same-cycle"))
    assert B._latest_complete_held_continuity(normal.conn,**context)==(
        B._HeldContinuityStatus.RESET,"REPLACEMENT_PINNED_COMPLETE_CYCLE_RESET")
    # Unknown consumed authority still fails closed, before any frontier read.
    monkeypatch.setattr(B,"latest_live_input_cycle",lambda *a,**k:pytest.fail("frontier read before consumed proof"))
    H.clear_consumed_proof_memo()
    monkeypatch.setattr(C,"read_consumed_instrument_values",
        lambda *a,**k:(_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),raising=False)
    status,reason=B._latest_complete_held_continuity(normal.conn,**context)
    assert status is B._HeldContinuityStatus.BLOCKED
    assert "consumed_physical_proof_read_unavailable" in reason

def test_input_continuity_unknown_consumed_proof_always_fails_closed(monkeypatch):
    conn,context=_component(monkeypatch)
    def unavailable(*a,**k):raise sqlite3.OperationalError("interrupted")
    monkeypatch.setattr(C,"read_consumed_instrument_values",unavailable)
    try:
        assert "consumed_physical_proof_read_unavailable" in H.replacement_live_input_lag_reason(conn,**context)
    finally:conn.close()


# ---- consumed-proof verdict memo: keyed on every non-clock input it reads ----

def _memo_context(normal, **overrides):
    return dict(city=normal.row["city"],target_date=normal.row["target_date"],
        metric=normal.row["temperature_metric"],decision_time=normal.request.computed_at,
        posterior_source_cycle_time=normal.row["source_cycle_time"],
        posterior_computed_at=normal.row["computed_at"],
        posterior_provenance=json.loads(normal.row["provenance_json"]),**overrides)

def _count_consumed_reads(monkeypatch):
    calls=[]
    real=C.read_consumed_instrument_values
    def counted(*a,**k):
        calls.append(1)
        return real(*a,**k)
    monkeypatch.setattr(C,"read_consumed_instrument_values",counted)
    return calls

def test_verdict_memo_serves_the_same_posterior_without_rereading(_shanghai_reader_current_certificate,monkeypatch):
    normal=_shanghai_reader_current_certificate
    H.clear_consumed_proof_memo()
    calls=_count_consumed_reads(monkeypatch)
    context=_memo_context(normal)
    assert H.replacement_live_input_lag_reason(normal.conn,**context) is None
    assert H.replacement_live_input_lag_reason(normal.conn,**context) is None
    assert len(calls)==1
    # A different posterior identity never reuses the verdict: its own
    # provenance is proved from source (here it fails closed on its own).
    other=json.loads(normal.row["provenance_json"]);other["memo_probe"]=1
    proved=[]
    real=H._exact_current_value_serving_lag
    monkeypatch.setattr(H,"_exact_current_value_serving_lag",lambda *a,**k:proved.append(k["provenance"]) or real(*a,**k))
    assert H.replacement_live_input_lag_reason(normal.conn,**{**context,"posterior_provenance":other}) is not None
    assert proved==[other]
    # Another family or metric for the same posterior text is its own key too.
    assert H.replacement_live_input_lag_reason(normal.conn,**{**context,"metric":"low"}) is not None
    assert len(proved)==2
    assert H.replacement_live_input_lag_reason(normal.conn,**context) is None
    assert len(proved)==2 and len(H._VERDICT_MEMO)==1

def test_verdict_memo_misses_when_station_ground_facts_change(_shanghai_reader_current_certificate,monkeypatch):
    normal=_shanghai_reader_current_certificate
    H.clear_consumed_proof_memo()
    calls=_count_consumed_reads(monkeypatch)
    context=_memo_context(normal)
    assert H.replacement_live_input_lag_reason(normal.conn,**context) is None
    from src.data import station_ground_evidence as G
    real=G.read_current_station_ground_evidence
    def changed(*a,**k):
        current=real(*a,**k)
        return None if current is None else {**current,"facts_identity":"changed-ground"}
    monkeypatch.setattr(G,"read_current_station_ground_evidence",changed)
    reason=H.replacement_live_input_lag_reason(normal.conn,**context)
    assert reason=="basis=station_ground_current_facts_changed"
    monkeypatch.setattr(G,"read_current_station_ground_evidence",lambda *a,**k:None)
    reason=H.replacement_live_input_lag_reason(normal.conn,**context)
    assert reason=="basis=station_ground_canonical_evidence_unavailable"

def test_verdict_memo_misses_when_a_read_file_changes(_shanghai_reader_current_certificate,monkeypatch):
    normal=_shanghai_reader_current_certificate
    H.clear_consumed_proof_memo()
    context=_memo_context(normal)
    assert H.replacement_live_input_lag_reason(normal.conn,**context) is None
    calls=_count_consumed_reads(monkeypatch)
    assert H.replacement_live_input_lag_reason(normal.conn,**context) is None and not calls
    (_cycle,(_reads,files)),=H._VERDICT_MEMO.values()
    body=next(Path(p) for p,_ in files if Path(p).is_file() and "raw_manifests" in p)
    original=body.read_bytes()
    try:
        body.write_bytes(original+b" ")
        H.replacement_live_input_lag_reason(normal.conn,**context)
        assert len(calls)==1
    finally:
        body.write_bytes(original)

def _consumed_raw_body(normal):
    raw_id=_consumed_raw_ids(normal)[0]
    artifact=normal.conn.execute("SELECT artifact_id FROM raw_model_forecasts WHERE raw_model_forecast_id=?",(raw_id,)).fetchone()[0]
    return Path(normal.conn.execute("SELECT artifact_path FROM raw_forecast_artifacts WHERE artifact_id=?",(artifact,)).fetchone()[0])

def test_memo_misses_a_same_length_body_edit_with_its_mtime_restored(_shanghai_reader_current_certificate):
    # Reviewer F-file: inode, size and mtime unchanged, only ctime moves. A warm
    # hit must not keep the bundle READY on bytes the hash validator rejects.
    import os
    normal=_shanghai_reader_current_certificate
    context=_memo_context(normal)
    H.clear_consumed_proof_memo();B._LIVE_GRADE_MEMO.clear()
    assert B.read_replacement_forecast_bundle(normal.conn,**normal.kwargs).ok
    assert H.replacement_live_input_lag_reason(normal.conn,**context) is None
    body=_consumed_raw_body(normal)
    original=body.read_bytes();before=body.stat()
    try:
        body.write_bytes(bytes([original[0]^1])+original[1:])
        os.utime(body,ns=(before.st_atime_ns,before.st_mtime_ns))
        after=body.stat()
        assert (after.st_ino,after.st_size,after.st_mtime_ns)==(before.st_ino,before.st_size,before.st_mtime_ns)
        assert H.replacement_live_input_lag_reason(normal.conn,**context) is not None
        assert not B.read_replacement_forecast_bundle(normal.conn,**normal.kwargs).ok
    finally:
        body.write_bytes(original);os.utime(body,ns=(before.st_atime_ns,before.st_mtime_ns))
    # The untouched file hits again.
    assert H.replacement_live_input_lag_reason(normal.conn,**context) is None

@pytest.mark.parametrize("table","raw_model_forecasts deterministic_forecast_anchors".split())
def test_memo_misses_a_deleted_consumed_row_and_hits_after_an_unrelated_commit(_shanghai_reader_current_certificate,monkeypatch,table):
    # Reviewer F1 (hwm_memo_repro): a warm READY bundle must not survive the
    # deletion of its exact consumed raw row or anchor relation (production:
    # the 180-day _prune_old DELETE); an unrelated commit still hits.
    normal=_shanghai_reader_current_certificate
    H.clear_consumed_proof_memo();B._LIVE_GRADE_MEMO.clear()
    assert B.read_replacement_forecast_bundle(normal.conn,**normal.kwargs).ok
    proved=[]
    real=H._exact_current_value_serving_lag
    monkeypatch.setattr(H,"_exact_current_value_serving_lag",lambda *a,**k:proved.append(1) or real(*a,**k))
    normal.conn.execute("CREATE TABLE unrelated_scratch(x)");normal.conn.execute("INSERT INTO unrelated_scratch VALUES (1)")
    normal.conn.commit()
    assert B.read_replacement_forecast_bundle(normal.conn,**normal.kwargs).ok and not proved
    key=("raw_model_forecast_id",_consumed_raw_ids(normal)[0]) if table=="raw_model_forecasts" else (
        "anchor_id",normal.row["openmeteo_anchor_id"])
    normal.conn.execute(f"DELETE FROM {table} WHERE {key[0]}=?",(key[1],));normal.conn.commit()
    assert not B.read_replacement_forecast_bundle(normal.conn,**normal.kwargs).ok

def test_c3_cancels_a_rest_whose_consumed_row_is_deleted_after_a_warm_read(_shanghai_reader_current_certificate):
    # Reviewer hwm_c3_memo_repro on live C3: a warm memo must not keep the
    # standing rest's q-version once its consumed evidence no longer exists.
    normal=_shanghai_reader_current_certificate
    H.clear_consumed_proof_memo();B._LIVE_GRADE_MEMO.clear()
    assert _c3_q(normal,normal.request.computed_at)==normal.row["posterior_identity_hash"]
    normal.conn.execute("DELETE FROM raw_model_forecasts WHERE raw_model_forecast_id=?",(_consumed_raw_ids(normal)[0],))
    normal.conn.commit()
    q=_c3_q(normal,normal.request.computed_at)
    assert q.startswith(f"__Q_AUTHORITY_BLOCKED__:{normal.row['posterior_identity_hash']}:")
    assert "consumed_proof_unverifiable" in q

def _consumed_raw_ids(normal):
    serving=json.loads(normal.row["provenance_json"])["bayes_precision_fusion"]["current_value_serving"]
    return sorted(item["raw_model_forecast_id"] for item in serving.values())

@pytest.mark.parametrize("table,column,where",[
    ("raw_forecast_artifacts","sha256","artifact_id=(SELECT MIN(artifact_id) FROM raw_forecast_artifacts)"),
    ("raw_model_forecasts","forecast_value_c","raw_model_forecast_id=?"),
    ("source_run","manifest_hash","1"),
    ("deterministic_forecast_anchors","artifact_id","1"),
])
def test_verdict_memo_misses_when_a_read_row_changes_in_place(_shanghai_reader_current_certificate,monkeypatch,table,column,where):
    # No table is assumed append-only: an in-place UPDATE of any row the
    # verdict read is seen on the next hit, never served from the memo.
    normal=_shanghai_reader_current_certificate
    H.clear_consumed_proof_memo()
    context=_memo_context(normal)
    assert H.replacement_live_input_lag_reason(normal.conn,**context) is None
    proved=[]
    real=H._exact_current_value_serving_lag
    monkeypatch.setattr(H,"_exact_current_value_serving_lag",lambda *a,**k:proved.append(1) or real(*a,**k))
    assert H.replacement_live_input_lag_reason(normal.conn,**context) is None and not proved
    params=(_consumed_raw_ids(normal)[0],) if "?" in where else ()
    # Committed, as a writer's would be: the verdict also reads on its own
    # read-only connections, which see only committed rows.
    assert normal.conn.execute(f"UPDATE {table} SET {column}=CASE WHEN typeof({column})='text' "
        f"THEN {column}||'x' ELSE {column}+1 END WHERE {where}",params).rowcount
    normal.conn.commit()
    H.replacement_live_input_lag_reason(normal.conn,**context)
    assert proved==[1]

def _recorded(tmp_path,body):
    path=tmp_path/f"reads{len(list(tmp_path.iterdir()))}.db"
    conn=sqlite3.connect(path);conn.row_factory=sqlite3.Row
    conn.execute("CREATE TABLE t(k INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t VALUES (?,?)",[(1,"a"),(2,"b"),(3,"c")]);conn.commit()
    record=H.ReadRecord()
    with H.recorded_reads(record,conn) as seen:
        body(seen,path)
    return conn,record

def test_recorded_read_sees_an_appended_row_only_where_it_read_to_the_end(tmp_path):
    def body(seen,path):
        assert [r["v"] for r in seen.execute("SELECT v FROM t WHERE k>=? ORDER BY k",(2,))]==["b","c"]
        assert seen.execute("SELECT v FROM t ORDER BY k").fetchone()["v"]=="a"
    conn,record=_recorded(tmp_path,body)
    frozen=record.frozen()
    assert record.replayable and H.reads_hold(frozen,conn)
    conn.execute("INSERT INTO t VALUES (0,'z')");conn.commit()
    assert not H.reads_hold(frozen,conn)  # the first-row read now answers 'z'
    conn.execute("DELETE FROM t WHERE k=0");conn.execute("INSERT INTO t VALUES (4,'d')");conn.commit()
    assert not H.reads_hold(frozen,conn)  # the exhausted range read gained a row

def test_recorded_read_shares_answers_only_within_one_visible_snapshot(tmp_path):
    # A cut's verdicts replay the same group reads on one read-only
    # connection; a commit elsewhere is a new snapshot and is read again.
    writer,record=_recorded(tmp_path,lambda seen,path:seen.execute("SELECT v FROM t WHERE k=?",(1,)).fetchall())
    path=next(r[2] for r in writer.execute("PRAGMA database_list") if r[1]=="main")
    reader=sqlite3.connect(f"file:{path}?mode=ro",uri=True)
    frozen=record.frozen()
    assert H.reads_hold(frozen,reader) and H.reads_hold(frozen,reader)
    writer.execute("UPDATE t SET v='changed' WHERE k=1");writer.commit()
    assert not H.reads_hold(frozen,reader)

def test_recorded_file_is_the_version_that_was_read_not_the_one_at_freeze(tmp_path):
    # A writer landing between the verdict's read and the memo store must not
    # be remembered as the version the verdict read.
    body=tmp_path/"body.json";body.write_text("{}")
    record=H.ReadRecord()
    with H.recorded_reads(record):
        body.read_text()
    body.write_text('{"changed": 1}')
    frozen=record.frozen()
    assert not H.reads_hold(frozen)

def test_recorded_read_disqualifies_a_worker_thread_but_not_the_deadline_opener(tmp_path):
    import threading
    from src.state.db import _connect_read_only
    path=tmp_path/"reads.db";sqlite3.connect(path).execute("CREATE TABLE t(a)").connection.commit()
    record=H.ReadRecord()
    with H.recorded_reads(record):
        conn=_connect_read_only(path,deadline_monotonic=__import__("time").monotonic()+5)
        conn.execute("SELECT COUNT(*) FROM t").fetchone();conn.close()
    assert record.replayable and record.reads
    record=H.ReadRecord()
    with H.recorded_reads(record):
        worker=threading.Thread(target=lambda:None);worker.start();worker.join()
    assert not record.replayable

def test_recorded_read_refuses_a_statement_or_connection_it_cannot_replay(tmp_path):
    _conn,record=_recorded(tmp_path,lambda seen,path:seen.execute("CREATE TEMP TABLE scratch(x)"))
    assert not record.replayable
    _conn,record=_recorded(tmp_path,lambda seen,path:sqlite3.connect(path).close())
    assert not record.replayable
    from src.state.db import _connect_read_only
    def own(seen,path):
        ro=_connect_read_only(path)
        try:assert ro.execute("SELECT COUNT(*) FROM t").fetchone()[0]==3
        finally:ro.close()
    conn,record=_recorded(tmp_path,own)
    assert record.replayable and H.reads_hold(record.frozen(),conn)
    conn.execute("INSERT INTO t VALUES (9,'x')");conn.commit()
    assert not H.reads_hold(record.frozen(),conn)

def test_verdict_memo_replays_reads_on_its_own_read_only_connections(_shanghai_reader_current_certificate,monkeypatch):
    normal=_shanghai_reader_current_certificate
    H.clear_consumed_proof_memo()
    assert H.replacement_live_input_lag_reason(normal.conn,**_memo_context(normal)) is None
    (_cycle,(reads,_files)),=H._VERDICT_MEMO.values()
    channels={read[0] for read in reads}
    assert None in channels and any(isinstance(c,str) and "mode=ro" in c for c in channels)
    assert all(read[3] or read[4] for read in reads)

def test_live_grade_memo_misses_when_a_read_row_changes_in_place(_shanghai_reader_current_certificate,monkeypatch):
    # The shape authority this memo remembers joins source_run, which its
    # writer replaces in place; the replaced row is re-proved, not served.
    from src.data import replacement_forecast_cycle_policy as P
    normal=_shanghai_reader_current_certificate
    B._LIVE_GRADE_MEMO.clear()
    assert B.read_replacement_forecast_bundle(normal.conn,**normal.kwargs).ok
    calls=[]
    real=P._current_evidence_shape_has_probability_authority
    monkeypatch.setattr(P,"_current_evidence_shape_has_probability_authority",lambda *a,**k:calls.append(1) or real(*a,**k))
    assert B.read_replacement_forecast_bundle(normal.conn,**normal.kwargs).ok and not calls
    normal.conn.execute("UPDATE source_run SET manifest_hash=manifest_hash||'x'")
    normal.conn.commit()
    B.read_replacement_forecast_bundle(normal.conn,**normal.kwargs)
    assert calls

def test_verdict_memo_misses_when_authority_config_changes(_shanghai_reader_current_certificate,monkeypatch):
    normal=_shanghai_reader_current_certificate
    H.clear_consumed_proof_memo()
    context=_memo_context(normal)
    assert H.replacement_live_input_lag_reason(normal.conn,**context) is None
    calls=_count_consumed_reads(monkeypatch)
    monkeypatch.setenv("ZEUS_REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS","29")
    H.replacement_live_input_lag_reason(normal.conn,**context)
    assert len(calls)==1

def test_verdict_memo_never_caches_unknown_or_invalid(_shanghai_reader_current_certificate,monkeypatch):
    normal=_shanghai_reader_current_certificate
    H.clear_consumed_proof_memo()
    context=_memo_context(normal)
    real=C.read_consumed_instrument_values
    def unknown(*a,**k):raise sqlite3.OperationalError("interrupted")
    monkeypatch.setattr(C,"read_consumed_instrument_values",unknown)
    for _ in range(2):
        assert "consumed_physical_proof_read_unavailable" in H.replacement_live_input_lag_reason(normal.conn,**context)
    monkeypatch.setattr(C,"read_consumed_instrument_values",lambda *a,**k:{})
    assert "consumed_proof_unverifiable" in H.replacement_live_input_lag_reason(normal.conn,**context)
    assert not H._VERDICT_MEMO
    # Once the proof verifies, the next unknown read is still not served stale
    # to a caller that asks for source revalidation (the executor).
    monkeypatch.setattr(C,"read_consumed_instrument_values",real)
    assert H.replacement_live_input_lag_reason(normal.conn,**context) is None
    monkeypatch.setattr(C,"read_consumed_instrument_values",unknown)
    assert "consumed_physical_proof_read_unavailable" in H.replacement_live_input_lag_reason(
        normal.conn,**context,use_memo=False)

def test_verdict_memo_is_bounded(monkeypatch):
    monkeypatch.setattr(H,"_MEMO_LIMIT",3)
    H.clear_consumed_proof_memo()
    for i in range(10):
        H._memo_put(H._VERDICT_MEMO,(i,),(CYCLE,()))
    assert list(H._VERDICT_MEMO)==[(7,),(8,),(9,)]

@pytest.mark.parametrize("purpose",tuple(B.ReplacementForecastAuthorityPurpose))
def test_readiness_revocation_and_expiry_are_read_every_time(_shanghai_reader_current_certificate,purpose):
    normal=_shanghai_reader_current_certificate
    kwargs={**normal.kwargs,"authority_purpose":purpose}
    assert B.read_replacement_forecast_bundle(normal.conn,**kwargs).ok
    revoked=B.read_replacement_forecast_bundle(normal.conn,**{**kwargs,"readiness":replace(normal.readiness,status="BLOCKED")})
    assert revoked.reason_code=="REPLACEMENT_READINESS_NOT_READY"
    expired=B.read_replacement_forecast_bundle(normal.conn,**{**kwargs,
        "readiness":replace(normal.readiness,expires_at=normal.request.computed_at)})
    assert expired.reason_code=="REPLACEMENT_LIVE_READINESS_EXPIRED"
    assert B.read_replacement_forecast_bundle(normal.conn,**kwargs).ok

def test_live_grade_memo_is_per_purpose_and_canonical_only(_shanghai_reader_current_certificate,monkeypatch):
    normal=_shanghai_reader_current_certificate
    B._LIVE_GRADE_MEMO.clear()
    for purpose in B.ReplacementForecastAuthorityPurpose:
        assert B.read_replacement_forecast_bundle(normal.conn,**{**normal.kwargs,"authority_purpose":purpose}).ok
    assert {key[1] for key in B._LIVE_GRADE_MEMO}=={p.value for p in B.ReplacementForecastAuthorityPurpose}
    from src.data import replacement_forecast_cycle_policy as P
    calls=[]
    real=P._current_evidence_shape_has_probability_authority
    monkeypatch.setattr(P,"_current_evidence_shape_has_probability_authority",lambda *a,**k:calls.append(1) or real(*a,**k))
    assert B.read_replacement_forecast_bundle(normal.conn,**normal.kwargs).ok
    assert not calls
    # A non-canonical view (wrapper) always re-proves.
    class View:
        def __getattr__(self,name):return getattr(normal.conn,name)
        def execute(self,*a):return normal.conn.execute(*a)
    B.read_replacement_forecast_bundle(View(),**{**normal.kwargs,"raw_input_hwm_conn":normal.conn})
    assert calls
