"""Behavioral tests for Nova's Claude Code CLI tool boundary."""

import json
import os
import textwrap

import pytest

pytestmark = pytest.mark.platforms("posix")


@pytest.fixture
def integration(tmp_path, monkeypatch):
    import tools.claude_code_tool as claude_tool
    from tools.registry import registry

    cli = tmp_path / "claude-fake"
    cli.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import json, os, sys, time
        if "--help" in sys.argv:
            print("--print --output-format --input-format --resume --permission-mode "
                  "--permission-prompts --include-partial-messages --agents --agent")
        elif "--version" in sys.argv:
            print("fake Claude Code 1.0")
        else:
            prompt = sys.stdin.read()
            if os.environ.get("FAKE_CLAUDE_AUTH_FAIL"):
                print("Not logged in. Please run claude auth login.", flush=True)
                sys.exit(1)
            with open(os.environ["FAKE_CLAUDE_RECORD"], "w", encoding="utf-8") as record:
                json.dump({"args": sys.argv[1:], "cwd": os.getcwd(), "prompt": prompt}, record)
            if os.environ.get("FAKE_CLAUDE_SLEEP"):
                time.sleep(float(os.environ["FAKE_CLAUDE_SLEEP"]))
            print(json.dumps({"type":"system", "session_id":"session-abc"}), flush=True)
            print(json.dumps({"type":"stream_event", "event":{"delta":{"text":"implemented"}}}), flush=True)
            print(json.dumps({"type":"result", "result":"implemented", "session_id":"session-abc"}), flush=True)
            sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))
    """))
    cli.chmod(0o755)
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    monkeypatch.setattr(claude_tool, "_load_config", lambda: {"claude_code_agents": {
        "enabled": True, "executable": "claude", "timeout_seconds": 15,
        "max_output_chars": 60000, "permission_mode": "acceptEdits",
    }})
    monkeypatch.setattr(claude_tool, "_find_cli", lambda _command: str(cli))
    monkeypatch.setattr(claude_tool, "_subprocess_env", lambda: dict(os.environ))
    return claude_tool, registry, tmp_path, record


def _dispatch(registry, **args):
    result = registry.dispatch("claude_code_agent", args)
    return json.loads(result)


def test_registry_dispatch_runs_in_selected_directory_and_captures_session(integration):
    _tool, registry, tmp_path, record_path = integration
    result = _dispatch(registry, task="Add a feature", working_directory=str(tmp_path))

    assert result["success"] is True
    assert result["output"] == "implemented"
    assert result["session_id"] == "session-abc"
    record = json.loads(record_path.read_text())
    assert record["cwd"] == str(tmp_path)
    assert "Add a feature" in record["prompt"]
    assert "acceptEdits" in record["args"]
    assert "--permission-prompts" in record["args"]
    assert "none" in record["args"]
    assert "bypassPermissions" not in record["args"]
    agents = json.loads(record["args"][record["args"].index("--agents") + 1])
    assert record["args"][record["args"].index("--agent") + 1] == "nova-code-implementer"
    assert "nova-code-implementer" in agents
    assert agents["nova-code-implementer"]["tools"] == ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]


def test_status_and_cancel_do_not_require_a_new_task(integration, monkeypatch):
    _tool, registry, tmp_path, _record = integration
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "20")
    started = _dispatch(registry, task="Long task", working_directory=str(tmp_path), background=True)
    assert started["status"] == "running"

    status = _dispatch(registry, action="status", run_id=started["run_id"])
    assert status["status"] == "running"
    cancelled = _dispatch(registry, action="cancel", run_id=started["run_id"])
    assert cancelled["cancel_confirmed"] is True
    assert cancelled["status"] == "cancelled"


def test_analysis_uses_plan_mode_and_can_resume_a_session(integration):
    _tool, registry, tmp_path, record_path = integration
    result = _dispatch(registry, task="Review this project", working_directory=str(tmp_path),
                       mode="analyze", session_id="existing-session")
    assert result["success"] is True
    args = json.loads(record_path.read_text())["args"]
    assert args[args.index("--permission-mode") + 1] == "plan"
    assert args[args.index("--resume") + 1] == "existing-session"
    assert args[args.index("--agent") + 1] == "nova-code-explorer"


@pytest.mark.parametrize("mode, agent_name", [
    ("debug", "nova-code-debugger"),
    ("refactor", "nova-code-refactor"),
    ("test", "nova-code-test-engineer"),
    ("review", "nova-code-reviewer"),
])
def test_coding_mode_selects_its_claude_agent(integration, mode, agent_name):
    _tool, registry, tmp_path, record_path = integration
    result = _dispatch(registry, task="Handle this repository task", working_directory=str(tmp_path), mode=mode)
    assert result["success"] is True
    args = json.loads(record_path.read_text())["args"]
    definitions = json.loads(args[args.index("--agents") + 1])
    assert args[args.index("--agent") + 1] == agent_name
    assert agent_name in definitions
    if mode == "review":
        assert set(definitions[agent_name]["tools"]).isdisjoint({"Edit", "Write", "Bash"})


def test_invalid_directory_and_session_are_rejected_before_execution(integration):
    _tool, registry, tmp_path, record_path = integration
    bad_directory = _dispatch(registry, task="Change code", working_directory=str(tmp_path / "missing"))
    bad_session = _dispatch(registry, task="Change code", working_directory=str(tmp_path), session_id="--help")
    assert "error" in bad_directory
    assert "error" in bad_session
    assert not record_path.exists()


def test_nonzero_exit_reports_failure(integration, monkeypatch):
    _tool, registry, tmp_path, _record = integration
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "7")
    result = _dispatch(registry, task="Fail this task", working_directory=str(tmp_path))
    assert result["error"] == "Claude Code exited unsuccessfully."
    assert result["exit_code"] == 7


def test_missing_cli_returns_actionable_error(integration, monkeypatch):
    tool, registry, tmp_path, record_path = integration
    monkeypatch.setattr(tool, "_find_cli", lambda _command: None)
    result = _dispatch(registry, task="Implement", working_directory=str(tmp_path))
    assert result["unavailable"] is True
    assert "Install the official Claude Code CLI" in result["error"]
    assert not record_path.exists()


def test_authentication_failure_is_reported_without_bypass(integration, monkeypatch):
    _tool, registry, tmp_path, _record = integration
    monkeypatch.setenv("FAKE_CLAUDE_AUTH_FAIL", "1")
    result = _dispatch(registry, task="Implement", working_directory=str(tmp_path))
    assert "authentication is required" in result["error"]
    assert "claude auth login" in result["error"]


def test_configured_timeout_stops_cli_process(integration, monkeypatch):
    tool, registry, tmp_path, _record = integration
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "5")
    config = tool._load_config()
    config["claude_code_agents"]["timeout_seconds"] = 1
    result = _dispatch(registry, task="Slow task", working_directory=str(tmp_path))
    assert result["status"] == "timed_out"
    assert result["error"] == "Claude Code exceeded its configured timeout."


def test_disabled_integration_fails_without_invoking_cli(integration, monkeypatch):
    tool, registry, tmp_path, record_path = integration
    monkeypatch.setattr(tool, "_load_config", lambda: {"claude_code_agents": {"enabled": False}})
    result = _dispatch(registry, task="Implement", working_directory=str(tmp_path))
    assert "disabled" in result["error"]
    assert not record_path.exists()
