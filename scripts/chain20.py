"""Run the entire chain 20x: dream + metacog + training rotation, sequentially.

Each step is the same entrypoint the scheduler uses; per-script
single-instance locks serialize with scheduled fires (which SKIP while
a chain step holds its lock). Progress to chain20.log. Rerun-safe:
each round is independent; failures logged, chain continues.
"""
import os
import subprocess
import time
from pathlib import Path

#: This script drives ITS OWN checkout. SCRIPTS, LOG and the step cwd used to
#: hardcode C:\Users\clint\azazael -- so the copy of this file in a second
#: checkout drove the FIRST one's tree and log, and a run from here silently
#: operated on the wrong repo (both copies pointed at the same root). Root is
#: derived now; AZAZAEL_CHAIN20_ROOT is the deliberate override for the rare,
#: intentional case of driving another tree.
ROOT = Path(os.environ.get("AZAZAEL_CHAIN20_ROOT")
            or Path(__file__).resolve().parents[1])
SCRIPTS = ROOT / "scripts"
LOG = ROOT / "logs" / "chain20.log"

# Interpreter paths stay pinned: they are machine-level, not repo-relative.
# Production is the 3.15t venv (py315t) and its same-version GIL sidecar
# (py315); the AppData installs are fallbacks for a box without the venvs.
# 3.14t was retired 2026-10-19 and must not reappear here.
def _pick(first, *fallbacks):
    for cand in (first, *fallbacks):
        if os.path.isfile(cand):
            return cand
    return first
PY = _pick(r'C:\Azazael\.venvs\py315\Scripts\python.exe',
          r'C:\Users\clint\AppData\Local\Programs\Python\Python315\python.exe')
PY_FT = _pick(r'C:\Azazael\.venvs\py315t\Scripts\python.exe',
             r'C:\Users\clint\AppData\Local\Programs\Python\Python315\python3.15t.exe')
ROUNDS = 20
STEP_TIMEOUT = 900
#: rc recorded when a step does not return. The log line's `rc=` field is read
#: as a code, so it has to BE one -- it used to print `rc=FAIL`, a verdict word
#: in a field named rc, and discarded the exception text, so a timed-out round
#: and a crashed round looked identical and neither left a code to grep for.
RC_TIMEOUT = 124
RC_EXC = 127


def log(msg):
    line = '%s %s' % (time.strftime('%Y-%m-%dT%H:%M:%S'), msg)
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def step(name, args, binary=None):
    t0 = time.time()
    rc = None
    try:
        p = subprocess.run([binary or PY] + args, capture_output=True,
                           text=True, timeout=STEP_TIMEOUT,
                           cwd=str(ROOT), encoding="utf-8", errors="replace")
        rc = p.returncode
        tail = ((p.stdout or '') + (p.stderr or '')).strip().splitlines()
        tail = tail[-1][-160:] if tail else ''
    except subprocess.TimeoutExpired as e:
        # The partial output survives the timeout; keep it as the evidence of
        # WHERE the step hung instead of replacing it with the word TIMEOUT.
        part = (e.stdout or '') + (e.stderr or '')
        if isinstance(part, bytes):
            part = part.decode('utf-8', 'replace')
        rc = RC_TIMEOUT
        tail = 'TIMEOUT after %ss :: %s' % (
            STEP_TIMEOUT, part.strip().replace('\n', ' ')[-160:])
    except Exception as e:
        rc = RC_EXC
        tail = 'EXC %s: %s' % (type(e).__name__, str(e)[:120])
    log('%s rc=%s %.0fs :: %s' % (name, rc, time.time() - t0, tail))
    return rc == 0


def main():
    ok = fail = 0
    for i in range(1, ROUNDS + 1):
        r0 = time.time()
        log('--- round %d/%d ---' % (i, ROUNDS))
        for name, script, extra, binary in (
                ('dream', 'dream_cycle.py', [], None),
                ('metacog', 'metacog_cycle.py', [], None),
                ('train', 'run_training_orchestrator.py', [], None),
                ('evolution', 'evolution_cycle.py', [], PY_FT)):
            if step('%s[%d]' % (name, i), [str(SCRIPTS / script)] + extra,
                    binary=binary):
                ok += 1
            else:
                fail += 1
        log('round %d done in %.0fs (ok=%d fail=%d)' % (i, time.time() - r0, ok, fail))
    log('CHAIN20 DONE ok=%d fail=%d' % (ok, fail))


if __name__ == '__main__':
    main()