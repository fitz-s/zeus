# Execution-lease protocol migration and rollback

Status: procedure for the Part B deploy of `canonical_execution_lease.md`.
Owner surface: `scripts/reconcile_materialization_inflight.py` (reconcile step,
implemented in `reconcile_inflight_for_migration`).
checked=2026-W40.

## What changes on disk

| Before (LEGACY) | After (lease-v1) |
|---|---|
| `inflight/<stamp>.pid<N>/` batch, owner by `claimed_at` age | `inflight/<stamp>.lease-v1.<gen>.pid<N>/` batch whose `claim.control` carries `protocol`, `leases`, ordered `records`. The control name is not `*.json`, so no request can collide with it. A batch without `claim.control` is read from `_claim.json`, the earlier layout, which reconcile still restores |
| no lease | `inflight/leases/<sha256(identity)>.lease`, owned by `flock` on the claiming OFD, shared with the executing resident worker via `SCM_RIGHTS`. The queue parent also keeps its copy until outcome handling ends, so the lease frees when both have let go. |
| — | `inflight/.staging.<batch>/` while a claim is built, flocked by its constructor until it is published by rename; free staging is crash debris and is drained |
| — | `quarantined_request_aliases/.capture.<name>.<id>/`: the captured entry under `payload/`, the terminal receipt at `quarantine.receipt.control`. The receipt is written complete under `receipt.staging/` and renamed into place. Under the capture flock, a readable but non-terminal receipt or a staged one is discarded and the capture is classified again. An unreadable receipt is kept and reported `unknown`. |
| blocked markers keyed on an envelope-inclusive fingerprint | `fence_version: f2-forecast-input`; old markers never match and are re-decided by the next attempt. A verdict caused by an invalid envelope never establishes a forecast-input fence (an existing forecast-input marker is still consulted before the worker runs). |

A LEGACY batch is the only kind the age bound may still restore. Both versions
can read each other's `requests/` files; neither interprets the other's
`inflight/` safely, which is why the transition is quiescent.

## Upgrade (quiescent ownership transition)

No step waits on a clock. Each step ends on an observable fact.

1. **Stop claim acquisition.** Stop the forecast-live daemon (the only claimant):
   `launchctl bootout gui/$UID/com.zeus.forecast-live`. Fact: no
   `src.ingest.forecast_live_daemon` process (`pgrep -f forecast_live_daemon`).
2. **Let executors finish or reap them.** The resident worker
   (`materialize_replacement_forecast_live.py --resident-worker`) exits when its
   parent closes stdin; one mid-request may still be computing. Fact: no
   `--resident-worker` process. If one remains and must not be waited for,
   `kill` it; its commit is all-or-nothing under the forecast writer lock and
   its request stays in `inflight/` for step 3.
3. **Reconcile.** `python3 scripts/reconcile_materialization_inflight.py`
   (dry run), then `--apply`. Exit 0 means quiescent: every inflight request is
   back in `requests/`, no lease is held, and no live constructor's staging
   directory remains (crash-left staging, whose flock is free, is drained).
   Exit 3 lists what is still owned: refused batches (a lease-v1 batch whose
   lease is HELD, or UNKNOWN including an unsupported protocol; a LEGACY batch
   whose recorded pid is alive), `held_leases`, and `live_staging`. Resolve
   that owner (step 2) and rerun. Never delete a refused batch by hand. Exit 0
   is evidence for step 1, not a substitute: it cannot see a claimant that has
   not yet taken its first lease, which is why step 1 stops acquisition first.
4. **Deploy the new code and start the consumer.** Restart through the normal
   live deploy (all daemons, mesh-coherent). The first tick claims from
   `requests/` under lease-v1.

The last validated serving posterior is untouched throughout: no step writes
`forecast_posteriors` or readiness. Steps 1-3 only pause new materialization.

## Rollback

The same sequence, reversed in code version:

1. Stop the forecast-live daemon (as upgrade step 1).
2. Let the resident worker exit or reap it (as upgrade step 2). Its lease frees
   with it.
3. Run the reconcile with the **new** code still checked out, so lease-v1
   batches are read by the code that understands them. Exit 0 required.
4. Check out the previous revision and start the daemon.

Old code never sees a lease-v1 batch, so its age bound can never steal a held
claim. Markers written under `f2-forecast-input` hash a field old code does not
compute, so old code never matches them either; each family re-decides once.

## Verification

- `pytest tests/test_materialization_execution_lease.py tests/adversarial/test_execution_lease_adversaries.py`
- Rehearsal evidence (copy of `state/replacement_forecast_live/` with a live
  resident worker): recorded in the Part B landing report.
