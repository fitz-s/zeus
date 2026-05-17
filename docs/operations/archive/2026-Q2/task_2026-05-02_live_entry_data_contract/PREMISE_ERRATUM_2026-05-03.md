# Premise erratum — `cb4beb6c` LIVE_ELIGIBLE/HORIZON_OUT_OF_RANGE counts

## Background

Commit `cb4beb6c fix: riskguard proxy bypass + unblock entry forecast rollout`
declared in its message:

> 204 LIVE_ELIGIBLE city-date pairs available; 51 HORIZON_OUT_OF_RANGE
> will be rejected by reader (expected).

The 2026-05-03 PM critic-opus review (Top-finding #6) flagged this as a
premise mismatch because the production DB at the time of review showed
408 LIVE_ELIGIBLE and 102 BLOCKED rows — exactly twice the cited figures.

## DB probe (verbatim)

Run on `state/zeus-world.db` at HEAD `11bbc8b2` on 2026-05-03 PM CDT:

```sql
SELECT status, track, COUNT(*) AS n
FROM readiness_state
WHERE strategy_key='producer_readiness'
GROUP BY status, track
ORDER BY status, track;
```

| status | track | n |
|---|---|---|
| BLOCKED | mn2t6_low_full_horizon | 51 |
| BLOCKED | mx2t6_high_full_horizon | 51 |
| LIVE_ELIGIBLE | mn2t6_low_full_horizon | 204 |
| LIVE_ELIGIBLE | mx2t6_high_full_horizon | 204 |

```sql
SELECT status, COUNT(*) AS n
FROM readiness_state
WHERE strategy_key='producer_readiness'
GROUP BY status;
```

| status | n |
|---|---|
| BLOCKED | 102 |
| LIVE_ELIGIBLE | 408 |

## Conclusion

The two figures are consistent at different scopes:

- Per-track (high or low, single track): **204 LIVE_ELIGIBLE / 51 HORIZON_OUT_OF_RANGE**
- Aggregate (high + low together): **408 LIVE_ELIGIBLE / 102 HORIZON_OUT_OF_RANGE**

Operator's commit message used per-track figures. critic-opus review
counted the aggregate. Both are correct measurements of the same DB.
**No data divergence; no remediation required.**

## Operational note

Future commit messages that quote LIVE_ELIGIBLE / HORIZON_OUT_OF_RANGE
counts should explicitly name the scope: "204 LIVE_ELIGIBLE per-track
(408 aggregate across high+low)" — the reader cannot infer which is
intended without the qualifier, and the difference between per-track
and aggregate looks identical to a 2x measurement error.

Authority basis: `docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md`
high/low track separation; AGENTS.md §1 dual-track discipline.
