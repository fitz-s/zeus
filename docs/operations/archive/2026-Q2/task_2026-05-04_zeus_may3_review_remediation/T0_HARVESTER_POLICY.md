# T0_HARVESTER_POLICY — Planner Triage

**Created:** 2026-05-04
**Verdict:** REALITY_ANSWERED — current code defaults harvester live to OFF (DR-33-A). T1C may proceed without operator override.
**Captured-by:** planner subagent

---

## 1. The plan's question

Per MASTER_PLAN_v2 §8 T0.7:
> Operator locks harvester live mode → `T0_HARVESTER_POLICY.md`; `ZEUS_HARVESTER_LIVE_ENABLED` state, learning-write policy, and whether live harvester remains disabled through T1C.

## 2. Reality findings (planner grep, 2026-05-04)

### 2.1 Default state: DISABLED-OFF

`src/execution/harvester.py:461-472`:

```python
if os.environ.get("ZEUS_HARVESTER_LIVE_ENABLED", "0") != "1":
    logger.info(
        "harvester_live disabled by ZEUS_HARVESTER_LIVE_ENABLED flag (DR-33-A default-OFF); "
        "cycle skipped; no data-plane calls"
    )
    return {
        "status": "disabled_by_feature_flag",
        "disabled_by_flag": True,
        ...
    }
```

The default is `"0"` (treated as off). The harvester short-circuits **before any data-plane call, DB connection, or HTTP request**. The OFF state is provably side-effect-free.

### 2.2 Same gate at all three harvester surfaces

`grep -rn "ZEUS_HARVESTER_LIVE_ENABLED"`:

- `src/execution/harvester.py:461` — `run_harvester()` main path
- `src/execution/harvester_pnl_resolver.py:44` — PNL resolver
- `src/ingest/harvester_truth_writer.py:443` — truth writer
- `src/ingest_main.py:607` — comment
- `src/contracts/ensemble_snapshot_provenance.py:139` — comment

Three execution paths gate on the same env var with the same default. Consistency is correct.

### 2.3 Learning-write rebrand evidence already mitigated upstream

`docs/to-do-list/known_gaps.md:91-94` (under "current non-Paris repair overlay"):

> Settlement/learning: harvester settlement lookup is metric/source/station aware, LOW settlement writes use LOW identity, pending-exit residual exposure can settle, and calibration-pair learning preserves actual snapshot/source lineage instead of rebranding live/Open-Meteo p_raw as TIGGE training rows.

This describes a previously closed antibody. T1C's "p_raw rebrand without lineage" guard is a strengthening, not a green-field protection.

### 2.4 Settlement / redeem split status

`grep -n "redeem\|settlement_status" src/execution/harvester.py` (planner spot-check) — harvester records settlements but redeem is a separate command in `src/execution/settlement_commands.py` (state machine: `requested → confirmed`). The split exists as command grammar; F4's claim that the in-process functions are still coupled needs T1C verification, but the state machine substrate is in place.

## 3. Reality answers

| T0.7 field | Reality answer | Evidence |
|---|---|---|
| `ZEUS_HARVESTER_LIVE_ENABLED` default | `"0"` (OFF) — DR-33-A staged rollout | `src/execution/harvester.py:461` |
| Live state | DISABLED today; no operator action needed to keep it disabled | env var unset = OFF |
| Learning-write authority | Existing antibody (per known_gaps overlay) prevents Open-Meteo→TIGGE rebrand | `known_gaps.md:91-94` |
| Disabled through T1C? | YES — T1C is structural separation; flag stays OFF until T2/T3 proves the new split is correct | reality + plan agreement |

## 4. Recommended draft policy (operator-confirmable)

```
ZEUS_HARVESTER_LIVE_ENABLED: "0" (off; default; do NOT export "1" before T1C closeout)
Live state:                 DISABLED through T1C closeout (and beyond, until T3 acceptance gates)
Learning-write policy:      MUST cite source lineage (snapshot_id + source authority) per fact;
                            otherwise diagnostic-only or no-op. T1C MUST add a hard guard.
Re-enable gate:             T1C closeout + T2 control plane + T3.13 settlement/source/city/time
                            identity contracts. Re-enabling requires explicit operator artifact.
T1C acceptance:             Settlement-record / redeem-enqueue / learning-write are 3 separate
                            functions with 3 separate authority gates. Settlement recorded is NOT
                            redeem confirmed.
```

## 5. No-operator-decision-needed determination

The plan asks the operator three things, and reality already answers each:

1. *"`ZEUS_HARVESTER_LIVE_ENABLED` state"* → OFF by default in code; the operator's environment shell does not export `"1"` (per `T-1_DAEMON_STATE.md:21` `ZEUS_MODE=unset` shows no aggressive enablement is happening). **Reality answers: stay OFF.**
2. *"learning-write policy"* → existing known_gaps overlay says the previous Open-Meteo rebrand is closed; T1C's job is to instrument the guard so the antibody persists. **Reality answers: lineage required, T1C adds the static guard.**
3. *"whether live harvester remains disabled through T1C"* → working contract §4.6 ("Operator environment variables may only brake or disable; they may not enable corrected live in the absence of per-position/per-intent evidence gates") makes this answer mandatory. **Reality answers: yes, disabled through T1C.**

Operator may simply sign this draft as-is. **No operator decision is required to unblock T1C planning.**

## 6. Source-evidence cite list (planner grep-verified within 10 minutes)

- `src/execution/harvester.py:456-472` — feature-flag gate and default `"0"`
- `src/execution/harvester_pnl_resolver.py:44-46` — same gate, PNL resolver path
- `src/ingest/harvester_truth_writer.py:443-445` — same gate, truth writer
- `docs/to-do-list/known_gaps.md:91-94` — closed antibody for p_raw rebrand
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md:122` (working-contract §4.6 — env brake-only)
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md:518-524` — T1C plan body

---

**Verdict:** REALITY_ANSWERED. Operator may sign this triage as-is.
