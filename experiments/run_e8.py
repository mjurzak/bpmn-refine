"""Run or analyze the provider-free E8 rehearsal."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.e8_runner import DEFAULT_E8_CONCURRENCY, analyze_e8, run_e8
from app.experiments import ExperimentConfig


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        help="JSON ExperimentConfig frozen after E1-E3",
    )
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_E8_CONCURRENCY,
        help=f"maximum concurrent cases (default: {DEFAULT_E8_CONCURRENCY})",
    )
    parser.add_argument(
        "--selection",
        type=Path,
        help="JSON file containing selected_case_ids for a frozen cost-limited slice",
    )
    args = parser.parse_args()
    if args.analyze:
        result = analyze_e8(args.out)
    else:
        config = (
            ExperimentConfig.model_validate_json(
                args.config.read_text(encoding="utf-8")
            )
            if args.config
            else ExperimentConfig()
        )
        selected_case_ids = None
        if args.selection:
            selection = json.loads(args.selection.read_text(encoding="utf-8"))
            selected_case_ids = selection["selected_case_ids"]
        result = asyncio.run(
            run_e8(
                args.cases,
                args.out,
                root=args.root,
                config=config,
                mock=args.mock,
                resume=not args.no_resume,
                concurrency=args.concurrency,
                selected_case_ids=selected_case_ids,
            )
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
