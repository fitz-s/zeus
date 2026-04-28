# RiskGuard Module Authority Book

**Recommended repo path:** `docs/reference/modules/riskguard.md`
**Current code path:** `src/riskguard`
**Authority status:** Dense module reference for policy emission, risk levels, metrics, and actuation semantics.

## 1. Module purpose
Turn measured risk conditions into actual behavioral constraints on Zeus. RiskGuard exists so that safety logic changes what the runtime can do, not just what it says.

## 2. What this module is not
- Not a passive alerting layer.
- Not a replacement for strategy or execution logic.
- Not a hidden control plane that bypasses `strategy_key` governance.

## 3. Domain model
- Risk levels (GREEN/YELLOW/ORANGE/RED) and their meanings.
- Metrics that justify those levels.
- Policy objects emitted to engine/execution/control layers.
- Notification/alerting as a downstream symptom, not the core product.

## 4. Runtime role
RiskGuard observes runtime conditions and emits durable policy that changes evaluation, sizing, entry permission, or exit-only behavior.

## 5. Authority role
K1 governance layer. It may evolve, but only inside K0 semantic boundaries. The change-control constitution explicitly names risk/control theater as forbidden: if risk does not actuate behavior, the design is wrong.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `src/riskguard/riskguard.py`, `policy.py`, `risk_level.py`, `metrics.py`
- `docs/authority/zeus_change_control_constitution.md` K1 governance layer and CONST-08
- `architecture/invariants.yaml` rule that RED/ORANGE must alter behavior, not merely advise

### Non-authority surfaces
- Discord or notification messages
- Status summaries that mirror risk without proving actuation
- Old packet docs about risk incidents unless translated into current tests/law

## 7. Public interfaces
- Risk level enum/value objects
- Policy resolution/emission APIs
- Metric computation helpers
- Alert/notification helpers
- R3 A2 allocation governor APIs in `src/risk_allocator/governor.py`:
  `CapPolicy`, `GovernorState`, `RiskAllocator.can_allocate()`,
  `maker_or_taker()`, `reduce_only_mode_active()`, and `PortfolioGovernor`.

## 8. Internal seams
- Metric collection vs policy resolution
- Policy resolution vs engine/execution consumption
- Alerting vs durable behavioral state
- Riskguard vs control-plane overrides

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `riskguard.py` | Primary risk policy emission/orchestration file. |
| `policy.py` | Resolved policy semantics and strategy-aware constraints. |
| `risk_level.py` | Level taxonomy and ordering. |
| `metrics.py` | Source metrics and decision-support calculations. |
| `discord_alerts.py` | Human-visible symptom layer; must stay downstream. |
| `../risk_allocator/governor.py` | R3 A2 blocking capital allocator/governor: reads current `position_lots`, unresolved submit-unknown side effects, open exchange reconcile findings, heartbeat/WS health, and drawdown evidence to deny new risk or force reduce-only/no-trade modes before executor submission. |

### R3 A2 risk allocator / portfolio governor

`src/risk_allocator/` is a K1 governance adjunct to RiskGuard. It is intentionally
blocking and read-only:

- `position_lots` is the exposure truth input. Latest append-only lot state per
  position is read; the allocator never repairs, inserts, updates, or deletes
  lots.
- OPTIMISTIC and CONFIRMED exposure remain separate. CONFIRMED exposure counts
  at full capacity weight; OPTIMISTIC exposure counts at
  `CapPolicy.optimistic_exposure_weight`.
- Per-market, per-event, per-resolution-window, and correlated-exposure caps
  deny allocation with structured reasons.
- Kill switch reasons include manual operator halt, heartbeat lost, WS-gap
  threshold breach, unresolved submit-unknown count, unresolved exchange
  reconciliation finding count, and drawdown threshold.
- Maker/taker selection is behavior-changing: shallow books, near-resolution
  windows, or heartbeat states that only allow non-resting orders select `FOK`
  for executor submission; healthy/deep/far-from-close paths may rest as `GTC`.
- Executor integration consults the process-wide allocator before command
  persistence or SDK contact. If no global governor state has been configured,
  the seam defaults to allow so isolated tests and non-live utility seams remain
  inert until cycle startup refreshes the governor.
- Cycle summaries include `portfolio_governor` for operator visibility, but the
  denial path is behavioral: `AllocationDenied` blocks submission.

## 10. Relevant tests
- tests/test_authority_gate.py
- tests/test_auto_pause_entries.py
- tests/test_bug100_k1_k2_structural.py
- tests/test_cross_module_invariants.py
- tests/test_risk_allocator.py

## 11. Invariants
- Risk must change evaluator/sizing/execution behavior.
- `strategy_key` remains the only governance key; risk metadata must not become a competing key.
- Alerting is not proof of risk actuation.
- Risk decisions must remain inspectable in canonical truth surfaces.
- R3 A2 kill switches and cap denials must block executor submission, not only
  annotate reports.
- R3 A2 allocation must preserve OPTIMISTIC vs CONFIRMED capacity accounting.

## 12. Negative constraints
- Do not add a risk level that only changes UI or logs.
- Do not let riskguard silently mutate canonical state without the state write path.
- Do not let control overrides or alerts bypass policy semantics.

## 13. Known failure modes
- Risk level flips but evaluator/executor behavior does not change.
- Per-strategy control gets encoded in ad hoc fields instead of policy/strategy_key grammar.
- Metrics drift or stale sources produce theater risk states.
- Human-visible alerts hide the absence of durable risk action.

## 14. Historical failures and lessons
- [Archive evidence] Strategy and exit failure analyses show that narrow-edge systems need risk actuation, not just diagnosis.
- [Archive evidence] Legacy truth-surface audits warn against storing effective policy only in reports or JSON side channels.

## 15. Code graph high-impact nodes
- `src/riskguard/riskguard.py` and `policy.py` are likely bridge nodes into engine/evaluator/execution.
- `src/riskguard/metrics.py` likely fans into observability and runtime support.

## 16. Likely modification routes
- Metric change: verify policy actuation and downstream consumers.
- Policy grammar change: review control, engine, execution, and state together.

## 17. Planning-lock triggers
- Any change to risk levels, policy grammar, or how risk affects runtime behavior.
- Any work that changes strategy-aware gating or auto-pause semantics.
- Any change that makes `RiskAllocator` advisory-only, changes the
  `position_lots` read contract, or bypasses executor pre-submit gating.

## 18. Common false assumptions
- Riskguard can be evaluated from alerts alone.
- A new risk level is harmless if defaulted to no-op.
- Metrics can lag reality because risk is 'advisory'.

## 19. Do-not-change-without-checking list
- Risk level taxonomy
- Policy-to-behavior mapping
- Any strategy-aware gating grammar

## 20. Verification commands
```bash
pytest -q tests/test_authority_gate.py tests/test_auto_pause_entries.py tests/test_bug100_k1_k2_structural.py
pytest -q tests/test_cross_module_invariants.py
pytest -q -p no:cacheprovider tests/test_risk_allocator.py
python -m py_compile src/riskguard/*.py
```

## 21. Rollback strategy
Rollback riskguard as a behavior bundle; never leave new metrics without matching policy consumption, or new policy without matching tests.

## 22. Open questions
- Which risk actions are still only operator-known rather than explicitly documented in module-level reference?

## 23. Future expansion notes
- Add a machine-readable risk-policy map to module manifest.
- Add explicit runtime evidence examples for each non-GREEN level.

## 24. Rehydration judgement
This book is the dense reference layer for riskguard. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
