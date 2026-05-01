# M5 post-close third-party review — 2026-04-27

Phase: M5 — Exchange reconciliation sweep
Status: POST-CLOSE PASS; R1 unfrozen, T1 still blocked by R1
Timestamp: 2026-04-27T19:57:30Z

## Review requirement

Per the R3 loop directive, M5 cannot unfreeze the next packet until the
additional post-close third-party critic and verifier pass. M5 was already
closed after pre-close critic+verifier review; this artifact records the extra
post-close review loop triggered after Huygens found an explicit freshness
marker blocker.

## Huygens blocker remediated

Post-close critic Huygens the 2nd blocked M5 because
`read_freshness={"ok": true, "fresh": false}` could still be accepted as
venue absence proof. The remediation tightened
`src/execution/exchange_reconcile.py::_assert_adapter_read_fresh()` so explicit
`ok` or `fresh` markers must be boolean `True` when present. Explicit
`fresh: false` now fails closed even if `ok: true`.

Regression: `tests/test_exchange_reconcile.py::test_explicit_fresh_false_is_not_absence_proof`
uses `{"ok": True, "fresh": False, "captured_at": NOW.isoformat()}` and
asserts the sweep raises without creating findings or mutating the local command
state.

## Post-remediation verification evidence

```text
python3 -m py_compile src/execution/exchange_reconcile.py tests/test_exchange_reconcile.py: PASS
pytest -q -p no:cacheprovider tests/test_exchange_reconcile.py tests/test_v2_adapter.py tests/test_digest_profile_matching.py::test_r3_m5_exchange_reconcile_routes_to_m5_profile_not_heartbeat: 38 passed
pytest -q -p no:cacheprovider tests/test_exchange_reconcile.py tests/test_v2_adapter.py tests/test_digest_profile_matching.py::test_r3_m5_exchange_reconcile_routes_to_m5_profile_not_heartbeat tests/test_venue_command_repo.py tests/test_exit_safety.py tests/test_user_channel_ingest.py tests/test_heartbeat_supervisor.py tests/test_cutover_guard.py: 133 passed, 6 skipped, 18 known deprecation warnings
python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M5: GREEN=10 YELLOW=0 RED=0
python3 scripts/topology_doctor.py closeout ... --summary-only: ok=true, blocking_issues=[]
```

## Third-party critic result

Critic: Lorentz the 2nd
Result: APPROVE

Evidence summarized by critic:

- `_assert_adapter_read_fresh` fails closed for explicit stale/unsuccessful
  metadata, empty marker maps, and future timestamps.
- Open-order, trade, and position enumeration proof is checked before
  absence-sensitive sweep logic.
- Real adapter missing read surfaces raise `V2ReadUnavailable`, not empty lists.
- Exchange-only state writes findings, not new `venue_commands` rows.
- FAILED/RETRYING trade facts do not advance command fill state and do not
  suppress local-absence findings.
- R1 remains frozen until the paired post-close verifier records PASS.

## Verifier status

Verifier: Plato the 2nd
Initial result: FAIL (procedural only)

Plato verified the code/tests/closeout evidence as green but failed the gate
because this `M5_post_close_2026-04-27.md` artifact did not yet exist and live
status surfaces still correctly showed the verifier gate as pending. R1 remained
frozen until the verifier re-run passed and the status surfaces were flipped in a
separate, explicit step.

## Verifier re-run result

Verifier: Pasteur the 2nd
Result: PASS

Pasteur verified the M5 post-close artifact, code/evidence consistency, broader M5 gate (`133 passed, 6 skipped`), and M5 drift (`GREEN=10 YELLOW=0 RED=0`). No technical blockers remained for the verifier gate.

## Unfreeze decision

Decision: R1 may be marked ready for phase entry at its inherited Q-FX-1 operator gate. T1 remains blocked by R1 even after M5 post-close passes.
