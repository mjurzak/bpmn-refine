"""Run or analyze the provider-free E8 rehearsal."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.e8_runner import analyze_e8, run_e8
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
    args = parser.parse_args()
    if args.analyze:
        result = analyze_e8(args.out)
    else:
        config = (
            ExperimentConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
            if args.config
            else ExperimentConfig()
        )
        result = asyncio.run(
            run_e8(
                args.cases,
                args.out,
                config=config,
                mock=args.mock,
                resume=not args.no_resume,
            )
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
