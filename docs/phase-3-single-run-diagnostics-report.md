# PerfGuardian 阶段三：单次运行深度诊断实施报告

报告日期：2026-08-14
项目根目录：`D:\PerDec`
范围：Python Runner、Analyzer、测试、文档与离线报告
Analyzer 输出版本：`0.3.0-experimental`

## 当前状态

阶段三单版本、单次运行诊断已经完成。一个 Runner 质检合格的运行不依赖
历史版本或至少三次重复，即可生成独立的 `analysis.json` 和可直接打开的
离线 `analysis.html`。本阶段没有实现 baseline/candidate 比较、回归门禁或
CI 阈值，也没有修改 Unity 工程或采集端。

开始实施时的 Git 基线为：

- Git 根：`D:/PerDec`
- 分支：`codex/chore-stage2-baseline`
- HEAD：`09013c51b029a0b28f37009e5a18f2961f9790f6`
- 工作树：开始时干净

本阶段没有执行 `commit`、`push`、`merge`、`rebase` 或 `reset`。

## 实现内容

### 单运行选择与输出隔离

Analyzer 现支持：

```powershell
python -m analyzer --artifacts-root artifacts --output-dir reports --run-id <run_id>
python -m analyzer --artifacts-root artifacts --output-dir reports --latest
```

`--run-id` 必须在 Runner 报告中精确匹配一次、唯一且
`eligible_for_analysis=true`；不存在、重复或不合格都会明确报错。
`--latest` 只比较合格运行的 Runner `completed_at`，先解析为 UTC，再选择
时间最大者；时间相同时使用字典序最大的 `run_id`，并把规则、候选数和
选择时间写入报告。

每次输出隔离为：

```text
reports/runs/<run_id>/analysis.json
reports/runs/<run_id>/analysis.html
```

原命令 `python -m analyzer --artifacts-root artifacts --output-dir reports`
仍为兼容的全量扫描入口，继续输出根目录的 `analysis.json` 和
`analysis.html`，不执行多版本性能比较。

### Runner 自动完成体验

Runner 的一次重复运行通过既有质检后，会直接对刚生成的运行目录调用
Analyzer，并将 `runner_automatic` 选择依据写入单运行报告。终端的 Runner
JSON 输出包含 Analyzer 状态以及 `analysis.html` 绝对路径。

Analyzer 失败只记入 Runner 自己的 `automatic_analysis.status=failed` 和
错误消息，不修改 Unity 的 `run.json`，不篡改原始产物，也不会把已经合格
的 Runner 质检结论改成失败。

## 实际操作流程

1. 在 `D:\PerDec\perfguardian.local.json` 配置 Unity Standalone EXE、项目、
   实验、场景和构建追溯字段。
2. 在 VS Code 选择“PerfGuardian：运行测试”并按 F5，或在项目根运行
   `python -m runner`。
3. Runner 为每次重复生成新的 UUID 目录，使用参数数组传入全部
   `--pg-*` 参数，并使 `-logFile` 与 `--pg-output` 指向同一目录。
4. 游戏自动结束或用户按既定场景结束；Runner 验证退出码、四项产物、
   manifest、事件尾项、帧数据、覆盖率和日志。
5. 合格运行自动生成该 run 独立的 JSON/HTML；终端显示绝对路径。
6. 对已有数据可使用“PerfGuardian：分析最新合格运行”或
   “PerfGuardian：按 Run ID 分析”VS Code 配置，也可运行上面的 Analyzer
   命令。

## 异常区间检测规则

所有参数集中在 `analyzer/diagnostics.py` 的
`DIAGNOSTIC_THRESHOLDS`，并完整写入每份 `analysis.json`，状态标记为
`experimental`。

单帧候选阈值为：

```text
adaptive = median(frame_time) + max(6 × MAD, 2 ms)
budget = 实际帧预算 × 1.5
effective = min(50 ms, max(adaptive, budget))
候选帧：frame_time_ms >= effective
```

帧预算优先使用正数 `target_frame_rate`；否则使用
`display_refresh_rate_hz` 和 `v_sync_count`；仍不可得时只使用分布阈值和
50 ms 绝对规则。P99 继续保留为阶段二兼容统计，但不再被当作正式事故
阈值。

连续候选帧合并为一个 incident；两个候选之间最多允许 1 个正常帧，短
正常间隔也包含在同一事故的起止区间。每个 incident 输出：ID、帧和时间
范围、持续帧数/时长、最大/平均/累计帧时间、相对稳态中位数的超额耗时、
严重程度、最严重帧、前后 3 帧证据窗口、GC 重叠、诊断证据/反证/限制和
原始 CSV 行号。

严重程度综合最差帧相对稳态中位数的倍数与绝对耗时：major 为至少 2 倍
或 33.3 ms，severe 为至少 3 倍或 50 ms；其余候选为 minor。这些规则仍
是实验规则，不等同于产品 SLA。

## 归因规则及边界

分类是确定性的启发式证据整理，不是调用栈结论：

- `gc_participating`：incident 与连续 GC Marker 工作窗口发生帧范围重叠；
  同时输出 GC 总 Marker 时间不足以解释整帧时的反证。
- `frame_pacing_wait`：实际帧时间接近帧预算，且
  `wait_for_target_fps_ms` 至少占帧时间 25%。等待不能被解释为脚本或 GPU
  计算变慢。
- `cpu_or_main_thread_side`：在同一个最严重帧上，CPU 时间高于 GPU 时间
  1.25 倍，并且 CPU 时间至少覆盖该帧的 80%。Marker 只列峰值，不求和
  为 CPU 总时间。
- `gpu_or_render_side`：在同一个最严重帧上，GPU 时间至少为 CPU 的 90%，
  并且 GPU 时间至少覆盖该帧的 80%。没有系统 GPU 利用率时明确保留限制。
- `unattributed`：字段缺失、CPU/GPU 都不足以解释整帧，或证据冲突时
  保守降级，不能为了给出原因而强行分类。

每个分类固定包含 `classification`、`confidence`、`evidence[]`、
`counter_evidence[]` 和 `limitations[]`。报告明确说明：当前数据没有进程
CPU 占用率、系统 GPU 利用率、调用栈或完整 PlayerLoop 树；Profiler
Marker 可重叠且可能包含 Collector 与同步 CSV 写入开销。

## GC、分配与内存规则

连续 `gc_collect_ms > 0` 的帧仍合并为一个 GC 工作窗口。专项结果包括工作
窗口数、总 Marker 时间、最大窗口时间、与 incident 重叠的窗口及对应
incident ID。

分配尖峰使用当前运行 `gc_allocated_bytes` 的中位数和 MAD：

```text
threshold = median + max(8 × MAD, 256 bytes)
```

若采集端声明含 Collector 开销，JSON 和 HTML 都保留此限制；尖峰只适合
同版本相对定位。

内存对 `memory_used_bytes`、`unity_reserved_bytes`、
`unity_unused_reserved_bytes`、`gc_used_bytes` 和 `gc_reserved_bytes` 输出
起点、终点、峰值、变化量、端点斜率、60 帧滚动窗口的稳健中位斜率和
阶梯增长候选。少于 30 秒一律为 `insufficient_duration`；达到时长后，
稳健斜率超过 32768 B/s 才标记 `growth_candidate`，否则为 `stable`。
`growth_candidate` 也只是复测提示，不是内存泄漏证明。

## 离线 HTML

`analysis.html` 不依赖 CDN 或网络资源。所有图、表和结论均由同一个
Analyzer 结果对象生成，不在 HTML 中维护第二套分析算法。页面包含：

- run ID、时长、帧数、构建/场景/环境、质量与追溯警告；
- 总体结论和实验阈值；
- 带 incident 区间标记的帧时间及 CPU/GPU 时间线；
- GC 窗口、分配尖峰和相应表格；
- Unity/GC 内存趋势和数值表；
- incident 排序表、证据、反证、限制、上下文帧和 CSV 行号；
- 原始四项产物的绝对路径。

## 修改文件

- `D:\PerDec\analyzer\__main__.py`
- `D:\PerDec\analyzer\core.py`
- `D:\PerDec\analyzer\diagnostics.py`（新增）
- `D:\PerDec\analyzer\report.py`（新增）
- `D:\PerDec\analyzer\selection.py`（新增）
- `D:\PerDec\runner\__main__.py`
- `D:\PerDec\runner\core.py`
- `D:\PerDec\tests\test_phase3.py`（新增）
- `D:\PerDec\.vscode\launch.json`
- `D:\PerDec\perfguardian.example.json`
- `D:\PerDec\README.md`
- `D:\PerDec\docs\experiment-protocol.md`
- `D:\PerDec\docs\phase-3-single-run-diagnostics-report.md`（本文件）

`artifacts` 原始数据未修改；`reports` 仅生成派生报告并由 `.gitignore`
排除。没有修改 Unity 工程或采集端代码。

## 测试证据

完整命令：

```powershell
C:\Users\一点\AppData\Local\Programs\Python\Python312\python.exe -B -m unittest discover -s D:\PerDec\tests -v
```

结果：`Ran 26 tests`，`OK`。覆盖原有 Analyzer/Runner 回归以及：

- 精确 run ID、latest 时间规则、缺失/重复/不合格错误；
- 不同 run 的报告输出隔离；
- 单帧、连续帧、短正常间隔合并和无事故；
- CPU、GPU、GC、帧率等待与 unattributed 分类；
- CPU/GPU 峰值出现在不同帧时不拼接为伪证据；
- 证据、反证与限制字段；
- GC 窗口重叠、分配尖峰；
- 5 秒短测不判断泄漏、长测只输出增长候选；
- 缺失 CPU/GPU 字段和合法尾部空值的保守降级；
- Analyzer 和 Runner 对 NaN/Infinity 的拒绝；
- HTML 从同一结果对象生成并包含关键模块；
- 原有全量扫描命令兼容；
- Runner 自动 Analyzer 成功/失败边界。

## 真实 run 验证

指定 run 存在，因此没有替换成其他数据：

```powershell
C:\Users\一点\AppData\Local\Programs\Python\Python312\python.exe -B -m analyzer `
  --artifacts-root artifacts --output-dir reports `
  --run-id c7bb51ff-b75c-491e-9cc8-4c7ee7b0ac62
```

运行成功，`analysis_eligible=true`。主要结果：

- 821 帧，测量覆盖 5000 ms；数据质量 `valid`。
- 帧时间 mean 6.0932 ms、median 6.0891 ms、P95 6.1135 ms、
  P99 6.1422 ms、max 9.1677 ms。
- 165.002 Hz、VSync 1 推导帧预算 6.0605 ms；本次自适应阈值
  8.0891 ms，预算阈值 9.0908 ms，最终 incident 阈值 9.0908 ms。
- 检出两个单帧 minor incident：frame 72 / CSV 74（9.1564 ms）与
  frame 811 / CSV 813（9.1677 ms）。二者均为
  `unattributed / low`：CPU 时间虽高于 GPU，但只覆盖约 63%–65% 的整帧，
  不足以保守认定 CPU 原因。
- CPU 819 个有效值、2 个缺失；GPU 818 个有效值、3 个缺失。GPU 尾部
  缺失形成 `unexpected_missing_values:gpu_frame_time_ms` 质量警告，但未使
  基础数据失效。
- 1 个 GC 工作窗口，总/最大 Marker 1.3886 ms，未与 incident 重叠。
- 分配中位数 690 bytes，未发现超过实验阈值的分配尖峰。
- 内存测量仅 5 秒，状态为 `insufficient_duration`；没有给出泄漏或持续
  增长结论。
- `commit_sha=abc123` 不阻止分析，只显示
  `placeholder_or_missing_commit_sha` 追溯警告。

输出：

- JSON：`D:\PerDec\reports\runs\c7bb51ff-b75c-491e-9cc8-4c7ee7b0ac62\analysis.json`
- HTML：`D:\PerDec\reports\runs\c7bb51ff-b75c-491e-9cc8-4c7ee7b0ac62\analysis.html`

该 5 秒短测只证明单运行闭环和诊断输出可用，不是正式性能结论。

## 当前风险与仍无法分析的数据

- 没有进程 CPU 使用率，不能回答 CPU 核心利用率、线程抢占或系统负载。
- 没有系统 GPU 利用率、显存占用和 GPU Marker 树，不能证明 GPU 单元或
  驱动层瓶颈。
- 没有调用栈、完整 PlayerLoop/Profiler Timeline，不能定位到具体方法。
- 当前 Marker 集不是 CPU 总时间的互斥分解，不能制造严格“剩余时间”。
- Collector 在同一帧执行并同步写 CSV；声明的 CPU、Marker 和分配值可能
  包含采集开销。
- 5 秒数据不足以判断内存泄漏或长期增长稳定性。
- `abc123` 是占位 commit，报告可诊断但构建追溯能力不足。

## 下一阶段建议

1. 先用 30–60 秒、场景可重复的单版本运行验证内存趋势和 incident
   稳定性，不立即引入版本比较。
2. 由 Unity 专项评估是否能安全补充更完整的主线程/渲染 Marker 或
   ProfilerRecorder 字段；任何协议扩展需按数据契约流程审批。
3. 用若干已知 CPU、GPU、GC 和帧率等待场景校准实验阈值，确认后再考虑
   固化严重程度或门禁标准。
4. 阶段三稳定后，再由总指挥决定是否进入多版本回归与 CI 阶段。

## 需要总指挥决定

- 是否接受当前实验阈值作为下一轮真实场景校准起点；它们尚不是正式 SLA。
- 是否安排 Unity 专项补充可定位到更深层原因的数据；当前 Python 端不能
  从已有 25 列推导调用栈或系统利用率。
- 是否批准后续使用更长的单场景测试验证内存趋势。
- 本次工作树尚未提交；是否另行授权创建阶段三提交或 PR。
