from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# This fallback also makes VS Code's "Run Python File" button usable.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from runner.core import RunConfig, run_once
else:
    from .core import RunConfig, run_once


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "perfguardian.local.json"
REQUIRED_FIELDS = (
    "exe",
    "project_id",
    "experiment_id",
    "experiment_version",
    "scenario_id",
    "scenario_version",
    "commit_sha",
    "branch",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run and validate Unity PerfGuardian captures",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="JSON configuration file",
    )
    parser.add_argument("--exe", type=Path)
    parser.add_argument("--project-id")
    parser.add_argument("--experiment-id")
    parser.add_argument("--experiment-version")
    parser.add_argument("--scenario-id")
    parser.add_argument("--scenario-version")
    parser.add_argument("--commit-sha")
    parser.add_argument("--branch")
    parser.add_argument("--warmup-seconds", type=float)
    parser.add_argument("--measurement-seconds", type=float)
    parser.add_argument("--sample-interval-ms", type=int)
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--artifacts-root", type=Path)
    return parser


def load_config(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"Configuration file not found: {path}. "
            "Copy perfguardian.example.json to perfguardian.local.json first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON configuration {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("The configuration root must be a JSON object.")
    unknown = sorted(set(data) - set(RunConfig.__dataclass_fields__))
    if unknown:
        raise ValueError(f"Unknown configuration fields: {', '.join(unknown)}")
    return data


def config_from_args(arguments: list[str] | None = None) -> RunConfig:
    parser = build_parser()
    args = parser.parse_args(arguments)
    try:
        values = load_config(args.config)
    except ValueError as exc:
        parser.error(str(exc))

    overrides = vars(args)
    overrides.pop("config")
    values.update({key: value for key, value in overrides.items() if value is not None})

    missing = [name for name in REQUIRED_FIELDS if values.get(name) in (None, "")]
    if missing:
        parser.error(f"Missing required configuration fields: {', '.join(missing)}")

    values.setdefault("warmup_seconds", 1)
    values.setdefault("measurement_seconds", 5)
    values.setdefault("sample_interval_ms", 16)
    values.setdefault("repetitions", 1)
    values.setdefault("artifacts_root", str(PROJECT_ROOT / "artifacts"))
    values["exe"] = Path(values["exe"])
    values["artifacts_root"] = Path(values["artifacts_root"])
    return RunConfig(**values)


def main(arguments: list[str] | None = None) -> int:
    config = config_from_args(arguments)
    if not config.exe.is_file():
        build_parser().error(f"Unity executable not found: {config.exe}")
    if config.repetitions < 1:
        build_parser().error("repetitions must be at least 1")
    reports = [run_once(config, index) for index in range(1, config.repetitions + 1)]
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0 if all(report["eligible_for_analysis"] for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
