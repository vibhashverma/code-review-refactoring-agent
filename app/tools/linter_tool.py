"""Sandboxed linter tool: runs Black and Flake8 on a code string.

The submitted code is only ever *read* by the linters; it is never imported,
executed, ``eval``-ed or ``exec``-ed.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

TIMEOUT_SECONDS = 30
MAX_CODE_BYTES = 500_000
MAX_OUTPUT_CHARS = 20_000
FLAKE8_ARGS = [
    "--isolated",
    "--max-line-length=88",
    "--extend-ignore=E203,W503",
]


def _safe_env(home: str) -> dict[str, str]:
    """Minimal environment: no API keys or other secrets are inherited."""
    env = {"HOME": home, "USERPROFILE": home, "TMP": home, "TEMP": home}
    for key in ("PATH", "SYSTEMROOT", "LANG"):
        if key in os.environ:
            env[key] = os.environ[key]
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _limit_resources() -> None:  # pragma: no cover - POSIX only, runs in child
    """Apply CPU / memory / file-size limits in the child process (POSIX)."""
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_SECONDS, TIMEOUT_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (10 << 20, 10 << 20))


def _run(args: list[str], cwd: str, env: dict[str, str]) -> dict[str, Any]:
    """Run an approved linter command (argument list, no shell, with timeout)."""
    kwargs: dict[str, Any] = {}
    if os.name == "posix":
        kwargs["preexec_fn"] = _limit_resources
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            shell=False,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        return {
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "error": f"timed out after {TIMEOUT_SECONDS}s",
        }
    except OSError as exc:
        return {"exit_code": None, "stdout": "", "stderr": "", "error": str(exc)}
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout[:MAX_OUTPUT_CHARS],
        "stderr": proc.stderr[:MAX_OUTPUT_CHARS],
        "error": None,
    }


def lint_code(code_string: str) -> dict[str, Any]:
    """Lint ``code_string`` with Black (--check) and Flake8; return a dict."""
    result: dict[str, Any] = {
        "ok": False,
        "syntax_error": None,
        "formatting_issues": False,
        "black": None,
        "flake8": None,
        "issues": [],
        "errors": [],
    }
    if len(code_string.encode("utf-8")) > MAX_CODE_BYTES:
        result["errors"].append("input exceeds maximum allowed size")
        return result

    try:  # parse only (static); the code is never executed
        ast.parse(code_string)
    except SyntaxError as exc:
        result["syntax_error"] = f"line {exc.lineno}: {exc.msg}"

    with tempfile.TemporaryDirectory(prefix="lint_sandbox_") as workdir:
        target = Path(workdir) / "snippet.py"
        target.write_text(code_string, encoding="utf-8")
        env = _safe_env(workdir)

        black = _run(
            [sys.executable, "-m", "black", "--check", "--diff", "-q", target.name],
            workdir,
            env,
        )
        flake = _run(
            [sys.executable, "-m", "flake8", *FLAKE8_ARGS, target.name], workdir, env
        )

    result["black"], result["flake8"] = black, flake
    for name, run in (("black", black), ("flake8", flake)):
        if run["error"]:
            result["errors"].append(f"{name}: {run['error']}")
    # Black: 0 = formatted, 1 = would reformat, 123 = cannot parse / internal error
    if black["exit_code"] == 1:
        result["formatting_issues"] = True
        result["issues"].append("black: file would be reformatted")
    elif black["exit_code"] not in (0, None):
        result["issues"].append(f"black: {black['stderr'].strip() or 'cannot format'}")
    for line in flake["stdout"].splitlines():
        result["issues"].append(line.replace("snippet.py:", "", 1))
    if flake["exit_code"] not in (0, 1, None):
        result["errors"].append(f"flake8 exited with {flake['exit_code']}")
    if result["syntax_error"]:
        result["issues"].insert(0, f"syntax error: {result['syntax_error']}")

    result["ok"] = (
        not result["issues"]
        and not result["errors"]
        and black["exit_code"] == 0
        and flake["exit_code"] == 0
    )
    return result


@tool
def run_linter_tool(code_string: str) -> str:
    """Lint Python source code in an isolated temp directory.

    Runs ``black --check`` and ``flake8`` and returns a JSON string with keys
    ``ok``, ``syntax_error``, ``formatting_issues``, ``black``, ``flake8``,
    ``issues`` and ``errors``.
    """
    return json.dumps(lint_code(code_string))
