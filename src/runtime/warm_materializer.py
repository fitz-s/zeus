# Created: 2026-09-29
# Last reused/audited: 2026-09-29
"""One resident interpreter around the existing materialization CLI.

Only process startup is amortized. Every request still executes the same
prepare/dependency-validation/FORECAST-commit/wake path and absolute deadline.
The existing durable queue owns retry after timeout, process death, or a lost
reply. This transport never manufactures completion or writes a truth table.
"""
from __future__ import annotations
import atexit
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid
from typing import Sequence

_ROOT=Path(__file__).resolve().parents[2]
_SCRIPT=_ROOT / "scripts" / "materialize_replacement_forecast_live.py"
_MAX_REPLY=8*1024*1024

class ResidentMaterializer:
    def __init__(self) -> None:
        self._lock=threading.Lock()
        self._process: subprocess.Popen[str] | None=None
        self._replies: queue.Queue=queue.Queue()

    def _start(self, executable: str) -> None:
        self._replies=queue.Queue()
        process=subprocess.Popen([executable,str(_SCRIPT),"--resident-worker"],
            cwd=_ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            # Preserve uncaptured startup/log-handler diagnostics in daemon logs;
            # stdout alone is the framed reply channel.
            stderr=None,text=True,bufsize=1)
        self._process=process
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

    def run(self, argv: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        args=list(argv)
        if len(args)<3 or Path(args[1]).resolve()!=_SCRIPT.resolve():
            raise ValueError("resident materializer accepts only its canonical CLI")
        options=args[2:]
        if "--commit" not in options or not any(x in options for x in ("--input-json","--batch-input-json")):
            raise ValueError("resident materializer requires queued commit request")
        if any(x.startswith("--") and x not in {"--commit","--input-json","--batch-input-json","--deadline-utc"} for x in options):
            raise ValueError("resident materializer option outside queue contract")
        started=time.monotonic()
        if not self._lock.acquire(timeout=timeout):
            raise subprocess.TimeoutExpired(args,timeout)
        try:
            if self._process is None or self._process.poll() is not None:self._start(args[0])
            request_id=uuid.uuid4().hex
            self._process.stdin.write(json.dumps({"request_id":request_id,"argv":options})+"\n")
            self._process.stdin.flush()
            remaining=max(0.0,timeout-(time.monotonic()-started))
            try:reply=self._replies.get(timeout=remaining)
            except queue.Empty:
                self._close();raise subprocess.TimeoutExpired(args,timeout)
            if isinstance(reply,BaseException):raise reply
            if not isinstance(reply,dict) or reply.get("request_id")!=request_id:
                raise ValueError("resident materializer response identity mismatch")
            return subprocess.CompletedProcess(args,int(reply["returncode"]),str(reply["stdout"]),str(reply["stderr"]))
        except subprocess.TimeoutExpired:
            raise
        except BaseException:
            self._close()
            raise
        finally:self._lock.release()

_WORKER=ResidentMaterializer()
atexit.register(_WORKER.close)

def run_warm_materialization(argv: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return _WORKER.run(argv,timeout=timeout)
