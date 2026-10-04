# Created: 2026-10-04
# Last reused/audited: 2026-10-04
# Authority basis: lease-v1 round-4 consult; copied unchanged from
#   artifacts/merge_safety_lease_v1_round4/test_round4_boundaries.py. Run from the checkout root.
# One change: test_fifo_reader_rejects_without_waiting_for_a_writer uses a race-free
#   harness (round-5 consult confirmed the original's text-buffer race); see its comment.
"""Independent round-4 probes on 844465199. Temporary queues only; no live DB writes."""
from __future__ import annotations
import errno
import fcntl
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import pytest
import src.data.replacement_forecast_live_materialization_queue as q
import src.data.materialization_execution_lease as lease
from tests.adversarial.test_execution_lease_adversaries import _request

ROOT=Path.cwd().resolve()

@pytest.fixture(autouse=True)
def release_claims():
    before=set(q._HELD_CLAIM_LEASES)
    yield
    for b in set(q._HELD_CLAIM_LEASES)-before:q._release_claim_batch(Path(b))

def setup_queue(tmp_path):
    requests=tmp_path/'requests';requests.mkdir()
    return requests,tmp_path/'inflight'

def atomic_publish(path, payload):
    temporary=path.with_name(path.name+'.new')
    q._write_request(temporary,payload)
    os.replace(temporary,path)

@pytest.mark.parametrize('cut',['before_lstat','before_rename'])
def test_quarantine_does_not_terminalize_a_new_regular_publication(tmp_path,monkeypatch,cut):
    requests,inflight=setup_queue(tmp_path)
    target=tmp_path/'target';target.write_text(json.dumps(_request()))
    source=requests/'London.json';source.symlink_to(target)
    replacement=_request(baseline_source_run_id='repaired-run')
    real_quarantine=q._quarantine_request_alias
    real_rename=os.rename
    swapped=False
    if cut=='before_lstat':
        def quarantine(path):
            nonlocal swapped
            if path==source and not swapped:
                atomic_publish(source,replacement);swapped=True
            return real_quarantine(path)
        monkeypatch.setattr(q,'_quarantine_request_alias',quarantine)
    else:
        def rename(src,dst,*a,**kw):
            nonlocal swapped
            if Path(src)==source and not swapped:
                atomic_publish(source,replacement);swapped=True
            return real_rename(src,dst,*a,**kw)
        monkeypatch.setattr(q.os,'rename',rename)
    batch,claimed,reasons=q._claim_available_slots(inflight,(source,))
    quarantine=tmp_path/q._REQUEST_ALIAS_DIR
    regular=[p for p in quarantine.iterdir() if p.is_file() and not p.is_symlink() and not p.name.endswith('.receipt.json')]
    receipts=[json.loads(p.read_text()) for p in quarantine.glob('*.receipt.json')]
    print('PROBE',json.dumps(dict(probe='quarantine_republish',cut=cut,swapped=swapped,request_pending=source.exists(),batch=str(batch),claimed=[str(p) for p in claimed],regular_quarantined=[p.name for p in regular],receipts=receipts,target_unchanged=json.loads(target.read_text())==_request())))
    assert swapped
    assert source.exists() or (batch is not None and (batch/source.name).exists()), 'a regular repaired publication was discarded as an alias'


def test_hardlink_republish_cannot_change_claimed_identity(tmp_path):
    from scripts.materialize_replacement_forecast_live import _ConsumedInputs
    requests,inflight=setup_queue(tmp_path)
    a=requests/'London.a.json';q._write_request(a,_request())
    b=requests/'London.b.json';os.link(a,b)
    try:one=q._new_claim_batch(inflight,(a,))
    except q.RequestNotRegular:return  # a hardened constructor may reject external hardlinks
    linked_before=os.stat(one/a.name).st_nlink
    # Real queue publisher writes its QUEUED name, never an inflight pathname.
    q._write_request(b,_request(baseline_source_run_id='new-baseline-run'))
    consumed=_ConsumedInputs().read(one/a.name,role=q.REQUEST_ROLE)
    actual=q._request_semantic_key(json.loads(consumed))
    recorded=q._claim_records(one)[0].identity
    two=None
    try:two=q._new_claim_batch(inflight,(b,))
    except q._ClaimIdentityOwned:pass
    print('PROBE',json.dumps(dict(probe='hardlink_publication',nlink=linked_before,recorded_identity=recorded,executed_identity=actual,second_claim=two is not None,first_held=q._claim_owner_alive(one),second_held=None if two is None else q._claim_owner_alive(two))))
    assert actual==recorded, 'queued hardlink republish mutates another executing request outside its identity lease'


def test_hardlink_atomic_republish_preserves_original_claim(tmp_path):
    requests,inflight=setup_queue(tmp_path)
    a=requests/'a.json';q._write_request(a,_request())
    b=requests/'b.json';os.link(a,b)
    one=q._new_claim_batch(inflight,(a,))
    atomic_publish(b,_request(baseline_source_run_id='new-baseline-run'))
    two=q._new_claim_batch(inflight,(b,))
    assert q._request_semantic_key(json.loads((one/a.name).read_text()))==q._claim_records(one)[0].identity
    assert q._request_semantic_key(json.loads((two/b.name).read_text()))==q._claim_records(two)[0].identity
    assert (one/a.name).stat().st_ino!=(two/b.name).stat().st_ino


def test_same_body_rename_over_is_rejected_and_restored(tmp_path,monkeypatch):
    requests,inflight=setup_queue(tmp_path)
    source=requests/'a.json';q._write_request(source,_request())
    alternate=tmp_path/'alternate';alternate.write_bytes(source.read_bytes())
    real=os.replace;swapped=False
    def replacement(src,dst,*a,**kw):
        nonlocal swapped
        if Path(src)==source and not swapped:
            swapped=True;real(alternate,source)
        return real(src,dst,*a,**kw)
    monkeypatch.setattr(q.os,'replace',replacement)
    with pytest.raises(FileNotFoundError):q._new_claim_batch(inflight,(source,))
    assert source.exists() and json.loads(source.read_text())==_request()
    assert not q._claim_batches(inflight)


def test_fifo_reader_rejects_without_waiting_for_a_writer(tmp_path):
    # Replaced in this tracked copy (round-5 consult, confirmed): the original
    # harness read its first line with text-mode readline() and then called
    # communicate(), which reads the raw pipe and loses text already buffered
    # in the TextIOWrapper; with no product code at all the child's second
    # line was lost 20/20. Same property, race-free: unbuffered binary output,
    # read whole, plus the original's external-writer positive control.
    fifo=tmp_path/'request.json';os.mkfifo(fifo)
    code=f'''import sys,os,json
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
import src.data.replacement_forecast_live_materialization_queue as q
try:q.read_regular_request(Path({str(fifo)!r}));print("READ",flush=True)
except q.RequestNotRegular:print("REJECTED",flush=True)
'''
    child=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    try:
        exited=True
        try:child.wait(timeout=15)
        except subprocess.TimeoutExpired:exited=False
        writer_needed=False
        if not exited:
            try:
                fd=os.open(fifo,os.O_WRONLY|os.O_NONBLOCK);writer_needed=True;os.close(fd)
            except OSError:pass
        out,err=child.communicate(timeout=5)
        print('PROBE',json.dumps(dict(probe='fifo_open',rejected_before_writer=exited,writer_needed=writer_needed,stdout=out.decode(),stderr=err.decode())))
        assert exited and out.decode().strip()=='REJECTED', 'S_ISREG is reached only after a blocking FIFO open'
    finally:
        if child.poll() is None:child.kill();child.wait()


def test_queue_plan_never_reads_an_alias_target(tmp_path,monkeypatch):
    requests,inflight=setup_queue(tmp_path)
    target=tmp_path/'secret';target.write_text(json.dumps(_request()))
    source=requests/'London.json';source.symlink_to(target)
    monkeypatch.setattr(q,'_current_money_risk_families',lambda:frozenset())
    monkeypatch.setattr(q,'_current_global_auction_scope_families',lambda *a,**k:frozenset())
    monkeypatch.setattr(q,'_priority_map_with_names',lambda db,files,p,**k:({x.name:(0,x.name) for x in files},{x.name for x in files}))
    opened=[];real=Path.open
    def tracked(path,*a,**kw):
        if path==source:opened.append(str(path))
        return real(path,*a,**kw)
    monkeypatch.setattr(Path,'open',tracked)
    q._build_request_claim_read_plan(request_path=requests,processed_path=tmp_path/'processed',failed_path=tmp_path/'failed',forecast_db=None,limit=1,lane=q.MATERIALIZATION_LANE_PRIORITY)
    print('PROBE',json.dumps(dict(probe='alias_preplan',following_opens=len(opened))))
    assert not opened, 'advisory planning still follows request aliases before no-follow acquisition'


def test_quarantine_collision_preserves_earlier_evidence(tmp_path,monkeypatch):
    requests,inflight=setup_queue(tmp_path)
    t1=tmp_path/'t1';t1.write_text('one');t2=tmp_path/'t2';t2.write_text('two')
    source=requests/'alias.json';source.symlink_to(t1)
    monkeypatch.setattr(q,'uuid4',lambda:SimpleNamespace(hex='a'*32))
    old=q._quarantine_request_alias(source)
    source.symlink_to(t2)
    # Force only the name collision, not any successful/failing filesystem call.
    new=q._quarantine_request_alias(source)
    print('PROBE',json.dumps(dict(probe='quarantine_collision',same_destination=old==new,original_alias_target=os.readlink(old))))
    assert old!=new and os.readlink(old)==str(t1), 'a destination collision overwrote prior quarantined evidence'


def test_scanner_relocks_replacement_directory_and_keeps_it(tmp_path,monkeypatch):
    inflight=tmp_path/'inflight';inflight.mkdir()
    stage=inflight/'.staging.test';stage.mkdir();parked=tmp_path/'old-stage'
    real=q._abandoned_staging;held=[]
    def observe(path):
        result=real(path)
        stage.rename(parked);stage.mkdir()
        fd=os.open(stage,os.O_RDONLY);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);held.append(fd)
        return result
    monkeypatch.setattr(q,'_abandoned_staging',observe)
    try:
        assert q._drain_abandoned_staging(inflight)==0
        assert stage.exists()
    finally:
        for fd in held:os.close(fd)


def test_named_dependency_alias_is_unfenceable_not_request_authority(tmp_path):
    from scripts.materialize_replacement_forecast_live import _ConsumedInputs
    target=tmp_path/'payload';target.write_bytes(b'{"stable":true}')
    alias=tmp_path/'payload-link';alias.symlink_to(target)
    consumed=_ConsumedInputs()
    assert consumed.read(alias,role='openmeteo_payload')==target.read_bytes()
    record=q._materialization_dependency_record(_request(openmeteo_payload_json=str(alias)),request_dir=tmp_path)
    assert record is None
    with pytest.raises(OSError):_ConsumedInputs().read(alias,role=q.REQUEST_ROLE)


def test_recovery_created_hardlinks_do_not_bypass_lease(tmp_path,monkeypatch):
    requests,inflight=setup_queue(tmp_path)
    original=requests/'London.json';q._write_request(original,_request())
    batch=q._new_claim_batch(inflight,[original]);q._release_claim_batch(batch)
    sync=q._fsync_directory;failed=False
    def fault(path):
        nonlocal failed
        if Path(path)==requests and not failed:
            failed=True
            raise OSError(errno.EIO,'durable-link restore interrupted before unlink')
        return sync(path)
    with monkeypatch.context() as m:
        m.setattr(q,'_fsync_directory',fault)
        with pytest.raises(OSError):q._recover_stale_claims(request_path=requests,inflight_path=inflight)
    q._recover_stale_claims(request_path=requests,inflight_path=inflight)
    pending=sorted(requests.glob('*.json'))
    assert len(pending)==2 and os.path.samestat(pending[0].stat(),pending[1].stat())
    one=q._new_claim_batch(inflight,[pending[0]])
    q._write_request(pending[1],_request(baseline_source_run_id='new-baseline-run'))
    two=None
    try:two=q._new_claim_batch(inflight,[pending[1]])
    except q._ClaimIdentityOwned:pass
    actual=q._request_semantic_key(json.loads((one/pending[0].name).read_text()))
    print('PROBE',json.dumps(dict(probe='recovery_hardlinks',recorded=q._claim_records(one)[0].identity,actual=actual,second_claim=two is not None)))
    assert actual==q._claim_records(one)[0].identity, 'normal recovery produced a writable alias of an executing request'


def test_postmove_dangling_alias_is_quarantined_and_reconcilable(tmp_path,monkeypatch):
    requests,inflight=setup_queue(tmp_path)
    p=requests/'London.json';q._write_request(p,_request())
    real=os.replace;changed=False
    def replace(src,dst,*a,**kw):
        nonlocal changed
        if Path(src)==p and not changed:
            changed=True;p.unlink();p.symlink_to(tmp_path/'missing-target')
        return real(src,dst,*a,**kw)
    with monkeypatch.context() as m:
        m.setattr(q.os,'replace',replace)
        with pytest.raises(q.RequestNotRegular):q._new_claim_batch(inflight,[p])
    report=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
    print('PROBE',json.dumps(dict(probe='postmove_dangling',quiescent=report.quiescent,refused=report.refused,remaining=[str(x.relative_to(inflight)) for x in inflight.rglob('*')],queued_link=p.is_symlink())))
    assert report.quiescent, 'rejected dangling alias caused its metadata to disappear and stranded an UNKNOWN batch'


def test_late_descriptor_delivery_is_explicitly_rejected(tmp_path,monkeypatch):
    import src.runtime.warm_materializer as w
    path=tmp_path/'test.lease';held=lease.acquire_all([path])
    sender,receiver=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
    rid='a'*32;seen=[];fds=[]
    monkeypatch.setenv(w.LEASE_SOCKET_ENV,str(receiver.fileno()))
    recv=socket.recv_fds
    def split(sock,bufsize,maxfds,*a,**kw):
        result=recv(sock,min(bufsize,8) if not seen else bufsize,maxfds,*a,**kw)
        seen.append((len(result[0]),len(result[1])))
        return result
    monkeypatch.setattr(socket,'recv_fds',split)
    monkeypatch.setattr(w.select,'select',lambda readers,*a:([readers[0]],[],[]))
    error=None;acks=[]
    try:
        sender.sendall(rid[:8].encode())
        socket.send_fds(sender,[rid[8:].encode()],[held[0].fd])
        try:fds=w.receive_leases({'request_id':rid,'lease_fds':1},acks.append)
        except RuntimeError as exc:error=str(exc)
        print('PROBE',json.dumps(dict(probe='late_rights',reads=seen,accepted=bool(fds),acks=acks,error=error)))
        assert error is not None, 'later-read descriptors are accepted despite the declared frame contract'
    finally:
        for fd in fds:os.close(fd)
        sender.close();receiver.close();lease.release(held)
