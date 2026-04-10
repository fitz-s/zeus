# Zeus 结构性强制规范 (Structural Enforcement Spec)

**状态**: Active
**目标**: 跨越“自然语言指导”与“AI 代码落地”之间的真空地带。将架构意图从 `.md` 文档转化为**物理上不可绕过的编译或执行时约束**。只要这些约束存在，任何 Coding Agent 哪怕在丧失全局上下文的情况下，也无法写出造成“语义丢失”或“生命周期破坏”的代码。

---

## 1. AST 语义防线：抽象语法树拦截器 (AST-Enforced Provenance Linter)

**问题缘由**：AI Agent 倾向于编写局部自洽的函数，容易忽略文档中要求的跨模块数据关联（例如调用退出逻辑时不核对入场方法）。
**强制规范**：
- 引入自定义的 AST (Abstract Syntax Tree) 代码检查工具 `scripts/semantic_linter.py`，并强制挂载为 Git pre-commit hook 及 CI 第一道防线。
- **标记语法**：为核心领域对象（如 `Position`）的属性添加元数据绑定。
  ```python
  class Position:
      # Linter Rule: Any read of p_posterior MUST be lexically preceded or accompanied by a read of entry_method
      p_posterior: float = Field(requires_context=["entry_method"])
  ```
- **工作机制**：当 AI (如 Claude Code) 提交一个对 `evaluate_exit` 的修改时，AST linter 会扫描函数体。如果它发现逻辑分支访问了 `position.p_posterior` 但是在同一执行图谱内没有对 `position.entry_method` 做分支判断，它将直接中断执行并抛出具有极强指导性的错误。
- **Agent 反馈闭环**：报错信息必须能被 Agent 解析：“*Semantic Rule Violation: You accessed a context-dependent probability without evaluating its entry provenance. Fix: Enclose in an entry_method check.*” Agent 看得懂代码错误，看不懂哲学。我们将哲学变成了语法错误。

---

## 2. 数据驱动的生杀大权：结构性数据依赖 (Type-Enforced Data Parity)

**问题缘由**：TIGGE 数据脚本写好了却从未被运行（4.1% 利用率），因为数据在函数签名中是可选的或解耦的。
**强制规范**：
- 彻底废除松散的参数传递（如 `**kwargs` 或可选的字典）。
- 将数据域转换为**类型强绑定 (Strongly-Typed Prerequisites)**。
  ```python
  class Evaluator:
      # 以前: def evaluate(market_data: dict, signals: list)
      # 现在: 缺乏 TiggeSnapshot 连实例化都做不到
      def evaluate(self, ens: EnsSnapshot, tigge: TiggeSnapshot) -> EvaluatedEdge:
          pass
  ```
- **强制机制**：当架构规划要求使用新数据时，首先在核心签名中加上该类型的主键。这个时候系统全部崩溃（因为没有上游提供）。这将**倒逼** Agent 必须去完成 ETL 并在 `CycleRunner` 中将其注水补齐。只要类型编译不过，任务就无法被标记为完成。

---

## 3. 持仓期失聪的解药：动态市场价格感知 (The P_market Velocity Integration)

**问题缘由**：222 个持仓期的市场快照全被浪费。系统在进入持仓后对盘口价格装聋作哑，违背了贝叶斯后验原则。
**强制规范**：
- 规定 `Position` 的 FSM (有限状态机) 在 `holding` 和 `day0_window` 状态下，必须计算价格与概率的“一阶导数（变化速率）”。
- **强制触发器**：在 `evaluate_exit` 时，不仅要求 `(P_model - P_market)` 的绝对值，必须带入 `delta(P_market) / dt`。
- 如果市场价格出现了与模型毫无关联的自由落体滑点（例如市场提前消化了不可知的天气站故障），自动触发强平（Exit Decision: `MARKET_MICROSTRUCTURE_DIVERGENCE`），跳过所有的模型确认，拥抱“市场比模型更早知道”的残酷真相。

---

## 4. 腐败与淘汰机制：硬编码参数的时间锁 (The Decay-by-Default Constant)

**问题缘由**：系统存在 8 个长期硬编码的阈值，它们是导致架构无法自我成长的数据债务。
**强制规范**：
- 废除所有的裸露常量（如 `edge_threshold = 0.015`）。
- 任何无法利用真实历史结算数据推导的常量，必须套用 `OperationalConstant` 衰变类。
  ```python
  class OperationalConstant:
      def __init__(self, fallback_value: float, implemented_at: date, max_lifespan_days: int = 30):
          self.value = fallback_value
          self.expiry = implemented_at + timedelta(days=max_lifespan_days)
          
      def get(self) -> float:
          remaining = (self.expiry - date.today()).days
          if remaining <= 0:
              # 常量已过期，自动将 Kelly 乘数降为 0，停止交易该策略
              raise OutdatedConstantError("Data-driven implementation timeout.")
          return self.value
  ```
- **强制机制**：这相当于给那些临时性的妥协贴上了“定时炸弹”。过了 30 天，如果没有任何 Agent 或开发者编写基于数据驱动的动态回测代码去替代它，整个策略的利润直接归零甚至阻断交易。这就确保了“未来一定会做”变成“必须在炸弹倒计时归零前做完”。

---

## 实施路径部署 (Execution Path)

为了让这个 Spec 真正在当前 codebase 里落地，我们需要按照以下顺序拆解 Task：
1. **优先构建约束环境**：编写 `scripts/semantic_linter.py` 并部署到 pytest 钩子或 git hook 里，建立对抗 AI 漏洞的物理防线。
2. **改写常量与类型签名**：运用 `OperationalConstant` 封存现有的 8 个硬编码，重构 `Evaluator` 签名逼迫 ETL 组装。
3. **闭环测试**：利用 Agent 故意写一段“违反上下文”的退群逻辑，验证新架构是否能准确拦截并提供修复建议。
