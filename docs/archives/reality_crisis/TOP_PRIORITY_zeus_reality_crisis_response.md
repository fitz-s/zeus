# TOP PRIORITY: Zeus Reality Crisis Response

> **Status**: EMERGENCY — 系统与外部现实严重偏移
> **Disposition**: ACTIVE CRISIS RESPONSE DOCUMENT
> **Authority basis**: `zeus_design_philosophy.md`, `zeus_first_principles_rethink.md`, 2026-04-03 external validation
> **Created**: 2026-04-03T23:17Z
> **Last updated**: 2026-04-03T23:17Z

---

## 一、危机声明

### 1.1 发现的事实

2026-04-03 对 Zeus 进行了全面的 Ideal vs Reality 评估，发现 **17 个外部现实假设与系统实现不匹配**：

| 类别 | 关键 Gap | 严重性 |
|------|----------|--------|
| **经济真相** | 5% taker fee 完全缺失，所有 edge/Kelly/exit 计算错误 | 🔴 CRITICAL |
| **执行真相** | tick_size 动态变化未处理，min_order_size 未检查 | 🔴 CRITICAL |
| **数据真相** | Settlement source 硬编码 WU，忽略 NOAA 市场 | 🟠 HIGH |
| **协议真相** | 无 WebSocket，完全依赖 REST polling | 🟠 HIGH |
| **协议真相** | UMA oracle dispute period 未处理 | 🟡 MEDIUM |

### 1.2 根本问题

**这不是 17 个独立的 bug，是一类感知失效**：

```
系统基于隐式假设运行 → 外部世界改变 → 系统不知道 → 静默错位
```

Zeus 的设计哲学已经预言了这个问题：

> "Zeus daemon 是脚本跑步机——优秀的机械执行，完全无法感知自己的假设是否和 reality 对齐。"

### 1.3 为什么打地鼠不是解决方案

传统方法：修 Fee → 修 tick_size → 修 settlement source → ...

问题：
1. 修完 Fee，下月 Polymarket 改 tick_size
2. 修完 tick_size，下月 Polymarket 加新的 resolution source  
3. 永远在追赶

正确方法：建立**系统性的外部假设感知机制**。

---

## 二、哲学基础（从 Zeus 设计哲学继承）

### 2.1 核心原则

**原则 1: 翻译损失的热力学极限**
```
函数、类型、测试：100% 跨 session 存活
设计理念、架构哲学：~20% 跨 session 存活
```

**原则 2: 免疫系统，不是 Security Guard**
```
Security guard: 巡逻→发现→报警。下次同样问题来，还要再巡逻→再发现→再报警。
Immune system: 遇到病原体→产生抗体→永久免疫这一类病原体。
```

**原则 3: Venus 是 Zeus 的自我意识**
```
Venus 的输出不是 alert。
Venus 的输出是 antibody。
```

**原则 4: 让类型系统为你防错**
```
错误在代码层面不可表达，而不是在文档层面被"禁止"。
```

### 2.2 三层意识架构

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

---

## 三、解决方案：Reality Contract Layer (RCL)

### 3.1 核心概念

不是修 17 个 gap。是建立一个**显式假设层**：

```
              External Reality
                    ↓
        ┌───────────────────────┐
        │  Reality Contract     │  ← 每个假设都是显式的、可验证的、有过期时间的
        │  Layer (RCL)          │
        └───────────────────────┘
                    ↓
              Zeus Runtime
```

### 3.2 RealityContract 数据结构

```python
@dataclass(frozen=True)
class RealityContract:
    """外部现实假设的显式契约"""
    contract_id: str           # e.g., "FEE_RATE_WEATHER"
    category: str              # "economic" | "execution" | "data" | "protocol"
    assumption: str            # 人类可读的假设描述
    current_value: Any         # 当前已知值
    verification_method: str   # 如何验证这个假设
    last_verified: datetime    # 上次验证时间
    ttl_seconds: int           # 验证有效期
    criticality: str           # "blocking" | "degraded" | "advisory"
    
    @property
    def is_stale(self) -> bool:
        return (datetime.utcnow() - self.last_verified).total_seconds() > self.ttl_seconds
    
    @property
    def must_reverify(self) -> bool:
        return self.is_stale and self.criticality == "blocking"
```

### 3.3 四类 Reality Contracts

#### A. Economic Reality Contracts

```yaml
# config/reality_contracts/economic.yaml
contracts:
  - id: FEE_RATE_WEATHER
    assumption: "Weather markets taker fee is 5%"
    verification: "GET /fee-schedule?category=weather"
    ttl_seconds: 3600  # 1 hour
    criticality: blocking
    on_change:
      - recalculate_all_position_ev
      - log_fee_change_event
      - alert_if_increase_gt_50pct

  - id: MAKER_REBATE_RATE
    assumption: "Maker rebate is 25%"
    verification: "GET /fee-schedule"
    ttl_seconds: 86400
    criticality: degraded
```

#### B. Execution Reality Contracts

```yaml
# config/reality_contracts/execution.yaml
contracts:
  - id: TICK_SIZE_STANDARD
    assumption: "Tick size is 0.01 for prices in [0.04, 0.96]"
    verification: "GET /book?token_id={id} → tick_size field"
    ttl_seconds: 300  # 5 min, because it can change mid-session
    criticality: blocking
    on_change:
      - cancel_pending_orders_with_invalid_tick
      - recalculate_rounding

  - id: MIN_ORDER_SIZE_SHARES
    assumption: "Minimum order size is 5 shares"
    verification: "GET /book → min_order_size field"
    ttl_seconds: 3600
    criticality: blocking
```

#### C. Data Reality Contracts

```yaml
# config/reality_contracts/data.yaml
contracts:
  - id: SETTLEMENT_SOURCE_{CITY}
    assumption: "City X settles via Weather Underground"
    verification: "GET /market/{id} → resolution_source in description"
    ttl_seconds: 86400
    criticality: blocking
    per_market: true
    on_change:
      - freeze_trading_for_market
      - alert_settlement_source_change

  - id: GAMMA_CLOB_PRICE_CONSISTENCY
    assumption: "Gamma outcomePrice matches CLOB midpoint within 5%"
    verification: "compare GET /gamma/market vs GET /clob/book"
    ttl_seconds: 300
    criticality: advisory
```

#### D. Protocol Reality Contracts

```yaml
# config/reality_contracts/protocol.yaml
contracts:
  - id: RESOLUTION_TIMELINE
    assumption: "Markets resolve within 24h of finalization_time"
    verification: "track resolved_at - finalization_time for recent settlements"
    ttl_seconds: 86400
    criticality: advisory

  - id: WEBSOCKET_REQUIRED
    assumption: "WebSocket is optional, REST polling is sufficient"
    verification: "compare data freshness: WS vs REST over 1 hour"
    ttl_seconds: 86400
    criticality: degraded
    upgrade_to_blocking_if: "REST latency > 60s vs WS"
```

### 3.4 RealityContractVerifier

```python
class RealityContractVerifier:
    """Venus 的核心感知引擎"""
    
    def verify_all_blocking(self) -> VerificationResult:
        """在任何交易决策前调用"""
        blocking = [c for c in self.contracts.values() 
                    if c.criticality == "blocking" and c.must_reverify]
        
        failures = []
        for contract in blocking:
            result = self._verify_single(contract)
            if not result.valid:
                failures.append(result)
        
        if failures:
            return VerificationResult(
                can_trade=False,
                reason="blocking_contracts_invalid",
                failures=failures
            )
        return VerificationResult(can_trade=True)
    
    def detect_drift(self) -> list[DriftEvent]:
        """Venus heartbeat 调用 - 检测假设漂移"""
        drifts = []
        for contract in self.contracts.values():
            old_value = contract.current_value
            new_value = self._fetch_current_value(contract)
            
            if old_value != new_value:
                drift = DriftEvent(
                    contract_id=contract.contract_id,
                    old_value=old_value,
                    new_value=new_value,
                    detected_at=datetime.utcnow(),
                    impact=self._assess_impact(contract, old_value, new_value)
                )
                drifts.append(drift)
                self._execute_on_change_handlers(contract, drift)
        
        return drifts
    
    def generate_antibody(self, drift: DriftEvent) -> Antibody:
        """Venus 的核心输出 - 不是 alert，是 antibody"""
        if drift.impact == "critical":
            return Antibody(
                type="code_change",
                target=f"update contract {drift.contract_id} default value",
                test=f"test_{drift.contract_id}_matches_reality",
                urgency="immediate"
            )
        elif drift.impact == "moderate":
            return Antibody(
                type="config_change",
                target=f"config/reality_contracts/*.yaml",
                urgency="next_session"
            )
        else:
            return Antibody(
                type="documentation",
                target="known_gaps.md",
                urgency="tracked"
            )
```

---

## 四、Venus 集成架构（从 venus_zeus_audit_integration_plan 继承）

### 4.1 核心原则

> **Venus 必须通过 durable truth contracts 与 Zeus 集成，而不是脆弱的实现细节绑定。**

### 4.2 Zeus 拥有运行时真相

```python
AUTHORITATIVE_INPUTS = [
    "zeus/scripts/healthcheck.py",
    "zeus/state/status_summary-{mode}.json",
    "zeus/state/positions-{mode}.json",
    "zeus/state/strategy_tracker-{mode}.json",
    "zeus/state/risk_state-{mode}.db",
    "zeus/state/zeus.db",
    "zeus/state/control_plane-{mode}.json",
    "zeus/state/assumptions.json",           # NEW: RCL
    "zeus/config/reality_contracts/*.yaml",  # NEW: RCL
]
```

### 4.3 Venus 拥有审计编排

```python
AUDIT_LAYERS = {
    "heartbeat": {
        "frequency": "every_30_minutes",
        "cost": "low",
        "checks": [
            "healthcheck",
            "status_freshness",
            "riskguard_freshness",
            "cycle_failure",
            "truth_contract_check",
            "legacy_truth_guard",
            "control_recommendations",
            "quarantine_pressure",
            "reality_contract_verification",  # NEW: RCL
        ]
    },
    "daily_audit": {
        "frequency": "daily_6am_utc",
        "cost": "medium",
        "checks": [
            "recent_trade_review",
            "recent_exit_review",
            "no_trade_stage_review",
            "expired_position_audit",
            "daily_strategy_attribution",
            "cycle_failure_review",
            "headline_vs_tracker_consistency",
            "reality_contract_drift_detection",  # NEW: RCL
        ]
    },
    "weekly_audit": {
        "frequency": "weekly_monday_6am_utc",
        "cost": "high",
        "checks": [
            "edge_realization_review",
            "divergence_exit_counterfactual",
            "edge_compression_review",
            "settlement_quality_review",
            "buy_no_semantic_review",
            "fallback_safety_scan",
            "cross_module_invariant_review",
            "reality_contract_full_audit",  # NEW: RCL
        ]
    }
}
```

### 4.4 Heartbeat 是 Guardrail，不是 Research Lab

```python
def should_run_in_heartbeat(check: dict) -> bool:
    return all([
        check.get("fast", False),
        check.get("safety_relevant", False),
        not check.get("requires_long_horizon", False),
    ])
```

Heartbeat 应该回答的唯一问题：

> Is Zeus currently safe, fresh, and reality-aligned enough to keep operating without intervention?

### 4.5 Contract Drift 必须被表面化

```python
REQUIRED_STATUS_KEYS = {"truth", "risk", "portfolio", "runtime", "control", "cycle"}

def status_contract_ok(payload: dict) -> tuple[bool, list[str]]:
    missing = sorted(k for k in REQUIRED_STATUS_KEYS if k not in payload)
    return (len(missing) == 0, missing)
```

---

## 五、从 Venus Operator Architecture 继承的基础设施

### 5.1 Venus 已有的能力（零构建需求）

| 能力 | 来源 | 状态 |
|------|------|------|
| Agent identity + Discord presence | openclaw.json, IDENTITY.md | ✅ Live |
| Operator contract for Zeus | AGENTS.md | ✅ Written |
| Health check specification | HEARTBEAT.md | ✅ Written |
| Diagnostic runbook | OPERATOR_RUNBOOK.md | ✅ Written |
| Cron scheduling engine | cron/jobs.json | ✅ Live |
| Discord alerting + cooldowns | discord_alerts.py | ✅ Live |
| ACP to spawn Claude Code | acpx plugin | ✅ Available |
| Native filesystem access to Zeus state | workspace-venus/zeus/state/ | ✅ Native |
| Control plane commands | control_plane.json | ✅ Zeus honors them |
| Memory across sessions | MEMORY.md + daily notes | ✅ Live |

**一切基础设施都已存在。缺失的是激活。**

### 5.2 需要构建的内容

#### A. Zeus Assumption Manifest (`state/assumptions.json`)

```json
{
  "updated_at": "2026-04-03T23:00:00Z",
  "assumptions": {
    "fees": {
      "taker_fee_weather": 0.05,
      "maker_fee": 0.00,
      "maker_rebate": 0.25
    },
    "execution": {
      "tick_size_standard": 0.01,
      "min_order_size_shares": 5,
      "tick_size_extreme_threshold_high": 0.96,
      "tick_size_extreme_threshold_low": 0.04
    },
    "settlement": {
      "default_source": "weather_underground",
      "precision_f": 1.0,
      "precision_c": 1.0,
      "rounding": "round_half_to_even"
    },
    "data_sources": {
      "gamma_vs_clob_divergence_threshold": 0.05,
      "rest_polling_acceptable_latency_seconds": 60
    }
  }
}
```

#### B. Venus World Model (`memory/venus_world_model.md`)

```markdown
# Venus World Model — Last Updated 2026-04-03

## Market Structure (verified 2026-04-03)
- Taker fee: 5% for weather markets
- Tick size: 0.01 for prices in [0.04, 0.96]
- Min order: 5 shares
- Settlement: WU for most cities, NOAA for some

## Known Reality Gaps (from 2026-04-03 audit)
- Fee not integrated into Kelly/edge/exit (CRITICAL)
- tick_size dynamic change not handled (CRITICAL)
- No WebSocket, REST polling only (HIGH)
- Settlement source hardcoded (HIGH)

## Antibodies Needed
- [ ] FeeGuard temporary protection
- [ ] Reality Contract Layer foundation
- [ ] RCL migration for all 17 gaps
```

---

## 六、INV-11: Reality Contract Integrity

在 `architecture/invariants.yaml` 中增加：

```yaml
- id: INV-11
  statement: External assumptions are explicit and verified.
  why: Silent assumption drift is the #1 cause of "correct code, wrong behavior".
  enforced_by:
    spec_sections: [RCL]
    config:
      - config/reality_contracts/*.yaml
    tests:
      - tests/test_reality_contracts.py::test_all_blocking_contracts_verified
      - tests/test_reality_contracts.py::test_no_hardcoded_external_values
    scripts:
      - scripts/verify_reality_contracts.py
```

---

## 七、实施计划

### 7.1 紧急行动（立即）

**P-FEE-GUARD**: 临时 fee 保护
```python
# src/execution/fee_guard.py (临时)
class FeeGuard:
    ASSUMED_TAKER_FEE = 0.05
    ASSUMED_ROUND_TRIP = 0.10
    
    @classmethod
    def adjust_edge(cls, gross_edge: float) -> float:
        return gross_edge - cls.ASSUMED_TAKER_FEE
    
    @classmethod
    def min_gross_edge_for_trade(cls) -> float:
        return cls.ASSUMED_TAKER_FEE + 0.02  # 7%
```

这是有意识的、暴露的、临时的补丁，等待 RCL 完成。

### 7.2 Phase 1: RCL Foundation（本周）

**P-RCL-01**: Reality Contract Layer 基础设施
```
├── src/contracts/reality_contract.py
│   └── RealityContract, VerificationResult, DriftEvent, Antibody
├── src/venus/reality_verifier.py
│   └── RealityContractVerifier
├── config/reality_contracts/
│   ├── economic.yaml
│   ├── execution.yaml
│   ├── data.yaml
│   └── protocol.yaml
├── state/assumptions.json
├── tests/test_reality_contracts.py
└── architecture/invariants.yaml (INV-11)
```

### 7.3 Phase 2: RCL Migration（下周）

**P-RCL-02**: 迁移 17 个 Gap 到 RCL
- 每个 gap → 一个或多个 contracts
- 每个 contract → verification method
- 每个 contract → on_change handlers

### 7.4 Phase 3: Venus Integration（下下周）

**P-RCL-03**: Venus Heartbeat 集成
- Heartbeat 调用 `verify_all_blocking()`
- `detect_drift()` 定期运行
- antibody generation pipeline

### 7.5 Phase 4: Continuous Operation（持续）

- Venus world model 持续更新
- Reality contracts 随外部变化演进
- antibody → test → code → 永久免疫

---

## 八、成功标准

### 8.1 错误的指标

- "修复了多少个 gap"
- "代码行数增加了多少"
- "测试覆盖率"

### 8.2 正确的指标

| 指标 | 定义 | 目标 |
|------|------|------|
| **假设显式率** | contracts_defined / assumptions_total | 100% |
| **验证覆盖率** | contracts_with_auto_verification / contracts_total | >90% |
| **漂移检测延迟** | time_from_reality_change to drift_detected | blocking < 1h |
| **抗体生成率** | antibodies_generated / drifts_detected | >80% |
| **Zero-surprise 天数** | 连续没有发现"系统不知道的外部变化"的天数 | 趋势向上 |

---

## 九、这份文档会在翻译中损失

你的设计哲学已经预言了这一点：

> "这个文档本身也会在翻译中损失。但 Bin.unit、for_city()、test_celsius_cities_get_celsius_semantics() 不会。"

所以这份文档的价值不在于它被阅读，而在于它被编码为：

1. **`src/contracts/reality_contract.py`** — RealityContract 类型
2. **`config/reality_contracts/*.yaml`** — 显式假设声明
3. **`tests/test_reality_contracts.py`** — 验证覆盖测试
4. **`architecture/invariants.yaml` INV-11** — 不变量保护

一旦这些存在，这份文档就可以消失。系统会继续运行。

---

## 十、批准与行动

**需要批准**:
1. RCL 作为解决方案的总体方向
2. P-FEE-GUARD 立即启动
3. P-RCL-01 本周启动

**下一步行动**:
1. 冻结 P-FEE-GUARD 工作包
2. 实施临时 fee 保护
3. 冻结 P-RCL-01 工作包
4. 构建 Reality Contract Layer

---

## 附录：完整 Gap 清单

### A. 原始 9 点验证

| # | Gap | 状态 | 行动 |
|---|-----|------|------|
| 1 | Position/execution runtime | ✅ 已解决 | 无 |
| 2 | Ensemble probability | ✅ 已解决 | 无 |
| 3 | Day0 observed high 作为硬下界 | ✅ 已解决 | 无 |
| 4 | Settlement source 硬编码 | ⚠️ 部分解决 | RCL DATA contract |
| 5 | 动态 Fee 缺失 | 🔴 未解决 | **P-FEE-GUARD + RCL ECONOMIC** |
| 6 | Feed vs calibration archive | ⚠️ 已知未解决 | 文档化 |
| 7 | Open-Meteo 商业授权 | ⚠️ 生产前必须处理 | 文档化 |
| 8 | Alpha 被流动性压缩 | ✅ 正确理解 | 无 |
| 9 | Bin 归一化 vs venue 执行真相 | ✅ 已解决 | 无 |

### B. 新发现 8 点验证

| # | Gap | 状态 | 行动 |
|---|-----|------|------|
| 10 | Gamma ≠ CLOB 可成交真值 | ✅ 已正确分离 | 无 |
| 11 | WebSocket vs REST polling | 🔴 未实现 | RCL PROTOCOL contract |
| 12 | API 限流时效性问题 | ⚠️ 部分实现 | RCL PROTOCOL contract |
| 13 | tick_size 动态变化 | 🔴 未实现 | **RCL EXECUTION contract** |
| 14 | 显示价 ≠ 成交价，depth 缺失 | ⚠️ 部分实现 | RCL EXECUTION contract |
| 15 | 合约规则漂移 | 🔴 未实现 | RCL DATA contract |
| 16 | UMA resolution timeline | 🔴 未实现 | RCL PROTOCOL contract |
| 17 | NOAA 时间尺度混淆 | 🔴 未实现 | RCL DATA contract |
