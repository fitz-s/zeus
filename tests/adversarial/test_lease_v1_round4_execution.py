# Created: 2026-10-03
# Last reused/audited: 2026-10-03
# Authority basis: lease-v1 round-3 consult; copied unchanged from
#   artifacts/merge_safety_lease_v1_round3/test_round3_execution.py. Run from the checkout root.
"""Executor consequences and stream controls for lease-v1 round 3. No forecast writes."""
from __future__ import annotations
import concurrent.futures
import fcntl
import hashlib
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
import src.runtime.warm_materializer as w
from tests.adversarial.test_execution_lease_adversaries import _request
from tests.adversarial.test_lease_v1_review import _worker_wrapper
ROOT=Path.cwd().resolve()

@pytest.fixture(autouse=True)
def cleanup():
    before=set(q._HELD_CLAIM_LEASES)
    yield
    for batch in set(q._HELD_CLAIM_LEASES)-before:q._release_claim_batch(Path(batch))

def test_hardlink_republish_duplicates_real_resident_execution(tmp_path,monkeypatch):
    requests=tmp_path/'requests';requests.mkdir();inflight=tmp_path/'inflight'
    target=requests/'publisher.json';q._write_request(target,_request())
    alias=requests/'claimed.json';os.link(target,alias)
    try:one=q._new_claim_batch(inflight,[alias])
    except q.RequestNotRegular:return
    q._write_request(target,_request(baseline_source_run_id='new-baseline-run'))
    duplicate=target
    try:two=q._new_claim_batch(inflight,[duplicate])
    except q._ClaimIdentityOwned:return
    ready=tmp_path/'ready';ready.mkdir();wrapper=tmp_path/'worker.py'
    wrapper.write_text(f'''import importlib.util,hashlib,json,os,signal,sys
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
spec=importlib.util.spec_from_file_location('review_symlink_worker',{str(ROOT/'scripts/materialize_replacement_forecast_live.py')!r})
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
def compute(argv):
 signal.pthread_sigmask(signal.SIG_BLOCK,{{signal.SIGUSR1}})
 path=Path(argv[argv.index('--input-json')+1])
 raw=m._ConsumedInputs('probe').read(path,role=m.REQUEST_ROLE)
 body=json.loads(raw)
 Path({str(ready)!r},str(os.getpid())+'.json').write_text(json.dumps({{'pid':os.getpid(),'city':body['city'],'baseline':body['baseline_source_run_id'],'sha256':hashlib.sha256(raw).hexdigest()}}))
 signal.sigwait({{signal.SIGUSR1}})
 return 0
m.main=compute
m._resident_worker()
''')
    monkeypatch.setattr(w,'_SCRIPT',wrapper)
    workers=[w.ResidentMaterializer(),w.ResidentMaterializer()]
    paths=[one/alias.name,two/duplicate.name]
    pool=concurrent.futures.ThreadPoolExecutor(max_workers=2);tasks=[];observations=[]
    try:
        for worker,path in zip(workers,paths):
            tasks.append(pool.submit(worker.run,[sys.executable,str(wrapper),'--input-json',str(path),'--commit'],timeout=10,lease_fds=q._claim_lease_fds([path])))
        deadline=time.monotonic()+7
        while len(list(ready.glob('*.json')))<2 and time.monotonic()<deadline:
            for task in tasks:
                if task.done():task.result()
            time.sleep(.01)
        observations=[json.loads(p.read_text()) for p in ready.glob('*.json')]
        print('ROUND3',json.dumps({'probe':'hardlink_real_residents','workers':observations,'held':[q._claim_owner_alive(one),q._claim_owner_alive(two)],'recorded':[q._claim_records(one)[0].identity,q._claim_records(two)[0].identity]}))
        assert len(observations)==2, 'synchronization failed'
        assert len({v['sha256'] for v in observations})==2,'same request bytes executing concurrently under two different identity leases'
    finally:
        for p in ready.glob('*.json'):
            try:os.kill(json.loads(p.read_text())['pid'],signal.SIGUSR1)
            except ProcessLookupError:pass
        for task in tasks:
            try:task.result(timeout=12)
            except Exception:pass
        for worker in workers:worker.close()
        pool.shutdown(wait=True)
