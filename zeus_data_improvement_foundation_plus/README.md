# Zeus Data Improvement Foundation Plus

这不是对旧 `DATA_IMPROVEMENT_PLAN.md` 的增补，而是一套**建立在已落地 foundation 之上的第二层深化包**。它假设：

1. `zeus_mature_project_foundation` 的 architecture kernel / lifecycle / canonical event surface 已经合并进 `main`。
2. 你的下一步不是再重做一遍 authority，而是把 **data truth / statistical truth / trading truth** 从“已有骨架”推进到“可持续学习的生产级系统”。

## 这个包解决什么

它聚焦 7 个高收益方向：

- **P0 概率链真相层统一**：把 `p_raw -> p_cal -> p_market -> p_posterior -> selection` 的完整轨迹变成可审计事实。
- **P1 Calibration v2**：从“pair rows”升级到“decision-group aware”的有效样本量与 blocked OOS 评估。
- **P2 Day0 residual v2**：把现在的 observation hard floor + heuristic seam，升级成可学习的剩余上行模型。
- **P3 Bias / Uncertainty v2**：从按城/季均值 bias，推进到 lead-aware distributional correction（EMOS 风格）。
- **P4 Selection / Risk v2**：family-wise FDR、相关矩阵数据化、组合风险预算。
- **P5 Replay / Promotion v2**：shadow -> candidate -> active 的统一 promotion pipeline。
- **P6 报表与 lineage**：所有 inventory / readiness / model status 从同一 truth surface 自动生成。

## 你会在包里看到什么

- `docs/`：完整改进方案、数学与统计升级、落地顺序、惊喜发现
- `migrations/`：建议新增的 SQLite schema
- `sql/diagnostics/`：你本地可直接跑的审计 SQL
- `src/`：可作为 patch 起点的 Python example code
- `tests/`：最核心的统计/选择逻辑单测
- `artifacts/audit/`：基于你上传 DB 快照生成的审计 CSV

## 推荐阅读顺序

1. `docs/01_repo_db_deep_audit.md`
2. `docs/02_mathematics_and_statistics_upgrade.md`
3. `docs/03_target_architecture_and_rollout.md`
4. `docs/04_surprise_findings_and_non_obvious_opportunities.md`

## 最重要的一句话

**Zeus 的下一阶段重点不应该是“再加更多 feature”，而应该是把已有 feature 接入同一个可回放、可比较、可提升的学习真相层。**
