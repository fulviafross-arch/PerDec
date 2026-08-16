import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from analyzer.__main__ import main as analyzer_main
from analyzer.core import analyze_artifacts, read_frames
from analyzer.diagnostics import (
    DIAGNOSTIC_THRESHOLDS,
    allocation_diagnostics,
    classify_incident,
    detect_incidents,
    memory_diagnostics,
)
from analyzer.report import render_single_run_html, write_single_run_report
from analyzer.selection import RunSelectionError, select_run
from runner.core import _generate_analysis


def manifest(refresh=60.0):
    return {
        "environment": {"display_refresh_rate_hz": refresh, "v_sync_count": 1},
        "build": {"build_type": "development", "commit_sha": "abc123"},
        "scenario": {"scenario_id": "scene", "active_scene": "Main"},
    }


def frame(index, frame_ms=16.67, cpu=10.0, gpu=8.0, wait=0.0, gc=0.0, allocation=100):
    return {
        "frame_index": index,
        "timestamp_ms": index * 17,
        "frame_time_ms": frame_ms,
        "cpu_frame_time_ms": cpu,
        "gpu_frame_time_ms": gpu,
        "wait_for_target_fps_ms": wait,
        "gc_collect_ms": gc,
        "gc_allocated_bytes": allocation,
        "memory_used_bytes": 1000000 + index * 100,
        "gc_used_bytes": 100000 + index * 10,
        "gc_reserved_bytes": 200000,
    }


class RunSelectionTests(unittest.TestCase):
    def write_report(self, root, run_id, completed_at, eligible=True):
        directory = Path(root) / "project" / "experiment" / run_id
        directory.mkdir(parents=True)
        report = {
            "run_id": run_id,
            "completed_at": completed_at,
            "eligible_for_analysis": eligible,
            "runner_status": "completed" if eligible else "invalid_artifacts",
        }
        (directory / "runner-report.json").write_text(json.dumps(report), encoding="utf-8")
        return directory

    def test_run_id_exact_and_latest_selection(self):
        with tempfile.TemporaryDirectory() as root:
            older = self.write_report(root, "older", "2026-08-14T01:00:00Z")
            newer = self.write_report(root, "newer", "2026-08-14T02:00:00+00:00")
            self.write_report(root, "ineligible", "2026-08-14T03:00:00Z", False)
            directory, _, selection = select_run(Path(root), run_id="older")
            self.assertEqual(directory, older)
            self.assertEqual(selection["mode"], "run_id")
            directory, _, selection = select_run(Path(root), latest=True)
            self.assertEqual(directory, newer)
            self.assertEqual(selection["selected_run_id"], "newer")
            self.assertIn("completed_at", selection["rule"])

    def test_run_id_missing_duplicate_and_ineligible_are_errors(self):
        with tempfile.TemporaryDirectory() as root:
            self.write_report(root, "bad", "2026-08-14T01:00:00Z", False)
            with self.assertRaisesRegex(RunSelectionError, "not found"):
                select_run(Path(root), run_id="missing")
            with self.assertRaisesRegex(RunSelectionError, "not eligible"):
                select_run(Path(root), run_id="bad")
            self.write_report(Path(root) / "copy", "duplicate", "2026-08-14T01:00:00Z")
            self.write_report(Path(root) / "another", "duplicate", "2026-08-14T02:00:00Z")
            with self.assertRaisesRegex(RunSelectionError, "duplicated"):
                select_run(Path(root), run_id="duplicate")

    def test_reports_are_isolated_by_run_id(self):
        base = {
            "run_summary": {}, "diagnostic_summary": {"incident_count": 0, "severity_counts": {}, "overall_conclusion": "ok"},
            "quality": {}, "timeline": [], "incidents": [], "gc_diagnostics": {},
            "allocation_diagnostics": {}, "memory_diagnostics": {}, "source_artifacts": {},
        }
        with tempfile.TemporaryDirectory() as root:
            first_json, _, _ = write_single_run_report(base | {"run_id": "one"}, Path(root), {"mode": "run_id"})
            second_json, _, _ = write_single_run_report(base | {"run_id": "two"}, Path(root), {"mode": "run_id"})
            self.assertNotEqual(first_json.parent, second_json.parent)
            self.assertTrue(first_json.exists())
            self.assertTrue(second_json.exists())


class IncidentDetectionTests(unittest.TestCase):
    def test_single_continuous_gap_merge_and_no_incident(self):
        rows = [frame(index) for index in range(12)]
        rows[3]["frame_time_ms"] = 40
        incidents, thresholds = detect_incidents(rows, manifest(), [], [])
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["start_frame_index"], 3)
        self.assertEqual(incidents[0]["end_frame_index"], 3)
        self.assertEqual(thresholds["status"], "experimental")

        rows[4]["frame_time_ms"] = 38
        incidents, _ = detect_incidents(rows, manifest(), [], [])
        self.assertEqual(incidents[0]["duration_frames"], 2)

        rows[4]["frame_time_ms"] = 16.67
        rows[5]["frame_time_ms"] = 39
        incidents, _ = detect_incidents(rows, manifest(), [], [])
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["end_frame_index"], 5)

        incidents, _ = detect_incidents([frame(index) for index in range(20)], manifest(), [], [])
        self.assertEqual(incidents, [])

    def test_classification_directions_and_evidence_contract(self):
        thresholds = {**DIAGNOSTIC_THRESHOLDS, "frame_budget_ms": 16.67, "steady_median_frame_time_ms": 16.67}
        cases = [
            ([frame(0, 40, cpu=35, gpu=5)], [], "cpu_bound_candidate"),
            ([frame(0, 40, cpu=20, gpu=36)], [], "gpu_bound_candidate"),
            ([frame(0, 40, cpu=20, gpu=10, gc=2)], [{"marker_total_ms": 2}], "gc_participating"),
            ([frame(0, 16.7, cpu=8, gpu=7, wait=6)], [], "frame_pacing"),
            ([frame(0, 40, cpu=None, gpu=None)], [], "unattributed"),
            ([frame(0, 40, cpu=20, gpu=5)], [], "unattributed"),
        ]
        for segment, windows, expected in cases:
            result = classify_incident(segment, manifest(), windows, [], thresholds)
            self.assertEqual(result["classification"], expected)
            self.assertIn(result["confidence"], ("low", "medium", "high"))
            self.assertIsInstance(result["evidence"], list)
            self.assertIsInstance(result["counter_evidence"], list)
            self.assertIsInstance(result["limitations"], list)

    def test_gc_overlap_and_legal_tail_do_not_create_fake_incident(self):
        rows = [frame(index) for index in range(10)]
        rows[4].update({"frame_time_ms": 40, "gc_collect_ms": 1.2})
        windows = [{"window_index": 0, "start_frame_index": 4, "end_frame_index": 4, "marker_total_ms": 1.2}]
        incidents, _ = detect_incidents(rows, manifest(), windows, [])
        self.assertEqual(incidents[0]["overlapping_gc_window_indexes"], [0])
        self.assertEqual(incidents[0]["diagnosis"]["classification"], "gc_participating")
        self.assertIn("generation_collection_counts", incidents[0]["resource_evidence"]["gc_windows"][0])
        tail_rows = [frame(index) for index in range(10)]
        tail_rows[-1]["cpu_frame_time_ms"] = None
        tail_rows[-1]["gpu_frame_time_ms"] = None
        tail_rows[-1]["gc_collect_ms"] = None
        normal, _ = detect_incidents(tail_rows, manifest(), [], [])
        self.assertEqual(normal, [])

    def test_cpu_gpu_peaks_from_different_frames_are_not_combined(self):
        thresholds = {**DIAGNOSTIC_THRESHOLDS, "frame_budget_ms": 16.67, "steady_median_frame_time_ms": 16.67}
        segment = [
            frame(0, 40, cpu=15, gpu=5),
            frame(1, 35, cpu=34, gpu=10),
        ]
        result = classify_incident(segment, manifest(), [], [], thresholds)
        self.assertEqual(result["classification"], "unattributed")
        self.assertTrue(any(item["signal"] == "cpu_share_of_frame" for item in result["counter_evidence"]))


class GcMemoryAndHtmlTests(unittest.TestCase):
    def test_analyzer_rejects_non_finite_numbers(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "frames.csv"
            path.write_text(
                "run_id,timestamp_ms,frame_index,frame_time_ms\n"
                "run,0,0,NaN\nrun,1,1,Infinity\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "non-finite"):
                read_frames(path)

    def test_allocation_spike_and_short_memory_run(self):
        rows = [frame(index, allocation=100) for index in range(20)]
        rows[10]["gc_allocated_bytes"] = 1000
        allocations = allocation_diagnostics(rows, ["gc_allocated_in_frame_includes_collector_overhead"])
        self.assertEqual(allocations["spike_count"], 1)
        self.assertTrue(allocations["limitations"])
        trends = {"memory_used_bytes": {"start": 1, "end": 2, "peak": 2, "delta": 1, "endpoint_slope_bytes_per_second": 1}}
        memory = memory_diagnostics(rows, trends)
        self.assertEqual(memory["status"], "insufficient_duration")
        self.assertIn("too short", memory["explanation"].lower())
        self.assertNotIn("leak detected", memory["explanation"].lower())

    def test_long_growth_is_candidate_not_leak_claim(self):
        rows = []
        for index in range(121):
            row = frame(index)
            row["timestamp_ms"] = index * 500
            row["memory_used_bytes"] = 1000000 + index * 50000
            rows.append(row)
        trends = {"memory_used_bytes": {"start": 1000000, "end": 7000000, "peak": 7000000, "delta": 6000000, "endpoint_slope_bytes_per_second": 100000}}
        result = memory_diagnostics(rows, trends)
        self.assertEqual(result["status"], "growth_candidate")
        self.assertIn("candidate", result["explanation"].lower())
        self.assertIn("not proof", result["explanation"].lower())

    def test_html_uses_analysis_object_and_contains_required_modules(self):
        result = {
            "run_id": "html-run", "measurement_coverage_ms": 5000, "frame_count": 2,
            "run_summary": {"build_type": "development", "environment": {}},
            "diagnostic_summary": {"incident_count": 0, "severity_counts": {}, "overall_conclusion": "SAME_OBJECT_SENTINEL"},
            "quality": {"status": "valid", "warnings": [], "quality_flags": []},
            "timeline": [frame(0), frame(1)], "incidents": [],
            "gc_diagnostics": {"window_count": 0, "total_marker_ms": 0, "incident_participation_count": 0},
            "allocation_diagnostics": {"spike_count": 0},
            "memory_diagnostics": {"status": "insufficient_duration", "explanation": "short"},
            "source_artifacts": {"frames_csv": "D:/data/frames.csv"},
            "selection": {"rule": "exact"},
        }
        output = render_single_run_html(result)
        for text in ("SAME_OBJECT_SENTINEL", "Frame Performance", "CPU/GPU Diagnosis", "GC", "Memory", "Data Quality", "Raw Artifacts", "<svg"):
            self.assertIn(text, output)

    def test_legacy_full_scan_cli_still_writes_outputs(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "reports"
            with patch("analyzer.__main__.analyze_artifacts", return_value={"runs": [], "eligible_run_count": 0}):
                with redirect_stdout(StringIO()):
                    self.assertEqual(analyzer_main(["--artifacts-root", root, "--output-dir", str(output)]), 0)
            self.assertTrue((output / "analysis.json").exists())
            self.assertTrue((output / "analysis.html").exists())


class RunnerAnalyzerBoundaryTests(unittest.TestCase):
    def test_runner_analysis_success_and_failure_do_not_change_runner_report(self):
        report = {"run_id": "one", "eligible_for_analysis": True}
        with tempfile.TemporaryDirectory() as root:
            with patch("analyzer.core.analyze_run", return_value={"run_id": "one"}), patch(
                "analyzer.report.write_single_run_report",
                return_value=(Path(root) / "analysis.json", Path(root) / "analysis.html", {}),
            ):
                result = _generate_analysis(Path(root), report, Path(root))
                self.assertEqual(result["status"], "completed")
            with patch("analyzer.core.analyze_run", side_effect=ValueError("bad analysis")):
                result = _generate_analysis(Path(root), report, Path(root))
                self.assertEqual(result["status"], "failed")
                self.assertTrue(report["eligible_for_analysis"])


if __name__ == "__main__":
    unittest.main()
