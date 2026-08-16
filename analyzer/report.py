from __future__ import annotations

import copy
import html
import json
from pathlib import Path
from typing import Any


def write_single_run_report(
    analysis: dict[str, Any],
    output_root: Path,
    selection: dict[str, Any],
) -> tuple[Path, Path, dict[str, Any]]:
    result = copy.deepcopy(analysis)
    result["selection"] = selection
    run_id = str(result["run_id"])
    run_output = output_root / "runs" / run_id
    run_output.mkdir(parents=True, exist_ok=True)
    json_path = run_output / "analysis.json"
    html_path = run_output / "analysis.html"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_single_run_html(result), encoding="utf-8")
    return json_path.resolve(), html_path.resolve(), result


def render_single_run_html(result: dict[str, Any]) -> str:
    run = result.get("run_summary") or {}
    environment = run.get("environment") or {}
    summary = result.get("diagnostic_summary") or {}
    quality = result.get("quality") or {}
    timeline = result.get("timeline") or []
    incidents = result.get("incidents") or []
    gc = result.get("gc_diagnostics") or {}
    allocations = result.get("allocation_diagnostics") or {}
    memory = result.get("memory_diagnostics") or {}
    thresholds = result.get("diagnostic_thresholds") or {}
    severity = summary.get("severity_counts") or {}
    styles = """
    :root{color-scheme:dark;--bg:#0b1020;--panel:#151c30;--line:#27324d;--text:#e8edf7;--muted:#9aa8c2;--accent:#68d5ff;--warn:#ffcc66;--bad:#ff6b7a;--good:#63d297}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,Segoe UI,sans-serif}main{max-width:1280px;margin:auto;padding:28px}h1,h2,h3{margin:.4em 0}.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}.card,section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin:14px 0}.value{font-size:24px;font-weight:700}.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}table{width:100%;border-collapse:collapse}th,td{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}code{color:var(--accent);word-break:break-all}svg{width:100%;height:auto;background:#0e1528;border-radius:8px}.legend span{display:inline-block;margin-right:16px}.dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:5px}details{border-top:1px solid var(--line);padding:10px 0}summary{cursor:pointer;font-weight:600}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 8px;margin:2px}.incident{border-left:4px solid var(--warn);padding-left:12px}.severe{border-color:var(--bad)}
    """
    summary_cards = "".join([
        _card("Run ID", result.get("run_id")),
        _card("Duration", f"{_fmt(result.get('measurement_coverage_ms'))} ms"),
        _card("Frames", result.get("frame_count")),
        _card("Incidents", summary.get("incident_count"), "warn" if incidents else "good"),
        _card("Severe / Major / Minor", f"{severity.get('severe',0)} / {severity.get('major',0)} / {severity.get('minor',0)}"),
        _card("Memory conclusion", memory.get("status"), "warn" if memory.get("status") != "stable" else "good"),
    ])
    incident_rows = "".join(
        f"<tr><td><a href='#{_e(item['incident_id'])}'>{_e(item['incident_id'])}</a></td><td>{_e(item['severity'])}</td><td>{_fmt(item['start_frame_index'])}–{_fmt(item['end_frame_index'])}</td><td>{_fmt(item['max_frame_time_ms'])}</td><td>{_e(item['diagnosis']['classification'])}</td><td>{_e(item['diagnosis']['confidence'])}</td><td>{_fmt(item['raw_csv_rows']['start'])}–{_fmt(item['raw_csv_rows']['end'])}</td></tr>"
        for item in sorted(incidents, key=lambda value: value["max_frame_time_ms"], reverse=True)
    ) or "<tr><td colspan='7'>No incident exceeded the experimental thresholds.</td></tr>"
    incident_details = "".join(_incident_html(item) for item in incidents)
    quality_flags = "".join(f"<span class='pill'>{_e(flag)}</span>" for flag in quality.get("quality_flags") or []) or "None"
    warnings = "".join(f"<li>{_e(value)}</li>" for value in quality.get("warnings") or []) or "<li>None</li>"
    traceability = "".join(f"<li>{_e(value)}</li>" for value in run.get("traceability_warnings") or []) or "<li>None</li>"
    gc_rows = "".join(
        f"<tr><td>{_fmt(item.get('window_index'))}</td><td>{_fmt(item.get('start_frame_index'))}–{_fmt(item.get('end_frame_index'))}</td><td>{_fmt(item.get('marker_total_ms'))}</td><td>{_e(item.get('generation_collection_counts') or {})}</td><td>{_fmt(item.get('gc_used_bytes_delta'))}</td><td>{_e(item.get('overlapping_incident_ids') or [])}</td></tr>"
        for item in gc.get("windows") or []
    ) or "<tr><td colspan='6'>No GC work window.</td></tr>"
    allocation_rows = "".join(
        f"<tr><td>{_fmt(item.get('frame_index'))}</td><td>{_fmt(item.get('timestamp_ms'))}</td><td>{_fmt(item.get('allocated_bytes'))}</td><td>{_fmt(item.get('csv_row_number'))}</td></tr>"
        for item in (allocations.get("spikes") or [])[:100]
    ) or "<tr><td colspan='4'>No allocation spike exceeded the experimental threshold.</td></tr>"
    memory_rows = "".join(
        f"<tr><td>{_e(name)}</td><td>{_fmt(values.get('start'))}</td><td>{_fmt(values.get('end'))}</td><td>{_fmt(values.get('peak'))}</td><td>{_fmt(values.get('delta'))}</td><td>{_fmt(values.get('robust_median_window_slope_bytes_per_second'))}</td><td>{len(values.get('step_growth_candidates') or [])}</td></tr>"
        for name, values in (memory.get("metrics") or {}).items() if values
    ) or "<tr><td colspan='7'>No memory series is available.</td></tr>"
    sources = result.get("source_artifacts") or {}
    source_rows = "".join(f"<tr><th>{_e(key)}</th><td><code>{_e(value)}</code></td></tr>" for key, value in sources.items())
    html_text = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>PerfGuardian { _e(result.get('run_id')) }</title><style>{styles}</style></head><body><main>
    <h1>PerfGuardian single-run diagnostics</h1><p class='muted'>Offline report. All charts and conclusions are rendered from the accompanying analysis.json object.</p>
    <div class='grid'>{summary_cards}</div>
    <section><h2>Overall conclusion</h2><p>{_e(summary.get('overall_conclusion'))}</p><p><strong>Selection:</strong> {_e((result.get('selection') or {}).get('rule'))}</p><p><strong>Experimental incident threshold:</strong> {_fmt(thresholds.get('effective_incident_frame_threshold_ms'))} ms; normal-gap merge ≤ {_fmt(thresholds.get('merge_normal_gap_frames'))} frame(s).</p></section>
    <section><h2>Run summary</h2><table><tr><th>Build</th><td>{_e(run.get('build_type'))}</td><th>Unity</th><td>{_e(run.get('unity_version'))}</td></tr><tr><th>Scene</th><td>{_e(run.get('scenario_id'))} / {_e(run.get('active_scene'))}</td><th>Commit</th><td>{_e(run.get('commit_sha'))}</td></tr><tr><th>CPU</th><td>{_e(environment.get('cpu_model'))}</td><th>GPU</th><td>{_e(environment.get('gpu_model'))}</td></tr><tr><th>Display</th><td colspan='3'>{_fmt(environment.get('display_width_pixels'))}×{_fmt(environment.get('display_height_pixels'))} @ {_fmt(environment.get('display_refresh_rate_hz'))} Hz, VSync={_fmt(environment.get('v_sync_count'))}</td></tr></table></section>
    <section><h2>Frame-time incidents</h2>{_line_chart(timeline, [('frame_time_ms','#68d5ff')], incidents, 'Frame time (ms)')}<table><thead><tr><th>ID</th><th>Severity</th><th>Frames</th><th>Max ms</th><th>Classification</th><th>Confidence</th><th>CSV rows</th></tr></thead><tbody>{incident_rows}</tbody></table></section>
    <section><h2>CPU / GPU timing</h2>{_line_chart(timeline, [('cpu_frame_time_ms','#63d297'),('gpu_frame_time_ms','#c18cff')], incidents, 'CPU / GPU frame time (ms)')}</section>
    <section><h2>GC and allocation</h2><div class='grid'>{_card('GC windows',gc.get('window_count'))}{_card('GC marker total',f"{_fmt(gc.get('total_marker_ms'))} ms")}{_card('GC windows in incidents',gc.get('incident_participation_count'))}{_card('Allocation spikes',allocations.get('spike_count'))}</div>{_line_chart(timeline,[('gc_collect_ms','#ff6b7a')],incidents,'GC marker (ms)')}{_line_chart(timeline,[('gc_allocated_bytes','#ffcc66')],incidents,'GC allocated bytes')}<h3>GC work windows</h3><table><tr><th>Index</th><th>Frames</th><th>Marker total ms</th><th>Generations</th><th>GC used delta</th><th>Overlapping incidents</th></tr>{gc_rows}</table><h3>Allocation spikes</h3><table><tr><th>Frame</th><th>Timestamp ms</th><th>Bytes</th><th>CSV row</th></tr>{allocation_rows}</table></section>
    <section><h2>Memory trend</h2><p><strong>{_e(memory.get('status'))}</strong> — {_e(memory.get('explanation'))}</p>{_line_chart(timeline,[('memory_used_bytes','#68d5ff'),('unity_reserved_bytes','#c18cff'),('gc_used_bytes','#63d297'),('gc_reserved_bytes','#ffcc66')],incidents,'Unity / GC memory bytes')}<table><tr><th>Metric</th><th>Start</th><th>End</th><th>Peak</th><th>Delta</th><th>Robust slope B/s</th><th>Step candidates</th></tr>{memory_rows}</table></section>
    <section><h2>Incident evidence</h2>{incident_details or '<p>No incident evidence blocks.</p>'}</section>
    <section><h2>Data quality and limitations</h2><p>Status: <strong>{_e(quality.get('status'))}</strong></p><h3>Quality flags</h3><p>{quality_flags}</p><h3>Quality warnings</h3><ul>{warnings}</ul><h3>Traceability warnings</h3><ul>{traceability}</ul><ul><li>No process CPU utilization, system GPU utilization, call stacks, or full PlayerLoop tree are collected.</li><li>Marker values can overlap and include Collector overhead where declared.</li><li>Short runs do not prove memory leaks.</li></ul></section>
    <section><h2>Raw artifacts</h2><table>{source_rows}</table></section>
    </main></body></html>"""
    return html_text


def render_collection_html(result: dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><td>{_e(run.get('run_id'))}</td><td>{_e(run.get('analysis_eligible'))}</td><td>{_fmt(run.get('frame_count'))}</td><td>{_fmt((run.get('diagnostic_summary') or {}).get('incident_count'))}</td><td>{_e((run.get('comparison_key') or {}).get('build_type'))}</td></tr>"
        for run in result.get("runs") or []
    )
    return f"<!doctype html><meta charset='utf-8'><title>PerfGuardian collection</title><style>body{{font:14px system-ui;max-width:1100px;margin:30px auto}}table{{width:100%;border-collapse:collapse}}td,th{{padding:8px;border-bottom:1px solid #ccc;text-align:left}}</style><h1>PerfGuardian collection scan</h1><p>This compatibility view does not compare runs.</p><table><tr><th>Run</th><th>Eligible</th><th>Frames</th><th>Incidents</th><th>Build</th></tr>{rows}</table>"


def _incident_html(item: dict[str, Any]) -> str:
    diagnosis = item["diagnosis"]
    evidence = "".join(f"<li><code>{_e(value.get('signal'))}</code>: {_e(value.get('detail'))} — {_e(value.get('value'))}</li>" for value in diagnosis["evidence"])
    counter = "".join(f"<li><code>{_e(value.get('signal'))}</code>: {_e(value.get('detail'))}</li>" for value in diagnosis["counter_evidence"])
    limits = "".join(f"<li>{_e(value)}</li>" for value in diagnosis["limitations"])
    frames = item["evidence_window"]["frames"]
    resource = item.get("resource_evidence") or {}
    synchronized = resource.get("worst_frame_synchronized_timing_ms") or {}
    markers = "".join(f"<span class='pill'>{_e(name)}={_fmt(value)} ms</span>" for name, value in (resource.get("marker_peaks_ms") or {}).items()) or "None"
    allocation = resource.get("allocation") or {}
    memory_rows = "".join(
        f"<tr><td>{_e(name)}</td><td>{_fmt(values.get('before'))}</td><td>{_fmt(values.get('after'))}</td><td>{_fmt(values.get('delta_bytes'))}</td></tr>"
        for name, values in ((resource.get("memory_context") or {}).get("metrics") or {}).items()
    )
    frame_rows = "".join(f"<tr><td>{_fmt(row.get('frame_index'))}</td><td>{_fmt(row.get('frame_time_ms'))}</td><td>{_fmt(row.get('cpu_frame_time_ms'))}</td><td>{_fmt(row.get('gpu_frame_time_ms'))}</td><td>{_fmt(row.get('gc_collect_ms'))}</td><td>{_fmt(row.get('wait_for_target_fps_ms'))}</td><td>{_fmt(row.get('csv_row_number'))}</td></tr>" for row in frames)
    css = "incident severe" if item["severity"] == "severe" else "incident"
    return f"<article id='{_e(item['incident_id'])}' class='{css}'><h3>{_e(item['incident_id'])}: {_e(item['severity'])} / {_e(diagnosis['classification'])}</h3><p>Frames {_fmt(item['start_frame_index'])}–{_fmt(item['end_frame_index'])}; max {_fmt(item['max_frame_time_ms'])} ms; excess {_fmt(item['excess_time_over_steady_baseline_ms'])} ms; confidence {_e(diagnosis['confidence'])}.</p><details open><summary>Evidence</summary><ul>{evidence}</ul><h4>Worst-frame synchronized timing</h4><p>frame={_fmt(synchronized.get('frame'))} ms; CPU={_fmt(synchronized.get('cpu'))} ms; GPU={_fmt(synchronized.get('gpu'))} ms; wait={_fmt(synchronized.get('wait_for_target_fps'))} ms.</p><h4>Marker peaks</h4><p>{markers}</p><h4>Allocation</h4><p>peak={_fmt(allocation.get('peak_bytes'))} bytes; threshold={_fmt(allocation.get('threshold_bytes'))}; spike={_e(allocation.get('is_spike'))}. {_e(allocation.get('limitation'))}</p><h4>Memory context</h4><table><tr><th>Metric</th><th>Before</th><th>After</th><th>Delta bytes</th></tr>{memory_rows}</table><h4>Counter-evidence</h4><ul>{counter}</ul><h4>Limitations</h4><ul>{limits}</ul></details><details><summary>Context frames</summary><table><tr><th>Frame</th><th>Frame ms</th><th>CPU ms</th><th>GPU ms</th><th>GC ms</th><th>Wait ms</th><th>CSV row</th></tr>{frame_rows}</table></details></article>"


def _line_chart(timeline: list[dict[str, Any]], series: list[tuple[str,str]], incidents: list[dict[str, Any]], title: str) -> str:
    width, height, pad = 1000, 260, 42
    points = [(float(row.get('timestamp_ms')), row) for row in timeline if isinstance(row.get('timestamp_ms'), (int,float))]
    values = [float(row.get(field)) for _, row in points for field, _ in series if isinstance(row.get(field), (int,float))]
    if not points or not values:
        return f"<p class='muted'>{_e(title)}: insufficient data.</p>"
    min_x, max_x = points[0][0], points[-1][0]
    min_y, max_y = min(values), max(values)
    if max_y == min_y: max_y = min_y + 1
    def sx(value: float) -> float: return pad + (value-min_x)/max(max_x-min_x,1)*(width-2*pad)
    def sy(value: float) -> float: return height-pad-(value-min_y)/(max_y-min_y)*(height-2*pad)
    bands = "".join(f"<rect x='{sx(float(item['start_timestamp_ms'])):.1f}' y='{pad}' width='{max(2,sx(float(item['end_timestamp_ms']))-sx(float(item['start_timestamp_ms']))):.1f}' height='{height-2*pad}' fill='#ff6b7a' opacity='.18'/>" for item in incidents if item.get('start_timestamp_ms') is not None and item.get('end_timestamp_ms') is not None)
    paths=[]
    legend=[]
    for field,color in series:
        chunks=[]; current=[]
        for x,row in points:
            value=row.get(field)
            if isinstance(value,(int,float)): current.append(f"{sx(x):.1f},{sy(float(value)):.1f}")
            elif current: chunks.append(current); current=[]
        if current: chunks.append(current)
        paths.extend(f"<polyline points='{' '.join(chunk)}' fill='none' stroke='{color}' stroke-width='1.5'/>" for chunk in chunks)
        legend.append(f"<span><i class='dot' style='background:{color}'></i>{_e(field)}</span>")
    return f"<h3>{_e(title)}</h3><div class='legend'>{''.join(legend)}</div><svg viewBox='0 0 {width} {height}' role='img'><text x='{pad}' y='20' fill='#9aa8c2'>{max_y:.3f}</text><text x='{pad}' y='{height-10}' fill='#9aa8c2'>{min_y:.3f}</text>{bands}{''.join(paths)}</svg>"


def _card(label: Any, value: Any, css: str = "") -> str:
    return f"<div class='card'><div class='muted'>{_e(label)}</div><div class='value {css}'>{_e(value)}</div></div>"


def _fmt(value: Any) -> str:
    if value is None: return "null"
    if isinstance(value,float): return f"{value:.4f}"
    return str(value)


def _e(value: Any) -> str:
    if isinstance(value,(dict,list)): value=json.dumps(value,ensure_ascii=False)
    return html.escape("" if value is None else str(value))


# Phase 3.1 keeps this module path stable while delegating the calibrated,
# compact report implementation to a focused module.
from .report_v31 import (  # noqa: E402
    render_collection_html as render_collection_html,
    render_single_run_html as render_single_run_html,
    write_single_run_report as write_single_run_report,
)
