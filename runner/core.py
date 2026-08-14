from __future__ import annotations

import csv
import json
import math
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARTIFACTS = ("run.json", "frames.csv", "events.jsonl", "player.log")
METRICS = ("frame_time_ms", "memory_used_bytes", "cpu_frame_time_ms", "gpu_frame_time_ms", "gc_allocated_bytes")
LOG_FAILURES = ("unhandled exception", "exception", "crash", "could not start", "collector startup failed", "outofmemory")


@dataclass(frozen=True)
class RunConfig:
    exe: Path
    project_id: str
    experiment_id: str
    experiment_version: str
    scenario_id: str
    scenario_version: str
    commit_sha: str
    branch: str
    warmup_seconds: float = 1
    measurement_seconds: float = 5
    sample_interval_ms: int = 16
    repetitions: int = 1
    artifacts_root: Path = Path("artifacts")


@dataclass
class Validation:
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sample_count: int = 0
    coverage_ms: float | None = None
    metrics: dict[str, dict[str, int | float]] = field(default_factory=dict)
    log_errors: list[str] = field(default_factory=list)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def create_run_dir(config: RunConfig, run_id: str | None = None) -> tuple[str, Path]:
    run_id = run_id or str(uuid.uuid4())
    path = config.artifacts_root / config.project_id / config.experiment_id / run_id
    path.mkdir(parents=True, exist_ok=False)
    return run_id, path


def command_for(config: RunConfig, run_id: str, run_dir: Path, repetition: int) -> list[str]:
    return [str(config.exe), "-logFile", str(run_dir / "player.log"), "--pg-output", str(run_dir),
            "--pg-run-id", run_id, "--pg-project-id", config.project_id,
            "--pg-experiment-id", config.experiment_id, "--pg-experiment-version", config.experiment_version,
            "--pg-scenario-id", config.scenario_id, "--pg-scenario-version", config.scenario_version,
            "--pg-commit-sha", config.commit_sha, "--pg-branch", config.branch,
            "--pg-warmup-seconds", str(config.warmup_seconds), "--pg-measurement-seconds", str(config.measurement_seconds),
            "--pg-sample-interval-ms", str(config.sample_interval_ms), "--pg-repetition-index", str(repetition),
            "--pg-repetition-count", str(config.repetitions), "--pg-quit-on-complete", "true"]


def validate_artifacts(run_dir: Path, run_id: str, measurement_seconds: float) -> Validation:
    result = Validation(False)
    for name in ARTIFACTS:
        item = run_dir / name
        if not item.is_file() or item.stat().st_size == 0:
            result.reasons.append(f"missing_or_empty:{name}")
    if result.reasons:
        return result
    try:
        manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result.reasons.append(f"invalid_run_json:{exc}")
        return result
    if manifest.get("schema_version") != "0.1.0-draft": result.reasons.append("unsupported_schema")
    if manifest.get("run_id") != run_id: result.reasons.append("manifest_run_id_mismatch")
    if manifest.get("status") != "completed": result.reasons.append(f"manifest_status:{manifest.get('status')}")
    collector = manifest.get("collector") if isinstance(manifest.get("collector"), dict) else {}
    has_top_level_failure = "failure_reason" in manifest
    has_collector_failure = "failure_reason" in collector
    top_level_failure = manifest.get("failure_reason")
    collector_failure = collector.get("failure_reason")
    if not has_top_level_failure and not has_collector_failure:
        result.reasons.append("manifest_failure_reason_missing")
    elif has_top_level_failure and has_collector_failure and top_level_failure != collector_failure:
        result.reasons.append("manifest_failure_reason_conflict")
    elif (top_level_failure if has_top_level_failure else collector_failure) is not None:
        result.reasons.append("manifest_failure_reason")
    try:
        events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        if not events or (events[-1].get("event_type") or events[-1].get("type")) != "run_completed": result.reasons.append("events_not_completed")
    except (OSError, json.JSONDecodeError) as exc: result.reasons.append(f"invalid_events:{exc}")
    previous = -1.0
    try:
        with (run_dir / "frames.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows: result.reasons.append("frames_empty")
        for row in rows:
            if row.get("run_id") != run_id: result.reasons.append("frame_run_id_mismatch"); break
            timestamp = _number(row.get("timestamp_ms"))
            if timestamp is None or timestamp < 0 or timestamp < previous: result.reasons.append("invalid_timestamp"); break
            previous = timestamp
        result.sample_count, result.coverage_ms = len(rows), (previous if rows else None)
        for metric in METRICS:
            raw_values = [row.get(metric) for row in rows]
            values = [_number(value) for value in raw_values]
            if any(_is_invalid_number(value) for value in raw_values):
                result.reasons.append(f"invalid_metric_value:{metric}")
            valid = sum(value is not None for value in values)
            result.metrics[metric] = {"valid": valid, "missing": len(rows)-valid, "missing_ratio": (len(rows)-valid)/len(rows) if rows else 1.0}
            if rows and valid == 0: result.warnings.append(f"all_values_missing:{metric}")
        if previous < measurement_seconds * 1000 * .8: result.reasons.append("insufficient_measurement_coverage")
    except (OSError, csv.Error) as exc: result.reasons.append(f"invalid_frames:{exc}")
    log = (run_dir / "player.log").read_text(encoding="utf-8", errors="replace").lower()
    result.log_errors = [term for term in LOG_FAILURES if term in log]
    if result.log_errors: result.reasons.append("player_log_failure")
    result.eligible = not result.reasons
    return result


def _number(value: Any) -> float | None:
    if value is None or str(value).strip().lower() in ("", "null"): return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError): return None


def _is_invalid_number(value: Any) -> bool:
    """Empty/null is permitted; every other non-finite or nonnumeric value is not."""
    if value is None or str(value).strip().lower() in ("", "null"):
        return False
    return _number(value) is None


def run_once(config: RunConfig, repetition: int) -> dict[str, Any]:
    run_id, run_dir = create_run_dir(config)
    command, started = command_for(config, run_id, run_dir, repetition), utc_now()
    start_clock, timed_out, exit_code = time.monotonic(), False, None
    process = subprocess.Popen(command)
    timeout = 60 + config.warmup_seconds + config.measurement_seconds
    try: exit_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True; process.terminate()
        try: exit_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); exit_code = process.wait()
    validation = validate_artifacts(run_dir, run_id, config.measurement_seconds)
    status = "timeout" if timed_out else ("completed" if exit_code == 0 and validation.eligible else "invalid_artifacts")
    report = {"runner_status": status, "run_id": run_id, "exe": str(config.exe), "command": command,
              "exit_code": exit_code, "started_at": started, "completed_at": utc_now(),
              "duration_seconds": time.monotonic()-start_clock, "timed_out": timed_out,
              "configuration": asdict(config) | {"exe": str(config.exe), "artifacts_root": str(config.artifacts_root)},
              "artifact_directory": str(run_dir), "eligible_for_analysis": status == "completed",
              "eligibility_reasons": validation.reasons, "quality_warnings": validation.warnings,
              "sample_count": validation.sample_count, "measurement_coverage_ms": validation.coverage_ms,
              "metrics": validation.metrics, "log_errors": validation.log_errors}
    (run_dir / "runner-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "runner-report.html").write_text("<html><body><pre>" + json.dumps(report, ensure_ascii=False, indent=2) + "</pre></body></html>", encoding="utf-8")
    return report
