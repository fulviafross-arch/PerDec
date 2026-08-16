# Phase 3.2A Windows External Evidence PoC report

## A. Implementation result

Runner now binds a standalone `ExternalProcessMonitor` to the exact PID returned
by `Popen`, samples at a configurable 100 ms cadence, and stops/flushes it in the
process lifecycle cleanup path. It writes
`artifacts/<project>/<experiment>/<run_id>/process.csv`. CPU, working/private
memory, cumulative and rate IO, thread count, process-alive, foreground,
minimized, and optional page-fault values are independently nullable; monitor
failure never changes Unity eligibility or `run.json`.

Analyzer optionally parses the file, aligns it to the Unity measurement timeline,
and attaches ±500 ms `process_evidence` only to `severe_hitch` and `hitch` events.
It emits summary availability/coverage in JSON and a compact Process Evidence
block in offline HTML. Evidence flags remain context, not root-cause classifiers;
existing CPU/GPU/GC/pacing/unattributed classification is unchanged.

## B. Modified files

- `D:\PerDec\runner\external_monitor\__init__.py`
- `D:\PerDec\runner\external_monitor\monitor.py`
- `D:\PerDec\runner\external_monitor\windows_process.py`
- `D:\PerDec\runner\core.py`
- `D:\PerDec\runner\__main__.py`
- `D:\PerDec\analyzer\external_process.py`
- `D:\PerDec\analyzer\core.py`
- `D:\PerDec\analyzer\report_v31.py`
- `D:\PerDec\tests\test_external_process.py`
- `D:\PerDec\perfguardian.example.json`
- `D:\PerDec\README.md`
- `D:\PerDec\docs\data-contract.md`
- `D:\PerDec\docs\external-process-telemetry-contract.md`
- `D:\PerDec\docs\phase-3-2a-external-evidence-poc-report.md`

## C. Telemetry contract

The complete `0.1.0-poc` contract is in
`docs/external-process-telemetry-contract.md`. Required timestamp semantics are
measurement-relative milliseconds; every telemetry value other than the timestamp
may be empty. Process CPU is total logical-capacity normalized and is distinct
from Unity `cpu_frame_time_ms`. Rates use monotonic counter delta / elapsed time;
the first rate sample is empty. Thread count is refreshed around 1 Hz and carried
between refreshes to avoid expensive system-wide thread snapshots at 10 Hz.

## D. Timestamp alignment

`frames.csv.timestamp_ms=0` starts at Unity `measurement_started`. Raw process
samples start on the Runner monotonic clock immediately before `Popen`; Runner
uses `measurement_started.recorded_at - runner_started_at` as a one-time offset
and rewrites process timestamps. Startup samples remain negative. The final real
run reported `aligned`, offset `15426.130 ms`, and covered `59938 ms` of the
`60003 ms` Unity measurement. Expected uncertainty comes from the 100 ms cadence,
event serialization/scheduling, and possible wall-clock adjustment; evidence is
therefore queried as a ±500 ms window rather than per-frame matching.

## E. Tests

Baseline before changes: 36 passed, 0 failed. Added: 8 tests. Current total:
44 passed, 0 failed.

```powershell
C:\Users\一点\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -m unittest discover -s D:\PerDec\tests -v
```

Coverage includes monitor thread shutdown/flush/header, rate/null semantics,
alignment, available/missing/partial CSV, ±500 ms selection, CPU/IO/window flags,
HTML, legacy artifacts and preservation of `unattributed`.

## F. Real run

- Run ID: `227a72e4-0f64-43b9-8991-eda5a24c8b1e`
- Unity measurement: `60003 ms`, `9542` frames, eligible, exit code 0
- Process telemetry: `756` samples total; `600` in measurement; `59938 ms` measurement coverage
- Cadence: median `94 ms`, P95 `110 ms`; CSV `88,588` bytes
- Monitor warnings: none
- Window check: `285` foreground-false samples and `31` minimized samples after one minimize/restore action
- Report: `D:\PerDec\reports\runs\227a72e4-0f64-43b9-8991-eda5a24c8b1e\analysis.html`

One actionable 36.37 ms hitch (`incident-0007`) retained its existing
`gpu_bound_candidate` classification. Its ±500 ms window contained 10 process
samples: process CPU peak 16.38%, IO read peak 0 B/s, working-set delta -282,624
bytes, plus background/minimized and private-memory-change flags. These are
overlapping facts, not a claim that minimizing or memory caused the hitch.

After the 1 Hz thread-count optimization, a final 5-second lifecycle smoke run
(`e3ef4e64-8264-4bfe-b444-9dda17330d2e`) also completed automatically: 823
Unity frames, 209 process samples total, 50 measurement-window samples, 4891 ms
process coverage, 94 ms median cadence, no monitor warnings, and automatic JSON/
HTML generation. The 60-second run above remains the main diagnostic and window-
state acceptance run; the shorter run verifies the final optimized code path.

## G. PoC conclusion

CPU, working/private memory, IO rates and window state are useful structured
context; window state was directly validated and can qualify pacing/hitch
observations. Thread count is useful at lower frequency. Page-fault rate is
available but remains exploratory and is not yet an evidence flag. Ten Hz is
adequate for ±500 ms context (about 10 samples/window), not for exact-frame
causality. It cannot expose system GPU, thread stacks, scheduler/ETW facts or
method-level PlayerLoop causes.

Initial self-sampling showed the 10 Hz Python monitor at about 41.6% of one core,
mainly from 10 Hz system thread snapshots. Refreshing thread count at 1 Hz reduced
the same rough 5-second check to about 5.0% of one core / 0.18% total logical CPU
capacity, with about 1.1 KB/s CSV growth. This is a coarse PoC observation, not a
precision benchmark.

## H. Phase 4 C++ handoff

Migrate PID-bound lifecycle, monotonic sampling thread, CPU/memory/IO/window-state
collection, nullable rows, rate derivation, timestamp alignment metadata and the
`process.csv` contract to a C++20/CMake/Win32 Runtime Core. Do not carry forward
Python-specific GIL/threading, CSV-per-sample flushing as the final buffer design,
or full-system thread snapshots at 10 Hz. ETW, GPU Engine/VRAM, per-thread CPU and
call stacks remain separate future decisions; Phase 4 was not started here.

## I. Git state

Branch at implementation: `codex/chore-stage2-baseline`; HEAD
`09013c51b029a0b28f37009e5a18f2961f9790f6`. The working tree already contained
uncommitted Phase 3/3.1 work and now also contains this PoC. No commit, push,
merge, rebase or reset was performed. No Unity project or Collector file was
modified.
