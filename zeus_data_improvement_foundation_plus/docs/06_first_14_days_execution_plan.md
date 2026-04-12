# 前 14 天执行计划

## Day 1-2
- 应用 migration
- 把 `probability_trace_fact` 接进 decision / trade 写路径
- 先跑 `sql/diagnostics/04_probability_surface_gaps.sql`

## Day 3-4
- 跑 `effective_sample_size.py`
- materialize `calibration_decision_group`
- 把 maturity gate 改成 `decision_groups`

## Day 5-6
- 建 `model_eval_run / model_eval_point`
- 做第一版 blocked OOS calibration report

## Day 7-9
- 产出 Day0 residual training frame
- 训练 shadow residual model
- 按 local_hour 评估

## Day 10-11
- 让 family-wise FDR 接到 evaluator
- 记录所有 tested hypotheses，而不是只记录幸存者

## Day 12-13
- 产出 settlement anomaly correlation 矩阵
- 给 risk throttle 一个数据化输入

## Day 14
- 重新生成 inventory
- 用 DB truth surface 替换手工 readiness 口径
