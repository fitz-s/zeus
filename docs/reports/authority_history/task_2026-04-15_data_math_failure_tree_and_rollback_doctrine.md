# task_2026-04-15_data_math_failure_tree_and_rollback_doctrine

Status: packet-scoped doctrine

## 1. Failure classes

### A. Stale-authority contradiction
Signal:
- code and active prose disagree
- a touched surface still implies paper support
- replay prose overclaims parity
Action:
- fix the prose to match code truth
- do not fix code to match stale prose
- rollback: revert prose edits only

### B. Calibration lineage gap
Signal:
- harvested pair lacks decision_group_id
- harvested pair lacks bias_corrected
- authority lineage is ambiguous
Action:
- thread missing field through the harvester chain
- add relationship test
- rollback: revert harvester + test changes

### C. Shadow metric boundary violation
Signal:
- a shadow metric gates live execution
- a shadow metric blocks candidates
- advisory output is consumed as calibrated truth
Action:
- add SHADOW_ONLY sentinel
- ensure return dicts carry shadow_only marker
- rollback: revert sentinel + marker additions

### D. Replay authority drift
Signal:
- replay output is consumed as promotion evidence
- replay limitations are hidden
- replay claims parity it cannot prove
Action:
- add missing parity dimensions to limitations
- surface linkage state prominently
- keep promotion_authority: False
- rollback: revert replay metadata additions

### E. Schema / DB truth contamination
Signal:
- a code change requires schema migration
- a diagnostic write touches canonical truth
- a rebuild is needed to maintain coherence
Action:
- STOP — do not proceed
- escalate to human gate
- rollback: revert entire patch

## 2. Rollback discipline

- Every ticket has an explicit rollback note
- Rollback scope must not exceed the ticket's changed files
- If rollback requires cross-zone changes, the ticket was misgoverned
- Prefer doc-only rollbacks; code rollbacks must preserve test green

## 3. Escalation triggers

Escalate to human when:
- a stop condition fires
- rollback scope exceeds the ticket
- two tickets conflict
- a test failure reveals hidden architecture dependence
- the packet would need to grow beyond its declared scope
