"""CLI for deterministic E8 dataset construction."""

from __future__ import annotations

import argparse
from pathlib import Path

from evaluation.enhancement.builder import build_enhancement_dataset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("build", nargs="?", default="build", choices=("build",))
    parser.add_argument("--dataset-root", type=Path, default=Path("data/eval/v1.0"))
    parser.add_argument("--out", type=Path, default=Path("data/eval/enhancement"))
    parser.add_argument("--seed", dest="seed_ids", action="append")
    parser.add_argument("--no-tier2", action="store_true")
    args = parser.parse_args()
    manifest = build_enhancement_dataset(
        dataset_root=args.dataset_root,
        out=args.out,
        seed_ids=tuple(args.seed_ids) if args.seed_ids else None,
        verify_tier2=not args.no_tier2,
    )
    print(f"wrote {len(manifest.cases)} enhancement cases to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
