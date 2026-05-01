# Ultimate Plan — Multi-Review Synthesis

Created: 2026-04-26
HEAD anchor: `874e00cc0244135f49708682cab434b4d151d25d`
Reviewers: architect (opus), critic (opus), explore (sonnet), scientist (opus), verifier (sonnet)
Reports: `evidence/multi_review/{architect,critic,citation_verification,trading_correctness,feasibility}_report.md`

---

## TL;DR

| Reviewer | Verdict | Top concern |
|---|---|---|
| architect | APPROVE_WITH_CONDITIONS | NC-NEW-D file-scope-not-function-scope; Q-FX-1 process-not-type |
| critic | **REVISE** | 80% citation drift on sample; WebSocket axis-45 missing entirely |
| explore | 32/57 NONE, 17 LINE_DRIFT, **2 SEMANTIC_MISMATCH** | down-07 `polymarket_client.py:353` doesn't have Q-FX-1 gate; cycle_runner.py:~364 wrong NC-NEW-D anchor |
| scientist | **CONDITIONAL** | Plan is live-readiness gate, not dominance plan; 0/20 cards improve edge; forecast/calibration/learning untouched; ≥5 Apr26 P0 tests unowned |
| verifier | CONDITIONALLY FEASIBLE | 120.5h is optimistic floor (realistic 135-145h); mid-03 planning-lock not machine-readable; mid-05 SDK gap |

**Aggregate verdict: REVISE-THEN-APPROVE.** The plan is structurally sound (K-collapse real, antibodies mostly category-killing) but has three classes of debt that must close before Wave A starts:

1. **Premise rot**: 17/57 LINE_DRIFT + 2 SEMANTIC_MISMATCH on file:line citations.
2. **Scope holes**: WebSocket coverage, deterministic-fake-CLOB, ≥5 Apr26 P0 behavioral tests, forecast/calibration/learning legs of the money path.
3. **Process-vs-type gates**: NC-NEW-D file-scope; Q-FX-1 string-vs-enum; mid-03 planning-lock not in YAML.

---

## Critical findings ranked

### S0 — must fix before Wave A

1. **Citation drift 80% on critic's sample, 30% on explore's broader sample**, vs L20 grep-gate's 20-30% baseline. Explore confirms 2 SEMANTIC_MISMATCH:
   - `down-07` cites `polymarket_client.py:353` for Q-FX-1 dual-gate runtime check, but line 353 is `def redeem` (different function).
   - `cycle_runner.py:~364` cited as NC-NEW-D allowlist anchor but contains `get_force_exit_review()`, not the proxy emission site.
   - Critic-sampled `polymarket_client.py:268` (claimed import line) is actually `def get_open_orders` — off by ~79 lines (the v2_preflight insert shifted everything).
   These are LOAD-BEARING citations; the plan cannot ship Wave C until corrected.

2. **Apr26 axis-45 (User WebSocket / FAIL/S1, "very high" confidence) NOT addressed** — zero `WebSocket|user.ws` hits across all 20 yamls. mid-05's polling sweep self-acknowledges miss-windows. Critic flags this as the biggest scope gap.

3. **EIP-712 signing determinism is unverified** but mid-02's `test_signed_order_hash_unique_per_idempotency_key` antibody depends on it. If signing has fresh entropy per call, F-003 closure is theatrical.

4. **mid-01 ownership ambiguity unresolved in YAML.** Prose says cycle_runner-as-proxy locked; YAML lists riskguard-direct AND cycle_runner-proxy.

### S1 — must fix before Wave B / production cutover

5. **Apr26 §F-012 fix design (deterministic-fake-CLOB + failure injection + restart simulation + resolved-market fixtures) NOT minted as a slice.** mid-06 has it as "recommended investment in conftest" only. Apr26 axis-31 (paper/live parity, FAIL/S1) and axis-49 ("failure-mode test suite") have **zero slice ownership**.

6. **0/20 cards improve edge.** Forecast / calibration / learning legs of the money path entirely absent. Apr26 Phase 4 (settlement corpus, high/low split, DST resolved fixtures) silently dropped. Mislabeling: plan is "live-readiness gate", not "dominate live market" as prompt promised.

7. **NC-NEW-D file-scope not function-scope.** Any future caller in cycle_runner.py slips past. Need symbol-scope filter.

8. **Q-FX-1 dual-gate is process not type.** Env var + evidence-file presence proves a decision was made; doesn't prevent misclassification at call sites. Add `FXClassification` enum.

9. **mid-03 planning-lock gate not machine-readable.** YAML `depends_on` won't surface the block; topology_doctor / aggregator can't see it. Manual executor awareness required.

10. **mid-05 `get_trades` SDK gap** — if py-clob-client-v2 lacks the method, sweep degrades to open_orders+positions only and F-006 closure weakens.

### S2 — should address (≥5 unowned Apr26 P0 tests)

Per scientist + critic:
- duplicate-submit idempotency (timeout-retry path)
- rapid sequential partial fills (3 fills <1s overlap, trade_id de-dup)
- RED cancel-all behavioral (mid-01 covers RED-as-authority, not RED-as-action)
- market-close-while-resting
- WS-resubscribe-recovery (mooted by axis-45 missing)

### Hidden coupling

- **mid-02 ↔ up-04 schema constraint contract.** mid-02 says `signed_order_hash` MUST be NON-NULL before `place_limit_order` returns. up-04's column is `DEFAULT NULL`. Legacy-row backfill will write NULL and trip mid-02's antibody on innocent historical rows. No cross-yaml CHECK-constraint contract.
- **mid-05 detection without actuator.** `exchange_reconcile_findings` records ghost-orders but findings→action loop is Wave-2-deferred. Detection without remediation is observability, not authority.
- **PARTIAL_FILL monotonicity runtime-only.** SQLite CHECK can't express it without a trigger reading prior rows. Picked runtime test = instance-killer, not category-killer.

---

## Recommended plan revisions

Three new slices to mint + four amendments to existing cards before Wave A starts.

### NEW SLICES

- **mid-07 `WS_OR_POLL_SUFFICIENCY`** — User WebSocket subscription OR documented polling loop with fill-miss-window measurement. Closes Apr26 axis-45 + axis-24. Estimated 12-18h. Blocks F-006 full closure.
- **mid-08 `FAILURE_INJECTION_SUITE`** — deterministic fake CLOB + failure-injection harness + restart simulation + 5+ Apr26 P0 behavioral tests as listed in scientist + critic reports. Estimated 14-20h. Owns axis-31, axis-49, the 5 unowned P0s.
- **up-08 `FROZEN_REPLAY_HARNESS`** — bit-identical replay (P_raw → Size) before/after Wave A+B against fixture portfolio. Antibody against silent calibration-schema drift from up-04 ALTER. Estimated 8-12h. Per scientist's PROBABILITY_CHAIN_FROZEN_REPLAY recommendation.

### AMENDMENTS

1. **GREP_GATE_RECHECK** — re-run citation grep-gate on all 20 cards within 24h of Wave A start. Auto-fix line drift via line-anchor symbols (function name + nearest stable comment) instead of bare line numbers. Memory `feedback_grep_gate_before_contract_lock`.
2. **EIP-712 determinism evidence** — mint `evidence/mid/eip712_signing_determinism_2026-04-26.md` BEFORE mid-02 contract lock. If non-deterministic, redesign F-003 closure.
3. **mid-01 yaml ownership lock** — pick cycle_runner-as-proxy in `depends_on` field; remove riskguard-direct option from yaml.
4. **NC-NEW-D function-scope tightening + FXClassification enum + mid-03 planning-lock pseudo-dep** — three small encoding fixes per architect report.

### LABELING

- Rename plan in §3 from "Zeus dominates live market" to "**Zeus live-readiness gate (Wave A+B+C+D+E) → readiness for dominance experiments**". Honest scope. Add §4 "**Post-readiness dominance roadmap**" listing the 4 missing money-path legs (forecast / calibration / edge-monitoring / learning) as deferred packets, not silently dropped.

---

## Updated plan-coverage estimate

Pre-review: claim "75% routed-into-slice".

Post-review:
- Live-readiness scope (S0/S1 execution-correctness gates): **65-70% covered after 3 new slices** (~96-130h additional). Up to ~85% with grep-recheck + amendments.
- Live-dominance scope (edge improvement + alpha monitoring + learning loop): **0% covered**. Defer to a separate "Dominance Roadmap" packet.

---

## Action items in priority order

1. Re-run citation grep-gate across 20 cards (24h deadline before Wave A).
2. Mint up-08 FROZEN_REPLAY_HARNESS, mid-07 WS_OR_POLL_SUFFICIENCY, mid-08 FAILURE_INJECTION_SUITE slice cards.
3. Capture EIP-712 determinism evidence; if non-deterministic, redesign mid-02 antibody.
4. Lock mid-01 ownership in YAML.
5. Tighten NC-NEW-D + add FXClassification enum + encode mid-03 planning-lock as pseudo-depend.
6. Re-label plan §3 as live-readiness gate + add §4 Dominance Roadmap section.

After these 6 items land, the plan upgrades from REVISE → APPROVE_WITH_CONDITIONS → APPROVE for Wave A start.

---

## Process retrospective

Multi-review caught **80% citation drift** that the in-debate teammates' own grep-gate did NOT catch. Lesson: in-debate grep-gates within 10-min windows are too short for compound drift; compounding effect over 12+ converged rounds means each round's "verified" status decays. Adding to memory: **multi-angle parallel review at packet close is mandatory before declaring DEBATE_CLOSED**, not optional.
