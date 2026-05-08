# TASK - Alignment Repair Workflow

Created: 2026-05-08
Worktree: `/Users/leofitz/.openclaw/worktrees/zeus-alignment-safe-implementation-2026-05-08`
Branch: `repair/alignment-safe-implementation-2026-05-08`
Base branch: `object-invariance-mainline-next-2026-05-08`
Base commit: `cda80d3e`
Authority basis: root `AGENTS.md`, `docs/operations/AGENTS.md`, topology navigation attempts on 2026-05-08, and deep alignment audit safe-to-implement cut.

## Objective

Move from deep alignment audit into a controlled repair phase without losing Zeus's money-path semantics.

The repair phase must convert audit findings into small, independently verifiable packets. The first wave is provenance and observability work, not live trading behavior changes.

## Baseline Boundary

The source worktree `/Users/leofitz/.openclaw/worktrees/zeus-object-invariance-mainline-next-2026-05-08` was dirty when this branch was created. This repair worktree is based on the committed HEAD `cda80d3e`; uncommitted changes from the source worktree were not copied.

Any implementation packet that depends on those uncommitted changes must explicitly merge/cherry-pick/rebase them after conflict-first inspection.

## Safe Implementation Queue

| Priority | Packet | Initial action | Why first |
|---|---|---|---|
| 1 | S1 market source-proof persistence | Design and persist source-contract evidence parsed by scanner. | Narrowest blast radius; provenance-only; no trading behavior change. |
| 2 | S2 lifecycle funnel report | Add read/report surface for evaluated -> selected -> rejected/submitted -> filled -> learned. | Converts empty trade tables into certified lifecycle state. |
| 3 | S3 calibration serving status surface | Report forecast readiness vs calibration readiness by serving bucket. | Prevents OpenData readiness from being mistaken for calibrated trade readiness. |
| 4 | S4 price/orderbook evidence report | Report price-only vs executable-snapshot-backed evidence modes. | Clarifies replay/economics authority without changing executor behavior. |

Not first-wave implementation: hourly observation contract, source-truth promotion-domain changes, OpenData Platt promotion/refit, or order submission behavior changes.

## Required Workflow Per Packet

1. Freeze packet scope.
   - Create or update a packet-local plan/progress record.
   - Name the structural decision being repaired.
   - State what is out of scope.

2. Run topology navigation before edits.
   - Use the exact intended files.
   - If topology is advisory-only, narrow intent/files until it admits the slice or record the blocker and stay docs-only.
   - For pipeline-impacting source/test changes, run task boot profiles before implementation.

3. Refresh reality evidence.
   - Read current code, current DB schemas/counts, and current authority docs.
   - Use read-only DB opens for evidence queries.
   - Distinguish current fact, historical evidence, derived report, and operator decision.

4. Write relationship tests before implementation when source changes are required.
   - Tests must prove cross-module invariants, not only function outputs.
   - Example invariant: scanner parsed source contract -> persisted fact -> audit query can reconstruct configured-vs-market source agreement.

5. Implement the smallest structural change.
   - Prefer existing helpers, schemas, writer patterns, and status surfaces.
   - Do not add behavioral trading changes to observability packets.
   - Do not mutate production DBs unless the packet explicitly authorizes it.

6. Verify with focused tests and evidence queries.
   - Run packet-specific tests.
   - Run a narrow status/query check proving the new surface answers the audit question.
   - Capture known unrelated failures separately.

7. Review and close.
   - Use a code-review stance for changed files.
   - Record residual risk and next packet.
   - Do not merge or archive packet evidence unless explicitly asked.

## Repair Launch Criteria

Do not start source/test implementation until all launch criteria are true:

- Packet has exactly one structural decision.
- Topology admits the exact source/test/doc file list, or the packet remains docs-only.
- Current code evidence has been read from disk, not stale editor cache.
- Current DB/schema evidence is read-only and timestamped.
- At least one relationship invariant is written as a test plan before code edits.
- A stop condition is named in advance.
- A verifier can explain how the repair preserves Zeus's money path.

If any criterion fails, stay in planning/evidence mode.

## Subagent Workflow

Use subagents to preserve main-session attention and reduce context loss.

| Stage | Agent | Model tier | Output required |
|---|---|---|---|
| Locate surfaces | `Explore` | fast/low tier | File/function/table map with evidence. |
| Structural boundary | `architect` | opus when cross-module | Whether the packet is provenance, reporting, or behavior. |
| Test design | `test-engineer` | sonnet | Relationship test plan and minimal fixtures. |
| Implementation | `executor` | sonnet | Small code/test patch scoped to admitted files. |
| Review | `code-reviewer` | sonnet/opus by risk | Findings first; focus on money-path regressions. |
| Verification | `verifier` | sonnet | Evidence that acceptance criteria are actually met. |

Rules:
- Broad grep/search goes to `Explore` first.
- Implementation agents receive exact allowed files, forbidden files, acceptance criteria, and tests to run.
- Review agents get the diff plus the original invariant, not just changed code.
- No worker may widen from observability/provenance into live order behavior without returning for operator approval.

### Subagent Dispatch Contracts

Every subagent prompt must include:

- Worktree path and branch.
- Read/write permission: read-only, tests-only, or exact editable files.
- Money-path boundary under repair.
- Authority surfaces to consult.
- Forbidden behaviors.
- Required output format.

Use these dispatch shapes:

```text
Explore: READ-ONLY. Map files/functions/tables for <packet>. Return exact evidence and unresolved questions. Do not modify files or DBs.
```

```text
test-engineer: READ-ONLY until plan is accepted. Design relationship tests for <invariant>. Return fixture shape, assertions, and existing test files to extend.
```

```text
executor: WRITE only admitted files. Implement the smallest patch that satisfies accepted tests. Do not widen scope. Report commands run and residual risk.
```

```text
code-reviewer/verifier: Review changed files against original invariant and Zeus money path. Findings first; state unreviewed surfaces explicitly.
```

### Subagent Handoff Template

Use this exact shape when dispatching workers:

```text
WORKTREE: /Users/leofitz/.openclaw/worktrees/zeus-alignment-safe-implementation-2026-05-08
BRANCH: repair/alignment-safe-implementation-2026-05-08
MODE: READ-ONLY | WRITE_ADMITTED_FILES_ONLY
PACKET: <S1/S2/S3/S4>
MONEY-PATH BOUNDARY: <contract/source/forecast/calibration/edge/execution/monitoring/settlement/learning>
STRUCTURAL DECISION: <one sentence>
CURRENT AUTHORITY: root AGENTS.md + topology route + packet TASK/PROGRESS + named source docs/tests
ALLOWED FILES: <exact list after topology admission>
FORBIDDEN FILES: all other source/runtime/DB/config files
REALITY EVIDENCE REQUIRED: <DB/schema/current fact/query>
RELATIONSHIP INVARIANT: <assertion that must survive across modules>
OUTPUT: evidence, uncertainty, recommended next action, no broad refactors
```

Worker output is not accepted unless it names what it did not inspect. Silent broad confidence is treated as a failed review.

## Evidence Ledger Template

Every implementation packet maintains this ledger before code edits:

| Evidence class | Required question | Example for S1 |
|---|---|---|
| Code boundary | Where is the relationship created? | `market_scanner._check_source_contract()` parse/validate boundary. |
| Persistence boundary | Where should durable truth be written? | Existing DB writer/migration pattern for market facts. |
| Runtime current fact | What does current DB show? | Active market/source rows and whether source proof is already persisted. |
| Authority doc | Which law says the repair is valid? | `AGENTS.md` money path + audit F24. |
| False-positive boundary | What would prove no repair is needed? | Existing durable source-contract archive found. |
| Test invariant | What cross-module property must hold? | Parsed source proof can be reconstructed from persisted fact without refetching Gamma. |

## Preflight Output Standard

Preflight is complete only when it produces:

- topology route result;
- Explore surface map;
- test-engineer relationship-test proposal;
- reality evidence query or reason it is unavailable;
- go/no-go verdict for implementation;
- exact editable-file proposal for the implementation slice.

No source implementation begins until all six items exist.

## S1 Topology Routing Finding

S1 can be admitted, but only when routed as a forward-substrate producer packet rather than a raw-provenance schema packet or generic source-contract packet.

Verified route:

```bash
python3 scripts/topology_doctor.py --navigation \
   --task "Phase 5 forward substrate producer: point-in-time market source proof facts from already-parsed Gamma source_contract evidence; no schema migration; no production DB writes" \
   --write-intent edit \
   --files src/data/market_scanner.py src/state/db.py tests/test_market_scanner_provenance.py
```

Verified outcome:
- `profile: phase 5 forward substrate producer implementation`
- `admission_status: admitted`
- admitted files: `src/data/market_scanner.py`, `src/state/db.py`, `tests/test_market_scanner_provenance.py`
- risk tier: T4
- gate budget: explicit operator-go, dry-run evidence, apply guard, rollback plan

Negative routing evidence:
- `U2 raw provenance schema` selects `r3 raw provenance schema implementation`, but admits `src/state/db.py` only and rejects `market_scanner.py` plus the provenance test as out of scope.
- `source contract auto conversion runtime` admits `market_scanner.py` plus the provenance test, but deliberately excludes DB/schema surfaces.
- Natural wording such as `forward substrate producer implementation` can still fall back to `generic` because high-fanout files need exact profile phrases.

Topology defect classification: phrase coverage gap, not missing file authority. The existing producer profile can cover S1; this packet adds S1-specific phrases such as `market source proof facts`, `source_contract audit facts`, and `market source-proof persistence` to avoid generic fallback.

## Zeus/Reality Alignment Gates

Every packet must answer these before code changes:

- Which money-path boundary is being repaired?
- What is the source of truth: code, DB, venue, weather source, market resolution, or operator decision?
- What relationship test proves the boundary now preserves meaning?
- What false-positive boundary prevents over-repair?
- What live-money behavior remains unchanged?
- What evidence would prove the repair is unsafe and stop the packet?

Reality alignment rule: code evidence is not enough. For each packet, pair code reads with at least one current-data or current-runtime evidence surface unless the packet is docs-only. Examples:

- S1 source-proof persistence: scanner parser code plus current market/source rows.
- S2 lifecycle funnel: writer code plus current `zeus_trades.db` stage counts.
- S3 calibration status: evaluator/manager code plus current Platt/source-run coverage.
- S4 price evidence: runtime snapshot/venue code plus current price/orderbook table counts.

The repair is valid only if the new structure makes the failure category observable or unconstructable. A patch that only fixes one sampled row or one display line is insufficient unless the packet is explicitly scoped that narrowly.

## S1 Preflight Workflow

Before S1 implementation:

1. Run topology with the likely S1 files, starting from `src/data/market_scanner.py`, state DB schema/writer files, and focused tests.
2. Ask `Explore` to map source-contract parse output, persistence boundary, and table creation/migration conventions.
3. Ask `test-engineer` to propose relationship tests: Gamma source proof -> parser -> persisted fact -> audit query reconstructs configured-vs-market source agreement.
4. Decide storage shape: table vs artifact. Prefer table only if it follows existing state DB migration/writer patterns.
5. Implement persistence without changing scan acceptance/rejection behavior.
6. Verify with tests and a fixture query that reconstructs one WU, one HKO, one NOAA/Ogimet, and one mismatch/quarantine-like source proof.

S1 cannot include source migrations, station remapping, settlement relabeling, or Gamma historical refetch unless the operator opens a separate packet.

## First Packet Recommendation

Start with S1 market source-proof persistence.

S1 is safe because the scanner already parses and validates source contracts. The packet only persists evidence already known at scan time, enabling future audits without changing market eligibility or settlement truth.

## Stop Conditions

- Topology route stays advisory-only for source/test files after narrowing intent.
- Current DB/code evidence contradicts the audit premise.
- A packet requires production DB mutation not authorized by the operator.
- A repair would change live order/exit behavior while the packet is scoped as observability/provenance.
- Relationship invariant cannot be expressed as a test.
