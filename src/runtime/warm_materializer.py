# Created: 2026-09-29
# Last reused/audited: 2026-10-03
# Authority basis: REQ-20260929-223929-bf51a2; canonical_execution_lease.md 2.2.
"""One resident interpreter around the existing materialization CLI.

Only process startup is amortized. Every request still executes the same
prepare/dependency-validation/FORECAST-commit/wake path and absolute deadline.
The existing durable queue owns retry after timeout, process death, or a lost
reply. This transport never manufactures completion or writes a truth table.

Execution ownership: each invocation carries the claim's identity-lease
descriptors to the worker over a Unix socket (``SCM_RIGHTS``), and the worker
acknowledges receipt before it computes. A flock belongs to the open file
description, so the worker then holds the same lease as the queue: parent death
cannot free it while the worker executes, and the worker's exit always does.
The descriptors are in the socket queue from send to receipt, so there is no
window in which no process holds them.
"""
from __future__ import annotations
import atexit
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import threading
import time
import uuid
from typing import Callable, Sequence

_ROOT=Path(__file__).resolve().parents[2]
_SCRIPT=_ROOT / "scripts" / "materialize_replacement_forecast_live.py"
_MAX_REPLY=8*1024*1024
LEASE_SOCKET_ENV="ZEUS_MATERIALIZER_LEASE_SOCKET_FD"

class ResidentMaterializer:
    def __init__(self) -> None:
        self._lock=threading.Lock()
        self._process: subprocess.Popen[str] | None=None
        self._replies: queue.Queue=queue.Queue()
        self._lease_socket: socket.socket | None=None

    def _start(self, executable: str) -> None:
        self._replies=queue.Queue()
        parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM)
        try:
            process=subprocess.Popen([executable,str(_SCRIPT),"--resident-worker"],
                cwd=_ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                # Preserve uncaptured startup/log-handler diagnostics in daemon logs;
                # stdout alone is the framed reply channel.
                stderr=None,text=True,bufsize=1,pass_fds=(child.fileno(),),
                env={**os.environ,LEASE_SOCKET_ENV:str(child.fileno())})
        except BaseException:
            parent.close();raise
        finally:
            child.close()
        self._process=process
        self._lease_socket=parent
        replies=self._replies
        def read() -> None:
            try:
                while True:
                    line=process.stdout.readline(_MAX_REPLY+1)
                    if not line: replies.put(EOFError("materializer worker exited")); return
                    if len(line)>_MAX_REPLY or not line.endswith("\n"):
                        replies.put(ValueError("materializer worker reply exceeds bound")); return
                    replies.put(json.loads(line))
            except BaseException as exc:
                replies.put(exc)
        threading.Thread(target=read,name="materializer-reply",daemon=True).start()

    def _close(self) -> None:
        process,self._process=self._process,None
        lease_socket,self._lease_socket=self._lease_socket,None
        if lease_socket is not None:lease_socket.close()
        if process is None:return
        try:
            if process.stdin:process.stdin.close()
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=1)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
        finally:
            if process.stdout:process.stdout.close()

    def close(self) -> None:
        with self._lock:self._close()

    def _reply(self, request_id: str, deadline: float, args: list[str], timeout: float) -> dict:
        try:reply=self._replies.get(timeout=max(0.0,deadline-time.monotonic()))
        except queue.Empty:
            self._close();raise subprocess.TimeoutExpired(args,timeout)
        if isinstance(reply,BaseException):raise reply
        if not isinstance(reply,dict) or reply.get("request_id")!=request_id:
            raise ValueError("resident materializer response identity mismatch")
        return reply

    def run(self, argv: Sequence[str], *, timeout: float,
            lease_fds: Sequence[int] = ()) -> subprocess.CompletedProcess[str]:
        args=list(argv)
        if len(args)<3 or Path(args[1]).resolve()!=_SCRIPT.resolve():
            raise ValueError("resident materializer accepts only its canonical CLI")
        options=args[2:]
        if "--commit" not in options or not any(x in options for x in ("--input-json","--batch-input-json")):
            raise ValueError("resident materializer requires queued commit request")
        if any(x.startswith("--") and x not in {"--commit","--input-json","--batch-input-json","--deadline-utc"} for x in options):
            raise ValueError("resident materializer option outside queue contract")
        started=time.monotonic()
        deadline=started+timeout
        if not self._lock.acquire(timeout=timeout):
            raise subprocess.TimeoutExpired(args,timeout)
        try:
            if self._process is None or self._process.poll() is not None:self._start(args[0])
            request_id=uuid.uuid4().hex
            fds=list(lease_fds)
            self._process.stdin.write(json.dumps({"request_id":request_id,"argv":options,
                                                   "lease_fds":len(fds)})+"\n")
            self._process.stdin.flush()
            if fds:
                socket.send_fds(self._lease_socket,[request_id.encode()],fds)
                ack=self._reply(request_id,deadline,args,timeout)
                if ack.get("lease_ack")!=len(fds):
                    raise ValueError("resident materializer did not acknowledge its leases")
            reply=self._reply(request_id,deadline,args,timeout)
            return subprocess.CompletedProcess(args,int(reply["returncode"]),str(reply["stdout"]),str(reply["stderr"]))
        except subprocess.TimeoutExpired:
            raise
        except BaseException:
            self._close()
            raise
        finally:self._lock.release()

_WORKER=ResidentMaterializer()
atexit.register(_WORKER.close)

def run_warm_materialization(argv: Sequence[str], *, timeout: float,
                             lease_fds: Sequence[int] = ()) -> subprocess.CompletedProcess[str]:
    return _WORKER.run(argv,timeout=timeout,lease_fds=lease_fds)


def receive_leases(message: dict, write: Callable[[str], None]) -> list[int]:
    """Worker side: take this invocation's lease descriptors, then acknowledge.

    Raises when the frame does not carry exactly the announced descriptors for
    this request; the worker must then not compute.
    """
    count=int(message.get("lease_fds") or 0)
    if count==0:return []
    raw=os.environ.get(LEASE_SOCKET_ENV)
    if raw is None:raise RuntimeError("lease descriptors announced without a lease socket")
    lease_socket=socket.socket(fileno=os.dup(int(raw)))
    try:
        data,fds,_flags,_address=socket.recv_fds(lease_socket,256,count)
    finally:
        lease_socket.close()
    if data!=str(message["request_id"]).encode() or len(fds)!=count:
        for fd in fds:os.close(fd)
        raise RuntimeError("lease descriptor frame does not match its request")
    write(json.dumps({"request_id":message["request_id"],"lease_ack":count})+"\n")
    return fds
