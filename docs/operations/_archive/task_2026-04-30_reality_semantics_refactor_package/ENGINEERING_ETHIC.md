# Engineering Ethic

Status: ethical operating rules for this refactor.

This is a live quantitative trading engine. The ethic is not "make the code
cleaner." The ethic is: do not let Zeus place, size, monitor, exit, settle, or
promote trades on semantically false objects.

## Principles

1. Money-path truth outranks convenience.
2. Unknown is not zero, not false, and not safe.
3. A probability is not a price. A quote is not a prior. A submitted limit is
   not a fill. A fill is not settlement.
4. Fail closed before live money; keep monitor/exit read-only where legal.
5. Backtest and shadow evidence are diagnostic until promoted through explicit
   governance and operator approval.
6. Old rows cannot be laundered into corrected economics.
7. Tests protect behavior before refactor edits.
8. No package, review, or handoff artifact is authority by placement alone.

## Quant-Machine Ethics

For a quant system, the moral failure mode is not only losing money. It is
creating apparently rigorous numbers from mismatched real-world objects.

This refactor must prevent:

- sizing from implied probability when executable cost is unavailable
- using YES complement as NO executable price without native NO token proof
- using held-token quote as posterior evidence
- using posterior belief to invent final submit price
- using target notional as filled exposure
- mixing legacy and corrected economics in promotion reports
- treating source validity, calibration, risk, collateral, or settlement
  readiness as solved because pricing semantics improved

## Operator and Live-Money Boundary

No live submission, production DB mutation, config flip, source-routing change,
schema migration apply, or strategy promotion is authorized by this package.

Corrected semantics should start shadow-only. Any canary requires separate live
readiness evidence and explicit operator go.

## Evidence Ethic

Every important claim must name its evidence class:

- authority: root/scoped AGENTS, architecture manifests, docs/authority, tests,
  executable source
- package input: this package and mirrored review/spec material
- current fact: operations current-fact surfaces with freshness constraints
- derived context: topology and graph output
- inference: a reasoned conclusion that still needs verification

Do not present inference as authority.

## Review Ethic

Do not close implementation packets on self-review. For this loop, every packet
needs critic and verifier review before close, and a third-party critic plus
verifier pass after close before freezing the next packet.

## Git and Co-Tenant Ethic

The worktree is dirty. Preserve unrelated edits. Stage specific files only if a
future commit is requested. Never use destructive git commands to make the tree
look clean.
