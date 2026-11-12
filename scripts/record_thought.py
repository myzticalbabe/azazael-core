"""record_thought.py — Append a chain-of-thought trace as training data.

Large-model reasoning doesn't enter the local loop by itself: this CLI is
the bridge. After completing (or failing) a task, record HOW it was
approached — task, reasoning summary, actions taken, outcome. Rows land as
event_type='thought_process' from source 'ReasoningTrace' and are consumed
two ways:

  1. Q-learning (RLTrainLoop): info-severity rows with data.success refine
     rewards exactly like any other record — reasoning states join the
     ops policy.
  2. DPO preferences: the DPO cycle pairs successful vs failed traces for
     the same task family ('thought_preferences' experiment), teaching
     which reasoning patterns lead to success.

Usage:
    python scripts/record_thought.py --task "fix HOF seeding" \\
        --reasoning "ranked by margin not raw score because ..." \\
        --actions "read,edit,bash,pytest" --success
    python scripts/record_thought.py --task "..." --reasoning-file trace.txt --fail
    cat trace.txt | python scripts/record_thought.py --task "..." --fail
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> int:
    p = argparse.ArgumentParser(description="Record a chain-of-thought trace")
    p.add_argument("--task", required=True, help="What was being attempted")
    p.add_argument("--reasoning", default="", help="How it was approached (the CoT)")
    p.add_argument("--reasoning-file", default="", help="File holding the reasoning text")
    p.add_argument("--actions", default="", help="Comma-separated tools/steps used")
    p.add_argument("--success", action="store_true", help="Task succeeded")
    p.add_argument("--fail", action="store_true", help="Task failed")
    p.add_argument("--session", default="", help="Session id (default: timestamp)")
    args = p.parse_args()

    reasoning = args.reasoning
    if args.reasoning_file:
        reasoning = Path(args.reasoning_file).read_text(encoding="utf-8")
    if not reasoning and not sys.stdin.isatty():
        reasoning = sys.stdin.read()
    reasoning = (reasoning or "").strip()[:4000]
    if not reasoning:
        print("empty reasoning — nothing recorded", file=sys.stderr)
        return 2

    success = True
    if args.fail:
        success = False
    elif not args.success:
        print("specify --success or --fail", file=sys.stderr)
        return 2

    from CORE.direct_memory import DirectMemory
    dm = DirectMemory()
    actions = [a.strip() for a in args.actions.split(",") if a.strip()]
    row_id = dm.record_thought(
        task=args.task,
        reasoning=reasoning,
        actions=actions,
        success=success,
        source="ReasoningTrace",
        tags=["training"],
        session_id=args.session or time.strftime("%Y-%m-%dT%H:%M"),
    )
    print("recorded thought_process id=%s success=%s (%d chars)" % (
        row_id, success, len(reasoning)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
