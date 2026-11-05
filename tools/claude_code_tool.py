"""Run repository coding work through the user's installed Claude Code CLI.

Nova remains the orchestrator: this tool receives a bounded task and context,
then invokes the official CLI directly without a shell. Background runs are
tracked in memory and can be polled or cancelled by their returned run id.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nova_constants import hermes_home_key
from tools.registry import registry, tool_error, tool_result


_MODES = {"analyze", "implement", "debug", "refactor", "test", "review"}
_PERMISSION_MODES = {"acceptEdits", "auto", "dontAsk", "manual", "plan"}
_MAX_TIMEOUT_SECONDS = 4 * 60 * 60
_DEFAULT_TIMEOUT_SECONDS = 30 * 60
_DEFAULT_MAX_OUTPUT_CHARS = 60_000
_MAX_TASK_CHARS = 40_000
_RUN_TTL_SECONDS = 30 * 60
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")

_AGENT_PROFILES = {
    "analyze": {
        "name": "nova-code-explorer",
        "description": "Explore and explain a codebase without modifying files.",
        "prompt": (
            "You are Nova's read-only codebase exploration agent. Inspect the requested repository, "
            "follow its local instructions, and report relevant files, architecture, and findings. "
            "Do not modify files or claim tests were run."
        ),
        "tools": ["Read", "Glob", "Grep"],
    },
    "implement": {
        "name": "nova-code-implementer",
        "description": "Implement a requested feature or code change in the current repository.",
        "prompt": (
            "You are Nova's coding implementation agent. Inspect local instructions and existing changes, "
            "implement the requested behavior with focused edits, run relevant project tests when permitted, "
            "and report changed files and observed results. Preserve unrelated user work."
        ),
        "tools": ["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
    },
    "debug": {
        "name": "nova-code-debugger",
        "description": "Find and fix a software defect or test failure in the current repository.",
        "prompt": (
            "You are Nova's debugging agent. Reproduce the reported failure where possible, inspect the "
            "relevant code, fix its root cause, and run focused regression checks when permitted. Report "
            "evidence and any unresolved limitation; do not suppress errors or weaken tests."
        ),
        "tools": ["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
    },
    "refactor": {
        "name": "nova-code-refactor",
        "description": "Refactor existing code while preserving its behavior and tests.",
        "prompt": (
            "You are Nova's code refactoring agent. Inspect project conventions and existing tests, make "
            "the smallest coherent structural change requested, preserve behavior, and report verification."
        ),
        "tools": ["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
    },
    "test": {
        "name": "nova-code-test-engineer",
        "description": "Add or improve tests for the current repository task.",
        "prompt": (
            "You are Nova's test engineering agent. Follow the repository's test conventions, add or update "
            "meaningful behavior tests for the stated requirement, and run the relevant test command when "
            "permitted. Do not weaken assertions to make tests pass."
        ),
        "tools": ["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
    },
    "review": {
        "name": "nova-code-reviewer",
        "description": "Review repository changes for correctness, regressions, and security issues.",
        "prompt": (
            "You are Nova's read-only code review agent. Inspect the requested changes and surrounding "
            "code. Report only actionable findings, ordered by severity, with file and line references. "
            "Do not modify files."
        ),
        "tools": ["Read", "Glob", "Grep"],
    },
}


@dataclass
class _ClaudeRun:
    run_id: str
    process: subprocess.Popen
    working_directory: str
    mode: str
    started_at: float
    timeout_seconds: int
    max_output_chars: int
    home_key: str | None
    lock: threading.Lock = field(default_factory=threading.Lock)
    output: str = ""
    output_truncated: bool = False
    reported_error: bool = False
    received_result: bool = False
    session_id: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    reader: threading.Thread | None = None
    timer: threading.Timer | None = None


_RUNS: dict[tuple[str | None, str], _ClaudeRun] = {}
_RUNS_LOCK = threading.Lock()


def _load_config() -> dict[str, Any]:
    from nova_cli.config import load_config_readonly

    config = load_config_readonly() or {}
    return config if isinstance(config, dict) else {}


def _config_section(config: dict[str, Any]) -> dict[str, Any]:
    section = config.get("claude_code_agents")
    return section if isinstance(section, dict) else {}


def _find_cli(command: str) -> str | None:
    from agent.anthropic_adapter import find_claude_code_cli
    from nova_platform.resolver import locate_command

    command = command.strip()
    if not command:
        return None
    if os.sep not in command and (not os.altsep or os.altsep not in command):
        return find_claude_code_cli(command)
    candidate = os.path.abspath(os.path.expandvars(os.path.expanduser(command)))
    found = locate_command(candidate).command
    return found[0] if found else None


def _probe_cli(executable: str) -> tuple[set[str], str | None]:
    """Read the installed CLI's help/version, so invocation follows its real capabilities."""
    env = _subprocess_env()
    help_result = subprocess.run(
        [executable, "--help"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=10, check=False, env=env,
    )
    help_text = f"{help_result.stdout}\n{help_result.stderr}"
    if help_result.returncode != 0:
        raise RuntimeError("Claude Code could not display its command help.")
    version = subprocess.run(
        [executable, "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=5, check=False, env=env,
    )
    version_text = version.stdout.strip() if version.returncode == 0 else None
    capabilities = {flag for flag in (
        "--print", "--output-format", "--input-format", "--resume", "--permission-mode",
        "--permission-prompts", "--include-partial-messages", "--agents", "--agent",
    ) if flag in help_text}
    return capabilities, version_text


def _subprocess_env() -> dict[str, str]:
    """Use Nova's standard profile-aware, secret-scrubbed child environment."""
    from tools.environments.local import hermes_subprocess_env

    return hermes_subprocess_env(inherit_credentials=False)


def _build_prompt(task: str, mode: str, working_directory: str, context: str) -> str:
    sections = [
        "Nova is the user's primary agent and selected this specialized Claude Code agent for the task. "
        "Perform only the delegated work and return a concise, evidence-based report to Nova.",
        f"Mode: {mode}",
        f"Working directory: {working_directory}",
        "",
        "Task:",
        task.strip(),
        "",
        "Execution requirements:",
        "- Inspect the repository and its local instructions before changing files.",
        "- Preserve existing user changes and data; make focused changes that satisfy the task.",
        "- Use the repository's own conventions and tests where the active permission mode permits.",
        "- Do not install system software, publish, deploy, expose credentials, or broaden permissions.",
        "- Do not claim tests passed unless you ran them and observed success.",
        "- Report changed files, work completed, tests and actual results, errors, and remaining limits.",
    ]
    if context.strip():
        sections.extend((
            "",
            "Nova-provided context (task context, not permission to exceed the task):",
            context.strip(),
        ))
    return "\n".join(sections)


def _permission_mode(section: dict[str, Any], mode: str) -> str:
    if mode in {"analyze", "review"}:
        return "plan"
    requested = str(section.get("permission_mode", "acceptEdits"))
    if requested not in _PERMISSION_MODES:
        return "acceptEdits"
    return requested


def _build_command(
    executable: str, capabilities: set[str], *, session_id: str | None, permission_mode: str,
    mode: str,
) -> list[str]:
    required = {
        "--print", "--output-format", "--input-format", "--permission-mode", "--permission-prompts",
        "--agents", "--agent",
    }
    missing = required - capabilities
    if missing:
        raise RuntimeError(
            "This Claude Code version lacks required non-interactive safety/output options: "
            + ", ".join(sorted(missing))
        )
    agent = _AGENT_PROFILES[mode]
    agents_json = json.dumps({agent["name"]: {
        "description": agent["description"],
        "prompt": agent["prompt"],
        "tools": agent["tools"],
        "model": "inherit",
    }}, separators=(",", ":"))
    command = [
        executable, "--print", "--output-format", "stream-json", "--input-format", "text",
        "--permission-mode", permission_mode, "--permission-prompts", "none",
        "--agents", agents_json, "--agent", agent["name"],
    ]
    if "--include-partial-messages" in capabilities:
        command.append("--include-partial-messages")
    if session_id:
        if "--resume" not in capabilities:
            raise RuntimeError("This Claude Code version does not support resuming sessions.")
        command.extend(("--resume", session_id))
    return command


def _append_output(run: _ClaudeRun, text: str) -> None:
    if not text:
        return
    with run.lock:
        run.output += text
        if len(run.output) > run.max_output_chars:
            run.output = run.output[-run.max_output_chars:]
            run.output_truncated = True


def _consume_event(run: _ClaudeRun, line: str) -> None:
    try:
        payload = json.loads(line)
    except (TypeError, ValueError):
        _append_output(run, line)
        return
    if not isinstance(payload, dict):
        return
    event_type = payload.get("type")
    if event_type == "system" and payload.get("session_id"):
        run.session_id = str(payload["session_id"])
    elif event_type == "stream_event":
        event = payload.get("event")
        if isinstance(event, dict):
            if event.get("session_id"):
                run.session_id = str(event["session_id"])
            delta = event.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("text"), str):
                _append_output(run, delta["text"])
    elif event_type == "result":
        run.received_result = True
        if payload.get("session_id"):
            run.session_id = str(payload["session_id"])
        result = payload.get("result")
        if isinstance(result, str):
            # Keep the authoritative final response under the same output cap.
            if not run.output:
                _append_output(run, result)
        if payload.get("is_error"):
            run.reported_error = True
            _append_output(run, "\nClaude Code reported an execution error.\n")


def _read_output(run: _ClaudeRun) -> None:
    try:
        stream = run.process.stdout
        if stream is None:
            return
        for line in stream:
            _consume_event(run, line)
    finally:
        with run.lock:
            run.exit_code = run.process.poll()


def _terminate(run: _ClaudeRun, *, timed_out: bool = False) -> bool:
    if run.process.poll() is not None:
        return False
    run.timed_out = timed_out
    run.cancelled = not timed_out
    try:
        if os.name == "posix":
            os.killpg(run.process.pid, signal.SIGTERM)
        else:
            run.process.terminate()
        try:
            run.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(run.process.pid, signal.SIGKILL)
            else:
                run.process.kill()
            run.process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return run.process.poll() is not None


def _watch_timeout(run: _ClaudeRun) -> None:
    if run.process.poll() is None:
        _terminate(run, timed_out=True)


def _start_run(
    *, task: str, working_directory: Path, mode: str, context: str, session_id: str | None,
    section: dict[str, Any],
) -> _ClaudeRun:
    executable = _find_cli(str(section.get("executable", "claude")))
    if not executable:
        raise FileNotFoundError(
            "Claude Code CLI was not found. Install the official Claude Code CLI and authenticate it with "
            "`claude auth login`."
        )
    capabilities, _version = _probe_cli(executable)
    permission_mode = _permission_mode(section, mode)
    command = _build_command(
        executable, capabilities, session_id=session_id, permission_mode=permission_mode, mode=mode,
    )
    timeout = section.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        timeout = _DEFAULT_TIMEOUT_SECONDS
    timeout_seconds = max(1, min(int(timeout), _MAX_TIMEOUT_SECONDS))
    output_limit = section.get("max_output_chars", _DEFAULT_MAX_OUTPUT_CHARS)
    if isinstance(output_limit, bool) or not isinstance(output_limit, int):
        output_limit = _DEFAULT_MAX_OUTPUT_CHARS
    output_limit = max(1_000, min(output_limit, 500_000))
    prompt = _build_prompt(task, mode, str(working_directory), context)
    if len(prompt) > _MAX_TASK_CHARS:
        raise ValueError(f"Task and context exceed the {_MAX_TASK_CHARS:,}-character limit.")
    kwargs: dict[str, Any] = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        command, cwd=str(working_directory), env=_subprocess_env(), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        bufsize=1, **kwargs,
    )
    run = _ClaudeRun(
        run_id=f"cc_{uuid.uuid4().hex}", process=process,
        working_directory=str(working_directory), mode=mode, started_at=time.monotonic(),
        timeout_seconds=timeout_seconds, max_output_chars=output_limit, home_key=hermes_home_key(),
    )
    run.reader = threading.Thread(target=_read_output, args=(run,), name=f"claude-code-{run.run_id}", daemon=True)
    run.reader.start()
    try:
        if process.stdin is not None:
            process.stdin.write(prompt)
            process.stdin.close()
    except (BrokenPipeError, OSError):
        # The CLI may exit early (usually authentication/argument rejection); its
        # captured output below is the useful failure detail.
        pass
    run.timer = threading.Timer(timeout_seconds, _watch_timeout, args=(run,))
    run.timer.daemon = True
    run.timer.start()
    key = (run.home_key, run.run_id)
    with _RUNS_LOCK:
        _prune_runs_locked()
        _RUNS[key] = run
    return run


def _prune_runs_locked() -> None:
    cutoff = time.monotonic() - _RUN_TTL_SECONDS
    for key, run in list(_RUNS.items()):
        if run.process.poll() is not None and run.started_at < cutoff:
            if run.timer:
                run.timer.cancel()
            _RUNS.pop(key, None)


def _get_run(run_id: str) -> _ClaudeRun | None:
    key = (hermes_home_key(), run_id)
    with _RUNS_LOCK:
        return _RUNS.get(key)


def _safe_output(run: _ClaudeRun) -> str:
    from agent.redact import redact_sensitive_text

    with run.lock:
        output = run.output
        truncated = run.output_truncated
    output = redact_sensitive_text(output, force=True)
    return ("[Earlier output omitted; showing the most recent output.]\n" if truncated else "") + output


def _run_status(run: _ClaudeRun) -> dict[str, Any]:
    exit_code = run.process.poll()
    if exit_code is not None and run.reader:
        run.reader.join(timeout=0.2)
    output = _safe_output(run)
    with run.lock:
        session_id = run.session_id
        truncated = run.output_truncated
    return {
        "run_id": run.run_id,
        "status": (
            "running" if exit_code is None else "timed_out" if run.timed_out
            else "cancelled" if run.cancelled else "completed"
        ),
        "exit_code": exit_code,
        "session_id": session_id,
        "working_directory": run.working_directory,
        "mode": run.mode,
        "elapsed_seconds": round(time.monotonic() - run.started_at, 1),
        "output": output,
        "output_truncated": truncated,
    }


def claude_code_tool(
    task: str = "", working_directory: str = "", mode: str = "implement", context: str = "",
    session_id: str = "", action: str = "run", run_id: str = "", background: bool = False,
) -> str:
    """Run, poll, or cancel a Claude Code coding-engine task."""
    config = _load_config()
    section = _config_section(config)
    action = (action or "run").strip().lower()
    if action in {"status", "cancel"}:
        run = _get_run((run_id or "").strip())
        if run is None:
            return tool_error("Claude Code run was not found in this Nova process or profile.", run_id=run_id)
        if action == "cancel":
            cancelled = _terminate(run)
            result = _run_status(run)
            result["cancel_confirmed"] = bool(cancelled and run.process.poll() is not None)
            return tool_result(result)
        return tool_result(_run_status(run))
    if action != "run":
        return tool_error("action must be run, status, or cancel.")
    if section.get("enabled", True) is False:
        return tool_error("Claude Code integration is disabled in Nova configuration.")

    task = (task or "").strip()
    if not task:
        return tool_error("Provide the coding task to run.")
    if len(task) > _MAX_TASK_CHARS:
        return tool_error(f"Task exceeds the {_MAX_TASK_CHARS:,}-character limit.")
    mode = (mode or "implement").strip().lower()
    if mode not in _MODES:
        return tool_error(f"mode must be one of: {', '.join(sorted(_MODES))}.")
    context = context or ""
    if not isinstance(context, str):
        return tool_error("context must be text.")
    if len(context) > _MAX_TASK_CHARS:
        return tool_error(f"context exceeds the {_MAX_TASK_CHARS:,}-character limit.")
    raw_cwd = (working_directory or os.getcwd()).strip()
    cwd = Path(raw_cwd).expanduser()
    if not cwd.is_absolute():
        cwd = Path.cwd() / cwd
    try:
        cwd = cwd.resolve(strict=True)
    except OSError:
        return tool_error("working_directory does not exist or cannot be accessed.", working_directory=raw_cwd)
    if not cwd.is_dir():
        return tool_error("working_directory must be an existing directory.", working_directory=str(cwd))
    session_id = (session_id or "").strip() or None
    if session_id and not _SESSION_ID_RE.fullmatch(session_id):
        return tool_error("session_id must be a valid Claude Code session id or name (up to 128 safe characters).")

    try:
        run = _start_run(
            task=task, working_directory=cwd, mode=mode, context=context, session_id=session_id,
            section=section,
        )
    except FileNotFoundError as exc:
        return tool_error(str(exc), unavailable=True)
    except subprocess.TimeoutExpired:
        return tool_error("Claude Code did not respond to its version/help probe before the timeout.")
    except (OSError, RuntimeError, ValueError) as exc:
        return tool_error(str(exc))

    if background:
        return tool_result({"success": True, "status": "running", "run_id": run.run_id,
                            "working_directory": run.working_directory, "mode": mode})

    try:
        run.process.wait(timeout=run.timeout_seconds + 5)
    except subprocess.TimeoutExpired:
        _terminate(run, timed_out=True)
    if run.reader:
        run.reader.join(timeout=2)
    if run.timer:
        run.timer.cancel()
    status = _run_status(run)
    if status["status"] == "timed_out":
        return tool_error("Claude Code exceeded its configured timeout.", **status)
    if status["status"] == "cancelled":
        return tool_error("Claude Code was cancelled.", **status)
    if status["exit_code"] != 0 or run.reported_error or not run.received_result:
        output = status.get("output") or "No output was returned."
        message = (
            "Claude Code exited unsuccessfully."
            if status["exit_code"] != 0 or run.reported_error
            else "Claude Code exited without a structured completion result."
        )
        if any(marker in output.lower() for marker in (
            "not logged in", "login required", "unauthorized", "authentication",
        )):
            message = (
                "Claude Code authentication is required. Sign in with `claude auth login`; Nova will "
                "not bypass Claude authentication or subscription requirements."
            )
        return tool_error(message, **status)
    status["success"] = True
    return tool_result(status)


_DESCRIPTION = (
    "Prefer this tool over terminal or execute_code when a user request requires repository coding work. "
    "It launches a mode-specific Claude Code agent (implementer, debugger, refactorer, test engineer, "
    "read-only explorer, or reviewer) selected by mode; Nova remains the primary agent and orchestrator. "
    "Do not use for ordinary explanation, research, summarization, or planning with no repository work. "
    "Pass only relevant task/context, select the target project, inspect the agent's report, and report "
    "tests only when observed. Analyze/review agents are read-only and use plan mode. For a long task, set "
    "background=true, then use action=status with the returned run_id; action=cancel terminates only that "
    "tracked process. Provide session_id only to continue the same Claude Code conversation. If the tool "
    "reports that Claude Code is missing or unauthenticated, explain that requirement and use Nova's native "
    "tools only when they can safely complete the requested coding work."
)

registry.register(
    name="claude_code_agent",
    toolset="delegation",
    schema={
        "name": "claude_code_agent",
        "description": _DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Coding/repository task; required only for action=run.",
                },
                "working_directory": {
                    "type": "string",
                    "description": "Existing project directory; defaults to Nova's current directory.",
                },
                "mode": {
                    "type": "string", "enum": sorted(_MODES),
                    "description": "Task type; analyze/review use read-only plan mode.",
                },
                "context": {
                    "type": "string",
                    "description": "Relevant architecture, requirements, or observed errors gathered by Nova.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Claude Code session id/name returned by an earlier run.",
                },
                "action": {
                    "type": "string", "enum": ["run", "status", "cancel"],
                    "description": "Run a task, poll a background run, or cancel that run.",
                },
                "run_id": {
                    "type": "string",
                    "description": "Nova run_id returned when background=true; required for status/cancel.",
                },
                "background": {
                    "type": "boolean",
                    "description": "Return a run_id for a long task, then poll with action=status.",
                },
            },
            "required": [],
        },
    },
    handler=lambda args, **_kw: claude_code_tool(
        task=args.get("task", ""), working_directory=args.get("working_directory", ""),
        mode=args.get("mode", "implement"), context=args.get("context", ""),
        session_id=args.get("session_id", ""), action=args.get("action", "run"),
        run_id=args.get("run_id", ""), background=args.get("background", False),
    ),
    emoji="🧑‍💻",
    execution_timeout=None,
)
