# Created: 2026-10-04
# Last reused/audited: 2026-10-04
# Authority basis: lease-v1 round-5 consult; copied unchanged from
#   artifacts/merge_safety_lease_v1_round5_review/test_round5_invariants.py. Run from the checkout root.
"""Round-5 independent invariants. Pinned checkout; temporary queues only."""
from __future__ import annotations
import contextlib
import errno
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import pytest
import src.data.replacement_forecast_live_materialization_queue as q
from tests.adversarial.test_execution_lease_adversaries import _request

ROOT = Path.cwd().resolve()

@pytest.fixture(autouse=True)
def cleanup():
    before=set(q._HELD_CLAIM_LEASES)
    yield
    for b in set(q._HELD_CLAIM_LEASES)-before:
        q._release_claim_batch(Path(b))

def queued(root):
    requests=root/'requests';requests.mkdir()
    return requests, root/'inflight'


def partial_shared_claim(tmp_path, monkeypatch):
    """Both publications were distinct when selected; a republish unifies them.
    All publisher changes create new immutable inodes through the real publisher.
    The first slot is replaced again between lease and capture and must be rejected.
    """
    requests,inflight=queued(tmp_path)
    first,second=requests/'London.first.json',requests/'London.second.json'
    q._write_request(first,_request())
    q._write_request(second,_request(baseline_source_run_id='other-run'))
    real_read=q._read_claim_slot;second_reads=0
    def read(source, **kwargs):
        nonlocal second_reads
        if source==second:
            second_reads+=1
            if second_reads==1:
                q._write_request(second,_request())
        return real_read(source,**kwargs)
    real_replace=os.replace;changed=False
    def replace(source,target,*args,**kwargs):
        nonlocal changed
        if Path(source)==first and not changed:
            changed=True
            q._write_request(first,_request(baseline_source_run_id='replacement-run'))
        return real_replace(source,target,*args,**kwargs)
    monkeypatch.setattr(q,'_read_claim_slot',read)
    monkeypatch.setattr(q.os,'replace',replace)
    batch,admitted,reasons=q._claim_available_slots(inflight,(first,second))
    assert changed and second_reads>=1 and batch is not None and admitted==(second,)
    duplicate=requests/'London.third.json';q._write_request(duplicate,_request())
    competing=None
    try:competing=q._new_claim_batch(inflight,(duplicate,))
    except q._ClaimIdentityOwned:pass
    return requests,inflight,batch,second.name,competing,duplicate.name,reasons


def test_dropped_slot_cannot_release_a_surviving_slots_shared_lease(tmp_path,monkeypatch):
    requests,inflight,batch,name,other,other_name,reasons=partial_shared_claim(tmp_path,monkeypatch)
    record=q._claim_records(batch)[0]
    print('PROBE',json.dumps({'probe':'shared_drop','admitted_identity':record.identity,
        'retained_fds':len(q._claim_lease_fds([batch/name])),
        'metadata_leases':q._claim_metadata(batch)['leases'],
        'competing_claim':other is not None,'reasons':reasons}))
    assert q._claim_lease_fds([batch/name]) and other is None, 'dropping one slot unlocked a surviving same-identity slot'


def test_two_residents_cannot_execute_after_shared_slot_dropped(tmp_path,monkeypatch):
    import concurrent.futures,time
    import src.runtime.warm_materializer as w
    requests,inflight,batch,name,other,other_name,reasons=partial_shared_claim(tmp_path,monkeypatch)
    if other is None:return
    ready=tmp_path/'ready';ready.mkdir()
    wrapper=tmp_path/'worker.py'
    wrapper.write_text(f'''import importlib.util,json,os,signal,sys
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
spec=importlib.util.spec_from_file_location('r5worker',{str(ROOT/'scripts/materialize_replacement_forecast_live.py')!r})
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
import src.data.replacement_forecast_live_materialization_queue as q
def compute(argv):
 signal.pthread_sigmask(signal.SIG_BLOCK,{{signal.SIGUSR1}})
 path=Path(argv[argv.index('--input-json')+1]);body=m._ConsumedInputs().read(path,role=q.REQUEST_ROLE)
 m._require_claimed_bytes(path,body)
 Path({str(ready)!r},str(os.getpid())+'.json').write_text(json.dumps({{'pid':os.getpid(),'identity':q._request_semantic_key(json.loads(body))}}))
 signal.sigwait({{signal.SIGUSR1}})
 return 0
m.main=compute
m._resident_worker()
''')
    monkeypatch.setattr(w,'_SCRIPT',wrapper)
    workers=[w.ResidentMaterializer(),w.ResidentMaterializer()]
    paths=[batch/name,other/other_name]
    pool=concurrent.futures.ThreadPoolExecutor(2)
    try:
        pending=[pool.submit(worker.run,[sys.executable,str(wrapper),'--input-json',str(p),'--commit'],timeout=10,lease_fds=q._claim_lease_fds([p])) for worker,p in zip(workers,paths)]
        deadline=time.monotonic()+7
        while len(list(ready.glob('*.json')))<2 and time.monotonic()<deadline:
            for future in pending:
                if future.done():future.result()
            time.sleep(.01)
        observations=[json.loads(p.read_text()) for p in ready.glob('*.json')]
        assert len(observations)==2, 'failed to synchronize two real residents'
        print('PROBE',json.dumps({'probe':'shared_drop_real_workers','workers':observations,'claim_hash_checks_passed':True}))
        assert len({tuple(v['identity']) for v in observations})==2, 'two actual residents consume identical semantic input under disjoint ownership'
    finally:
        for worker in workers:worker.close()
        pool.shutdown(wait=True)


def test_capture_receipt_filename_cannot_hide_repaired_regular_request(tmp_path,monkeypatch):
    requests,inflight=queued(tmp_path)
    source=requests/'receipt.json';target=tmp_path/'target';target.write_text('untouched')
    source.symlink_to(target)
    real=os.rename;changed=False
    def rename(old,new,*args,**kwargs):
        nonlocal changed
        if Path(old)==source and not changed:
            changed=True;q._write_request(source,_request())
        return real(old,new,*args,**kwargs)
    monkeypatch.setattr(q.os,'rename',rename)
    q._quarantine_request_aliases(requests)
    q._settle_abandoned_captures(requests)
    captures=list((tmp_path/q._REQUEST_ALIAS_DIR).glob('.capture.*'))
    print('PROBE',json.dumps({'probe':'receipt_name_collision','repair_happened':changed,
        'request_exists':source.exists(),'capture_entries':[str(p.relative_to(tmp_path)) for c in captures for p in c.iterdir()]}))
    assert changed
    assert any(q._load_request_payload_for_coalescing(p)==_request() for p in requests.glob('*.json')), 'repaired receipt.json is mistaken for quarantine terminal metadata'


def test_reconcile_accounts_for_regular_capture_after_crash(tmp_path):
    requests,inflight=queued(tmp_path)
    source=requests/'London.json';target=tmp_path/'target';target.write_text('untouched');source.symlink_to(target)
    code=f'''from pathlib import Path
import os,sys
sys.path.insert(0,{str(ROOT)!r})
import src.data.replacement_forecast_live_materialization_queue as q
source=Path({str(source)!r})
real=os.rename
changed=False
def rename(old,new,*a,**kw):
 global changed
 if Path(old)==source and not changed:
  changed=True;q._write_request(source,{_request()!r})
 return real(old,new,*a,**kw)
q.os.rename=rename
q._settle_capture=lambda *a,**kw:os._exit(71)
q._quarantine_request_aliases(source.parent)
'''
    child=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=15)
    assert child.returncode==71,child.stderr
    report=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
    captures=list((tmp_path/q._REQUEST_ALIAS_DIR).glob('.capture.*'))
    print('PROBE',json.dumps({'probe':'capture_crash_reconcile','quiescent':report.quiescent,
        'request_restored':source.exists(),'captured_regular_count':sum(q._is_regular_entry(p) for c in captures for p in c.iterdir())}))
    assert source.exists() or not report.quiescent, 'reconcile falsely certifies quiescence with a recoverable regular request still captured'


def live_fds():
    found=set()
    for name in os.listdir('/dev/fd'):
        try:fd=int(name);os.fstat(fd)
        except (OSError,ValueError):continue
        found.add(fd)
    return found


def test_empty_admission_closes_staging_descriptor(tmp_path):
    requests,inflight=queued(tmp_path)
    first=requests/'first.json';q._write_request(first,_request())
    original=q._new_claim_batch(inflight,[first])
    duplicate=requests/'duplicate.json';q._write_request(duplicate,_request())
    before=live_fds()
    try:
        for i in range(8):
            batch,claimed,reasons=q._claim_available_slots(inflight,[duplicate])
            assert batch is None
        after=live_fds()
        leaked=after-before
        print('PROBE',json.dumps({'probe':'empty_admission_fd','iterations':8,'new_live_descriptors':sorted(leaked)}))
        assert not leaked, 'no-admission return bypasses staging descriptor close'
    finally:
        for fd in live_fds()-before:
            try:os.close(fd)
            except OSError:pass
