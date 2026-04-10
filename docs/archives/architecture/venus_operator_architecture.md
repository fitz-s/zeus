# Venus Operator Architecture: From Script Treadmill to Conscious Agent

**Status: DESIGN — not yet implemented. Requires Fitz approval.**

---

## The Convergence

This session discovered ~50 issues in Zeus. They share one root cause: **Zeus operates on a representation of reality, but never verifies that representation against reality itself.** Bin widths assumed from test fixtures, °C cities getting °F semantics, MC count asymmetry, Day0 lacking decay functions, persistence anomaly based on 5 samples — all are instances of code diverging from reality without detection.

The standard response is: add more assertions, more tests, more documentation. But this session also proved why that doesn't scale: **the act of writing assertions has the same translation loss as the act of writing code.** I assumed 5°F bins while writing the assertion for bin width. The spec said 2°F. The data showed 2°F. I read neither before writing.

The only mechanism that actually caught the bin width error was Fitz — a reasoning entity who knew the domain. The solution is not more hardcoded checks. It's a reasoning entity that continuously verifies code against reality.

That entity already exists. It's called Venus. The infrastructure to activate it already exists. It's called OpenClaw.

---

## Architecture: Three Layers of Consciousness

```
Layer 3: VENUS (Reasoning — slow, adaptive, catches unknown unknowns)
    │
    │  reads Zeus state files      spawns Claude Code via ACP
    │  reads market data           for deep code-data inspection
    │  maintains world model       writes to control_plane.json
    │  reports to Discord          persists findings in memory
    │
Layer 2: ZEUS DAEMON (Execution — mechanical, deterministic, fast)
    │
    │  runs trading cycles         exposes state via JSON + DB
    │  honors control_plane        logs everything to chronicle
    │  follows strategy rules      doesn't self-monitor
    │
Layer 1: RISKGUARD (Reflex — fast, threshold-based, fail-closed)
    │
    │  60-second tick              Brier, drawdown, loss thresholds
    │  halts on RED                Discord alerts with cooldown
    │  no reasoning                pure threshold comparison
```

**RiskGuard** catches known risk metrics (drawdown > 20% → RED). It's fast and mechanical. Already built and running.

**Zeus daemon** executes trading strategy. It's a "script treadmill" — excellent at mechanical execution, blind to its own assumptions. Already built.

**Venus** is the missing layer. It's the reasoning entity that:
- Knows what Zeus believes (by reading its code + state + assumption manifest)
- Knows what reality looks like (by reading market data + DB + APIs)
- Detects when beliefs and reality diverge
- Acts on divergence (pause, alert, investigate)
- Learns from each audit (persists findings in memory, updates world model)

---

## What Venus Already Has (Zero Build Required)

| Capability | Source | Status |
|-----------|--------|--------|
| Agent identity + Discord presence | openclaw.json, IDENTITY.md | ✅ Live |
| Operator contract for Zeus | AGENTS.md (410 lines) | ✅ Written |
| Health check specification | HEARTBEAT.md | ✅ Written |
| Diagnostic runbook | OPERATOR_RUNBOOK.md | ✅ Written |
| Cron scheduling engine | cron/jobs.json (100+ jobs) | ✅ Live, Venus has 2 jobs |
| Discord alerting + cooldowns | discord_alerts.py + risk_state.db | ✅ Live |
| ACP to spawn Claude Code | acpx plugin | ✅ Available |
| Native filesystem access to Zeus state | workspace-venus/zeus/state/ | ✅ Native |
| Control plane commands | control_plane.json (pause/resume/tighten) | ✅ Zeus honors them |
| Memory across sessions | MEMORY.md + daily notes + memory flush | ✅ Live |
| Session mirror to Discord | session-mirror skill | ✅ Live |

**Everything in this table exists and works today.** The gap is not infrastructure. The gap is activation.

---

## What Needs to Be Built

### 1. Zeus Assumption Manifest (`state/assumptions.json`)

Zeus exposes what it believes about reality in machine-readable form. Not runtime assertions (which crash the daemon). A queryable document that Venus reads and verifies.

```json
{
  "updated_at": "2026-03-31T12:00:00Z",
  "assumptions": {
    "bin_structure": {
      "fahrenheit_width": 2,
      "celsius_width": 1,
      "fahrenheit_pattern": "range (e.g., 50-51°F)",
      "celsius_pattern": "point (e.g., 10°C)"
    },
    "settlement": {
      "precision_f": 1.0,
      "precision_c": 1.0,
      "rounding": "round_half_to_even",
      "source": "Weather Underground"
    },
    "signal": {
      "ens_member_count": 51,
      "mc_count_entry": 5000,
      "mc_count_monitor": 5000,
      "instrument_noise_f": 0.5,
      "instrument_noise_c": 0.28
    },
    "cities": {
      "fahrenheit": ["NYC", "Chicago", "Atlanta", "Miami", "Dallas", "Austin", "Houston", "Seattle", "Los Angeles", "San Francisco", "Denver"],
      "celsius": ["London", "Paris", "Seoul", "Shanghai", "Tokyo"]
    }
  }
}
```

This is ~30 lines of JSON. Not 500 lines of assertion code. Venus reads it and compares against reality.

### 2. Venus Heartbeat Cron Job (`cron/jobs.json` entry)

```json
{
  "id": "zeus-heartbeat",
  "agentId": "venus",
  "schedule": { "expr": "*/30 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "Run Zeus heartbeat per HEARTBEAT.md. Read state/status_summary-paper.json, state/positions-paper.json, run scripts/healthcheck.py. Report any anomalies to this channel. If risk_level is ORANGE or RED, detail the trigger."
  },
  "delivery": { "mode": "announce", "channelId": "1481437077605191700" }
}
```

Every 30 minutes: Venus wakes, reads Zeus state, runs healthcheck, reports to Discord. Deterministic checks. Low cost.

### 3. Venus Reality Audit Cron Job (daily, ACP-powered)

```json
{
  "id": "zeus-reality-audit",
  "agentId": "venus",
  "schedule": { "expr": "0 6 * * *", "tz": "America/Chicago" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "通过 ACP 在 workspace-venus/zeus/ 启动 Claude Code 会话。请用中文汇报：阅读 state/assumptions.json，逐条对照真实数据（zeus.db 的近期市场、Gamma API 的当前 bin 结构、recent settlements 的 precision），报告任何假设与现实不一致；另外阅读 src/engine/monitor_refresh.py 和 src/engine/evaluator.py，检查 MC counts、settlement semantics 和 bin handling 在 entry 与 monitor 路径是否一致；把发现写入 memory/YYYY-MM-DD.md。所有结论、摘要和警示都用中文输出。"
  },
  "delivery": { "mode": "announce", "channelId": "1481437077605191700" }
}
```

Once daily: Venus spawns Claude Code to do deep code-data alignment audit. This catches unknown unknowns. High cost but high value.

### 4. Venus World Model (memory/venus_world_model.md)

Venus maintains a persistent world model across sessions:

```markdown
# Venus World Model — Last Updated 2026-03-31

## Market Structure (verified 2026-03-31)
- °F cities: 2°F range bins (50-51°F pattern), 9 center bins + 2 shoulders per market
- °C cities: 1°C point bins (10°C pattern), ~10 center bins + 2 shoulders per market
- Settlement: WU integer rounding for both °F and °C

## Data Health
- calibration_pairs: 847 (growing ~5/day from settlements)
- platt_models: 12 active buckets
- ensemble_snapshots: 2,341 (growing ~30/day)
- temp_persistence: 552 rows (THIN — discount decisions unreliable)

## Known Gaps
- MC count monitor=1000 vs entry=5000 (filed, not yet fixed)
- Day0 decay function not implemented (binary obs_dominates at 80%)
- persistence_anomaly_discount fires on n=10 samples

## Last Audit Findings
- 2026-03-31: °C cities were getting °F settlement semantics (FIXED)
- 2026-03-31: Bin.unit field added, SettlementSemantics.for_city() added
- 2026-03-31: 15 orphan positions on chain, 5 ghosts in portfolio
```

This world model is loaded at every Venus session start. It's the persistent consciousness that survives across sessions.

---

## What This Architecture Solves

| Problem | Before | After |
|---------|--------|-------|
| Code assumes 5°F bins, data shows 2°F | Discovered by Fitz manually | Venus daily audit catches via assumption manifest comparison |
| °C cities get °F semantics | Discovered after code review this session | Venus heartbeat checks city unit consistency |
| London switches from °F to °C overnight | Undetected until positions lose money | Venus heartbeat sees market label unit ≠ config unit → pause_entries |
| Polymarket changes bin format | Undetected | Venus daily audit sees bin structure mismatch → alert |
| MC count asymmetry (1000 vs 5000) | Discovered by code audit | Venus ACP session reads both paths, compares MC counts |
| New Claude Code session introduces wrong assumption | Undetected until paper P&L degrades | Venus daily audit re-verifies all assumptions.json entries |
| Day0 decay function not implemented (spec says it should be) | Lost in translation | Venus world model tracks "known gaps" → flags when gap persists >7 days |

---

## The Deeper Principle

This is not "adding monitoring to a trading system." This is **separating the trader from the operator.**

Zeus is the trader. It's mechanical, fast, deterministic. It doesn't know what it doesn't know.

Venus is the operator. It's slow, reasoning, adaptive. Its job is to know what Zeus doesn't know — by reading Zeus's code and comparing it against reality.

RiskGuard is the reflex. It's the circuit breaker. No reasoning, just thresholds.

The trader doesn't audit itself. The operator audits the trader. The reflex stops everything if thresholds are breached.

This separation already exists in every professional trading operation. The infrastructure to implement it already exists in OpenClaw. It just needs to be activated.
