"""H2b gate: the human approves, revises, or rejects the pilot's UI stack.

The architecture approved at H2 leaves the frontend framework open, and the
Frontend Developer Agent needs one final choice. The human only decides:
- approve: the proposal is stored as "ui_stack" in the approved snapshot.
- revise:  the human's feedback goes to the Architect, which proposes again.
- reject:  nothing is stored.

Every decision is saved as a record file (outputs/approvals/). This gate does
not commit it: files under outputs/ are run records, and the Pilot Git Agent
commits them on the next agent branch.
"""

from datetime import datetime

from agents.h3_review import ask_choice, write_decision_record

GATE = "H2b"
RECORD_NAME = "UI-stack"  # This gate belongs to the whole project, not to one task.


def print_list(title, items):
    print(f"\n{title}:")
    for item in items:
        print(f"  - {item}")


def show_proposal(proposal):
    print("\n" + "=" * 70)
    print("H2b - HUMAN APPROVAL: UI STACK")
    print("=" * 70)
    for label, field in (
        ("Framework", "framework"),
        ("Language", "language"),
        ("Build tool", "build_tool"),
        ("Test runner", "test_runner"),
        ("Package manager", "package_manager"),
        ("Frontend folder", "frontend_folder"),
    ):
        print(f"{label}: {getattr(proposal, field)}")
        print(f"  Why: {getattr(proposal.reasons, field)}")
    print_list("Alternatives considered", proposal.alternatives_considered)
    print_list("Risks", proposal.risks)
    print("=" * 70)


def run_h2b_review(*, orchestrator, architect, specification, snapshot, pilot_root, store_ui_stack, ask=input):
    """Show the proposal, take the human's decision, and act on it.

    store_ui_stack(proposal): saves the approved proposal in the snapshot.
    Returns a dict with the final decision, or None if no proposal could be made.
    """

    def propose(previous_proposal=None, feedback=None):
        """Ask the Architect. None means the run must stop (the reason is printed)."""
        try:
            return architect.propose_ui_stack(
                specification,
                snapshot["architecture"],
                snapshot["tasks"],
                previous_proposal=previous_proposal,
                feedback=feedback,
            )
        except Exception as error:
            print(f"\nThe Architect could not make a valid UI stack proposal: {error}")
            print("Nothing was stored.")
            return None

    proposal = propose()

    while proposal is not None:
        show_proposal(proposal)
        decision = ask_choice(ask, "Enter 'approve', 'revise', or 'reject': ", ("approve", "revise", "reject"))

        feedback = ""
        while decision == "revise" and not feedback:
            feedback = ask("Your feedback for the Architect: ").strip()

        try:
            # Store before writing the record, so a saved "approve" record can
            # never exist for a UI stack that did not reach the snapshot.
            if decision == "approve":
                store_ui_stack(proposal)

            record_path = write_decision_record(
                pilot_root,
                RECORD_NAME,
                {
                    "gate": GATE,
                    "decision": decision,
                    "decided_at": datetime.now().isoformat(timespec="seconds"),
                    "proposal": proposal.model_dump(),
                    "feedback": feedback,
                },
                gate=GATE,
            )
        except OSError as error:
            print(f"\nCould not save this decision: {error}")
            print("Stopped. Check the snapshot and outputs/approvals/ in the pilot workspace.")
            return None

        orchestrator.record_approval(GATE, decision, feedback)
        print(f"\nDecision record: {record_path}")

        if decision == "approve":
            print("H2b approved: the UI stack is stored in the approved snapshot.")
            return {"decision": "approve", "ui_stack": proposal.model_dump()}

        if decision == "reject":
            print("H2b rejected: nothing was stored in the approved snapshot.")
            return {"decision": "reject"}

        print("\nThe Architect is revising the proposal...")
        proposal = propose(previous_proposal=proposal, feedback=feedback)

    return None
