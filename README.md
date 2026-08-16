# PerfGuardian

PerfGuardian runs Unity Standalone performance captures, validates their output,
and produces offline single-run diagnostic reports.

## Quick start

Edit `perfguardian.local.json`, then either select **PerfGuardian：运行测试**
in VS Code's Run and Debug panel and press F5, or run this short command:

```powershell
python -m runner
```

Command-line values remain available as temporary overrides, for example:

```powershell
python -m runner --measurement-seconds 30 --repetitions 3
python -m analyzer --artifacts-root artifacts --output-dir reports
python -m analyzer --artifacts-root artifacts --output-dir reports --latest
python -m analyzer --artifacts-root artifacts --output-dir reports --run-id <run_id>
```

The command without a selector remains the compatible full-scan mode and writes
`reports/analysis.json` and `reports/analysis.html`. `--latest` selects the
eligible run with the latest valid Runner completion time. `--run-id` requires
one exact, unique, eligible run. A selected run is isolated at:

```text
reports/runs/<run_id>/analysis.json
reports/runs/<run_id>/analysis.html
```

Analyzer output currently uses the internal `0.5.0-experimental` diagnostics
schema. It supports legacy and 25-column captures, preserves Collector
availability semantics, merges incremental-GC work windows, detects long-frame
intervals, and emits conservative CPU/GPU/GC/frame-pacing/unattributed evidence.
The HTML report is fully offline and includes timelines, incident evidence, GC,
allocation, memory trends, quality warnings, and raw CSV row references.

On Windows, Runner also starts an optional 10 Hz process monitor bound to the
exact `Popen` PID. It writes `process.csv` beside the Unity artifacts and adds
CPU, memory, IO, thread-count, foreground, and minimized-window context to
severe/major incidents. Monitor failure never changes Unity artifact eligibility.
Configure its cadence with `external_monitor_interval_ms` (default `100`).

Each repetition is written to `artifacts/<project_id>/<experiment_id>/<uuid4>/`.
The runner always passes `-logFile` and `--pg-output` to that same new directory.
It never overwrites prior captures and never edits Unity's `run.json`.
After an eligible capture, the Runner automatically writes that run's isolated
Analyzer report and prints its absolute path. Analyzer failure is recorded in
the Runner report but does not alter the eligible capture or Unity manifest.

The default short settings (1 second warmup, 5 seconds measurement, 16 ms interval,
one repetition) are integration-only and are not performance conclusions.

## Development

Python 3.11+ is required. The MVP uses only the Python standard library.

```powershell
python -m unittest discover -s tests -v
```

See `docs/data-contract.md`, `docs/experiment-protocol.md`, and
`docs/external-process-telemetry-contract.md`.
