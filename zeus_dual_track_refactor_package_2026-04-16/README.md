# Zeus Dual-Track Refactor Package

这个包是给 Zeus 当前 `data-improve` 分支准备的“大型 refactor 作战包”。

目标不是做一些局部修补，而是把 Zeus 从“单指标 daily high 交易机”升级成
“共享本地日历日时间几何、但在高温/低温两条物理轨道上完全隔离”的预测交易机器。

## 你应该按这个顺序读

1. `01_arch_diagnosis_zh.md`
2. `02_target_architecture_zh.md`
3. `03_execution_plan_zh.md`
4. `06_tigge_dual_track_zh.md`
5. `04_schema_v2.sql`
6. `05_code_skeletons.py`
7. `07_validation_and_rollback_zh.md`

## 这个包解决的核心问题

- 为什么现在是 Zeus 最适合大改的窗口
- 为什么 low 不能作为 high 的“参数翻转”
- 如何避免 max / low 数据、校准、运行时相互污染
- 如何把 TIGGE 双轨、DB、校准、runtime 串成真正可落地的迁移序列
- 每一步该改哪些文件、增加哪些契约、用什么验收

## 三条总原则

1. 先建立 metric spine，再重建数据。
2. 共享的是时间几何，不共享训练身份。
3. fallback 可以服务运行时，但不能污染训练集。

## 三条禁止动作

1. 禁止高低温共用一套 calibration_pairs / platt bucket 身份。
2. 禁止把 daily low 当成 `mx2t6 -> mn2t6`、`max -> min` 的机械翻转。
3. 禁止在 low Day0 因果无效时回退到 high 逻辑或伪纯预测 Platt。

## 推荐执行顺序（最短安全路径）

- 先补文档宪法和契约
- 再补 DB / type spine / source authority
- 再把 high 迁移到 local-calendar-day max
- 然后再接 low 的 raw / extract / coverage / ingest
- 最后做 low runtime shadow 与渐进上线
