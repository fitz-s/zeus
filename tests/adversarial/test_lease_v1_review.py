# Created: 2026-10-03
# Last reused/audited: 2026-10-03
# Authority basis: consult REQ-20261003-211813-a30820 (lease-v1 re-review);
#   copied from artifacts/merge_safety_lease_v1_review/test_review_execution_lease.py.
# Seam changes: none, except test_characterize_priority_tail_churn, whose final assertion is flipped to a progress assertion (requested).
"""Independent lease-v1 review. Temporary files/processes only; no production DBs.
Faults target actual filesystem/read/transport boundaries. No product code is edited.
"""
from __future__ import annotations
import errno
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest
import src.data.materialization_execution_lease as lease
import src.data.replacement_forecast_live_materialization_queue as q
from tests.adversarial.test_execution_lease_adversaries import _request, three_slots

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture(autouse=True)
def cleanup_claims():
    before = set(q._HELD_CLAIM_LEASES)
    yield
    for batch in set(q._HELD_CLAIM_LEASES) - before:
        q._release_claim_batch(Path(batch))


def queued(tmp_path, city='London', name='a.json'):
    requests=tmp_path/'requests';requests.mkdir(exist_ok=True)
    path=requests/name;path.write_text(json.dumps(_request(city=city)))
    return requests, tmp_path/'inflight', path


def test_claim_identity_must_come_from_the_same_bytes(tmp_path, monkeypatch):
    requests,inflight,a=queued(tmp_path)
    duplicate=requests/'duplicate.json';duplicate.write_bytes(a.read_bytes())
    other=tmp_path/'other-publication';other.write_text(json.dumps(_request(city='Paris')))
    parked=tmp_path/'original-publication'
    original_load=q._load_request_payload_for_coalescing
    swapped=False
    def read_during_aba(path):
        nonlocal swapped
        if Path(path)!=a or swapped:
            return original_load(path)
        swapped=True
        # Read body A first; the independent identity read sees publication B.
        # Both publications are immutable files: rename A -> B -> A preserves
        # A's exact bytes and mtime, which are all the later check compares.
        os.replace(a,parked);os.replace(other,a)
        try:
            result=original_load(path)
        finally:
            os.replace(a,other);os.replace(parked,a)
        return result
    monkeypatch.setattr(q,'_load_request_payload_for_coalescing',read_during_aba)
    first=q._new_claim_batch(inflight,(a,))
    second=None
    try:
        second=q._new_claim_batch(inflight,(duplicate,))
    except q._ClaimIdentityOwned:
        pass
    body_key=q._request_semantic_key(json.loads((first/a.name).read_text()))
    recorded=q._claim_records(first)[0].identity
    print('PROBE',json.dumps(dict(probe='split_identity_bytes',body_identity=body_key,
        leased_record_identity=recorded,second_same_body_owner=second is not None,
        first_held=q._claim_owner_alive(first),second_held=None if second is None else q._claim_owner_alive(second))))
    assert recorded==body_key and second is None, 'one body can execute under another identity lease'


def test_prepublish_crash_does_not_permanently_block_discovery(tmp_path):
    requests,inflight,path=queued(tmp_path)
    code=f'''import os,sys
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
import src.data.replacement_forecast_live_materialization_queue as q
write=q._write_lease_claim_metadata
def die(*a,**k):
    write(*a,**k)
    os._exit(71)
q._write_lease_claim_metadata=die
q._new_claim_batch(Path({str(inflight)!r}),(Path({str(path)!r}),))
'''
    child=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=15)
    assert child.returncode==71, child.stderr
    assert path.exists() # no body was moved before the crash
    # The surviving request can finish normally; isolate its orphan metadata.
    finished=tmp_path/'processed';finished.mkdir();path.rename(finished/path.name)
    _,recovered,_=q._recover_stale_claims(request_path=requests,inflight_path=inflight,lease_only=True)
    report=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
    from src.ingest.forecast_live_daemon import _replacement_forecast_inflight_pending
    pending=_replacement_forecast_inflight_pending({'request_dir':str(requests)})
    residue=[str(p.relative_to(inflight)) for p in inflight.rglob('*')]
    print('PROBE',json.dumps(dict(probe='prepublish_crash',recovered=recovered,
        quiescent=report.quiescent,discovery_thinks_inflight_pending=pending,residue=residue)))
    assert not pending, 'hidden staging claim metadata permanently looks like inflight work to discovery'


def test_reconcile_does_not_certify_unpublished_live_lease(tmp_path):
    requests,inflight,_=queued(tmp_path)
    key=lease.lease_name(('semantic','London'))
    held=lease.acquire_all([inflight/'leases'/key])
    try:
        result=subprocess.run([sys.executable,str(ROOT/'scripts/reconcile_materialization_inflight.py'),
            '--request-dir',str(requests),'--apply'],capture_output=True,text=True,timeout=15)
        report=json.loads(result.stdout)
        state,borrowed=lease.observe([held[0].path]);lease.release(borrowed)
        print('PROBE',json.dumps(dict(probe='unpublished_held_lease',exit_code=result.returncode,
            report=report,lease_state=state.value,remaining=held[0].path.exists())))
        assert not report['quiescent'], 'exit 0 does not prove no live acquisition or lease remains'
    finally:
        lease.release(held)


def test_unknown_protocol_does_not_enter_legacy_recovery(tmp_path,monkeypatch):
    requests,inflight,path=queued(tmp_path)
    first=q._new_claim_batch(inflight,(path,))
    future=first.with_name(first.name.replace('.lease-v1.','.lease-v2.'))
    first.rename(future)
    metadata=future/q._CLAIM_METADATA_NAME
    body=json.loads(metadata.read_text());body['protocol']='lease-v2';metadata.write_text(json.dumps(body))
    monkeypatch.setattr(q,'_claim_age_seconds',lambda p:10000000)
    state,borrowed,_dead=q._observe_claim(future,stale_after=60);lease.release(borrowed)
    _,recovered,_=q._recover_stale_claims(request_path=requests,inflight_path=inflight)
    print('PROBE',json.dumps(dict(probe='unknown_protocol',state=state.value,recovered=recovered,
        original_descriptors_still_retained=str(first) in q._HELD_CLAIM_LEASES)))
    assert state is lease.LeaseState.UNKNOWN and recovered==0


def test_started_count_is_not_pending_count(three_slots,tmp_path,monkeypatch):
    requests,files,_rev,build=three_slots
    # Actual batch adapter observes a worker exit without a single envelope.
    monkeypatch.setattr(q,'_run_command',lambda argv:subprocess.CompletedProcess(argv,2,'','worker died before ack'))
    report=q.process_replacement_forecast_live_materialization_queue(request_dir=requests,
        processed_dir=tmp_path/'processed',failed_dir=tmp_path/'failed',forecast_db=None,
        seed_limit=0,limit=3,lane=q.MATERIALIZATION_LANE_PRIORITY)
    print('PROBE',json.dumps(dict(probe='started_metrics',leased=report.leased_count,
        started=report.started_count,completed=report.completed_count,deferred=report.deferred_count,
        held_first=report.held_first,actual_children_started=0)))
    assert report.started_count==0 and report.held_first is False


def test_characterize_priority_tail_churn(three_slots):
    requests,files,_rev,build=three_slots
    results=[]
    for i in range(4):
        plan=build()
        p=files['Paris'];body=json.loads(p.read_text());body['computed_at']=f'2026-08-24T09:0{i}:00+00:00';p.write_text(json.dumps(body))
        claim,reasons=q._try_claim_priority_request(plan)
        results.append({'claimed':0 if claim is None else claim.claimed_count,'reasons':reasons})
    print('PROBE',json.dumps(dict(probe='priority_tail_churn',ticks=results,held_request_pending=files['London'].exists())))
    # Progress (flipped from the all-or-none characterization, as the consult
    # asked): churn in the lower Paris slot never blocks the stable held London
    # slot planned ahead of it; London is claimed on the first tick.
    assert results[0]['claimed']>=1 and not files['London'].exists()


def test_scm_inflight_reference_keeps_the_lock(tmp_path):
    path=tmp_path/'leases'/'test.lease'
    sender,receiver=socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM)
    code=f'''import os,socket,sys
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
from src.data import materialization_execution_lease as lease
held=lease.acquire_all([Path({str(path)!r})])
s=socket.socket(fileno=int(sys.argv[1]))
socket.send_fds(s,[b'queued'],[held[0].fd])
os._exit(0)
'''
    child=subprocess.Popen([sys.executable,'-c',code,str(sender.fileno())],pass_fds=(sender.fileno(),))
    sender.close();child.wait(timeout=15)
    try:
        assert child.returncode==0
        state,borrowed=lease.observe([path]);lease.release(borrowed)
        assert state is lease.LeaseState.HELD
        data,fds,flags,_=socket.recv_fds(receiver,32,1)
        assert data==b'queued' and len(fds)==1
        for fd in fds:os.close(fd)
        state,borrowed=lease.observe([path]);lease.release(borrowed)
        print('PROBE',json.dumps(dict(probe='queued_scm_reference',after_receipt_and_close=state.value)))
        assert state is lease.LeaseState.ACQUIRED_FOR_RECOVERY
    finally:
        receiver.close()


def _worker_wrapper(tmp_path,mode):
    path=tmp_path/(mode+'.py')
    text=f'''import importlib.util,json,os,signal,sys
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
spec=importlib.util.spec_from_file_location('review_worker',{str(ROOT/'scripts/materialize_replacement_forecast_live.py')!r})
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
import src.runtime.warm_materializer as w
real_receive=w.receive_leases
mode={mode!r}
def receive(message,write):
    if mode=='before_ack':
        def die(frame): os._exit(73)
        return real_receive(message,die)
    if mode=='wrong_ack':
        def corrupt(frame):
            value=json.loads(frame);value['lease_ack']+=1;write(json.dumps(value)+'\\n')
        return real_receive(message,corrupt)
    fds=real_receive(message,write)
    if mode=='after_ack':os._exit(74)
    return fds
w.receive_leases=receive
def compute(argv):
    if mode=='timeout':
        signal.pthread_sigmask(signal.SIG_BLOCK,{{signal.SIGUSR1}});signal.sigwait({{signal.SIGUSR1}})
    print(json.dumps({{'pid':os.getpid(),'fd_count':len(os.listdir('/dev/fd'))}}))
    return 0
m.main=compute
m._resident_worker()
'''
    path.write_text(text)
    return path


@pytest.mark.parametrize('mode',['before_ack','wrong_ack','after_ack','timeout'])
def test_ack_failures_release_and_restart(tmp_path,monkeypatch,mode):
    import src.runtime.warm_materializer as w
    wrapper=_worker_wrapper(tmp_path,mode)
    monkeypatch.setattr(w,'_SCRIPT',wrapper)
    worker=w.ResidentMaterializer()
    requests,inflight,path=queued(tmp_path)
    batch=q._new_claim_batch(inflight,(path,))
    command=(sys.executable,str(wrapper),'--input-json',str(batch/path.name),'--commit')
    try:
        with pytest.raises((EOFError,ValueError,subprocess.TimeoutExpired)):
            worker.run(command,timeout=3,lease_fds=q._claim_lease_fds([batch/path.name]))
        assert worker._process is None
        q._release_claim_batch(batch)
        assert q._claim_owner_alive(batch) is False
        # A replacement worker can accept another invocation after any failure.
        wrapper=_worker_wrapper(tmp_path,'restart')
        monkeypatch.setattr(w,'_SCRIPT',wrapper)
        result=worker.run((sys.executable,str(wrapper),'--input-json','inert','--commit'),timeout=5)
        assert result.returncode==0
        print('PROBE',json.dumps(dict(probe='ack_fault',mode=mode,restarted=True)))
    finally:
        worker.close()


def test_resident_repeated_invocations_do_not_leak_received_fds(tmp_path,monkeypatch):
    import src.runtime.warm_materializer as w
    wrapper=_worker_wrapper(tmp_path,'repeat')
    monkeypatch.setattr(w,'_SCRIPT',wrapper)
    worker=w.ResidentMaterializer();observed=[];batches=[]
    try:
        for index in range(5):
            requests,inflight,path=queued(tmp_path,city=f'City{index}',name=f'{index}.json')
            batch=q._new_claim_batch(inflight,(path,));batches.append(batch)
            result=worker.run((sys.executable,str(wrapper),'--input-json',str(batch/path.name),'--commit'),
                timeout=5,lease_fds=q._claim_lease_fds([batch/path.name]))
            assert result.returncode==0
            observed.append(json.loads(result.stdout))
            q._release_claim_batch(batch)
        # Next frame is processed only after previous frame's finally closes fds.
        worker.run((sys.executable,str(wrapper),'--input-json','inert','--commit'),timeout=5)
        assert len({v['pid'] for v in observed})==1
        assert len({v['fd_count'] for v in observed})==1
        assert all(q._claim_owner_alive(b) is False for b in batches)
        print('PROBE',json.dumps(dict(probe='resident_repeat',observations=observed)))
    finally:
        worker.close()
