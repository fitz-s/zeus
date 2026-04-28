# Architect Report — Ultimate Plan 2026-04-26

Reviewer: architect
HEAD: 874e00c
Frame: Fitz High-Dimensional Thinking (Constraints #1-#4)

## STRENGTHS

1. **Genuine K-collapse (Constraint #1).** §8.3's 17 transitions decompose to 4 already-shipped + 2 typing-only + 11 NET_NEW under K=5 (Mid) + K6 (Down). ALTER consolidation is real: up-04 absorbs mid-02's 3 signing cols → ONE 15-col ALTER. `merged_into: up-04` is the right epistemic move. F-001 row-state CLOSED correctly distinguished from F-001 payload-bytes RESIDUAL.
2. **Type depth at boundaries.** `OrderSemantics.for_market()` + `UnknownMarketError` fail-closed + `PrecisionAuthorityConflictError` make wrong code largely unconstructable, not just runtime-checked. NC-NEW-C semgrep allowlist (3 callers) closes the bypass channel that pure-Python dispatchers leak.
3. **Triple-belt at signing seam (X1).** AST-shape contract (NOT line numbers) is a Constraint #2 antibody: encoded in 4 tests across 3 modules, survives line drift and SDK reshape. Memory `feedback_zeus_plan_citations_rot_fast` honored.
4. **Provenance chain audited (Constraint #4).** Q-NEW-1 disputed → resolved by direct on-chain `eth_call` with raw hex + block height + ABI decode on disk. Earlier "marketing label" ruling overturned by ground truth. Correct provenance-interrogation template.
5. **Defensive-deploy ordering.** up-06 BEFORE up-04 (consumers reject UNVERIFIED before backfill creates UNVERIFIED rows). Non-obvious; prevents the leak window most plans miss.
6. **Append-only as defense-in-depth.** NC-NEW-B = SQLite triggers + semgrep + Python encapsulation (3 layers). Database enforces, not reviewer attention.

## GAPS

1. **NC-NEW-D allowlist is file-scope, not function-scope.** Allowlist names `cycle_runner.py`; any future callsite in that file slips past. Antibody catches imposter files but not imposter functions in allowlisted files. Needs symbol-scope filter or runtime sole-caller assertion.
2. **NC-NEW-F (heartbeat no-`os._exit`) is honestly weak.** Plan admits `pattern-not` struct-match has limits. Runtime test parallel catches it, but the semgrep belt is documentation, not enforcement.
3. **Q-FX-1 dual-gate is process, not type.** Env var + evidence-file presence proves a decision was made. Does NOT prevent wrong classification at the call site. A typed `FXClassification` enum imported into accounting paths would make misclassification a TypeError.
4. **Citation rot persists across 4-6 week execution.** R3L3 grep-verified at 874e00c, but most cards cite line numbers. Mitigation (10-min re-grep, X1 AST-shape) is partial. Memory L20+L22 imply ~20-30% premise mismatch is the baseline.
5. **mid-05 creates findings without actuator.** `exchange_reconcile_findings` records ghost-orders/orphans but findings→action loop is Wave-2-deferred. Detection without remediation is observability, not authority — antibody-without-actuator liability.
6. **PARTIAL_FILL monotonicity is runtime-tested only.** SQLite CHECK cannot express "filled_size ≥ max(prior events for command_id)" without a trigger reading prior rows. Plan picks runtime test = instance-killer, not category-killer.

## FATAL_FLAWS_IF_ANY

None. Two near-fatal risks flagged honestly with mitigations:
- X1 seam depends on V2 SDK NOT introducing `create_and_post_order`. Antibody catches it; SDK-upstream breakage is operator-coordination (Q1 + Q-HB + impact_report rewrite).
- INV-29 amendment requires planning-lock receipt for mid-03. Receipt-bypass via amend is technically possible but unlikely without intent.

## VERDICT

**APPROVE_WITH_CONDITIONS**

1. Tighten NC-NEW-D to function-scope (or add runtime test asserting the inline emitter is the SOLE caller within cycle_runner.py).
2. Add `FXClassification` enum imported by accounting/PnL surfaces so misclassification is TypeError, not string mismatch.
3. Schedule 10-min grep re-verification gate immediately before each contract lock (memory L20). Wave A→B→C→D spans weeks; citations WILL rot.
4. Open Wave-2 findings→action loop scope on `exchange_reconcile_findings` BEFORE mid-05 ships, to avoid antibody-without-actuator.

The plan identifies K=5 (Mid) + 7 boundary (Up) + 1 transport (Down) structural decisions. It does not rebrand 31 patches as 13 decisions; the K-collapse is substantive. Antibody quality is mostly category-killing (types + triggers + AST shape), with 2-3 instance-killers acknowledged honestly. Approve once the four conditions land in slice cards before Wave A begins.
