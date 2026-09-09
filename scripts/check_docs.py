"""Check local Markdown links and coverage of the application documentation index."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")
SCAN_ROOTS = ("docs", "evaluation", "experiments", "data")


def markdown_files() -> list[Path]:
    files = [ROOT / "README.md"]
    for relative in SCAN_ROOTS:
        files.extend((ROOT / relative).rglob("*.md"))
    return sorted(set(files))


def local_target(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().strip("<>").split("#", 1)[0]
    if not target or "://" in target or target.startswith("mailto:"):
        return None
    return (source.parent / unquote(target)).resolve()


def check_links(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for source in files:
        for line_number, line in enumerate(
            source.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for raw_target in LINK_RE.findall(line):
                target = local_target(source, raw_target)
                if target is not None and not target.exists():
                    errors.append(
                        f"{source.relative_to(ROOT)}:{line_number}: "
                        f"missing local target {raw_target!r}"
                    )
    return errors


def check_docs_index() -> list[str]:
    index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    errors: list[str] = []
    for document in sorted((ROOT / "docs").glob("*.md")):
        if document.name == "README.md":
            continue
        if f"]({document.name})" not in index:
            errors.append(f"docs/README.md: missing index entry for {document.name}")
    return errors


def main() -> int:
    errors = check_links(markdown_files()) + check_docs_index()
    if errors:
        print("Documentation check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Documentation check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
