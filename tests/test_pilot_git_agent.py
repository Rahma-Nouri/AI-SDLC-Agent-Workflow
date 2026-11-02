import shutil
import subprocess

import pytest

from agents.pilot_git_agent import AGENT_EMAIL, AGENT_NAME, PilotGitAgent, PilotGitError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

NEW_FILE_PATCH = (
    "--- /dev/null\n"
    "+++ b/pilot/app/backend/db.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+import sqlite3\n"
    "+DB_PATH = 'exam.db'\n"
)


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def make_pilot_repo(tmp_path, autocrlf="false"):
    """A tiny pilot workspace on branch pilot/main, like the real one."""
    repo = tmp_path / "pilot-workspace"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Setup")
    git(repo, "config", "user.email", "setup@example.com")
    git(repo, "config", "core.autocrlf", autocrlf)
    git(repo, "switch", "-q", "-c", "pilot/main")
    snapshot = repo / "outputs" / "approved_context" / "current_approved_context.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text('{"saved_at": "old"}\n', encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "setup")
    return repo, snapshot


def write_run_records(repo, snapshot, patch_text):
    """What --resume-patch leaves behind before the Git step runs."""
    proposals = repo / "outputs" / "proposed_changes"
    proposals.mkdir(parents=True, exist_ok=True)
    patch_path = proposals / "T1_backend_test.patch"
    report_path = proposals / "T1_backend_test_report.json"
    patch_path.write_text(patch_text, encoding="utf-8", newline="\n")
    report_path.write_text('{"task_id": "T1"}\n', encoding="utf-8")
    snapshot.write_text('{"saved_at": "new"}\n', encoding="utf-8")
    return patch_path, [patch_path, report_path, snapshot]


def test_patch_is_committed_on_a_new_agent_branch_by_the_agent(tmp_path):
    repo, snapshot = make_pilot_repo(tmp_path)
    patch_path, records = write_run_records(repo, snapshot, NEW_FILE_PATCH)

    result = PilotGitAgent(repo).apply_patch_on_new_branch(
        "T1", patch_path, ["pilot/app/backend/db.py"], "Add the database module", records
    )

    assert result.branch == "agent/T1-attempt-1"
    assert git(repo, "branch", "--show-current") == "agent/T1-attempt-1"
    assert git(repo, "log", "-2", "--format=%an <%ae>") == f"{AGENT_NAME} <{AGENT_EMAIL}>\n{AGENT_NAME} <{AGENT_EMAIL}>"
    assert git(repo, "show", "--name-only", "--format=", result.code_commit) == "pilot/app/backend/db.py"
    assert "T1_backend_test.patch" in git(repo, "show", "--name-only", "--format=", result.records_commit)
    # The base branch is untouched, and nothing is left uncommitted.
    assert git(repo, "log", "pilot/main", "--format=%s") == "setup"
    assert git(repo, "status", "--porcelain") == ""


def test_a_waiting_agent_branch_blocks_the_next_attempt_until_h3(tmp_path):
    repo, snapshot = make_pilot_repo(tmp_path)
    patch_path, records = write_run_records(repo, snapshot, NEW_FILE_PATCH)
    agent = PilotGitAgent(repo)
    agent.apply_patch_on_new_branch("T1", patch_path, ["pilot/app/backend/db.py"], "First", records)

    with pytest.raises(PilotGitError, match="H3"):
        agent.apply_patch_on_new_branch("T1", patch_path, ["pilot/app/backend/db.py"], "Second", records)

    # After a decision returns to pilot/main, the next attempt gets a new number.
    git(repo, "switch", "-q", "pilot/main")
    patch_path, records = write_run_records(repo, snapshot, NEW_FILE_PATCH)
    assert agent.apply_patch_on_new_branch("T1", patch_path, ["pilot/app/backend/db.py"], "Second", records).branch == "agent/T1-attempt-2"


def test_unexpected_changes_stop_before_any_branch_is_created(tmp_path):
    repo, snapshot = make_pilot_repo(tmp_path)
    patch_path, records = write_run_records(repo, snapshot, NEW_FILE_PATCH)
    (repo / "notes.txt").write_text("someone edited the pilot by hand", encoding="utf-8")

    with pytest.raises(PilotGitError, match="unexpected changes"):
        PilotGitAgent(repo).apply_patch_on_new_branch("T1", patch_path, ["pilot/app/backend/db.py"], "x", records)

    assert git(repo, "branch", "--list", "agent/*") == ""


def test_a_patch_that_does_not_apply_leaves_no_trace(tmp_path):
    repo, snapshot = make_pilot_repo(tmp_path)
    broken = "--- a/missing.py\n+++ b/missing.py\n@@ -1 +1 @@\n-old\n+new\n"
    patch_path, records = write_run_records(repo, snapshot, broken)

    with pytest.raises(PilotGitError):
        PilotGitAgent(repo).apply_patch_on_new_branch("T1", patch_path, ["missing.py"], "x", records)

    assert git(repo, "branch", "--show-current") == "pilot/main"
    assert git(repo, "branch", "--list", "agent/*") == ""


def test_undeclared_files_are_rejected_and_the_attempt_is_undone(tmp_path):
    repo, snapshot = make_pilot_repo(tmp_path)
    patch_path, records = write_run_records(repo, snapshot, NEW_FILE_PATCH)

    with pytest.raises(PilotGitError, match="declared"):
        PilotGitAgent(repo).apply_patch_on_new_branch("T1", patch_path, ["pilot/app/backend/other.py"], "x", records)

    assert git(repo, "branch", "--show-current") == "pilot/main"
    assert git(repo, "branch", "--list", "agent/*") == ""
    assert not (repo / "pilot" / "app" / "backend" / "db.py").exists()
    # The run records stay, so the failed attempt can be reviewed.
    assert patch_path.exists()
    assert '"new"' in snapshot.read_text(encoding="utf-8")


def test_refuses_to_work_in_the_workflow_folder(tmp_path):
    repo, _ = make_pilot_repo(tmp_path)

    with pytest.raises(PilotGitError, match="World 1"):
        PilotGitAgent(repo, workflow_root=repo).ensure_ready()


def test_changing_an_existing_file_works_with_windows_line_endings(tmp_path):
    # core.autocrlf=true is the Windows default: files on disk use CRLF.
    repo, snapshot = make_pilot_repo(tmp_path, autocrlf="true")
    module = repo / "pilot" / "app.py"
    module.parent.mkdir()
    module.write_text("x = 1\ny = 2\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "add app")
    git(repo, "rm", "-q", "--cached", "-r", ".")
    git(repo, "reset", "-q", "--hard")  # re-checkout: now CRLF on disk
    assert b"\r\n" in module.read_bytes()

    patch = "--- a/pilot/app.py\n+++ b/pilot/app.py\n@@ -1,2 +1,2 @@\n x = 1\n-y = 2\n+y = 3\n"
    patch_path, records = write_run_records(repo, snapshot, patch)

    result = PilotGitAgent(repo).apply_patch_on_new_branch("T1", patch_path, ["pilot/app.py"], "Change y", records)

    assert git(repo, "show", f"{result.code_commit}:pilot/app.py") == "x = 1\ny = 3"


def test_main_hands_the_agent_report_to_the_git_step(tmp_path):
    import json

    import main as workflow_main

    repo, snapshot = make_pilot_repo(tmp_path)
    patch_path, records = write_run_records(repo, snapshot, NEW_FILE_PATCH)
    report_path = records[1]
    report_path.write_text(
        json.dumps(
            {
                "task_id": "T1",
                "summary": "Add the file-backed SQLite module. It has no side effects on import.",
                "changed_files": [{"path": "pilot/app/backend/db.py", "purpose": "Database."}],
            }
        ),
        encoding="utf-8",
    )

    result = workflow_main.apply_patch_with_pilot_git_agent(
        project_root=repo,
        patch_path=patch_path,
        report_path=report_path,
        record_paths=records,
        git_agent=PilotGitAgent(repo, workflow_root=tmp_path / "workflow"),
    )

    assert result.branch == "agent/T1-attempt-1"
    assert git(repo, "log", "-1", "--format=%s", result.code_commit) == "[T1] Add the file-backed SQLite module"


def test_works_in_a_folder_with_hungarian_letters(tmp_path):
    # The real project lives under "Budapesti Műszaki és Gazdaságtudományi
    # Egyetem". On Windows this used to fail with "not a Git worktree root",
    # because git's UTF-8 path was read with the default Windows code page.
    folder = tmp_path / "Budapesti Műszaki és Gazdaságtudományi Egyetem"
    folder.mkdir()
    repo, snapshot = make_pilot_repo(folder)
    patch_path, records = write_run_records(repo, snapshot, NEW_FILE_PATCH)

    result = PilotGitAgent(repo).apply_patch_on_new_branch(
        "T1", patch_path, ["pilot/app/backend/db.py"], "Add the database module", records
    )

    assert result.branch == "agent/T1-attempt-1"


def test_records_left_by_a_failed_run_do_not_block_the_next_run(tmp_path):
    repo, snapshot = make_pilot_repo(tmp_path)
    # A failed earlier run left its proposal behind (untracked) ...
    old_proposal = repo / "outputs" / "proposed_changes" / "T1_backend_failed_run.patch"
    old_proposal.parent.mkdir(parents=True)
    old_proposal.write_text("old proposal\n", encoding="utf-8", newline="\n")
    # ... and the next run writes its own records.
    patch_path, records = write_run_records(repo, snapshot, NEW_FILE_PATCH)

    result = PilotGitAgent(repo).apply_patch_on_new_branch(
        "T1", patch_path, ["pilot/app/backend/db.py"], "Add the database module", records
    )

    # The old proposal is kept as evidence in the records commit, and the
    # code commit contains only the declared code file.
    assert "T1_backend_failed_run.patch" in git(repo, "show", "--name-only", "--format=", result.records_commit)
    assert git(repo, "show", "--name-only", "--format=", result.code_commit) == "pilot/app/backend/db.py"
    assert git(repo, "status", "--porcelain") == ""


def test_hand_made_code_changes_still_block_a_run(tmp_path):
    repo, snapshot = make_pilot_repo(tmp_path)
    patch_path, records = write_run_records(repo, snapshot, NEW_FILE_PATCH)
    (repo / "pilot").mkdir(exist_ok=True)
    (repo / "pilot" / "hand_edit.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(PilotGitError, match="unexpected changes"):
        PilotGitAgent(repo).apply_patch_on_new_branch(
            "T1", patch_path, ["pilot/app/backend/db.py"], "x", records
        )
