# Created: 2026-10-03
# Last reused/audited: 2026-10-03
# Authority basis: lease-v1 round-3 consult; copied unchanged from
#   artifacts/merge_safety_lease_v1_round3/test_round3_boundaries.py. Run from the checkout root.
"""Round-3 independent boundary probes. Temporary queues and inert computation only."""
from __future__ import annotations
import concurrent.futures
import errno
import fcntl
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
from tests.adversarial.test_execution_lease_adversaries import _request, three_slots
from tests.adversarial.test_lease_v1_review import _worker_wrapper
ROOT=Path.cwd().resolve()

@pytest.fixture(autouse=True)
def cleanup():
    before=set(q._HELD_CLAIM_LEASES)
    yield
    for batch in set(q._HELD_CLAIM_LEASES)-before:
        q._release_claim_batch(Path(batch))

def queued(tmp_path):
    requests=tmp_path/'requests'; requests.mkdir()
    return requests,tmp_path/'inflight'

@pytest.mark.parametrize('body',[b'{',b'[]',b'null',b'"text"',b'\xff',b'{}'])
def test_malformed_body_leases_nothing(tmp_path,body):
    requests,inflight=queued(tmp_path);p=requests/'London.json';p.write_bytes(body)
    with pytest.raises((ValueError,FileNotFoundError)):
        q._new_claim_batch(inflight,[p])
    assert not list(inflight.rglob('*.lease'))
    assert p.read_bytes()==body

def test_filename_never_overrides_body_identity(tmp_path):
    requests,inflight=queued(tmp_path);p=requests/'London.high.json'
    p.write_text(json.dumps(_request(city='Paris',temperature_metric='low')))
    b=q._new_claim_batch(inflight,[p]);r=q._claim_records(b)[0]
    assert r.identity==q._request_semantic_key(_request(city='Paris',temperature_metric='low'))

def test_symlinked_request_cannot_change_executing_identity(tmp_path,monkeypatch):
    requests,inflight=queued(tmp_path)
    target=tmp_path/'publisher-latest.json';target.write_text(json.dumps(_request()))
    alias=requests/'London.alias.json';alias.symlink_to(target)
    try:
        one=q._new_claim_batch(inflight,[alias])
    except (OSError,ValueError):
        return  # Rejecting symlink request publications is safe.
    claimed=one/alias.name
    assert claimed.is_symlink()
    # The publisher updates its latest pointer normally; it never touches inflight.
    newbody=_request(baseline_source_run_id='new-baseline-run')
    update=tmp_path/'publisher-new.tmp';update.write_text(json.dumps(newbody));os.replace(update,target)
    duplicate=requests/'London.new.json';duplicate.write_text(json.dumps(newbody))
    two=None
    try:two=q._new_claim_batch(inflight,[duplicate])
    except q._ClaimIdentityOwned:pass
    # Real worker named-request reader, not a custom permissive open.
    from scripts.materialize_replacement_forecast_live import _ConsumedInputs
    read=json.loads(_ConsumedInputs('probe').read(claimed,role=q.REQUEST_ROLE))
    print('ROUND3',json.dumps({'probe':'symlink_identity','claimed_is_symlink':claimed.is_symlink(),
        'recorded':q._claim_records(one)[0].identity,'actual_worker_read':q._request_semantic_key(read),
        'second_owner':two is not None}))
    assert two is None and q._claim_records(one)[0].identity==q._request_semantic_key(read)

def test_staging_observation_must_not_delete_a_now_held_constructor(tmp_path,monkeypatch):
    requests,inflight=queued(tmp_path);p=requests/'London.json';p.write_text(json.dumps(_request()))
    original_open=q.os.open; original_write=q._write_lease_claim_metadata
    observed=[];attempted=False;deleted_while_held=[]
    def open_stage(path,*args,**kw):
        nonlocal attempted
        if Path(path).name.startswith(q._STAGING_PREFIX) and not attempted:
            attempted=True
            # Scanner catches the public directory after mkdir, before its flock.
            observed.extend(q._abandoned_staging(inflight))
        return original_open(path,*args,**kw)
    def write_and_drain(directory,*args,**kw):
        original_write(directory,*args,**kw)
        assert Path(directory) in observed
        probe=original_open(directory,os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(probe)
        # Resume the paused scanner after the constructor has acquired the flock.
        with monkeypatch.context() as m:
            m.setattr(q,'_abandoned_staging',lambda _:tuple(observed))
            q._drain_abandoned_staging(inflight)
        deleted_while_held.append(not Path(directory).exists())
    monkeypatch.setattr(q.os,'open',open_stage)
    monkeypatch.setattr(q,'_write_lease_claim_metadata',write_and_drain)
    caught=None
    try:q._new_claim_batch(inflight,[p])
    except OSError as exc:caught=repr(exc)
    print('ROUND3',json.dumps({'probe':'staging_before_flock','deleted_while_held':deleted_while_held,'error':caught,'request_retained':p.exists()}))
    assert not any(deleted_while_held)

@pytest.mark.parametrize('phase',['after_acquire','after_mkdir','after_dir_open','after_flock','during_metadata','after_metadata','after_publish','after_first_move','after_fsync'])
def test_process_crash_boundaries_preserve_requests(tmp_path,phase):
    requests,inflight=queued(tmp_path)
    files=[]
    for city in ['London','Paris','Hong Kong']:
        p=requests/(city.replace(' ','_')+'.json');p.write_text(json.dumps(_request(city=city)));files.append(p)
    code=f'''import sys,os,fcntl,json
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
import src.data.replacement_forecast_live_materialization_queue as q
phase={phase!r};inflight=Path({str(inflight)!r});files=[Path(v) for v in {list(map(str,files))!r}]
def die():os._exit(71)
acq=q._lease.acquire_all
if phase=='after_acquire':
 def injected(*a,**k):r=acq(*a,**k);die()
 q._lease.acquire_all=injected
mkdir=Path.mkdir
if phase=='after_mkdir':
 def injected(p,*a,**k):
  r=mkdir(p,*a,**k)
  if p.name.startswith(q._STAGING_PREFIX):die()
  return r
 Path.mkdir=injected
op=q.os.open
if phase=='after_dir_open':
 def injected(p,*a,**k):
  fd=op(p,*a,**k)
  if Path(p).name.startswith(q._STAGING_PREFIX):die()
  return fd
 q.os.open=injected
fl=q.fcntl.flock
if phase=='after_flock':
 import stat
 def injected(fd,*a,**k):
  r=fl(fd,*a,**k)
  if stat.S_ISDIR(os.fstat(fd).st_mode):die()
  return r
 q.fcntl.flock=injected
write=q._write_lease_claim_metadata
if phase in ('during_metadata','after_metadata'):
 def injected(d,*a,**k):
  if phase=='during_metadata':(d/q._CLAIM_METADATA_NAME).write_text('{{');die()
  write(d,*a,**k);die()
 q._write_lease_claim_metadata=injected
rename=q.os.rename
if phase=='after_publish':
 def injected(*a,**k):rename(*a,**k);die()
 q.os.rename=injected
replace=q.os.replace
if phase=='after_first_move':
 def injected(*a,**k):replace(*a,**k);die()
 q.os.replace=injected
sync=q._fsync_directory
if phase=='after_fsync':
 def injected(*a,**k):sync(*a,**k);die()
 q._fsync_directory=injected
q._new_claim_batch(inflight,files)
'''
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=15)
    assert result.returncode==71,result.stderr
    q._recover_stale_claims(request_path=requests,inflight_path=inflight,lease_only=True)
    report=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
    found=[json.loads(p.read_text())['city'] for p in requests.glob('*.json')]
    print('ROUND3',json.dumps({'probe':'process_crash','phase':phase,'cities':sorted(found),'quiescent':report.quiescent,'residue':list(map(str,inflight.rglob('*')))}))
    assert sorted(found)==['Hong Kong','London','Paris']
    assert report.quiescent and not q.inflight_requests_pending(inflight)
    assert not list(inflight.rglob('*'))

@pytest.mark.parametrize('admission',[q.CLAIM_PREFIX,q.CLAIM_EACH])
def test_partial_admission_never_splits_duplicate_keys(tmp_path,admission):
    requests,inflight=queued(tmp_path);paths=[]
    for name,city in [('a.json','London'),('b.json','Paris'),('c.json','London'),('d.json','Rome')]:
        p=requests/name;p.write_text(json.dumps(_request(city=city)));paths.append(p)
    owner_source=requests/'Paris.owner.json';owner_source.write_text(paths[1].read_text())
    owner=q._new_claim_batch(inflight,[owner_source])
    with q._claim_construction(inflight,paths,admission=admission) as (batch,admitted,reasons):pass
    assert list(admitted)==([paths[0]] if admission==q.CLAIM_PREFIX else [paths[0],paths[2],paths[3]])
    duplicate=requests/'London.new.json';duplicate.write_text(json.dumps(_request()))
    with pytest.raises(q._ClaimIdentityOwned):q._new_claim_batch(inflight,[duplicate])
    assert q._claim_owner_alive(owner) and q._claim_owner_alive(batch)

def test_fragmented_stream_is_not_a_malformed_invocation(tmp_path,monkeypatch):
    wrapper=_worker_wrapper(tmp_path,'normal');monkeypatch.setattr(w,'_SCRIPT',wrapper)
    requests,inflight=queued(tmp_path);p=requests/'London.json';p.write_text(json.dumps(_request()))
    batch=q._new_claim_batch(inflight,[p]);worker=w.ResidentMaterializer()
    send=socket.send_fds;recv=socket.recv_fds
    # Limit one real recvmsg to a valid short stream read; all sent bytes/fds
    # are genuine and unchanged. This exercises the receiver's framing law.
    text=wrapper.read_text().replace('m._resident_worker()',
        "\nimport socket\norig_recv=socket.recv_fds\nsocket.recv_fds=lambda s,b,n,*a,**k:orig_recv(s,min(8,b),n,*a,**k)\nm._resident_worker()")
    wrapper.write_text(text)
    failure=None;result=None
    try:
        try:result=worker.run([sys.executable,str(wrapper),'--input-json',str(batch/p.name),'--commit'],timeout=5,lease_fds=q._claim_lease_fds([batch/p.name]))
        except (EOFError,ValueError,subprocess.TimeoutExpired) as exc:failure=repr(exc)
        print('ROUND3',json.dumps({'probe':'short_stream','failure':failure,'worker_reaped':worker._process is None,'parent_holds':q._claim_owner_alive(batch)}))
        assert result is not None and result.returncode==0
    finally:worker.close()
