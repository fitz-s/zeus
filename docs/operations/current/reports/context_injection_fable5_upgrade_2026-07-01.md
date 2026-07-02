# Context 注入体系 Fable 5 升级方案 (2026-07-01)

目标:把 CLAUDE.md / memory / hooks 注入体系升级到 2026-07 agentic 运行语义(Fable 5 harness)。
输入:Fable 5 本体 schema 比对(`scratchpad/claudemd_fable5_delta.md`)、injection 盘点(`scratchpad/injection_inventory_report.md`)、memory 审计(`scratchpad/memory_audit_report.md`)。

## 0. 现状底数

新 session 落地即注入 **~37-38KB (~9-10K tokens)**,排名:

| 来源 | 字节 | 问题 |
|---|---|---|
| MEMORY.md 索引 | 18.2KB | 索引变全文副本,违反自身一行法则;harness 原生注入、无预算帽 |
| ~/.claude/CLAUDE.md | 10KB | 含硬错误级过时 API(Task/TeamCreate/run_in_background-on-Agent) |
| session-start.mjs 聚合 | 6KB 硬帽 | **22KB AGENTS.md 在此被截断**,与 notepad(1.5KB)/banners 抢预算,截断点任意 |
| zeus/.claude/CLAUDE.md | 1.7KB | 基本健康 |
| chatgpt-consult banner | 1.1KB | 健康 |
| worktree visibility | ≤1.5KB | 健康(有帽) |

## 1. CLAUDE.md 升级 (硬错误级,最高优先)

现行 ~/.claude/CLAUDE.md 三处已失效 API,照做会 schema 报错或被静默忽略:

1. `Task(subagent_type, model="haiku|sonnet|opus")` → 工具现名 `Agent`,enum 加 `fable`,新增 `subagent_type:"fork"`(继承全上下文、固定跑父模型)。
2. "ALWAYS pass run_in_background on Agent" → 参数不存在;agent 天然后台、完成自动回唤。并行 = 同一 message block 多 dispatch。
3. `TeamCreate + team_name` → deprecated/ignored;正确形态 = 命名 agent + SendMessage 续上下文。

缺失新原语:Workflow(显式 opt-in 编排,自带 adversarial-verify/judge-panel/pipeline 模式)、ToolSearch 批载 deferred tools、原生 `isolation:"worktree"`、ScheduleWakeup/Monitor 替代轮询。

**提案草稿已写:`~/.claude/CLAUDE.fable5-proposed.md`(~5.3KB,原 10KB)。** 保留:codegraph 路由三角、haiku 输出契约(file:line only)、Code Provenance 全段、language 法则。删除:与 harness 文档重复的编排 pattern 目录、失效 API 段。

## 2. AGENTS.md boot 截断 (结构性修复)

22KB 根法被 6KB 预算任意截断 = 每个 session 拿到的 Zeus 法律是随机前缀。修复方向(择一):

- **A(推荐):写 `AGENTS.boot.md` 摘要(≤4KB)**——mission、money path 一句版、authority 顺序、K1 DB split、INV 硬约束索引、"完整法律按需 Read AGENTS.md"。session-start.mjs 改载摘要,全文留给按需读取。Fable 按需 Read 的纪律远好于旧模型,截断风险 > 缺省风险。
- B:提高 SESSION_START_CONTEXT_BUDGET。治标,且挤压总 context。

## 3. Hooks 清理项 (injection 盘点结论)

- **死槽位**:session-start.mjs `priorityOrder` 里的 `[PROJECT MEMORY]` pattern 无任何 producer——vestigial,删。
- **每 tool call 的 Node 启动税**:pre-tool-use.mjs / post-tool-use.mjs 全局无 matcher,每次 tool call 起一个 Node 进程(0 字节注入但真实延迟)。steer-now.sh 已因此从 Node 改写为 /bin/sh(文件内注释自证 ~47ms/call)。同样待遇候选:二者热路径改 sh 前置守门,或加 matcher。
- keyword-detector.mjs(39KB 脚本)+ steer-guard.mjs 每 UserPromptSubmit 全量跑——同类延迟税,低优先。
- worktree-reaper 双触发(SessionStart + SubagentStop)是设计而非 bug,保留。
- 其余 Zeus dispatch.py 守门 hooks(citation_grep_gate、cotenant_staging_guard 等)全部条件触发、0 常态注入——健康,不动。

## 4. 语义冲突 (操作员拍板项)

1. **caveman full vs Fable 通信法则**。Fable 训练语义:最终消息 readable > concise,完整句、禁 fragments/箭头链;"用户重读一遍,省的 token 全亏回去"。caveman full 每回合与权重打架。建议:降 **lite**,或 scope 化(工具间 status 行任意压缩,最终 turn 消息完整句 + 照旧砍寒暄填充)。
2. **superpowers using-superpowers 全文注入**(EXTREMELY_IMPORTANT + red-flags 表,~4KB/session)。"1% 可能就必须先调 skill"是弱模型反合理化脚手架,与 Fable "有足够信息就行动"相抵。建议砍成 ~6 行触发器清单(brainstorming before creative / systematic-debugging before fix / TDD / verification-before-completion),判断交还模型。
3. **agent-tiers.md 权威**停在 2026-04-02(pre-Fable)。建议权威收回 CLAUDE.md 内联 tier 表(提案草稿已含 fable 行)。

## 5. Memory 清理 (审计 40 文件,已执行机械级修复)

**已执行(备份于 scratchpad/memory_backup_20260701/):**
- MEMORY.md 索引重写:18,207B → 6,752B(−63%);修复第 21 行断裂链接(Redeem 条目);全部 40 行原均 446 字符/行(100% 违反自身 ≤120 法则)→ 一行 hook 版。
- 删除 2 个与 ~/.claude/CLAUDE.md 逐字重复的 memory(codegraph-utility-profile、tool-routing-verified-sweet-spot)——"repo 已记录的不存 memory"法则。
- 修复 2 处 frontmatter `name:` ≠ 文件名(edge-measured-…、no-fills-gated-…)——此前 16 条 `[[链接]]` 中 5 条因此断裂。
- forecast-gap-is-data-precision:机制段标注 SUPERSEDED(cell-distance 死,由 residual 文件取代),操作员法("data precision、never de-bias、加 settlement 站自家预报")保留——residual 文件本身回链它,不删。

**留给操作员拍板的重剪(内容有损):**
- partial-matched-order(~80% 是带 order hash/commit 的破案叙事,法则明确排除"past fix")、no-caps 的 Busan EXTENSION 段、forecast-blackout 的事故流水——各剪至 2-3 句 durable 启发式。
- adverse-selection → capital-gated-rho-mix → emos 三代取代链:可合并为 emos 单文件(保 ρ 公式 + walk-forward 法则)。
- version-suffix-elimination:wave 状态若已完成即属"milestone"应删——需先核对 PR #405 现状。
- 11 条真正 dangling `[[links]]`(指向早已删除的 memory)——低优先,顺手清。

**STALE-VERIFY 待核清单**(下次触碰对应领域时顺带):evaluate_exit 仍是 live owner;deploy_live.py LIVE_REPO 是否已修;9router 是否还在用;omc cache 版本号;state-redesign 分支是否已并。

## 6. 执行状态 (2026-07-01 第二轮,操作员四项全批)

1. **CLAUDE.md v2** ✅ 提案稿重写(~7.5KB):不只做减法,新增 Fable 语义增量——Turn contract(最终消息契约/自治校准/先搜后答)、Context & cache economy(并行 batch、270s/1200s+ 等待经济学、REPORT WRITTEN 契约)、Memory discipline(一行索引法/supersede-don't-accrete/name==文件名)、Workflow budget 指令、agent 最终消息需转述。语言段按操作员要求削弱到只剩术语规则(mirror-language 由 caveman tracker 每回合承担)。待操作员 diff 替换。
2. **caveman hook 改造** ✅ 直接改插件(cache = live):(a) SKILL.md 加 Model scope 节 + tracker.js 每回合文本改为 model-scoped——Fable 主线程只砍 fluff、最终消息完整句、推理永不压缩;sonnet/opus/haiku 级 subagent 输出全压;(b) 修复 activate.js 的 SKILL.md 路径 bug(原找 src/skills/ 不存在,一直走 hardcoded fallback),现真正读 SKILL.md。两 hook 已实测。⚠️ 插件更新会 revert——cache 路径 `plugins/cache/caveman/caveman/18e45320a0b1/`,备忘录已记。
3. **AGENTS.md 前置重构** ✅ 文件头插入 4.1KB Boot Digest(9 节全法压缩,关键法条前置),prefix-slice 截断后 session 拿到完整法律摘要;正文 source of truth 不动。map-maintenance gate ✅;--docs 的 958 条 drift 全部 pre-existing、与本变更无关(root 法:分开报告,不顺手修)。
4. **memory 重剪** ✅ 完成(changelog: scratchpad/memory_trim_changelog.md):
   - 删 3 文件:capital-gated-rho-mix、adverse-selection(ρ=1−exp(−C/W) 公式 + as-of-decision walk-forward 法 salvage 进 emos 文件)、version-suffix(CI antibody `tests/test_no_internal_version_suffixes.py` 已核实在 repo,memory 冗余)。
   - 7 文件重剪至 durable heuristic(去 order hash / commit 号 / 吞吐计数 / 美元数字 / 事故流水):partial-matched 3.7K→1.5K、forecast-blackout 3.4K→1.8K、no-fills 3.1K→1.8K、fok 2.2K→1.6K、settlement-drain 3.3K→2.2K、live-daemon 2.6K→1.9K、no-caps 去 Busan 段(法条原文未动)。
   - dangling `[[links]]` 全清:trimmer 清 11 条 + 主线程补 2 条(本次删除引起)+ 4 条日期后缀漂移改全名。验证:0 dangling。
   - 净结果:40→36 文件,memory 目录 md 内容 118.9KB→~99.4KB;**MEMORY.md 索引 18.2KB→6.3KB(每 session 省 ~12KB ≈ 3K tokens)**。

## 7. 本轮新增净效果 (per fresh session)

| 项 | 之前 | 之后 |
|---|---|---|
| MEMORY.md 注入 | 18.2KB | 6.3KB |
| AGENTS.md boot 切片 | 任意前缀(法律随机截断) | 4.6KB Boot Digest 完整存活(带 §-指针 + reading contract 防"只看 digest") |
| caveman 注入语义 | 全模型 fragments 强制 | model-scoped:Fable 只砍 fluff + 完整句;sonnet/opus 级全压;推理永不压缩;activate.js 路径 bug 修复(此前一直走 fallback) |
| ~/.claude/CLAUDE.md | 10KB 含 3 处失效 API | v2 提案 7.5KB:失效 API 清除 + Turn contract / Context & cache economy / Memory discipline / Workflow budget 增量 |

## 8. 第三轮 (2026-07-01):Node 税 + superpowers + 行业前卫调查

1. **Node 启动税** ✅ 根修法不是 sh wrapper,是补 matcher(hook 源码只处理特定工具却注册成全量):
   - `PreToolUse` pre-tool-use.mjs:无 matcher → `"Bash|Edit|Write|MultiEdit|NotebookEdit|Task|Agent|Skill|SendMessage"`
   - `PostToolUse` post-tool-use.mjs:无 matcher → `"Skill|Task|Agent"`
   - 效果:Read/Grep/Glob/MCP 等大宗调用(一个 session 数百次)不再各起一个 Node 进程(~47ms/次)。steer-now.sh 保持全量(已是 /bin/sh,设计如此)。JSON 校验通过。
2. **superpowers 注入** ✅ using-superpowers/SKILL.md 3.1KB→1.4KB:弃"1% 就必须"+red-flags 反合理化墙(弱模型脚手架),换判断优先 + 6 行触发表 + 优先级规则。SessionStart 注入自动缩水(hook 是 cat SKILL.md)。⚠️ 同 caveman:插件更新会 revert。
3. **行业前卫调查** 🔄 ChatGPT Pro consult 已发(REQ-20260701-150857,gist=提案稿+审计报告),覆盖 (a) CLAUDE.md/AGENTS.md 先锋方法论 (b) agentic search vs RAG (c) context/loop engineering;本地 WebSearch 已确认:业界 2025-05 起弃 embedding RAG 转 agentic search(Anthropic/Cursor/Devin/Amp 全部 grep+工具循环;Amazon AAAI 2026: agentic keyword search = 94.5% RAG faithfulness 零向量库),Zeus 的 codegraph 路线正确。官方 best-practices 新杠杆待并入 v3:逐行 prune 测试("删了会犯错吗?")、compaction 保留指令、advisory→hook 迁移原则、按需知识走 skills、@import 分层。
4. 未动(有意):keyword-detector/steer-guard(每 prompt 一次,量级无关紧要)、code-review-graph per-edit 增量索引(有真实价值)。

## 9. 第四轮:consult 裁决 + 前卫分解落地 (2026-07-01)

Consult(REQ-20260701-150857,答案 /tmp/cgc_answer_REQ-20260701-150857-127dc8.txt)BLOCKER 裁决:**monolith 全局 CLAUDE.md 是反模式**;2026 前卫分层 = ≤3KB 全局 bootstrap(跨项目操作员契约)+ 项目 boot digest(定位器)+ path-scoped rules + skills(按需全文)+ hooks(强制层)。与官方 docs 一致("长文件降 adherence、<200 行、程序性内容进 skill")。

**接受并落地:**
- 全局提案稿重写为 3.97KB bootstrap(`~/.claude/CLAUDE.fable5-proposed.md`):Turn contract / Context economy(含 compaction 保留指令、2-corrections 法)/ Agents(标注 schema 验证日期 2026-07-01)/ Retrieval meta / Memory / Style / Provenance 法条版。
- Repo-specific 内容下沉 `zeus/.claude/CLAUDE.md`(1.7KB→2.6KB):Retrieval router 全表(rg/codegraph/semantic 三层 + Read-before-assert)、provenance 触发器、长任务 durable state 契约。
- 程序性 checklist 进 skill:新建 `zeus/.claude/skills/provenance-audit/SKILL.md`(四步审计 + verdict 词表 + 文件头契约)——全局只留法条一段。

**本地裁决否决/降级的 consult 建议(带理由):**
- "Agent/fork/SendMessage/ToolSearch 语法未验证"[HIGH] → 降级:已对照本 session live tool schema 逐一验证正确;仅采纳"标注验证日期"。
- trading-risk rules 文件 + destructive-shell PreToolUse 阻断器 → 拒收:与 AGENTS.md §2/§3、riskguard、kill-switch、maintree_git_state_guard 重复,且违反操作员 no-gate-accretion 法(gate-mass is the disease)。
- memory linter hook → 拒收:会重新引入刚移除的 per-edit 进程税;≤120 法已写进 MEMORY.md 头 + bootstrap 一行。
- cache 数字删除 → 部分拒收:270s/1200s 出自本 harness ScheduleWakeup 文档,是 live-schema 事实非猜测,保留。
- "reasoning never compressed" 措辞 → 保留(操作员显式指令),另采纳其 evidence-preservation 补充进 compaction 行。

**分层后每 session 常驻注入(全部落地后):bootstrap ~4KB + zeus CLAUDE ~2.6KB + AGENTS boot digest ~4.6KB + MEMORY 索引 6.3KB + banners ~2.5KB ≈ 20KB(初始 37-38KB,−47%),且每 KB 都过了"删掉会犯错吗"测试。**

## 10. 第五轮:执行到位 (2026-07-01,操作员裁决"裁决过的要做到位")

**逐行 prune pass(每行问"删了会犯错吗?"),全部落地:**
- 全局 CLAUDE.md **正式切换**:10KB→3.3KB(备份 `~/.claude/CLAUDE.md.pre-fable5.bak`;提案文件删除,不再有双份)。砍掉:header 元解释、"industry-settled"理由句、tier 表→单行、provenance 细则(进 skill)。
- zeus/.claude/CLAUDE.md 2.6KB→1.5KB(Review 段 Tier 0 清单收缩为指针——REVIEW.md 已是权威)。
- MEMORY.md 索引:35/35 行全部超 120 → 重写 + 二次修 8 行(awk 按字节,em-dash=3B)→ **4.3KB,linter 全绿**。
- AGENTS.md Boot Digest 4.6KB→4.4KB(reading contract/END 哨兵压缩,§-指针全保留);map-maintenance gate ✅。
- caveman Model-scope 段 4 bullet→3 行;chatgpt-consult ACTIVATION 1.1KB→0.6KB。

**consult 强制层(此前被我否决,操作员改判执行,全部 sh 零 Node 税,已实测):**
- `~/.claude/hooks/destructive-git-guard.sh`(PreToolUse Bash):dirty tree 上的丢弃类 git(`checkout .`/`checkout HEAD --`/`clean -f`/`reset --hard`)→ BLOCK 提示 stash;force-push main/master → BLOCK;`DESTRUCTIVE_GIT_OK=1` bypass。5/5 测试通过(dirty-block/force-block/innocent-pass/bypass-pass/clean-pass)。与 zeus maintree_git_state_guard 互补(它管主树劫持,这个管全局丢工作类——transcript-replay 事故的直接强制化)。
- `~/.claude/hooks/context-lint.sh`(SessionStart):bootstrap>4.5KB / 项目 CLAUDE>3.5KB / MEMORY>7KB / 索引行>120B / 全局配置含失效 API 名 → 显式警告,干净时零字节。首跑即抓到 4 项真实违规(含 harness auto-memory 把索引又写胖了)——这就是 prose 法则需要 linter 的实证。
- `zeus/.git/hooks/commit-msg`:确定性剥离 Co-Authored-By/Claude-Session/Generated-with trailer(prose 覆盖→强制层)。实测通过。

**最终常驻注入 ≈ 16-17KB(初始 37-38KB,−55%+):** 全局 3.3 + zeus 1.5 + digest 4.4 + 索引 4.3 + banners(superpowers 1.6 + consult 0.6 + caveman 精简版)。违规不再靠自觉——linter 每 session 盯。

**仍属 advisory(有意,不再加 gate):** trading-risk 边界(riskguard/kill-switch/AGENTS.md §2 已是系统级强制)、memory 内容质量(linter 管形状,不管语义)。

## 11. 第六轮:必要性审计 + "实际应用"验证 (2026-07-01)

**逐条 memory 必要性(标准:代码/repo 是否已接管),证据驱动删 5 条(备份 .round5 后缀):**
- edli-pre-submit-…-ghost — 恢复已进 `src/execution/command_recovery.py`(reconcile_abandoned_unsubmitted_ghosts)
- exit-stuck-…-fok — FOK→FAK 修复已进 `src/execution/executor.py:4106`,代码注释即文档
- partial-matched-… — never-escalates 处理已进 `command_recovery.py:9286`
- state-model-redesign-thread — 分支已不存在,derive_position_phase 已在 `src/state/canonical_projections.py`
- forecast-gap-… — 操作员法一句折入 forecast-residual 文件,机制早已被取代
净:**40 → 30 条**,索引 3.7KB。全部 `[[links]]` 复验零 dangling。保留的 30 条均过必要性(operator 法/工作流/诊断启发式,repo 无法呈现)。

**元注释清除:** CLAUDE.md "schema verified 2026-07-01; re-verify…" 删除(§Agents 标题裸化)。

**"添加的内容是否实际应用"——全部跑真实路径验证,不靠假设:**
| 项 | 验证方式 | 结果 |
|---|---|---|
| superpowers 精简 | 直接跑 hooks/session-start | 1,857B(原 ~4KB)✓ |
| caveman model-scope | 直接跑 activate.js | 2,387B 含 Model scope ✓ |
| CLAUDE.md 新增(compaction 保留/2-corrections/ToolSearch 批载/REPORT WRITTEN) | grep 正式文件 | 4/4 ✓ |
| context-lint | SessionStart 注册 + 手跑 | 全绿 ✓ |
| **Boot Digest 完整进 slice** | **模拟真实 session-start.mjs** | **初测 FALSE——notepad 等前置占 3.9KB,digest 被截尾**。修复:AGENTS 头部再砍 ~600B + SESSION_START_CONTEXT_BUDGET 6000→9000(~/.claude/hooks/session-start.mjs:143)。复测 `digest-complete-in-slice: True` ✓ |

净账:预算 +3KB 换根法完整交付;总常驻仍 ≈ −18KB vs 起点。⚠️ revert 面三处(插件更新会覆盖):caveman cache、superpowers cache、session-start.mjs 预算行。

## 12. 第七轮:标准纠偏——"删了会犯错吗",不是"验证不了就留" (2026-07-01)

操作员纠正 prune 标准后重过全部 30 条,再删 6(备份 .round6):
- **重复即删**(内容已被更权威表面接管):agent-report-to-file(→ CLAUDE.md context economy)、parallel-agents-clobber(→ CLAUDE.md agents 节)、consult-waiter-stub(→ 修复已写进 chatgpt-consult skill 本文,"heartbeat 现在追踪最大消息")
- **一句折入 SPOT 后删**:diagnose-order-emission("live-fire submit path first, bisect venue→backward")、monitor-exit-evaluate-exit("rebuild 前先 grep 现有 owner")、edge-measured(其余部分被 verify-alpha + no-fills 覆盖)
- 8 处 [[diagnose-order-emission]] 引用统一重指 [[how-to-work-spot-plan-execute]];链接零 dangling;lint 全绿。

**终态:memory 40→24 条(−40%),索引 3.05KB(起点 18.2KB,−83%),目录 196K→112K。** 留下的 24 条每条都能答出"删了会犯哪个具体的错":操作员法 8(no-caps/no-shadow/no-fixed-number/flag-freeze/ship-decision/redeem/backtest/forecast-gap-law-in-residual)、工作流契约 4(SPOT/auto-mode/consult-authorized/omc-fork)、活跃项目 2(EMOS/residual)、独有机制+诊断 10(3-gates/entries-pause/snapshots-DB/9router/deploy/blackout/backlog-drain/transcript-recovery/live-source-parity/alpha-winrate)。

## 13. 第八轮:精工化 + 可执行项收尾 (2026-07-01,操作员定调"十四行诗")

**目标从最小化升级为精工化——每行既是法条又是好句子:**
- 全局 CLAUDE.md 重写为格言体(3.17KB):The turn / The context / The agents / The search / The memory / The voice / The code you did not write。平行句式、对仗、强动词("Finish, then speak"、"haiku enumerates… sonnet builds. opus untangles. fable orchestrates and judges"、"Running proves nothing but running")。内容零损失,语言全重铸。
- zeus/.claude/CLAUDE.md 同体裁(1.29KB):Finding / Touching / Reviewing / Sizing / Lasting things。
- MEMORY.md 头三句化("config, git, and docs own those, and they rot here")。

**剩余可执行项全部进 linter(context-lint.sh 扩展,SessionStart 一次、干净时零输出):**
- revert-watch ×3:caveman Model-scope 消失 / superpowers >2500B / session-start 预算≠9000 → 显式报警 + 指回报告 §8/§10 重施步骤。
- notepad Priority >2500B 报警;AGENTS head-through-digest >5000B 报警(slice 装不下预警)。

**linter 首跑立抓第五个真问题:notepad.md 已烂到 482KB** —— 8 个重复 Priority Context 段 + 24 个同秒时间戳的重复 Working Memory 条(6-14 累积 bug);原生 notepad_prune 空转(声称清 16 条,文件原样)。手术(备份 scratchpad/notepad_backup_20260701.md):只留当前 Priority(CWA/HKO)+ Manual 段 → **2.5KB(−99.5%)**。

**终验(真实路径):boot 模拟 digest-complete ✓;lint 全绿零警告;四大自有面合计 9.95KB**(全局 3.2 + zeus 1.3 + 索引 3.0 + notepad 2.5)。

**明知未做(需操作员输入/无法脚本化):** 插件/skill description 面裁剪(最大剩余杠杆,需圈定在用清单)、adherence 行为抽查(需跨 session 观察)。

## 14. 第九轮:post-cutoff delta——只收活源验证的增量 (2026-07-01)

操作员两次纠偏定住方法:(a) 不重述已删的;(b) 训练先验≠2026-07 共识,只有活源(live browse + 多源交叉)算数。我凭先验加的 9 条"社区共识"全部回滚——事后逐条对照,每条都已有 skill/hook/memory 归属,回滚双重正确。

**活源验证通道:** followup consult(浏览 Q2-2026 vendor docs/changelog/practitioner posts,答案 /tmp/cgc_answer_delta2026.txt)+ 本地搜索(GitHub 官方 2500 仓库分析、ICLR 2026 AMBIG-SWE、Augment/Morph/Osmani 2026 指南)。两通道独立收敛。

**Consult 裁决:没有缺失的宪法;一处伪共识修正 + 按优先级的小增量,"替换不增长"。已按十四行诗体落笔(bootstrap 3.17→3.83KB,lint 绿):**
1. [HIGH-修正] "embeddings lost to grep" 是我写的过时先验——Q2 实况 = hybrid retrieval。替换为四路由:rg / semantic / graph / deep source,后接 Read-before-assert。(Cursor Q2 SDK、Sourcegraph Q2 双源)
2. [HIGH] agent-config 供应链:skills/hooks/MCP/plugins 是可执行供应链,enable/update 前审 diff、禁盲导。(arXiv prevalence study + AgentShield + Anthropic containment + OpenAI 四源)并入 "The code you did not write" 段。
3. [MED] 风险拨盘:被 guard 挡 → 先收窄(read-only/dry-run/缩 scope)再问——与本地 auto-mode-classifier memory 独立收敛,现为行业共识。(Cursor Auto-review + Anthropic Q2 changelog)
4. [MED] verifier 有界循环:自跑的 loop 需可测停机(/goal——2026-05 新原语、Stop hook、budget)。(Anthropic /goal docs + Osmani loop-engineering + OpenAI Codex 三源)
5. 本地搜索补充:规则冲突显式优先级(ICLR 2026 AMBIG-SWE:无排序→跳过验证)→ header 加优先级脊柱一行;模糊需求问一句/模糊实现点名假设(AMBIG-SWE:静默硬做 resolve rate 48.8%→28%)。
6. 拒收(consult 优先级 5 及以下):remote env preflight(背景 agent 均为本地 harness 追踪)、workflow-as-code 审查(workflow 本就 opt-in 稀用)、tool-schema smoke test 自动化(改为 harness 升级后手动跑一次)。

**Consult [NIT] 显式背书:** 没有任何 Q2-2026 源支持把 root-cause/YAGNI/plan-before-code/never-weaken-tests/scope-discipline/secrets 加回全局——它们作为 skill/hook/memory/repo 层归属是正确的,全局文件保持小。

**操作员终裁两处(第十轮):** (1) verifier-bounded-loop 行删除——那是"如何造 loop"的 loop-engineering 视角,归 workflow/skill 层,不属于操作契约;(2) Agents 段适度还原被砍过头的实操细节:delegate-vs-DIY 判据、fork 为深分析默认、SendMessage 续用优先于重生、explorer/verifier/implementer 三角色分立。

**第十一轮(操作员):** (1) 删 "done is a claim — grep the real file"(冗余:agent 报告 commit SHA,核证走 SHA)→ 改为 "done-claims carry a commit SHA";(2) **Workflow 升为常设授权**——不再需要 "ultracode" 口令;agent 默认 sonnet,opus 只花在决定成败的位置(judge/终验/最难切片);"+Nk" 仍是硬顶;显式记录"该工具的正确用法仍待发掘,发现有效形态即沉淀"。超预算 118B 被 linter 当场抓获,砍回 4.48KB,lint 绿。
