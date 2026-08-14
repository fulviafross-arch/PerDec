# PerfGuardian 阶段一实施总结与下一阶段交接报告

报告日期：2026-08-12  
项目根目录：`D:\PerDec`  
当前数据协议：`0.1.0-draft`  
负责范围：项目基建、Python Runner、Analyzer、QA 与真实 Unity 闭环验收

## 1. 给总指挥的结论摘要

阶段一已经建立可运行的最小闭环：通过本地 JSON 配置或 VS Code 启动 Python Runner；Runner 为每次测试生成新 UUID 和独立目录，以安全参数数组启动 Unity Standalone，传入采集参数和原生 `-logFile`；等待 Unity 自动预热、测量、写出四类原始产物并自动退出；随后检查进程结果和产物质量，生成机器可读 JSON 与离线 HTML 报告。Analyzer 只读取 Runner 判定为可分析的运行，并计算逐运行统计。

真实 Unity 短测已经证明以下链路可用：

`配置加载 → UUID 目录 → 自动启动 Effect3.exe → 1 秒预热 → 5 秒测量 → 四类产物 → 自动退出 → Runner 质检 → Analyzer 统计`

最新真实运行 `4291d5ae-891f-4a10-9d0b-2ace73fa2747`：Unity 自行退出、退出码 0、未超时、276 个帧样本、测量覆盖 5005 ms、四类产物均有效、Player 日志未发现失败特征，`eligible_for_analysis=true`。CPU 和 GPU 指标均为 276/276 有效；GC 为 0/276，在当前 Release 构建中属于已知不可用指标，报告为质量警告但暂不阻塞最小闭环。

本阶段结果只证明集成闭环和数据接口可运行。当前参数为 1 秒预热、5 秒测量、单次重复，不能作为正式性能基线，也不能用于判定性能回归。

## 2. 当前状态

阶段状态：**MVP 闭环完成，具备进入“实验治理、重复运行汇总和回归判定”阶段的条件。**

当前已具备：

- 唯一项目与 Git 根目录为 `D:\PerDec`。
- Python Runner、产物验证、Runner 报告与 Analyzer 已实现。
- Unity Standalone 真实自动运行已通过。
- `frame_time_ms`、`memory_used_bytes`、`cpu_frame_time_ms`、`gpu_frame_time_ms` 已在最新构建中获得有效数据。
- VS Code F5、`python -m runner` 和直接执行 `runner\__main__.py` 均可作为入口。
- 本机参数集中保存在被 Git 忽略的 `perfguardian.local.json`，无需每次输入长命令。
- 当前未执行 Git 提交、推送或远程仓库操作。

尚未达到正式性能平台标准的部分：

- 还没有批准的正式预热、测量、重复次数、噪声预算或回归阈值。
- Analyzer 当前是“逐运行统计列表”，尚未做跨重复聚合、置信区间、基线比较和回归结论。
- GC 在当前 Release 构建中不可用。
- 报告 HTML 是离线 JSON 展示型 MVP，尚无图表和交互界面。
- 测试覆盖了核心纯函数，但进程超时、终止升级和更多坏产物场景仍需补齐自动测试。

## 3. 目录和组件

```text
D:\PerDec\
├── runner\
│   ├── __main__.py              # CLI、配置加载、重复运行入口
│   └── core.py                  # 启动 Unity、超时控制、质检、Runner 报告
├── analyzer\
│   └── __main__.py              # 合格运行的逐运行统计
├── tests\
│   └── test_runner.py           # 当前单元测试
├── docs\
│   ├── data-contract.md         # 0.1.0-draft 数据契约说明
│   ├── experiment-protocol.md   # 短测和验收规则
│   └── phase-1-handoff-report.md# 本报告
├── .vscode\launch.json          # VS Code F5 启动项
├── perfguardian.example.json    # 可提交的配置模板
├── perfguardian.local.json      # 本机配置，Git 忽略
├── artifacts\                   # 原始运行产物，Git 忽略
└── reports\                     # Analyzer 输出，Git 忽略
```

## 4. 用户启动接口

### 4.1 推荐入口

在 VS Code 的“运行和调试”中选择 `PerfGuardian：运行测试` 后按 F5。对应配置位于 `.vscode\launch.json`，其实际启动模块为：

```powershell
python -m runner
```

也可以直接在 `D:\PerDec` 终端执行相同命令。直接点击 `runner\__main__.py` 的“运行 Python 文件”也已兼容。

### 4.2 本地配置接口

默认读取 `D:\PerDec\perfguardian.local.json`。本机文件已加入 `.gitignore`，以免提交用户名、EXE 路径或其他机器信息。可共享模板是 `perfguardian.example.json`。

| JSON 字段 | 类型 | 必需/默认 | 含义 |
|---|---|---|---|
| `exe` | 路径字符串 | 必需 | Unity Standalone EXE |
| `project_id` | 字符串 | 必需 | 被测项目稳定标识 |
| `experiment_id` | 字符串 | 必需 | 实验定义稳定标识 |
| `experiment_version` | 字符串 | 必需 | 实验定义版本 |
| `scenario_id` | 字符串 | 必需 | 场景稳定标识 |
| `scenario_version` | 字符串 | 必需 | 场景版本 |
| `commit_sha` | 字符串 | 必需 | 被测构建提交；正式实验不得继续使用 `abc123` |
| `branch` | 字符串 | 必需 | 被测构建分支 |
| `warmup_seconds` | 数字 | 默认 1 | 预热秒数 |
| `measurement_seconds` | 数字 | 默认 5 | 正式测量秒数 |
| `sample_interval_ms` | 整数 | 默认 16 | 采样间隔毫秒 |
| `repetitions` | 整数 | 默认 1 | 重复次数，从 1 顺序执行到 N |
| `artifacts_root` | 路径字符串 | 默认项目下 `artifacts` | 原始产物根目录 |

命令行同名参数可以临时覆盖配置文件，不会修改配置文件。例如：

```powershell
python -m runner --measurement-seconds 60 --repetitions 3
```

Runner 会拒绝缺少必需字段、非法 JSON、未知配置字段、不存在的 EXE 和小于 1 的重复次数。

## 5. Runner 运行流程

1. `runner.__main__` 解析 `--config`，默认读取 `perfguardian.local.json`。
2. 合并命令行覆盖值，构造 `RunConfig`。
3. 对每个 repetition 调用一次 `run_once`；不会只保留最好的一次。
4. 使用 UUID v4 生成 `run_id`。
5. 在 `artifacts\<project_id>\<experiment_id>\<run_id>\` 创建全新目录，`exist_ok=False` 防止覆盖。
6. 以参数数组组装 Unity 命令；不拼接未转义命令字符串，支持中文和空格路径。
7. 启动 EXE，不默认传 `-batchmode` 或 `-nographics`。
8. 等待 Unity 自行完成。总超时为 `30 秒启动宽限 + warmup + measurement + 30 秒退出宽限`。
9. 若超时，先调用正常终止并等待 5 秒，仍不退出则强制结束；保留全部已有产物。
10. 读取退出码并验证 Unity 四类产物。
11. 生成 `runner-report.json` 与 `runner-report.html`。
12. 所有重复均可分析时 CLI 返回 0；任一重复不合格时返回 1。

Runner 不写回或修改 Unity 的 `run.json`。`timeout` 与 `invalid_artifacts` 只存在于 Runner 自己的报告中。

## 6. Runner → Unity 命令行接口

每次启动固定传递以下参数：

| 参数 | 来源/语义 |
|---|---|
| `-logFile <run_dir>\player.log` | Unity 原生日志，必须与本次目录一致 |
| `--pg-output <run_dir>` | 四类 Unity 产物输出目录 |
| `--pg-run-id <uuid4>` | Runner 生成的全局唯一运行 ID |
| `--pg-project-id` | 配置中的项目 ID |
| `--pg-experiment-id` | 配置中的实验 ID |
| `--pg-experiment-version` | 实验版本 |
| `--pg-scenario-id` | 场景 ID |
| `--pg-scenario-version` | 场景版本 |
| `--pg-commit-sha` | 被测构建提交 |
| `--pg-branch` | 被测构建分支 |
| `--pg-warmup-seconds` | 预热时长，秒 |
| `--pg-measurement-seconds` | 测量时长，秒 |
| `--pg-sample-interval-ms` | 采样间隔，毫秒 |
| `--pg-repetition-index` | 当前重复序号，从 1 开始 |
| `--pg-repetition-count` | 总重复次数 |
| `--pg-quit-on-complete true` | 要求 Unity 测量完成后自动退出 |

`-logFile` 与 `--pg-output` 指向同一个预先创建的 UUID 目录。这一点是 Runner/Unity 接口的硬约束。

## 7. Unity → Runner 四类原始产物接口

### 7.1 `run.json`

作用：单次运行清单、构建与环境信息、场景、实验协议、采集器来源和最终状态。

最新真实产物包含以下主要结构：

```text
schema_version
run_id
project_id
experiment_id / experiment_version
started_at / completed_at
status
build.{commit_sha, branch, unity_version, target_platform, build_type}
environment.{host_id, os_name, os_version, cpu_model, gpu_model,
             memory_total_bytes, graphics_driver_version,
             display_width_pixels, display_height_pixels,
             display_refresh_rate_hz}
scenario.{scenario_id, scenario_version, active_scene, parameters}
protocol.{warmup_seconds, measurement_seconds, sample_interval_ms,
          repetition_index, repetition_count}
collector.{collector_id, collector_version, execution_mode,
           各指标 source, failure_reason, quality_flags}
```

时间戳使用 UTC RFC 3339；容量使用字节。最新运行的 `quality_flags` 包含 `graphics_driver_version_unavailable`，因为显卡驱动版本为 `null`。

### 7.2 `frames.csv`

每行是一条帧级采样，最新 CSV 表头为：

```csv
schema_version,run_id,timestamp_ms,frame_time_ms,cpu_frame_time_ms,gpu_frame_time_ms,memory_used_bytes,gc_allocated_bytes
```

字段语义：

| 字段 | 单位 | 当前数据来源/规则 |
|---|---:|---|
| `schema_version` | 无 | 当前 `0.1.0-draft` |
| `run_id` | 无 | 必须与 Runner UUID 一致 |
| `timestamp_ms` | ms | 正式测量开始后的相对时间；非负、单调不减 |
| `frame_time_ms` | ms | `UnityEngine.Time.unscaledDeltaTime` |
| `cpu_frame_time_ms` | ms | `UnityEngine.FrameTimingManager` |
| `gpu_frame_time_ms` | ms | `UnityEngine.FrameTimingManager` |
| `memory_used_bytes` | bytes | `Profiler.GetTotalAllocatedMemoryLong` |
| `gc_allocated_bytes` | bytes | `ProfilerRecorder: GC Allocated In Frame`；Release 当前不可用 |

数值字段只允许有限数字或协议允许的空/`null`。不允许 `NaN`、`Infinity` 或任意文本。

### 7.3 `events.jsonl`

每个非空行是一个 JSON 对象。正常最小事件顺序为：

```text
run_started → measurement_started → run_completed
```

事件至少包括 `schema_version`、`run_id`、`timestamp_ms`、`recorded_at`、`event_type` 和 `message`。当前正常完成验收要求最后一个事件的 `event_type`（兼容旧字段 `type`）为 `run_completed`。

### 7.4 `player.log`

由 Unity 原生 `-logFile` 写入，覆盖引擎启动阶段和采集器运行期日志。Runner 当前按不区分大小写搜索：`unhandled exception`、`exception`、`crash`、`could not start`、`collector startup failed`、`outofmemory`。发现这些特征会使运行不可分析。

## 8. Runner 验收规则和报告接口

单次运行只有同时满足以下条件，才会得到 `runner_status=completed` 和 `eligible_for_analysis=true`：

- 未超时，进程自行退出，退出码为 0。
- `run.json`、`frames.csv`、`events.jsonl`、`player.log` 均存在且非空。
- `run.json` 为合法 JSON，`schema_version=0.1.0-draft`，`run_id` 一致，`status=completed`，失败原因为空。
- `events.jsonl` 每行均可解析，最后事件为 `run_completed`。
- `frames.csv` 至少有一行，所有 `run_id` 一致。
- `timestamp_ms` 非负且单调不减。
- 指标为有限数字或允许的空/`null`。
- 最后帧时间戳至少达到配置测量时长的 80%。这是当前 MVP 容差，尚未经过正式实验批准。
- `player.log` 不包含当前失败特征。

Runner 内部结论：

| `runner_status` | 含义 | 可分析 |
|---|---|---|
| `completed` | 进程和产物全部通过 | 是 |
| `timeout` | 超过 Runner 总超时 | 否 |
| `invalid_artifacts` | 非零退出或任一产物规则不通过 | 否 |

每次运行额外生成：

- `runner-report.json`：供后续机器处理。
- `runner-report.html`：当前为离线 `<pre>` JSON 展示。

核心报告字段：

```text
runner_status
run_id
exe
command[]
exit_code
started_at / completed_at / duration_seconds
timed_out
configuration{}
artifact_directory
eligible_for_analysis
eligibility_reasons[]
quality_warnings[]
sample_count
measurement_coverage_ms
metrics.<metric>.{valid, missing, missing_ratio}
log_errors[]
```

某指标全部为空时会产生 `all_values_missing:<metric>` 警告，但 MVP 当前不会仅因此拒绝整次运行。最新运行只有 `all_values_missing:gc_allocated_bytes`。

## 9. Analyzer 接口和当前能力

启动命令：

```powershell
python -m analyzer --artifacts-root artifacts --output-dir reports
```

Analyzer 递归扫描 `runner-report.json`，只读取 `eligible_for_analysis=true` 的运行。对每个运行、每个指标分别输出：

- `count`
- `missing_ratio`
- `mean`
- `median`
- `p90`
- `p95`
- `p99`
- `min`
- `max`
- `measurement_coverage_ms`

全空指标只输出 `count=0`、`missing_ratio=1.0` 和说明，不生成伪统计。输出文件为 `reports\analysis.json` 与 `reports\analysis.html`。

重要边界：当前 `eligible_run_count` 是扫描到的全部历史合格运行数；Analyzer 尚未按 commit、实验版本、场景版本或采集能力分组，也没有生成跨重复综合统计。因此不能把当前 `analysis.json` 中不同历史运行简单合并为同一基线。

## 10. 最新真实验收证据

### 10.1 运行身份与环境

| 项目 | 值 |
|---|---|
| Run ID | `4291d5ae-891f-4a10-9d0b-2ace73fa2747` |
| EXE | 本机 Unity Standalone 构建路径（由 `perfguardian.local.json` 配置且不提交） |
| Unity | `2022.3.47f1c1` |
| 构建类型 | Release / Windows Standalone |
| CPU | Intel Core i7-14700HX |
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU |
| 内存总量 | 16,943,939,584 bytes（约 15.78 GiB） |
| 分辨率 / 刷新率 | 2560×1600 / 约 165 Hz |
| 场景 | `Scene01` v001，活动场景 `Main` |
| 配置 | 预热 1 s、测量 5 s、间隔 16 ms、重复 1 |

`commit_sha=abc123` 仍是占位值，所以这次运行不能与真实构建提交建立可追溯关系。

### 10.2 Runner 验收结果

| 项目 | 结果 |
|---|---|
| Runner 状态 | `completed` |
| 进程退出 | 自行退出，退出码 0 |
| 超时 | 否 |
| Runner 总耗时 | 约 10.547 s |
| Unity 清单状态 | `completed` |
| 事件末项 | `run_completed` |
| 样本数 | 276 |
| 测量覆盖 | 5005 ms，即配置 5 s 的约 100.1% |
| 日志失败特征 | 无 |
| 可进入 Analyzer | 是 |

### 10.3 最新运行指标质量

| 指标 | 有效/总数 | 缺失率 | 质量结论 |
|---|---:|---:|---|
| `frame_time_ms` | 276/276 | 0% | 有效 |
| `memory_used_bytes` | 276/276 | 0% | 有效 |
| `cpu_frame_time_ms` | 276/276 | 0% | 有效 |
| `gpu_frame_time_ms` | 276/276 | 0% | 有效 |
| `gc_allocated_bytes` | 0/276 | 100% | Release 已知不可用；不生成统计 |

### 10.4 最新运行逐指标统计

| 指标 | Mean | Median | P90 | P95 | P99 | Min | Max |
|---|---:|---:|---:|---:|---:|---:|---:|
| 帧时间 (ms) | 6.0607 | 6.0614 | 6.0665 | 6.0669 | 6.0683 | 6.0545 | 6.0732 |
| CPU 帧时间 (ms) | 6.0828 | 6.0806 | 6.3647 | 6.5057 | 6.7924 | 5.2050 | 6.8699 |
| GPU 帧时间 (ms) | 5.3160 | 5.8732 | 6.4026 | 6.5894 | 7.3318 | 2.5938 | 7.9340 |
| 内存 (bytes) | 131,934,591.70 | 131,950,403 | 131,971,912 | 131,972,372 | 131,974,068 | 131,828,970 | 131,975,348 |

内存平均值约为 125.82 MiB。以上数据仅用于验证统计链路；由于测试只有 5 秒且重复一次，不形成正式性能结论。

### 10.5 历史运行说明

当前 Analyzer 扫描到 4 个 `eligible_for_analysis=true` 的历史运行。其中较早运行存在 CPU/GPU 全空，后续构建已有 CPU/GPU 数据。历史运行的采集能力不同，不应直接合并比较。`Test001` 是用户手工中止的 `cancelled` 样例，不是自动退出成功证据，也不是性能基线。

## 11. 自动测试证据

最近一次执行：

```powershell
python -m unittest discover -s tests -v
```

结果：6 项测试全部通过。现有覆盖包括：

- 中文和空格路径保持为独立参数。
- UUID 运行目录不重复、不覆盖。
- 合法 completed 产物可分析。
- cancelled 状态拒绝。
- 时间戳倒退拒绝。
- `NaN`/`Infinity` 指标拒绝。
- Player 日志异常特征拒绝。
- 缺失产物拒绝。
- JSON 配置加载和命令行覆盖。

仍需补齐的自动测试：

- 使用 mock/伪进程完整覆盖正常退出、非零退出、超时、terminate 后退出、kill 升级。
- 分别覆盖空文件、坏 `run.json`、坏 JSONL、事件末项错误。
- 覆盖 manifest/frame `run_id` 不一致。
- 覆盖负时间戳、非法文本数字、非法 null 表达。
- 覆盖测量覆盖不足的边界。
- Analyzer 百分位、全空指标、筛除不合格运行和多运行分组测试。

因此当前测试证据足以支持 MVP 演示和继续开发，但尚未达到正式 CI 质量门槛。

## 12. 已知风险和接口问题

### 12.1 `failure_reason` 位置需要统一

文字契约当前描述顶层 `run.json.failure_reason`；最新 Unity 实际产物把该字段放在 `collector.failure_reason`。Runner 当前使用 `manifest.get("failure_reason")`，字段缺失会返回 `None`，因此会通过检查，但实际上没有严格验证 Unity 当前的 `collector.failure_reason`。

建议下一阶段由总指挥明确唯一位置。推荐以实际结构 `collector.failure_reason` 为准，并安排兼容期：Runner 同时接受旧顶层字段与新嵌套字段，但若两者同时存在且不一致则拒绝；协议文档、Unity 生产者、Runner 消费者和契约测试同步更新。

### 12.2 GC Release 限制

`gc_allocated_bytes` 在当前 Release Standalone 中全为 `null`。已经决定暂不处理，Analyzer 不生成伪统计，Runner 只发质量警告。后续如果要将 GC 纳入硬验收，需要 Unity 专项提供 Release 可用采集来源或明确该字段在特定构建类型下永久可选。

### 12.3 显卡驱动版本缺失

最新 `run.json.environment.graphics_driver_version=null`，并带有 `graphics_driver_version_unavailable`。正式跨机器基线可能需要该信息；当前不阻塞闭环。

### 12.4 日志判定较粗

当前通过关键词扫描 Player 日志，`exception` 等广泛关键词可能对已处理异常或普通文本产生误报。后续应引入分级规则、上下文摘要和允许列表，并保留原始日志引用。

### 12.5 覆盖率规则尚属 MVP

当前以最后一条 `timestamp_ms >= measurement_seconds × 1000 × 80%` 判定覆盖足够。它没有检查首样本位置、采样大段缺口、期望样本数或最大相邻间隔。正式实验前应由总指挥批准更严格规则。

### 12.6 配置追溯不足

本地配置仍使用 `commit_sha=abc123`，实验/场景版本均为 `001`。正式运行前必须由构建流程注入真实提交和稳定版本，防止结果无法复现。

### 12.7 Analyzer 不做跨重复结论

当前只计算每次运行的描述统计，尚未根据 repetition 形成实验级汇总；也未防止不同 commit、硬件、分辨率或采集器版本混入同一报告。下一阶段必须先定义分组键，再做基线和回归。

## 13. 下一阶段建议

建议按以下顺序推进：

1. **冻结 `0.1.0-draft` 的实际结构。** 统一 `failure_reason` 路径，补齐必需/可选字段、数据类型、单位和兼容规则，并加入契约测试。
2. **定义实验身份与分组键。** 至少包含 project、experiment/version、scenario/version、commit、平台、CPU/GPU、分辨率、构建类型、采集器版本和协议版本。
3. **批准正式实验参数。** 明确预热、测量时长、重复次数、前后台状态、帧率/VSync、分辨率、质量档、温度/电源策略和噪声预算。
4. **强化 Runner 测试和报告。** 完成超时/进程/坏产物覆盖，报告中增加进程自行退出证据、覆盖率百分比、相邻采样缺口和错误上下文。
5. **实现实验级 Analyzer。** 按分组键聚合 repetition，输出中心趋势、离散度、置信区间、离群点说明、基线对比和回归候选；没有批准阈值前只展示差异，不自动判失败。
6. **增加人类可读报告。** 用本地 HTML 图表展示帧时间序列、分位数、CPU/GPU、内存趋势、缺失率和重复间差异。
7. **建立正式 QA 门槛。** 增加格式检查、静态检查、单元测试、契约测试；真实入口稳定后再配置 CI，不使用空测试制造绿色状态。

## 14. 需要总指挥决策

1. 是否以 `collector.failure_reason` 作为协议正式位置，以及兼容旧顶层字段多久。
2. GC 在 Release 不可用时是否长期作为可选指标，仅产生警告。
3. `graphics_driver_version` 是否属于正式基线的硬性环境字段。
4. 正式实验的预热、测量、重复次数、覆盖率和采样缺口阈值。
5. 实验级聚合的唯一分组键和允许比较的环境差异。
6. Analyzer 下一阶段是先做“重复聚合与基线比较”，还是先做“HTML 可视化”。推荐先完成聚合与可比性规则。
7. 是否批准阶段一代码进入首个可回滚提交；当前工作树尚未提交。

## 15. 固定专项汇报

### 当前状态

Python Runner/Analyzer MVP 与真实 Unity 自动闭环已完成；CPU/GPU 最新采集有效；GC Release 限制已知。

### 已完成

项目基建、本地配置、VS Code 启动、UUID 隔离、Unity 参数传递、超时机制、四产物质检、Runner JSON/HTML、逐运行 Analyzer、6 项单元测试、真实 5 秒短测。

### 修改文件

所有项目修改均位于 `D:\PerDec`：`README.md`、`.gitignore`、`.vscode\launch.json`、`pyproject.toml`、`perfguardian.example.json`、`runner\`、`analyzer\`、`tests\`、`docs\`。本机配置 `perfguardian.local.json` 被 Git 忽略；`artifacts\` 和 `reports\` 也被忽略。

### 验证证据

6/6 单元测试通过。最新真实运行自动退出码 0，276 样本，覆盖 5005 ms，四产物有效，CPU/GPU 0% 缺失，Player 日志无失败特征，Analyzer 已输出统计。

### 风险/阻塞

协议中 `failure_reason` 位置不一致；GC Release 不可用；显卡驱动版本缺失；短测不构成正式基线；Analyzer 未做跨重复聚合；超时和坏产物测试尚不完整。

### 下一步

先冻结真实数据契约和实验可比性规则，再实现重复聚合、基线比较、完整测试和可视化报告。

### 需要总指挥决策

确认失败原因字段位置、GC 和驱动版本策略、正式实验参数、分组键、Analyzer 优先级及是否提交阶段一代码。
