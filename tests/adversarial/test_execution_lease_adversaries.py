# Created: 2026-10-03
# Last reused/audited: 2026-10-03
# Authority basis: consult REQ-20261002-082408-568e9b (NO-GO on ca90f2ebc);
#   docs/operations/current/plans/canonical_execution_lease.md section 3.
"""Executed invariant probes for the canonical execution lease. No real DBs or trading effects.

Adapted from artifacts/merge_safety_claim_wait_review/test_adversarial_claims.py,
which targeted ca90f2ebc's API. Every assertion is unchanged. API seam changes:

1. ``_request`` and the ``three_slots`` fixture were imported from
   tests/test_materialization_priority_claim_throughput.py, which exists only
   on ca90f2ebc. They are inlined below verbatim from that commit (module
   alias ``queue`` renamed ``q``).
2. ``q._HELD_CLAIM_LOCKS`` (batch -> one owner-lock fd) is now
   ``q._HELD_CLAIM_LEASES`` (batch -> its identity leases); the cleanup fixture
   and the ``fd_still_retained`` report read the new registry.
3. ``claim.batch_path / q._CLAIM_OWNER_LOCK_NAME`` (one per-batch lock file) is
   now the claim's identity-lease file, ``q._claim_lease_paths(batch)[0]``, in
   unreadable_new_lock: it is the file whose mode the probe removes.
4. ``q._priority_slot_owner_observed`` returns deferral reasons or None instead
   of a bool; distinct_names only wraps it and passes the result through, so
   that probe needed no edit.
"""
import errno, json, os, signal, subprocess, sys, time
from pathlib import Path
import pytest
import src.data.replacement_forecast_live_materialization_queue as q


def _request(**overrides) -> dict[str, object]:
    return {
        "city": "London",
        "target_date": "2026-08-25",
        "temperature_metric": "high",
        "source_cycle_time": "2026-08-24T00:00:00+00:00",
        "computed_at": "2026-08-24T08:00:00+00:00",
        "baseline_source_run_id": "baseline-run",
        "openmeteo_source_run_id": "openmeteo-run",
        "openmeteo_payload_json": "payload.json",
        "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "30C"}],
        **overrides,
    }


@pytest.fixture
def three_slots(tmp_path, monkeypatch):
    """Held, global and expansion requests, interleaved exactly as live ranks them."""
    requests = tmp_path / "requests"
    requests.mkdir()
    files = {}
    for city in ("London", "Paris", "Hong Kong"):
        path = requests / f"{city.replace(' ', '_')}.2026-08-25.high.json"
        path.write_text(json.dumps(_request(city=city)), encoding="utf-8")
        files[city] = path
    revision = [1]
    monkeypatch.setattr(q, "_claim_db_fingerprint", lambda _db: revision[0])
    monkeypatch.setattr(q, "_current_money_risk_families",
                        lambda *_a, **_kw: frozenset({("London", "2026-08-25", "high")}))
    monkeypatch.setattr(q, "_current_global_auction_scope_families",
                        lambda *_a, **_kw: frozenset({("Paris", "2026-08-25", "high")}))

    def priority(_db, paths, _payloads, **_kwargs):
        # Expansion ranks first, so only the interleave puts held/global ahead.
        rank = {"Hong_Kong": -3, "London": -2, "Paris": -1}
        return ({p.name: (rank[p.name.split(".")[0]], p.name) for p in paths},
                {p.name for p in paths})

    monkeypatch.setattr(q, "_priority_map_with_names", priority)

    def plan():
        return q._build_request_claim_read_plan(
            request_path=requests, processed_path=tmp_path / "processed",
            failed_path=tmp_path / "failed", forecast_db=tmp_path / "forecasts.db",
            limit=3, lane=q.MATERIALIZATION_LANE_PRIORITY,
        )

    return requests, files, revision, plan


@pytest.fixture(autouse=True)
def close_review_claims():
    before=set(q._HELD_CLAIM_LEASES)
    yield
    for batch in set(q._HELD_CLAIM_LEASES)-before:
        q._release_claim_batch(Path(batch))

@pytest.mark.parametrize('fault', ['second_move', 'post_move_fsync'])
def test_partial_claim_failure_is_recoverable(three_slots, monkeypatch, tmp_path, fault):
    requests, files, rev, build=three_slots
    plan=build()
    original_replace=q.os.replace
    original_sync=q._fsync_directory
    with monkeypatch.context() as m:
        if fault=='second_move':
            def broken(src,dst):
                if Path(src)==files['Paris']:
                    raise OSError(errno.EIO, 'review injected second-slot I/O failure')
                return original_replace(src,dst)
            m.setattr(q.os,'replace',broken)
        else:
            def broken_sync(path):
                if Path(path).name.startswith('priority.'):
                    raise OSError(errno.EIO,'review injected claim-directory fsync failure')
                return original_sync(path)
            m.setattr(q,'_fsync_directory',broken_sync)
        with pytest.raises(OSError):
            q._try_claim_priority_request(plan)
    inflight=requests.parent/q.MATERIALIZATION_INFLIGHT_DIR_NAME
    batches=list(inflight.iterdir())
    stranded=sum(len(q._claim_request_files(b)) for b in batches)
    live=[q._claim_owner_alive(b) for b in batches]
    _,recovered,_=q._recover_stale_claims(request_path=requests,inflight_path=inflight)
    print('COUNTEREXAMPLE',json.dumps(dict(probe=fault,stranded=stranded,locks_held=live,recovered=recovered,processable_pending=len(list(requests.glob('*.json'))))))
    assert recovered==stranded, 'aborted constructor retains a held lock without a processing owner'


def test_dispatch_preserves_held_global_expansion_order(three_slots, monkeypatch, tmp_path):
    requests,files,rev,build=three_slots
    expected=[p.name for p in build().claim.selected_files]
    rank={'Hong_Kong':-3,'London':-2,'Paris':-1}
    monkeypatch.setattr(q,'_cycle_advance_seed_priority_map',lambda db,paths,*a,**kw:{p.name:(rank[p.name.split('.')[0]],p.name) for p in paths})
    observed=[]
    def transport(argv):
        paths=list(argv[argv.index('--batch-input-json')+1:argv.index('--deadline-utc')])
        observed.extend(Path(x).name for x in paths)
        return subprocess.CompletedProcess(argv,0,stdout='\n'.join(json.dumps(dict(input_json=x,returncode=0,stdout='',stderr='')) for x in paths),stderr='')
    monkeypatch.setattr(q,'_run_command',transport)
    report=q.process_replacement_forecast_live_materialization_queue(request_dir=requests,processed_dir=tmp_path/'processed',failed_dir=tmp_path/'failed',forecast_db=tmp_path/'absent.db',seed_limit=0,limit=3,lane=q.MATERIALIZATION_LANE_PRIORITY)
    print('COUNTEREXAMPLE',json.dumps(dict(probe='dispatch_order',planned=expected,dispatched=observed,status=report.status)))
    assert observed==expected, 'batch dispatch discarded held/global interleave'


def test_orphan_only_priority_tick_recovers_claims(three_slots, tmp_path):
    requests,files,rev,build=three_slots
    claim,_=q._try_claim_priority_request(build())
    q._release_claim_batch(claim.batch_path)
    report=q.process_replacement_forecast_live_materialization_queue(request_dir=requests,processed_dir=tmp_path/'processed',failed_dir=tmp_path/'failed',forecast_db=tmp_path/'absent.db',seed_limit=0,limit=3,lane=q.MATERIALIZATION_LANE_PRIORITY,runner=lambda argv:pytest.fail('recovery should not dispatch'))
    stranded=len(q._claim_request_files(claim.batch_path))
    print('COUNTEREXAMPLE',json.dumps(dict(probe='orphan_only',status=report.status,reasons=report.reason_codes,stranded=stranded)))
    assert stranded==0,'empty requests suppresses autonomous dead-owner recovery'


def test_unreadable_new_lock_is_not_a_legacy_lease(three_slots, tmp_path):
    requests,files,rev,build=three_slots
    claim,_=q._try_claim_priority_request(build())
    lock=q._claim_lease_paths(claim.batch_path)[0]
    meta=claim.batch_path/q._CLAIM_METADATA_NAME
    data=json.loads(meta.read_text());data['claimed_at']='2000-01-01T00:00:00+00:00';meta.write_text(json.dumps(data))
    held=q._claim_owner_alive(claim.batch_path)
    lock.chmod(0)
    try:
        unknown=q._claim_owner_alive(claim.batch_path)
        if unknown is not None:pytest.skip('test uid bypasses mode bits')
        _,recovered,_=q._recover_stale_claims(request_path=requests,inflight_path=requests.parent/q.MATERIALIZATION_INFLIGHT_DIR_NAME)
        print('COUNTEREXAMPLE',json.dumps(dict(probe='unreadable_lock',known_held_before=held,unknown_after=unknown,recovered=recovered,fd_still_retained=str(claim.batch_path) in q._HELD_CLAIM_LEASES)))
        assert recovered==0,'unknown lock state falls back to age and steals a live new-format batch'
    finally:
        if lock.exists():lock.chmod(0o644)


def _await_file(path, process=None):
    end=time.monotonic()+15
    while not path.exists():
        if process is not None and process.poll() is not None:
            raise AssertionError('probe parent exited: '+str(process.returncode))
        if time.monotonic()>end:raise AssertionError('probe synchronization timeout')
        time.sleep(.01)
    return json.loads(path.read_text())


def test_parent_death_does_not_reclaim_live_resident_work(tmp_path):
    root=Path.cwd().resolve(); requests=tmp_path/'requests';requests.mkdir()
    request=requests/'London.json';request.write_text(json.dumps(_request()))
    # Keep the real resident protocol, Popen and queue _run_command. Replace only
    # the expensive compute body with a signal-controlled in-memory computation.
    wrapper=tmp_path/'resident_probe.py'
    wrapper.write_text("import importlib.util,json,os,signal,sys\nfrom pathlib import Path\n"
        +f"sys.path.insert(0,{str(root)!r})\n"
        +f"spec=importlib.util.spec_from_file_location('review_actual_materializer',{str(root/'scripts/materialize_replacement_forecast_live.py')!r})\n"
        +"module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)\n"
        +"def compute(argv):\n"
        +" signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGUSR1})\n"
        +" p=Path(argv[argv.index('--input-json')+1]);body=p.read_bytes()\n"
        +f" Path({str(tmp_path/'worker_ready.json')!r}).write_text(json.dumps(dict(pid=os.getpid(),read_bytes=len(body))))\n"
        +" signal.sigwait({signal.SIGUSR1})\n"
        +f" Path({str(tmp_path/'worker_resumed.json')!r}).write_text(json.dumps(dict(pid=os.getpid(),retained_bytes=len(body))))\n"
        +" return 0\nmodule.main=compute\nmodule._resident_worker()\n")
    parent_code=f"""import json,os,sys
from pathlib import Path
sys.path.insert(0,{str(root)!r})
import src.data.replacement_forecast_live_materialization_queue as q
import src.runtime.warm_materializer as w
w._SCRIPT=Path({str(wrapper)!r})
b=q._new_claim_batch(Path({str(tmp_path/'inflight')!r}),(Path({str(request)!r}),))
Path({str(tmp_path/'parent_ready.json')!r}).write_text(json.dumps(dict(batch=str(b),pid=os.getpid())))
q._run_command((sys.executable,str(w._SCRIPT),'--input-json',str(b/'London.json'),'--commit'))
"""
    log=(tmp_path/'parent.log').open('w')
    parent=subprocess.Popen([sys.executable,'-c',parent_code],stdout=log,stderr=log,env=dict(os.environ))
    child_pid=None
    try:
        info=_await_file(tmp_path/'parent_ready.json',parent)
        child=_await_file(tmp_path/'worker_ready.json',parent);child_pid=child['pid']
        batch=Path(info['batch']);held_before=q._claim_owner_alive(batch)
        parent.kill();parent.wait(timeout=5)
        os.kill(child_pid,0) # exact child is still alive after its parent died
        held_after=q._claim_owner_alive(batch)
        _,recovered,_=q._recover_stale_claims(request_path=requests,inflight_path=tmp_path/'inflight')
        second=q._new_claim_batch(tmp_path/'inflight',(request,)) if recovered else None
        os.kill(child_pid,signal.SIGUSR1)
        resumed=_await_file(tmp_path/'worker_resumed.json')
        print('COUNTEREXAMPLE',json.dumps(dict(probe='parent_death',parent_returncode=parent.returncode,worker_pid=child_pid,held_before=held_before,held_after=held_after,recovered=recovered,second_owner=second is not None,old_worker_resumed=resumed['retained_bytes']>0)))
        assert recovered==0,'live resident work became reclaimable when only the parent died'
    finally:
        if parent.poll() is None:parent.kill();parent.wait(timeout=5)
        if child_pid:
            try:os.kill(child_pid,signal.SIGKILL)
            except ProcessLookupError:pass
        log.close()


def test_repaired_owner_envelope_is_not_suppressed(tmp_path, monkeypatch):
    from tests.test_cycle_monotone_materialization import _licensed_worker_queue
    from src.data.station_ground_evidence import forecast_db_from_connection
    gen,conn,consume,fenced,worker,responses,queue=_licensed_worker_queue(tmp_path,monkeypatch)
    try:
        root=tmp_path/'queue';path=root/'requests'/'Shanghai.current.json'
        good=json.loads(path.read_text());bad=dict(good,day0_enqueue_owner_witness={})
        path.write_text(json.dumps(bad));db=forecast_db_from_connection(conn)
        seen=[]
        def runner(argv):
            f=Path(argv[argv.index('--input-json')+1]);code,out,err=worker(f)
            seen.append(json.loads((out or err).strip().splitlines()[-1]))
            return subprocess.CompletedProcess(argv,code,out,err)
        report=q._process_claimed_materialization_batch(request_path=path.parent,processed_path=root/'processed',failed_path=root/'failed',forecast_db=db,limit=1,runner=runner,marker_dir=root/'blocked_attempts')
        assert len(seen)==1 and seen[0]['error_type']=='RequestInputInvalid',seen
        assert 'day0_enqueue_owner_witness missing' in seen[0]['error'],seen
        path.write_text(json.dumps(good))
        code,out,err=worker(path)
        assert code==0,(out,err)
        repaired_response=json.loads((out or err).strip().splitlines()[-1])
        suppressed=q._blocked_attempt_state(marker_dir=root/'blocked_attempts',input_json=path,payload=good,forecast_db=db)[2]
        print('COUNTEREXAMPLE',json.dumps(dict(probe='owner_repair_collision',old_error=seen[0]['error'],old_category=seen[0]['failure_category'],repaired_worker_status=repaired_response['status'],repaired_worker_code=code,repaired_marker_suppressed=suppressed)))
        assert not suppressed,'a parse verdict on excluded owner fields suppresses repaired valid inputs'
    finally:next(gen,None)


def test_distinct_names_cannot_acquire_same_identity(tmp_path, monkeypatch):
    requests=tmp_path/'requests';requests.mkdir()
    old=requests/'London.old.json';old.write_text(json.dumps(_request()))
    monkeypatch.setattr(q,'_claim_db_fingerprint',lambda db:1)
    monkeypatch.setattr(q,'_current_money_risk_families',lambda *a,**k:frozenset())
    monkeypatch.setattr(q,'_current_global_auction_scope_families',lambda *a,**k:frozenset())
    monkeypatch.setattr(q,'_priority_map_with_names',lambda db,files,payloads,**kw:({p.name:(-1,p.name) for p in files},{p.name for p in files}))
    def build():return q._build_request_claim_read_plan(request_path=requests,processed_path=tmp_path/'processed',failed_path=tmp_path/'failed',forecast_db=None,limit=1,lane=q.MATERIALIZATION_LANE_PRIORITY)
    plan=build();original=q._priority_slot_owner_observed;claims=[];injected=False
    def interleaved(*a,**kw):
        nonlocal injected
        observed=original(*a,**kw)
        if not injected:
            injected=True
            newer=requests/'London.new.json'
            newer.write_text(json.dumps(dict(_request(),computed_at='2026-08-24T09:00:00+00:00')))
            second,reasons=q._try_claim_priority_request(build())
            assert second is not None,reasons
            claims.append(second)
        return observed
    monkeypatch.setattr(q,'_priority_slot_owner_observed',interleaved)
    first,reasons=q._try_claim_priority_request(plan)
    if first is not None:claims.append(first)
    ids=[q._request_semantic_key(q._load_request_payload_for_coalescing(c.selected_files[0])) for c in claims]
    print('COUNTEREXAMPLE',json.dumps(dict(probe='identity_race',live_batches=len(claims),distinct_semantic_identities=len(set(ids)),locks_held=[q._claim_owner_alive(c.batch_path) for c in claims])))
    assert len(claims)==1,'filename rename is not atomic identity acquisition'
