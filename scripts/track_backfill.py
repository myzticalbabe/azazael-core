"""Backfill training tracks to >=30 runs each, sequentially.

One subprocess per run (same entrypoint the scheduler uses); the
orchestrator's single-instance lock serializes with any concurrent
scheduled fire. Progress appends to backfill.log; rerun-safe (recomputes
shortfalls from the store at start).
"""
import sys, json, time, subprocess, sqlite3, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from CORE.local_memory import get_memory_store

TARGET = 30
PYTHON = r'C:\Users\clint\AppData\Local\Programs\Python\Python315\python.exe'
ORCH = r'C:\Azazael\azazael\scripts\run_training_orchestrator.py'
LOG = r'C:\Azazael\azazael\logs\track_backfill.log'
TRACK_EVENTS = ('sb3_training_orchestrated', 'grpo_training_orchestrated',
                'dpo_training_orchestrated', 'offline_training_orchestrated',
                'training_run_complete', 'dreamtune_training_orchestrated',
                'wormga_training_orchestrated')


def counts():
    store = get_memory_store()
    c = sqlite3.connect(str(getattr(store, 'db_path',
                                    'C:/Users/clint/.azazael/memory_store.db')), timeout=30)
    trk = {}
    for (d,) in c.execute(
            "SELECT data FROM memories WHERE event_type IN (%s)" %
            ",".join("?" * len(TRACK_EVENTS)), TRACK_EVENTS):
        try:
            a = (json.loads(d) if isinstance(d, str) else (d or {})).get('algo', '?')
        except Exception:
            a = '?'
        trk[a] = trk.get(a, 0) + 1
    c.close()
    return trk


def log(msg):
    line = '%s %s' % (time.strftime('%Y-%m-%dT%H:%M:%S'), msg)
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def main():
    trk = counts()
    plan = []
    for algo, n in sorted(trk.items()):
        if algo == '?' or n >= TARGET:
            continue
        plan.extend([algo] * (TARGET - n))
    # RL may be missing entirely if never run under new tag
    if trk.get('RL', 0) < TARGET and 'RL' not in [a for a in plan]:
        plan.extend(['RL'] * (TARGET - trk.get('RL', 0)))
    log('shortfall plan: %d runs across %d tracks' % (len(plan), len(set(plan))))
    ok, fail = 0, 0
    for i, algo in enumerate(plan, 1):
        t0 = time.time()
        try:
            p = subprocess.run([PYTHON, ORCH, '--algo', algo],
                               capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace")
            good = p.returncode == 0
        except Exception as e:
            good = False
            log('run %d/%d %s EXC %s' % (i, len(plan), algo, str(e)[:120]))
        if good:
            ok += 1
        else:
            fail += 1
            try:
                tail = (p.stderr or p.stdout or '')[-300:]
            except Exception:
                tail = ''
            log('run %d/%d %s FAIL rc=%s %s' % (i, len(plan), algo, p.returncode, tail))
        if i % 10 == 0 or i == len(plan):
            log('progress %d/%d ok=%d fail=%d (%.0fs elapsed)' % (
                i, len(plan), ok, fail, time.time() - _t_start))
    log('DONE ok=%d fail=%d' % (ok, fail))


_t_start = time.time()
if __name__ == '__main__':
    main()
