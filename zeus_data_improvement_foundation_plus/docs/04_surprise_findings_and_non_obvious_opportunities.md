# 额外惊喜：我认为你最不该错过的隐性机会

这些点不只是“data improvement 正常工作”，而是随着 context 变深之后，我认为你很值得直接纳入 roadmap 的内容。

## 1. 概率链条持久化缺口，比再加一个模型更重要

当前 DB 能看到：

- `shadow_signals` 有 `p_raw_json / p_cal_json`
- `opportunity_fact.p_raw` 为空
- `trade_decisions.p_calibrated` 全空

这意味着真正的 probability lineage 还没有统一。

### 为什么这很重要

如果你不先统一 probability trace：

- 无法准确比较不同 calibrator / fusion policy
- 无法知道某笔 trade 真实基于哪个概率链条被选出来
- replay / promotion / regression triage 都会变慢

这是我认为最高 ROI 的“非显眼改进”。

---

## 2. 你手里其实已经有 execution alpha 数据了

`token_price_log` 超过 400 万行。
这不是附属日志，而是一块潜在的新 alpha：

- 哪类边会快速消失？
- 哪类边值得挂单而不是立刻进？
- 哪些城市 / bin / 时段 spread 特别差？
- fill_quality 的主要解释变量是什么？

Zeus 目前显然更偏 forecast / signal sophistication，
但这块数据足够让你做出 **execution-aware Zeus**。

---

## 3. 当前 replay 已经有影子，但还没成为 promotion backbone

当前 DB 里已经有：

- `shadow_signals`
- `replay_results`
- `decision_log`

说明你已经有 replay / shadow 的基础设施苗头。
下一步应该把它们统一接进：

- `model_eval_run`
- `promotion_registry`
- active/shadow/candidate 切换规则

---

## 4. 当前 inventory 不能再依赖手工 truth surface

你上传的 xlsx 在 2026-04-10 10:46 CT 生成，里面反映的是 merge 前后的过渡状态。
它对“数据体量”很有用，但对“当前 main 的真实 readiness”已经不该再作为唯一 authority。

### 建议

让 inventory 完全由 SQL + DB truth 自动生成：

- coverage
- model status
- OOS status
- provenance
- missingness
- risk state

这样你之后每次 huge merge 都不需要再人工判断“现在到底算 ready 还是 not ready”。

---

## 5. `market_price_history` 当前快照只有 1 个 market slug，值得排查

这不一定是 bug，也可能只是 snapshot 采样方式的问题。
但如果你打算做 execution / microstructure modeling，这张表应该被验证：

- 采集逻辑是不是只跟踪了 1 个 market
- 是否应该和 `token_price_log` 合并 / 取代
- 是否需要按 strategy / discovery mode 分开统计

---

## 6. `forecast_skill` 只有 10 城，而 `model_bias` 已经到 38 城

这是一个非常适合“顺手升级”的点：

- `model_bias` 更像 aggregate layer
- `forecast_skill` 更像 fine-grained eval layer

把两者接到统一 evaluation spine 后，你就可以：

- 真正做 lead-aware EMOS
- 做 forecast error correlation
- 做 source gating / source blending

---

## 7. 最适合做成 moat 的，其实是“physics + truth + replay”，不只是 signal math

很多系统都可以做个校准器、做个 bootstrap。
但 Zeus 已经有一些真正可以形成护城河的东西：

- settlement semantics
- point-in-time truth
- Day0 hard floor physics
- replay / canonical position surface
- rich market trace

如果把这些统一起来，Zeus 的优势会比“再加一个小模型”更大。
