# W0-b 反转发现:trade_decisions 是 position_lots 的身份脊柱,不是 fail-soft 投影

2026-07-21 · 主线程实测。**consult 的 W0-b(去掉候选门 EXISTS)在 Zeus 这里不成立**——它没有的一个事实推翻了它。按操作员契约,这里给出推翻的推理,不闷头码无效改动。

## consult 说什么
round-3 verdict:"command_recovery.py:6500 的 filled-entry lot 修复门锚在 fail-soft 的 trade_decisions 上;把 trade_decisions 降级为投影,门重锚到 canonical fill authority。" 具体:删 `AND EXISTS(SELECT 1 FROM trade_decisions td WHERE ...)`,主查询已 `JOIN canonical_trade_fact`,fill authority 已在。

## 它不知道的事实(实测)
1. **position_lots.position_id 是 INTEGER,keyed 到 `trade_decisions.trade_id`**(schema:`position_id INTEGER NOT NULL`;`venue_command_repo.py:589-590` docstring 明说 "the current position_lots schema still keys exposure by the integer trade_decisions.trade_id")。
2. runtime 字符串 position(如 `c19a88f8-bb9`)→ 整数 lot 身份的**唯一桥就是 trade_decisions**:`_trade_decision_id_for_runtime_id`(venue_command_repo.py)= `SELECT trade_id FROM trade_decisions WHERE runtime_trade_id=?`。
3. apply 路径 `_append_filled_entry_position_lot_repair` → `resolve_position_lot_id_for_command` → 无 trade_decisions 行则**返回 None → 不修复**。

→ **trade_decisions 不是 fail-soft 审计投影,是 position_lots 的身份脊柱。** consult 只看候选查询,没看 apply 的 resolve,故误判。

## 为何去掉候选门 EXISTS 是无效的
apply 的 resolve 照样需要 trade_decisions 行来把 runtime position 映射到整数 lot 身份。去掉候选门只是把 247 个从"**门拦**"变成"**apply 跳**"(resolve→None→return False→"stayed"),**一个都修不了**,还多 247 个 no-op savepoint。候选门与 apply resolve 检查的是同一个 trade_decisions 存在性。

## 离线只读证明(活库,一次性 evidence)
| | 候选数 |
|---|---|
| OLD(trade_decisions 门) | 7 |
| NEW(fill-authority) | 254 |
| OLD\NEW | 0(证 OLD⊆NEW,放松只增不减) |
| NEW\OLD(缺口) | **247**,全 CONFIRMED + 正 filled_size,日期 07-11~今 |

247 个 confirmed-fill 仓位无 position_lots 行,且因无 trade_decisions 行,apply 层也修不了。W0-a 解冻 trade_decisions 只让**未来**进场有行;这 247 个既有缺口仍需显式 recovered_projection 合成。

## exposure 风险(待你定紧迫度)
position_lots 被读于:`riskguard.py:693`、`risk_allocator/governor.py`、`canonical_asset_exposure.py`、`canonical_projections.py`、`ledger.py`。247 仓位无 lot 行 → 若 exposure 主要经 position_lots 计,riskguard 少算 live exposure(真钱风险);若主要经 position_current(这 247 应有 position_current 行),则 position_lots 是二级细账,风险降级。**未定:exposure 主载体是 position_current 还是 position_lots** —— 这决定 W0-c 紧迫度,需一探(riskguard/governor 的 exposure 计算读哪张)。

## 重定 W0-b / W0-c
- **W0-b(去候选门)不做**——无效。保留候选门或删皆不修复缺口;删只多 no-op。不改 command_recovery.py:6500 的门。
- **真修复路径** = W0-a(解冻,已完成)+ **W0-c 扩容**:为 247 缺口仓位合成 recovered_projection trade_decisions 行(经 `synthesize_missing_bridge`,字段源自 position_current/events/venue_commands 权威,标 `recovered_projection`,原始 vs 恢复时间戳分开),使 resolve 生效 → lots 物化。consult 的"拒绝合成 trade_decisions"在这里让位于"trade_decisions 是身份脊柱"的事实:合成是**唯一**能修复缺口的路径,但须严格标记 + 权威溯源。
- **更深的架构问题(记入 REDESIGN,非 W0)**:position_lots 用整数 trade_decisions.trade_id 做身份,把一个"审计投影"变成不可绕过的身份脊柱——这正是 REDESIGN money-hot 要解耦的(lot 身份应锚 venue command/fill 的不可变 id,不是 trade_decisions.trade_id)。W0 不动此架构,W4 money-hot 时重新 key。

## 结论
按计划"码 W0-b"= 码一个无效改动。正确动作:不改门;W0-c 扩为 recovered_projection 合成(需先定 exposure 紧迫度 + position_lots per-fill 幂等)。此发现交操作员定 W0-c 范围与优先级。

---

## Fork 1 已解决(实测):缺口 lot **不是** live exposure 风险 — W0-c 降级低紧迫

`w0b_exposure_probe.py`(只读活库)拆分 267 个 NEW 候选:

| | 数 |
|---|---|
| 有 position_current 行 | **267(全部)** |
| 无 position_current | **0** |

相位分布(全有 position_current):`settled` **220(82%)** · day0_window 20 · voided 11 · active 8 · pending_exit 5 · admin_closed 3 → 仅 **33 个 open**。

**定性**:
1. exposure 由 **position_current 承载**(全 267 有行)。缺的 position_lots 是**二级 lot 明细账**,不是 live exposure 少算。RiskGuard 主 exposure 读 position_current;`_unprojected_entry_fill_equity_usd`(riskguard.py:671)另有网兜"有 fill 但无 position_current"的仓位——此处该网命中 0(全有 pc),证明 exposure 无缺口。
2. **82% 已结算**——lot 是纯历史明细;governor `load_position_lots`(governor.py:798)也另读 position_current(:868)。
3. 仅 33 个 open 仓位缺 lot 明细,且都有 position_current。

**W0-c 重定级**:从"impaired money-path repair lane(疑真钱风险)"→ **二级账完整性补全,低紧迫,多为历史**。真钱 exposure/风险由 position_current 独立承载,不受缺 lot 影响。

**修正原 FINDINGS G2 措辞**:G2 closure 写的"impaired money-path repair lane"过重——修复通道产出的是 position_lots 二级明细;live exposure 由 position_current 独立承载。准确表述:*trade_decisions 冻结导致 position_lots 明细补全通道停摆(二级账不完整),但不影响 live exposure/风险计量*。W0-a 解冻仍应做(让未来进场正常物化 lot),W0-c(补 247 历史缺口)可从容排期,非救火。

## 净结论(交操作员)
- **W0-a**(解冻迁移)= 独立、已测绿、值得落地:让未来进场的 lot 明细正常物化,并消除 trade_decisions 每次 INSERT 报错的噪音。仍操作员门运行。
- **W0-b(去候选门)= 撤销**(no-op,不改 command_recovery)。
- **W0-c** = 低紧迫二级账补全(recovered_projection 合成 247 历史行);可后排。
- **架构**:position_lots 用 trade_decisions.trade_id 做身份 → REDESIGN money-hot 解耦目标(W4),非 W0。
