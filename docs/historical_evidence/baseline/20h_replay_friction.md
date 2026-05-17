# 20h Replay Friction Baseline

Created: 2026-05-06
Last reused or audited: 2026-05-06
Authority basis: ULTIMATE_DESIGN sunset 2027-05-06; IMPLEMENTATION_PLAN Phase 0.A

## Method

Analyzed most recent substantial session JSONL to estimate topology-attributable
friction. Did NOT re-run the session (Phase 5 work per IMPLEMENTATION_PLAN §2).

## Session analyzed

File: `bccc8776-2487-4170-8893-8201d976c157.jsonl`
Path: `.claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus/bccc8776-...`
Size: 104 MB (largest session file in project)

## Measurements

| Metric | Value |
|---|---|
| Session start | 2026-05-02 16:13:06 UTC |
| Session end | 2026-05-06 10:57:22 UTC |
| Session duration | 90.74 hours |
| Total tool calls | 7,581 |
| Topology-attributable invocations | 54 |
| src/ file edits | 336 |
| Topology ratio (topology_calls / total_calls) | 0.007 (0.7%) |

Topology invocations counted: Bash calls to topology_doctor*.py scripts,
task_boot_profile reads, digest_profile reads detected in tool `input` fields.

## Friction estimate

```
estimated_friction_hours = session_duration × topology_ratio
                         = 90.74 × 0.007
                         = 0.64 hours
```

**Note on session scope**: This 90-hour session spans 4 calendar days and
encompasses multiple sub-tasks. The ~20-hour session referenced in briefing §1
appears to be a specific autonomous sub-session. The 0.7% topology ratio from
this larger session is used as the proxy; the actual 20h session friction
estimate is: 20 × 0.007 = **0.14 hours**.

This low ratio reflects that most topology friction is invisible in tool calls
— it appears as bootstrap token cost (250K tokens/task × 0.7% = not the right
metric). The real friction is the context window consumed by topology files
before agents can act, not the explicit topology_doctor invocation count.

## Fixture status

Session found and analyzed. However, the specific "~20-hour autonomous session"
from briefing §1 is not identifiable as a discrete JSONL. The 90.74h file is
the best available proxy. If Phase 5 requires the exact session, substitution
policy R7 applies (synthetic 5-task panel).

## Acceptance target

Post-cutover target: ≤2h topology-attributable friction per 20h session (briefing §9).
Current estimate: ~0.14h for 20h proportional slice (by invocation ratio).
Actual friction via token-cost path: much higher (agent context budget exhaustion
before writing code is the dominant cost, not explicit topology_doctor calls).
