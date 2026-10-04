# Created: 2026-10-03
# Last reused/audited: 2026-10-03
# Authority basis: consult REQ-20261003-211813-a30820 (lease-v1 re-review);
#   copied from artifacts/merge_safety_lease_v1_review/test_runtime_boundaries.py.
# Seam changes: none.
"""End-to-end controls for the independent lease-v1 review; no forecast writes."""
from __future__ import annotations
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
from tests.adversarial.test_execution_lease_adversaries import _request
import src.data.replacement_forecast_live_materialization_queue as q
import src.runtime.warm_materializer as w

ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture(autouse=True)
def cleanup():
    before=set(q._HELD_CLAIM_LEASES)
    yield
    for batch in set(q._HELD_CLAIM_LEASES)-before:q._release_claim_batch(Path(batch))


def test_same_family_input_aba_allows_concurrent_resident_execution(tmp_path,monkeypatch):
    requests=tmp_path/'requests';requests.mkdir();inflight=tmp_path/'inflight'
    a=requests/'original.json';a.write_text(json.dumps(_request()))
    b=tmp_path/'repaired';b.write_text(json.dumps(_request(baseline_source_run_id='new-baseline-run')))
    duplicate=requests/'duplicate.json';duplicate.write_bytes(a.read_bytes());parked=tmp_path/'parked'
    load=q._load_request_payload_for_coalescing;injected=False
    def swap(path):
        nonlocal injected
        if Path(path)!=a or injected:return load(path)
        injected=True
        os.replace(a,parked);os.replace(b,a)
        try:return load(path)
        finally:os.replace(a,b);os.replace(parked,a)
    monkeypatch.setattr(q,'_load_request_payload_for_coalescing',swap)
    one=q._new_claim_batch(inflight,(a,))
    two=None
    try:two=q._new_claim_batch(inflight,(duplicate,))
    except q._ClaimIdentityOwned:pass
    if two is None:return
    ready=tmp_path/'ready';ready.mkdir()
    wrapper=tmp_path/'worker.py'
    wrapper.write_text(f'''import importlib.util,json,os,signal,sys
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
spec=importlib.util.spec_from_file_location('review_runtime_worker',{str(ROOT/'scripts/materialize_replacement_forecast_live.py')!r})
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
def compute(argv):
    signal.pthread_sigmask(signal.SIG_BLOCK,{{signal.SIGUSR1}})
    path=Path(argv[argv.index('--input-json')+1]);body=json.loads(path.read_text())
    Path({str(ready)!r},str(os.getpid())+'.json').write_text(json.dumps({{'pid':os.getpid(),'city':body['city'],'baseline':body['baseline_source_run_id']}}))
    signal.sigwait({{signal.SIGUSR1}})
    return 0
m.main=compute
m._resident_worker()
''')
    monkeypatch.setattr(w,'_SCRIPT',wrapper)
    workers=[w.ResidentMaterializer(),w.ResidentMaterializer()]
    paths=[one/a.name,two/duplicate.name]
    pool=concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        pending=[pool.submit(worker.run,(sys.executable,str(wrapper),'--input-json',str(path),'--commit'),
            timeout=10,lease_fds=q._claim_lease_fds([path])) for worker,path in zip(workers,paths)]
        deadline=time.monotonic()+7
        while len(list(ready.glob('*.json')))<2 and time.monotonic()<deadline:
            if any(task.done() for task in pending):
                for task in pending:
                    if task.done():task.result()
                break
            time.sleep(.01)
        observations=[json.loads(p.read_text()) for p in ready.glob('*.json')]
        print('PROBE',json.dumps({'probe':'real_duplicate_execution','workers':observations,
            'claims_held':[q._claim_owner_alive(one),q._claim_owner_alive(two)],
            'recorded_baselines':[q._claim_records(one)[0].identity[-2],q._claim_records(two)[0].identity[-2]]}))
        assert len(observations)==2, 'counterexample synchronization failed'
        assert len({(v['city'],v['baseline']) for v in observations})==2, 'two real residents are computing the same input identity'
    finally:
        for worker in workers:worker.close()
        pool.shutdown(wait=True)


def test_staging_crash_reaches_the_discovery_job_gate(tmp_path,monkeypatch):
    requests=tmp_path/'requests';requests.mkdir();seeds=tmp_path/'seeds';seeds.mkdir()
    inflight=tmp_path/'inflight';path=requests/'London.json';path.write_text(json.dumps(_request()))
    code=f'''import os,sys
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
import src.data.replacement_forecast_live_materialization_queue as q
write=q._write_lease_claim_metadata
def die(*a,**k):write(*a,**k);os._exit(71)
q._write_lease_claim_metadata=die
q._new_claim_batch(Path({str(inflight)!r}),(Path({str(path)!r}),))
'''
    process=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=15)
    assert process.returncode==71,process.stderr
    # Finish the surviving request through the actual queue's preflight/receipt
    # path and an inert successful runner (no DB computation).
    monkeypatch.setattr(q,'_claim_db_fingerprint',lambda db:1)
    monkeypatch.setattr(q,'_current_money_risk_families',lambda:frozenset())
    monkeypatch.setattr(q,'_current_global_auction_scope_families',lambda *a,**k:frozenset())
    monkeypatch.setattr(q,'_priority_map_with_names',lambda db,files,p,**k:({x.name:(-1,x.name) for x in files},{x.name for x in files}))
    done=q.process_replacement_forecast_live_materialization_queue(request_dir=requests,
        processed_dir=tmp_path/'processed',failed_dir=tmp_path/'failed',forecast_db=None,
        seed_limit=0,limit=1,lane=q.MATERIALIZATION_LANE_PRIORITY,
        runner=lambda argv:subprocess.CompletedProcess(argv,0,'',''))
    assert done.processed_count==1 and not list(requests.glob('*.json'))
    import src.ingest.forecast_live_daemon as daemon
    import src.data.replacement_forecast_production as production
    import src.data.replacement_forecast_seed_discovery as discovery
    cfg=dict(request_dir=requests,seed_dir=seeds,seed_discovery_limit=1,inflight_dir=inflight,
        forecast_db=tmp_path/'absent.db',raw_manifest_dir=tmp_path/'manifests')
    monkeypatch.setattr(production,'_replacement_forecast_live_materialization_queue_config',lambda:cfg)
    calls=[]
    class Result:
        status='DISCOVERED_TEST'
        discovered_count=0
        def as_dict(self):return {'status':self.status}
    monkeypatch.setattr(discovery,'discover_replacement_forecast_materialization_seeds',lambda **kwargs:(calls.append(kwargs) or Result()))
    monkeypatch.setattr(daemon,'_replacement_forecast_discovery_revision',lambda cfg:None)
    job=getattr(daemon._replacement_forecast_discovery_job,'__wrapped__',daemon._replacement_forecast_discovery_job)
    report=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
    before=job()
    # Positive control: cleaning only the now-ownerless metadata unblocks the
    # real job immediately; nothing else (time/input/database) changes.
    stages=list(inflight.glob('.staging.*'))
    for stage in stages:q._remove_empty_claim_batch(stage)
    after=job()
    print('PROBE',json.dumps(dict(probe='discovery_job_gate',completed_request=done.processed_count,
        reconcile_quiescent=report.quiescent,before=before,after=after,discovery_calls=len(calls))))
    assert before.get('status')!='deferred_active_materialization_queue', 'the crash leaves an undrainable global discovery gate'


def test_parent_loss_between_announcement_and_fd_send(tmp_path):
    # Announce an invocation over stdin but close before sending its datagram.
    # Isolated actual resident loop; no computation allowed.
    wrapper=tmp_path/'worker.py';ready=tmp_path/'receiving'
    wrapper.write_text(f'''import importlib.util,os,sys
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
spec=importlib.util.spec_from_file_location('review_interrupted_transfer',{str(ROOT/'scripts/materialize_replacement_forecast_live.py')!r})
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
import src.runtime.warm_materializer as w
receive=w.receive_leases
def marked(*a,**k):Path({str(ready)!r}).write_text('receiving');return receive(*a,**k)
w.receive_leases=marked
m.main=lambda argv:os._exit(99)
m._resident_worker()
''')
    import socket
    parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM)
    worker=subprocess.Popen([sys.executable,str(wrapper)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,pass_fds=(child.fileno(),),env={**os.environ,w.LEASE_SOCKET_ENV:str(child.fileno())})
    child.close()
    try:
        worker.stdin.write(json.dumps({'request_id':'interrupted','argv':['--input-json','inert','--commit'],'lease_fds':1})+'\n');worker.stdin.flush()
        deadline=time.monotonic()+5
        while not ready.exists() and time.monotonic()<deadline:
            if worker.poll() is not None:break
            time.sleep(.01)
        assert ready.exists(),worker.stderr.read() if worker.poll() is not None else 'worker did not start'
        parent.close();worker.stdin.close()
        exited=True
        try:worker.wait(timeout=.5)
        except subprocess.TimeoutExpired:exited=False
        print('PROBE',json.dumps(dict(probe='announcement_gap',exited_after_parent_channels_closed=exited,
            computation_started=False)))
        assert exited, 'resident waits indefinitely on the orphaned datagram receive'
    finally:
        parent.close()
        if worker.poll() is None:worker.kill();worker.wait(timeout=5)
        worker.stdout.close();worker.stderr.close()
