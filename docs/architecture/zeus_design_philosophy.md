# Zeus 设计哲学：为什么认识到问题不等于解决问题

**地位：Zeus 项目的最高权威文档。所有其他设计文档（包括 Blueprint v2）服从本文档的原则。**

---

## 失效弧线

同一个错误，发生了至少两次：

```
Blueprint v1 写完 → Rainstorm 照此建造 → 死于仓位管理
吸取教训 → Zeus spec 写完 → Zeus 照此实现 → 仍然在遇到 Rainstorm 一次一次解决过的问题
```

Blueprint v2 如果不成为第三次，是这份文档存在的全部理由。

---

## 表面叙事的矛盾

叙事是"信号好、管理差"。但 code review 的实际数据不支持这个叙事。

P0-1（forecast slicing 用错天）和 P0-3（GFS 被 51 成员检查拒绝）不是仓位管理 bug——它们是信号模块之间的接口 bug。真正完美的模块是那些完全自包含的模块：ensemble_signal 的 MC 计算、fdr_filter 的 BH 程序、temperature.py 的运算符重载。它们完美是因为它们不依赖任何其他模块的正确行为。

10 个 P0 中的每一个都发生在模块 A 的输出传递到模块 B 的那个瞬间。不是信号层 vs 管理层的问题。**是自包含计算 vs 跨模块关系的问题。**

这个区分很重要，因为它改变了诊断。如果问题是"信号层吸引了太多注意力"，解决方案是"把注意力转移到管理层"。Blueprint v2 做的就是这个。但如果问题是"跨模块关系在实现过程中系统性地丢失"，那么把注意力从信号层转移到管理层不会解决问题——管理层内部的模块关系同样会丢失。

证据已经出现了。Blueprint v2 刚落地，cal_std 问题就证明了这一点：Zeus 复制了 Rainstorm 的退出阈值公式 `edge_threshold = -cal_std * 0.015`，但没有复制产生 cal_std 的统计框架。这个公式在 Rainstorm 中有意义是因为 Rainstorm 用 Gaussian CDF 产出校准标准差。Zeus 用 member counting，没有对应的 std 概念。**复制了接口的形状，丢失了接口的语义。**

这就是失效的确切机制：复制设计但不复制上下文。

---

## 为什么认识到问题不足以防止问题

### 不是认知问题

从 Session 1 的 CLAUDE.md 开始，设计者就知道仓位管理比信号重要。"敬畏市场"原则被写入。Rainstorm 尸检被执行。20 个最佳设计被定义。Blueprint v2 把 Position 放在中心。认知层面没有任何缺失。

Zeus spec §0 的第一句话就是："Zeus is a position management system, not a signal system." First Principles Rethink 用了 273 行来分析完全相同的问题。知识是存在的。但实现仍然分配了 18 个模块给信号、4 个给生命周期。

这不是遗忘。这不是无知。**这是注意力分配的物理属性。**

### 是翻译问题——但不是人类的翻译问题

信号逻辑是可算法化的。"从 51 个 ENS 成员计算 P_raw" 直接对应一个函数签名、一段 NumPy 代码、一组可断言的输出。你能在 spec 里写出伪代码，实现者能直接翻译。

"退出验证应该和入场验证有相等的严格度" 不对应任何函数。它是一个跨越多个模块的抽象约束。实现者需要**发明**验证层，不是翻译公式。

当设计者写 `Position 应该携带 entry_method，evaluate_exit 应该使用相同的 method` 时，这在设计者的头脑中是一个不可分割的约束。但当 Claude Code 实现它时，`entry_method` 变成了 Position 上的一个字符串字段，`evaluate_exit` 变成了一个接受 MonitorData 的方法。这两个实现之间的关系——evaluate_exit 必须根据 entry_method 选择概率计算路径——没有任何东西在代码中强制它。它是一个隐式约束，存在于自然语言 spec 中，但不存在于 Python 的类型系统中。

**Claude Code 系统性地做好了什么？** 可以用函数表达的东西。`compute_p_raw(members, bins)` 是一个纯函数。输入明确，输出明确，可以独立测试。Claude Code 写这种代码非常可靠。

**Claude Code 系统性地做不好什么？** 必须用关系表达的东西。"exit 的概率计算路径必须和 entry 的路径一致"不是一个函数——它是两个函数之间的一个约束。没有一行代码"实现"这个约束。它的正确性分布在两个不同模块中、可能在两个不同 session 中被写的代码之间。当一个 session 修改了 entry 路径但另一个 session 没有同步修改 exit 路径，约束就被违反了——悄无声息地。

192 个测试全绿。但这些测试验证的是函数行为（"给定输入 X，输出应该是 Y"），不是模块关系（"模块 A 的输出和模块 B 的期望在语义上必须匹配"）。**测试套件和代码库有同样的注意力偏向。**

### 自然语言规范在错误的地方失去精度

Spec 的精度分布不均匀：

```
精确的（直接可翻译为代码）：
  "P_raw = count(members in range) / 51"
  "Platt: P_cal = sigmoid(A × logit(P_raw) + B)"
  "Kelly: f* = (P_posterior - c) / (1 - c)"

模糊的（需要实现者自行解释）：
  "退出验证应有与入场相等的严格度"
  "Position 应携带自己的完整身份穿越所有模块"
  "没有下游模块需要推断方向或概率空间"
```

第一组可以 1:1 变成代码。第二组在翻译时必然损失精度，因为"相等的严格度"不告诉你具体写什么代码，只告诉你写多少。"穿越所有模块"不告诉你在哪些具体的函数签名上加哪些具体的参数。

10 个 P0 全是**不变量违反**：
- "概率空间不能被翻转两次"是一个不变量
- "决策时数据必须被保留"是一个不变量
- "每个发布的订单必须有一个 portfolio 记录"是一个不变量

这些不变量被架构隐含，但从未作为机器可检查的属性显式声明。

### 验证的延迟是损失的来源

Zeus 的验证时间线：
- Phase 0（baseline）：验证了 ✓
- Phase A（信号数学）：单元测试验证了 ✓
- Phase C（执行层）：单元测试通过了 ✓ ... 但测试只验证单个函数的输入输出，不验证跨模块的语义传递
- Phase D readiness：**直到外部 code review 才发现 10 个 P0**

没有任何验证步骤检查模块之间的语义不变量。Code review 是第一次有人看**接口**而非**模块**。它发现了每个接口上的灾难性失败。但 code review 发生在实现完成之后——延迟了 5+ 个 session，期间系统在积累有毒的 paper trading 数据。

---

## Blueprint v2 会产生第三次失效吗？

如果 Blueprint v2 作为自然语言文档交给 Claude Code 实现，**是的**。

原因：Blueprint v2 说"Position 携带 entry_method，evaluate_exit 根据 entry_method 分发"。Claude Code 会创建 entry_method 字段（一个字符串），会创建 evaluate_exit 方法。但它可能在 evaluate_exit 内部硬编码一个概率计算路径而不检查 entry_method——因为在写 evaluate_exit 的那个 session 里，context window 里没有 entry 的代码，Claude Code 不记得 entry 用的是哪个路径。这正是 P0-6 发生的方式。

Blueprint v2 的 Position 对象有 40+ 个字段。但**字段不是约束**。`entry_method: str` 和 `def evaluate_exit(self)` 可以完美共存于同一个类中而不产生任何交互。Python 不会在 evaluate_exit 忽略 entry_method 时报错。

看看 Blueprint v2 实际增加了什么：
1. Position 对象有 40+ 字段 — 这是一个**数据结构**，不是约束
2. CycleRunner 是纯编排器 — 这是一个**架构模式**，不是约束
3. 退出有 8 层 — 这是一个**功能规范**，不是约束
4. Decision Chain — 这是一个**日志框架**，不是约束
5. Truth hierarchy — 这是一个**优先级规则**，不是约束

全部是文档化的设计意图。没有一个是**机器可检查的约束**。

当 Claude Code 下一次实现一个新模块，那个模块的代码不会被 Blueprint v2 的任何内容阻止引入一个新的接口不变量违规。因为 Blueprint v2 的约束存在于 .md 文件中，不存在于类型签名或自动化测试中。

材料中有一个矛盾揭示了更深的答案：对话中反复说"Rainstorm 的数学是精美的，Rainstorm 死了"。同时也说"Zeus 中被 reviewer 称赞的部分全部来自 Rainstorm 移植"。如果 Rainstorm 的代码好到值得移植，为什么 Rainstorm 死了？

答案：**Rainstorm 的单个模块是优秀的。它的接口不是。** Temperature 类型系统在隔离状态下是杰出的。但它没有渗透到每一个使用温度的位置——bare float 在 14 个月里从 15+ 个缝隙泄漏进来。

这揭示了失效模式的精确性质：**不是坏设计的问题，是好设计的不完整执行。** 设计是正确的。执行是部分的。执行中的缺口就是 bug 居住的地方。

---

## 打破循环需要什么

不是更好的文档。不是更好的 spec。不是更好的 review。不是更好的 Blueprint。不是更好的 Position 定义。这些全部运行在同一条有损的自然语言→代码翻译路径上。

**是可执行的跨模块约束测试，先于实现存在，且由实现者以外的主体编写。**

### 机制 1：类型化的语义边界

Temperature/TemperatureDelta 已经证明了这个模式的有效性——它消灭了一整个 bug 类别。扩展到其他语义域：

```python
# 不是这个（自然语言约束）：
# "概率必须在持有方向空间中"

# 而是这个（类型强制）：
class HeldSideProbability:
    """只能由 Position.to_held_side() 构造。
    下游函数接受 HeldSideProbability 而非 float。
    如果传入 float，TypeError。"""
    value: float
    direction: Literal["buy_yes", "buy_no"]
```

当 executor 收到 HeldSideProbability 时，它不需要猜测空间。当有人尝试传入裸 float 时，TypeError 阻止编译。这消除了 P0-4（方向双翻转）的整个类别——不是通过记住不要犯错，而是通过让错误在类型层面不可表达。

### 机制 2：结构对称性的元测试

```python
# 不是这个（自然语言约束）：
# "退出应有与入场相等的验证严格度"

# 而是这个（自动化检查）：
def test_exit_entry_parity():
    """如果入场增加了一个验证层，退出也必须增加。"""
    entry_layers = count_validation_steps(evaluator.evaluate)
    exit_layers = count_validation_steps(Position.evaluate_exit)
    assert exit_layers >= entry_layers * 0.7, \
        f"Exit has {exit_layers} layers vs entry's {entry_layers}. " \
        f"Blueprint v2 §7 requires comparable rigor."
```

这不是测试行为——它测试代码的**结构**。如果有人在入场路径加了一个新验证但没在退出路径加，CI 失败。约束从文档移到了自动化管道中。

### 机制 3：Position 是跨模块的唯一传递对象

```python
# 不是这个（约定）：
# "所有模块应该通过 Position 传递上下文"

# 而是这个（接口强制）：
class Evaluator:
    def evaluate(self, candidate: MarketCandidate) -> EdgeDecision: ...

class Executor:
    def enter(self, candidate: MarketCandidate, decision: EdgeDecision) -> Position: ...

class Monitor:
    def check(self, position: Position, data: MonitorData) -> ExitDecision: ...
```

当每个模块间的接口只接受和返回类型化的对象（而非 bare float/dict/str），语义上下文在类型层面被强制携带。一个新模块不可能"忘记"传递方向或概率空间——因为它收到的对象已经携带了这些信息。

### 机制 4：可执行的跨模块不变量测试

这些测试的关键属性：
- 它们测试的是模块之间的**关系**，不是模块内部的行为
- 它们在实现之前就存在——它们是 spec 的可执行形式
- 它们由设计者写（或由设计者指导的 reviewer agent 写），不是由实现 Claude Code 写——这打破了"写测试的人和写代码的人有相同的盲点"的循环
- 它们在 CI 中每次 commit 都运行——不是等 code review 或 live trading 才发现

```python
# tests/test_lifecycle_invariants.py
# 这些测试在任何实现代码之前写好
# 它们定义的不是"函数做什么"，而是"模块之间的关系是什么"

def test_exit_uses_same_probability_method_as_entry():
    """P0-6 prevention: exit MUST recompute probability with entry's method."""
    position = create_test_position(
        direction="buy_no",
        entry_method="ens_member_counting",
        entry_p_held_side=0.85
    )
    monitor_data = create_monitor_data(fresh_ens=mock_ens_data())

    with track_probability_calls() as tracker:
        position.evaluate_exit(monitor_data)

    assert tracker.method_used == position.entry_method

def test_position_probability_space_never_flips_after_creation():
    """P0-4 prevention: held-side probability is set once and never re-flipped."""
    position = create_test_position(direction="buy_no", entry_p_held_side=0.85)

    position.evaluate_exit(mock_monitor_data())
    executor_price = compute_limit_price(position)
    harvester_pnl = compute_settlement_pnl(position, settlement_price=1.0)

    assert position.p_held_side == 0.85

def test_pending_order_becomes_tracked_position():
    """P0-5 prevention: a posted order MUST become a portfolio position."""
    order = executor.place_order(mock_edge(), mock_market())
    assert portfolio.has_position(order.token_id)
    pos = portfolio.get_position(order.token_id)
    assert pos.chain_state in ("pending_tracked", "synced")

def test_harvester_uses_decision_time_snapshot_not_latest():
    """P0-7 prevention: calibration pairs use the forecast at decision time."""
    position = create_test_position(decision_snapshot_id="snap_001")
    ensemble_store.save_snapshot("snap_002", different_data())

    pairs = harvester.generate_calibration_pairs(position, settlement_result)

    for pair in pairs:
        assert pair.snapshot_id == "snap_001"

def test_no_trade_case_records_rejection_stage():
    """Decision chain: when we DON'T trade, we record exactly why."""
    mock_market = create_market_with_edge(edge=0.08)
    risk_guard.set_level(RiskLevel.YELLOW)

    artifact = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    no_trades = [d for d in artifact.decisions if not d.should_trade]
    assert len(no_trades) >= 1
    assert no_trades[0].rejection_stage == RejectionStage.RISK_REJECTED
```

当 Claude Code 实现 evaluate_exit 时，如果它忽略了 entry_method，`test_exit_uses_same_probability_method_as_entry` 会**立即失败**。不是在两周的 paper trading 之后。不是在 code review 之后。是在 pytest 的那一秒。

---

## 一年后

取决于一件事：Zeus 的测试套件是否从"验证函数行为"扩展到"验证模块关系"。

如果测试套件仍然只有 `test_platt_predict_returns_correct_value` 和 `test_kelly_size_respects_limit` 这类单模块测试，第四次重组会发生。因为下一个 Claude Code 在下一个 session 修改了某个模块的接口时，没有任何测试会告诉它"你刚破坏了这个模块和另一个模块之间的关系"。

如果测试套件包含 20-30 个跨模块不变量测试——每个测试对应一个曾经的 P0 或一个 Rainstorm 教训——第四次重组不会发生。因为使这些关系失效的代码变更会在提交时被拒绝，而不是在两个月后被 code review 发现。

**循环在不变量从文档移到类型签名和自动化测试的那一刻打破。不早，也不迟。**

---

## 根本原因的一句话

循环的根本原因不是设计者不够聪明。不是文档不够详细。不是 Claude Code 不够能干。**是关系约束从自然语言到代码的翻译过程中存在系统性的信息丢失，而当前没有任何机制检测这个丢失。**

可执行的跨模块不变量测试就是那个机制。它不依赖任何人记住任何东西。它把关系约束从散文变成了代码。代码要么通过要么失败。没有模糊地带。

---

## 给未来每一次实现的指令

在写任何新模块之前，先写它与现有模块的**关系测试**。不是"这个函数输出什么"——是"这个函数的输出传到下一个函数时，什么性质必须被保持"。

如果你无法用一个 pytest 断言表达这个性质，你还没有理解这两个模块之间的关系。回去理解它。然后写测试。然后写代码。

这个顺序不可逆转。
