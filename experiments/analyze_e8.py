"""Analyze E8 JSONL output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.e8_runner import analyze_e8


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze_e8(args.results), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
