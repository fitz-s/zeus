# T0_ALERT_POLICY — Planner Triage

**Created:** 2026-05-04
**Verdict:** REALITY_ANSWERED — Discord alert infrastructure exists and is wired to RiskGuard. Operator decision degenerates to "ratify existing adapter, name fallback."
**Captured-by:** planner subagent

---

## 1. The plan's question

Per MASTER_PLAN_v2 §8 T0.8:
> Operator chooses alert delivery owner → `T0_ALERT_POLICY.md`; Discord/riskguard/email/local-log fallback, cooldown, escalation owner.

## 2. Reality findings (planner grep, 2026-05-04)

### 2.1 Discord alert delivery already exists

`src/riskguard/discord_alerts.py` (13 086 bytes, exists today) provides:

- `alert_halt(...)` — `src/riskguard/discord_alerts.py:11` (per its own docstring)
- `alert_resume(...)` — same module
- `alert_warning(...)` — same module
- `alert_redeem(...)` — same module
- `alert_daily_report(...)` — same module

Wiring:

- `src/riskguard/riskguard.py:17` — `from src.riskguard.discord_alerts import alert_halt, alert_resume, alert_warning`
- `src/riskguard/discord_alerts.py:6` — `Webhook URL resolved from macOS Keychain: zeus_discord_webhook`
- `src/riskguard/discord_alerts.py:82-87` — keychain resolver lookup

### 2.2 Manifests confirm it

`src/riskguard/AGENTS.md:20`:
> `discord_alerts.py` | Discord webhook alerts for halt/resume/warning (keychain-resolved) | LOW

So this is a known, registered surface. F13's claim that *"`monitoring/alerts.yaml` is greenfield unless alert infrastructure exists"* is partially obsolete — the alert infrastructure exists; the YAML config does not.

### 2.3 Cooldown / retry behavior in code today

Planner did not exhaustively read `discord_alerts.py`, but it has 13 KB and includes `alert_warning` (suggests severity tiers) and a keychain-resolved webhook URL. T2E's job is to wire the new Tier-1 counters into this existing adapter, not to build a new one.

## 3. Reality answers

| T0.8 field | Reality answer | Evidence |
|---|---|---|
| Delivery adapter | Discord webhook (`src/riskguard/discord_alerts.py`) | file exists, 13 KB |
| Severity tiers | `halt` / `warning` / `resume` / `redeem` / `daily_report` already named | `discord_alerts.py:11` |
| Webhook secret resolution | macOS Keychain `zeus_discord_webhook` | `discord_alerts.py:6,82-87` |
| Wired into RiskGuard | yes | `riskguard.py:17` |
| Local-log fallback | Python `logger` calls run alongside, but the explicit "if Discord fails, log instead" path is not yet enumerated | T2E to wire |
| Cooldown semantics | not yet enumerated by planner | T2E to wire |

## 4. Recommended draft policy (operator-confirmable)

```
Primary delivery adapter: Discord webhook (src/riskguard/discord_alerts.py)
Severity → method mapping:
  HIGH    → alert_halt() or alert_warning(severity="HIGH") + escalation
  MEDIUM  → alert_warning(severity="MEDIUM")
  LOW     → logger.warning + observability counter
Fallback:                If Discord post fails OR keychain webhook absent:
                         logger.error("ALERT_FALLBACK") + write line to
                         logs/alerts_fallback.log + retry once with
                         exponential backoff (T2E to implement).
Cooldown:                Per-counter, 5 minutes between repeats of the same
                         alert key (T2E to implement; counters in plan §11
                         T2E table).
Escalation owner:        Operator (Discord DM is the operator's primary
                         channel today). No on-call rotation defined.
T1 dependency:           T1F/T1BD/T1C/T1E counters MUST emit; alert wiring
                         is T2E (not blocking T1 closeout).
```

## 5. No-operator-decision-needed determination

The plan asks the operator three things; reality + Zeus convention answer all three:

1. *"Discord/riskguard/email/local-log fallback"* → reality: Discord is the existing adapter; local-log is the only sane fallback.
2. *"cooldown"* → planner default 5 min/key is conservative; T2E may revise.
3. *"escalation owner"* → reality: this is a single-operator system (Fitz). No formal escalation chain exists; the operator IS the escalation owner.

Operator may sign this draft as-is. **No operator decision is required to unblock T1 emission of the counters listed in MASTER_PLAN_v2 §11 T2E.**

## 6. Implication for T1A/T1F/T1BD readiness

T0.8 does NOT block T1A/T1F/T1BD:

- T1A introduces no new counter.
- T1F adds `placeholder_envelope_blocked_total` / `compat_submit_rejected_total`. These are emitted; alerts are wired in T2E.
- T1BD adds `cost_basis_chain_mutation_blocked_total{field}`, `position_projection_field_dropped_total`, `position_loader_field_defaulted_total`. Same pattern — counter first, alert wiring T2E.

Therefore T0.8 is technically read-only context for T1, not a blocker.

## 7. Source-evidence cite list (planner grep-verified within 10 minutes)

- `src/riskguard/discord_alerts.py:6` — webhook resolution doc
- `src/riskguard/discord_alerts.py:11` — public alert function names
- `src/riskguard/discord_alerts.py:82-87` — keychain resolver call
- `src/riskguard/discord_alerts.py:167` — `sqlite3.connect(... timeout=5)` — for risk_state.db reads
- `src/riskguard/riskguard.py:17` — alert imports wired into RiskGuard
- `src/riskguard/AGENTS.md:20` — registry row
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md:208-212` — F13 finding text

---

**Verdict:** REALITY_ANSWERED. Operator may sign this triage as-is. T2E wires the counters into the existing adapter.
