# Fix: spine μ/σ threading — kill SPINE_INPUTS_UNAVAILABLE:MU_SIGMA_NOT_STASHED on live FSR

Created: 2026-06-16
Last reused or audited: 2026-06-16
Authority basis: task brief (money-path wiring bug — q-kernel spine never runs on live
FORECAST_SNAPSHOT_READY families) + `src/engine/event_reactor_adapter.py` Q-KERNEL SPINE
INPUTS block + `src/engine/qkernel_spine_bridge.py` `_spine_inputs_missing_reason`.

## Summary (one line)

The live spine-input producer sourced the bound forecast snapshot through a fetch that
ALSO runs the executable-forecast **trade-eligibility reader-block gate**; that gate raises
`FORECAST_READER_*` for live FSR families and the producer's fail-soft `except` swallowed
it, so the spine bridge read μ/σ back as `None` → `MU_SIGMA_NOT_STASHED` universally → every
forecast family fell back to the legacy path and rejected `TRADE_SCORE_NON_POSITIVE` →
zero harvest fills. Fix: source the member envelope through a new accessor that keeps the
same data-INTEGRITY predicates but does NOT run the trade-eligibility gate. Pure input
threading; no decision-math change.

## The brief's hypothesis was WRONG — corrected root cause

The brief hypothesized a `_payload(event)` double-parse: that the stash mutates one dict
and the bridge reads a different `_payload(event)` dict. **This is not the bug.**

Traced facts (all read from the actual code, line numbers as of this branch):

- `_build_event_bound_no_submit_receipt_core` (def 2179) computes `payload = _payload(event)`
  **once** at line 2215. That SAME `payload` object is threaded into
  `_generate_candidate_proofs(payload=payload, ...)` at 2440 and into
  `decide_family_via_spine(payload=payload, ...)` at 2525. Object identity and ordering are
  correct: `_generate_candidate_proofs` (which contains the stash block) runs at 2438 —
  BEFORE the bridge at 2523 — on the same object.
- `SPINE_STASH_DIAG=0` is a **red herring**. That diagnostic (event_reactor_adapter.py
  :11726-11744) lives in the CANONICAL path (`_market_analysis_from_event_snapshot`, the
  only caller is `_canonical_probability_and_fdr_proof` at 10851). The live FSR lane goes
  through `_replacement_authority_probability_and_fdr_proof` (9768), which returns non-None
  and `return`s at 9779-9780 — the canonical path NEVER runs live. So SPINE_STASH_DIAG can
  never fire live regardless of the bug, and proves nothing about the live stash.
- The live stash is a SEPARATE block in `_generate_candidate_proofs`
  (the "Q-KERNEL SPINE INPUTS" block, ~7715-7760). It is the live fix attempt and it was
  failing silently.

### Why the live block failed

The block fetched the bound snapshot via:

```python
_spine_snap = _forecast_snapshot_row_for_event(forecast_conn, event=event, family=family,
                                                allow_latest=False, decision_time=decision_time)
```

`_forecast_snapshot_row_for_event` (def 11145) does the simple causal SELECT, then calls
`_forecast_snapshot_reader_block_reason` (11195-11204), which **raises** a `FORECAST_READER_*`
ValueError when the executable-forecast reader scope is incomplete or blocked. The whole
block is wrapped in `except Exception: pass`, so that raise was swallowed → nothing stashed
→ `_edli_spine_mu_native` absent → bridge `_spine_inputs_missing_reason` returns
`MU_SIGMA_NOT_STASHED` (qkernel_spine_bridge.py:469-472).

That reader-block gate is a **trade-eligibility** check (source_run + coverage +
`read_executable_forecast` revalidation). It is NOT a member-validity check. Trade
eligibility is already owned by the live replacement-authority lane; re-deciding it here
purely to READ the member envelope is both redundant and the actual defect.

## Empirical proof (read-only probes against live state DBs)

Live log (state at fix time):

```
$ grep -oE "SPINE_INPUTS_UNAVAILABLE:[A-Z_]+" logs/zeus-live.log | sort | uniq -c
  33 SPINE_INPUTS_UNAVAILABLE:MU_SIGMA_NOT_STASHED
$ grep -c SPINE_STASH_DIAG logs/zeus-live.log
  0
```

Replaying `_forecast_snapshot_row_for_event(allow_latest=False)` against the 12 most-recent
live `FORECAST_SNAPSHOT_READY` events (real `OpportunityEvent`, real causal_snapshot_id):

```
Wuhan 2026-06-17 high causal=1171166: RAISED ValueError: FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:scope_incomplete
Wellington 2026-06-17 ... : RAISED FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:scope_incomplete
... (12/12 RAISED)
```

The bound causal snapshots themselves are perfectly valid — `_snapshot_members` reads 51
ensemble members and yields clean μ/σ directly (NO gate):

```
snap 1171166 Wuhan 2026-06-17:  size=51 mean=32.166 std=0.496
snap 1171160 Wellington:        size=51 mean=13.982 std=0.313
snap 1171155 Warsaw:            size=51 mean=19.910 std=0.702
```

So the envelope was always available; only the redundant reader-block gate stood in the way.

## The fix (pure input threading)

`src/engine/event_reactor_adapter.py`:

1. New accessor `_bound_forecast_snapshot_row_for_spine(conn, *, event, family, decision_time)`
   — fetches the family's bound causal `ensemble_snapshots` row with the SAME data-INTEGRITY
   predicates `_forecast_snapshot_row_for_event` uses (VERIFIED authority, causality OK, not
   boundary-ambiguous, `available_at ≤ decision_time`, pinned to `causal_snapshot_id`) but
   WITHOUT the trade-eligibility reader-block gate. Returns the row dict or None.
2. The Q-KERNEL SPINE INPUTS block now sources `_spine_snap` from this accessor instead of
   `_forecast_snapshot_row_for_event`. Everything downstream (member read → empirical
   mean/std → stash of `_edli_spine_*`) is byte-identical.

No decision-math, gate, threshold, sizing, direction-law, coherence, or submit-pipeline
change. Members are still validated by `_snapshot_members` (raises ⇒ honest MU_SIGMA). The
spine's selection (edge_lcb/ΔU/direction/coherence) and the submit pipeline are untouched.

Post-fix probe (same 12 live families, NEW accessor):

```
ok=12 none=0 accessor_raised=0 members_fail=0   (was: 12/12 RAISED before fix)
Wuhan 2026-06-17:  OK n=51 mu=32.166 sigma=0.496
... (all 12 OK with finite μ/σ)
```

## Tests

New: `tests/integration/test_qkernel_spine_musigma_threading.py` (3 tests). Builds an
in-memory `ensemble_snapshots` row reproducing the live condition (VERIFIED + causality-OK
bound snapshot with a valid 51-member envelope, but NO source_run/coverage scope ⇒
reader-block raises). Asserts:
- the OLD gated fetch RAISES `FORECAST_READER_*` on this fixture (documents the swallowed cause);
- the NEW accessor RETURNS the row and members are readable (μ/σ computable);
- running the live stash logic with the NEW accessor makes the bridge's OWN
  `_spine_inputs_missing_reason` return `UNKNOWN` (no gap), not `MU_SIGMA_NOT_STASHED`.

RED-on-revert (verified): replaying the live stash logic with the PRE-FIX source
(`_forecast_snapshot_row_for_event`) inside the same fail-soft `except` ⇒ payload has no
`_edli_spine_mu_native` ⇒ bridge classifier returns `MU_SIGMA_NOT_STASHED`. Switching to the
new accessor clears it.

Suite results (`.venv/bin/python -m pytest -q`):
- `tests/integration/test_qkernel_spine_routing.py` + new threading test: **9 passed**.
- `tests/money_path/`: **192 passed, 3 failed**. The 3 failures are all in
  `test_finding_b_free_cash_bound.py` (the known free-cash-bound baseline) — confirmed
  PRE-EXISTING by re-running on clean HEAD with the change stashed (identical 3 failures).
  Not attributable to this change.

## Files

- `src/engine/event_reactor_adapter.py` — new `_bound_forecast_snapshot_row_for_spine`
  accessor; spine-input block re-pointed to it.
- `tests/integration/test_qkernel_spine_musigma_threading.py` — RED-on-revert test (new).
