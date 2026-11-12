"""Scheduled dream cycle: recall -> dream (8 episodes) -> evaluate -> integrate.

Robustness: overlap guard (lockfile w/ stale-PID steal), file log,
consecutive-failure counter, nonzero exit on failure (visible in
Task Scheduler Last Result). Policy bridge ARMED: approved suggestions
apply via MetaCognitionWorm (governed, audited, reversible).
Run every 5 minutes via Task Scheduler (Azazael-DreamCycle).
"""
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
LOCK_FILE = os.path.join(tempfile.gettempdir(), 'dream_cycle.lock')
FAIL_FILE = os.path.join(tempfile.gettempdir(), 'dream_cycle_fails.txt')


def log(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = time.strftime('%Y-%m-%dT%H:%M:%S')
    with open(os.path.join(LOG_DIR, 'dream_cycle.log'), 'a', encoding='utf-8') as f:
        f.write(f'{ts} {msg}\n')


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def acquire_lock():
    try:
        with open(LOCK_FILE, encoding="utf-8") as f:
            old = int(f.read().strip() or '0')
        if old and _pid_alive(old):
            return False
    except (OSError, ValueError):
        pass
    with open(LOCK_FILE, 'w', encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def release_lock():
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


def fail_count(delta=None):
    try:
        with open(FAIL_FILE, encoding="utf-8") as f:
            n = int(f.read().strip() or '0')
    except (OSError, ValueError):
        n = 0
    if delta is None:
        return n
    n = max(0, n + delta)
    with open(FAIL_FILE, 'w', encoding="utf-8") as f:
        f.write(str(n))
    return n


def _tuned_creativity():
    """Tuned dreaming params from the DREAMTUNE track (2026-09-09 apply).

    Returns (creativity_factor, scenario_count); falls back to built-in
    defaults when the file is missing or older than 48h (stale tunings
    must not steer dreams forever if the track dies). Precedence:
    these explicit args < metacog steering inside the pipeline.
    """
    cf, sc = 0.3, 40
    try:
        p = Path(__file__).resolve().parent.parent / "config" / "tuned_creativity.json"
        if p.exists() and (time.time() - p.stat().st_mtime) < 48 * 3600:
            d = json.loads(p.read_text(encoding="utf-8"))
            cf = min(0.95, max(0.05, float(d.get("creativity_factor", cf))))
            sc = min(100, max(5, int(d.get("scenario_count", sc))))
            return cf, sc, True
    except Exception:
        pass
    return cf, sc, False


def main():
    from CORE.WORMS.expansion.dreaming import DreamingModule
    from CORE.dream_policy_bridge import apply_dream_policy
    if not acquire_lock():
        log('SKIP overlap: previous cycle still running')
        return 0
    try:
        dm = DreamingModule()
        cf, sc, tuned = _tuned_creativity()
        out = dm.run_dreaming_pipeline(
            num_episodes=8, scenario_count=sc, creativity_factor=cf,
            auto_integrate=True,
            policy_fn=apply_dream_policy)
        fail_count(-999)
        integ = out.get('integration') or {}
        pr = integ.get('policy_result') or {}
        log('OK dreams=%s ms=%s applied=%d skipped=%d cf=%.2f sc=%d %s' % (
            out.get('dream_count'), out.get('duration_ms'),
            len(pr.get('applied', [])), len(pr.get('skipped', [])),
            cf, sc, 'tuned' if tuned else 'defaults'))
        return 0
    except Exception:
        n = fail_count(+1)
        log('FAIL #%d\n%s' % (n, traceback.format_exc()[-1500:]))
        return 1
    finally:
        release_lock()


if __name__ == '__main__':
    sys.exit(main())
