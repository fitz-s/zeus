# Created: 2026-10-04
# Last reused/audited: 2026-10-04
# Authority basis: lease-v1 round-6 consult; copied from
#   artifacts/merge_safety_lease_v1_round6_review/test_round6_review.py. Run from the checkout root.
#   One substitution: the colliding request is named by _LEGACY_CLAIM_METADATA_NAME
#   ("_claim.json", the name the review reproduced). _CLAIM_METADATA_NAME is now the
#   non-.json "claim.control", which no request can take.
"""Independent round-6 boundary checks. Temporary queues and owned children only.
No product edits, live DB writes, or venue operations.
"""
from __future__ import annotations
import errno
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import src.data.materialization_execution_lease as lease
import src.data.replacement_forecast_live_materialization_queue as q
from scripts import materialize_replacement_forecast_live as worker
from tests.adversarial.test_execution_lease_adversaries import _request, three_slots

ROOT = Path.cwd().resolve()

@pytest.fixture(autouse=True)
def release_claims():
    before = set(q._HELD_CLAIM_LEASES)
    yield
    for batch in set(q._HELD_CLAIM_LEASES) - before:
        q._release_claim_batch(Path(batch))


def queued(tmp_path, name="London.json"):
    requests=tmp_path/'requests'; requests.mkdir(exist_ok=True)
    path=requests/name; q._write_request(path,_request())
    return requests,tmp_path/'inflight',path


@pytest.mark.parametrize('order',list(itertools.permutations(range(3))))
def test_ownership_union_survives_every_drop_order(tmp_path,order):
    paths=[tmp_path/'leases'/f'{letter}.lease' for letter in 'abcd']
    required={0:{paths[0],paths[1]},1:{paths[1],paths[2]},2:{paths[1],paths[3]}}
    with q.ClaimOwnership() as own:
        for index, keys in required.items():
            assert own.require(index,keys)
        assert len(own.leases)==4
        for index in order:
            own.drop(index); required.pop(index)
            want=set().union(*required.values()) if required else set()
            assert set(own.leases)==want
            assert {h.path for h in own.survivors()}==want
            for path in want:
                assert lease.acquire_all([path]) is None
    assert not list((tmp_path/'leases').glob('*.lease'))


def test_require_conflict_preserves_incumbent_union(tmp_path):
    a,b,c=[tmp_path/'leases'/f'{x}.lease' for x in 'abc']
    foreign=lease.acquire_all([c])
    try:
        with q.ClaimOwnership() as own:
            assert own.require(0,[a])
            assert not own.require(1,[a,b,c])
            assert own.required=={0:frozenset([a])}
            assert set(own.leases)=={a}
            free=lease.acquire_all([b]); assert free is not None; lease.release(free)
    finally: lease.release(foreign)


def test_handoff_transfers_unique_survivor_union(tmp_path):
    a,b,c=[tmp_path/'leases'/f'{x}.lease' for x in 'abc']
    batch=tmp_path/'batch'
    with q.ClaimOwnership() as own:
        assert own.require(0,[a,b]) and own.require(1,[b,c])
        own.drop(0)
        kept=own.handoff(batch)
        assert {v.path for v in kept}=={b,c}
    got=q._claim_lease_fds([batch/'request.json'])
    assert len(got)==len(set(got))==2
    assert all(lease.observe([p])[0] is lease.LeaseState.HELD for p in [b,c])
    q._release_claim_batch(batch)
    assert not list((tmp_path/'leases').glob('*.lease'))


@pytest.mark.parametrize('change',['intact','missing_fd','unrelated_fd','unlink','recreate','rename'])
def test_worker_coverage_uses_current_lease_inode(tmp_path,monkeypatch,change):
    requests,inflight,source=queued(tmp_path)
    batch=q._new_claim_batch(inflight,[source]); claimed=batch/source.name
    fds=q._claim_lease_fds([claimed]); required=q.claim_required_lease_paths(claimed)
    assert len(required)==2
    extra=[]
    try:
        if change=='missing_fd':fds=fds[:1]
        elif change=='unrelated_fd':
            p=tmp_path/'unrelated';p.write_bytes(b'');fd=os.open(p,os.O_RDWR);extra.append(fd);fds=(fd,)
        elif change=='unlink':required[0].unlink()
        elif change=='recreate':required[0].unlink();required[0].touch()
        elif change=='rename':required[0].rename(required[0].with_suffix('.parked'))
        monkeypatch.setattr(worker,'_INVOCATION_LEASE_FDS',fds)
        worker._require_claimed_bytes(claimed,claimed.read_bytes())
        if change=='intact':worker._require_lease_coverage(claimed)
        else:
            with pytest.raises(worker.ClaimLeaseNotCovered):worker._require_lease_coverage(claimed)
    finally:
        for fd in extra:os.close(fd)


@pytest.mark.parametrize('outcome',['claim','empty','changed_tail'])
def test_producer_phase_preserves_claim_tuple_and_trace(three_slots,monkeypatch,outcome):
    requests,files,revision,build=three_slots
    plan=build()
    if outcome=='empty':
        from dataclasses import replace
        plan=replace(plan,claim=replace(plan.claim,selected_files=()))
    elif outcome=='changed_tail':
        p=files['Paris'];body=json.loads(p.read_text());body['computed_at']='2026-08-24T09:00:00+00:00';q._write_request(p,body)
    trace={'phases':[],'seed_window':[]}
    monkeypatch.setattr(q._claim_read_local,'producer_trace',trace,raising=False)
    result=q._try_claim_priority_request(plan)
    assert isinstance(result,tuple) and len(result)==2 and isinstance(result[1],tuple)
    assert [x['phase'] for x in trace['phases']].count('priority_claim_apply')==1
    if outcome=='empty':assert result==(None,())
    else:
        assert result[0] is not None
        assert result[0].claimed_count==(1 if outcome=='changed_tail' else 3)
        assert bool(result[1])==(outcome=='changed_tail')


def test_public_queue_retains_producer_trace(three_slots,tmp_path):
    requests,files,revision,build=three_slots
    result=q.process_replacement_forecast_live_materialization_queue(
        request_dir=requests,processed_dir=tmp_path/'processed',failed_dir=tmp_path/'failed',
        forecast_db=None,seed_limit=0,limit=3,lane=q.MATERIALIZATION_LANE_PRIORITY,
        runner=lambda argv:subprocess.CompletedProcess(argv,0,'',''))
    assert result.processed_count==3
    assert result.producer_trace and result.producer_trace['completed_at']
    assert 'priority_claim_apply' in {x['phase'] for x in result.producer_trace['phases']}
    assert q._producer_trace() is None


@pytest.mark.parametrize('cut',['empty','partial'])
def test_crash_during_receipt_write_does_not_poison_queue(tmp_path,cut):
    requests=tmp_path/'requests';requests.mkdir()
    target=tmp_path/'target';target.write_bytes(b'untouched')
    alias=requests/'bad.json';alias.symlink_to(target)
    code=f'''import os,sys,json
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
import src.data.replacement_forecast_live_materialization_queue as q
original=json.dump

def crash(body,handle,*a,**kw):
    if Path(handle.name).name==q._ALIAS_RECEIPT_NAME:
        if {cut!r}=='partial':handle.write('{{"status":')
        handle.flush();os.fsync(handle.fileno());os._exit(73)
    return original(body,handle,*a,**kw)
json.dump=crash
q._quarantine_request_alias(Path({str(alias)!r}))
'''
    child=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=20)
    assert child.returncode==73,child.stderr
    captures=q._capture_dirs(requests);assert len(captures)==1
    assert len(q._capture_entries(captures[0]))==1
    assert not q._capture_settled(captures[0])
    healthy=requests/'London.json';q._write_request(healthy,_request())
    seeds=tmp_path/'seeds';seeds.mkdir()
    errors=[]
    for _ in range(2):
        try:
            q.process_replacement_forecast_live_materialization_queue(
                request_dir=requests,processed_dir=tmp_path/'processed',failed_dir=tmp_path/'failed',
                seed_dir=seeds,seed_processed_dir=tmp_path/'seed_processed',seed_failed_dir=tmp_path/'seed_failed',
                forecast_db=None,seed_limit=1,limit=1,discover=False,lane=q.MATERIALIZATION_LANE_BACKGROUND,
                runner=lambda argv:subprocess.CompletedProcess(argv,0,'',''))
        except OSError as exc:errors.append(type(exc).__name__)
    reconcile_error=None
    try:report=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
    except OSError as exc:reconcile_error=type(exc).__name__
    print('PROBE',json.dumps(dict(probe='receipt_write_crash',cut=cut,retry_errors=errors,
        reconcile_error=reconcile_error,healthy_pending=healthy.exists(),
        target_unchanged=target.read_bytes()==b'untouched',settled=q._capture_settled(captures[0]))))
    assert not errors and reconcile_error is None,'partial exclusive receipt permanently prevents automatic settlement'
    assert q._capture_settled(captures[0])


def test_request_control_name_cannot_overwrite_claim_metadata(tmp_path,monkeypatch):
    requests,inflight,source=queued(tmp_path,name=q._LEGACY_CLAIM_METADATA_NAME)
    monkeypatch.setattr(q,'_claim_db_fingerprint',lambda db:1)
    monkeypatch.setattr(q,'_current_money_risk_families',lambda:frozenset())
    monkeypatch.setattr(q,'_current_global_auction_scope_families',lambda *a,**k:frozenset())
    monkeypatch.setattr(q,'_priority_map_with_names',lambda db,paths,payloads,**kw:({p.name:(0,p.name) for p in paths},{p.name for p in paths}))
    seen=[]
    def runner(argv):
        seen.append(argv);return subprocess.CompletedProcess(argv,0,'','')
    report=q.process_replacement_forecast_live_materialization_queue(
        request_dir=requests,processed_dir=tmp_path/'processed',failed_dir=tmp_path/'failed',
        forecast_db=None,seed_limit=0,limit=1,lane=q.MATERIALIZATION_LANE_PRIORITY,runner=runner)
    retained=[str(p) for p in tmp_path.rglob('*.json') if q._load_request_payload_for_coalescing(p)==_request()]
    print('PROBE',json.dumps(dict(probe='claim_control_name',name=source.name,
        report=report.as_dict(),executions=len(seen),retained_request_paths=retained)))
    assert seen or retained,'accepted regular request was deleted as batch metadata before any execution'


@pytest.mark.parametrize('layout',['legacy','new'])
def test_recover_regular_receipt_json_with_control_separation(tmp_path,layout):
    requests=tmp_path/'requests';requests.mkdir()
    capture=tmp_path/q._REQUEST_ALIAS_DIR/'.capture.old';capture.mkdir(parents=True)
    parent=capture
    if layout=='new':parent=capture/q._CAPTURE_PAYLOAD_DIR;parent.mkdir()
    q._write_request(parent/'receipt.json',_request())
    report=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
    assert report.quiescent
    assert q._load_request_payload_for_coalescing(requests/'receipt.json')==_request()


def test_held_capture_refused_then_settled(tmp_path):
    requests=tmp_path/'requests';requests.mkdir()
    capture=tmp_path/q._REQUEST_ALIAS_DIR/'.capture.held';capture.mkdir(parents=True)
    (capture/q._CAPTURE_PAYLOAD_DIR).mkdir()
    q._write_request(capture/q._CAPTURE_PAYLOAD_DIR/'London.json',_request())
    fd=os.open(capture,os.O_RDONLY)
    import fcntl
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
        report=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
        assert not report.quiescent and report.unsettled_captures==((capture.name,'held'),)
    finally:os.close(fd)
    report=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
    assert report.quiescent and (requests/'London.json').exists()
