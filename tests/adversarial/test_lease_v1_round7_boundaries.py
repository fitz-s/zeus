# Created: 2026-10-04
# Last reused/audited: 2026-10-09
# Purpose: Exercise real capture leases, crash recovery and unreadable-evidence isolation.
# Reuse: Preserve unreadable-capture UNKNOWN isolation, healthy work and normal reread RESET.
# Authority basis: lease-v1 round-7 consult, strengthened for queue isolation from
#   artifacts/merge_safety_lease_v1_round7_review/test_round7_boundaries.py. Run from the checkout root.
"""Independent round-7 crash, UNKNOWN isolation and metadata-layout probes.
Temporary queues and owned child processes only; no live DB or venue writes.
Run from the pinned checkout; the repository conftest supplies isolation.
"""
from __future__ import annotations
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone
import pytest
import src.data.replacement_forecast_live_materialization_queue as q
from tests.adversarial.test_execution_lease_adversaries import _request

ROOT = Path.cwd().resolve()

@pytest.fixture(autouse=True)
def cleanup_claims():
    before=set(q._HELD_CLAIM_LEASES)
    yield
    for batch in set(q._HELD_CLAIM_LEASES)-before:
        q._release_claim_batch(Path(batch))


def capture_fixture(tmp_path, *, regular=False, name='bad.json'):
    requests=tmp_path/'requests'; requests.mkdir(exist_ok=True)
    capture=tmp_path/q._REQUEST_ALIAS_DIR/'.capture.test'; capture.mkdir(parents=True)
    payload=capture/q._CAPTURE_PAYLOAD_DIR; payload.mkdir()
    target=tmp_path/'target'; target.write_bytes(b'NEVER READ OR WRITE')
    entry=payload/name
    if regular:q._write_request(entry,_request())
    else:entry.symlink_to(target)
    return requests,capture,entry,target


def public_background(requests, *, runner=None):
    root=requests.parent
    (root/'seeds').mkdir(exist_ok=True)
    return q.process_replacement_forecast_live_materialization_queue(
        request_dir=requests,processed_dir=root/'processed',failed_dir=root/'failed',
        seed_dir=root/'seeds',seed_processed_dir=root/'seed_processed',seed_failed_dir=root/'seed_failed',
        forecast_db=None,seed_limit=1,limit=1,discover=False,lane=q.MATERIALIZATION_LANE_BACKGROUND,
        runner=runner or (lambda argv:subprocess.CompletedProcess(argv,0,'','')))


def healthy_worker_case(requests):
    """A fresh queue request and explicit fake worker; no posterior is invented."""
    now = datetime.now(timezone.utc)
    payload = _request(
        target_date=(now + timedelta(days=1)).date().isoformat(),
        source_cycle_time=now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
        computed_at=now.isoformat(),
        expires_at=(now + timedelta(hours=6)).isoformat(),
    )
    healthy = requests / 'London.json'
    q._write_request(healthy, payload)
    calls = []

    def worker(argv):
        path = Path(argv[argv.index('--input-json') + 1])
        calls.append(json.loads(path.read_text()))
        return subprocess.CompletedProcess(argv, 0, '', '')

    return healthy, payload, calls, worker


def assert_healthy_completed(reports, healthy, payload, calls):
    assert calls == [payload], 'fresh healthy request must reach the worker exactly once'
    assert sum(report.started_count for report in reports) == 1
    assert sum(report.completed_count for report in reports) == 1
    assert sum(report.processed_count for report in reports) == 1
    assert sum(report.failed_count for report in reports) == 0
    assert sum(report.committed_posterior_count for report in reports) == 0
    assert not healthy.exists()
    receipts = [Path(path) for report in reports for path in report.processed_files]
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt['status'] == 'SUCCEEDED'
    assert receipt['computed_at'] == payload['computed_at']
    assert receipt['result_evidence']['committed_posterior'] is False


@pytest.mark.parametrize('cut',[
    'stage_mkdir','stage_open_empty','stage_partial','stage_complete_unflushed',
    'stage_fsync','before_rename','after_rename','after_stage_rmdir','after_capture_fsync',
    'old_final_unlinked','staged_receipt_unlinked',
])
def test_receipt_crash_points_recover(tmp_path,cut):
    requests,capture,entry,target=capture_fixture(tmp_path)
    # Two cuts exercise repair of old torn state, not just new publication.
    if cut=='old_final_unlinked':(capture/q._ALIAS_RECEIPT_NAME).write_bytes(b'{"status":')
    if cut=='staged_receipt_unlinked':
        (capture/q._RECEIPT_STAGING_DIR).mkdir()
        (capture/q._RECEIPT_STAGING_DIR/q._ALIAS_RECEIPT_NAME).write_bytes(b'partial')
    child_code='''import builtins,fcntl,json,os,sys
from pathlib import Path
sys.path.insert(0,ROOT)
import src.data.replacement_forecast_live_materialization_queue as q
c=Path(CAPTURE); s=c/q._RECEIPT_STAGING_DIR; final=c/q._ALIAS_RECEIPT_NAME
fd=os.open(c,os.O_RDONLY);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
real_mkdir=Path.mkdir;real_open=builtins.open;real_dump=json.dump;real_sync=os.fsync
real_rename=os.rename;real_rmdir=Path.rmdir;real_unlink=Path.unlink;real_dirsync=q._fsync_directory
def die():os._exit(73)
def mkdir(p,*a,**kw):
    r=real_mkdir(p,*a,**kw)
    if p==s and CUT=='stage_mkdir':die()
    return r
def op(p,*a,**kw):
    h=real_open(p,*a,**kw)
    if Path(p)==s/q._ALIAS_RECEIPT_NAME and CUT=='stage_open_empty':die()
    return h
def dump(body,h,*a,**kw):
    if Path(h.name)==s/q._ALIAS_RECEIPT_NAME:
        if CUT=='stage_partial':h.write('{"status":');h.flush();real_sync(h.fileno());die()
        if CUT=='stage_complete_unflushed':real_dump(body,h,*a,**kw);die()
    return real_dump(body,h,*a,**kw)
def sync(n):
    r=real_sync(n)
    if CUT=='stage_fsync' and (s/q._ALIAS_RECEIPT_NAME).exists() and os.path.samestat(os.fstat(n),(s/q._ALIAS_RECEIPT_NAME).stat()):die()
    return r
def rename(a,b,*args,**kw):
    receipt=Path(a)==s/q._ALIAS_RECEIPT_NAME
    if receipt and CUT=='before_rename':die()
    r=real_rename(a,b,*args,**kw)
    if receipt and CUT=='after_rename':die()
    return r
def rmdir(p,*a,**kw):
    r=real_rmdir(p,*a,**kw)
    if p==s and CUT=='after_stage_rmdir':die()
    return r
def unlink(p,*a,**kw):
    existed=p.exists();r=real_unlink(p,*a,**kw)
    if existed and ((p==final and CUT=='old_final_unlinked') or (p==s/q._ALIAS_RECEIPT_NAME and CUT=='staged_receipt_unlinked')):die()
    return r
def dirsync(p):
    r=real_dirsync(p)
    if Path(p)==c and CUT=='after_capture_fsync':die()
    return r
Path.mkdir=mkdir;builtins.open=op;json.dump=dump;os.fsync=sync;os.rename=rename;Path.rmdir=rmdir;Path.unlink=unlink;q._fsync_directory=dirsync
q._settle_capture(c,Path(REQUESTS))
raise AssertionError('crash boundary not reached')
'''
    prefix=f'ROOT={str(ROOT)!r};CAPTURE={str(capture)!r};REQUESTS={str(requests)!r};CUT={cut!r}\n'
    child=subprocess.run([sys.executable,'-B','-c',prefix+child_code],capture_output=True,text=True,timeout=20)
    assert child.returncode==73,child.stderr
    before=(capture/q._ALIAS_RECEIPT_NAME).read_bytes() if (capture/q._ALIAS_RECEIPT_NAME).exists() else None
    states=[q._settle_free_capture(capture,requests) for _ in range(2)]
    report=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
    assert states==['settled','settled'] and report.quiescent
    assert q._capture_settled(capture) and entry.is_symlink()
    assert target.read_bytes()==b'NEVER READ OR WRITE'
    assert not list(requests.glob('*.json')) and not (tmp_path/'blocked_attempts').exists()
    if cut in ('after_rename','after_stage_rmdir','after_capture_fsync'):
        assert (capture/q._ALIAS_RECEIPT_NAME).read_bytes()==before, 'valid final receipt was rewritten'
    print('PROBE',json.dumps({'cut':cut,'states':states,'quiescent':report.quiescent,'empty_stage_remaining':(capture/q._RECEIPT_STAGING_DIR).exists()}))


@pytest.mark.parametrize('final_body',[b'',b'{"status":',b'{}',b'[]',b'\xff'])
def test_readable_invalid_final_is_rebuilt(tmp_path,final_body):
    requests,capture,entry,target=capture_fixture(tmp_path)
    (capture/q._ALIAS_RECEIPT_NAME).write_bytes(final_body)
    assert q._settle_free_capture(capture,requests)=='settled'
    assert q._capture_settled(capture) and target.read_bytes()==b'NEVER READ OR WRITE'


@pytest.mark.parametrize('kind',['permission','symlink','fifo','io_error'])
def test_unreadable_final_is_isolated_and_not_deleted(tmp_path,monkeypatch,kind):
    requests,capture,entry,target=capture_fixture(tmp_path)
    control=capture/q._ALIAS_RECEIPT_NAME
    if kind=='symlink':control.symlink_to(target)
    elif kind=='fifo':os.mkfifo(control)
    else:control.write_bytes(b'{"status":"QUARANTINED_REQUEST_ALIAS","request_name":"bad.json"}')
    before=control.lstat(); real=q.read_regular_request
    if kind=='permission':
        control.chmod(0)
        if os.access(control,os.R_OK):pytest.skip('uid bypasses permission bits')
    if kind=='io_error':
        def fail(p):
            if Path(p)==control:raise OSError(errno.EIO,'injected receipt read error')
            return real(p)
        monkeypatch.setattr(q,'read_regular_request',fail)
    healthy,payload,calls,worker=healthy_worker_case(requests)
    try:
        reports=[public_background(requests,runner=worker) for _ in range(2)]
        dry=q.reconcile_inflight_for_migration(request_path=requests,apply=False)
        result=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
        assert dry.unsettled_captures == result.unsettled_captures
        assert (capture.name,'unknown') in result.unsettled_captures and not result.quiescent
        assert os.path.samestat(before,control.lstat())
        assert_healthy_completed(reports,healthy,payload,calls)
        assert target.read_bytes()==b'NEVER READ OR WRITE'
        print('PROBE',json.dumps({'kind':kind,'unknown_isolated':True,'statuses':[r.status for r in reports],'unsettled':result.unsettled_captures}))
    finally:
        if kind=='permission':control.chmod(0o600)


def test_unreadable_capture_directory_does_not_poison_queue(tmp_path):
    requests,capture,entry,target=capture_fixture(tmp_path)
    healthy,payload,calls,worker=healthy_worker_case(requests)
    capture_before,entry_before=capture.lstat(),entry.lstat()
    capture.chmod(0)
    if os.access(capture,os.R_OK):
        capture.chmod(0o700);pytest.skip('uid bypasses permission bits')
    try:
        reports=[public_background(requests,runner=worker) for _ in range(2)]
        dry=q.reconcile_inflight_for_migration(request_path=requests,apply=False)
        report=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
        assert dry.unsettled_captures == report.unsettled_captures == ((capture.name,'unknown'),)
        assert not dry.quiescent and not report.quiescent
        assert not q._capture_settled(capture)
        assert os.path.samestat(capture_before,capture.lstat())
        assert capture.lstat().st_mode & 0o777 == 0
        assert target.read_bytes()==b'NEVER READ OR WRITE'
        assert_healthy_completed(reports,healthy,payload,calls)
    finally:capture.chmod(0o700)
    # Only the fixture restores access. Normal recovery rereads the same entry.
    dry=q.reconcile_inflight_for_migration(request_path=requests,apply=False)
    assert dry.quiescent and dry.settled_captures == 0
    assert not q._capture_settled(capture)
    public_background(requests,runner=worker)
    assert q._capture_settled(capture)
    assert os.path.samestat(entry_before,entry.lstat()) and entry.is_symlink()
    assert target.read_bytes()==b'NEVER READ OR WRITE'
    assert q.reconcile_inflight_for_migration(request_path=requests,apply=True).quiescent
    assert calls == [payload]


def test_unreadable_receipt_normal_reread_resets_after_permission_restore(tmp_path):
    requests,capture,entry,target=capture_fixture(tmp_path)
    assert q._settle_free_capture(capture,requests) == 'settled'
    control=capture/q._ALIAS_RECEIPT_NAME
    original=control.read_bytes(); identity=control.lstat(); entry_identity=entry.lstat()
    control.chmod(0)
    try:
        if os.access(control,os.R_OK):pytest.skip('uid bypasses permission bits')
        for apply in (False,True):
            report=q.reconcile_inflight_for_migration(request_path=requests,apply=apply)
            assert report.unsettled_captures == ((capture.name,'unknown'),)
            assert not report.quiescent and not q._capture_settled(capture)
        assert os.path.samestat(identity,control.lstat())
    finally:control.chmod(0o600)
    assert q._capture_settled(capture)
    for apply in (False,True):
        assert q.reconcile_inflight_for_migration(request_path=requests,apply=apply).quiescent
    assert os.path.samestat(identity,control.lstat()) and control.read_bytes()==original
    assert os.path.samestat(entry_identity,entry.lstat()) and entry.is_symlink()
    assert target.read_bytes()==b'NEVER READ OR WRITE'


@pytest.mark.parametrize('kind', ['symlink', 'fifo'])
def test_unsearchable_entry_metadata_cannot_prove_terminal_receipt(tmp_path, kind):
    requests,capture,entry,target=capture_fixture(tmp_path)
    if kind == 'fifo':
        entry.unlink()
        os.mkfifo(entry)
    assert q._settle_free_capture(capture,requests) == 'settled'
    control=capture/q._ALIAS_RECEIPT_NAME
    original=control.read_bytes(); control_identity=control.lstat(); entry_identity=entry.lstat()
    healthy,payload,calls,worker=healthy_worker_case(requests)
    directory=entry.parent
    directory.chmod(0o400)
    try:
        if os.access(directory,os.X_OK):pytest.skip('uid bypasses permission bits')
        assert os.listdir(directory) == [entry.name]
        with pytest.raises(PermissionError):os.lstat(entry)
        assert not q._capture_settled(capture), 'unreadable metadata is not proof of a nonregular entry'
        reports=[public_background(requests,runner=worker) for _ in range(2)]
        for apply in (False,True):
            report=q.reconcile_inflight_for_migration(request_path=requests,apply=apply)
            assert report.unsettled_captures == ((capture.name,'unknown'),)
            assert not report.quiescent and report.settled_captures == 0
        assert directory.lstat().st_mode & 0o777 == 0o400
        assert os.path.samestat(control_identity,control.lstat()) and control.read_bytes()==original
        assert_healthy_completed(reports,healthy,payload,calls)
    finally:directory.chmod(0o700)
    # Readability is restored only by the fixture; normal reread proves the same alias.
    public_background(requests,runner=worker)
    assert q._capture_settled(capture)
    for apply in (False,True):
        assert q.reconcile_inflight_for_migration(request_path=requests,apply=apply).quiescent
    assert os.path.samestat(control_identity,control.lstat()) and control.read_bytes()==original
    assert os.path.samestat(entry_identity,entry.lstat())
    assert target.read_bytes()==b'NEVER READ OR WRITE' and calls == [payload]


@pytest.mark.parametrize('layout',['new','old_lease_v1','legacy'])
def test_metadata_layout_recovery(tmp_path,layout):
    requests=tmp_path/'requests';requests.mkdir()
    p=requests/'London.json';q._write_request(p,_request())
    batch=q._new_claim_batch(tmp_path/'inflight',[p]);q._release_claim_batch(batch)
    control=batch/q._CLAIM_METADATA_NAME
    if layout!='new':control.rename(batch/q._LEGACY_CLAIM_METADATA_NAME)
    if layout=='legacy':
        old=batch/ q._LEGACY_CLAIM_METADATA_NAME
        data=json.loads(old.read_text());data.pop('protocol');data['owner_pid']=99999999
        old.write_text(json.dumps(data));dest=batch.with_name('old.pid99999999');batch.rename(dest);batch=dest
    expected='claim.control' if layout=='new' else '_claim.json'
    assert q._claim_metadata_path(batch).name==expected
    report=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
    assert report.quiescent and q._load_request_payload_for_coalescing(p)==_request()
    assert not batch.exists()


def test_current_metadata_precedence_keeps_legacy_basename_as_payload(tmp_path):
    requests=tmp_path/'requests';requests.mkdir()
    p=requests/'_claim.json';q._write_request(p,_request())
    batch=q._new_claim_batch(tmp_path/'inflight',[p]);claimed=batch/p.name
    assert q._claim_metadata_path(batch).name=='claim.control'
    assert claimed in q._captured_entries(batch)
    assert q.claim_record_sha256(claimed)==hashlib.sha256(claimed.read_bytes()).hexdigest()
    assert q.claim_required_lease_paths(claimed)
    q._release_claim_batch(batch)
    report=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
    assert report.quiescent and q._load_request_payload_for_coalescing(p)==_request()


@pytest.mark.parametrize('control_kind',['malformed','unreadable','dangling'])
def test_current_control_never_falls_back_to_valid_legacy_metadata(tmp_path,control_kind):
    requests=tmp_path/'requests';requests.mkdir()
    p=requests/'London.json';q._write_request(p,_request())
    batch=q._new_claim_batch(tmp_path/'inflight',[p]);q._release_claim_batch(batch)
    current=batch/q._CLAIM_METADATA_NAME
    (batch/q._LEGACY_CLAIM_METADATA_NAME).write_bytes(current.read_bytes())
    if control_kind=='malformed':current.write_bytes(b'{')
    elif control_kind=='dangling':current.unlink();current.symlink_to(tmp_path/'missing')
    else:
        current.chmod(0)
        if os.access(current,os.R_OK):current.chmod(0o600);pytest.skip('uid bypasses permission bits')
    try:
        assert q._claim_metadata_path(batch)==current
        report=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
        assert not report.quiescent and dict(report.refused)[batch.name]=='UNKNOWN'
        assert (batch/p.name).exists()
    finally:
        if control_kind=='unreadable':current.chmod(0o600)


def test_mixed_old_new_and_held_batch_reconcile(tmp_path):
    requests=tmp_path/'requests';requests.mkdir();inflight=tmp_path/'inflight'
    batches=[]
    for i in range(3):
        p=requests/f'city{i}.json';q._write_request(p,_request(city=f'City{i}'))
        b=q._new_claim_batch(inflight,[p]);batches.append((b,p))
        if i<2:q._release_claim_batch(b)
    (batches[0][0]/q._CLAIM_METADATA_NAME).rename(batches[0][0]/q._LEGACY_CLAIM_METADATA_NAME)
    first=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
    assert not first.quiescent and dict(first.refused)[batches[2][0].name]=='HELD'
    assert all(p.exists() for _,p in batches[:2])
    q._release_claim_batch(batches[2][0])
    second=q.reconcile_inflight_for_migration(request_path=requests,apply=True)
    assert second.quiescent and all(p.exists() for _,p in batches)
