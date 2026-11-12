"""AOA CLI — Unified command-line interface for the Armorie-of-Anubis.



Commands:

    aoa init                    Initialize all Arms

    aoa arms list               List registered Arms

    aoa arms status             Show status of all Arms

    aoa run <arm> <capability>  Run a capability on an Arm

    aoa workflow start <file>   Start a cross-Arm workflow

    aoa session start           Start a new session

    aoa session milestones      List session milestones

    aoa session complete        Complete and save the current session

"""



import sys

import json

import argparse

from pathlib import Path

from typing import List

# Wire Azazael sub-system
_AZAZAEL_SRC = Path(__file__).resolve().parents[3] / 'src'
if _AZAZAEL_SRC.exists():
    sys.path.insert(0, str(_AZAZAEL_SRC))

# Ensure AOA is importable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here



try:

    from core.arm_registry import ArmRegistry

    from core.event_bus import get_event_bus, AOEvent, AOEventType

    from core.session_manager import get_session_manager, MilestoneStatus

    from core.router import CrossArmRouter

except ImportError as exc:

    note_failure("aoa_cli:L56", exc)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from core.arm_registry import ArmRegistry

    from core.event_bus import get_event_bus, AOEvent, AOEventType

    from core.session_manager import get_session_manager, MilestoneStatus

    from core.router import CrossArmRouter





def cmd_init(args):

    """Initialize all Arms."""

    # Import arm modules to trigger @register_arm decorators
    try:
        import arms.azazael_arm
    except Exception as exc:
        note_failure("aoa_cli:L79", exc)
    try:
        import arms.codex_arm
    except Exception as exc:
        note_failure("aoa_cli:L83", exc)
    try:
        import arms.eve_arm
    except Exception as exc:
        note_failure("aoa_cli:L87", exc)
    try:
        import arms.libra_arm
    except Exception as exc:
        note_failure("aoa_cli:L91", exc)
    try:
        import arms.support_arms
    except Exception as exc:
        note_failure("aoa_cli:L95", exc)
    try:
        import arms.kali_arm
    except Exception as exc:
        note_failure("aoa_cli:L99", exc)
    registry = ArmRegistry()

    print("AOA — Armorie-of-Anubis v1.0.0")

    print(f"Discovered {len(registry.arms)} Arms across {len(registry.get_categories())} categories")

    print()

    results = registry.initialize_all()

    for arm_id, success in results.items():

        status = "  OK" if success else "FAIL"

        print(f"  [{status}] {arm_id}")

    return 0





def cmd_arms_list(args):

    """List all registered Arms."""

    # Import arm modules to trigger @register_arm decorators
    try:
        import arms.azazael_arm
    except Exception as exc:
        note_failure("aoa_cli:L130", exc)
    try:
        import arms.codex_arm
    except Exception as exc:
        note_failure("aoa_cli:L134", exc)
    try:
        import arms.eve_arm
    except Exception as exc:
        note_failure("aoa_cli:L138", exc)
    try:
        import arms.libra_arm
    except Exception as exc:
        note_failure("aoa_cli:L142", exc)
    try:
        import arms.support_arms
    except Exception as exc:
        note_failure("aoa_cli:L146", exc)
    try:
        import arms.kali_arm
    except Exception as exc:
        note_failure("aoa_cli:L150", exc)
    registry = ArmRegistry()

    arms = registry.list_arms()

    if not arms:

        print("No Arms registered. Run 'aoa init' first.")

        return 0

    print(f"{"ARM ID":<20} {"NAME":<30} {"CATEGORY":<15} {"PRI":>3} {"CAPS":>4}")

    print("-" * 80)

    for arm in arms:

        print(f"{arm['arm_id']:<20} {arm['name']:<30} {arm['category']:<15} "

              f"{arm['priority']:>3} {arm['capabilities']:>4}")

    return 0





def cmd_arms_status(args):

    """Show status report for all Arms."""

    # Import arm modules to trigger @register_arm decorators
    try:
        import arms.azazael_arm
    except Exception as exc:
        note_failure("aoa_cli:L185", exc)
    try:
        import arms.codex_arm
    except Exception as exc:
        note_failure("aoa_cli:L189", exc)
    try:
        import arms.eve_arm
    except Exception as exc:
        note_failure("aoa_cli:L193", exc)
    try:
        import arms.libra_arm
    except Exception as exc:
        note_failure("aoa_cli:L197", exc)
    try:
        import arms.support_arms
    except Exception as exc:
        note_failure("aoa_cli:L201", exc)
    try:
        import arms.kali_arm
    except Exception as exc:
        note_failure("aoa_cli:L205", exc)
    registry = ArmRegistry()

    report = registry.get_status_report()

    print(json.dumps(report, indent=2))

    return 0





def cmd_run(args):

    """Run a capability on a specific Arm."""

    # Import arm modules to trigger @register_arm decorators
    try:
        import arms.azazael_arm
    except Exception as exc:
        note_failure("aoa_cli:L226", exc)
    try:
        import arms.codex_arm
    except Exception as exc:
        note_failure("aoa_cli:L230", exc)
    try:
        import arms.eve_arm
    except Exception as exc:
        note_failure("aoa_cli:L234", exc)
    try:
        import arms.libra_arm
    except Exception as exc:
        note_failure("aoa_cli:L238", exc)
    try:
        import arms.support_arms
    except Exception as exc:
        note_failure("aoa_cli:L242", exc)
    try:
        import arms.kali_arm
    except Exception as exc:
        note_failure("aoa_cli:L246", exc)
    registry = ArmRegistry()

    registry.initialize_all()

    payload = json.loads(args.payload) if args.payload else {}

    router = CrossArmRouter(registry)

    result = router.route_task(args.arm, args.capability, payload)

    print(json.dumps(result, indent=2))

    return 0 if result.get("status") == "ok" else 1





def cmd_session_start(args):

    """Start a new AOA session."""

    sm = get_session_manager()

    session = sm.create_session(name=args.name or "AOA Session", tags=args.tags.split(",") if args.tags else [])

    print(f"Session started: {session.session_id}")

    print(f"  Name: {session.session_name}")

    print(f"  Started: {session.started_at}")

    return 0





def cmd_session_milestones(args):

    """List session milestones."""

    sm = get_session_manager()

    session = sm.get_active_session()

    if session is None:

        print("No active session. Start one with 'aoa session start'.")

        return 0

    summary = sm.get_session_summary()

    print(json.dumps(summary, indent=2))

    return 0





def cmd_session_complete(args):

    """Complete and save the current session."""

    sm = get_session_manager()

    session = sm.complete_session()

    if session is None:

        print("No active session to complete.")

        return 0

    filepath = sm.save_session(session.session_id)

    print(f"Session completed: {session.session_id}")

    print(f"  Milestones: {len(session.milestones)}")

    print(f"  Saved to: {filepath}")

    return 0





def cmd_workflow_start(args):

    """Start a cross-Arm workflow from a JSON file."""

    # Import arm modules to trigger @register_arm decorators
    try:
        import arms.azazael_arm
    except Exception as exc:
        note_failure("aoa_cli:L345", exc)
    try:
        import arms.codex_arm
    except Exception as exc:
        note_failure("aoa_cli:L349", exc)
    try:
        import arms.eve_arm
    except Exception as exc:
        note_failure("aoa_cli:L353", exc)
    try:
        import arms.libra_arm
    except Exception as exc:
        note_failure("aoa_cli:L357", exc)
    try:
        import arms.support_arms
    except Exception as exc:
        note_failure("aoa_cli:L361", exc)
    try:
        import arms.kali_arm
    except Exception as exc:
        note_failure("aoa_cli:L365", exc)
    registry = ArmRegistry()

    registry.initialize_all()

    router = CrossArmRouter(registry)



    with open(args.file, encoding="utf-8") as f:

        wf_data = json.load(f)



    workflow = router.create_workflow(

        name=wf_data.get("name", "Workflow"),

        description=wf_data.get("description", ""))



    from core.router import WorkflowStep

    for step_data in wf_data.get("steps", []):

        step = WorkflowStep(

            step_id=step_data["step_id"],

            arm_id=step_data["arm_id"],

            capability=step_data["capability"],

            payload=step_data.get("payload", {}),

            depends_on=step_data.get("depends_on", []),

        )

        workflow.add_step(step)



    print(f"Executing workflow: {workflow.name}")

    print(f"  Steps: {len(workflow.steps)}")

    results = router.execute_workflow(workflow.workflow_id)

    for r in results:

        status = "  OK" if r["status"] == "completed" else "FAIL"

        print(f"  [{status}] {r['step_id']}: {r['arm_id']}.{r['capability']}")

    return 0





def main():

    parser = argparse.ArgumentParser(

        description="AOA — Armorie-of-Anubis Umbrella Orchestrator",

        prog="aoa")

    subparsers = parser.add_subparsers(dest="command", help="Commands")



    # init

    subparsers.add_parser("init", help="Initialize all Arms")



    # arms list

    subparsers.add_parser("arms", help="List Arms").add_argument(

        "subcommand", choices=["list", "status"], help="List or status")



    # run

    run_parser = subparsers.add_parser("run", help="Run a capability on an Arm")

    run_parser.add_argument("arm", help="Arm ID")

    run_parser.add_argument("capability", help="Capability to run")

    run_parser.add_argument("--payload", "-p", help="JSON payload")



    # session

    sess_parser = subparsers.add_parser("session", help="Session management")

    sess_sub = sess_parser.add_subparsers(dest="subcommand")

    start_p = sess_sub.add_parser("start", help="Start a session")

    start_p.add_argument("--name", "-n", help="Session name")

    start_p.add_argument("--tags", help="Comma-separated tags")

    sess_sub.add_parser("milestones", help="List milestones")

    sess_sub.add_parser("complete", help="Complete and save session")



    # workflow

    wf_parser = subparsers.add_parser("workflow", help="Workflow management")

    wf_sub = wf_parser.add_subparsers(dest="subcommand")

    start_wf = wf_sub.add_parser("start", help="Start a workflow")

    start_wf.add_argument("file", help="JSON workflow file")



    args = parser.parse_args()



    if args.command is None:

        parser.print_help()

        return 0



    commands = {

        "init": cmd_init,

    }



    if args.command == "init":

        return cmd_init(args)

    elif args.command == "arms":

        if args.subcommand == "list":

            return cmd_arms_list(args)

        elif args.subcommand == "status":

            return cmd_arms_status(args)

    elif args.command == "run":

        return cmd_run(args)

    elif args.command == "session":

        if args.subcommand == "start":

            return cmd_session_start(args)

        elif args.subcommand == "milestones":

            return cmd_session_milestones(args)

        elif args.subcommand == "complete":

            return cmd_session_complete(args)

    elif args.command == "workflow":

        if args.subcommand == "start":

            return cmd_workflow_start(args)



    parser.print_help()

    return 0





if __name__ == "__main__":

    sys.exit(main())

