from __future__ import annotations

import csv
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .windows_process import WindowsProcessSampler


PROCESS_COLUMNS = (
    "timestamp_ms",
    "process_cpu_percent",
    "working_set_bytes",
    "private_bytes",
    "thread_count",
    "io_read_bytes",
    "io_write_bytes",
    "io_read_rate_bytes_per_sec",
    "io_write_rate_bytes_per_sec",
    "process_alive",
    "is_foreground",
    "is_minimized",
    "page_fault_count",
    "page_fault_rate_per_sec",
)


def derive_interval_metrics(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    elapsed_seconds: float | None,
    logical_cpu_count: int | None,
) -> dict[str, float | None]:
    result = {
        "process_cpu_percent": None,
        "io_read_rate_bytes_per_sec": None,
        "io_write_rate_bytes_per_sec": None,
        "page_fault_rate_per_sec": None,
    }
    if previous is None or elapsed_seconds is None or elapsed_seconds <= 0:
        return result
    cpu_delta = _counter_delta(current, previous, "cpu_time_seconds")
    if cpu_delta is not None and logical_cpu_count and logical_cpu_count > 0:
        result["process_cpu_percent"] = max(
            0.0, cpu_delta / elapsed_seconds / logical_cpu_count * 100.0
        )
    for counter, rate in (
        ("io_read_bytes", "io_read_rate_bytes_per_sec"),
        ("io_write_bytes", "io_write_rate_bytes_per_sec"),
        ("page_fault_count", "page_fault_rate_per_sec"),
    ):
        delta = _counter_delta(current, previous, counter)
        if delta is not None:
            result[rate] = delta / elapsed_seconds
    return result


class ExternalProcessMonitor:
    """Low-overhead PoC sampler bound to the PID returned by Popen."""

    def __init__(
        self,
        *,
        pid: int,
        output_path: Path,
        sample_interval_ms: int = 100,
        run_clock_origin: float | None = None,
        sampler: Any | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if sample_interval_ms <= 0:
            raise ValueError("sample_interval_ms must be positive")
        self.pid = pid
        self.output_path = output_path
        self.sample_interval_ms = sample_interval_ms
        self._monotonic = monotonic
        self._origin = run_clock_origin if run_clock_origin is not None else monotonic()
        self._sampler = sampler if sampler is not None else WindowsProcessSampler(pid)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.sample_count = 0
        self.warnings: list[str] = []
        self.first_timestamp_ms: float | None = None
        self.last_timestamp_ms: float | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("monitor already started")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", encoding="utf-8", newline="") as handle:
            csv.DictWriter(handle, fieldnames=PROCESS_COLUMNS).writeheader()
        self._thread = threading.Thread(
            target=self._run,
            name=f"perfguardian-process-monitor-{self.pid}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout_seconds: float = 5.0) -> dict[str, Any]:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout_seconds)
            if self._thread.is_alive():
                self.warnings.append("monitor_thread_did_not_stop_within_timeout")
        try:
            self._sampler.close()
        except Exception as exc:
            self.warnings.append(f"sampler_close_failed:{type(exc).__name__}:{exc}")
        coverage = None
        if self.first_timestamp_ms is not None and self.last_timestamp_ms is not None:
            coverage = max(0.0, self.last_timestamp_ms - self.first_timestamp_ms)
        return {
            "status": "completed" if not self.warnings else "completed_with_warnings",
            "pid": self.pid,
            "sample_interval_ms": self.sample_interval_ms,
            "sample_count": self.sample_count,
            "coverage_ms": coverage,
            "warnings": list(self.warnings),
        }

    def _run(self) -> None:
        previous: dict[str, Any] | None = None
        previous_clock: float | None = None
        interval_seconds = self.sample_interval_ms / 1000.0
        next_deadline = self._monotonic()
        try:
            with self.output_path.open("a", encoding="utf-8", newline="", buffering=1) as handle:
                writer = csv.DictWriter(handle, fieldnames=PROCESS_COLUMNS)
                while not self._stop_event.is_set():
                    sample_clock = self._monotonic()
                    try:
                        raw = self._sampler.sample()
                        rates = derive_interval_metrics(
                            raw,
                            previous,
                            sample_clock - previous_clock if previous_clock is not None else None,
                            os.cpu_count(),
                        )
                        timestamp_ms = (sample_clock - self._origin) * 1000.0
                        row = {column: None for column in PROCESS_COLUMNS}
                        row.update(raw)
                        row.update(rates)
                        row["timestamp_ms"] = timestamp_ms
                        writer.writerow({key: _csv_value(row.get(key)) for key in PROCESS_COLUMNS})
                        handle.flush()
                        self.sample_count += 1
                        self.first_timestamp_ms = timestamp_ms if self.first_timestamp_ms is None else self.first_timestamp_ms
                        self.last_timestamp_ms = timestamp_ms
                        previous, previous_clock = raw, sample_clock
                        if raw.get("process_alive") is False:
                            break
                    except Exception as exc:
                        self.warnings.append(f"sampling_failed:{type(exc).__name__}:{exc}")
                    next_deadline += interval_seconds
                    remaining = next_deadline - self._monotonic()
                    if remaining <= 0:
                        next_deadline = self._monotonic()
                        remaining = 0
                    self._stop_event.wait(remaining)
        except Exception as exc:
            self.warnings.append(f"monitor_failed:{type(exc).__name__}:{exc}")


def _counter_delta(current: dict[str, Any], previous: dict[str, Any], field: str) -> float | None:
    left, right = current.get(field), previous.get(field)
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    return max(0.0, float(left) - float(right))


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return value
