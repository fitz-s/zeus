# Full-suite pytest baseline — `origin/live` @ `1de5d41ee`, 2026-08-04

**Status**: measurement only. No cleanup is proposed or authorized here. Recorded
because the number was not known and no gate reports it.

## The number

```
1961 failed, 19742 passed, 130 skipped, 17 deselected,
26 xfailed, 9 xpassed, 96 errors, 8 subtests passed in 2197.52s (36:37)
```

21,959 tests collected; **~9% of the suite is red on the default branch.**
Failures span **364 distinct test files**.

Run against a detached checkout of unmodified `origin/live`, `-p no:randomly`,
no `--maxfail`. `pytest-timeout` was absent locally, so `--timeout=600` (which
CI passes) was dropped; a hung test would show as a slow run, not a failure.

## Why no gate reports this

`full-pytest-sweep` is the only job that runs the whole suite. Two properties
hide the baseline:

1. `--maxfail=20` aborts the run after 20 failures. Its output reads
   `20 failed, 752 passed` — 772 of 21,959 tests, **3.5% of the suite**. The
   count looks like a small, contained problem.
2. It is ADVISORY (`continue-on-error: true`), pending its own documented
   promotion condition: *"Promote to REQUIRED after (a) G4 cleanup PR ships and
   baseline failures reach zero, (b) 5-7 days of advisory runs with zero
   new-failure-ID events."*

Neither is a defect — the workflow header states both, and its anchor case (the
G4 audit: 225 failures, 13 clusters, all green per-PR) is exactly this problem.
The gap is that nobody had measured the current figure.

The per-PR gates cannot see it either: they run a curated subset chosen by a
semantic-diff classifier, so a broken test is invisible until an unrelated
change happens to select its file. That mechanism surfaced three separate
dormant clusters during the 2026-08-02/04 CI work.

## By exception type

| exception | count |
|---|---|
| `AssertionError` | 520 |
| `ValueError` | 386 |
| `yaml.scanner.ScannerError` | 218 |
| `AttributeError` | 208 |
| `TypeError` | 194 |
| `KeyError` | 35 |
| `ImportError` | 26 |
| `NameError` | 25 |
| `ModuleNotFoundError` | 22 |
| `CertificateVerificationError` | 17 |
| `FileNotFoundError` | 17 |

All 218 `ScannerError`s are one break: the unquoted `why:` scalar at
`architecture/source_rationale.yaml:1755` introduced by `9ccf81333`. Fixed by
this PR. Measured on `tests/test_topology_doctor.py`: **93 failed → 34 failed**,
59 tests recovered in one file.

The `ImportError` / `ModuleNotFoundError` / `NameError` group (73) is worth an
early look — those usually mean a rename left callers behind, which is the same
shape as the `NO_SUBMIT` → `PRE_SUBMIT` cluster repaired in #481.

## Heaviest files

| file | failures |
|---|---|
| `tests/test_exchange_reconcile.py` | 125 |
| `tests/test_digest_profile_matching.py` | 120 |
| `tests/test_topology_doctor.py` | 93 |
| `tests/test_runtime_guards.py` | 78 |
| `tests/test_user_channel_ingest.py` | 62 |
| `tests/test_unknown_side_effect.py` | 49 |
| `tests/decision_kernel/test_certificate_ledger.py` | 41 |
| `tests/test_executor_command_split.py` | 39 |
| `tests/test_command_recovery.py` | 38 |
| `tests/decision/test_family_decision_engine.py` | 36 |
| `tests/test_day0_exit_gate.py` | 32 |
| `tests/test_cotenant_staging_guard.py` | 26 |
| `tests/integration/test_qkernel_spine_blockers_pr409.py` | 26 |
| `tests/test_executor.py` | 25 |
| `tests/test_check_live_restart_preflight.py` | 25 |
| `tests/test_market_analysis.py` | 23 |
| `tests/execution/test_venue_sync_contract.py` | 23 |
| `tests/test_pnl_flow_and_audit.py` | 22 |
| `tests/test_db.py` | 22 |
| `tests/test_day0_first_principles_antibodies.py` | 21 |
| `tests/state/test_position_open_idempotency.py` | 21 |
| `tests/execution/test_staleness_cancel.py` | 21 |
| `tests/strategy/live_inference/test_direction_law.py` | 20 |
| `tests/test_review_blocker_exit_lifecycle.py` | 18 |
| `tests/test_provenance_5_projections.py` | 18 |
| `tests/test_cycle_runner_discovery_gate_authority.py` | 18 |
| `tests/test_entry_gate_authority_promotion.py` | 17 |
| `tests/test_run_replay_cli.py` | 15 |
| `tests/test_market_discovery_full_coverage.py` | 15 |

Full distribution over all 364 files is reproducible with the command above.

## What this is NOT

Not evidence of a live-money defect. Every cluster investigated during this work
(`PROOF_BUNDLE_REQUIRED`, `buy_candidates_enabled`, held-SELL reauction) proved
to be a stale test whose production path was verified correct, and trading ran
normally throughout. A large red baseline is a *loss of signal*, not proof of
breakage: it means the suite can no longer tell anyone when something real
breaks.
