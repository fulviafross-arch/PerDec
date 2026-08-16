import csv
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from analyzer.core import analyze_run
from analyzer.external_process import (
    attach_process_evidence,
    load_external_process,
    summarize_process_window,
)
from analyzer.report import render_single_run_html
from runner.core import align_process_timestamps
from runner.external_monitor import ExternalProcessMonitor, derive_interval_metrics


class FakeSampler:
    def __init__(self):
        self.calls = 0
        self.closed = False
        self.two_samples = threading.Event()

    def sample(self):
        self.calls += 1
        if self.calls >= 2:
            self.two_samples.set()
        return {
            "cpu_time_seconds": self.calls * 0.01,
            "working_set_bytes": 1000 + self.calls,
            "private_bytes": 800 + self.calls,
            "thread_count": 4,
            "io_read_bytes": self.calls * 100,
            "io_write_bytes": self.calls * 50,
            "process_alive": True,
            "is_foreground": True,
            "is_minimized": False,
            "page_fault_count": self.calls,
        }

    def close(self):
        self.closed = True


def write_process_csv(path, rows, fields=None):
    fields = fields or list(rows[0])
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


class MonitorLifecycleTests(unittest.TestCase):
    def test_start_sample_stop_flush_and_header(self):
        with tempfile.TemporaryDirectory() as root:
            sampler = FakeSampler()
            output = Path(root) / "process.csv"
            monitor = ExternalProcessMonitor(
                pid=123, output_path=output, sample_interval_ms=5, sampler=sampler
            )
            monitor.start()
            self.assertTrue(sampler.two_samples.wait(1))
            result = monitor.stop()
            self.assertFalse(monitor._thread.is_alive())
            self.assertTrue(sampler.closed)
            self.assertGreaterEqual(result["sample_count"], 2)
            with output.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
                self.assertIn("process_cpu_percent", rows[0])
                self.assertEqual(rows[0]["io_read_rate_bytes_per_sec"], "")

    def test_interval_rates_and_first_sample_are_not_fabricated(self):
        first = derive_interval_metrics({"cpu_time_seconds": 1, "io_read_bytes": 10}, None, None, 4)
        self.assertIsNone(first["process_cpu_percent"])
        self.assertIsNone(first["io_read_rate_bytes_per_sec"])
        result = derive_interval_metrics(
            {"cpu_time_seconds": 1.4, "io_read_bytes": 210, "io_write_bytes": 50},
            {"cpu_time_seconds": 1.0, "io_read_bytes": 10, "io_write_bytes": 10},
            0.2, 4,
        )
        self.assertAlmostEqual(result["process_cpu_percent"], 50)
        self.assertAlmostEqual(result["io_read_rate_bytes_per_sec"], 1000)
        self.assertAlmostEqual(result["io_write_rate_bytes_per_sec"], 200)

    def test_timestamp_alignment_uses_measurement_started(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            process = root / "process.csv"
            write_process_csv(process, [{"timestamp_ms": 900}, {"timestamp_ms": 1100}], ["timestamp_ms"])
            (root / "events.jsonl").write_text(
                json.dumps({"event_type": "measurement_started", "recorded_at": "2026-01-01T00:00:01Z"}) + "\n",
                encoding="utf-8",
            )
            result = align_process_timestamps(process, root / "events.jsonl", "2026-01-01T00:00:00Z")
            self.assertEqual(result["status"], "aligned")
            with process.open(encoding="utf-8", newline="") as handle:
                values = [float(row["timestamp_ms"]) for row in csv.DictReader(handle)]
            self.assertEqual(values, [-100, 100])


class ProcessEvidenceTests(unittest.TestCase):
    def sample(self, timestamp, cpu=10, read_rate=100, foreground=True, minimized=False):
        return {
            "timestamp_ms": timestamp, "process_cpu_percent": cpu,
            "working_set_bytes": 1000 + timestamp, "private_bytes": 900 + timestamp,
            "thread_count": 5, "io_read_rate_bytes_per_sec": read_rate,
            "io_write_rate_bytes_per_sec": 50, "is_foreground": foreground,
            "is_minimized": minimized,
        }

    def test_missing_and_partial_process_csv_degrade_gracefully(self):
        with tempfile.TemporaryDirectory() as root:
            metadata, rows = load_external_process(Path(root), None, 5000)
            self.assertEqual(metadata["availability"], "unavailable")
            self.assertEqual(rows, [])
            write_process_csv(
                Path(root) / "process.csv",
                [{"timestamp_ms": 0, "working_set_bytes": 100, "is_foreground": "true"}],
            )
            metadata, rows = load_external_process(
                Path(root), {"external_process_monitor": {"timestamp_alignment": {"status": "synthetic_test"}}}, 5000
            )
            self.assertEqual(metadata["availability"], "available")
            self.assertIsNone(rows[0].get("private_bytes"))

    def test_incident_window_and_window_state_summary(self):
        rows = [self.sample(t) for t in (4300, 4500, 5000, 5500, 5700)]
        rows[2].update({"process_cpu_percent": 80, "io_read_rate_bytes_per_sec": 20 * 1024 * 1024, "is_foreground": False})
        rows[3]["is_minimized"] = True
        event = {
            "event_type": "hitch", "start_timestamp_ms": 5000, "end_timestamp_ms": 5000,
            "classification": "unattributed", "diagnosis": {"classification": "unattributed"},
        }
        attach_process_evidence([event], rows)
        evidence = event["process_evidence"]
        self.assertEqual(evidence["sample_count"], 3)
        self.assertIn("process_cpu_elevated", evidence["evidence_flags"])
        self.assertIn("io_read_spike", evidence["evidence_flags"])
        self.assertIn("background_window_state", evidence["evidence_flags"])
        self.assertIn("minimized_window_state", evidence["evidence_flags"])
        self.assertEqual(event["classification"], "unattributed")
        self.assertEqual(event["diagnosis"]["classification"], "unattributed")

    def test_normal_external_data_does_not_reclassify_unattributed(self):
        event = {
            "event_type": "severe_hitch", "start_timestamp_ms": 5000, "end_timestamp_ms": 5000,
            "classification": "unattributed", "diagnosis": {"classification": "unattributed"},
        }
        attach_process_evidence([event], [self.sample(t) for t in (4500, 5000, 5500)])
        self.assertTrue(event["process_evidence"]["no_obvious_process_level_spike"])
        self.assertEqual(event["diagnosis"]["classification"], "unattributed")

    def test_html_shows_process_evidence(self):
        event = {
            "event_id": "incident-1", "event_type": "hitch", "priority": "P1",
            "severity": "major", "is_actionable": True, "start_timestamp_ms": 5,
            "end_timestamp_ms": 5, "peak_frame_time_ms": 40,
            "classification": "unattributed", "confidence": "low",
            "process_evidence": summarize_process_window([self.sample(5)], -495, 505),
        }
        result = {
            "run_id": "run", "run_summary": {}, "diagnostic_summary": {"top_issue_ids": ["incident-1"]},
            "quality": {}, "events": [event], "chart_data": {}, "gc_diagnostics": {},
            "allocation_diagnostics": {}, "memory_diagnostics": {}, "diagnostic_thresholds": {},
            "external_process_monitor": {"availability": "available", "measurement_sample_count": 1, "sample_interval_ms": 100, "timestamp_alignment": {"status": "aligned"}},
        }
        html = render_single_run_html(result)
        self.assertIn("External process telemetry", html)
        self.assertIn("Process Evidence", html)
        self.assertIn("No obvious process-level spike", html)

    def test_process_cpu_limitation_matches_run_availability(self):
        old = "No process CPU utilization or system GPU utilization is available."
        available = {
            "event_type": "budget_miss", "limitations": [old],
            "diagnosis": {"limitations": [old]},
        }
        attach_process_evidence([available], [self.sample(0)], True)
        for values in (available["limitations"], available["diagnosis"]["limitations"]):
            self.assertNotIn(old, values)
            self.assertTrue(any("available as sampled window context" in item for item in values))

        unavailable = {"event_type": "budget_miss", "limitations": [old]}
        attach_process_evidence([unavailable], [], False)
        self.assertIn(
            "Process CPU utilization and system GPU utilization are unavailable.",
            unavailable["limitations"],
        )

    def test_non_actionable_process_evidence_message_uses_run_availability(self):
        event = {
            "event_id": "minor-1", "event_type": "budget_miss", "priority": "P3",
            "severity": "minor", "is_actionable": False, "start_timestamp_ms": 5,
            "end_timestamp_ms": 5, "peak_frame_time_ms": 20,
            "classification": "unattributed", "confidence": "low",
        }
        base = {
            "run_id": "run", "run_summary": {}, "diagnostic_summary": {},
            "quality": {}, "events": [event], "chart_data": {}, "gc_diagnostics": {},
            "allocation_diagnostics": {}, "memory_diagnostics": {}, "diagnostic_thresholds": {},
        }
        available_html = render_single_run_html(base | {
            "external_process_monitor": {"availability": "available"}
        })
        self.assertNotIn("External process telemetry unavailable.", available_html)
        self.assertNotIn("Process evidence is not attached", available_html)

        unavailable_html = render_single_run_html(base | {
            "external_process_monitor": {"availability": "unavailable"}
        })
        self.assertIn("External process telemetry unavailable.", unavailable_html)

    def test_legacy_run_without_process_csv_still_analyzes(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "run.json").write_text(json.dumps({
                "schema_version": "0.1.0-draft", "run_id": "legacy",
                "environment": {"display_refresh_rate_hz": 60, "v_sync_count": 1},
                "collector": {"gc_allocated_bytes_source": "test"},
            }), encoding="utf-8")
            header = "run_id,timestamp_ms,frame_index,frame_time_ms,cpu_frame_time_ms,gpu_frame_time_ms,memory_used_bytes,gc_allocated_bytes\n"
            body = "".join(f"legacy,{i * 17},{i},16.7,10,8,{1000000 + i},100\n" for i in range(20))
            (root / "frames.csv").write_text(header + body, encoding="utf-8")
            result = analyze_run(root)
            self.assertTrue(result["analysis_eligible"])
            self.assertEqual(result["external_process_monitor"]["availability"], "unavailable")


if __name__ == "__main__":
    unittest.main()
