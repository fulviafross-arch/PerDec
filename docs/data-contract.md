# PerfGuardian data contract

Protocol version: `0.1.0-draft`.

The MVP consumes four Unity-produced files from one run directory: `run.json`,
`frames.csv`, `events.jsonl`, and `player.log`. Unknown optional metric values
must be JSON/CSV `null` (or empty CSV), never zero or inferred. Timestamps are
non-negative milliseconds; configuration durations are seconds; capacities bytes.

`run.json` requires `schema_version`, `run_id`, `status`, and `failure_reason`.
Supported schema: `0.1.0-draft`. Eligibility requires `status: "completed"` and
`failure_reason: null`.

`frames.csv` requires `run_id` and `timestamp_ms`. Supported metrics are
`frame_time_ms`, `memory_used_bytes`, `cpu_frame_time_ms`, `gpu_frame_time_ms`,
and `gc_allocated_bytes`. Cells are finite numeric values or empty/null; all
frame run IDs match the directory run ID and timestamps are non-decreasing.

`events.jsonl` is one JSON object per non-empty line. Its final object must have
`event_type` (or legacy `type`) equal to `run_completed`. Runner statuses such as
`timeout` and `invalid_artifacts` are report-only and never written to `run.json`.

Contract changes require rationale, producer/consumer impacts, migration,
compatibility period, and acceptance tests before implementation.
