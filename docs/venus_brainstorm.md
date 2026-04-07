# Venus Sensing Architecture — Three-Way Brainstorm

Participants: Fitz, Zeus-side Claude (Opus), Venus
Date: 2026-04-06

---

## Zeus-side opening position

### The problem with the current design

venus-architect designed 8 fixed diagnostic checks (`diagnose_truth_surfaces.py`). These checks detect the 8 problems we ALREADY found. They are useful as baseline monitoring — but they are a script treadmill, not self-awareness.

§三 says:
> Security guard 巡逻→发现→报警。下次同样的问题来，还要再巡逻→再发现→再报警。
> Immune system 遇到病原体→产生抗体→永久免疫这一类病原体。

The 8 checks are a security guard. They patrol known routes. The 9th problem — the one we haven't imagined yet — passes through undetected.

### What Venus should actually do

Venus's unique capability (vs RiskGuard scripts) is **reasoning about relationships between data sources that were never designed to be compared.**

Examples of what no fixed check can do:
1. Read `positions-paper.json` + query Polymarket API for wallet's actual positions → compare → any divergence is a finding
2. Read `trade_decisions` timestamps + read `settlements` timestamps → notice "Zeus traded 33 markets for Apr 1-5 dates but settlements stopped at Mar 30" → no check was written for this pattern
3. Read `settlement_edge_usd` column definition + read how P&L is displayed to operator → notice the semantic mismatch that caused me to give Fitz wrong P&L data

These require a reasoning entity, not a script. A script can only check what the script author already knew to check.

### Three questions for Venus

1. **What data sources should Venus cross-reference?** The truth surface chain is: trade_decisions → position_current → position_events → risk_state → status_summary → Venus. But Venus could also read: Polymarket API (live positions, order status, market resolution), chain state (on-chain token balances), and historical patterns (is this week's behavior consistent with last week's?).

2. **How does Venus decide what to investigate?** Fixed checks are necessary but not sufficient. What triggers Venus to look deeper? Anomaly detection on metrics over time? Fitz's questions? Unexpected silence (a metric that should change but hasn't)?

3. **What form should Venus's antibodies take?** When Venus discovers a new divergence category, what does it produce?
   - A new test in `tests/` (permanent CI check)?
   - A new RealityContract in `config/reality_contracts/` (TTL-based)?
   - A new check in `diagnose_truth_surfaces.py` (script-level)?
   - A code change that makes the divergence impossible (type system / contract)?
   - All of the above, routed by severity?

### My proposed hierarchy

```
Layer 0: Type system (makes wrong code unwritable)     — AlphaDecision, ExecutionPrice, etc.
Layer 1: CI tests (catches regressions)                — test_*.py
Layer 2: Runtime contracts (catches drift)              — RealityContract, ProvenanceRegistry  
Layer 3: Fixed diagnostics (catches known failure modes) — diagnose_truth_surfaces.py
Layer 4: Venus reasoning (catches unknown unknowns)     — cross-source analysis, anomaly detection
```

Layers 0-3 are mechanical. Layer 4 is where Venus lives. Each Venus finding should **descend** into Layers 0-3 as an antibody — so Venus never needs to find the same category twice.

### What I need from Venus

Tell me:
- What does Venus actually see today? (Which files, APIs, databases?)
- What has Venus already caught that Zeus didn't know about?
- What would Venus check if it had 5 minutes of reasoning time per heartbeat?
- What data would Venus need that it currently can't access?

---

## Venus's response

I agree with the overall direction: Venus should not be another fixed-check script. Its job is cross-source semantic comparison — detecting when two truths are both individually plausible but jointly inconsistent.

### What Venus should see today

At minimum, Venus should cross-reference:
- `status_summary-{mode}.json`
- `positions-{mode}.json`
- `risk_state-{mode}.db`
- `control_plane-{mode}.json`
- `reality_contract_state.json`
- `zeus.db` tables that carry decision, settlement, and audit truth
- chain / Polymarket API truth when live access is available

The most valuable findings come from relationship checks, not point checks:
- summary vs positions
- positions vs chain
- risk state vs control plane
- decision log vs settlement / chronicle
- paper vs live isolation
- runtime truth vs contract freshness

### What should trigger deeper reasoning

Venus should not scan everything all the time. It should deepen when one of these happens:
- a semantic mismatch appears between surfaces
- a relationship that should always exist disappears
- a metric changes in a way that contradicts adjacent truth
- a contract goes stale while the system keeps behaving as if it is fresh
- Fitz asks a targeted question

### What Venus should produce

Venus findings should descend into one of four durable forms, in this order:
1. type / schema constraint that prevents the bad state from being represented
2. invariant test that makes the regression fail in CI
3. runtime contract or gate that detects the drift in operation
4. fixed diagnostic only when the issue is already well understood

In other words: Venus finds the new category once; engineering turns it into an antibody so Venus never has to rediscover it.

### Highest-value current focus areas

Based on recent work, the strongest candidates for Venus attention are:
- paper/live truth separation
- summary-layer soft gaps being mistaken for hard failures
- reality contract freshness vs healthcheck semantics
- settlement / claim / archive lifecycle integrity
- stale compatibility surfaces that still look authoritative

### Delegation stance

I do not need to personally implement every fix. The correct workflow is:
- Venus identifies the semantic fault line
- ACP/Claude designs the durable fix and tests
- I review the proposed antibody and make sure it encodes the relationship, not just the symptom

---

## Fitz's synthesis

The sensing layer is now useful because it can see truth-surface drift directly, but it is not yet a finished immune loop. The current report makes the important failures visible: canonical truth still falls back to `working_state_fallback`, settlement freshness is stale, and decision/position surfaces still disagree. The next step is not more observation for its own sake; it is to convert the highest-signal mismatch into a durable antibody — a test, contract, or schema change that makes the category of failure hard to repeat.

The practical standard is this: Venus is only an antibody when it stops re-describing a known mismatch and instead leaves behind a machine-enforceable constraint. Fixed diagnostics are useful as scaffolding, but the real value is in the downstream test or contract that makes the same drift category impossible to miss next time.
