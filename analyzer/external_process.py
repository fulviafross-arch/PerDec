from __future__ import annotations

import csv
import math
import statistics
from pathlib import Path
from typing import Any


PROCESS_EVIDENCE_CONFIG = {
    "window_before_ms": 500.0,
    "window_after_ms": 500.0,
    "process_cpu_elevated_percent": 75.0,
    "io_rate_spike_bytes_per_sec": 10 * 1024 * 1024,
    "memory_change_bytes": 8 * 1024 * 1024,
    "status": "experimental_evidence_only",
}
NUMERIC_FIELDS = (
    "timestamp_ms", "process_cpu_percent", "working_set_bytes", "private_bytes",
    "thread_count", "io_read_bytes", "io_write_bytes",
    "io_read_rate_bytes_per_sec", "io_write_rate_bytes_per_sec",
    "page_fault_count", "page_fault_rate_per_sec",
)
BOOLEAN_FIELDS = ("process_alive", "is_foreground", "is_minimized")


def load_external_process(
    run_dir: Path,
    runner_report: dict[str, Any] | None,
    measurement_coverage_ms: float | int | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = run_dir / "process.csv"
    runner_metadata = (runner_report or {}).get("external_process_monitor") or {}
    base = {
        "availability": "unavailable",
        "sample_interval_ms": runner_metadata.get("sample_interval_ms"),
        "sample_count": 0,
        "measurement_sample_count": 0,
        "coverage_ms": 0.0,
        "timestamp_alignment": runner_metadata.get("timestamp_alignment") or {
            "status": "unknown",
            "warnings": ["runner_alignment_metadata_unavailable"],
        },
        "field_availability": {},
        "warnings": list(runner_metadata.get("warnings") or []),
        "evidence_config": dict(PROCESS_EVIDENCE_CONFIG),
    }
    if not path.is_file():
        base["warnings"].append("process_csv_missing")
        return base, []
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or [])
            rows = [_parse_row(raw, line, base["warnings"]) for line, raw in enumerate(reader, 2)]
    except (OSError, csv.Error) as exc:
        base["warnings"].append(f"process_csv_read_failed:{type(exc).__name__}:{exc}")
        return base, []
    rows = [row for row in rows if isinstance(row.get("timestamp_ms"), (int, float))]
    if not rows:
        base["warnings"].append("process_csv_has_no_timestamped_samples")
        return base, []
    rows.sort(key=lambda row: row["timestamp_ms"])
    timestamps = [row["timestamp_ms"] for row in rows]
    in_measurement = [
        row for row in rows
        if row["timestamp_ms"] >= 0
        and (not isinstance(measurement_coverage_ms, (int, float)) or row["timestamp_ms"] <= measurement_coverage_ms)
    ]
    intervals = [right - left for left, right in zip(timestamps, timestamps[1:]) if right > left]
    if base["sample_interval_ms"] is None and intervals:
        base["sample_interval_ms"] = statistics.median(intervals)
    base.update({
        "availability": "available",
        "sample_count": len(rows),
        "measurement_sample_count": len(in_measurement),
        "coverage_ms": max(0.0, timestamps[-1] - timestamps[0]),
        "measurement_coverage_ms": (
            max(row["timestamp_ms"] for row in in_measurement) - min(row["timestamp_ms"] for row in in_measurement)
            if len(in_measurement) > 1 else 0.0
        ),
        "field_availability": {
            field: sum(row.get(field) is not None for row in rows)
            for field in tuple(NUMERIC_FIELDS) + tuple(BOOLEAN_FIELDS)
            if field in columns
        },
    })
    alignment_status = (base["timestamp_alignment"] or {}).get("status")
    if alignment_status not in ("aligned", "synthetic_test"):
        base["warnings"].append("process_unity_timestamp_alignment_not_confirmed")
    return base, rows


def attach_process_evidence(
    events: list[dict[str, Any]],
    process_rows: list[dict[str, Any]],
    telemetry_available: bool | None = None,
) -> None:
    available = bool(process_rows) if telemetry_available is None else telemetry_available
    for event in events:
        _update_process_cpu_limitation(event, available)
        if event.get("event_type") not in ("severe_hitch", "hitch"):
            continue
        start, end = event.get("start_timestamp_ms"), event.get("end_timestamp_ms")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            event["process_evidence"] = {
                "availability": "unavailable", "sample_count": 0,
                "warnings": ["incident_timestamp_unavailable"],
            }
            continue
        window_start = start - PROCESS_EVIDENCE_CONFIG["window_before_ms"]
        window_end = end + PROCESS_EVIDENCE_CONFIG["window_after_ms"]
        samples = [row for row in process_rows if window_start <= row["timestamp_ms"] <= window_end]
        event["process_evidence"] = summarize_process_window(samples, window_start, window_end)


def _update_process_cpu_limitation(event: dict[str, Any], available: bool) -> None:
    old = "No process CPU utilization or system GPU utilization is available."
    replacement = (
        "External process CPU utilization is available as sampled window context; system GPU utilization is unavailable."
        if available
        else "Process CPU utilization and system GPU utilization are unavailable."
    )
    lists = [event.get("limitations"), (event.get("diagnosis") or {}).get("limitations")]
    seen: set[int] = set()
    for values in lists:
        if not isinstance(values, list) or id(values) in seen:
            continue
        seen.add(id(values))
        for index, value in enumerate(values):
            if value == old:
                values[index] = replacement


def summarize_process_window(
    samples: list[dict[str, Any]], window_start_ms: float, window_end_ms: float
) -> dict[str, Any]:
    if not samples:
        return {
            "availability": "unavailable", "sample_count": 0,
            "window_start_ms": window_start_ms, "window_end_ms": window_end_ms,
            "evidence_flags": [], "warnings": ["no_process_samples_in_incident_window"],
        }
    result: dict[str, Any] = {
        "availability": "available",
        "sample_count": len(samples),
        "window_start_ms": window_start_ms,
        "window_end_ms": window_end_ms,
        "process_cpu_percent": _mean_max(samples, "process_cpu_percent"),
        "working_set_bytes": _start_end_max_delta(samples, "working_set_bytes"),
        "private_bytes": _start_end_max_delta(samples, "private_bytes"),
        "thread_count": _min_max(samples, "thread_count"),
        "io_read_rate_bytes_per_sec": _mean_max(samples, "io_read_rate_bytes_per_sec"),
        "io_write_rate_bytes_per_sec": _mean_max(samples, "io_write_rate_bytes_per_sec"),
        "page_fault_rate_per_sec": _mean_max(samples, "page_fault_rate_per_sec"),
        "foreground_ratio": _true_ratio(samples, "is_foreground"),
        "minimized_seen": any(row.get("is_minimized") is True for row in samples),
        "evidence_flags": [],
        "warnings": ["sampled_window_context_is_not_per_frame_causality"],
    }
    flags = result["evidence_flags"]
    cpu_max = (result["process_cpu_percent"] or {}).get("max")
    if isinstance(cpu_max, (int, float)) and cpu_max >= PROCESS_EVIDENCE_CONFIG["process_cpu_elevated_percent"]:
        flags.append("process_cpu_elevated")
    for name, flag in (("io_read_rate_bytes_per_sec", "io_read_spike"), ("io_write_rate_bytes_per_sec", "io_write_spike")):
        maximum = (result[name] or {}).get("max")
        if isinstance(maximum, (int, float)) and maximum >= PROCESS_EVIDENCE_CONFIG["io_rate_spike_bytes_per_sec"]:
            flags.append(flag)
    for name, flag in (("working_set_bytes", "working_set_change"), ("private_bytes", "private_bytes_change")):
        delta = (result[name] or {}).get("delta")
        if isinstance(delta, (int, float)) and abs(delta) >= PROCESS_EVIDENCE_CONFIG["memory_change_bytes"]:
            flags.append(flag)
    threads = result["thread_count"] or {}
    if threads.get("min") is not None and threads.get("max") != threads.get("min"):
        flags.append("thread_count_change")
    if result["foreground_ratio"] is not None and result["foreground_ratio"] < 1.0:
        flags.append("background_window_state")
    if result["minimized_seen"]:
        flags.append("minimized_window_state")
    result["no_obvious_process_level_spike"] = not flags
    return result


def _parse_row(raw: dict[str, Any], line: int, warnings: list[str]) -> dict[str, Any]:
    row: dict[str, Any] = dict(raw)
    for field in NUMERIC_FIELDS:
        if field not in raw or raw[field] in (None, "", "null"):
            row[field] = None
            continue
        try:
            value = float(raw[field])
            if not math.isfinite(value):
                raise ValueError("non-finite")
            row[field] = value
        except (TypeError, ValueError):
            row[field] = None
            warnings.append(f"process_csv:{line}:{field}:invalid_number")
    for field in BOOLEAN_FIELDS:
        value = str(raw.get(field) or "").strip().lower()
        row[field] = True if value == "true" else False if value == "false" else None
    return row


def _values(samples: list[dict[str, Any]], field: str) -> list[float]:
    return [float(row[field]) for row in samples if isinstance(row.get(field), (int, float))]


def _mean_max(samples: list[dict[str, Any]], field: str) -> dict[str, float] | None:
    values = _values(samples, field)
    return {"mean": statistics.fmean(values), "max": max(values)} if values else None


def _start_end_max_delta(samples: list[dict[str, Any]], field: str) -> dict[str, float] | None:
    values = _values(samples, field)
    return {"start": values[0], "end": values[-1], "max": max(values), "delta": values[-1] - values[0]} if values else None


def _min_max(samples: list[dict[str, Any]], field: str) -> dict[str, float] | None:
    values = _values(samples, field)
    return {"min": min(values), "max": max(values)} if values else None


def _true_ratio(samples: list[dict[str, Any]], field: str) -> float | None:
    values = [row[field] for row in samples if isinstance(row.get(field), bool)]
    return sum(values) / len(values) if values else None
