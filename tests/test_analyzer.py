import csv
import json
import tempfile
import unittest
from pathlib import Path

from analyzer.core import analyze_run, gc_windows, metric_status, read_frames


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_RUNS = PROJECT_ROOT / "artifacts" / "MyGame" / "PerfTest"


class AnalyzerUnitTests(unittest.TestCase):
    def test_absent_null_and_zero_are_distinct(self):
        rows = [
            {"gc_allocated_bytes": None, "gc_collect_ms": 0.0},
            {"gc_allocated_bytes": None, "gc_collect_ms": 0.0},
        ]
        columns = ["gc_allocated_bytes", "gc_collect_ms"]
        collector = {"gc_allocated_bytes_source": None}
        absent = metric_status("job_wait_ms", columns, rows, collector)
        unavailable = metric_status("gc_allocated_bytes", columns, rows, collector)
        zeros = metric_status("gc_collect_ms", columns, rows, collector)
        self.assertEqual(absent["availability"], "field_absent")
        self.assertEqual(unavailable["availability"], "declared_unavailable")
        self.assertEqual(zeros["availability"], "available")
        self.assertEqual(zeros["zero_count"], 2)

    def test_incremental_gc_frames_merge_into_one_window(self):
        rows = [
            {"frame_index": 9, "gc_collect_ms": 0.0, "gc_gen0_collections": 0, "gc_used_bytes": 200},
            {"frame_index": 10, "gc_collect_ms": 0.9, "gc_gen0_collections": 0, "gc_used_bytes": 190},
            {"frame_index": 11, "gc_collect_ms": 0.4, "gc_gen0_collections": 1, "gc_used_bytes": 120},
            {"frame_index": 12, "gc_collect_ms": 0.0, "gc_gen0_collections": 0, "gc_used_bytes": 121},
        ]
        windows = gc_windows(rows)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["start_frame_index"], 10)
        self.assertEqual(windows[0]["end_frame_index"], 11)
        self.assertEqual(windows[0]["completion_frame_index"], 11)
        self.assertAlmostEqual(windows[0]["marker_total_ms"], 1.3)
        self.assertEqual(windows[0]["generation_collection_counts"]["gen0"], 1)

    def test_column_name_parser_accepts_old_shape(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "frames.csv"
            path.write_text(
                "run_id,timestamp_ms,frame_time_ms,memory_used_bytes\n"
                "one,0,16.5,100\n",
                encoding="utf-8",
            )
            rows, columns = read_frames(path)
            self.assertEqual(columns[0], "run_id")
            self.assertEqual(rows[0]["frame_time_ms"], 16.5)
            self.assertNotIn("frame_index", rows[0])


@unittest.skipUnless(REAL_RUNS.exists(), "real stage-two artifacts are not present")
class StageTwoRealArtifactTests(unittest.TestCase):
    def analyze(self, run_id):
        return analyze_run(REAL_RUNS / run_id)

    def test_release_unavailable_fields_do_not_invalidate_base_run(self):
        result = self.analyze("2e671632-f3d2-493a-abb0-7758041b5213")
        self.assertTrue(result["analysis_eligible"])
        self.assertEqual(
            result["metrics"]["gc_allocated_bytes"]["availability"],
            "declared_unavailable",
        )
        self.assertEqual(
            result["metrics"]["script_update_ms"]["availability"],
            "declared_unavailable",
        )

    def test_development_031_gc_window_and_legal_tail(self):
        result = self.analyze("5d3cd807-5c58-4804-b77f-f49faaa4554c")
        window = next(
            item for item in result["gc_windows"]
            if item["start_frame_index"] == 757
        )
        self.assertEqual(window["end_frame_index"], 758)
        self.assertEqual(window["completion_frame_index"], 758)
        self.assertAlmostEqual(window["marker_total_ms"], 1.3679, places=4)
        self.assertEqual(window["generation_collection_counts"]["gen0"], 1)
        self.assertEqual(result["quality"]["issues"], [])
        self.assertNotIn(
            "unexpected_missing_values:gc_collect_ms", result["quality"]["warnings"]
        )
        self.assertEqual(
            result["metrics"]["gc_gen1_collections"]["availability"],
            "declared_unsupported",
        )

    def test_old_alignment_cpu_spike_is_not_attributed_to_gc(self):
        result = self.analyze("702a60cc-4c01-4334-bf6c-6cb65f8f8d2a")
        evidence = next(
            item for item in result["anomaly_frames"]
            if item["frame_index"] == 377
        )
        self.assertFalse(evidence["gc_window_active"])
        self.assertEqual(
            evidence["attribution"]["category"], "cpu_or_main_thread_side"
        )


if __name__ == "__main__":
    unittest.main()
