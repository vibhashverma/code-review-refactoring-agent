"""Linter tool isolation tests."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.tools import linter_tool
from app.tools.linter_tool import lint_code, run_linter_tool

GOOD = "def add(a, b):\n    return a + b\n"
BAD_FORMAT = "def add(a,b):\n  return a+b\n"
SYNTAX_ERROR = "def broken(:\n    pass\n"


def run(code: str) -> dict:
    return json.loads(run_linter_tool.invoke({"code_string": code}))


def sandbox_dirs() -> set[Path]:
    return set(Path(tempfile.gettempdir()).glob("lint_sandbox_*"))


def test_valid_code_passes():
    result = run(GOOD)
    assert result["ok"] is True
    assert result["issues"] == [] and result["errors"] == []
    assert result["black"]["exit_code"] == 0
    assert result["flake8"]["exit_code"] == 0


def test_bad_formatting_detected():
    result = run(BAD_FORMAT)
    assert result["ok"] is False
    assert result["formatting_issues"] is True
    assert result["syntax_error"] is None
    assert any("E231" in i or "E225" in i for i in result["issues"])


def test_syntax_error_detected():
    result = run(SYNTAX_ERROR)
    assert result["ok"] is False
    assert result["syntax_error"].startswith("line 1")
    assert any("E999" in i for i in result["issues"])


def test_unused_import_reported_by_flake8():
    result = run("import os\n")
    assert any("F401" in i for i in result["issues"])


def test_temp_files_cleaned_up():
    before = sandbox_dirs()
    run(GOOD)
    run(SYNTAX_ERROR)
    assert sandbox_dirs() == before


def test_temp_files_cleaned_up_when_subprocess_fails():
    before = sandbox_dirs()
    with patch.object(
        linter_tool.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)
    ):
        result = lint_code(GOOD)
    assert sandbox_dirs() == before
    assert result["ok"] is False
    assert any("timed out" in e for e in result["errors"])


def test_code_is_never_executed(tmp_path):
    marker = tmp_path / "pwned.txt"
    code = f"open({str(marker)!r}, 'w').write('x')\nimport os\nos.system('echo hi')\n"
    run(code)
    assert not marker.exists()


def test_subprocess_uses_arg_lists_without_shell_and_with_timeout():
    with patch.object(linter_tool.subprocess, "run") as mocked:
        mocked.return_value = subprocess.CompletedProcess([], 0, "", "")
        lint_code(GOOD)
    assert mocked.call_count == 2
    for call in mocked.call_args_list:
        assert isinstance(call.args[0], list)
        assert call.kwargs["shell"] is False
        assert call.kwargs["timeout"] == linter_tool.TIMEOUT_SECONDS


def test_environment_does_not_leak_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-secret")
    env = linter_tool._safe_env("/tmp/x")
    assert "sk-secret" not in env.values()
    assert "ghp-secret" not in env.values()


def test_oversized_input_rejected():
    result = lint_code("x = 1\n" * 200_000)
    assert result["ok"] is False
    assert "maximum" in result["errors"][0]
