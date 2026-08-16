from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunSelectionError(ValueError):
    pass


def select_run(
    artifacts_root: Path,
    *,
    run_id: str | None = None,
    latest: bool = False,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    if bool(run_id) == bool(latest):
        raise RunSelectionError("Select exactly one of --run-id or --latest.")
    candidates = _runner_reports(artifacts_root)
    if run_id:
        matches = [item for item in candidates if item[1].get("run_id") == run_id]
        if not matches:
            raise RunSelectionError(f"Run ID not found: {run_id}")
        if len(matches) > 1:
            paths = ", ".join(str(item[0]) for item in matches)
            raise RunSelectionError(f"Run ID is duplicated: {run_id}; reports: {paths}")
        path, report = matches[0]
        if not report.get("eligible_for_analysis"):
            reasons = report.get("eligibility_reasons") or [report.get("runner_status")]
            raise RunSelectionError(f"Run is not eligible for analysis: {run_id}; reasons={reasons}")
        return path.parent, report, {
            "mode": "run_id",
            "requested_run_id": run_id,
            "selected_run_id": run_id,
            "rule": "exact runner-report.json run_id match; must be unique and eligible",
        }

    eligible = []
    invalid_timestamps = []
    for path, report in candidates:
        if not report.get("eligible_for_analysis"):
            continue
        completed_at = report.get("completed_at")
        try:
            instant = _parse_rfc3339(completed_at)
        except (TypeError, ValueError):
            invalid_timestamps.append(str(path))
            continue
        eligible.append((instant, str(report.get("run_id") or ""), path, report))
    if not eligible:
        detail = f" Invalid completed_at reports: {invalid_timestamps}" if invalid_timestamps else ""
        raise RunSelectionError("No eligible run with a valid completed_at timestamp was found." + detail)
    eligible.sort(key=lambda item: (item[0], item[1]))
    instant, selected_id, path, report = eligible[-1]
    return path.parent, report, {
        "mode": "latest",
        "selected_run_id": selected_id,
        "selected_completed_at": instant.isoformat().replace("+00:00", "Z"),
        "eligible_timestamped_candidate_count": len(eligible),
        "excluded_invalid_timestamp_count": len(invalid_timestamps),
        "rule": "maximum eligible runner-report.completed_at parsed as RFC 3339 UTC; ties resolved by lexicographically greatest run_id",
    }


def _runner_reports(artifacts_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    output = []
    for path in sorted(artifacts_root.rglob("runner-report.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RunSelectionError(f"Cannot parse runner report {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise RunSelectionError(f"Runner report root must be an object: {path}")
        output.append((path, value))
    return output


def _parse_rfc3339(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("missing timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)
