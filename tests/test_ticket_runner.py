"""Pure logic of the ticket runner (plan.md A5). GitHub and Claude calls are not exercised."""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ticket_runner", ROOT / "scripts/ticket_runner.py")
runner = importlib.util.module_from_spec(spec)
sys.modules["ticket_runner"] = runner
spec.loader.exec_module(runner)


def raw_issue(number, title="S2: GST validation library", author="owner", labels=("agent:ready",)):
    return {
        "number": number,
        "title": title,
        "body": "body",
        "author": {"login": author},
        "labels": [{"name": n} for n in labels],
    }


def test_select_issues_oldest_first_and_limited():
    raw = [raw_issue(9), raw_issue(7), raw_issue(8)]
    picked = runner.select_issues(raw, {"owner"}, limit=2)
    assert [i.number for i in picked] == [7, 8]


def test_select_issues_ignores_untrusted_authors():
    raw = [raw_issue(7, author="stranger"), raw_issue(8)]
    assert [i.number for i in runner.select_issues(raw, {"owner"}, 5)] == [8]


def test_select_issues_skips_already_claimed():
    raw = [raw_issue(7, labels=("agent:ready", "agent:working")), raw_issue(8)]
    assert [i.number for i in runner.select_issues(raw, {"owner"}, 5)] == [8]


def test_slice_id_and_branch_name():
    issue = runner.select_issues([raw_issue(7)], {"owner"}, 1)[0]
    assert issue.sid == "S2"
    assert issue.branch == "ticket/7-s2-gst-validation-library"


def test_build_prompt_fills_placeholders():
    issue = runner.select_issues([raw_issue(7)], {"owner"}, 1)[0]
    prompt = runner.build_prompt(issue, "#{number} {sid} {title}\n{body}")
    assert prompt == "#7 S2 S2: GST validation library\nbody"


def test_parse_claude_output_success_and_error():
    ok = json.dumps(
        {"subtype": "success", "is_error": False, "result": "done", "total_cost_usd": 1.5}
    )
    assert runner.parse_claude_output(ok) == (True, "done", 1.5)
    err = json.dumps({"subtype": "error_max_turns", "is_error": True, "result": ""})
    assert runner.parse_claude_output(err)[0] is False
    assert runner.parse_claude_output("not json")[0] is False


def test_prompt_template_has_all_placeholders():
    template = (ROOT / ".claude/runner-prompt.md").read_text()
    for placeholder in ("{number}", "{title}", "{sid}", "{body}"):
        assert placeholder in template
