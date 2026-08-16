from __future__ import annotations

import math
import statistics
from typing import Any

DIAGNOSTIC_THRESHOLDS: dict[str, Any] = {
    "status": "experimental",
    "adaptive_mad_multiplier": 6.0,
    "adaptive_minimum_margin_ms": 2.0,
    "frame_budget_multiplier": 1.5,
    "budget_miss_mad_multiplier": 4.0,
    "budget_miss_minimum_margin_ms": 1.0,
    "budget_miss_frame_budget_multiplier": 1.25,
    "hitch_frame_time_ms": 33.33,
    "absolute_long_frame_ms": 50.0,
    "merge_normal_gap_frames": 1,
    "merge_max_gap_ms": 50.0,
    "actionable_evidence_context_frames": 5,
    "non_actionable_evidence_context_frames": 0,
    "chart_max_buckets": 900,
    "chart_preserved_context_frames": 5,
    "top_issue_limit": 5,
    "attribution_minimum_frame_share": 0.8,
    "cpu_over_gpu_ratio": 1.25,
    "gpu_near_cpu_ratio": 0.9,
    "pacing_wait_minimum_frame_share": 0.25,
    "pacing_frame_budget_minimum_multiplier": 0.8,
    "pacing_frame_budget_maximum_multiplier": 3.5,
    "pacing_state_minimum_frames": 3,
    "allocation_mad_multiplier": 8.0,
    "allocation_minimum_margin_bytes": 256.0,
    "memory_minimum_duration_seconds": 30.0,
    "memory_rolling_window_frames": 60,
    "memory_growth_slope_bytes_per_second": 32768.0,
    "memory_step_minimum_bytes": 1048576,
    "memory_step_mad_multiplier": 8.0,
}

INCIDENT_MARKERS = (
    "gc_collect_ms", "script_update_ms", "script_fixed_update_ms",
    "script_late_update_ms", "physics_simulate_ms", "ui_build_batch_ms",
    "wait_for_target_fps_ms", "gfx_wait_for_present_ms", "job_wait_ms",
)


def median_absolute_deviation(values: list[float]) -> float:
    if not values:
        return 0.0
    center = statistics.median(values)
    return statistics.median(abs(value - center) for value in values)


def frame_budget_ms(manifest: dict[str, Any]) -> tuple[float | None, str]:
    environment = manifest.get("environment") or {}
    refresh = environment.get("display_refresh_rate_hz")
    v_sync = environment.get("v_sync_count")
    target = environment.get("target_frame_rate")
    if isinstance(target, (int, float)) and target > 0:
        return 1000.0 / target, "target_frame_rate"
    if isinstance(refresh, (int, float)) and refresh > 0 and isinstance(v_sync, int) and v_sync > 0:
        return 1000.0 * v_sync / refresh, "display_refresh_rate_hz_and_v_sync_count"
    return None, "unavailable"


def incident_thresholds(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    values = [float(row["frame_time_ms"]) for row in rows if _number(row.get("frame_time_ms"))]
    median = statistics.median(values) if values else None
    mad = median_absolute_deviation(values)
    budget, budget_source = frame_budget_ms(manifest)
    adaptive = None
    budget_threshold = None
    if median is not None:
        adaptive = median + max(
            DIAGNOSTIC_THRESHOLDS["adaptive_mad_multiplier"] * mad,
            DIAGNOSTIC_THRESHOLDS["adaptive_minimum_margin_ms"],
        )
    if budget is not None:
        budget_threshold = budget * DIAGNOSTIC_THRESHOLDS["frame_budget_multiplier"]
    budget_miss_adaptive = None
    budget_miss_budget = None
    if median is not None:
        budget_miss_adaptive = median + max(
            DIAGNOSTIC_THRESHOLDS["budget_miss_mad_multiplier"] * mad,
            DIAGNOSTIC_THRESHOLDS["budget_miss_minimum_margin_ms"],
        )
    if budget is not None:
        budget_miss_budget = budget * DIAGNOSTIC_THRESHOLDS["budget_miss_frame_budget_multiplier"]
    budget_miss_threshold = max(
        value for value in (budget_miss_adaptive, budget_miss_budget) if value is not None
    ) if budget_miss_adaptive is not None or budget_miss_budget is not None else DIAGNOSTIC_THRESHOLDS["hitch_frame_time_ms"]
    distribution_threshold = max(
        value for value in (adaptive, budget_threshold) if value is not None
    ) if adaptive is not None or budget_threshold is not None else None
    absolute = DIAGNOSTIC_THRESHOLDS["absolute_long_frame_ms"]
    effective = min(distribution_threshold, absolute) if distribution_threshold is not None else absolute
    allocation_values = [float(row["gc_allocated_bytes"]) for row in rows if _number(row.get("gc_allocated_bytes"))]
    allocation_median = statistics.median(allocation_values) if allocation_values else None
    allocation_mad = median_absolute_deviation(allocation_values)
    allocation_threshold = (
        allocation_median + max(
            DIAGNOSTIC_THRESHOLDS["allocation_mad_multiplier"] * allocation_mad,
            DIAGNOSTIC_THRESHOLDS["allocation_minimum_margin_bytes"],
        )
        if allocation_median is not None else None
    )
    return {
        **DIAGNOSTIC_THRESHOLDS,
        "steady_median_frame_time_ms": median,
        "steady_mad_frame_time_ms": mad,
        "frame_budget_ms": budget,
        "frame_budget_source": budget_source,
        "adaptive_threshold_ms": adaptive,
        "frame_budget_threshold_ms": budget_threshold,
        "effective_incident_frame_threshold_ms": effective,
        "budget_miss_adaptive_threshold_ms": budget_miss_adaptive,
        "budget_miss_frame_budget_threshold_ms": budget_miss_budget,
        "budget_miss_threshold_ms": budget_miss_threshold,
        "allocation_median_bytes": allocation_median,
        "allocation_mad_bytes": allocation_mad if allocation_values else None,
        "allocation_spike_threshold_bytes": allocation_threshold,
        "rule": "budget miss >= max(median + max(4*MAD, 1ms), frame_budget*1.25); hitch >= 33.33ms; severe hitch >= 50ms",
    }


def detect_incidents(
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
    gc_window_rows: list[dict[str, Any]],
    quality_flags: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    thresholds = incident_thresholds(rows, manifest)
    threshold = thresholds["budget_miss_threshold_ms"]
    candidate_positions = [
        index for index, row in enumerate(rows)
        if isinstance(row.get("frame_time_ms"), (int, float))
        and row["frame_time_ms"] >= threshold
    ]
    groups: list[tuple[int, int]] = []
    for position in candidate_positions:
        if not groups:
            groups.append((position, position))
            continue
        start, end = groups[-1]
        previous_timestamp = rows[end].get("timestamp_ms")
        current_timestamp = rows[position].get("timestamp_ms")
        time_gap = (
            current_timestamp - previous_timestamp
            if isinstance(previous_timestamp, (int, float)) and isinstance(current_timestamp, (int, float))
            else 0.0
        )
        if (
            position - end - 1 <= thresholds["merge_normal_gap_frames"]
            and time_gap <= thresholds["merge_max_gap_ms"]
        ):
            groups[-1] = (start, position)
        else:
            groups.append((position, position))
    incidents = [
        _build_incident(
            number, start, end, rows, manifest, gc_window_rows,
            quality_flags, thresholds,
        )
        for number, (start, end) in enumerate(groups, start=1)
    ]
    return incidents, thresholds


def _build_incident(
    number: int,
    start: int,
    end: int,
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
    gc_window_rows: list[dict[str, Any]],
    quality_flags: list[str],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    segment = rows[start : end + 1]
    frame_values = [float(row["frame_time_ms"]) for row in segment]
    baseline = thresholds["steady_median_frame_time_ms"] or 0.0
    worst_offset = max(range(len(segment)), key=lambda index: frame_values[index])
    worst = segment[worst_offset]
    start_timestamp = _value(segment[0], "timestamp_ms")
    end_timestamp = _value(segment[-1], "timestamp_ms")
    duration = None
    if start_timestamp is not None and end_timestamp is not None:
        duration = end_timestamp - start_timestamp + frame_values[-1]
    start_frame = _frame_index(segment[0], start)
    end_frame = _frame_index(segment[-1], end)
    overlapping_gc = [
        window for window in gc_window_rows
        if window["end_frame_index"] >= start_frame
        and window["start_frame_index"] <= end_frame
    ]
    diagnosis = classify_incident(segment, manifest, overlapping_gc, quality_flags, thresholds)
    peak = max(frame_values)
    event_type, event_severity, priority, actionable = event_metadata(
        peak, diagnosis["classification"], len(segment), thresholds
    )
    context = int(
        thresholds["actionable_evidence_context_frames"]
        if actionable else thresholds["non_actionable_evidence_context_frames"]
    )
    evidence_start = max(0, start - context)
    evidence_end = min(len(rows) - 1, end + context)
    incident_id = f"incident-{number:04d}"
    return {
        "event_id": incident_id,
        "incident_id": incident_id,
        "event_type": event_type,
        "severity": event_severity,
        "priority": priority,
        "is_actionable": actionable,
        "start_frame_index": start_frame,
        "end_frame_index": end_frame,
        "start_timestamp_ms": start_timestamp,
        "end_timestamp_ms": end_timestamp,
        "frame_count": end - start + 1,
        "duration_frames": end - start + 1,
        "duration_ms": duration,
        "slow_frame_count": sum(value >= thresholds["budget_miss_threshold_ms"] for value in frame_values),
        "peak_frame_time_ms": peak,
        "max_frame_time_ms": peak,
        "mean_frame_time_ms": statistics.fmean(frame_values),
        "cumulative_frame_time_ms": sum(frame_values),
        "excess_time_over_steady_baseline_ms": sum(max(value - baseline, 0.0) for value in frame_values),
        "classification": diagnosis["classification"],
        "confidence": diagnosis["confidence"],
        "evidence": diagnosis["evidence"],
        "counter_evidence": diagnosis["counter_evidence"],
        "limitations": diagnosis["limitations"],
        "worst_frame": _frame_evidence(worst, start + worst_offset),
        "resource_evidence": _resource_evidence(
            rows, start, end, worst, overlapping_gc, thresholds
        ),
        "evidence_window": {
            "start_frame_index": _frame_index(rows[evidence_start], evidence_start),
            "end_frame_index": _frame_index(rows[evidence_end], evidence_end),
            "frames": [_frame_evidence(row, index) for index, row in enumerate(rows[evidence_start:evidence_end + 1], start=evidence_start)],
        },
        "overlapping_gc_window_indexes": [window["window_index"] for window in overlapping_gc],
        "diagnosis": diagnosis,
        "raw_csv_rows": {"start": start + 2, "end": end + 2},
    }


def severity(max_frame_ms: float, thresholds: dict[str, Any]) -> str:
    if max_frame_ms >= thresholds["absolute_long_frame_ms"]:
        return "severe"
    if max_frame_ms >= thresholds["hitch_frame_time_ms"]:
        return "major"
    return "minor"


def event_metadata(
    peak_frame_ms: float,
    classification: str,
    frame_count: int,
    thresholds: dict[str, Any],
) -> tuple[str, str, str, bool]:
    if peak_frame_ms >= thresholds["absolute_long_frame_ms"]:
        return "severe_hitch", "severe", "P0", True
    if peak_frame_ms >= thresholds["hitch_frame_time_ms"]:
        return "hitch", "major", "P1", True
    if classification == "frame_pacing" and frame_count >= thresholds["pacing_state_minimum_frames"]:
        return "pacing_state", "info", "P3", False
    return "budget_miss", "minor", "P3", False


def classify_incident(
    segment: list[dict[str, Any]],
    manifest: dict[str, Any],
    overlapping_gc: list[dict[str, Any]],
    quality_flags: list[str],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    counter: list[dict[str, Any]] = []
    limitations = [
        "Deterministic heuristic classification; not a call-stack conclusion.",
        "Profiler markers can overlap and are not summed as CPU total time.",
        "No process CPU utilization or system GPU utilization is available.",
    ]
    frame_max = _max(segment, "frame_time_ms")
    worst_row = max(
        segment,
        key=lambda row: row.get("frame_time_ms")
        if isinstance(row.get("frame_time_ms"), (int, float)) else float("-inf"),
    )
    cpu_max = _value(worst_row, "cpu_frame_time_ms")
    gpu_max = _value(worst_row, "gpu_frame_time_ms")
    wait_max = _max(segment, "wait_for_target_fps_ms")
    marker_peaks = {marker: _max(segment, marker) for marker in INCIDENT_MARKERS}
    available_peaks = {key: value for key, value in marker_peaks.items() if value is not None}
    top_markers = sorted(available_peaks.items(), key=lambda item: item[1], reverse=True)[:5]
    minimum_share = thresholds["attribution_minimum_frame_share"]
    cpu_dominates_gpu = (
        cpu_max is not None and gpu_max is not None
        and cpu_max > gpu_max * thresholds["cpu_over_gpu_ratio"]
    )
    gpu_near_or_above_cpu = (
        cpu_max is not None and gpu_max is not None
        and gpu_max >= cpu_max * thresholds["gpu_near_cpu_ratio"]
    )
    cpu_explains_frame = (
        cpu_max is not None and frame_max is not None
        and cpu_max >= frame_max * minimum_share
    )
    gpu_explains_frame = (
        gpu_max is not None and frame_max is not None
        and gpu_max >= frame_max * minimum_share
    )
    if overlapping_gc:
        total = sum(window["marker_total_ms"] for window in overlapping_gc)
        evidence.append({"signal": "gc_window_overlap", "value": len(overlapping_gc), "detail": f"GC marker total {total:.4f} ms"})
        classification, confidence = "gc_participating", "high"
        if frame_max and total < frame_max * 0.25:
            counter.append({"signal": "gc_share_of_worst_frame", "value": total / frame_max, "detail": "GC participates but does not explain most frame time."})
    elif _pacing_wait_dominates(worst_row, manifest, thresholds):
        evidence.append({"signal": "wait_for_target_fps_ms.max", "value": wait_max, "detail": "Frame is near configured refresh budget and contains substantial pacing wait."})
        classification, confidence = "frame_pacing", "high"
        counter.append({"signal": "not_compute_bottleneck", "value": True, "detail": "Waiting time is not evidence that scripts or GPU computation became slower."})
    elif cpu_explains_frame and gpu_explains_frame and gpu_near_or_above_cpu:
        evidence.append({"signal": "cpu_gpu_at_worst_frame", "value": {"cpu_ms": cpu_max, "gpu_ms": gpu_max}, "detail": "CPU and GPU timings both explain most of the worst frame and are close enough that a single side is not dominant."})
        classification, confidence = "mixed", "medium"
    elif cpu_dominates_gpu and cpu_explains_frame:
        evidence.append({"signal": "cpu_vs_gpu_at_worst_frame", "value": {"cpu_ms": cpu_max, "gpu_ms": gpu_max}, "detail": "On the worst frame, CPU timing is materially above GPU timing and explains enough of that frame."})
        classification, confidence = "cpu_bound_candidate", "medium"
        if top_markers:
            evidence.append({"signal": "marker_peaks_ms", "value": dict(top_markers), "detail": "Available marker peaks; incomplete coverage of main-thread work."})
        if not top_markers or top_markers[0][1] < cpu_max * 0.25:
            limitations.append("Known markers explain only a small part of CPU timing; main-thread interval remains unattributed.")
    elif gpu_near_or_above_cpu and gpu_explains_frame:
        evidence.append({"signal": "gpu_vs_cpu_at_worst_frame", "value": {"cpu_ms": cpu_max, "gpu_ms": gpu_max}, "detail": "On the worst frame, GPU timing is close to or above CPU timing and explains enough of that frame."})
        classification, confidence = "gpu_bound_candidate", "medium"
        counter.append({"signal": "system_gpu_utilization", "value": None, "detail": "System GPU utilization is not collected."})
    else:
        classification, confidence = "unattributed", "low"
        evidence.append({"signal": "available_timing", "value": {"frame_ms": frame_max, "cpu_ms": cpu_max, "gpu_ms": gpu_max}, "detail": "Available columns do not support a deterministic cause."})
        if cpu_dominates_gpu and not cpu_explains_frame:
            counter.append({
                "signal": "cpu_share_of_frame",
                "value": cpu_max / frame_max if frame_max else None,
                "detail": "CPU timing exceeds GPU timing but is too small to explain the measured long frame conservatively.",
            })
    known_times = [value for value in (cpu_max, gpu_max) if value is not None]
    unexplained = max(frame_max - max(known_times), 0.0) if frame_max is not None and known_times else None
    evidence.append({
        "signal": "unexplained_frame_time_ms",
        "value": unexplained,
        "detail": "Diagnostic hint computed as frame_time - max(CPU, GPU); CPU/GPU timings are not a strict additive decomposition.",
    })
    if unexplained is not None and frame_max and unexplained >= frame_max * 0.25:
        limitations.append("A material part of the frame is unexplained; possible wait, scheduling, IO, synchronization, or unavailable marker evidence.")
        if gpu_near_or_above_cpu and not gpu_explains_frame:
            counter.append({
                "signal": "gpu_share_of_frame",
                "value": gpu_max / frame_max if frame_max else None,
                "detail": "GPU timing is near or above CPU timing but is too small to explain the measured long frame conservatively.",
            })
    if "gc_allocated_in_frame_includes_collector_overhead" in quality_flags:
        limitations.append("gc_allocated_bytes includes Collector overhead and is only suitable for same-version trends/spikes.")
    if "profiler_markers_include_collector_overhead" in quality_flags:
        limitations.append("Profiler markers include Collector execution and synchronous CSV writing overhead.")
    missing = [name for name, value in (("cpu_frame_time_ms", cpu_max), ("gpu_frame_time_ms", gpu_max)) if value is None]
    if missing:
        limitations.append("Missing worst-frame evidence: " + ", ".join(missing))
    if classification != "gc_participating":
        counter.append({"signal": "gc_window_overlap", "value": False, "detail": "No GC work window overlaps this incident."})
    return {
        "classification": classification,
        "confidence": confidence,
        "evidence": evidence,
        "counter_evidence": counter,
        "limitations": limitations,
    }


def allocation_diagnostics(rows: list[dict[str, Any]], quality_flags: list[str]) -> dict[str, Any]:
    values = [float(row["gc_allocated_bytes"]) for row in rows if _number(row.get("gc_allocated_bytes"))]
    if not values:
        return {"status": "insufficient_data", "threshold_bytes": None, "spikes": [], "limitations": ["gc_allocated_bytes is unavailable."]}
    median = statistics.median(values)
    mad = median_absolute_deviation(values)
    threshold = median + max(
        DIAGNOSTIC_THRESHOLDS["allocation_mad_multiplier"] * mad,
        DIAGNOSTIC_THRESHOLDS["allocation_minimum_margin_bytes"],
    )
    spikes = [
        {"frame_index": _frame_index(row, index), "timestamp_ms": row.get("timestamp_ms"), "allocated_bytes": row.get("gc_allocated_bytes"), "csv_row_number": index + 2}
        for index, row in enumerate(rows)
        if isinstance(row.get("gc_allocated_bytes"), (int, float)) and row["gc_allocated_bytes"] >= threshold
    ]
    limitations = []
    if "gc_allocated_in_frame_includes_collector_overhead" in quality_flags:
        limitations.append("Values include Collector overhead; use for same-version relative spikes only.")
    return {"status": "available", "median_bytes": median, "mad_bytes": mad, "threshold_bytes": threshold, "spike_count": len(spikes), "spikes": spikes, "limitations": limitations}


def _resource_evidence(
    rows: list[dict[str, Any]],
    start: int,
    end: int,
    worst: dict[str, Any],
    overlapping_gc: list[dict[str, Any]],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    segment = rows[start:end + 1]
    marker_peaks = {
        marker: _max(segment, marker)
        for marker in INCIDENT_MARKERS
        if _max(segment, marker) is not None
    }
    allocations = [
        float(row["gc_allocated_bytes"])
        for row in segment if _number(row.get("gc_allocated_bytes"))
    ]
    allocation_peak = max(allocations) if allocations else None
    allocation_threshold = thresholds.get("allocation_spike_threshold_bytes")
    before = max(0, start - 1)
    after = min(len(rows) - 1, end + 1)
    memory = {}
    for metric in (
        "memory_used_bytes", "unity_reserved_bytes", "unity_unused_reserved_bytes",
        "gc_used_bytes", "gc_reserved_bytes",
    ):
        first = rows[before].get(metric)
        last = rows[after].get(metric)
        memory[metric] = {
            "before": first if _number(first) else None,
            "after": last if _number(last) else None,
            "delta_bytes": last - first if _number(first) and _number(last) else None,
        }
    return {
        "worst_frame_synchronized_timing_ms": {
            "frame": worst.get("frame_time_ms"),
            "cpu": worst.get("cpu_frame_time_ms"),
            "gpu": worst.get("gpu_frame_time_ms"),
            "wait_for_target_fps": worst.get("wait_for_target_fps_ms"),
            "unexplained": _unexplained_frame_time(worst),
        },
        "marker_peaks_ms": marker_peaks,
        "allocation": {
            "peak_bytes": allocation_peak,
            "threshold_bytes": allocation_threshold,
            "is_spike": (
                allocation_peak >= allocation_threshold
                if allocation_peak is not None and isinstance(allocation_threshold, (int, float))
                else None
            ),
            "limitation": "gc_allocated_bytes may include Collector overhead where declared.",
        },
        "memory_context": {
            "before_frame_index": _frame_index(rows[before], before),
            "after_frame_index": _frame_index(rows[after], after),
            "metrics": memory,
        },
        "gc_windows": [
            {
                "window_index": window.get("window_index"),
                "marker_total_ms": window.get("marker_total_ms"),
                "generation_collection_counts": window.get("generation_collection_counts"),
                "gc_used_bytes_delta": window.get("gc_used_bytes_delta"),
            }
            for window in overlapping_gc
        ],
    }


def memory_diagnostics(rows: list[dict[str, Any]], existing_trends: dict[str, Any]) -> dict[str, Any]:
    duration_seconds = float(rows[-1].get("timestamp_ms") or 0) / 1000 if rows else 0.0
    metrics: dict[str, Any] = {}
    for metric, trend in existing_trends.items():
        if trend is None:
            metrics[metric] = None
            continue
        window_slopes = _rolling_slopes(rows, metric, int(DIAGNOSTIC_THRESHOLDS["memory_rolling_window_frames"]))
        steps = _step_candidates(rows, metric)
        metrics[metric] = {
            **trend,
            "robust_median_window_slope_bytes_per_second": statistics.median(window_slopes) if window_slopes else None,
            "window_slope_count": len(window_slopes),
            "step_growth_candidates": steps,
        }
    if duration_seconds < DIAGNOSTIC_THRESHOLDS["memory_minimum_duration_seconds"]:
        status = "insufficient_duration"
        explanation = "Measurement is too short for a memory leak or sustained-growth conclusion."
    else:
        primary = metrics.get("memory_used_bytes") or {}
        slope = primary.get("robust_median_window_slope_bytes_per_second")
        if isinstance(slope, (int, float)) and slope > DIAGNOSTIC_THRESHOLDS["memory_growth_slope_bytes_per_second"]:
            status = "growth_candidate"
            explanation = "Sustained positive robust slope exceeds the experimental threshold; this is a candidate, not proof of a leak."
        else:
            status = "stable"
            explanation = "No sustained growth above the experimental threshold was observed."
    return {
        "status": status,
        "measurement_duration_seconds": duration_seconds,
        "minimum_duration_seconds": DIAGNOSTIC_THRESHOLDS["memory_minimum_duration_seconds"],
        "explanation": explanation,
        "metrics": metrics,
        "limitations": ["Memory leak claims require longer scenario-controlled runs and repetition.", "Unity allocated memory is not process working set."],
    }


def gc_diagnostics(windows: list[dict[str, Any]], incidents: list[dict[str, Any]]) -> dict[str, Any]:
    incident_by_window: dict[int, list[str]] = {}
    for incident in incidents:
        for index in incident["overlapping_gc_window_indexes"]:
            incident_by_window.setdefault(index, []).append(incident["incident_id"])
    enriched = []
    for window in windows:
        incident_ids = incident_by_window.get(window["window_index"], [])
        enriched.append({**window, "overlapping_incident_ids": incident_ids})
    return {
        "window_count": len(windows),
        "total_marker_ms": sum(window["marker_total_ms"] for window in windows),
        "max_window_marker_ms": max((window["marker_total_ms"] for window in windows), default=0.0),
        "incident_participation_count": sum(bool(item["overlapping_incident_ids"]) for item in enriched),
        "windows": enriched,
    }


def build_activity_events(
    windows: list[dict[str, Any]],
    memory: dict[str, Any],
    measurement_coverage_ms: float | int | None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for number, window in enumerate(windows, start=1):
        event_id = f"gc-activity-{number:04d}"
        events.append({
            "event_id": event_id,
            "event_type": "gc_activity",
            "severity": "info",
            "priority": "P3",
            "is_actionable": False,
            "start_frame_index": window.get("start_frame_index"),
            "end_frame_index": window.get("end_frame_index"),
            "start_timestamp_ms": window.get("start_timestamp_ms"),
            "end_timestamp_ms": window.get("end_timestamp_ms"),
            "duration_ms": window.get("marker_total_ms"),
            "frame_count": window.get("duration_frames"),
            "peak_frame_time_ms": None,
            "classification": "gc_participating",
            "confidence": "high",
            "evidence": [{
                "signal": "gc_work_window",
                "value": {
                    "marker_total_ms": window.get("marker_total_ms"),
                    "generation_collection_counts": window.get("generation_collection_counts"),
                    "overlapping_event_ids": window.get("overlapping_incident_ids", []),
                },
                "detail": "GC work activity; overlap can participate in a hitch but is not declared the root cause.",
            }],
            "counter_evidence": [],
            "limitations": ["GC activity alone is not a performance fault or root-cause conclusion."],
            "raw_csv_rows": {
                "start": (window.get("start_frame_index") + 2) if isinstance(window.get("start_frame_index"), int) else None,
                "end": (window.get("end_frame_index") + 2) if isinstance(window.get("end_frame_index"), int) else None,
            },
        })
    if memory.get("status") == "growth_candidate":
        primary = (memory.get("metrics") or {}).get("memory_used_bytes") or {}
        events.append({
            "event_id": "memory-anomaly-0001",
            "event_type": "memory_anomaly",
            "severity": "major",
            "priority": "P1",
            "is_actionable": True,
            "start_frame_index": 0,
            "end_frame_index": None,
            "start_timestamp_ms": 0,
            "end_timestamp_ms": measurement_coverage_ms,
            "duration_ms": measurement_coverage_ms,
            "frame_count": None,
            "peak_frame_time_ms": None,
            "classification": "unattributed",
            "confidence": "medium",
            "evidence": [{
                "signal": "memory_growth_candidate",
                "value": primary,
                "detail": memory.get("explanation"),
            }],
            "counter_evidence": [],
            "limitations": list(memory.get("limitations") or []),
            "raw_csv_rows": {"start": 2, "end": None},
        })
    return events


def summarize_events(
    events: list[dict[str, Any]],
    gc_window_count: int,
    memory_status: str,
    frame_count: int,
    quality: dict[str, Any],
) -> dict[str, Any]:
    frame_events = [event for event in events if event["event_type"] not in ("gc_activity", "memory_anomaly")]
    actionable = [event for event in events if event.get("is_actionable")]
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    top = sorted(
        actionable,
        key=lambda event: (
            priority_rank.get(event.get("priority"), 9),
            -(event.get("peak_frame_time_ms") or 0),
            -(event.get("duration_ms") or 0),
            -confidence_rank.get(event.get("confidence"), 0),
        ),
    )[: int(DIAGNOSTIC_THRESHOLDS["top_issue_limit"])]
    budget_events = [event for event in frame_events if event["event_type"] == "budget_miss"]
    pacing_events = [event for event in frame_events if event["event_type"] == "pacing_state"]
    severity_counts = {
        level: sum(event.get("severity") == level for event in frame_events)
        for level in ("severe", "major", "minor", "info")
    }
    summary = {
        "incident_count": len(frame_events),
        "performance_event_count": len(frame_events),
        "actionable_issue_count": len(actionable),
        "severe_hitch_count": sum(event["event_type"] == "severe_hitch" for event in events),
        "hitch_count": sum(event["event_type"] == "hitch" for event in events),
        "budget_miss_count": len(budget_events),
        "budget_miss_frame_count": sum(event.get("slow_frame_count") or 0 for event in budget_events),
        "budget_miss_frame_ratio": (
            sum(event.get("slow_frame_count") or 0 for event in budget_events) / frame_count
            if frame_count else 0.0
        ),
        "longest_budget_miss_frame_count": max((event.get("frame_count") or 0 for event in budget_events), default=0),
        "pacing_state_count": len(pacing_events),
        "pacing_state_frame_count": sum(event.get("frame_count") or 0 for event in pacing_events),
        "gc_window_count": gc_window_count,
        "memory_status": memory_status,
        "unattributed_actionable_count": sum(
            event.get("classification") == "unattributed" for event in actionable
        ),
        "top_issue_ids": [event["event_id"] for event in top],
        "severity_counts": severity_counts,
    }
    if quality.get("status") != "valid":
        conclusion = "Data quality is not fully valid; diagnostic conclusions are limited."
    elif actionable:
        conclusion = (
            f"{len(actionable)} actionable issue(s): "
            f"{summary['severe_hitch_count']} severe hitch(es), "
            f"{summary['hitch_count']} hitch(es); pacing and budget misses are reported separately."
        )
    else:
        conclusion = "No actionable hitch or memory anomaly was detected; pacing and budget misses are reported separately."
    summary["overall_conclusion"] = conclusion
    return summary


def chart_data(
    rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    max_buckets = int(DIAGNOSTIC_THRESHOLDS["chart_max_buckets"])
    bucket_size = max(1, math.ceil(len(rows) / max_buckets))
    fields = (
        "frame_time_ms", "cpu_frame_time_ms", "gpu_frame_time_ms",
        "gc_collect_ms", "gc_allocated_bytes", "memory_used_bytes",
        "unity_reserved_bytes", "gc_used_bytes", "gc_reserved_bytes",
        "wait_for_target_fps_ms",
    )
    buckets: list[dict[str, Any]] = []
    for start in range(0, len(rows), bucket_size):
        subset = rows[start:start + bucket_size]
        item: dict[str, Any] = {
            "start_timestamp_ms": subset[0].get("timestamp_ms"),
            "end_timestamp_ms": subset[-1].get("timestamp_ms"),
            "source_frame_count": len(subset),
        }
        for field in fields:
            values = [float(row[field]) for row in subset if _number(row.get(field))]
            item[f"{field}_min"] = min(values) if values else None
            item[f"{field}_mean"] = statistics.fmean(values) if values else None
            item[f"{field}_max"] = max(values) if values else None
        buckets.append(item)
    context = int(DIAGNOSTIC_THRESHOLDS["chart_preserved_context_frames"])
    preserved_indexes: set[int] = set()
    for event in events:
        if not event.get("is_actionable"):
            continue
        start = event.get("start_frame_index")
        end = event.get("end_frame_index")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        preserved_indexes.update(range(max(0, start - context), min(len(rows), end + context + 1)))
    preserved = [
        {
            "frame_index": _frame_index(rows[index], index),
            "timestamp_ms": rows[index].get("timestamp_ms"),
            **{field: rows[index].get(field) for field in fields},
            "csv_row_number": index + 2,
        }
        for index in sorted(preserved_indexes)
    ]
    return {
        "source_point_count": len(rows),
        "bucket_size_frames": bucket_size,
        "bucket_count": len(buckets),
        "downsampled": bucket_size > 1,
        "buckets": buckets,
        "preserved_actionable_points": preserved,
    }


def timeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = (
        "frame_index", "timestamp_ms", "frame_time_ms", "cpu_frame_time_ms",
        "gpu_frame_time_ms", "gc_collect_ms", "gc_allocated_bytes",
        "memory_used_bytes", "unity_reserved_bytes", "unity_unused_reserved_bytes",
        "gc_used_bytes", "gc_reserved_bytes",
        "wait_for_target_fps_ms",
    )
    return [{field: row.get(field) for field in fields} | {"csv_row_number": index + 2} for index, row in enumerate(rows)]


def _pacing_wait_dominates(row: dict[str, Any], manifest: dict[str, Any], thresholds: dict[str, Any]) -> bool:
    budget = thresholds.get("frame_budget_ms")
    if not isinstance(budget, (int, float)):
        return False
    frame = row.get("frame_time_ms")
    wait = row.get("wait_for_target_fps_ms")
    return bool(
        isinstance(frame, (int, float)) and isinstance(wait, (int, float))
        and wait >= frame * thresholds["pacing_wait_minimum_frame_share"]
        and budget * thresholds["pacing_frame_budget_minimum_multiplier"] <= frame
        <= budget * thresholds["pacing_frame_budget_maximum_multiplier"]
    )


def _rolling_slopes(rows: list[dict[str, Any]], metric: str, window: int) -> list[float]:
    points = [(row.get("timestamp_ms"), row.get(metric)) for row in rows]
    slopes = []
    for start in range(0, max(0, len(points) - window), max(1, window // 2)):
        subset = [(t, v) for t, v in points[start:start + window] if isinstance(t, (int, float)) and isinstance(v, (int, float))]
        if len(subset) < 2 or subset[-1][0] <= subset[0][0]:
            continue
        slopes.append((subset[-1][1] - subset[0][1]) / ((subset[-1][0] - subset[0][0]) / 1000.0))
    return slopes


def _step_candidates(rows: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    deltas = []
    for index in range(1, len(rows)):
        before, after = rows[index - 1].get(metric), rows[index].get(metric)
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            deltas.append((index, after - before))
    positive = [delta for _, delta in deltas if delta > 0]
    mad = median_absolute_deviation(positive)
    threshold = max(DIAGNOSTIC_THRESHOLDS["memory_step_minimum_bytes"], DIAGNOSTIC_THRESHOLDS["memory_step_mad_multiplier"] * mad)
    return [
        {"frame_index": _frame_index(rows[index], index), "timestamp_ms": rows[index].get("timestamp_ms"), "delta_bytes": delta, "csv_row_number": index + 2}
        for index, delta in deltas if delta >= threshold
    ]


def _frame_evidence(row: dict[str, Any], position: int) -> dict[str, Any]:
    fields = (
        "timestamp_ms", "frame_time_ms", "cpu_frame_time_ms", "gpu_frame_time_ms",
        "gc_collect_ms", "gc_allocated_bytes", "memory_used_bytes",
        "unity_reserved_bytes", "unity_unused_reserved_bytes", "gc_used_bytes", "gc_reserved_bytes",
        "wait_for_target_fps_ms", "script_update_ms", "script_fixed_update_ms",
        "script_late_update_ms", "physics_simulate_ms", "ui_build_batch_ms",
        "gfx_wait_for_present_ms", "job_wait_ms",
    )
    return {"frame_index": _frame_index(row, position), "csv_row_number": position + 2} | {field: row.get(field) for field in fields}


def _max(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if isinstance(row.get(field), (int, float))]
    return max(values) if values else None


def _value(row: dict[str, Any], field: str) -> float | None:
    value = row.get(field)
    return float(value) if isinstance(value, (int, float)) else None


def _unexplained_frame_time(row: dict[str, Any]) -> float | None:
    frame = _value(row, "frame_time_ms")
    known = [
        value for value in (
            _value(row, "cpu_frame_time_ms"),
            _value(row, "gpu_frame_time_ms"),
        )
        if value is not None
    ]
    return max(frame - max(known), 0.0) if frame is not None and known else None


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _frame_index(row: dict[str, Any], fallback: int) -> int:
    value = row.get("frame_index")
    return int(value) if isinstance(value, (int, float)) else fallback
