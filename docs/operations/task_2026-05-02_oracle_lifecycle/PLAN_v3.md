# Oracle Artifact Lifecycle Fix — Plan v3 (Simplified)

**Status**: DRAFT (revised after critic-opus NO-GO + operator feedback)
**Author**: team-lead (Claude opus-4-7)
**Date**: 2026-05-02
**Scope**: Resolve oracle artifact worktree-pinning + remove fail-closed halt. **Defer everything else to backlog.**

---

## Core principle

> **Oracle 是 Kelly sizing 修饰,不是 truth gate。Oracle 缺失 = 有界精度损失;Live trading halt = 无界机会成本。后者更糟。**

实测数据支撑(critic 引,待 haiku 复核):
- 47 cities median error rate = **0.0**
- 41/47 cities = 0.0
- Mean = 0.014
- Max = Shenzhen 0.40

所以:大多数情况下 oracle 给的 multiplier 就是 1.0;少数城市真有偏差。即使**完全没有** oracle 数据,所有交易都按标准 Kelly 跑,最坏情形是少数城市偶尔过度 sizing —— **远小于** 全部 halt 的损失。

## What v3 cuts from v2

| v2 内容 | v3 处理 |
|---------|---------|
| 5 层 tier (T1/T2/T3/T4/T5) | **删除**,不需要 |
| Floor file 带 source/authority/expires | **删除**,不需要 |
| `OracleEvidenceRepository` 类 | **删除**,不建新抽象 |
| `kelly_multiplier` 第 2 个 multiplier | **删除**,既存 `oracle_penalty.penalty_multiplier` 已够 |
| `ZEUS_ORACLE_FILE_OVERRIDE` 逃生舱 | **删除**,YAGNI |
| Lagos/HK/Shenzhen overrides | **删除**,oracle_penalty 自己读 JSON 算 |
| `availability_status=DEGRADED_T*` 传播 | **延期**,task #21 backlog |
| Alert 通道命名 | **延期**,task #22 backlog |

## What v3 keeps

只剩 2 件事:

### PR-A: 路径中心化(机械,今天可发,恢复 live)

新文件 `src/contracts/storage_paths.py`:

```python
import os
from pathlib import Path

ZEUS_STORAGE_ROOT = Path(
    os.environ.get("ZEUS_STORAGE_ROOT", "~/.openclaw/storage/zeus")
).expanduser()

ORACLE_DIR = ZEUS_STORAGE_ROOT / "oracle"
ORACLE_SHADOW_SNAPSHOTS = ORACLE_DIR / "shadow_snapshots"
ORACLE_ERROR_RATES = ORACLE_DIR / "error_rates.json"
```

替换 4 处硬编码:
- `scripts/oracle_snapshot_listener.py:43` (SNAPSHOT_DIR)
- `scripts/bridge_oracle_to_calibration.py:45-46` (SNAPSHOT_DIR + ORACLE_FILE)
- `src/engine/evaluator.py:109` (PROJECT_ROOT / "data")
- `src/strategy/oracle_penalty.py:23-24` (Path(__file__).resolve()...)

数据迁移:
1. `mkdir -p ~/.openclaw/storage/zeus/oracle/shadow_snapshots`
2. `cp data/oracle_error_rates.json ~/.openclaw/storage/zeus/oracle/error_rates.json`
3. `cp -r raw/oracle_shadow_snapshots/* ~/.openclaw/storage/zeus/oracle/shadow_snapshots/`
4. SHA256 验证

新 cron:
```
5 10 * * * cd /Users/leofitz/.openclaw/workspace-venus/zeus && .venv/bin/python scripts/bridge_oracle_to_calibration.py >> /Users/leofitz/.openclaw/logs/oracle-bridge.log 2>&1
```

写入加固:
- `bridge_oracle_to_calibration.py` 写入用 atomic `.tmp + os.replace`
- 写完 `chmod 0o664`

阶段顺序(sonnet 修订 — PR-B 在 PR-A **之前**):

**先做 PR-B**(删熔断,系统获得抗脆弱性) →
**再做 PR-A**(搬路径,此时无 fail-closed 风险)

PR-A 内部阶段:
**P1**(repo 添加 storage_paths.py + 测试,无 caller 改动) →
**P2**(数据迁移到新路径) →
**P3**(添加 bridge cron + 立即 force-run 一次刷新文件) →
**P4**(改 listener 写新路径) →
**P5**(改 reader/evaluator 读新路径) →
**P6**(冒烟测试 + 回收旧路径)

每阶段单独 commit,可单独回滚。

### PR-B: 删除 evaluator 的 fail-closed gate(简单删除,不替代)

删除位置(critic 引,实施时再次 grep 验证行号):
- `src/engine/evaluator.py:413-466` `_oracle_evidence_rejection_reason()`
- `src/engine/evaluator.py:2599-2613` 抛 ORACLE_EVIDENCE_UNAVAILABLE 的代码块
- `src/engine/evaluator.py:109-110` `ORACLE_EVIDENCE_MAX_STALENESS_DAYS = 30` 常量

保留:
- `src/contracts/semantic_types.py:58` 枚举值 `ORACLE_EVIDENCE_UNAVAILABLE` —— 留作历史日志可读;但已无 emitter
- `src/strategy/oracle_penalty.py` 整个文件不动,这是 graceful fallback 的所在(file 缺失 → 全城市 OK → mult 1.0)

新增(可见性,非熔断):
- evaluator 启动 + 每 N 分钟一次,在 daemon log 里写一行 `oracle_evidence_age_days={N}, file_present={bool}`(soft warn,不阻断任何决策)
- `state/daemon-heartbeat.json` 加字段 `oracle_evidence_age_days`(visibility 用,无 alert 动作)

**缓存失效修复(sonnet 必修)**:
- `oracle_penalty._cache` 当前 lazy-load + 永不刷新 → daemon 启动后即使 cron 每天写新数据,daemon 永远读不到
- 修复:evaluator 每个 cycle 开头调用 `oracle_penalty.reload()`(函数已存在 src/strategy/oracle_penalty.py,无 caller)
- 成本:每 cycle 重读小 JSON,可忽略

测试(sonnet 必修):
- **删除**(不是修改):`tests/test_runtime_guards.py` L684 `test_oracle_evidence_gate_rejects_missing_and_stale_rows` + L712 同类测试
- **新增**:`tests/runtime/test_evaluator_oracle_resilience.py`:删除 oracle_error_rates.json → evaluator 产出决策(不抛 ORACLE_EVIDENCE_UNAVAILABLE)
- **新增**:`tests/strategy/test_oracle_penalty_reload.py`:写新文件 → 调用 reload() → 验证读到新数据

---

## 关于 critic 5 个 blocker 的回应

| Defect | v3 处理 |
|--------|--------|
| **D1**(NIH:重造 oracle_penalty tier 模型) | ✅ 不再建 OracleEvidenceRepository,oracle_penalty 现状不动 |
| **D2**(floor 数字反向) | ✅ 不建 floor 文件,oracle_penalty 自己处理缺失 |
| **D3**(7 天 vs 30 天矛盾) | ✅ 不引入新阈值,30 天常量也删(随 fail-closed gate 一起删) |
| **D4**(availability_status 信号丢失) | ⚠ 延期 task #21,目前所有交易标 OK。trade-off 已 accept(operator 决策) |
| **D5**(P3/P4 时序竞态) | ✅ 阶段重排:cron+force-run 早于 reader 切换 |

## 关于 critic 6 项必修的回应

6. Floor 带 source/authority/expires → **不做 floor 文件**
7. 4-tuple → dataclass → **不建 OracleEvidenceRepository**
8. Alert 通道 → 延期 task #22,heartbeat 字段先放着
9. 删 ZEUS_ORACLE_FILE_OVERRIDE + T5 → ✅ v3 都删了
10. Tier 用 last_date 不是 mtime → **不建 tier 系统**(整个问题消失)
11. 拆 2 个 PR → ✅ 拆 PR-A + PR-B

→ v3 自然吸收了 critic 9/11 项。剩 2 项(D4 + 8)operator 已 accept 延期。

## 风险

| 风险 | 严重度 | 应对 |
|------|--------|------|
| 删 fail-closed 后,某天 oracle 数据真的彻底坏掉(全城市错误率 50%+),系统继续过度 sizing | Med | oracle_penalty 既存 BLACKLIST 阈值(>10% error)仍然给 mult=0.0,不会过度 sizing 真正高错误城市 |
| 路径迁移期间 listener 写新路径 + bridge 读旧路径 → 不一致 | Med | 阶段顺序保证 cron 改、文件迁移、reader 切换之间的依赖正确 |
| Test 改动遗漏 | Low | 删除 fail-closed 必伴随 grep `ORACLE_EVIDENCE_UNAVAILABLE` 遍历测试,每处更新 |
| 操作员 6 个月后忘记 oracle 已不再 halt,误以为系统还在严格守门 | Med | 在 evaluator 文件顶 + AGENTS.md 写一段"oracle 是 sizing 修饰非 truth gate"的设计注释 |

## 验证序列(live-trading 重启前必过)

1. PR-A 提交并 merge
2. 手动跑 listener → 写到新路径 ✓
3. 手动跑 bridge → 写到新路径 ✓
4. cron 显示新 entry 到位
5. PR-B 提交并 merge
6. 删除 `~/.openclaw/storage/zeus/oracle/error_rates.json` → 跑 evaluator 单测,必须产出决策不熔断
7. 恢复文件 → 重新跑,行为正常
8. `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.zeus.live-trading.plist`

## 仍待 haiku 确认的事项

- bridge_oracle_to_calibration 算法是否有 bug(时区? 缺失天处理? metric 聚合?)
- `data/oracle_error_rates.json` 数字是否 current(critic 引的 median 0/mean 0.014/Shenzhen 0.40 在 mtime 10:08 today,但 source data 时效?)
- INCIDENTAL_THRESHOLD=0.03 vs 实测分布是否合理

→ 上述确认完后,**plan v3 不需要任何数字改动**,因为 v3 不建 floor 文件,不引入阈值。haiku 报告用于:
- 确认算法没 bug(若有 → 单独 task)
- 给 oracle_penalty.py 既存阈值做 sanity check(顺手任务)

## Haiku 数据复核(2026-05-02 17:55)

### 算法 verdict: CORRECT(1 处独立 bug,不阻塞 v3)

- 主算法(snapshot 对 settlement,频次占比)正确
- floor() 单位转换故意对齐 PM UMA 投票者截断行为
- **独立 bug**: `_snapshot_daily_high` 不解析 OGIMET 格式 → Istanbul/Moscow/Tel Aviv 实测 snap 永远 None,error_rate 来自 hist_rate 继承。已记 task #23 backlog

### 数据 current 性 ✅
- `data/oracle_error_rates.json` mtime = 2026-05-02 10:08:52
- 47 城市分布:Median 0.0、Mean 0.0142、Std 0.0592、P75 0.0、P90 0.0249、P95 0.0708、P99 0.2563

### 真实 top-3(critic + 我都猜错过)

| 城市 | error_rate | N |
|------|-----------|---|
| Shenzhen | 0.40 | 25 |
| Seoul | 0.0877 | 57 |
| Kuala Lumpur | 0.0833 | 12 |

Lagos / Hong Kong 实测 0.0。

### 强化 v3 简化论点的证据

> "JSON 中的非零值多来源于 snapshot_data 之外的 hist_rate"

→ 当前 shadow 比对绝大多数返回 0;非零值是从更老的 historical data 继承。**Oracle gate 在今天的现实中几乎从未"真正捕捉"到 inconsistency**,但它的 fail-closed 代价是 halt 所有交易。

### INCIDENTAL_THRESHOLD = 0.03 现状合理 ✅
- P90 = 0.0249,阈值刚好捕捉长尾的 Sao Paulo/Chengdu/KL/Seoul/Shenzhen
- 既存 oracle_penalty.py 阈值不动

---

## Sonnet review 要回答的(简化前必经)

- v3 vs v2 的简化是否丢了任何**对 live trading 真正重要**的 invariant?
- 删除 fail-closed 后,system 任何**其他**保护机制是否会因此被绕过?(比如 evaluator 还有其他 reject 路径,oracle gate 是它们的 dependency 吗?)
- PR-A/PR-B 拆分是否真的安全(PR-A merge 后 PR-B 还没 merge 那段时间,系统行为是什么)?
- Shenzhen 0.40 是单独城市存在的真实大偏差。删除 fail-closed 后,Shenzhen 仍由 oracle_penalty 的 BLACKLIST(>10%)处理 → mult=0,trade 不会下。这条 invariant 保留,对吗?
