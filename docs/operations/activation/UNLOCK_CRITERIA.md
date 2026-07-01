# Active activation evidence criteria

**Authority**: active control-plane evidence checklist for
`scripts/produce_activation_evidence.py`.

**Created**: 2026-05-04. **Last audited**: 2026-06-28.

## Retired Controls

`ZEUS_ENTRY_FORECAST_ROLLOUT_GATE` is no longer live evaluator authority.
The evaluator does not read promotion evidence and does not branch on this
environment variable. Do not produce C1 rollout-gate evidence and do not use
`state/entry_forecast_promotion_evidence.json` as a live-entry admission input.

Promotion evidence remains a control-plane CLI artifact for
`src/control/cli/promote_entry_forecast.py`; it is not a money-path gate.

## Active Control

`ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS` controls whether entry-forecast
blockers affect the healthcheck predicate. It does not authorize orders.

## Producer

```bash
python scripts/produce_activation_evidence.py --all \
  --out-dir evidence/activation/
```

Each invocation writes:

- `evidence/activation/<date>_c4_healthcheck_diff.txt`
- `evidence/activation/<date>_summary.md`

Artifacts are evidence for an operator change. They are not sufficient by
themselves to flip a flag.

## Unlock Checklist

| Evidence | Required property |
|---|---|
| `tests/test_healthcheck.py::test_phase_c4_flag_off_healthy_unaffected_by_entry_forecast_blockers` | Flag OFF preserves the base health predicate |
| `tests/test_healthcheck.py::test_phase_c4_flag_on_healthy_false_when_entry_forecast_blocked` | Flag ON surfaces entry-forecast blockers |
| `tests/test_produce_activation_evidence.py` | Producer emits only active healthcheck evidence |
| `evidence/activation/<date>_c4_healthcheck_diff.txt` | Shows the current predicate diff |
| `evidence/activation/<date>_summary.md` | Shows `c4.ready_to_flip=True` |

**Sufficient set**: all listed tests green, producer summary fresh within
7 days, and `c4.healthy_when_off != c4.healthy_when_on`.

**Forbidden states**:

- `healthy_when_off == healthy_when_on` because the change would be a no-op.
- Any producer output referencing `ZEUS_ENTRY_FORECAST_ROLLOUT_GATE`, because
  that is no longer a live evaluator control.

## Audit Trail

Every active flag flip commit must include:

```text
Activation flip: ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS=1
Evidence: evidence/activation/<date>_summary.md (c4.ready_to_flip=true)
Tests:    tests/test_healthcheck.py, tests/test_produce_activation_evidence.py
Observation: <operator health window summary>
```
