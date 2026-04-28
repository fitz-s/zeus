# Skills Matrix — R3 Phase × Step × Skill

Maps every R3 phase × implementation step to the OMC skill or sub-agent that
should be invoked. Cold-start agents follow this when they hit each step.

Memory cross-refs:
- `feedback_lifecycle_decomposition_for_execution` (R3 structure)
- `feedback_multi_angle_review_at_packet_close` (5-angle review at wave close)
- `feedback_default_dispatch_reviewers_per_phase` (auto-dispatch critic + code-reviewer)
- `feedback_critic_prompt_adversarial_template` (10 attacks; never "narrow scope")
- `feedback_subagent_for_oneoff_checks` (subagents for spot checks)

## §1 Universal step-skill mapping

These apply to EVERY phase regardless of phase letter:

| Step | Skill / Sub-agent | Model | Why |
|---|---|---|---|
| Boot — read R3 packet | (agent direct, no skill) | inherits | trust agent's reading + Read tool |
| Boot — drift check | Bash (run script) | inherits | mechanical verification |
| Boot — ambiguity surface | `deep-interview` | opus | force precision; mathematical ambiguity gate before code |
| Implementation — file navigation | `explore` | sonnet | find downstream callers, related symbols |
| Implementation — write code | `executor` | opus for HIGH risk, sonnet otherwise | focused implementation |
| Implementation — multi-file refactor | `general-purpose` | sonnet | wider context |
| Implementation — schema design (read-only) | `architect` | opus | structural reasoning before locking |
| Tests — TDD + flake hardening | `test-engineer` | sonnet | test strategy + integration coverage |
| Tests — verify spec coverage | `verifier` | sonnet | evidence-based completion |
| External SDK / docs | `document-specialist` + WebFetch | sonnet | sole source for V2 SDK, TIGGE, Polymarket docs |
| On-chain RPC | sub-agent + Bash + curl | sonnet | direct eth_call (memory `feedback_on_chain_eth_call_for_token_identity`) |
| Pre-merge — review | `code-reviewer` | sonnet | severity-rated review |
| Pre-merge — adversarial | `critic` | opus | 10-attack template |
| Pre-merge — security | `security-reviewer` | opus | OWASP / secrets / unsafe |
| Pre-merge — completion check | `verifier` | sonnet | evidence-based |
| Pre-merge — simplify | `code-simplifier` | sonnet | reduce complexity post-MVP |
| Memory write | `remember` skill | inherits | persist phase-specific learnings |
| Wave close — multi-review | architect + critic + explore + scientist + verifier (parallel) | opus + opus + sonnet + opus + sonnet | per `feedback_multi_angle_review_at_packet_close` |

## §2 Per-phase additional skill recommendations

### Z0 plan-lock (low risk, doc-only)
- Boot: agent direct.
- Impl: `writer` skill for impact_report v2 rewrite (sonnet or haiku).
- Tests: `test-engineer` for grep-based CI gates.
- Pre-merge: `code-reviewer` (light).

### Z1 CutoverGuard (HIGH risk, state machine + ops)
- Boot: `deep-interview` MANDATORY (state transitions are ambiguous).
- Impl: `executor` (opus) — atomic state-machine implementation.
- Tests: `test-engineer` — every illegal transition must raise.
- Pre-merge: `critic` + `code-reviewer` + `security-reviewer` (operator-token surface is auth-critical).

### Z2 V2 adapter + VenueSubmissionEnvelope (HIGH risk, external SDK)
- Boot: `document-specialist` MANDATORY — capture py-clob-client-v2 v1.0.0 surface to `reference_excerpts/py_clob_client_v2_<date>.md`. WebFetch GitHub source. memory `feedback_on_chain_eth_call_for_token_identity` for any token-id disputes.
- Impl: `executor` (opus). Coordinate with `architect` (read-only) for envelope schema.
- Tests: `test-engineer` + mock SDK to verify both one-step and two-step paths.
- Pre-merge: `critic` (NC-NEW-G spirit check) + `code-reviewer` + `security-reviewer` (live placement surface).

### Z3 HeartbeatSupervisor (HIGH risk, async)
- Boot: `deep-interview` for async vs sync semantics; document concurrency model.
- Impl: `executor` (opus) for async coroutine. Test with `asyncio` fixtures.
- Tests: `test-engineer` — failure injection at every cadence boundary.
- Pre-merge: `critic` + `code-reviewer`.

### Z4 CollateralLedger (HIGH risk, multi-asset semantics)
- Boot: `deep-interview` for buy/sell asymmetry edge cases (wrapped CTF, legacy USDC.e variants).
- Impl: `executor` (opus). Coordinate with `architect` for ledger schema.
- Tests: `test-engineer` + `scientist` (math correctness for reservation accounting).
- Pre-merge: `critic` (NC-NEW-K spirit check — sell preflight does NOT substitute pUSD) + `code-reviewer` + `security-reviewer`.

### U1 ExecutableMarketSnapshotV2 (medium risk, append-only DB)
- Boot: `architect` for snapshot freshness model.
- Impl: `executor`. SQLite triggers for APPEND-ONLY enforcement.
- Tests: `test-engineer` — UPDATE/DELETE must raise.
- Pre-merge: `code-reviewer`.

### U2 5-projection schema (HIGH risk, schema split)
- Boot: `deep-interview` MANDATORY — 5 projections is the structural backbone; ambiguity here cascades.
- Impl: `executor` (opus) + `architect` for schema design. Plan migration carefully.
- Tests: `test-engineer` + provenance chain reconstructability test.
- Pre-merge: `critic` (NC-NEW-H + NC-NEW-I spirit check) + `code-reviewer` + `security-reviewer`.

### M1 Lifecycle grammar (HIGH risk, planning-lock)
- Boot: `deep-interview` for INV-29 amendment scope; verify cycle_runner-as-proxy still correct on HEAD.
- Impl: `executor` (opus). Run planning-lock check pre-merge.
- Tests: `test-engineer` — NC-NEW-D function-scope antibody MUST run + green.
- Pre-merge: `critic` + `code-reviewer` + planning-lock receipt cite in PR.

### M2 SUBMIT_UNKNOWN_SIDE_EFFECT (HIGH risk, exception classification)
- Boot: `tracer` to map all exception sites + their current handling.
- Impl: `executor`. typed exception hierarchy.
- Tests: `test-engineer` + chaos injection.
- Pre-merge: `critic` + `code-reviewer`.

### M3 User-channel WS (HIGH risk, async + external)
- Boot: `document-specialist` for Polymarket WS docs + `deep-interview` for gap-detection thresholds.
- Impl: `executor` (opus) — async ingestor.
- Tests: `test-engineer` + WS gap simulation in fake venue (depends on T1 fake venue if T1 has landed; otherwise stub).
- Pre-merge: `critic` + `code-reviewer` + `security-reviewer` (WS auth surface).

### M4 Cancel/replace + exit safety (HIGH risk, mutex)
- Boot: `deep-interview` for mutex semantics under concurrent exit triggers.
- Impl: `executor` (opus). SQLite-backed mutex.
- Tests: `test-engineer` + race condition harness.
- Pre-merge: `critic` + `code-reviewer`.

### M5 Exchange reconciliation sweep (HIGH risk, large module)
- Boot: `tracer` to map current command_recovery + sweep boundaries.
- Impl: `executor` (opus). New `src/execution/exchange_reconcile.py` module.
- Tests: `test-engineer` — idempotency under repeated cycles.
- Pre-merge: `critic` (NC-NEW-A boundary preserved) + `code-reviewer` + `verifier` (F-006 closure evidence).

### R1 Settlement command ledger (medium risk, chain ops)
- Boot: `document-specialist` for redemption ABI + chain reconcile docs.
- Impl: `executor`. Coordinate with Z4 — redemption goes through R1, not Z4.
- Tests: `test-engineer` + crash-recovery fixtures.
- Pre-merge: `critic` + `code-reviewer`.

### T1 Fake venue (HIGH risk, large test infra)
- Boot: `document-specialist` for Polymarket V2 behavior catalog. Read all R3 phase yamls' acceptance tests.
- Impl: `executor` (opus). New `tests/fakes/polymarket_v2.py` + fixtures.
- Tests: `test-engineer` — fake venue and live adapter MUST produce schema-identical events.
- Pre-merge: `critic` (paper/live parity spirit check) + `code-reviewer`.

### F1 Forecast source registry (medium risk, pluggable system)
- Boot: `architect` for source registry design + `explore` to map existing scaffolding (`src/data/ensemble_client.py` + `forecasts_append.py`).
- Impl: `executor`. Typed registry + ingest protocol.
- Tests: `test-engineer` — gated source raises SourceNotEnabled.
- Pre-merge: `code-reviewer`.

### F2 Calibration retrain loop (HIGH risk, math + corpus)
- Boot: `scientist` MANDATORY — Platt re-fit math + frozen-replay correctness. `deep-interview` for corpus filter semantics.
- Impl: `executor` (opus) + `scientist` for math review.
- Tests: `test-engineer` + frozen-replay harness against 3 fixture portfolios.
- Pre-merge: `critic` (NC-NEW-H matched-not-confirmed spirit check) + `code-reviewer`.

### F3 TIGGE ingest stub (medium risk, dormant code path)
- Boot: `document-specialist` — capture TIGGE archive access docs to `reference_excerpts/tigge_archive_access.md`.
- Impl: `executor`. Dual-gate (artifact + env flag) wired; fetch raises until both open.
- Tests: `test-engineer` — gate-closed raises; gate-open returns ForecastBundle.
- Pre-merge: `code-reviewer`.

### A1 StrategyBenchmarkSuite (HIGH risk, large module)
- Boot: `scientist` for metrics math + `deep-interview` for promotion criteria.
- Impl: `executor` (opus) + `scientist` for metric implementations.
- Tests: `test-engineer` — replay + paper + shadow gates.
- Pre-merge: `critic` (INV-NEW-Q spirit check) + `code-reviewer` + `verifier`.

### A2 RiskAllocator + PortfolioGovernor (HIGH risk, sizing math)
- Boot: `scientist` for capacity math + drawdown governor semantics.
- Impl: `executor` (opus). Coordinate with Z4 (CollateralLedger) + A1 (benchmark inputs).
- Tests: `test-engineer` — kill switch fires on every threshold.
- Pre-merge: `critic` (NC-NEW-I spirit check — OPTIMISTIC vs CONFIRMED in capacity calc) + `code-reviewer`.

### G1 Live readiness gates (medium risk, orchestration)
- Boot: read all 19 prior phase yamls; map each gate to its antibody source.
- Impl: `executor`. New `scripts/live_readiness_check.py` runs all 17 gates.
- Tests: `test-engineer` + integration harness.
- Pre-merge: `verifier` (every gate maps to a runnable antibody) + `critic` + `code-reviewer` + operator review.

## §3 Wave-close multi-review pattern

Every wave (A through F) closes with a 5-angle multi-review:

| Wave | Phases | Multi-review trigger |
|---|---|---|
| A | Z0..Z4 | After Z4 lands |
| B | U1, U2, M1..M5 | After M5 lands |
| C | R1, T1 | After T1 lands |
| D | F1..F3 | After F3 lands |
| E | A1, A2 | After A2 lands |
| F | G1 | After G1 green = LIVE deploy gate |

Multi-review at wave close (per `feedback_multi_angle_review_at_packet_close`):

```python
# Dispatch 5 sub-agents in parallel
Agent(architect, opus): "review the K-collapse + structural integrity"
Agent(critic, opus): "10-attack template; find missing scope"
Agent(explore, sonnet): "verify ALL file:line citations across new cards"
Agent(scientist, opus): "domain correctness — money path coverage"
Agent(verifier, sonnet): "critical path arithmetic + sub-sequence locks + hour estimates"
```

Each writes ≤700-word report. Synthesize into `MULTI_REVIEW_<wave>_SYNTHESIS.md`.

If any reviewer returns REVISE: pause next wave, address, re-review.

## §4 Anti-patterns (skills to NOT use)

- DO NOT use `executor` for ambiguity resolution. Use `deep-interview` first.
- DO NOT use `critic` BEFORE implementation. Critic is post-merge / pre-merge gate.
- DO NOT use `general-purpose` for HIGH-risk phases unless ambiguity has been resolved.
- DO NOT use `code-simplifier` BEFORE all antibodies pass. It removes lines that may be load-bearing.
- DO NOT use `writer` skill for code. Only for docs.
- DO NOT skip `document-specialist` for external SDK / docs. Inference produces drift.

## §5 When to escalate to operator

Operator (user) intervention is required for:

- Cross-phase ambiguity that 2+ phase agents cannot resolve via `_cross_phase_question.md`.
- Operator-decision gate stuck > 5 days.
- INV-29 amendment governance call.
- impact_report v2 critic gate.
- TIGGE ingest go-live decision.
- Calibration retrain go-live decision.
- CLOB v2 cutover go/no-go.
- Any change that would amend the R3 plan structure (planning-lock event).

DO NOT escalate routine implementation decisions. Phase boundary owns the
decision space within the boundary.
