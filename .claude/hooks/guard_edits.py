#!/usr/bin/env python3
"""PreToolUse hook for Edit/Write/MultiEdit/NotebookEdit.

Blocks (exit 2, reason sent to Claude):
- edits to approved artifacts, policies and guardrails (they change only by a human PR);
- content that looks like a secret.
"""

import json
import os
import re
import sys
from fnmatch import fnmatch
from pathlib import Path

PROTECTED = [
    "intent/*/intent.md",
    "intent/*/spec.md",
    ".claude/skills/*",
    ".claude/hooks/*",
    ".claude/settings.json",
]
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
]


def relative(path: str) -> str:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
    p = Path(path)
    p = p if p.is_absolute() else root / p
    try:
        return p.resolve().relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def new_content(tool_input: dict) -> str:
    parts = [
        tool_input.get("content", ""),
        tool_input.get("new_string", ""),
        tool_input.get("new_source", ""),
    ]
    parts += [e.get("new_string", "") for e in tool_input.get("edits", [])]
    return "\n".join(p for p in parts if p)


def main() -> int:
    event = json.load(sys.stdin)
    tool_input = event.get("tool_input", {})
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    rel = relative(path) if path else ""
    for pattern in PROTECTED:
        if rel and fnmatch(rel, pattern):
            print(
                f"Blocked: {rel} is a protected file (approved artifact, policy or guardrail). "
                "It changes only through a human-reviewed PR. If you believe it must change, "
                "stop and explain why in your summary.",
                file=sys.stderr,
            )
            return 2
    content = new_content(tool_input)
    for pattern in SECRET_PATTERNS:
        if pattern.search(content):
            print(
                "Blocked: the content looks like a secret (API key, token or private key). "
                "Read secrets from environment variables; never write them into files.",
                file=sys.stderr,
            )
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
