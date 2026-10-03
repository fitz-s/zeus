# Canonical execution lease for replacement materialization

Status: BUILT on fix/execution-lease (migration: `canonical_execution_lease_migration.md`). Part B of the claim-wait work; Part A
(`fix/claim-wait-a`) shipped only the claim-fence relaxation and reason names.
Owner surface: `src/data/replacement_forecast_live_materialization_queue.py`
(claim/recovery), `src/runtime/warm_materializer.py` and
`scripts/materialize_replacement_forecast_live.py` (executor).
checked=2026-W40; basis=consult NO-GO on ca90f2ebc
(`/tmp/cgc/answer_REQ-20261002-082408-568e9b.txt`), adversarial harness
`artifacts/merge_safety_claim_wait_review/test_adversarial_claims.py`.

## 1. Problem

Three distinct things are currently conflated, and every repair that kept them
together failed an executed counterexample:

| Thing | What it is | Where it lives today |
|---|---|---|
| Forecast-input identity | the immutable inputs a posterior is computed from | `_blocked_attempt_fingerprint` |
| Publication authority | which published request file, with which owner envelope, may run | request JSON + `day0_enqueue_owner_witness` |
| Execution ownership | who is executing that identity right now | `inflight/<batch>/` directory + `claimed_at` age |

Today's ownership facts (all reproduced on `origin/live` by the harness):

- Two differently named publications of one semantic identity can both be
  claimed: the owner scan and the rename are separate steps (identity race).
- A claim whose construction raises after a partial move strands requests in an
  inflight batch until the age bound expires.
- The resident worker keeps executing after its queue parent dies; nothing
  binds the claim to the executor.
- Recovery is by `claimed_at` age, a timer, not an ownership fact.
- `_process_claimed_materialization_batch` re-sorts a claimed batch, so a
  planned held/global/expansion order is not the dispatch order.

## 2. Target design

### 2.1 One identity-keyed lease, shared by every lane

- Key: the semantic identity (`_request_semantic_key`), hashed to a fixed name
  under `inflight/leases/<sha256>.lease`. Not a batch name, not a filename.
- Acquire: `open(O_CREAT)` + `flock(LOCK_EX|LOCK_NB)` on that path. Failure to
  lock means another owner exists for this identity, regardless of which file
  it publishes. This is the only exclusion primitive; the priority fast path,
  the flocked background claim, seed transport and stale recovery all call it.
- Validate under the lease: only after the lease is held, re-read the selected
  request bytes and compare with the plan's snapshot row; then move it.
- Multiple keys (multi-slot): acquire in sorted key order, release all on any
  failure before returning. No partial ownership escapes the constructor.

### 2.2 The executor holds the lease through execution

The lease must be held by the process that computes, for exactly as long as it
computes. Preferred: worker-side acquisition. The parent sends request paths to
the resident worker; the worker acquires each identity lease itself, validates,
computes, writes its terminal envelope, then releases. The parent never holds
execution ownership, so parent death cannot orphan or duplicate work.

Alternative, if parent-side claims stay: an explicit acknowledged transfer per
invocation (parent passes the descriptor with `SCM_RIGHTS`, the worker
acknowledges before the parent closes its copy). Startup-only inheritance is not
a transfer and is rejected.

Liveness is then a fact: a lease file whose lock can be taken has no owner. No
age path exists for new-protocol batches. An unreadable new-protocol lease is
UNKNOWN and is never reclaimed by time.

### 2.3 Exception-safe claim construction

One context manager owns the whole claim: lease acquisition, metadata write,
request moves, directory fsyncs. On any exception it restores every request it
moved (rename back; the lease is still held, so nobody else can take it),
removes the empty batch, and only then releases the leases. Releasing a
descriptor is in a `finally` that cannot be skipped by a cleanup failure.

### 2.4 Multi-slot with ordered dispatch

The plan's interleave (held, global-only, expansion) is a contract about
execution order, not only about which files are leased. The claim carries an
ordered list of records `(slot, identity, request_sha256, owner_generation)`;
dispatch iterates that list as given and never re-sorts. Throughput is reported
as leased, started, completed, deferred and held-first-executed per tick;
"leased" alone is not progress.

### 2.5 Protocol versions and quiescent migration

- Claim metadata carries `protocol: "lease-v1"`. Observation states are typed:
  `HELD`, `ACQUIRED_FOR_RECOVERY`, `UNKNOWN`, `LEGACY`.
- `LEGACY` (no protocol field) is the only state that may use the existing age
  bound; it exists only during migration.
- Migration is a quiescent ownership transition, not a timed wait: stop claim
  acquisition; let resident executors finish or reap them; reconcile `inflight/`
  back to `requests/`; start the new consumer. Rollback is the same sequence in
  reverse. The last validated serving posterior is preserved throughout.

### 2.6 Separate ownership-envelope verdicts from forecast-input verdicts

The Hong Kong republish loop (one family republished every 10-20 s, each copy
spawning a child that returns the same BLOCKED) is real head-of-line waste. Its
cause: the owner witness names the publishing seed file, so it differs on each
republish, and it is hashed into the attempt fingerprint, so the seed producer
never matches the request side's BLOCKED marker.

Excluding the witness from the fingerprint (tried in ca90f2ebc) is unsound: the
worker validates the witness as a named input, so an invalid envelope produces
an INPUT_VERDICT, and that verdict would then suppress a repaired request whose
inputs are identical (the consult's `repaired_owner_envelope` counterexample).

Sound split:

- Envelope verdicts (`RequestInputInvalid` on `day0_enqueue_owner_witness`,
  `STALE_DAY0_ENQUEUE_OWNER`) are terminal for the exact publication bytes only.
  They never write or match a forecast-input suppression marker.
- Forecast-input verdicts (typed BLOCKED evidence over inputs) are keyed on the
  forecast-input identity, which excludes the envelope. Only these may suppress
  a republish at the producer.
- The marker records `fence_version`; markers written under the old,
  envelope-inclusive identity are never honoured by the new one.

With that split, the producer can drop a republish whose forecast-input identity
already holds a forecast-input BLOCKED, while a repaired envelope still runs.

## 3. Acceptance for Part B

- `artifacts/merge_safety_claim_wait_review/test_adversarial_claims.py`, unchanged,
  passes in full: partial_claim (both faults), dispatch_preserves, orphan_only,
  unreadable_new_lock, parent_death, repaired_owner_envelope, distinct_names.
- The Part A tests and the round 2/3/8/9 harnesses stay green; failure-set diff
  over queue-importing modules NEW=∅.
- A replay on a frozen real queue snapshot reports leased/started/completed/
  held-first per tick, before and after.
- A migration rehearsal on a copy of `state/replacement_forecast_live/` with a
  live resident worker shows no duplicate execution and no stranded request.

## 4. Not in scope

Changing posterior math, the commit-time dependency witness, coverage rules, or
the global auction. The forecast-DB claim fence stays relaxed as in Part A.
