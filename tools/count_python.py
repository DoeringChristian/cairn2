"""Enforce the physical-line budget for all handwritten Cairn runtime Python."""

import sys
from pathlib import Path

MAX_LINES = 9_000


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    package = root / "cairn"
    files = sorted(path for path in package.rglob("*.py") if path.is_file())
    if not files:
        print(f"No runtime Python files found under {package}", file=sys.stderr)
        return 2
    total = 0
    for path in files:
        count = len(path.read_text(encoding="utf-8").splitlines())
        total += count
        print(f"{count:6d}  {path.relative_to(root).as_posix()}")
    print(f"{total:6d}  TOTAL ({MAX_LINES:,} physical-line limit)")
    if total > MAX_LINES:
        print(f"FAIL: runtime exceeds the limit by {total - MAX_LINES:,} lines.", file=sys.stderr)
        return 1
    print(f"PASS: {MAX_LINES - total:,} lines remain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
