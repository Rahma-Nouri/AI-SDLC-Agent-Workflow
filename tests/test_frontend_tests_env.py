"""P2.2c: npm environment with human approval, the Vitest runner, and the frontend loop.

The normal suite never runs npm: a fake command runner stands in for it.
One opt-in test runs the real npm (set AI_SDLC_REAL_NPM=1).
"""

import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

import main as workflow_main
from agents.frontend_developer import FrontendDeveloperAgent
from agents.frontend_environment import APPROVAL_FILE_NAME, FrontendEnvironment, npm_command
from agents.pilot_git_agent import PilotGitAgent
from agents.testing_agent import TestingAgent
from test_frontend_agent import APP, FOLDER, PACKAGE, GateOrchestrator, approve_h2, make_frontend_repo, model_answer  # noqa: F401
from test_pilot_git_agent import git

TEST_FILE = f"{FOLDER}/src/App.test.tsx"
GOOD_PACKAGE = json.dumps({"name": "exam-ui", "devDependencies": {"vitest": "^3.2.4"}})


class FakeRunner:
    """Stands in for subprocess.run: records each command and answers like npm/npx."""

    def __init__(self, test_exit_codes=(0,), install_exit_code=0):
        self.calls = []
        self.test_exit_codes = list(test_exit_codes)
        self.install_exit_code = install_exit_code

    def __call__(self, args, **kwargs):
        self.calls.append({"args": list(args), **kwargs})
        if "install" in args:
            return SimpleNamespace(returncode=self.install_exit_code, stdout="added 1 package", stderr="")
        code = self.test_exit_codes.pop(0)
        return SimpleNamespace(returncode=code, stdout="1 passed" if code == 0 else "1 failed: expected 2", stderr="")

    def commands(self, word):
        return [call for call in self.calls if word in call["args"]]


def answers(*items):
    items = list(items)
    return lambda _prompt: items.pop(0)


def never_asked(_prompt):
    raise AssertionError("The human must not be asked.")


def frontend_folder(tmp_path, package=GOOD_PACKAGE, gitignore="node_modules/\ndist/\n"):
    (tmp_path / FOLDER).mkdir(parents=True)
    (tmp_path / PACKAGE).write_text(package, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(gitignore, encoding="utf-8")
    return tmp_path


# --- environment ---------------------------------------------------------------

def test_approve_installs_the_listed_packages_safely(tmp_path, capsys):
    pilot = frontend_folder(tmp_path)
    runner = FakeRunner()

    result = FrontendEnvironment(pilot, FOLDER, ask=answers("approve"), runner=runner, platform="linux").prepare()

    assert result.status == "ready" and result.installed_now
    assert result.packages == ["vitest@^3.2.4"]
    assert "vitest@^3.2.4" in capsys.readouterr().out
    call = runner.calls[0]
    assert call["args"] == ["npm", "install", "--ignore-scripts", "--no-package-lock", "--no-audit", "--no-fund"]
    assert call["cwd"] == pilot / FOLDER
    assert call["shell"] is False and call["timeout"] and call["encoding"] == "utf-8"
    assert (pilot / FOLDER / "node_modules" / APPROVAL_FILE_NAME).read_text(encoding="utf-8") == "vitest@^3.2.4\n"


def test_unchanged_list_does_not_ask_again_and_a_changed_list_does(tmp_path):
    pilot = frontend_folder(tmp_path)
    runner = FakeRunner()
    FrontendEnvironment(pilot, FOLDER, ask=answers("approve"), runner=runner).prepare()

    again = FrontendEnvironment(pilot, FOLDER, ask=never_asked, runner=runner).prepare()
    assert again.status == "ready" and not again.installed_now
    assert len(runner.commands("install")) == 1

    (pilot / PACKAGE).write_text(json.dumps({"devDependencies": {"vitest": "^3.2.4", "jsdom": "^26.0.0"}}), encoding="utf-8")
    changed = FrontendEnvironment(pilot, FOLDER, ask=answers("approve"), runner=runner).prepare()
    assert changed.installed_now and len(runner.commands("install")) == 2


def test_reject_installs_nothing(tmp_path):
    pilot = frontend_folder(tmp_path)
    runner = FakeRunner()

    result = FrontendEnvironment(pilot, FOLDER, ask=answers("reject"), runner=runner).prepare()

    assert result.status == "rejected"
    assert runner.calls == []
    assert not (pilot / FOLDER / "node_modules").exists()


@pytest.mark.parametrize(
    "version",
    [
        "git+https://github.com/x/y.git",
        "github:x/y",
        "x/y",
        "file:../evil",
        "link:../evil",
        "https://example.com/pkg.tgz",
        "http://example.com/pkg.tgz",
        "../local-folder",
        "C:\\packages\\evil",
        "npm:other-package@1.0.0",
    ],
)
def test_non_registry_specs_are_refused_without_asking(tmp_path, version):
    pilot = frontend_folder(tmp_path, json.dumps({"dependencies": {"react": "^19.0.0", "evil": version}}))
    runner = FakeRunner()

    result = FrontendEnvironment(pilot, FOLDER, ask=never_asked, runner=runner).prepare()

    assert result.status == "failed" and "registry" in result.detail
    assert runner.calls == []


@pytest.mark.parametrize("field", ["peerDependencies", "optionalDependencies", "overrides", "workspaces"])
def test_fields_that_install_hidden_packages_are_refused(tmp_path, field):
    pilot = frontend_folder(tmp_path, json.dumps({"devDependencies": {"vitest": "^3.2.4"}, field: {"x": "1.0.0"}}))

    result = FrontendEnvironment(pilot, FOLDER, ask=never_asked, runner=FakeRunner()).prepare()

    assert result.status == "failed" and field in result.detail


def test_npmrc_in_the_frontend_folder_is_refused(tmp_path):
    pilot = frontend_folder(tmp_path)
    (pilot / FOLDER / ".npmrc").write_text("registry=https://evil.example.com/\n", encoding="utf-8")

    result = FrontendEnvironment(pilot, FOLDER, ask=never_asked, runner=FakeRunner()).prepare()

    assert result.status == "failed" and ".npmrc" in result.detail


@pytest.mark.parametrize("gitignore", ["__pycache__/\n", "node_modules/\n", "dist/\n"])
def test_gitignore_must_ignore_node_modules_and_dist_before_any_install(tmp_path, gitignore):
    pilot = frontend_folder(tmp_path, gitignore=gitignore)
    runner = FakeRunner()

    result = FrontendEnvironment(pilot, FOLDER, ask=never_asked, runner=runner).prepare()

    assert result.status == "failed" and ".gitignore" in result.detail
    assert runner.calls == []


@pytest.mark.parametrize("package", ["{not json", "[1]", None])
def test_missing_or_invalid_package_json_fails_cleanly(tmp_path, package):
    pilot = frontend_folder(tmp_path)
    if package is None:
        (pilot / PACKAGE).unlink()
    else:
        (pilot / PACKAGE).write_text(package, encoding="utf-8")

    assert FrontendEnvironment(pilot, FOLDER, ask=never_asked, runner=FakeRunner()).prepare().status == "failed"


def test_failed_or_missing_npm_is_reported_and_nothing_is_approved(tmp_path):
    pilot = frontend_folder(tmp_path)

    failed = FrontendEnvironment(pilot, FOLDER, ask=answers("approve"), runner=FakeRunner(install_exit_code=1)).prepare()
    assert failed.status == "failed" and "npm install failed" in failed.detail

    def no_npm(*_args, **_kwargs):
        raise FileNotFoundError("npm.cmd not found")

    missing = FrontendEnvironment(pilot, FOLDER, ask=answers("approve"), runner=no_npm).prepare()
    assert missing.status == "failed" and "could not run" in missing.detail
    assert not (pilot / FOLDER / "node_modules" / APPROVAL_FILE_NAME).exists()


@pytest.mark.parametrize(("platform", "npm", "npx"), [("win32", "npm.cmd", "npx.cmd"), ("linux", "npm", "npx")])
def test_windows_uses_npm_cmd_and_npx_cmd(tmp_path, platform, npm, npx):
    assert npm_command("npm", platform) == npm
    pilot = frontend_folder(tmp_path)
    (pilot / TEST_FILE).parent.mkdir(parents=True)
    (pilot / TEST_FILE).write_text("test", encoding="utf-8")
    runner = FakeRunner()

    FrontendEnvironment(pilot, FOLDER, ask=answers("approve"), runner=runner, platform=platform).prepare()
    TestingAgent(pilot, runner=runner, platform=platform).run_vitest("T7", FOLDER, [TEST_FILE])

    assert runner.calls[0]["args"][0] == npm
    assert runner.calls[1]["args"][0] == npx


# --- Vitest runner ---------------------------------------------------------------

def test_vitest_runner_runs_once_in_the_frontend_folder(tmp_path):
    pilot = frontend_folder(tmp_path)
    (pilot / TEST_FILE).parent.mkdir(parents=True)
    (pilot / TEST_FILE).write_text("test", encoding="utf-8")
    runner = FakeRunner(test_exit_codes=[1])

    result = TestingAgent(pilot, runner=runner, platform="linux").run_vitest("T7", FOLDER, [TEST_FILE])

    call = runner.calls[0]
    # --no: npx must never download a package the human did not approve.
    assert call["args"] == ["npx", "--no", "vitest", "run", "src/App.test.tsx"]
    assert call["cwd"] == (pilot / FOLDER).resolve()
    assert call["env"]["CI"] == "true" and call["shell"] is False and call["timeout"]
    check = result.checks[0]
    assert check.check_name == "Vitest" and check.exit_code == 1 and check.passed is False
    assert "expected 2" in check.output and "npx --no vitest run" in check.command


@pytest.mark.parametrize("path", ["pilot/app/backend/test_x.py", "--watch", f"{FOLDER}/src/missing.test.tsx"])
def test_vitest_runner_refuses_paths_outside_the_folder_or_missing(tmp_path, path):
    pilot = frontend_folder(tmp_path)

    with pytest.raises(ValueError):
        TestingAgent(pilot, runner=FakeRunner()).run_vitest("T7", FOLDER, [path])


@pytest.mark.parametrize("name", ["x&calc.test.tsx", "x^y.test.tsx", "x%PATH%.test.tsx", "x y.test.tsx"])
def test_vitest_runner_refuses_file_names_cmd_could_misread(tmp_path, name):
    pilot = frontend_folder(tmp_path)
    (pilot / FOLDER / name).write_text("test", encoding="utf-8")
    runner = FakeRunner()

    with pytest.raises(ValueError, match="not allowed"):
        TestingAgent(pilot, runner=runner, platform="win32").run_vitest("T7", FOLDER, [f"{FOLDER}/{name}"])
    assert runner.calls == []


def test_vitest_timeout_is_recorded(tmp_path):
    pilot = frontend_folder(tmp_path)
    (pilot / TEST_FILE).parent.mkdir(parents=True)
    (pilot / TEST_FILE).write_text("test", encoding="utf-8")

    def slow(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"], output="still running")

    check = TestingAgent(pilot, runner=slow, timeout_seconds=1).run_vitest("T7", FOLDER, [TEST_FILE]).checks[0]

    assert check.exit_code is None and not check.passed and "timed out" in check.output


# --- the frontend flow -------------------------------------------------------------

class SequenceClient:
    """Stands in for Gemini: one prepared answer per request."""

    def __init__(self, *answers_):
        self.answers = list(answers_)
        self.requests = []
        self.models = SimpleNamespace(generate_content=self.generate_content)

    def generate_content(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(text=self.answers.pop(0), candidates=None)


GITIGNORE_WITH_NODE = "__pycache__/\n.venv/\nnode_modules/\ndist/\n"


def first_answer():
    return model_answer(
        {PACKAGE: GOOD_PACKAGE, APP: "export const add = (a, b) => a - b;\n", TEST_FILE: "test('adds', () => {});\n",
         ".gitignore": GITIGNORE_WITH_NODE},
        tests_added=[TEST_FILE],
    )


def run_flow(repo, client, runner, ask=None):
    agent = FrontendDeveloperAgent(client=client, model="test-model")
    environment = FrontendEnvironment(repo, FOLDER, ask=ask or answers("approve"), runner=runner)
    return workflow_main.run_frontend_patch(
        repo, GateOrchestrator(), "T7", agent=agent, git_agent=PilotGitAgent(repo),
        environment=environment, run_command=runner,
    )


def test_frontend_flow_installs_tests_fixes_once_and_tests_again(tmp_path, approve_h2):
    repo = make_frontend_repo(tmp_path)
    fix = model_answer({APP: "export const add = (a, b) => a + b;\n"})
    client = SequenceClient(first_answer(), fix)
    runner = FakeRunner(test_exit_codes=[1, 0])

    outcome = run_flow(repo, client, runner)

    report = outcome["loop_report"]
    assert report.final_result == "passed" and report.decision == "ready_for_review"
    assert [run.passed for run in report.runs] == [False, True]
    assert report.fix_attempts[0].outcome == "committed"
    assert report.tests_run == [TEST_FILE]
    assert all("vitest" in run.command for run in report.runs)
    # One install (the list did not change between the two runs), two Vitest runs.
    assert len(runner.commands("install")) == 1 and len(runner.commands("vitest")) == 2
    # The fix request carried the real failure and the frontend frozen-test rule.
    fix_prompt = client.requests[1]["contents"]
    assert "expected 2" in fix_prompt and "*.test.*" in fix_prompt
    # Everything is committed by the Frontend Agent, including the loop report.
    assert git(repo, "status", "--porcelain") == ""
    assert set(git(repo, "log", "pilot/main..HEAD", "--format=%an").splitlines()) == {"AI-SDLC Frontend Agent"}
    assert any(name.startswith("outputs/test_results/T7_correction_loop_") for name in git(repo, "ls-files").splitlines())


def test_a_fix_that_changes_a_frontend_test_is_rejected(tmp_path, approve_h2):
    repo = make_frontend_repo(tmp_path)
    weakened_test = model_answer({TEST_FILE: "test('adds', () => { /* nothing checked */ });\n"})
    client = SequenceClient(first_answer(), weakened_test)

    outcome = run_flow(repo, client, FakeRunner(test_exit_codes=[1]))

    report = outcome["loop_report"]
    assert report.fix_attempts[0].outcome == "rejected_frozen_tests"
    assert report.final_result == "failed"
    assert git(repo, "show", f"HEAD:{TEST_FILE}") == "test('adds', () => {});"


def test_rejected_packages_stop_with_environment_not_ready(tmp_path, approve_h2):
    repo = make_frontend_repo(tmp_path)
    runner = FakeRunner()

    outcome = run_flow(repo, SequenceClient(first_answer()), runner, ask=answers("reject"))

    report = outcome["loop_report"]
    assert report.final_result == "environment_not_ready" and report.runs == []
    assert runner.calls == []  # no install, no test run
    assert git(repo, "status", "--porcelain") == ""  # the report is committed; the branch waits for H3


def test_patch_without_the_gitignore_lines_stops_before_any_install(tmp_path, approve_h2):
    repo = make_frontend_repo(tmp_path)
    answer = model_answer({PACKAGE: GOOD_PACKAGE, TEST_FILE: "test('x', () => {});\n"}, tests_added=[TEST_FILE])
    runner = FakeRunner()

    outcome = run_flow(repo, SequenceClient(answer), runner, ask=never_asked)

    report = outcome["loop_report"]
    assert report.final_result == "environment_not_ready"
    assert "node_modules/" in report.environment[0].detail
    assert runner.calls == []


def test_retest_on_a_frontend_branch_uses_vitest(tmp_path, approve_h2):
    repo = make_frontend_repo(tmp_path)
    run_flow(repo, SequenceClient(first_answer()), FakeRunner(test_exit_codes=[0]))
    runner = FakeRunner(test_exit_codes=[0])

    report = workflow_main.run_retest_agent_branch(
        repo, GateOrchestrator(), git_agent=PilotGitAgent(repo),
        environment=FrontendEnvironment(repo, FOLDER, ask=never_asked, runner=runner), run_command=runner,
    )

    assert report.trigger == "retest" and report.final_result == "passed"
    assert report.tests_run == [TEST_FILE]
    assert runner.commands("vitest") and not runner.commands("pytest")


def test_h3_revise_on_a_frontend_branch_uses_vitest(tmp_path, approve_h2, monkeypatch):
    monkeypatch.setattr(workflow_main, "GitHubClient", lambda: None)
    repo = make_frontend_repo(tmp_path)
    run_flow(repo, SequenceClient(first_answer()), FakeRunner(test_exit_codes=[0]))
    runner = FakeRunner(test_exit_codes=[0])
    reviser = FrontendDeveloperAgent(client=SequenceClient(model_answer({APP: "export const add = (a, b) => b + a;\n"})), model="m")
    replies = answers("revise", "Swap the order", "no")

    outcome = workflow_main.run_h3_review_command(
        repo, GateOrchestrator(), git_agent=PilotGitAgent(repo), backend_agent=reviser, github_client=None,
        environment=FrontendEnvironment(repo, FOLDER, ask=never_asked, runner=runner), run_command=runner, ask=replies,
    )

    report = outcome["revision_report"]
    assert report.trigger == "h3_revise" and report.final_result == "passed"
    assert report.tests_run == [TEST_FILE]
    assert runner.commands("vitest")


def test_a_test_runner_other_than_vitest_stops_before_gemini(tmp_path, capsys):
    repo = make_frontend_repo(tmp_path)
    snapshot_path = repo / "outputs" / "approved_context" / "current_approved_context.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["ui_stack"]["test_runner"] = "Jest"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    client = SequenceClient()

    assert run_flow(repo, client, FakeRunner()) is None
    assert client.requests == []
    assert "Only Vitest" in capsys.readouterr().out


# --- the one real npm test (opt-in) --------------------------------------------------

@pytest.mark.skipif(os.getenv("AI_SDLC_REAL_NPM") != "1", reason="real npm test: set AI_SDLC_REAL_NPM=1 (needs internet)")
@pytest.mark.skipif(shutil.which("npm") is None, reason="npm is not installed")
def test_real_npm_installs_vitest_and_runs_a_tiny_test(tmp_path):
    pilot = frontend_folder(tmp_path, json.dumps({"name": "tiny", "type": "module", "devDependencies": {"vitest": "^3.2.4"}}))
    (pilot / FOLDER / "sum.test.js").write_text(
        "import { expect, test } from 'vitest';\ntest('adds', () => { expect(1 + 1).toBe(2); });\n", encoding="utf-8"
    )

    first = FrontendEnvironment(pilot, FOLDER, ask=answers("approve")).prepare()
    assert first.status == "ready", first.detail
    assert first.installed_now

    # Unchanged list: no question, no second install.
    assert FrontendEnvironment(pilot, FOLDER, ask=never_asked).prepare().status == "ready"

    passing = TestingAgent(pilot).run_vitest("T7", FOLDER, [f"{FOLDER}/sum.test.js"]).checks[0]
    assert passing.passed, passing.output

    (pilot / FOLDER / "sum.test.js").write_text(
        "import { expect, test } from 'vitest';\ntest('adds', () => { expect(1 + 1).toBe(3); });\n", encoding="utf-8"
    )
    failing = TestingAgent(pilot).run_vitest("T7", FOLDER, [f"{FOLDER}/sum.test.js"]).checks[0]
    assert failing.exit_code not in (0, None), failing.output
