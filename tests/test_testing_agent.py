import subprocess
import sys

import pytest

from agents.testing_agent import TestingAgent


def test_runner_records_a_successful_pytest_execution(tmp_path, monkeypatch):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_example.py").write_text(
        "def test_example():\n    assert True\n",
        encoding="utf-8",
    )

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="2 passed\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = TestingAgent(tmp_path).run_pytest(
        task_id="T1",
        test_paths=["tests/test_example.py"],
    )

    assert result.task_id == "T1"
    assert result.checks[0].passed is True
    assert result.checks[0].exit_code == 0
    assert "2 passed" in result.checks[0].output
    assert captured["command"] == [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_example.py",
        "-q",
    ]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == tmp_path.resolve()


def test_runner_records_test_failures(tmp_path, monkeypatch):
    test_file = tmp_path / "test_example.py"
    test_file.write_text("def test_example():\n    assert False\n", encoding="utf-8")

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="1 failed\n",
            stderr="",
        ),
    )

    result = TestingAgent(tmp_path).run_pytest(
        task_id="T1",
        test_paths=["test_example.py"],
    )

    assert result.checks[0].passed is False
    assert result.checks[0].exit_code == 1
    assert "1 failed" in result.checks[0].output


def test_runner_records_a_timeout(tmp_path, monkeypatch):
    test_file = tmp_path / "test_slow.py"
    test_file.write_text("def test_slow():\n    pass\n", encoding="utf-8")

    def fake_run(command, **kwargs):

        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=b"test started",
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = TestingAgent(tmp_path, timeout_seconds=2).run_pytest(
        task_id="T1",
        test_paths=["test_slow.py"],
    )

    assert result.checks[0].passed is False
    assert result.checks[0].exit_code is None
    assert "timed out after 2 seconds" in result.checks[0].output


@pytest.mark.parametrize(
    "test_path",
    ["missing_test.py", "../outside/test_example.py", "-k"],
)
def test_runner_rejects_unsafe_or_missing_paths(tmp_path, monkeypatch, test_path):
    def unexpected_run(*args, **kwargs):
        pytest.fail("pytest must not start for an invalid test path")

    monkeypatch.setattr(subprocess, "run", unexpected_run)

    with pytest.raises(ValueError):
        TestingAgent(tmp_path).run_pytest(
            task_id="T1",
            test_paths=[test_path],
        )