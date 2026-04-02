# Venus × Zeus Audit Integration Plan

_Status: draft skeleton for implementation_

## Purpose

This file is the integration contract for how Venus should audit Zeus going forward.

It exists to prevent three failure modes:

1. **Truth drift** — Venus reads the wrong surface and reports false reality.
2. **Coupling drift** — Venus binds to Zeus internal implementation details and breaks when Zeus evolves independently.
3. **Scope drift** — heavy analysis leaks into heartbeat, or safety-critical checks get buried in slow cron jobs.

The rule is simple:

> Venus should depend on Zeus truth contracts, not Zeus private internals.

---

## Core Integration Philosophy

### 1. Zeus owns runtime truth
Zeus is responsible for emitting stable, mode-qualified truth surfaces.
Venus does not infer current reality from stale files, historical docs, or implementation gossip.

```python
AUTHORITATIVE_INPUTS = [
    "zeus/scripts/healthcheck.py",
    "zeus/state/status_summary-{mode}.json",
    "zeus/state/positions-{mode}.json",
    "zeus/state/strategy_tracker-{mode}.json",
    "zeus/state/risk_state-{mode}.db",
    "zeus/state/zeus.db",
    "zeus/state/control_plane-{mode}.json",
]
```

### 2. Venus owns audit orchestration
Venus decides:
- what belongs in heartbeat
- what belongs in daily cron
- what belongs in weekly audit
- when to alert
- when to recommend control-plane actions
- which findings should become antibodies

```python
AUDIT_RESPONSIBILITIES = {
    "zeus": "emit_truth",
    "venus": "audit_and_escalate",
}
```

### 3. Heartbeat is a guardrail, not a research lab
Heartbeat is for high-frequency, low-latency, safety-relevant checks.
It should be able to stay quiet when the system is healthy.

```python
def should_run_in_heartbeat(check: dict) -> bool:
    return all([
        check.get("fast", False),
        check.get("safety_relevant", False),
        not check.get("requires_long_horizon", False),
    ])
```

### 4. Cron is for interpretation and antibodies
Daily and weekly cron tasks should absorb the heavy work:
- trade review
- strategy attribution
- divergence counterfactuals
- code-risk scans
- invariant review

```python
CRON_AUDIT_CLASSES = {
    "daily": ["recent_trade_review", "expired_position_audit", "strategy_attribution"],
    "weekly": ["edge_realization", "divergence_counterfactual", "code_risk_scan"],
}
```

---

## Contract Boundary

Venus should not bind to Zeus private implementation details unless there is no exposed truth surface.
That means heartbeat and cron should prefer:

1. healthcheck output
2. truth files
3. control plane
4. risk DB / zeus DB

and only then inspect internal modules if a deeper audit is explicitly needed.

```python
READ_ORDER = [
    "healthcheck",
    "truth_files",
    "control_plane",
    "risk_db",
    "zeus_db",
    "internal_code_only_if_needed",
]
```

---

## Layer Map

## Layer A — Heartbeat

Heartbeat should answer only this question:

> Is Zeus currently safe, fresh, and reality-aligned enough to keep operating without intervention?

### Heartbeat responsibilities
- run `python3 zeus/scripts/healthcheck.py`
- detect stale status or stale RiskGuard
- detect cycle failure / failure_reason
- detect risk escalation (ORANGE / RED)
- detect truth contract drift
- detect legacy truth misuse
- detect control-plane recommendations
- detect quarantine / unverified-entry pressure
- stay silent when healthy

```python
HEARTBEAT_CHECKS = [
    "healthcheck",
    "status_freshness",
    "riskguard_freshness",
    "cycle_failure",
    "truth_contract_check",
    "legacy_truth_guard",
    "control_recommendations",
    "quarantine_pressure",
]
```

### Heartbeat non-goals
Heartbeat should not perform:
- long trade-by-trade reviews
- edge bucket statistics
- divergence exit counterfactuals
- codebase-wide risk scans
- calibration distribution analysis

```python
HEARTBEAT_FORBIDDEN = [
    "counterfactual_analysis",
    "weekly_statistics",
    "broad_code_scan",
    "long_horizon_calibration_review",
]
```

---

## Layer B — Daily Cron Audit

Daily audit should answer:

> Did Zeus spend the last day doing sensible things, and if not, where did the error enter the chain?

### Daily cron responsibilities
- recent 12h / 24h trade review
- recent exits quality review
- rejected-signal / no-trade-stage review
- expired-position detailed audit
- daily strategy attribution
- cycle-failure clustering
- headline-vs-attribution consistency review

```python
DAILY_AUDIT_CHECKS = [
    "recent_trade_review",
    "recent_exit_review",
    "no_trade_stage_review",
    "expired_position_audit",
    "daily_strategy_attribution",
    "cycle_failure_review",
    "headline_vs_tracker_consistency",
]
```

### Daily audit output style
- concise summary
- clear causality
- action items
- no fake certainty when sample size is small

```python
def daily_sample_sufficient(n: int) -> bool:
    return n >= 10
```

---

## Layer C — Weekly Audit

Weekly audit should answer:

> Is Zeus actually converting edge into money, and what structural weaknesses are still leaking alpha or truth?

### Weekly cron responsibilities
- edge realization review
- divergence exit counterfactual
- strategy-specific edge decay / compression review
- settlement quality review
- buy_no semantic consistency review
- fallback / silent-failure / dead-config scan
- cross-module invariant review

```python
WEEKLY_AUDIT_CHECKS = [
    "edge_realization_review",
    "divergence_exit_counterfactual",
    "edge_compression_review",
    "settlement_quality_review",
    "buy_no_semantic_review",
    "fallback_safety_scan",
    "cross_module_invariant_review",
]
```

---

## Truth Integrity Requirements

This integration exists because Venus has already been misled by stale or wrong truth surfaces.
So truth-integrity checks are first-class, not optional.

### Required invariants
1. `status_summary-{mode}.json` is the headline performance authority.
2. `strategy_tracker-{mode}.json` is attribution only.
3. legacy truth files must stay tombstoned.
4. heartbeat must reject deprecated truth files.
5. tracker accounting metadata must explicitly describe its scope.

```python
def tracker_is_safe_attribution_surface(tracker_payload: dict, status_path: str) -> bool:
    accounting = tracker_payload.get("accounting", {})
    return (
        accounting.get("tracker_role") == "attribution_surface"
        and accounting.get("performance_headline_authority") == status_path
        and accounting.get("includes_legacy_history") is False
    )
```

### Contract drift must be surfaced
If Zeus changes shape, Venus should report drift instead of silently assuming success.

```python
REQUIRED_STATUS_KEYS = {"truth", "risk", "portfolio", "runtime", "control", "cycle"}


def status_contract_ok(payload: dict) -> tuple[bool, list[str]]:
    missing = sorted(k for k in REQUIRED_STATUS_KEYS if k not in payload)
    return (len(missing) == 0, missing)
```

---

## Control Plane Policy

Venus should not take broad autonomous action.
It may only recommend or apply narrow control-plane actions when the trigger is explicit.

Allowed actions:
- `pause_entries`
- `tighten_risk`
- `request_status`

```python
def autosafe_commands(health: dict) -> list[dict]:
    cmds = []
    if health.get("cycle_failed"):
        cmds.append({"command": "request_status"})
    if health.get("risk_level") == "ORANGE":
        cmds.append({"command": "tighten_risk"})
    if health.get("risk_level") == "RED":
        cmds.append({"command": "pause_entries"})
    return cmds
```

---

## Adapter Layer Requirement

Venus should not scatter raw Zeus field access across heartbeat logic.
A small adapter layer should normalize Zeus truth surfaces into a stable audit object.

```python
def normalize_status(payload: dict) -> dict:
    return {
        "mode": payload.get("truth", {}).get("mode"),
        "generated_at": payload.get("truth", {}).get("generated_at"),
        "risk_level": payload.get("risk", {}).get("level"),
        "open_positions": payload.get("portfolio", {}).get("open_positions", 0),
        "exposure": payload.get("portfolio", {}).get("total_exposure_usd", 0.0),
        "cycle_failed": payload.get("cycle", {}).get("failed", False),
        "failure_reason": payload.get("cycle", {}).get("failure_reason", ""),
        "recommended_commands": payload.get("control", {}).get("recommended_commands", []),
    }
```

This keeps Zeus free to evolve internally while preserving Venus compatibility through one narrow translation point.

---

## Rollout Strategy

This should not be switched in one shot.
Use a cautious rollout:

1. shadow checks
2. compare outputs with current heartbeat / cron
3. switch primary surfaces
4. remove legacy logic

```python
ROLLOUT_PLAN = {
    "phase_1": "shadow_only",
    "phase_2": "compare_old_vs_new",
    "phase_3": "switch_primary",
    "phase_4": "remove_legacy_checks",
}
```

---

## First Build Targets

The first implementation pass should be intentionally narrow.

### Heartbeat v1
- healthcheck
- truth contract validation
- freshness checks
- cycle failure detection
- riskguard level / recommendations
- legacy truth guard

```python
HEARTBEAT_V1 = [
    "healthcheck",
    "truth_contract_check",
    "freshness_check",
    "cycle_failure_check",
    "riskguard_check",
    "legacy_truth_guard",
]
```

### Daily audit v1
- recent exit review
- strategy attribution snapshot
- no-trade-stage snapshot
- expired-position audit

```python
DAILY_V1 = [
    "recent_exit_review",
    "strategy_attribution_snapshot",
    "no_trade_stage_snapshot",
    "expired_position_audit",
]
```

### Weekly audit v1
- divergence exit review
- edge realization buckets
- cross-module invariant review
- code risk scan

```python
WEEKLY_V1 = [
    "divergence_exit_review",
    "edge_realization_buckets",
    "cross_module_invariant_review",
    "code_risk_scan",
]
```

---

## Deliverables

This file is the skeleton. Building from it should produce:

1. Venus audit adapter module
2. updated HEARTBEAT.md
3. daily Zeus audit prompt / task definition
4. weekly Zeus audit prompt / task definition
5. optional invariant tests for truth-contract drift

```python
DELIVERABLES = [
    "venus_audit_adapter.py",
    "HEARTBEAT.md.v2",
    "zeus_daily_audit_prompt_v2.md",
    "zeus_weekly_audit_prompt_v2.md",
    "truth_contract_invariant_tests.py",
]
```

---

## Final Guiding Sentence

The most important constraint is this:

> Venus must integrate with Zeus through durable truth contracts, not fragile implementation intimacy.

If this rule holds, Zeus can keep changing internally without breaking Venus audit logic every time.
