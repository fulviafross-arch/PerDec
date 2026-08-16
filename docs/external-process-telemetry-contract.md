# External Process Telemetry Contract (Phase 3.2A PoC)

Status: `0.1.0-poc`, additive and optional. Producer: Python Runner External
Monitor. Consumer: Python Analyzer; intended migration target: Phase 4 C++
Runtime Core. File: `<run_directory>/process.csv`, UTF-8, one header row.
This contract is the Phase 4 C++ compatibility baseline, and Phase 4 output must
remain compatible with the current Python Analyzer. Additive fields are allowed;
breaking changes should be avoided unless necessary.

| Field | Type / unit | Null | Sampling semantics |
|---|---|---:|---|
| `timestamp_ms` | finite number, ms | no | Unity measurement-relative time after Runner alignment; startup/warm-up samples may be negative |
| `process_cpu_percent` | finite float, % | yes | Target process CPU over the preceding interval, normalized to total logical-CPU capacity; first sample is null |
| `working_set_bytes` | integer, bytes | yes | OS resident working set at sample time |
| `private_bytes` | integer, bytes | yes | OS private committed bytes at sample time |
| `thread_count` | integer, count | yes | Target PID thread count; Win32 snapshot refreshed about 1 Hz and carried forward between refreshes |
| `io_read_bytes` / `io_write_bytes` | integer, bytes | yes | Cumulative target-process transfer counters |
| `io_read_rate_bytes_per_sec` / `io_write_rate_bytes_per_sec` | finite float, B/s | yes | Counter delta divided by monotonic elapsed time; first sample is null |
| `process_alive` | boolean | yes | PID exit status at sample time |
| `is_foreground` | boolean | yes | A visible top-level window owned by the PID is the foreground window |
| `is_minimized` | boolean | yes | Any visible top-level target window is iconic/minimized |
| `page_fault_count` | integer, count | yes | Optional cumulative Win32 process page-fault counter |
| `page_fault_rate_per_sec` | finite float, faults/s | yes | Optional counter delta rate; first sample is null |

## Timestamp alignment

Samples are first timestamped from the monotonic Runner clock immediately before
`Popen`. After the process stops, Runner reads the Unity
`measurement_started.recorded_at` event and subtracts the wall-clock offset from
every sample. The resulting `timestamp_ms=0` is the Unity measurement start used
by `frames.csv`; negative samples preserve startup/warm-up context. Runner records
the strategy, offset and status under
`runner-report.json.external_process_monitor.timestamp_alignment`.

The sampling intervals themselves remain monotonic. Alignment error can come from
wall-clock adjustment between anchors, event serialization latency, Python thread
scheduling and the roughly 100 ms sampling cadence. Analyzer therefore queries an
incident window (default ±500 ms) and never treats a sample as an exact frame fact.
If the measurement event or file is unavailable, alignment is marked unavailable.

## Compatibility and acceptance

Rationale: Unity-internal timings cannot provide Windows process/window context.
The producer adds only an optional Runner artifact; Unity Collector and its four
required artifacts are unchanged. The Analyzer treats missing, malformed or
partially unavailable process telemetry as non-fatal and does not change existing
incident classification. Existing artifacts remain supported indefinitely during
this PoC. Acceptance tests cover lifecycle/flush, null degradation, rate math,
alignment, incident-window selection, window state and legacy runs without the
file. Phase 4 may replace the producer with C++ while retaining this CSV semantics
for a compatibility period.
