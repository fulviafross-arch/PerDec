from __future__ import annotations

import copy
import html
import json
from pathlib import Path
from typing import Any


def write_single_run_report(
    analysis: dict[str, Any], output_root: Path, selection: dict[str, Any]
) -> tuple[Path, Path, dict[str, Any]]:
    result = copy.deepcopy(analysis)
    result["selection"] = selection
    run_output = output_root / "runs" / str(result["run_id"])
    run_output.mkdir(parents=True, exist_ok=True)
    json_path, html_path = run_output / "analysis.json", run_output / "analysis.html"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_single_run_html(result), encoding="utf-8")
    return json_path.resolve(), html_path.resolve(), result


def render_single_run_html(result: dict[str, Any]) -> str:
    run = result.get("run_summary") or {}
    environment = run.get("environment") or {}
    summary = result.get("diagnostic_summary") or {}
    quality = result.get("quality") or {}
    events = result.get("events") or result.get("incidents") or []
    chart = result.get("chart_data") or _legacy_chart_data(result.get("timeline") or [])
    gc = result.get("gc_diagnostics") or {}
    allocations = result.get("allocation_diagnostics") or {}
    memory = result.get("memory_diagnostics") or {}
    thresholds = result.get("diagnostic_thresholds") or {}
    external = result.get("external_process_monitor") or {"availability": "unavailable"}
    external_available = external.get("availability") == "available"
    by_id = {_event_id(event): event for event in events}
    top_issues = [by_id[event_id] for event_id in summary.get("top_issue_ids") or [] if event_id in by_id]
    frame_events = [event for event in events if event.get("event_type") not in ("gc_activity", "memory_anomaly")]
    pacing_events = [event for event in events if event.get("event_type") == "pacing_state"]
    actionable = [event for event in events if event.get("is_actionable")]

    styles = """
    :root{color-scheme:dark;--bg:#0b1020;--panel:#151c30;--line:#27324d;--text:#e8edf7;--muted:#9aa8c2;--accent:#68d5ff;--warn:#ffcc66;--bad:#ff6b7a;--good:#63d297}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,Segoe UI,sans-serif}main{max-width:1280px;margin:auto;padding:28px}h1,h2,h3{margin:.4em 0}.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:12px}.card,section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin:14px 0}.value{font-size:24px;font-weight:700}.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}table{width:100%;border-collapse:collapse}th,td{padding:7px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}code{color:var(--accent);word-break:break-all}svg{width:100%;height:auto;background:#0e1528;border-radius:8px}.legend span{display:inline-block;margin-right:16px}.dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:5px}details{border-top:1px solid var(--line);padding:10px 0}summary{cursor:pointer;font-weight:600}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 8px;margin:2px}.event{border-left:4px solid var(--line);padding-left:12px}.event[data-severity='severe']{border-color:var(--bad)}.event[data-severity='major']{border-color:var(--warn)}.filters{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.filters button{border:1px solid var(--line);background:transparent;color:var(--text);padding:5px 9px;border-radius:7px;cursor:pointer}.filters button.active{background:var(--accent);color:var(--bg)}.hidden{display:none}.top-issue{margin:12px 0;padding:12px;border-left:4px solid var(--bad);background:#10182b}.top-issue h3{margin-top:0}
    """
    cards = "".join([
        _card("Actionable issues", summary.get("actionable_issue_count"), "bad" if summary.get("actionable_issue_count") else "good"),
        _card("Severe hitches", summary.get("severe_hitch_count"), "bad" if summary.get("severe_hitch_count") else "good"),
        _card("Major hitches", summary.get("hitch_count"), "warn" if summary.get("hitch_count") else "good"),
        _card("Pacing states", summary.get("pacing_state_count")),
        _card("Budget-miss events", summary.get("budget_miss_count")),
        _card("GC windows", summary.get("gc_window_count")),
        _card("Memory", summary.get("memory_status"), "good" if summary.get("memory_status") == "stable" else "warn"),
        _card("External process telemetry", external.get("availability"), "good" if external.get("availability") == "available" else "warn"),
    ])
    top_html = "".join(_top_issue_html(event, external_available) for event in top_issues) or "<p>No actionable issue.</p>"
    frame_rows = "".join(_event_row(event) for event in frame_events) or "<tr><td colspan='9'>No frame-performance event.</td></tr>"
    pacing_rows = "".join(_event_row(event) for event in pacing_events) or "<tr><td colspan='9'>No pacing state.</td></tr>"
    detailed = "".join(_event_html(event, external_available) for event in events) or "<p>No detailed event.</p>"
    gc_rows = "".join(
        f"<tr><td>{_fmt(item.get('window_index'))}</td><td>{_fmt(item.get('start_frame_index'))}–{_fmt(item.get('end_frame_index'))}</td><td>{_fmt(item.get('marker_total_ms'))}</td><td>{_e(item.get('generation_collection_counts') or {})}</td><td>{_fmt(item.get('gc_used_bytes_delta'))}</td><td>{_e(item.get('overlapping_incident_ids') or [])}</td></tr>"
        for item in gc.get("windows") or []
    ) or "<tr><td colspan='6'>No GC work window.</td></tr>"
    memory_rows = "".join(
        f"<tr><td>{_e(name)}</td><td>{_fmt(values.get('start'))}</td><td>{_fmt(values.get('end'))}</td><td>{_fmt(values.get('peak'))}</td><td>{_fmt(values.get('delta'))}</td><td>{_fmt(values.get('robust_median_window_slope_bytes_per_second'))}</td><td>{len(values.get('step_growth_candidates') or [])}</td></tr>"
        for name, values in (memory.get("metrics") or {}).items() if values
    ) or "<tr><td colspan='7'>No memory series is available.</td></tr>"
    flags = "".join(f"<span class='pill'>{_e(flag)}</span>" for flag in quality.get("quality_flags") or []) or "None"
    warnings = "".join(f"<li>{_e(value)}</li>" for value in quality.get("warnings") or []) or "<li>None</li>"
    traceability = "".join(f"<li>{_e(value)}</li>" for value in run.get("traceability_warnings") or []) or "<li>None</li>"
    sources = "".join(f"<tr><th>{_e(key)}</th><td><code>{_e(value)}</code></td></tr>" for key, value in (result.get("source_artifacts") or {}).items())

    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>PerfGuardian {_e(result.get('run_id'))}</title><style>{styles}</style></head><body><main>
    <h1>PerfGuardian single-run diagnostics</h1><p class='muted'>Offline calibrated report. Full-resolution facts remain in frames.csv.</p><div class='grid'>{cards}</div>
    <section><h2>Summary</h2><p>{_e(summary.get('overall_conclusion'))}</p><p><strong>Run:</strong> {_e(result.get('run_id'))}; {_fmt(result.get('frame_count'))} frames / {_fmt(result.get('measurement_coverage_ms'))} ms.</p><p><strong>Budget miss:</strong> {_fmt(thresholds.get('budget_miss_threshold_ms'))} ms; <strong>hitch:</strong> {_fmt(thresholds.get('hitch_frame_time_ms'))} ms; <strong>severe:</strong> {_fmt(thresholds.get('absolute_long_frame_ms'))} ms.</p>{_process_overview_html(external)}<p class='muted'>Charts use {_fmt(chart.get('bucket_count'))} buckets from {_fmt(chart.get('source_point_count'))} frames; actionable neighborhoods retain exact points.</p></section>
    <section><h2>Top Issues</h2>{top_html}</section>
    <section><h2>Frame Performance</h2>{_line_chart(chart,[('frame_time_ms_max','#68d5ff')],actionable,'Frame-time bucket maxima (ms)')}<table><tr><th>Event</th><th>Type</th><th>Priority</th><th>Time ms</th><th>Frames</th><th>Peak ms</th><th>Classification</th><th>Confidence</th><th>CSV rows</th></tr>{frame_rows}</table></section>
    <section><h2>CPU/GPU Diagnosis</h2>{_line_chart(chart,[('cpu_frame_time_ms_mean','#63d297'),('gpu_frame_time_ms_mean','#c18cff')],actionable,'CPU / GPU bucket means (ms)')}</section>
    <section><h2>GC</h2><div class='grid'>{_card('GC windows',gc.get('window_count'))}{_card('GC marker total',f"{_fmt(gc.get('total_marker_ms'))} ms")}{_card('GC windows in events',gc.get('incident_participation_count'))}{_card('Allocation spikes',allocations.get('spike_count'))}</div>{_line_chart(chart,[('gc_collect_ms_max','#ff6b7a')],[],'GC marker bucket maxima (ms)')}<table><tr><th>Index</th><th>Frames</th><th>Marker ms</th><th>Generations</th><th>GC used delta</th><th>Overlapping events</th></tr>{gc_rows}</table></section>
    <section><h2>Memory</h2><p><strong>{_e(memory.get('status'))}</strong> — {_e(memory.get('explanation'))}</p>{_line_chart(chart,[('memory_used_bytes_mean','#68d5ff'),('unity_reserved_bytes_mean','#c18cff'),('gc_used_bytes_mean','#63d297'),('gc_reserved_bytes_mean','#ffcc66')],[],'Unity / GC memory bucket means (bytes)')}<table><tr><th>Metric</th><th>Start</th><th>End</th><th>Peak</th><th>Delta</th><th>Robust slope B/s</th><th>Steps</th></tr>{memory_rows}</table></section>
    <section><h2>Frame Pacing / Waiting States</h2><p class='muted'>Pacing is separate from compute hitches and is not promoted by duration alone.</p><table><tr><th>Event</th><th>Type</th><th>Priority</th><th>Time ms</th><th>Frames</th><th>Peak ms</th><th>Classification</th><th>Confidence</th><th>CSV rows</th></tr>{pacing_rows}</table></section>
    <section><h2>Data Quality</h2><p>Status: <strong>{_e(quality.get('status'))}</strong></p><p>{flags}</p><h3>Warnings</h3><ul>{warnings}</ul><h3>Traceability</h3><ul>{traceability}</ul><ul><li>Process telemetry is sampled window context, not per-frame proof or root-cause attribution.</li><li>No system GPU utilization, call stacks, ETW, or per-thread profiling is collected.</li><li>Unexplained frame time is a diagnostic hint, not an additive decomposition.</li><li>GC overlap means participation, not root cause.</li></ul></section>
    <section><h2>Detailed Events</h2><div class='filters'><button type='button' class='active' data-filter='all'>All</button><button type='button' data-filter='severe'>Severe</button><button type='button' data-filter='major'>Major</button><button type='button' data-filter='budget_miss'>Budget Miss</button><button type='button' data-filter='pacing_state'>Pacing</button><button type='button' data-filter='gc_activity'>GC</button><button type='button' data-filter='unattributed'>Unattributed</button></div>{detailed}</section>
    <section><h2>Run &amp; Raw Artifacts</h2><table><tr><th>Build</th><td>{_e(run.get('build_type'))}</td><th>Unity</th><td>{_e(run.get('unity_version'))}</td></tr><tr><th>Scene</th><td>{_e(run.get('scenario_id'))} / {_e(run.get('active_scene'))}</td><th>Commit</th><td>{_e(run.get('commit_sha'))}</td></tr><tr><th>CPU</th><td>{_e(environment.get('cpu_model'))}</td><th>GPU</th><td>{_e(environment.get('gpu_model'))}</td></tr>{sources}</table></section>
    </main><script>document.querySelectorAll('[data-filter]').forEach(function(button){{button.addEventListener('click',function(){{document.querySelectorAll('[data-filter]').forEach(function(item){{item.classList.remove('active')}});button.classList.add('active');var filter=button.dataset.filter;document.querySelectorAll('.event-detail').forEach(function(item){{var visible=filter==='all'||item.dataset.eventType===filter||item.dataset.severity===filter||item.dataset.classification===filter;item.classList.toggle('hidden',!visible)}})}})}});</script></body></html>"""


def render_collection_html(result: dict[str, Any]) -> str:
    rows = "".join(f"<tr><td>{_e(run.get('run_id'))}</td><td>{_e(run.get('analysis_eligible'))}</td><td>{_fmt(run.get('frame_count'))}</td><td>{_fmt((run.get('diagnostic_summary') or {}).get('actionable_issue_count'))}</td></tr>" for run in result.get("runs") or [])
    return f"<!doctype html><meta charset='utf-8'><title>PerfGuardian collection</title><h1>PerfGuardian collection scan</h1><table><tr><th>Run</th><th>Eligible</th><th>Frames</th><th>Actionable issues</th></tr>{rows}</table>"


def _top_issue_html(event: dict[str, Any], external_available: bool) -> str:
    return f"<article class='top-issue'><h3>{_e(event.get('priority'))} · {_e(event.get('event_type'))} · {_e(_event_id(event))}</h3><p><strong>{_fmt(_peak(event))} ms</strong> at {_fmt(event.get('start_timestamp_ms'))} ms; {_e(_classification(event))} / {_e(_confidence(event))}.</p>{_process_evidence_html(event.get('process_evidence'), external_available)}{_evidence_lists(event)}</article>"


def _event_row(event: dict[str, Any]) -> str:
    raw = event.get("raw_csv_rows") or {}
    return f"<tr><td><a href='#{_e(_event_id(event))}'>{_e(_event_id(event))}</a></td><td>{_e(event.get('event_type'))}</td><td>{_e(event.get('priority'))}</td><td>{_fmt(event.get('start_timestamp_ms'))}–{_fmt(event.get('end_timestamp_ms'))}</td><td>{_fmt(event.get('frame_count') or event.get('duration_frames'))}</td><td>{_fmt(_peak(event))}</td><td>{_e(_classification(event))}</td><td>{_e(_confidence(event))}</td><td>{_fmt(raw.get('start'))}–{_fmt(raw.get('end'))}</td></tr>"


def _event_html(event: dict[str, Any], external_available: bool) -> str:
    event_id, event_type = _event_id(event), event.get("event_type") or "budget_miss"
    severity, classification = event.get("severity") or "minor", _classification(event)
    open_attribute = " open" if event.get("is_actionable") else ""
    frames = (event.get("evidence_window") or {}).get("frames") or []
    frame_rows = "".join(f"<tr><td>{_fmt(row.get('frame_index'))}</td><td>{_fmt(row.get('frame_time_ms'))}</td><td>{_fmt(row.get('cpu_frame_time_ms'))}</td><td>{_fmt(row.get('gpu_frame_time_ms'))}</td><td>{_fmt(row.get('gc_collect_ms'))}</td><td>{_fmt(row.get('wait_for_target_fps_ms'))}</td><td>{_fmt(row.get('csv_row_number'))}</td></tr>" for row in frames)
    context = f"<table><tr><th>Frame</th><th>Frame ms</th><th>CPU ms</th><th>GPU ms</th><th>GC ms</th><th>Wait ms</th><th>CSV row</th></tr>{frame_rows}</table>" if frames else "<p class='muted'>Use the CSV row reference for full-resolution facts.</p>"
    return f"<details id='{_e(event_id)}' class='event event-detail' data-event-type='{_e(event_type)}' data-severity='{_e(severity)}' data-classification='{_e(classification)}'{open_attribute}><summary>{_e(event.get('priority'))} · {_e(event_type)} · {_e(event_id)} · {_fmt(_peak(event))} ms · {_e(classification)}</summary>{_process_evidence_html(event.get('process_evidence'), external_available)}{_evidence_lists(event)}{context}</details>"


def _process_overview_html(external: dict[str, Any]) -> str:
    if external.get("availability") != "available":
        return "<p><strong>External process telemetry:</strong> Unavailable.</p>"
    alignment = external.get("timestamp_alignment") or {}
    return (
        "<p><strong>External process telemetry:</strong> Available; "
        f"{_fmt(external.get('measurement_sample_count'))} measurement samples at about "
        f"{_fmt(external.get('sample_interval_ms'))} ms; alignment={_e(alignment.get('status'))}.</p>"
    )


def _process_evidence_html(value: dict[str, Any] | None, external_available: bool) -> str:
    if not value:
        return "" if external_available else "<h4>Process Evidence</h4><p>External process telemetry unavailable.</p>"
    if value.get("availability") != "available":
        return (
            "<h4>Process Evidence</h4><p>No process samples are available in this incident window.</p>"
            if external_available
            else "<h4>Process Evidence</h4><p>External process telemetry unavailable.</p>"
        )
    cpu = value.get("process_cpu_percent") or {}
    read = value.get("io_read_rate_bytes_per_sec") or {}
    write = value.get("io_write_rate_bytes_per_sec") or {}
    working = value.get("working_set_bytes") or {}
    private = value.get("private_bytes") or {}
    foreground = value.get("foreground_ratio")
    flags = value.get("evidence_flags") or []
    conclusion = (
        "No obvious process-level spike in the sampled window."
        if value.get("no_obvious_process_level_spike")
        else "Overlapping process evidence: " + ", ".join(_e(flag) for flag in flags) + "."
    )
    return (
        "<h4>Process Evidence</h4>"
        f"<p>Samples: {_fmt(value.get('sample_count'))}; process CPU peak: {_fmt(cpu.get('max'))}%; "
        f"IO read/write peak: {_fmt(read.get('max'))} / {_fmt(write.get('max'))} B/s; "
        f"working/private delta: {_fmt(working.get('delta'))} / {_fmt(private.get('delta'))} bytes; "
        f"foreground ratio: {_fmt(foreground)}; minimized: {_e(value.get('minimized_seen'))}.</p>"
        f"<p>{conclusion}</p><p class='muted'>10 Hz window evidence is contextual and does not establish root cause.</p>"
    )


def _evidence_lists(event: dict[str, Any]) -> str:
    diagnosis = event.get("diagnosis") or {}
    evidence_values = event.get("evidence") or diagnosis.get("evidence") or []
    counter_values = event.get("counter_evidence") or diagnosis.get("counter_evidence") or []
    limitation_values = event.get("limitations") or diagnosis.get("limitations") or []
    evidence = "".join(f"<li><code>{_e(value.get('signal'))}</code>: {_e(value.get('detail'))} — {_e(value.get('value'))}</li>" for value in evidence_values) or "<li>None</li>"
    counter = "".join(f"<li><code>{_e(value.get('signal'))}</code>: {_e(value.get('detail'))}</li>" for value in counter_values) or "<li>None</li>"
    limits = "".join(f"<li>{_e(value)}</li>" for value in limitation_values) or "<li>None</li>"
    return f"<h4>Evidence</h4><ul>{evidence}</ul><h4>Counter-evidence</h4><ul>{counter}</ul><h4>Limitations</h4><ul>{limits}</ul>"


def _line_chart(chart: dict[str, Any], series: list[tuple[str, str]], events: list[dict[str, Any]], title: str) -> str:
    width, height, pad = 1000, 260, 42
    points = []
    for bucket in chart.get("buckets") or []:
        start, end = bucket.get("start_timestamp_ms"), bucket.get("end_timestamp_ms")
        if isinstance(start, (int, float)):
            points.append(((start + end) / 2 if isinstance(end, (int, float)) else start, bucket))
    values = [float(row[field]) for _, row in points for field, _ in series if isinstance(row.get(field), (int, float))]
    if not points or not values:
        return f"<p class='muted'>{_e(title)}: insufficient data.</p>"
    min_x, max_x, min_y, max_y = points[0][0], points[-1][0], min(values), max(values)
    if max_y == min_y:
        max_y += 1
    sx = lambda value: pad + (value - min_x) / max(max_x - min_x, 1) * (width - 2 * pad)
    sy = lambda value: height - pad - (value - min_y) / (max_y - min_y) * (height - 2 * pad)
    bands = "".join(f"<rect x='{sx(float(event['start_timestamp_ms'])):.1f}' y='{pad}' width='{max(2,sx(float(event.get('end_timestamp_ms') or event['start_timestamp_ms']))-sx(float(event['start_timestamp_ms']))):.1f}' height='{height-2*pad}' fill='#ff6b7a' opacity='.18'/>" for event in events if isinstance(event.get("start_timestamp_ms"), (int, float)))
    paths, legend = [], []
    for field, color in series:
        chunks, current = [], []
        for x, row in points:
            value = row.get(field)
            if isinstance(value, (int, float)):
                current.append(f"{sx(x):.1f},{sy(float(value)):.1f}")
            elif current:
                chunks.append(current); current = []
        if current:
            chunks.append(current)
        paths.extend(f"<polyline points='{' '.join(chunk)}' fill='none' stroke='{color}' stroke-width='1.5'/>" for chunk in chunks)
        legend.append(f"<span><i class='dot' style='background:{color}'></i>{_e(field)}</span>")
    return f"<h3>{_e(title)}</h3><div class='legend'>{''.join(legend)}</div><svg viewBox='0 0 {width} {height}' role='img'><text x='{pad}' y='20' fill='#9aa8c2'>{max_y:.3f}</text><text x='{pad}' y='{height-10}' fill='#9aa8c2'>{min_y:.3f}</text>{bands}{''.join(paths)}</svg>"


def _legacy_chart_data(timeline: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = []
    for row in timeline:
        bucket = {"start_timestamp_ms": row.get("timestamp_ms"), "end_timestamp_ms": row.get("timestamp_ms")}
        for field, value in row.items():
            if field not in ("timestamp_ms", "frame_index", "csv_row_number"):
                bucket[f"{field}_min"] = value; bucket[f"{field}_mean"] = value; bucket[f"{field}_max"] = value
        buckets.append(bucket)
    return {"source_point_count": len(timeline), "bucket_count": len(buckets), "downsampled": False, "buckets": buckets, "preserved_actionable_points": []}


def _event_id(event: dict[str, Any]) -> str:
    return str(event.get("event_id") or event.get("incident_id") or "event")


def _classification(event: dict[str, Any]) -> str:
    return str(event.get("classification") or (event.get("diagnosis") or {}).get("classification") or "unattributed")


def _confidence(event: dict[str, Any]) -> str:
    return str(event.get("confidence") or (event.get("diagnosis") or {}).get("confidence") or "low")


def _peak(event: dict[str, Any]) -> Any:
    return event.get("peak_frame_time_ms") if event.get("peak_frame_time_ms") is not None else event.get("max_frame_time_ms")


def _card(label: Any, value: Any, css: str = "") -> str:
    return f"<div class='card'><div class='muted'>{_e(label)}</div><div class='value {css}'>{_e(value)}</div></div>"


def _fmt(value: Any) -> str:
    if value is None:
        return "null"
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def _e(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return html.escape("" if value is None else str(value))
