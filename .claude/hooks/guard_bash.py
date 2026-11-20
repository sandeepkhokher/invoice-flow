#!/usr/bin/env python3
"""PreToolUse hook for Bash and Read.

Blocks (exit 2): reading .env files; pushing to main; force pushes; merging PRs;
deleting the repo or branches on GitHub; shell writes to protected files (approved artifacts,
policies, guardrails). The agent acts up to the review gate, never past it.

The shell-write check is best-effort pattern matching; the backstop is branch protection and
human review of every PR.
"""

import json
import re
import sys
from pathlib import Path

ENV_READ = re.compile(r"(^|[\s/'\"=<])\.env(\.[A-Za-z0-9_]+)?($|[\s'\";|&>])")
RULES = [
    (
        re.compile(r"\bgit\s+push\b.*\b(main|master)\b"),
        "Pushing to main is not allowed; open a PR.",
    ),
    (re.compile(r"\bgit\s+push\b.*(--force\b|\s-f\b)"), "Force pushes are not allowed."),
    (re.compile(r"\bgh\s+pr\s+merge\b"), "Merging is a human decision; leave the PR for review."),
    (re.compile(r"\bgh\s+repo\s+delete\b"), "Deleting the repository is not allowed."),
    (re.compile(r"\bgit\s+push\b.*\s--delete\b"), "Deleting remote branches is not allowed."),
]


PROTECTED_IN_SHELL = re.compile(
    r"(intent/[^\s'\"]*/(intent|spec)\.md|\.claude/(skills|hooks)/|\.claude/settings\.json)"
)
SHELL_WRITE = re.compile(
    r"(>|\btee\b|\bsed\s+(-[a-zA-Z]*i|--in-place)|\bperl\s+-[a-zA-Z]*i|\brm\b|\bmv\b|\bcp\b"
    r"|\btruncate\b|\bchmod\b|\bln\b|\bgit\s+(checkout|restore|rm|mv)\b|open\(.*['\"][wa])"
)


def main() -> int:
    event = json.load(sys.stdin)
    tool, tool_input = event.get("tool_name"), event.get("tool_input", {})
    if tool == "Read":
        name = Path(tool_input.get("file_path", "")).name
        if name.startswith(".env") and name != ".env.example":
            print("Blocked: .env files hold secrets and must not be read.", file=sys.stderr)
            return 2
        return 0
    command = tool_input.get("command", "")
    if ENV_READ.search(command) and ".env.example" not in command:
        print("Blocked: .env files hold secrets and must not be read.", file=sys.stderr)
        return 2
    if PROTECTED_IN_SHELL.search(command) and SHELL_WRITE.search(command):
        print(
            "Blocked: this command appears to modify a protected file (approved artifact, policy "
            "or guardrail). Those change only through a human-reviewed PR.",
            file=sys.stderr,
        )
        return 2
    for pattern, reason in RULES:
        if pattern.search(command):
            print(f"Blocked: {reason}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
