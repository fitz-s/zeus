# critic-carol cycle-1 (P8) durable learnings

**Continuity**: Inherits critic-beth's 3-cycle learnings (P6/P7A/P7B). Fresh spawn 2026-04-18; not a continuation of critic-beth's context.

## Inherited from critic-beth

- L0.0 peer-not-suspect discipline
- Two-seam principle (write-side + read-side always audited)
- P3.1 guard-removal vocab grep methodology
- Baseline-restore grep for regression math validation
- Pre-commitment predictions before diving into diff
- Deferral-with-rationale principle (items left for future phase need written rationale)
- Broken-monkeypatches silent-GREEN pattern detection
- Mirror-test heuristic (write-side seam requires read-side test)
- First-try PASS template (P7B reference)
- Extended P3.1 vocabulary: `_requires_explicit_|_must_specify_|_no_default_`
- P3.1 cycle-3 candidates: `_refuses_until_|_latent_|_silent_|_accidental_green`

## New learnings from P8

### 1. Empirical probe for degraded-cycle paths
When a commit removes a guard and replaces it with a graceful-degradation branch, the MINIMAL review is to **actually run the branch with a synthetic fixture and dump the full `summary` dict**. Static review misses observability gaps (missing reason codes, missing persistence). Pattern:

```python
# /tmp/probe_<finding>.py
import sys; sys.path.insert(0, '.')
# stub minimal fixtures
# call target function
# inspect returned summary / side effects
```

Surfaced both MAJOR-1 (`entries_blocked_reason` missing) and MAJOR-2 (`risk_state.db` not persisted) in P8 review.

### 2. Text-match antibodies are latent translation-loss bombs
A test that asserts `"specific error message" in str(exc)` locks in a pre-refactor stable artifact but NOT the structural invariant. Prefer `"no RuntimeError of any kind escapes branch X"` over `"RuntimeError matching string Y escapes branch X"`.

This is orthogonal to critic-beth's silent-GREEN pattern — a different failure mode (future false-negatives on reworded guards) that slips past guard-removal reviews.

### 3. "Call-count + no-raise" antibody pair is stronger than either alone
R-BQ.1 (no-raise) + R-BQ.2 (exactly-one-call-to-tick) work together. Either alone could silent-pass. Pair gives redundant coverage.

**New pattern**: Require PAIRED antibodies for each guard removal — one NEGATIVE (guard did not fire) + one POSITIVE (replacement fired).

### 4. Observability drift is a distinct severity class
Entries being correctly blocked but `entries_blocked_reason` being absent is not a functional bug (entries are blocked) but IS a Fitz-Four-Constraint-#3 (Immune System) concern. Operators rely on reason-coded fields for runbook automation. **Treat as MAJOR, not MINOR.**

### 5. Cross-DB state drift is a systemic concern
`tick_with_portfolio` computes but doesn't persist → `status_summary.json` and cycle `summary` dict disagree. Any future `tick_*` variant that reads risk_state.db but doesn't write back creates two-value observability. **Flag this class eagerly during review of any DT#N refactor.**

### 6. `save_portfolio(degraded)` is a sneaky provenance trap
If `_reconcile_pending_positions` marks `portfolio_dirty=True` in a degraded cycle, the degraded state (`authority="degraded"`) gets JSON-written to positions-cache. Truth stamp drops to UNVERIFIED. Future cycles read this back. Read-only claim in DT#6 contract needs nailing down.

### 7. Contract-first + small-surface + pre-commitment = first-try PASS pattern
P7B + P8 both first-try PASS under Gen-Verifier. Key ingredients:
- ≤2 seams per commit
- ≥3 antibodies per seam (ideally a pair-based antibody design)
- Explicit deferral list in contract
- Contract lists hard-forbidden moves explicitly

**Future critics should check for all four in the contract before beginning review** — absence of any is a red flag. First-try PASS is earnable, not accidental.

## Signals to watch in future phases

- **Phase that rewires risk_state / status_summary / monitor_refresh read lanes**: check for the `tick_with_portfolio` persistence gap fix.
- **Phase that adds new DATA_DEGRADED producers**: check that `entries_blocked_reason` tuple includes it.
- **Any phase that adds metric-aware SQL filter (P9 B093 half-2)**: verify both SQL WHERE clause AND cache-key changes.
- **Any commit that removes a `raise` guard**: look for text-match antibody anti-pattern in replacement tests.

## Methodology trend observation

P7B + P8 = 2 consecutive first-try PASSes under Gen-Verifier. Pattern has converged on predictable low-friction review when contracts are tight and surface is small.

**The critic's role is shifting from "find the flaw" to "surface P9 forward-log items".**

Maintain pre-commitment discipline to keep finding-quality high — the risk of this pattern is **complacency**. When first-try PASS streak reaches 3+, reintroduce adversarial mode (6-tier hypothesis ordering, worst-case probes) even on apparently-clean commits to prevent blind spots.

## Meta — critic rotation hygiene

critic-beth retired after 3 cycles to prevent over-familiarity. critic-carol inherits her methodology file but brings fresh pre-commitment predictions. **This rotation is valuable**: P5 predictions hit 5/5 for beth, P8 predictions hit 5/5 for carol — both high-quality but the actual findings differ (beth found Python 3.14 fix-pack issues; carol found observability drift). Rotation finds orthogonal issue-classes.

Suggest: **next rotation at end of P9** (critic-carol → critic-dave). Three cycles per critic keeps the disk-durable learnings compounding while preventing over-familiarity. Cost: the onboarding read (critic-beth's 3 learnings docs ≈ 500 lines) is worth the orthogonal finding-class advantage.

## Addendum — Write/Edit blocked

Same constraint as critic-beth cycle 3: `oh-my-claudecode:critic` Opus subagent has Write/Edit blocked this invocation. Team-lead persisted verdict + learnings on critic's behalf. Consider flagging at OMC agent-definition level: critic's wide-review deliverables ARE disk artifacts, tools should align.

---

*Authored*: critic-carol (Opus, sniper-mode Gen-Verifier), 2026-04-18 cycle 1
*Preserved-by*: team-lead (Write/Edit on critic's behalf)
*Next cycle opens*: P9 (absorbs P8 observability forward-log + LOW limited activation + DT#5/#2/#7)
