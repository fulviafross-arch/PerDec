"""Synthetic Analyzer calibration fixtures; not real performance experiments."""

import unittest
from pathlib import Path

from analyzer.core import analyze_run
from analyzer.diagnostics import (
    build_activity_events,
    chart_data,
    detect_incidents,
    memory_diagnostics,
    summarize_events,
)


def manifest(refresh=165.0):
    return {"environment": {"display_refresh_rate_hz": refresh, "v_sync_count": 1}}


def frame(index, frame_ms=6.1, cpu=5.5, gpu=4.0, wait=0.0, gc=0.0, memory=None):
    return {
        "frame_index": index,
        "timestamp_ms": index * 6,
        "frame_time_ms": frame_ms,
        "cpu_frame_time_ms": cpu,
        "gpu_frame_time_ms": gpu,
        "wait_for_target_fps_ms": wait,
        "gc_collect_ms": gc,
        "gc_allocated_bytes": 100,
        "memory_used_bytes": memory if memory is not None else 1_000_000,
        "unity_reserved_bytes": 2_000_000,
        "gc_used_bytes": 100_000,
        "gc_reserved_bytes": 200_000,
    }


def detect(rows, windows=None):
    return detect_incidents(rows, manifest(), windows or [], [])[0]


class PerformanceEventCalibrationTests(unittest.TestCase):
    def test_stable_run_has_no_actionable_issue(self):
        events = detect([frame(index) for index in range(200)])
        summary = summarize_events(events, 0, "stable", 200, {"status": "valid"})
        self.assertEqual(events, [])
        self.assertEqual(summary["actionable_issue_count"], 0)

    def test_unexplained_108ms_frame_is_p0_severe_hitch(self):
        rows = [frame(index) for index in range(100)]
        rows[50].update({"frame_time_ms": 108.0, "cpu_frame_time_ms": 10.0, "gpu_frame_time_ms": 8.0})
        event = detect(rows)[0]
        self.assertEqual(event["event_type"], "severe_hitch")
        self.assertEqual(event["severity"], "severe")
        self.assertEqual(event["priority"], "P0")
        self.assertTrue(event["is_actionable"])
        self.assertEqual(event["classification"], "unattributed")
        unexplained = next(item for item in event["evidence"] if item["signal"] == "unexplained_frame_time_ms")
        self.assertEqual(unexplained["value"], 98.0)
        self.assertTrue(any("scheduling" in value for value in event["limitations"]))

    def test_cpu_and_gpu_hitches_are_conservative_candidates(self):
        cpu_rows = [frame(index) for index in range(40)]
        cpu_rows[20].update({"frame_time_ms": 60.0, "cpu_frame_time_ms": 56.0, "gpu_frame_time_ms": 10.0})
        cpu_event = detect(cpu_rows)[0]
        self.assertEqual(cpu_event["event_type"], "severe_hitch")
        self.assertEqual(cpu_event["classification"], "cpu_bound_candidate")

        gpu_rows = [frame(index) for index in range(40)]
        gpu_rows[20].update({"frame_time_ms": 45.0, "cpu_frame_time_ms": 8.0, "gpu_frame_time_ms": 42.0})
        gpu_event = detect(gpu_rows)[0]
        self.assertEqual(gpu_event["event_type"], "hitch")
        self.assertEqual(gpu_event["classification"], "gpu_bound_candidate")

    def test_gc_overlap_participates_but_is_not_root_cause(self):
        rows = [frame(index) for index in range(40)]
        rows[20].update({"frame_time_ms": 55.0, "gc_collect_ms": 2.0})
        windows = [{
            "window_index": 0, "start_frame_index": 20, "end_frame_index": 20,
            "marker_total_ms": 2.0, "duration_frames": 1,
            "generation_collection_counts": {"gen0": 1, "gen1": 0, "gen2": 0},
        }]
        event = detect(rows, windows)[0]
        self.assertEqual(event["event_type"], "severe_hitch")
        self.assertEqual(event["classification"], "gc_participating")
        self.assertNotIn("root_cause", str(event).lower())

    def test_budget_miss_is_minor_and_not_actionable(self):
        rows = [frame(index) for index in range(50)]
        rows[25]["frame_time_ms"] = 9.0
        event = detect(rows)[0]
        self.assertEqual(event["event_type"], "budget_miss")
        self.assertEqual(event["severity"], "minor")
        self.assertEqual(event["priority"], "P3")
        self.assertFalse(event["is_actionable"])

    def test_long_pacing_state_is_not_severe_hitch(self):
        rows = [frame(index) for index in range(300)]
        for index in range(100, 200):
            rows[index].update({"frame_time_ms": 8.5, "cpu_frame_time_ms": 6.0, "gpu_frame_time_ms": 5.5, "wait_for_target_fps_ms": 3.0})
        events = detect(rows)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "pacing_state")
        self.assertEqual(events[0]["severity"], "info")
        self.assertFalse(events[0]["is_actionable"])

    def test_adjacent_slow_frames_merge_into_one_event(self):
        rows = [frame(index) for index in range(50)]
        for index in range(20, 25):
            rows[index]["frame_time_ms"] = 12.0
        events = detect(rows)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["frame_count"], 5)
        self.assertEqual(events[0]["slow_frame_count"], 5)

    def test_memory_growth_creates_candidate_activity(self):
        rows = [frame(index, memory=1_000_000 + index * 50_000) for index in range(121)]
        for index, row in enumerate(rows):
            row["timestamp_ms"] = index * 500
        trends = {"memory_used_bytes": {"start": 1_000_000, "end": 7_000_000, "peak": 7_000_000, "delta": 6_000_000, "endpoint_slope_bytes_per_second": 100_000}}
        memory = memory_diagnostics(rows, trends)
        events = build_activity_events([], memory, 60_000)
        self.assertEqual(memory["status"], "growth_candidate")
        self.assertEqual(events[0]["event_type"], "memory_anomaly")
        self.assertTrue(events[0]["is_actionable"])

    def test_chart_data_is_bounded_and_preserves_severe_neighborhood(self):
        rows = [frame(index) for index in range(5_000)]
        rows[2_500].update({"frame_time_ms": 108.0, "cpu_frame_time_ms": 10.0, "gpu_frame_time_ms": 8.0})
        events = detect(rows)
        chart = chart_data(rows, events)
        self.assertLessEqual(chart["bucket_count"], 900)
        self.assertTrue(chart["downsampled"])
        self.assertTrue(any(point["frame_time_ms"] == 108.0 for point in chart["preserved_actionable_points"]))
        self.assertEqual(max(bucket["frame_time_ms_max"] for bucket in chart["buckets"]), 108.0)


REAL_60_SECOND_RUN = (
    Path(__file__).resolve().parents[1]
    / "artifacts" / "MyGame" / "PerfTest"
    / "0dfe53bc-db98-41a6-9735-bfd1cd0cc585"
)


@unittest.skipUnless(REAL_60_SECOND_RUN.exists(), "real 60-second artifact is not present")
class RealRunCalibrationRegressionTests(unittest.TestCase):
    def test_severe_hitches_pacing_gc_memory_and_chart_regression(self):
        result = analyze_run(REAL_60_SECOND_RUN)
        actionable = [event for event in result["events"] if event["is_actionable"]]
        severe = sorted(actionable, key=lambda event: event["peak_frame_time_ms"], reverse=True)
        self.assertEqual(result["diagnostic_summary"]["actionable_issue_count"], 2)
        self.assertAlmostEqual(severe[0]["peak_frame_time_ms"], 107.9989, places=3)
        self.assertEqual(severe[0]["classification"], "unattributed")
        self.assertAlmostEqual(severe[1]["peak_frame_time_ms"], 69.2197, places=3)
        self.assertEqual(severe[1]["classification"], "cpu_bound_candidate")
        long_pacing = [
            event for event in result["events"]
            if event["event_type"] == "pacing_state" and event["frame_count"] >= 100
        ]
        self.assertTrue(long_pacing)
        self.assertTrue(all(not event["is_actionable"] and event["severity"] == "info" for event in long_pacing))
        self.assertEqual(result["diagnostic_summary"]["gc_window_count"], 4)
        self.assertEqual(result["memory_diagnostics"]["status"], "stable")
        self.assertLessEqual(result["chart_data"]["bucket_count"], 900)
        self.assertNotIn("timeline", result)


if __name__ == "__main__":
    unittest.main()
