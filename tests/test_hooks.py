"""The guardrail hooks block what they must and allow normal work (plan.md A3)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".claude" / "hooks"


def run_hook(name: str, event: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env={"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": "/usr/bin:/bin"},
    )


def edit(path: str, content: str = "x = 1\n") -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": str(ROOT / path), "content": content}}


@pytest.mark.parametrize(
    "path",
    [
        "intent/0001-invoice-processing/intent.md",
        "intent/0001-invoice-processing/spec.md",
        ".claude/skills/data-protection/SKILL.md",
        ".claude/hooks/guard_edits.py",
        ".claude/settings.json",
    ],
)
def test_protected_paths_are_blocked(path):
    result = run_hook("guard_edits.py", edit(path))
    assert result.returncode == 2
    assert "protected" in result.stderr


@pytest.mark.parametrize(
    "path", ["app/main.py", "intent/0001-invoice-processing/plan.md", "CLAUDE.md"]
)
def test_normal_paths_are_allowed(path):
    assert run_hook("guard_edits.py", edit(path)).returncode == 0


@pytest.mark.parametrize(
    "secret",
    [
        "KEY = 'sk-ant-api03-abcdefghijklmnopqrstuvwxyz'",
        "-----BEGIN RSA PRIVATE KEY-----",
        "aws = 'AKIAABCDEFGHIJKLMNOP'",
    ],
)
def test_secrets_in_content_are_blocked(secret):
    result = run_hook("guard_edits.py", edit("app/config.py", secret))
    assert result.returncode == 2
    assert "secret" in result.stderr


def test_multiedit_content_is_scanned():
    event = {
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": str(ROOT / "app/x.py"),
            "edits": [{"old_string": "a", "new_string": "t='sk-ant-api03-abcdefghijklmnopqrst'"}],
        },
    }
    assert run_hook("guard_edits.py", event).returncode == 2


@pytest.mark.parametrize(
    "command",
    [
        "cat .env",
        "grep KEY .env.local",
        "git push origin main",
        "git push --force origin ticket/1-x",
        "gh pr merge 12 --squash",
        "gh repo delete sandeepkhokher/invoice-flow",
    ],
)
def test_dangerous_bash_is_blocked(command):
    event = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert run_hook("guard_bash.py", event).returncode == 2


@pytest.mark.parametrize(
    "command",
    ["make test", "git push -u origin ticket/7-gst", "cp .env.example /tmp/x", "uv run pytest"],
)
def test_normal_bash_is_allowed(command):
    event = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert run_hook("guard_bash.py", event).returncode == 0


def test_reading_env_file_is_blocked_but_example_allowed():
    blocked = {"tool_name": "Read", "tool_input": {"file_path": str(ROOT / ".env")}}
    allowed = {"tool_name": "Read", "tool_input": {"file_path": str(ROOT / ".env.example")}}
    assert run_hook("guard_bash.py", blocked).returncode == 2
    assert run_hook("guard_bash.py", allowed).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "echo 'x' >> intent/0001-invoice-processing/spec.md",
        "sed -i '' 's/a/b/' intent/0001-invoice-processing/intent.md",
        "rm -rf .claude/hooks/",
        "cp /tmp/x .claude/settings.json",
        "tee .claude/skills/data-protection/SKILL.md < /tmp/x",
        "python3 -c \"open('intent/0001-invoice-processing/spec.md','a').write('x')\"",
    ],
)
def test_shell_writes_to_protected_files_are_blocked(command):
    event = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert run_hook("guard_bash.py", event).returncode == 2


@pytest.mark.parametrize(
    "command",
    [
        "tail -5 intent/0001-invoice-processing/spec.md",
        "grep FR-2 intent/0001-invoice-processing/spec.md",
        "echo done > /tmp/out.txt",
        "sed -i '' 's/a/b/' intent/0001-invoice-processing/plan.md",
    ],
)
def test_reads_of_protected_files_and_other_writes_are_allowed(command):
    event = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert run_hook("guard_bash.py", event).returncode == 0
