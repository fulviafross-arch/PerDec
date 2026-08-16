from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import analyze_artifacts, analyze_run
from .report import render_collection_html, write_single_run_report
from .selection import RunSelectionError, select_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze eligible PerfGuardian runs with Collector quality metadata"
    )
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--run-id", help="Analyze exactly one eligible run")
    selection.add_argument("--latest", action="store_true", help="Analyze the eligible run with the latest RFC 3339 runner completion time")
    return parser


def main(arguments: list[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    if args.run_id or args.latest:
        try:
            run_dir, runner_report, selection = select_run(
                args.artifacts_root, run_id=args.run_id, latest=args.latest
            )
            analysis = analyze_run(run_dir, runner_report)
            json_path, html_path, result = write_single_run_report(
                analysis, args.output_dir, selection
            )
        except (OSError, ValueError, RunSelectionError) as exc:
            build_parser().error(str(exc))
        print(json.dumps({
            "run_id": result["run_id"],
            "analysis_eligible": result["analysis_eligible"],
            "incident_count": result["diagnostic_summary"]["incident_count"],
            "actionable_issue_count": result["diagnostic_summary"]["actionable_issue_count"],
            "severe_hitch_count": result["diagnostic_summary"]["severe_hitch_count"],
            "hitch_count": result["diagnostic_summary"]["hitch_count"],
            "budget_miss_count": result["diagnostic_summary"]["budget_miss_count"],
            "pacing_state_count": result["diagnostic_summary"]["pacing_state_count"],
            "analysis_json": str(json_path),
            "analysis_html": str(html_path),
            "selection": result["selection"],
        }, ensure_ascii=False, indent=2))
        return 0
    output = analyze_artifacts(args.artifacts_root)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    (args.output_dir / "analysis.json").write_text(rendered, encoding="utf-8")
    (args.output_dir / "analysis.html").write_text(render_collection_html(output), encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
