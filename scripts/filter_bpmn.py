"""
filter the SAP-SAM dataset to keep only BPMN rows

reads all CSV files from the input directory, filters by namespace,
and writes the filtered rows to the output directory (one file per input).
chunked reading keeps 10+ GB CSV files within memory.

usage:
    python scripts/filter_bpmn.py \
        --input "input/path" \
        --output "output/path"

by default only BPMN 2.0 is kept; pass --all-bpmn to include
BPMN 1.0, Choreography, and Conversation as well.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

# namespace substrings that identify each BPMN dialect in Signavio's format
BPMN2_MARKERS = ["bpmn2.0#", "bpmn2.0/"]
BPMN_ALL_MARKERS = BPMN2_MARKERS + ["bpmn1.0", "bpmn2.0choreography", "bpmn2.0conversation"]

CHUNK_SIZE = 5_000  # rows per chunk, keeps RAM low for large CSVs


def _is_bpmn(namespace: pd.Series, markers: list[str]) -> pd.Series:
    ns_lower = namespace.fillna("").str.lower()
    mask = pd.Series(False, index=namespace.index)
    for marker in markers:
        mask |= ns_lower.str.contains(marker, regex=False)
    return mask


def filter_file(src: Path, dst: Path, markers: list[str]) -> tuple[int, int]:
    """return (total_rows, kept_rows) for this file"""
    total, kept = 0, 0
    first_chunk = True

    for chunk in pd.read_csv(src, chunksize=CHUNK_SIZE, low_memory=False):
        total += len(chunk)
        filtered = chunk[_is_bpmn(chunk["Namespace"], markers)]
        kept += len(filtered)

        if filtered.empty:
            continue

        filtered.to_csv(
            dst,
            mode="w" if first_chunk else "a",
            header=first_chunk,
            index=False,
        )
        first_chunk = False

    return total, kept


def main() -> None:
    parser = argparse.ArgumentParser(description="filter SAP-SAM CSVs to BPMN-only rows")
    parser.add_argument("--input", required=True, help="directory containing SAP-SAM CSV files")
    parser.add_argument("--output", required=True, help="directory to write filtered CSVs")
    parser.add_argument(
        "--all-bpmn",
        action="store_true",
        help="include BPMN 1.0, Choreography, and Conversation (default: BPMN 2.0 only)",
    )
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)

    if not input_dir.is_dir():
        print(f"error: input directory not found: {input_dir}", file=sys.stderr)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(input_dir.glob("*.csv"))
    if not csv_files:
        print(f"error: no CSV files found in {input_dir}", file=sys.stderr)
        sys.exit(1)

    markers = BPMN_ALL_MARKERS if args.all_bpmn else BPMN2_MARKERS
    label = "all BPMN dialects" if args.all_bpmn else "BPMN 2.0 only"
    print(f"filter mode: {label}")
    print(f"processing {len(csv_files)} files from {input_dir}")
    print(f"output -> {output_dir}\n")

    grand_total, grand_kept = 0, 0

    for i, src in enumerate(csv_files, 1):
        dst = output_dir / src.name
        print(f"[{i:>3}/{len(csv_files)}] {src.name} ... ", end="", flush=True)

        try:
            total, kept = filter_file(src, dst, markers)
        except Exception as exc:
            print(f"FAILED ({exc})")
            continue

        grand_total += total
        grand_kept += kept
        ratio = kept / total * 100 if total else 0
        print(f"{kept:>7,} / {total:>7,} rows kept ({ratio:.1f}%)")

    print(f"\ndone — {grand_kept:,} BPMN rows kept out of {grand_total:,} total ({grand_kept/grand_total*100:.1f}%)")


if __name__ == "__main__":
    main()
