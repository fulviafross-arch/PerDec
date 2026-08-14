# PerfGuardian

PerfGuardian runs Unity Standalone performance captures, validates their output,
and produces offline analysis reports.

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
```

Analyzer output currently uses the internal `0.2.0-experimental` schema. It
supports legacy and 25-column captures, preserves Collector availability
semantics, merges incremental-GC work windows, and emits P99 anomaly evidence.
Release and Development runs are explicitly flagged as not directly comparable.

Each repetition is written to `artifacts/<project_id>/<experiment_id>/<uuid4>/`.
The runner always passes `-logFile` and `--pg-output` to that same new directory.
It never overwrites prior captures and never edits Unity's `run.json`.

The default short settings (1 second warmup, 5 seconds measurement, 16 ms interval,
one repetition) are integration-only and are not performance conclusions.

## Development

Python 3.11+ is required. The MVP uses only the Python standard library.

```powershell
python -m unittest discover -s tests -v
```

See `docs/data-contract.md` and `docs/experiment-protocol.md`.
