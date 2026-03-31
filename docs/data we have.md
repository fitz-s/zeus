数据库现状

当前 rainstorm.db 里的核心量级是：

settlements = 1643
actual_temp_f 非空 = 1629
actual_temp_source = wu_daily_observed = 1627
forecasts = 171003
observations_total = 240234
hourly_all = 219519
daily_all = 20715
wu_daily_observed = 4136
hko_daily_extract = 296
openmeteo_archive_hourly = 114168
这说明现在的系统已经有足够厚的观测层、truth 层和 forecast 层，不再是最初那种“只能拿 sparse day1”的状态。

Truth 数据

现在 truth 侧的主力已经非常清楚：

WU 是主力 truth 源
HKO 是 Hong Kong 的特例主 truth 源
IEM/NOAA 仍然存在，但主导性已经被 WU 压过去了
按城市看，当前 WU/HKO 最厚的是：

London = 664
NYC = 658
Austin = 301
Dallas = 298
Houston = 298
Hong Kong (HKO) = 296
Tokyo = 288
Shenzhen = 210
Munich = 206
中等厚度的还有：

Seattle = 135
Atlanta = 124
Shanghai = 119
Seoul = 118
Wellington = 115
Buenos Aires = 107
Chicago = 104
仍然偏薄的主要是：

Miami = 100
Los Angeles = 96
San Francisco = 96
Denver = 55
Paris = 44
Settlement truth 覆盖

当前 settlements.actual_temp_f 已非空 1629 条。
按 city/source 看，settlement universe 里已正式落到 actual_temp_f 的主要是：

Atlanta / Chicago / Dallas / London / Los Angeles / Miami / NYC / Paris / San Francisco / Seattle
其中绝大多数是 wu_daily_observed
NYC 还有少量 iem_asos
也就是说，原始核心结算城市的 truth 基本已经站住了。

51-source 现状

51 source data 目录现在已经是一个独立的数据工作区，主清单是：

full 38-city manifest
city_count = 38
分层上有：

主 21 城 manifest
full 38 城 manifest
当前 ECMWF Open Data 已经补满：

38 城 × day1..day7
当前扫描 gap_count = 0
也就是说，近实时 ensemble 这边现在已经是齐的。

TIGGE 则是历史主线，还在继续补：

当前 city_count = 38
当前扫描 gap_count = 515
现在最空的历史坑主要集中在：

2024-01-01 的 day2..day7
2024-01-15 的 day1..day7
以及 2024/2025/2026 Q1 大量还没铺满的日期点
这和我们的策略一致：
Open Data 负责“今天到 day7”，TIGGE 负责“历史回测梯子”。

自动循环

现在自动循环已经接进了：

TIGGE
ECMWF Open Data
WU
HKO
入口和状态文件是：

run_gap_fill_cycle.py
run_gap_fill_cycle_supervisor.py
gap_fill_cycle_supervisor_status.json
gap_fill_cycle_last_run.json
当前 supervisor 正在等待活跃 worker 完成。
此刻活跃的是一条 TIGGE worker，在补 2026-03-31 的 day2..day7。

