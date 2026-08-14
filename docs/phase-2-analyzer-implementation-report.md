# PerfGuardian 阶段二 Analyzer 优化实施报告

报告日期：2026-08-14  
项目根目录：`D:\PerDec`  
公共数据协议：`0.1.0-draft`（新增 16 列仍待总指挥批准）  
Analyzer 实验输出版本：`0.2.0-experimental`

## 1. 当前状态

已根据 Unity Collector `0.3.1` 交接完成 Analyzer 第一轮 P0/P1 消费能力，并用阶段二指定的三个真实样例及一份新运行验证。公共 `docs/data-contract.md` 未擅自扩展；新增分析结构暂时只属于 Analyzer 的版本化实验输出。

最新真实运行：`c7bb51ff-b75c-491e-9cc8-4c7ee7b0ac62`。Unity Development Standalone 自行退出、退出码 0、未超时，821 帧覆盖 5000 ms，Runner 判定可分析。

## 2. 已完成

### 2.1 兼容解析和质量语义

- 按 CSV 列名解析，不依赖固定列数；兼容旧 8/9 列与新 25 列产物。
- 区分 `field_absent`、`declared_unavailable`、`declared_unsupported`、`all_null_undeclared`、`available`。
- 合法数值 0 单独计入 `zero_count`，不与 `null` 混淆。
- 每指标输出 `valid_count`、`missing_count`、`missing_ratio` 和统计量。
- 拒绝 NaN、Infinity、非整数的整数字段。
- 验证 `frame_index` 从 0 连续无缺口，验证时间戳非负且单调。
- 读取 `collector.quality_flags`、指标来源、Collector 版本和对齐元数据。
- 允许声明过的最后一帧 Recorder/GC 数据未完成；CPU/GPU 尾部最多 4 帧缺失按 FrameTiming 延迟处理。
- 零星中间缺失保留质量警告；超过 5% 或基础指标未声明全空时输出 `inconclusive`。

### 2.2 GC 事件模型

- 将连续 `gc_collect_ms > 0` 合并为一个工作窗口。
- 接受窗口内或结束后一帧的 `gc_gen*_collections` 完成计数。
- 输出窗口起止帧、完成帧、持续帧数、Marker 总耗时、最大单帧耗时、各代完成次数。
- 输出 GC 窗口前后 `gc_used_bytes` 及变化量。
- `gc_max_generation=0` 时 Gen1/Gen2 标记为 `declared_unsupported`，不生成失败警告。

### 2.3 异常帧证据

- 当前以单运行 P99 帧时间作为候选阈值。
- 每个候选包含 `frame_index`、CSV 原始行号、时间戳、帧/CPU/GPU 时间、GC 状态、GC 分配与内存、九个 Marker 值。
- 输出保守归因类别：`gc_participating`、`frame_pacing_wait`、`cpu_or_main_thread_side`、`gpu_or_render_side`、`unattributed`。
- 不将 Marker 求和作为 CPU 总时间，不制造严格残差。

### 2.4 内存和可比性

- 输出 Unity/GC 内存起点、终点、峰值、变化量和端点斜率。
- 输出包含构建类型、Unity/Collector 版本、场景、硬件、分辨率、刷新率、VSync、预热与测量时长的 `comparison_key`。
- 扫描到 Release 与 Development 混合时输出 `mixed_build_types_not_directly_comparable`。
- 当前不执行 Release/Development 的绝对值比较。

### 2.5 Runner 契约修正

Runner 现在兼容失败原因位于旧顶层 `failure_reason` 或实际 `collector.failure_reason`。两处同时存在但内容冲突时拒绝运行；两处都缺失时也拒绝。Runner 仍不会修改 Unity 的 `run.json`。

## 3. 最新真实运行结果

### 3.1 运行与质量

| 项目 | 结果 |
|---|---|
| Run ID | `c7bb51ff-b75c-491e-9cc8-4c7ee7b0ac62` |
| 构建类型 | Development |
| Collector | `perfguardian.unity.runtime 0.3.1` |
| 帧数 | 821 |
| 测量覆盖 | 5000 ms |
| Runner 退出码 | 0 |
| Runner 可分析 | 是 |
| Analyzer 质量状态 | `valid` |
| Analyzer 警告 | `unexpected_missing_values:gpu_frame_time_ms` |

GPU 警告来自第 534 帧单个中间 `null`；另两处为尾部第 819/820 帧。总缺失率约 0.365%，不会否定本次运行，但保留为可见质量信息。CPU 仅尾部 2 帧缺失。

### 3.2 主要指标

| 指标 | 有效/总数 | 缺失率 | Mean | P95 | P99 | Max |
|---|---:|---:|---:|---:|---:|---:|
| `frame_time_ms` | 821/821 | 0% | 6.0932 ms | 6.1135 ms | 6.1422 ms | 9.1677 ms |
| `cpu_frame_time_ms` | 819/821 | 0.244% | 6.0920 ms | 6.4152 ms | 6.5057 ms | 6.7071 ms |
| `gpu_frame_time_ms` | 818/821 | 0.365% | 3.8388 ms | 4.7194 ms | 5.1709 ms | 5.6719 ms |
| `gc_allocated_bytes` | 820/821 | 0.122% | 693.72 B | 712 B | 712 B | 826 B |
| `gc_used_bytes` | 820/821 | 0.122% | 11,537,992 B | 11,923,456 B | 11,948,032 B | 11,956,224 B |
| `wait_for_target_fps_ms` | 820/821 | 0.122% | 2.1629 ms | 2.6302 ms | 2.7767 ms | 3.0526 ms |

`gc_allocated_bytes` 带有 `gc_allocated_in_frame_includes_collector_overhead`，只能用于同 Collector 版本的趋势和尖峰分析，不能解释为纯业务分配。

### 3.3 GC 窗口

Analyzer 正确识别一个增量 GC 工作窗口：

| 项目 | 值 |
|---|---:|
| 起始帧 | 757 |
| 结束帧 | 758 |
| 完成帧 | 758 |
| 持续帧数 | 2 |
| Marker 总耗时 | 1.3886 ms |
| 最大单帧 Marker | 0.9986 ms |
| Gen0 完成次数 | 1 |
| GC used before | 11,956,224 B |
| GC used after | 10,465,280 B |
| GC used delta | -1,490,944 B |

第 757/758 帧只报告为一个 GC 工作窗口，而不是两次完整 GC。

### 3.4 内存趋势

| 指标 | 起点 | 终点 | 峰值 | 变化量 |
|---|---:|---:|---:|---:|
| Unity used | 174,151,500 B | 174,247,982 B | 174,259,471 B | +96,482 B |
| GC used | 11,292,672 B | 10,465,280 B | 11,956,224 B | -827,392 B |
| GC reserved | 11,370,496 B | 15,163,392 B | 15,163,392 B | +3,792,896 B |
| Unity reserved | 318,656,512 B | 318,656,512 B | 318,656,512 B | 0 B |

本次只有 5 秒，端点斜率仅是实验性特征，不能据此判断泄漏。

## 4. 验证证据

执行：`python -B -m unittest discover -s D:\PerDec\tests -v`。

结果：13 项测试全部通过，其中包括阶段二三个真实样例：

- Release `2e671632-f3d2-493a-abb0-7758041b5213`：诊断字段不可用不否定基础分析。
- Development 0.3.0 `702a60cc-4c01-4334-bf6c-6cb65f8f8d2a`：第 377 帧识别为 CPU/主线程侧且不归因给 GC。
- Development 0.3.1 `5d3cd807-5c58-4804-b77f-f49faaa4554c`：757–758 帧合并为一个 1.3679 ms GC 窗口，合法尾部缺失通过。

同时覆盖旧列结构、absent/null/zero 区分、合成 GC 窗口和 Runner 嵌套失败原因兼容。

## 5. 修改文件

- `D:\PerDec\analyzer\core.py`
- `D:\PerDec\analyzer\__main__.py`
- `D:\PerDec\runner\core.py`
- `D:\PerDec\tests\test_analyzer.py`
- `D:\PerDec\tests\test_runner.py`
- `D:\PerDec\docs\phase-2-analyzer-implementation-report.md`
- 重新生成：`D:\PerDec\reports\analysis.json`、`analysis.html`
- 新真实产物：`D:\PerDec\artifacts\MyGame\PerfTest\c7bb51ff-b75c-491e-9cc8-4c7ee7b0ac62`

## 6. 风险/阻塞

1. 新 16 列尚未获准进入公共协议，因此 Analyzer 输出标记为 experimental。
2. 当前 P99 是单运行候选阈值，不是批准的回归阈值。
3. 归因属于确定性启发式证据分类，不等于完整调用栈结论。
4. GPU 存在极少量非尾部空值，当前保留警告；尚无 Collector 质量标记解释该情况。
5. GC 分配和部分 Marker 含 Collector 同步写入开销。
6. 当前扫描包含不同 Collector 版本及 Release/Development，禁止直接合并比较。
7. `commit_sha=abc123` 仍为占位值，不满足正式可追溯实验要求。
8. Analyzer HTML 仍是转义后的 JSON 展示，尚未形成图表仪表盘。

## 7. 下一步

1. 由总指挥批准或拒绝新增 16 列进入 `0.1.0-draft`，批准后同步更新公共数据契约和 Runner 契约测试。
2. 将扫描结果按完整 `comparison_key` 分组，实现同构重复运行的实验级聚合。
3. 定义正式异常阈值、基线比较、噪声预算和最少重复次数；批准前不自动判回归。
4. 为异常证据补充邻近帧窗口和 Marker 峰值排序，增强“未归因主线程时间”表达。
5. 生成图表化 HTML：帧时间序列、CPU/GPU、GC 窗口、内存趋势和质量状态。
6. 正式构建流程注入真实 commit、构建类型和实验版本。

## 8. 需要总指挥决策

1. 是否批准 25 列结构进入公共协议。
2. 是否批准 Release 正式比较、Development 诊断复跑的双模式流程。
3. 是否批准连续 GC Marker 帧合并为单个窗口的规则。
4. `0.2.0-experimental` Analyzer 对象应作为内部格式，还是进入新的版本化输出协议。
5. GPU 极少量中间空值是只保留质量警告，还是需要 Unity Collector 增加明确质量标记。
6. 下一步优先实现实验级聚合，还是图表化 HTML。建议先聚合与可比性门槛，再做可视化。

当前没有执行 Git 提交、推送或远程仓库操作。
