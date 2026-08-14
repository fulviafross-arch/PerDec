from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from .core import analyze_artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze eligible PerfGuardian runs with Collector quality metadata"
    )
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(arguments: list[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    output = analyze_artifacts(args.artifacts_root)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    (args.output_dir / "analysis.json").write_text(rendered, encoding="utf-8")
    (args.output_dir / "analysis.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>PerfGuardian Analysis</title>"
        "<h1>PerfGuardian Analysis</h1><pre>" + html.escape(rendered) + "</pre>",
        encoding="utf-8",
    )
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
