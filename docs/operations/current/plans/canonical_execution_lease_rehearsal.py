# Created: 2026-10-03
# Last reused/audited: 2026-10-04
# Authority basis: canonical_execution_lease_migration.md (rehearsal evidence).
"""Migration rehearsal for the lease-v1 protocol on a clone of the live queue.

Usage (from any directory; writes only under --scratch):
  python canonical_execution_lease_rehearsal.py run --old-root OLD --new-root NEW \
      --source /Users/leofitz/zeus/state/replacement_forecast_live \
      --scratch DIR --out RESULTS.json

The queue directory is cloned (APFS ``cp -c``). Consumers run the real queue
tick (plan, claim, dispatch) of OLD or NEW through the real ResidentMaterializer
process, descriptor transport and resident loop; only the worker's compute body
is a logged stub (no forecast DB is opened). Phases:

  A   OLD consumer; its worker blocks mid-request; the consumer is SIGKILLed.
  A2  migration steps 2-3: reap the worker, reconcile (NEW code).
  S   a NEW constructor is stopped after writing staging metadata and before
      publishing it: reconcile and lease recovery must leave it alone while it
      lives; after SIGKILL they must drain it, and its request stays queued.
  B   NEW consumer B's worker blocks holding the lease; consumer C competes;
      B's parent is SIGKILLed; reconcile must refuse the held batch.
  R   rollback steps: stop C, reap B's worker, reconcile.
  D   drain the queue with NEW code.

Results: per-identity execution spans (a reaped worker's span ends at its
kill), concurrent duplicate executions, identities completed more than once,
stranded requests/batches/leases/staging, and every reconcile exit code.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()


# --- consumer: one queue process with a real resident worker -----------------


def consumer(code_root: Path, queue_root: Path, ticks: int, log: Path, block_tick: int) -> None:
    sys.path.insert(0, str(code_root))
    os.chdir(code_root)
    import src.data.replacement_forecast_live_materialization_queue as q
    import src.runtime.warm_materializer as w

    wrapper = queue_root / f".rehearsal_worker_{os.getpid()}.py"
    block_flag = queue_root / ".rehearsal_block_next"
    wrapper.write_text(f'''import importlib.util,json,os,signal,sys,time
from pathlib import Path
sys.path.insert(0,{str(code_root)!r})
spec=importlib.util.spec_from_file_location("rehearsal_materializer",{str(code_root / "scripts/materialize_replacement_forecast_live.py")!r})
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
import hashlib
import src.data.replacement_forecast_live_materialization_queue as rq
import uuid
def log(event,name,key=None,sha=None,invocation=None):
    with open({str(log)!r},"a") as f:f.write(json.dumps(dict(event=event,name=name,key=key,sha256=sha,invocation=invocation,pid=os.getpid(),t=time.time()))+"\\n")
def compute(argv):
    names=argv[argv.index("--batch-input-json")+1:argv.index("--deadline-utc")]
    block=Path({str(block_flag)!r})
    for n in names:
        # The worker's own request reader: the bytes it would compute from.
        try:
            raw=module._ConsumedInputs("rehearsal").read(Path(n),role=module.REQUEST_ROLE)
        except Exception:
            raw=Path(n).read_bytes()  # the pre-lease code path (phase A)
        key=rq._request_semantic_key(json.loads(raw))
        invocation=uuid.uuid4().hex
        log("start",Path(n).name,key,hashlib.sha256(raw).hexdigest(),invocation)
        if block.exists():
            block.unlink()
            signal.pthread_sigmask(signal.SIG_BLOCK,{{signal.SIGUSR1}})
            Path({str(queue_root / ".rehearsal_worker_ready.json")!r}).write_text(json.dumps(dict(pid=os.getpid(),name=Path(n).name)))
            signal.sigwait({{signal.SIGUSR1}})
        log("end",Path(n).name,key,hashlib.sha256(raw).hexdigest(),invocation)
        print(json.dumps(dict(input_json=n,returncode=0,stdout=json.dumps(dict(status="SUCCEEDED")),stderr="")),flush=True)
    return 0
module.main=compute
module._resident_worker()
''')
    w._SCRIPT = wrapper
    real_run = q._run_command
    q._run_command = lambda argv: real_run((argv[0], str(wrapper), *argv[2:]))
    q._materialization_subprocess_timeout_seconds = lambda: 3600.0
    requests = queue_root / "requests"
    idle = 0
    for tick in range(ticks):
        if tick == block_tick:
            block_flag.write_text("1")
        lane = q.MATERIALIZATION_LANE_PRIORITY if tick % 2 == 0 else q.MATERIALIZATION_LANE_ALL
        report = q.process_replacement_forecast_live_materialization_queue(
            request_dir=requests, processed_dir=queue_root / "processed",
            failed_dir=queue_root / "failed", forecast_db=None,
            seed_limit=0, limit=3, discover=False, lane=lane,
        )
        print(json.dumps(dict(tick=tick, lane=lane, status=report.status,
                              processed=report.processed_count, failed=report.failed_count)),
              flush=True)
        if not any(requests.glob("*.json")):
            break
        idle = 0 if (report.processed_count or report.failed_count) else idle + 1
        if idle >= 6:
            print(json.dumps(dict(tick=tick, stopped="no progress in 6 ticks",
                                  remaining=sorted(p.name for p in requests.glob("*.json")))),
                  flush=True)
            break


# --- staging constructor stopped before publication ---------------------------


def stage(code_root: Path, queue_root: Path, ready: Path) -> None:
    sys.path.insert(0, str(code_root))
    os.chdir(code_root)
    import src.data.replacement_forecast_live_materialization_queue as q

    write = q._write_lease_claim_metadata

    def write_then_stop(directory, *args, **kwargs):
        write(directory, *args, **kwargs)
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
        ready.write_text(json.dumps(dict(pid=os.getpid(), staging=str(directory))))
        signal.sigwait({signal.SIGUSR1})  # SIGKILL ends it here, unpublished

    q._write_lease_claim_metadata = write_then_stop
    request = sorted((queue_root / "requests").glob("*.json"))[0]
    q._new_claim_batch(queue_root / "inflight", (request,))


def recover_lease_only(code_root: Path, queue_root: Path) -> None:
    sys.path.insert(0, str(code_root))
    os.chdir(code_root)
    import src.data.replacement_forecast_live_materialization_queue as q
    from src.ingest.forecast_live_daemon import _replacement_forecast_inflight_pending

    _keys, recovered, _unknown = q._recover_stale_claims(
        request_path=queue_root / "requests", inflight_path=queue_root / "inflight",
        lease_only=True,
    )
    print(json.dumps(dict(
        recovered=recovered,
        discovery_sees_pending_inflight=_replacement_forecast_inflight_pending(
            {"request_dir": str(queue_root / "requests")}),
    )))


# --- orchestrator --------------------------------------------------------------


def run(args) -> None:
    py = sys.executable
    env = {**os.environ, "NO_PROXY": "*", "no_proxy": "*"}
    scratch = Path(args.scratch).resolve()
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    queue = scratch / "replacement_forecast_live"
    subprocess.run(["cp", "-cR", str(args.source), str(queue)], check=True)
    log = scratch / "exec.jsonl"
    old, new = Path(args.old_root).resolve(), Path(args.new_root).resolve()
    sys.path.insert(0, str(new))  # tree() classifies batches with NEW code
    report: dict[str, object] = {"old_root": str(old), "new_root": str(new)}
    reaped: dict[int, float] = {}

    def spawn(*argv):
        return subprocess.Popen([py, str(HERE), *map(str, argv)], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env=env)

    def wait_file(path: Path, proc, limit=300.0):
        end = time.monotonic() + limit
        while not path.exists():
            if proc.poll() is not None:
                raise SystemExit(f"process exited early: {proc.stdout.read()[-3000:]}")
            if time.monotonic() > end:
                raise SystemExit(f"rehearsal sync timeout on {path}")
            time.sleep(0.05)
        data = json.loads(path.read_text())
        path.unlink()
        return data

    def alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def reap(pid: int) -> None:
        os.kill(pid, signal.SIGKILL)
        while alive(pid):
            time.sleep(0.02)
        reaped[pid] = time.time()

    def reconcile(apply: bool) -> dict:
        out = subprocess.run([py, str(new / "scripts/reconcile_materialization_inflight.py"),
                              "--request-dir", str(queue / "requests"), *(["--apply"] if apply else [])],
                             capture_output=True, text=True, env=env, cwd=new)
        body = json.loads(out.stdout)
        return {"exit": out.returncode, "restored": len(body["restored"]),
                "refused": body["refused"], "held_leases": body["held_leases"],
                "live_staging": body["live_staging"], "drained_staging": body["drained_staging"]}

    def tree() -> dict:
        # Batch layout (which name is metadata) is the queue's to decide.
        import src.data.replacement_forecast_live_materialization_queue as q

        root = queue / "inflight"
        leases = root / "leases"
        batches = [p for p in root.iterdir() if p.is_dir() and p != leases] if root.exists() else []
        return {
            "requests": len(list((queue / "requests").glob("*.json"))),
            "inflight_requests": sorted(b.name + "/" + p.name for b in batches
                                        for p in q._captured_entries(b)),
            "staging": sorted(p.name for p in root.glob(".staging.*")) if root.exists() else [],
            "lease_files": len(list(leases.glob("*.lease"))) if leases.exists() else 0,
            "captures": sorted(p.name for p in (queue / "quarantined_request_aliases").glob(".capture.*"))
            if (queue / "quarantined_request_aliases").exists() else [],
        }

    # The clone copies the live daemon's LEGACY batches, owned by the running
    # production daemon on the real queue. Migration step 1 stops that daemon;
    # model it by rebinding each cloned legacy batch to a verifiably dead pid.
    gone = subprocess.Popen([py, "-c", "pass"])
    gone.wait()
    rebound = []
    for batch in sorted((queue / "inflight").iterdir()) if (queue / "inflight").exists() else ():
        if not batch.is_dir() or ".lease-v" in batch.name or batch.name in ("leases",) \
                or batch.name.startswith("."):
            continue
        meta = batch / "_claim.json"
        if meta.exists():
            body = json.loads(meta.read_text())
            body["owner_pid"] = gone.pid
            meta.write_text(json.dumps(body))
        target = batch.with_name(re.sub(r"\.pid\d+", f".pid{gone.pid}", batch.name))
        batch.rename(target)
        rebound.append(target.name)
    report["clone_legacy_batches_rebound_to_dead_pid"] = rebound
    report["start"] = tree()

    # A: old code; its worker blocks; the daemon (consumer) is SIGKILLed.
    a = spawn("consumer", old, queue, 1, log, 0)
    wa = wait_file(queue / ".rehearsal_worker_ready.json", a)
    a.kill()
    a.wait()
    report["A_old_worker"] = {"pid": wa["pid"], "request": wa["name"],
                              "alive_after_parent_kill": alive(wa["pid"])}
    report["A_after_stop"] = tree()
    reap(wa["pid"])
    report["A2_reconcile_dry"] = reconcile(False)
    report["A2_reconcile_apply"] = reconcile(True)
    report["A2_after"] = tree()

    # S: constructor stopped between staging metadata and publication.
    ready = queue / ".rehearsal_stage_ready.json"
    s = spawn("stage", new, queue, ready)
    staged = wait_file(ready, s)
    report["S_constructor"] = {"pid": staged["pid"], "staging": Path(staged["staging"]).name}
    report["S_alive_tree"] = tree()
    report["S_alive_reconcile_dry"] = reconcile(False)
    report["S_alive_reconcile_apply"] = reconcile(True)
    r = subprocess.run([py, str(HERE), "recover", new, queue], capture_output=True, text=True, env=env)
    report["S_alive_lease_recovery"] = json.loads(r.stdout.strip().splitlines()[-1])
    report["S_alive_staging_survives"] = Path(staged["staging"]).exists()
    s.kill()
    s.wait()
    report["S_constructor_alive_after_kill"] = alive(staged["pid"])
    report["S_dead_tree_before_drain"] = tree()
    r = subprocess.run([py, str(HERE), "recover", new, queue], capture_output=True, text=True, env=env)
    report["S_dead_lease_recovery"] = json.loads(r.stdout.strip().splitlines()[-1])
    report["S_dead_staging_drained_by_recovery"] = not Path(staged["staging"]).exists()
    report["S_dead_reconcile_apply"] = reconcile(True)
    report["S_after"] = tree()

    # B: new code; B's worker holds the lease; C competes; B's parent dies.
    b = spawn("consumer", new, queue, 1, log, 0)
    wb = wait_file(queue / ".rehearsal_worker_ready.json", b)
    c = spawn("consumer", new, queue, 6, log, -1)
    b.kill()
    b.wait()
    c.wait()
    report["B_worker"] = {"pid": wb["pid"], "request": wb["name"],
                          "alive_after_parent_kill": alive(wb["pid"])}
    report["B_competitor_ticks"] = len([l for l in c.stdout.read().splitlines() if l.startswith("{")])
    report["B_inflight_while_worker_lives"] = [p for p in tree()["inflight_requests"] if wb["name"] in p]
    report["B_reconcile_while_worker_lives"] = reconcile(True)

    # R: rollback rehearsal; consumers stopped; reap the executor; reconcile.
    reap(wb["pid"])
    report["R_reconcile_apply"] = reconcile(True)
    report["R_after"] = tree()

    # D: drain with the new code.
    d = spawn("consumer", new, queue, 1000, log, -1)
    d.wait()
    lines = [json.loads(l) for l in d.stdout.read().splitlines() if l.startswith("{")]
    report["D_ticks"] = len([l for l in lines if "lane" in l])
    report["D_processed"] = sum(l.get("processed", 0) for l in lines)
    report["D_failed"] = sum(l.get("failed", 0) for l in lines)
    report["D_stopped"] = [l for l in lines if "stopped" in l]
    report["end"] = tree()
    report["final_reconcile_dry"] = reconcile(False)

    # Execution-log analysis, grouped by the semantic request key the worker
    # parsed from the bytes it actually read (never by filename): two
    # executions of one semantic identity under any names are the same work.
    events = [json.loads(l) for l in log.read_text().splitlines()]
    by_identity: dict[str, list] = {}
    for e in events:
        key = json.dumps(e["key"]) if e.get("key") is not None else "unparsed:" + e["name"]
        by_identity.setdefault(key, []).append(e)
    overlaps, multi = [], []
    for key, evs in by_identity.items():
        spans = []
        for invocation in {e["invocation"] for e in evs}:
            mine = [e for e in evs if e["invocation"] == invocation]
            start = next(e for e in mine if e["event"] == "start")
            end = next((e for e in mine if e["event"] == "end"), None)
            stop = end["t"] if end else reaped.get(start["pid"], float("inf"))
            spans.append((start["t"], stop, invocation, end is not None))
        spans.sort()
        for i, (s1, e1, inv1, _d1) in enumerate(spans):
            for s2, _e2, inv2, _d2 in spans[i + 1:]:
                if s2 < e1:
                    overlaps.append([key, inv1, inv2])
        if sum(done for *_x, done in spans) > 1:
            multi.append(key)
    report["executions_started"] = sum(e["event"] == "start" for e in events)
    report["executions_completed"] = sum(e["event"] == "end" for e in events)
    report["identities_executed"] = len(by_identity)
    report["identity_grouping"] = ("semantic request key parsed by the worker from the bytes it read; "
                                   "one span per invocation id (start/end paired by id)")
    report["invocations"] = len({e["invocation"] for e in events})
    report["unparsed_executions"] = sum(1 for e in events if e.get("key") is None)
    report["reaped_pids"] = {str(k): v for k, v in reaped.items()}
    report["concurrent_duplicate_executions"] = overlaps
    # Sequential re-execution of one identity from distinct publications is
    # existing queue behavior, not concurrent ownership (round-5 consult).
    report["identities_completed_more_than_once"] = multi
    # Held window: from B's invocation starting until B's worker was reaped.
    # "Same identity" is the parsed semantic key, never the filename.
    b_event = next(e for e in events if e["pid"] == wb["pid"] and e["event"] == "start")
    report["B_competitor_ran_held_identity_while_held"] = any(
        e["event"] == "start" and e["key"] == b_event["key"]
        and e["invocation"] != b_event["invocation"]
        and b_event["t"] <= e["t"] < reaped[wb["pid"]]
        for e in events
    )
    Path(args.out).write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(json.dumps(report, indent=1, default=str))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    for name in ("--old-root", "--new-root", "--source", "--scratch", "--out"):
        r.add_argument(name, required=True)
    c = sub.add_parser("consumer")
    for name in ("code_root", "queue_root", "ticks", "log", "block_tick"):
        c.add_argument(name)
    s = sub.add_parser("stage")
    for name in ("code_root", "queue_root", "ready"):
        s.add_argument(name)
    rc = sub.add_parser("recover")
    for name in ("code_root", "queue_root"):
        rc.add_argument(name)
    args = parser.parse_args()
    if args.cmd == "run":
        run(args)
    elif args.cmd == "consumer":
        consumer(Path(args.code_root), Path(args.queue_root), int(args.ticks), Path(args.log),
                 int(args.block_tick))
    elif args.cmd == "stage":
        stage(Path(args.code_root), Path(args.queue_root), Path(args.ready))
    else:
        recover_lease_only(Path(args.code_root), Path(args.queue_root))


if __name__ == "__main__":
    main()
