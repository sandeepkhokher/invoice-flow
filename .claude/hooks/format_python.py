#!/usr/bin/env python3
"""PostToolUse hook for Edit/Write: format and lint-fix the edited Python file only.

Fast and scoped to one file. Remaining lint errors go back to Claude (exit 2) so it fixes them.
"""

import json
import subprocess
import sys


def main() -> int:
    event = json.load(sys.stdin)
    path = event.get("tool_input", {}).get("file_path", "")
    if not path.endswith(".py"):
        return 0
    subprocess.run(["uv", "run", "-q", "ruff", "format", "-q", path], capture_output=True)
    check = subprocess.run(
        ["uv", "run", "-q", "ruff", "check", "--fix", "-q", path], capture_output=True, text=True
    )
    if check.returncode != 0:
        print(f"ruff found issues in {path}:\n{check.stdout}{check.stderr}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
