from __future__ import annotations

import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable

from .diagnostics import (
    allocation_diagnostics,
    build_activity_events,
    chart_data,
    detect_incidents,
    gc_diagnostics,
    memory_diagnostics,
    summarize_events,
)
from .external_process import attach_process_evidence, load_external_process

ANALYSIS_SCHEMA_VERSION = "0.5.0-experimental"
SUPPORTED_DATA_SCHEMAS = {"0.1.0-draft"}

BASE_METRICS = (
    "frame_time_ms",
    "cpu_frame_time_ms",
    "gpu_frame_time_ms",
    "memory_used_bytes",
    "gc_allocated_bytes",
)
EXTENDED_METRICS = (
    "unity_reserved_bytes",
    "unity_unused_reserved_bytes",
    "gc_used_bytes",
    "gc_reserved_bytes",
    "gc_gen0_collections",
    "gc_gen1_collections",
    "gc_gen2_collections",
    "gc_collect_ms",
    "script_update_ms",
    "script_fixed_update_ms",
    "script_late_update_ms",
    "physics_simulate_ms",
    "ui_build_batch_ms",
    "wait_for_target_fps_ms",
    "gfx_wait_for_present_ms",
    "job_wait_ms",
)
METRICS = BASE_METRICS + EXTENDED_METRICS
MARKERS = (
    "gc_collect_ms",
    "script_update_ms",
    "script_fixed_update_ms",
    "script_late_update_ms",
    "physics_simulate_ms",
    "ui_build_batch_ms",
    "wait_for_target_fps_ms",
    "gfx_wait_for_present_ms",
    "job_wait_ms",
)
INTEGER_FIELDS = {
    "timestamp_ms", "frame_index", "memory_used_bytes", "gc_allocated_bytes",
    "unity_reserved_bytes", "unity_unused_reserved_bytes", "gc_used_bytes",
    "gc_reserved_bytes", "gc_gen0_collections", "gc_gen1_collections",
    "gc_gen2_collections",
}
MEMORY_METRICS = (
    "memory_used_bytes", "gc_used_bytes", "gc_reserved_bytes",
    "unity_reserved_bytes", "unity_unused_reserved_bytes",
)


def percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    low, high = int(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def parse_number(value: Any) -> float | None:
    if value is None or str(value).strip().lower() in ("", "null"):
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite number: {value}")
    return number


def read_frames(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        rows: list[dict[str, Any]] = []
        for line_number, raw in enumerate(reader, start=2):
            row: dict[str, Any] = dict(raw)
            for field in ("timestamp_ms", "frame_index") + METRICS:
                if field not in raw:
                    continue
                try:
                    value = parse_number(raw[field])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{path.name}:{line_number}:{field}: {exc}") from exc
                if value is not None and field in INTEGER_FIELDS and not value.is_integer():
                    raise ValueError(f"{path.name}:{line_number}:{field}: expected integer")
                row[field] = int(value) if value is not None and field in INTEGER_FIELDS else value
            rows.append(row)
    return rows, columns


def metric_status(
    metric: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    collector: dict[str, Any],
) -> dict[str, Any]:
    if metric not in columns:
        return {
            "availability": "field_absent",
            "valid_count": 0,
            "missing_count": len(rows),
            "missing_ratio": 1.0 if rows else 0.0,
            "zero_count": 0,
            "statistics": None,
        }
    values = [row.get(metric) for row in rows]
    valid = [value for value in values if isinstance(value, (int, float))]
    missing_count = len(rows) - len(valid)
    result: dict[str, Any] = {
        "availability": _declared_availability(metric, valid, collector),
        "valid_count": len(valid),
        "missing_count": missing_count,
        "missing_ratio": missing_count / len(rows) if rows else 0.0,
        "zero_count": sum(value == 0 for value in valid),
        "statistics": None,
    }
    if valid:
        result["statistics"] = {
            "mean": statistics.fmean(valid),
            "median": statistics.median(valid),
            "p90": percentile(valid, 0.90),
            "p95": percentile(valid, 0.95),
            "p99": percentile(valid, 0.99),
            "min": min(valid),
            "max": max(valid),
        }
    return result


def _declared_availability(
    metric: str, values: list[float | int], collector: dict[str, Any]
) -> str:
    flags = set(collector.get("quality_flags") or [])
    sources = collector.get("profiler_marker_sources") or {}
    if metric == "gc_allocated_bytes" and (
        "gc_allocated_in_frame_unavailable" in flags
        or collector.get("gc_allocated_bytes_source") is None
    ):
        return "declared_unavailable"
    if metric in ("gc_gen1_collections", "gc_gen2_collections"):
        generation = int(metric[6])
        maximum = collector.get("gc_max_generation")
        if maximum is not None and maximum < generation:
            return "declared_unsupported"
    if f"profiler_marker_unavailable:{metric}" in flags or (
        metric in MARKERS and metric in sources and sources.get(metric) is None
    ):
        return "declared_unavailable"
    if not values:
        return "all_null_undeclared"
    return "available"


def quality_assessment(
    rows: list[dict[str, Any]],
    columns: list[str],
    collector: dict[str, Any],
    metrics: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    flags = set(collector.get("quality_flags") or [])
    issues: list[str] = []
    warnings: list[str] = []
    indexes = [row.get("frame_index") for row in rows]
    if "frame_index" in columns:
        if any(index is None for index in indexes):
            issues.append("frame_index_missing")
        elif indexes != list(range(len(rows))):
            issues.append("frame_index_not_contiguous_from_zero")
    timestamps = [row.get("timestamp_ms") for row in rows]
    if any(value is None or value < 0 for value in timestamps):
        issues.append("invalid_timestamp")
    elif any(current < previous for previous, current in zip(timestamps, timestamps[1:])):
        issues.append("timestamp_not_monotonic")
    for metric, result in metrics.items():
        status = result["availability"]
        if status == "all_null_undeclared":
            warnings.append(f"all_null_undeclared:{metric}")
        if result["missing_count"] and status == "available":
            missing_indexes = [index for index, row in enumerate(rows) if row.get(metric) is None]
            if not _is_allowed_tail_missing(metric, missing_indexes, len(rows), flags):
                warnings.append(f"unexpected_missing_values:{metric}")
    inconclusive = any(
        warning.startswith("all_null_undeclared:")
        and warning.split(":", 1)[1] in BASE_METRICS
        for warning in warnings
    ) or any(
        warning.startswith("unexpected_missing_values:")
        and metrics[warning.split(":", 1)[1]]["missing_ratio"] > 0.05
        for warning in warnings
    )
    status = "invalid" if issues else ("inconclusive" if inconclusive else "valid")
    return {
        "status": status,
        "issues": issues,
        "warnings": warnings,
        "quality_flags": sorted(flags),
    }


def _is_allowed_tail_missing(
    metric: str, missing_indexes: list[int], row_count: int, flags: set[str]
) -> bool:
    if not missing_indexes:
        return True
    if missing_indexes != list(range(min(missing_indexes), row_count)):
        return False
    tail_count = len(missing_indexes)
    if metric in ("cpu_frame_time_ms", "gpu_frame_time_ms"):
        return tail_count <= 4
    if metric in MARKERS or metric.startswith("gc_"):
        declared = (
            "last_frame_profiler_data_not_finalized" in flags
            or "last_frame_gc_collection_count_not_finalized" in flags
        )
        return declared and tail_count <= 1
    return False


def gc_windows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    position = 0
    while position < len(rows):
        if not _positive(rows[position].get("gc_collect_ms")):
            position += 1
            continue
        start = position
        while position + 1 < len(rows) and _positive(rows[position + 1].get("gc_collect_ms")):
            position += 1
        end = position
        completion_end = min(end + 1, len(rows) - 1)
        completion_rows = rows[start : completion_end + 1]
        marker_values = [row["gc_collect_ms"] for row in rows[start : end + 1]]
        generation_counts = {
            f"gen{generation}": sum(
                int(row.get(f"gc_gen{generation}_collections") or 0)
                for row in completion_rows
            )
            for generation in range(3)
        }
        before = _nearest_value(rows, start - 1, "gc_used_bytes", -1)
        after = _nearest_value(rows, completion_end, "gc_used_bytes", 1)
        windows.append(
            {
                "window_index": len(windows),
                "start_frame_index": _frame_index(rows[start], start),
                "end_frame_index": _frame_index(rows[end], end),
                "start_timestamp_ms": rows[start].get("timestamp_ms"),
                "end_timestamp_ms": rows[end].get("timestamp_ms"),
                "completion_frame_index": _completion_frame(completion_rows, start),
                "duration_frames": end - start + 1,
                "marker_total_ms": sum(marker_values),
                "marker_max_frame_ms": max(marker_values),
                "generation_collection_counts": generation_counts,
                "gc_used_bytes_before": before,
                "gc_used_bytes_after": after,
                "gc_used_bytes_delta": after - before if before is not None and after is not None else None,
            }
        )
        position += 1
    return windows


def _completion_frame(rows: list[dict[str, Any]], start: int) -> int | None:
    for offset, row in enumerate(rows):
        if any(_positive(row.get(f"gc_gen{generation}_collections")) for generation in range(3)):
            return _frame_index(row, start + offset)
    return None


def _nearest_value(
    rows: list[dict[str, Any]], position: int, field: str, direction: int
) -> float | int | None:
    while 0 <= position < len(rows):
        value = rows[position].get(field)
        if isinstance(value, (int, float)):
            return value
        position += direction
    return None


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and value > 0


def _frame_index(row: dict[str, Any], fallback: int) -> int:
    value = row.get("frame_index")
    return int(value) if isinstance(value, (int, float)) else fallback


def memory_trends(rows: list[dict[str, Any]]) -> dict[str, Any]:
    duration_seconds = 0.0
    if rows and isinstance(rows[-1].get("timestamp_ms"), (int, float)):
        duration_seconds = rows[-1]["timestamp_ms"] / 1000
    output: dict[str, Any] = {}
    for metric in MEMORY_METRICS:
        values = [row.get(metric) for row in rows]
        valid = [value for value in values if isinstance(value, (int, float))]
        if not valid:
            output[metric] = None
            continue
        first = next(value for value in values if isinstance(value, (int, float)))
        last = next(value for value in reversed(values) if isinstance(value, (int, float)))
        output[metric] = {
            "start": first,
            "end": last,
            "peak": max(valid),
            "delta": last - first,
            "endpoint_slope_bytes_per_second": (last - first) / duration_seconds
            if duration_seconds > 0 else None,
        }
    return output


def anomaly_evidence(
    rows: list[dict[str, Any]], manifest: dict[str, Any], windows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    valid = [row["frame_time_ms"] for row in rows if isinstance(row.get("frame_time_ms"), (int, float))]
    threshold = percentile(valid, 0.99)
    if threshold is None:
        return []
    window_indexes = {
        index for window in windows
        for index in range(window["start_frame_index"], window["end_frame_index"] + 1)
    }
    evidence: list[dict[str, Any]] = []
    for position, row in enumerate(rows):
        frame_time = row.get("frame_time_ms")
        if not isinstance(frame_time, (int, float)) or frame_time < threshold:
            continue
        index = _frame_index(row, position)
        evidence.append(
            {
                "frame_index": index,
                "csv_row_number": position + 2,
                "timestamp_ms": row.get("timestamp_ms"),
                "frame_time_ms": frame_time,
                "cpu_frame_time_ms": row.get("cpu_frame_time_ms"),
                "gpu_frame_time_ms": row.get("gpu_frame_time_ms"),
                "gc_window_active": index in window_indexes,
                "gc_allocated_bytes": row.get("gc_allocated_bytes"),
                "gc_used_bytes": row.get("gc_used_bytes"),
                "markers_ms": {metric: row.get(metric) for metric in MARKERS},
                "attribution": _attribution(row, manifest, index in window_indexes),
            }
        )
    return evidence


def _attribution(
    row: dict[str, Any], manifest: dict[str, Any], gc_active: bool
) -> dict[str, str]:
    frame = row.get("frame_time_ms")
    cpu = row.get("cpu_frame_time_ms")
    gpu = row.get("gpu_frame_time_ms")
    wait = row.get("wait_for_target_fps_ms")
    refresh = (manifest.get("environment") or {}).get("display_refresh_rate_hz")
    budget = 1000 / refresh if isinstance(refresh, (int, float)) and refresh > 0 else None
    if gc_active:
        return {"category": "gc_participating", "confidence": "high"}
    if (
        isinstance(wait, (int, float)) and isinstance(frame, (int, float))
        and isinstance(budget, (int, float)) and wait > frame * 0.25
        and abs(frame - budget) <= budget * 0.20
    ):
        return {"category": "frame_pacing_wait", "confidence": "high"}
    if isinstance(cpu, (int, float)) and isinstance(gpu, (int, float)):
        if cpu > gpu * 1.25:
            return {"category": "cpu_or_main_thread_side", "confidence": "medium"}
        if gpu >= cpu * 0.9:
            return {"category": "gpu_or_render_side", "confidence": "medium"}
    return {"category": "unattributed", "confidence": "low"}


def comparison_key(manifest: dict[str, Any]) -> dict[str, Any]:
    build = manifest.get("build") or {}
    environment = manifest.get("environment") or {}
    scenario = manifest.get("scenario") or {}
    protocol = manifest.get("protocol") or {}
    collector = manifest.get("collector") or {}
    return {
        "schema_version": manifest.get("schema_version"),
        "experiment_id": manifest.get("experiment_id"),
        "experiment_version": manifest.get("experiment_version"),
        "scenario_id": scenario.get("scenario_id"),
        "scenario_version": scenario.get("scenario_version"),
        "commit_sha": build.get("commit_sha"),
        "build_type": build.get("build_type"),
        "unity_version": build.get("unity_version"),
        "collector_version": collector.get("collector_version"),
        "cpu_model": environment.get("cpu_model"),
        "gpu_model": environment.get("gpu_model"),
        "display_width_pixels": environment.get("display_width_pixels"),
        "display_height_pixels": environment.get("display_height_pixels"),
        "display_refresh_rate_hz": environment.get("display_refresh_rate_hz"),
        "v_sync_count": environment.get("v_sync_count"),
        "target_frame_rate": environment.get("target_frame_rate"),
        "warmup_seconds": protocol.get("warmup_seconds"),
        "measurement_seconds": protocol.get("measurement_seconds"),
    }


def analyze_run(run_dir: Path, runner_report: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") not in SUPPORTED_DATA_SCHEMAS:
        raise ValueError(f"unsupported schema: {manifest.get('schema_version')}")
    rows, columns = read_frames(run_dir / "frames.csv")
    collector = manifest.get("collector") or {}
    metrics = {metric: metric_status(metric, columns, rows, collector) for metric in METRICS}
    windows = gc_windows(rows) if "gc_collect_ms" in columns else []
    quality = quality_assessment(rows, columns, collector, metrics)
    frame_values = [row["frame_time_ms"] for row in rows if isinstance(row.get("frame_time_ms"), (int, float))]
    quality_flags = quality["quality_flags"]
    performance_events, diagnostic_thresholds = detect_incidents(
        rows, manifest, windows, quality_flags
    )
    measurement_coverage_ms = rows[-1].get("timestamp_ms") if rows else None
    external_process_monitor, process_rows = load_external_process(
        run_dir, runner_report, measurement_coverage_ms
    )
    attach_process_evidence(
        performance_events,
        process_rows,
        external_process_monitor.get("availability") == "available",
    )
    trends = memory_trends(rows)
    memory = memory_diagnostics(rows, trends)
    gc = gc_diagnostics(windows, performance_events)
    allocations = allocation_diagnostics(rows, quality_flags)
    build = manifest.get("build") or {}
    scenario = manifest.get("scenario") or {}
    environment = manifest.get("environment") or {}
    traceability_warnings = []
    if build.get("commit_sha") in (None, "", "abc123", "unknown"):
        traceability_warnings.append("placeholder_or_missing_commit_sha")
    activities = build_activity_events(
        gc["windows"], memory, rows[-1].get("timestamp_ms") if rows else None
    )
    events = performance_events + activities
    diagnostic_summary = summarize_events(
        events, gc["window_count"], memory["status"], len(rows), quality
    )
    return {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "run_id": manifest.get("run_id"),
        "artifact_directory": str(run_dir),
        "runner_eligible_for_analysis": runner_report.get("eligible_for_analysis") if runner_report else None,
        "analysis_eligible": quality["status"] == "valid",
        "frame_count": len(rows),
        "measurement_coverage_ms": measurement_coverage_ms,
        "columns": columns,
        "collector": {
            "collector_id": collector.get("collector_id"),
            "collector_version": collector.get("collector_version"),
            "frame_capture_mode": collector.get("frame_capture_mode"),
            "gc_max_generation": collector.get("gc_max_generation"),
            "profiler_marker_alignment": collector.get("profiler_marker_alignment"),
            "gc_collection_count_alignment": collector.get("gc_collection_count_alignment"),
        },
        "comparison_key": comparison_key(manifest),
        "run_summary": {
            "build_type": build.get("build_type"),
            "commit_sha": build.get("commit_sha"),
            "unity_version": build.get("unity_version"),
            "scenario_id": scenario.get("scenario_id"),
            "scenario_version": scenario.get("scenario_version"),
            "active_scene": scenario.get("active_scene"),
            "environment": {
                "cpu_model": environment.get("cpu_model"),
                "gpu_model": environment.get("gpu_model"),
                "display_width_pixels": environment.get("display_width_pixels"),
                "display_height_pixels": environment.get("display_height_pixels"),
                "display_refresh_rate_hz": environment.get("display_refresh_rate_hz"),
                "v_sync_count": environment.get("v_sync_count"),
                "target_frame_rate": environment.get("target_frame_rate"),
            },
            "traceability_warnings": traceability_warnings,
        },
        "quality": quality,
        "metrics": metrics,
        "gc_windows": windows,
        "memory_trends": trends,
        "anomaly_threshold": {"method": "p99", "frame_time_ms": percentile(frame_values, 0.99)},
        "anomaly_frames": anomaly_evidence(rows, manifest, windows),
        "diagnostic_thresholds": diagnostic_thresholds,
        "diagnostic_summary": diagnostic_summary,
        "events": events,
        "gc_diagnostics": gc,
        "allocation_diagnostics": allocations,
        "memory_diagnostics": memory,
        "external_process_monitor": external_process_monitor,
        "chart_data": chart_data(rows, events),
        "source_artifacts": {
            "run_json": str(run_dir / "run.json"),
            "frames_csv": str(run_dir / "frames.csv"),
            "events_jsonl": str(run_dir / "events.jsonl"),
            "player_log": str(run_dir / "player.log"),
            "process_csv": str(run_dir / "process.csv"),
        },
    }


def _overall_conclusion(
    incidents: list[dict[str, Any]],
    quality: dict[str, Any],
    memory: dict[str, Any],
) -> str:
    if quality["status"] != "valid":
        return "Data quality is not fully valid; diagnostic conclusions are limited."
    if not incidents:
        return "No frame-time incident exceeded the experimental single-run thresholds."
    severe = sum(incident["severity"] == "severe" for incident in incidents)
    major = sum(incident["severity"] == "major" for incident in incidents)
    conclusion = f"Detected {len(incidents)} frame-time incident(s): {severe} severe, {major} major."
    if memory["status"] == "insufficient_duration":
        conclusion += " Memory duration is insufficient for a leak or sustained-growth conclusion."
    return conclusion


def analyze_artifacts(artifacts_root: Path) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for report_path in sorted(artifacts_root.rglob("runner-report.json")):
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not report.get("eligible_for_analysis"):
                skipped.append({"path": str(report_path), "reason": "runner_ineligible"})
                continue
            runs.append(analyze_run(report_path.parent, report))
        except (OSError, ValueError, csv.Error, json.JSONDecodeError) as exc:
            skipped.append({"path": str(report_path), "reason": str(exc)})
    build_types = sorted({
        run["comparison_key"].get("build_type") for run in runs
        if run["comparison_key"].get("build_type")
    })
    warnings = []
    if len(build_types) > 1:
        warnings.append("mixed_build_types_not_directly_comparable")
    analysis_eligible_count = sum(run["analysis_eligible"] for run in runs)
    return {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "eligible_run_count": analysis_eligible_count,
        "runner_eligible_run_count": len(runs),
        "analysis_ineligible_or_inconclusive_count": len(runs) - analysis_eligible_count,
        "skipped_run_count": len(skipped),
        "comparison_warnings": warnings,
        "runs": runs,
        "skipped_runs": skipped,
    }
