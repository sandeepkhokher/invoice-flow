"""Ticket runner: builds GitHub issues labelled `agent:ready` with headless Claude Code.

Plumbing is deterministic (this script); judgment is Claude's (one `claude -p` session per
ticket, in its own git worktree). Flow per issue (plan.md, A5):

  agent:ready -> claim (agent:working) -> worktree from origin/main -> claude -p
  -> runner re-runs `make lint` + `make test` itself (independent gate)
  -> pass: push branch, open PR "Closes #N", label agent:pr-open
  -> fail: label agent:blocked, comment the reason; worktree kept for inspection

Usage:
  uv run python scripts/ticket_runner.py            # one pass
  uv run python scripts/ticket_runner.py --loop     # poll every 2 minutes
  uv run python scripts/ticket_runner.py --dry-run  # show what would be picked up
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKTREES = ROOT / ".worktrees"
PROMPT_TEMPLATE = ROOT / ".claude" / "runner-prompt.md"
READY, WORKING, PR_OPEN, BLOCKED = "agent:ready", "agent:working", "agent:pr-open", "agent:blocked"
CLAUDE_TIMEOUT_S = 60 * 60
GATE_TIMEOUT_S = 15 * 60


@dataclass
class Issue:
    number: int
    title: str
    body: str
    author: str
    labels: list[str]

    @property
    def sid(self) -> str:
        """Slice ID from the title, e.g. 'S4' from 'S4: Synthetic invoice generator'."""
        m = re.match(r"\s*([A-Z]\d+)\s*:", self.title)
        return m.group(1) if m else f"#{self.number}"

    @property
    def branch(self) -> str:
        return f"ticket/{self.number}-{slugify(self.title)}"


@dataclass
class Result:
    ok: bool
    summary: str
    cost_usd: float | None = None
    gate_output: str = ""


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "ticket"


def run(cmd: list[str], cwd: Path = ROOT, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def gh(*args: str) -> str:
    proc = run(["gh", *args])
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def repo_owner() -> str:
    return json.loads(gh("repo", "view", "--json", "owner"))["owner"]["login"]


def select_issues(raw: list[dict], allowed_authors: set[str], limit: int) -> list[Issue]:
    """Ready issues, oldest first, written by an allowed author (issue bodies become prompts,
    so on a public repo only trusted authors' issues are built)."""
    issues = [
        Issue(
            number=i["number"],
            title=i["title"],
            body=i.get("body") or "",
            author=i["author"]["login"],
            labels=[label["name"] for label in i.get("labels", [])],
        )
        for i in raw
    ]
    picked = [
        i
        for i in issues
        if READY in i.labels and WORKING not in i.labels and i.author in allowed_authors
    ]
    return sorted(picked, key=lambda i: i.number)[:limit]


def build_prompt(issue: Issue, template: str) -> str:
    return (
        template.replace("{number}", str(issue.number))
        .replace("{title}", issue.title)
        .replace("{sid}", issue.sid)
        .replace("{body}", issue.body)
    )


def parse_claude_output(stdout: str) -> tuple[bool, str, float | None]:
    """Parse `claude -p --output-format json`. Returns (ok, result text, cost)."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return False, f"Could not parse Claude output:\n{stdout[-2000:]}", None
    ok = not data.get("is_error", False) and data.get("subtype", "success") == "success"
    return ok, str(data.get("result", "")), data.get("total_cost_usd")


def tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def set_labels(number: int, add: str, remove: str) -> None:
    gh("issue", "edit", str(number), "--add-label", add, "--remove-label", remove)


def comment(number: int, text: str) -> None:
    gh("issue", "comment", str(number), "--body", text)


def prepare_worktree(issue: Issue) -> Path:
    path = WORKTREES / f"issue-{issue.number}"
    run(["git", "fetch", "-q", "origin", "main"])
    proc = run(["git", "worktree", "add", "-q", str(path), "-b", issue.branch, "origin/main"])
    if proc.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {proc.stderr.strip()}")
    sync = run(["uv", "sync", "-q"], cwd=path, timeout=600)
    if sync.returncode != 0:
        raise RuntimeError(f"uv sync failed: {sync.stderr.strip()}")
    return path


def gate(path: Path) -> tuple[bool, str]:
    """The runner's own check, independent of what the session reported."""
    out = []
    for target in ("lint", "test"):
        proc = run(["make", target], cwd=path, timeout=GATE_TIMEOUT_S)
        out.append(f"$ make {target}\n{tail(proc.stdout + proc.stderr, 15)}")
        if proc.returncode != 0:
            return False, "\n\n".join(out)
    return True, "\n\n".join(out)


def build(issue: Issue, path: Path, template: str) -> Result:
    try:
        proc = run(
            [
                "claude",
                "-p",
                build_prompt(issue, template),
                "--permission-mode",
                "auto",
                "--output-format",
                "json",
            ],
            cwd=path,
            timeout=CLAUDE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return Result(False, f"Claude session timed out after {CLAUDE_TIMEOUT_S // 60} minutes.")
    ok, summary, cost = parse_claude_output(proc.stdout)
    if not ok:
        return Result(False, summary or tail(proc.stderr), cost)
    commits = run(["git", "rev-list", "--count", "origin/main..HEAD"], cwd=path).stdout.strip()
    if commits in ("", "0"):
        return Result(False, f"Session finished without committing anything.\n\n{summary}", cost)
    passed, gate_output = gate(path)
    if not passed:
        return Result(False, f"Runner gate failed.\n\n{summary}", cost, gate_output)
    return Result(True, summary, cost, gate_output)


def open_pr(issue: Issue, path: Path, result: Result) -> str:
    push = run(["git", "push", "-q", "-u", "origin", issue.branch], cwd=path)
    if push.returncode != 0:
        raise RuntimeError(f"git push failed: {push.stderr.strip()}")
    cost = f"${result.cost_usd:.2f}" if result.cost_usd is not None else "n/a"
    body = (
        f"Closes #{issue.number}\n\nBuilt unattended by the ticket runner (`claude -p`, "
        f"auto mode) in its own worktree. Session cost: {cost}.\n\n## Session summary\n"
        f"{result.summary}\n\n## Runner gate (re-run independently)\n```\n"
        f"{result.gate_output}\n```\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    )
    return gh(
        "pr",
        "create",
        "--base",
        "main",
        "--head",
        issue.branch,
        "--label",
        "stage:build",
        "--title",
        issue.title,
        "--body",
        body,
    ).strip()


def process(issue: Issue, path: Path, template: str) -> None:
    result = build(issue, path, template)
    if result.ok:
        url = open_pr(issue, path, result)
        set_labels(issue.number, PR_OPEN, WORKING)
        comment(issue.number, f"Runner opened {url}")
        run(["git", "worktree", "remove", "--force", str(path)])
        print(f"#{issue.number}: PR {url}")
    else:
        set_labels(issue.number, BLOCKED, WORKING)
        comment(
            issue.number,
            f"Runner could not complete this ticket. Worktree kept at `{path}`.\n\n"
            f"{result.summary[-3000:]}\n\n```\n{result.gate_output[-3000:]}\n```",
        )
        print(f"#{issue.number}: blocked")


def one_pass(max_parallel: int, dry_run: bool) -> int:
    raw = json.loads(
        gh(
            "issue",
            "list",
            "--state",
            "open",
            "--label",
            READY,
            "--limit",
            "50",
            "--json",
            "number,title,body,author,labels",
        )
    )
    issues = select_issues(raw, {repo_owner()}, max_parallel)
    if dry_run or not issues:
        for i in issues:
            print(f"would build #{i.number} {i.title} on {i.branch}")
        if not issues:
            print("no ready issues")
        return len(issues)
    template = PROMPT_TEMPLATE.read_text()
    jobs = []
    for issue in issues:  # claim and create worktrees one at a time (git locks)
        set_labels(issue.number, WORKING, READY)
        comment(issue.number, f"Runner picked this up on branch `{issue.branch}`.")
        try:
            jobs.append((issue, prepare_worktree(issue)))
        except Exception as exc:
            set_labels(issue.number, BLOCKED, WORKING)
            comment(issue.number, f"Runner could not prepare a worktree: {exc}")
    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        for future in [pool.submit(process, i, p, template) for i, p in jobs]:
            future.result()
    return len(jobs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--loop", action="store_true", help="poll every --interval seconds")
    parser.add_argument("--interval", type=int, default=120)
    parser.add_argument("--max-parallel", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    while True:
        one_pass(args.max_parallel, args.dry_run)
        if not args.loop:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
