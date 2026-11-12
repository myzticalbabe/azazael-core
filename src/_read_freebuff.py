import sys

sys.path.insert(0, "C:/Azazael/azazael/src")
from CORE.LaneMessageBoard import LaneMessageBoard

b = LaneMessageBoard(lane="freebuff")
msgs = b.poll_tasks("freebuff")
print("count:", len(msgs))
for m in msgs:
    print("#", m.task_id, "|", m.action, "|", m.detail[:100].encode("ascii", "ignore").decode())
