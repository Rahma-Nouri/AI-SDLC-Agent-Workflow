"""I2 (issue #78): "reject" is always safe.

Reject deletes the agent's branch, leaves pilot/main unchanged, and never loses a
run record (anything under outputs/), even an uncommitted one. A crash or Ctrl+C
in the middle of an H2/H3 review leaves no half-done change behind.

All offline: temporary Git repos, a fake Gemini client, and a fake backend agent.
"""

import json
import shutil

import pytest

from agents.pilot_git_agent import PilotGitAgent, PilotGitError
from test_correction_loop import BUGGY, CODE, FIXED, FakeBackendAgent, diff, result
from test_h2_revise_agent import ARCH, OLD, REVISED, agent_branches, make_architect, make_h2_setup, review, script
from test_h3_review import FakeGitHub, add_agent_report, git, make_review_setup
from test_h3_review import review as h3_review

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

SNAPSHOT = "outputs/approved_context/current_approved_context.json"


def changes(repo):
    """Every uncommitted path (modified or untracked)."""
    lines = git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()
    # Each line is "XY path". git() strips the output, which can eat the first
    # line's leading space, so split on whitespace instead of cutting 3 chars.
    return sorted(line.split(maxsplit=1)[1] for line in lines)


def interrupt(*_args):
    """The human presses Ctrl+C at this prompt."""
    raise KeyboardInterrupt


def interrupt_git_commit(monkeypatch, message_start):
    """Make the Pilot Git Agent's first 'git commit' with this message raise Ctrl+C."""
    original = PilotGitAgent._git
    state = {"done": False}

    def fake_git(self, *args, check=True):
        if not state["done"] and "commit" in args and any(str(a).startswith(message_start) for a in args):
            state["done"] = True
            raise KeyboardInterrupt
        return original(self, *args, check=check)

    monkeypatch.setattr(PilotGitAgent, "_git", fake_git)


# --- problem 1: uncommitted run records must not block or be lost --------------


def test_h2_reject_keeps_an_uncommitted_h2_approve_record(tmp_path):
    repo, remote, _ = make_h2_setup(tmp_path)
    # An earlier approve-only H2 review left its record uncommitted on pilot/main (I1).
    assert review(repo, script("approve")) is True
    (approve_record,) = (repo / "outputs" / "approvals").glob("architecture_H2_approve_*.json")
    approve_text = approve_record.read_text(encoding="utf-8")
    before = git(repo, "rev-parse", "pilot/main")

    # Revise, then reject the revision; then reject the old architecture to leave H2.
    result_ = review(repo, script("revise", "Add a cache section", "reject", "Not needed", "reject"), make_architect(REVISED))

    assert result_ is False
    assert git(repo, "branch", "--show-current") == "pilot/main"
    assert agent_branches(repo) == ""  # the revision branch was deleted
    assert git(repo, "rev-parse", "pilot/main") == before == git(remote, "rev-parse", "pilot/main")
    assert (repo / ARCH).read_text(encoding="utf-8") == OLD
    # The uncommitted approve record survived, unchanged, and the reject record exists.
    assert approve_record.read_text(encoding="utf-8") == approve_text
    assert len(list((repo / "outputs" / "approvals").glob("architecture_H2_reject_*.json"))) == 1
    assert all(path.startswith("outputs/") for path in changes(repo))


def test_h3_reject_keeps_uncommitted_records_even_when_pilot_main_has_another_version(tmp_path):
    repo, _ = make_review_setup(tmp_path, BUGGY, tests_passed=False)
    snapshot = repo / SNAPSHOT
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    # The snapshot differs on the agent branch and on pilot/main, and the branch
    # has a newer, uncommitted version: a plain "git switch" refuses this case.
    snapshot.write_text('{"saved_at": "branch"}\n', encoding="utf-8", newline="\n")
    git(repo, "add", SNAPSHOT)
    git(repo, "commit", "-q", "-m", "agent: snapshot")
    git(repo, "switch", "-q", "pilot/main")
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text('{"saved_at": "main"}\n', encoding="utf-8", newline="\n")
    git(repo, "add", SNAPSHOT)
    git(repo, "commit", "-q", "-m", "snapshot on pilot/main")
    git(repo, "switch", "-q", "agent/T1-attempt-1")
    snapshot.write_text('{"saved_at": "uncommitted"}\n', encoding="utf-8", newline="\n")
    # Plus an untracked record, like the H2 approve record from I1.
    approvals = repo / "outputs" / "approvals"
    approvals.mkdir(parents=True)
    (approvals / "architecture_H2_approve_1.json").write_text('{"gate": "H2"}\n', encoding="utf-8", newline="\n")
    before = git(repo, "rev-parse", "pilot/main")

    outcome, _ = h3_review(repo, ["reject", "Wrong approach"], github=FakeGitHub())

    assert outcome["decision"] == "reject"
    assert git(repo, "branch", "--show-current") == "pilot/main"
    assert git(repo, "branch", "--list", "agent/*") == ""
    assert git(repo, "rev-parse", "pilot/main") == before  # pilot/main unchanged
    assert snapshot.read_text(encoding="utf-8") == '{"saved_at": "uncommitted"}\n'
    assert (approvals / "architecture_H2_approve_1.json").read_text(encoding="utf-8") == '{"gate": "H2"}\n'
    assert len(list(approvals.glob("T1_H3_reject_*.json"))) == 1
    assert all(path.startswith("outputs/") for path in changes(repo))


def test_reject_still_blocks_hand_made_changes_outside_outputs(tmp_path):
    repo, _ = make_review_setup(tmp_path, BUGGY, tests_passed=False)
    (repo / CODE).write_text("# edited by hand\n", encoding="utf-8", newline="\n")
    (repo / "outputs" / "note.json").write_text("{}\n", encoding="utf-8", newline="\n")

    with pytest.raises(PilotGitError, match="unexpected changes: " + CODE):
        h3_review(repo, ["reject", "Wrong approach"], github=FakeGitHub())

    # Nothing happened: same branch, the branch exists, the hand-made change is still there.
    assert git(repo, "branch", "--show-current") == "agent/T1-attempt-1"
    assert (repo / CODE).read_text(encoding="utf-8") == "# edited by hand\n"
    assert (repo / "outputs" / "note.json").exists()


def test_h2_reject_with_a_hand_made_change_keeps_the_branch(tmp_path, capsys):
    repo, _, _ = make_h2_setup(tmp_path)

    def edit_by_hand(_prompt):
        (repo / "pilot" / "pilot_specification.md").write_text("edited\n", encoding="utf-8")
        return "reject"

    result_ = review(repo, script("revise", "Add a cache section", edit_by_hand, "Not needed"), make_architect(REVISED))

    assert result_ is False
    assert "unexpected changes: pilot/pilot_specification.md" in capsys.readouterr().out
    assert "agent/architecture-revision-1" in agent_branches(repo)  # not deleted


# --- problem 2: a crash (exception or Ctrl+C) in the middle of a review ---------


def test_h2_ctrl_c_after_a_revision_goes_back_to_pilot_main_and_keeps_the_branch(tmp_path, capsys):
    repo, _, _ = make_h2_setup(tmp_path)
    before = git(repo, "rev-parse", "pilot/main")

    with pytest.raises(KeyboardInterrupt):
        review(repo, script("revise", "Add a cache section", interrupt), make_architect(REVISED))

    assert git(repo, "branch", "--show-current") == "pilot/main"
    assert git(repo, "rev-parse", "pilot/main") == before
    assert (repo / ARCH).read_text(encoding="utf-8") == OLD
    assert all(path.startswith("outputs/") for path in changes(repo))
    # The revision (and its committed notes record) is kept on its branch, not lost.
    assert "agent/architecture-revision-1" in agent_branches(repo)
    assert "agent/architecture-revision-1" in capsys.readouterr().out
    # The next H2 review works normally (a new revision branch is created).
    assert review(repo, script("revise", "Add a cache section", "approve"), make_architect(REVISED)) is True


def test_h2_ctrl_c_while_committing_the_revision_undoes_it(tmp_path, monkeypatch):
    repo, _, _ = make_h2_setup(tmp_path)
    before = git(repo, "rev-parse", "pilot/main")
    interrupt_git_commit(monkeypatch, "[architecture] Revise")

    with pytest.raises(KeyboardInterrupt):
        review(repo, script("revise", "Add a cache section"), make_architect(REVISED))

    assert git(repo, "branch", "--show-current") == "pilot/main"
    assert git(repo, "rev-parse", "pilot/main") == before
    assert (repo / ARCH).read_text(encoding="utf-8") == OLD
    # Only run records are left (the revise record), never a half-written architecture.
    left = changes(repo)
    assert left and all(path.startswith("outputs/approvals/architecture_H2_revise_") for path in left)


def test_h3_ctrl_c_during_a_fix_leaves_no_half_applied_change(tmp_path, monkeypatch):
    repo, _ = make_review_setup(tmp_path, BUGGY, tests_passed=False)
    backend = FakeBackendAgent(result(diff(CODE, BUGGY, FIXED), [CODE]))
    interrupt_git_commit(monkeypatch, "[T1] Fix:")

    with pytest.raises(KeyboardInterrupt):
        h3_review(repo, ["revise", "add must return a sum", "no"], backend=backend, github=FakeGitHub())

    # The branch keeps waiting for H3 (that is its normal state) without the half-done fix.
    assert git(repo, "branch", "--show-current") == "agent/T1-attempt-1"
    assert (repo / CODE).read_text(encoding="utf-8") == BUGGY
    assert all(path.startswith("outputs/") for path in changes(repo))
    # So the human can simply run the H3 review again, and reject is safe.
    monkeypatch.undo()
    outcome, _ = h3_review(repo, ["reject", "Start over"], github=FakeGitHub())
    assert outcome["decision"] == "reject" and git(repo, "branch", "--show-current") == "pilot/main"


def test_the_next_command_says_what_to_do_on_a_left_over_branch(tmp_path):
    repo, _ = make_review_setup(tmp_path, BUGGY, tests_passed=False)
    with pytest.raises(PilotGitError, match=r"python main\.py --h3-review"):
        PilotGitAgent(repo).ensure_ready()

    git(repo, "switch", "-q", "pilot/main")
    git(repo, "switch", "-q", "-c", "agent/architecture-revision-1")
    with pytest.raises(PilotGitError, match=r"interrupted H2 review.*git -C .* switch pilot/main"):
        PilotGitAgent(repo).ensure_ready()



# --- follow-up 1: reject keeps the records the branch committed -----------------


def only_record(folder, pattern):
    (path,) = folder.glob(pattern)
    return json.loads(path.read_text(encoding="utf-8"))


def test_h3_reject_copies_the_branch_records_to_pilot_main_without_code(tmp_path):
    repo, _ = make_review_setup(tmp_path, BUGGY, tests_passed=False)
    # pilot/main already has a committed record with the same name as one the
    # branch changed: the pilot/main version must not be overwritten.
    git(repo, "switch", "-q", "pilot/main")
    (repo / "outputs" / "approvals").mkdir(parents=True)
    shared = repo / "outputs" / "approvals" / "shared.json"
    shared.write_text('{"from": "main"}\n', encoding="utf-8", newline="\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "a record on pilot/main")
    git(repo, "switch", "-q", "agent/T1-attempt-1")
    git(repo, "merge", "-q", "--no-edit", "pilot/main")
    shared.write_text('{"from": "branch"}\n', encoding="utf-8", newline="\n")
    git(repo, "commit", "-q", "-am", "agent: changes the shared record")
    add_agent_report(repo, "T1_backend_20261102_110000_000000_report.json", unresolved_issues=["x"])
    before = git(repo, "rev-parse", "pilot/main")

    outcome, _ = h3_review(repo, ["reject", "Wrong approach"], github=FakeGitHub())

    assert outcome["decision"] == "reject" and git(repo, "branch", "--list", "agent/*") == ""
    assert git(repo, "rev-parse", "pilot/main") == before  # nothing committed on pilot/main
    expected = [
        "outputs/proposed_changes/T1_backend_20261102_110000_000000_report.json",
        "outputs/test_results/T1_retest_20261102_220000_000000.json",
    ]
    for path in expected:
        assert (repo / path).is_file()
    assert shared.read_text(encoding="utf-8") == '{"from": "main"}\n'  # not overwritten
    # Never pilot code or tests from the rejected branch.
    assert not (repo / CODE).exists() and not (repo / "tests").exists()
    record = only_record(repo / "outputs" / "approvals", "T1_H3_reject_*.json")
    assert record["copied_records"] == expected
    # They are uncommitted files on pilot/main, so the next agent branch commits them.
    assert sorted(changes(repo)) == sorted(expected + ["outputs/approvals/" + p.name for p in (repo / "outputs" / "approvals").glob("T1_H3_reject_*.json")])


def test_h2_reject_copies_the_committed_revise_record(tmp_path):
    repo, _, _ = make_h2_setup(tmp_path)
    before = git(repo, "rev-parse", "pilot/main")

    review(repo, script("revise", "Add a cache section", "reject", "Not needed", "reject"), make_architect(REVISED))

    approvals = repo / "outputs" / "approvals"
    (revise,) = approvals.glob("architecture_H2_revise_*.json")  # committed on the deleted branch
    assert json.loads(revise.read_text(encoding="utf-8"))["notes"] == "Add a cache section"
    assert (repo / ARCH).read_text(encoding="utf-8") == OLD  # pilot code is not copied
    assert git(repo, "rev-parse", "pilot/main") == before and agent_branches(repo) == ""
    record = only_record(approvals, "architecture_H2_reject_*.json")
    assert record["copied_records"] == [f"outputs/approvals/{revise.name}"]


# --- follow-up 2: leftover records do not block an H3 approve --------------------


def test_h3_approve_commits_leftover_records_and_merges(tmp_path):
    repo, remote = make_review_setup(tmp_path, FIXED, tests_passed=True)
    leftover = repo / "outputs" / "approvals" / "architecture_H2_approve_1.json"
    leftover.parent.mkdir(parents=True)
    leftover.write_text('{"gate": "H2"}\n', encoding="utf-8", newline="\n")

    outcome, _ = h3_review(repo, ["approve", "Looks good", "no"], github=FakeGitHub())

    assert outcome["decision"] == "approve"
    assert git(repo, "branch", "--show-current") == "pilot/main"
    assert "outputs/approvals/architecture_H2_approve_1.json" in git(repo, "ls-files").splitlines()
    assert changes(repo) == []
    assert git(remote, "rev-parse", "pilot/main") == git(repo, "rev-parse", "pilot/main")


def test_h3_approve_is_still_blocked_by_hand_made_changes(tmp_path):
    repo, remote = make_review_setup(tmp_path, FIXED, tests_passed=True)
    before = git(repo, "rev-parse", "pilot/main")
    branch_head = git(repo, "rev-parse", "HEAD")
    (repo / CODE).write_text("# edited by hand\n", encoding="utf-8", newline="\n")

    with pytest.raises(PilotGitError, match="unexpected changes: " + CODE):
        h3_review(repo, ["approve", "Looks good", "no"], github=FakeGitHub())

    assert git(repo, "branch", "--show-current") == "agent/T1-attempt-1"
    assert git(repo, "rev-parse", "HEAD") == branch_head  # nothing committed
    assert git(repo, "rev-parse", "pilot/main") == before == git(remote, "rev-parse", "pilot/main")
    assert not list((repo / "outputs").glob("approvals/T1_H3_approve_*.json"))  # no approve record
    assert (repo / CODE).read_text(encoding="utf-8") == "# edited by hand\n"
