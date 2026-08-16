# PerfGuardian Phase 3.1 诊断校准交接报告

日期：2026-08-14
Analyzer 输出版本：`0.4.0-experimental`
真实回归 run：`0dfe53bc-db98-41a6-9735-bfd1cd0cc585`

## A. 修改内容

- 事件模型：将帧事件区分为 `severe_hitch`、`hitch`、`budget_miss`、
  `pacing_state`，并将 `gc_activity`、`memory_anomaly` 作为独立活动事件。
- 分级规则：`>=50 ms` 为 severe/P0，`33.33–50 ms` 为 major/P1；轻度预算
  超限为 minor/P3 且默认不可行动；持续 pacing 为 info/P3，不因持续帧数
  自动升级。
- 归因变化：稳定为 `cpu_bound_candidate`、`gpu_bound_candidate`、
  `gc_participating`、`frame_pacing`、`mixed`、`unattributed`；CPU/GPU 必须
  在最严重帧解释至少 80% 帧时间。新增 `unexplained_frame_time_ms`，明确它
  只是 `frame - max(CPU, GPU)` 的诊断提示，不是严格分解。
- 合并规则：候选慢帧允许最多 1 个正常帧、最多 50 ms 时间间隔；事件输出
  `frame_count`、`slow_frame_count`、持续时间、峰值、均值和原始 CSV 行号。
- 首页降噪：突出 Actionable、Severe、Major、Top 5；budget miss、pacing、GC
  分开统计。详细事件支持 All/Severe/Major/Budget/Pacing/GC/Unattributed
  离线筛选，非行动事件默认折叠。
- 体积优化：移除完整逐帧 `timeline`；9476 帧聚合为 862 个图表桶，并额外
  保留 39 个行动事件邻域精确点。完整事实仍在 `frames.csv`。
- HTML 所有数量均消费 `diagnostic_summary`，不维护第二套诊断算法。

Unity 公共数据协议未修改；Unity 工程和 Collector 未修改。

## B. 修改文件

- `D:\PerDec\analyzer\__main__.py`
- `D:\PerDec\analyzer\core.py`
- `D:\PerDec\analyzer\diagnostics.py`
- `D:\PerDec\analyzer\report.py`
- `D:\PerDec\analyzer\report_v31.py`
- `D:\PerDec\tests\test_phase3.py`
- `D:\PerDec\tests\test_phase31_calibration.py`
- `D:\PerDec\docs\phase-3-1-diagnostic-calibration-report.md`

## C. 测试

命令：

```powershell
C:\Users\一点\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -m unittest discover -s D:\PerDec\tests -v
```

- 总测试数：36
- 通过：36
- 失败：0

新增 fixture 明确为 Analyzer 自动测试数据，不是正式性能实验。覆盖稳定运行、
108 ms 未知长帧、CPU/GPU 长帧、GC 重叠、165 Hz budget miss、持续 pacing、
相邻慢帧合并、未解释帧差、稳定内存、持续增长候选、图表降采样与真实60秒
artifact 回归。

## D. 真实60秒运行前后对比

| 指标 | 修改前 | 修改后 |
|---|---:|---:|
| Actionable issues | 未建模（旧 severe+major 代理值 13） | 2 |
| Severe hitches | 4（含相对倍数与 pacing 放大） | 2（绝对 >=50 ms） |
| Major hitches | 9（含相对倍数） | 0（绝对 33.33–50 ms） |
| Budget misses | 46 个旧 minor incident 代理值 | 37 个合并事件 / 136 个慢帧 |
| Pacing states | 2 个归因事件 | 8 个 info 状态 / 464 帧 |
| Unattributed actionable | 旧模型无明确字段 | 1 |
| `analysis.json` | 5,936,513 bytes | 2,365,344 bytes（-60.2%） |
| `analysis.html` | 1,268,131 bytes | 254,943 bytes（-79.9%） |

回归要点：

- 107.999 ms 事件保留为 `severe_hitch / P0 / unattributed`。
- 69.220 ms 事件保留为 `severe_hitch / P0 / cpu_bound_candidate`。
- 原约116帧 pacing 区间在更低预算超限阈值下扩展合并为244帧状态，但保持
  `pacing_state / info / P3 / not actionable`，不再作为 severe 性能故障。
- 4 个 GC 窗口作为活动和参与证据，不声明根因。
- 内存继续为 `stable`。
- Top Issues 仅包含上述两个真正需要调查的严重长帧；9 ms 附近事件不再
  淹没首页。

输出：

- `D:\PerDec\reports\runs\0dfe53bc-db98-41a6-9735-bfd1cd0cc585\analysis.json`
- `D:\PerDec\reports\runs\0dfe53bc-db98-41a6-9735-bfd1cd0cc585\analysis.html`

## E. 仍无法归因的问题

- 107.999 ms 长帧现有 CPU/GPU/GC/Marker 无法解释，保持 `unattributed`。
- 当前没有进程调度、IO、前后台/窗口状态、进程 CPU 占用或系统 GPU 利用率。
- CPU/GPU 帧时间不是严格可加字段；`unexplained_frame_time_ms` 只能提示缺口。
- Marker 集不构成完整 PlayerLoop 或调用栈，不能定位具体方法。

## F. Phase 3.2 handoff

Windows External Process Monitor 建议字段：

- `process_cpu_percent`
- `working_set_bytes`
- `private_bytes`
- `thread_count`
- `io_read_bytes`
- `io_write_bytes`
- `process_status`
- `foreground_window_state`
- 可选 `gpu_utilization_percent`
- 可选 `vram_used_bytes`

本轮未实现这些字段。

## G. Git状态

- branch：`codex/chore-stage2-baseline`
- HEAD：`09013c51b029a0b28f37009e5a18f2961f9790f6`
- working tree：存在阶段三及阶段3.1未提交修改
- 未执行 commit、push、merge、rebase 或 reset
