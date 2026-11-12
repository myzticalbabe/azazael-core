"""Metacognition cycle: feed -> observe -> analyze per worm -> adapt worst.

Reads recent live outcome signals, observes outcomes into MetaCognitionWorm,
runs per-worm analysis (which persists confidence models), adapts the worst
offender, records a cycle summary row. Actuation beyond scoring is
human-approved (no config writes here).

Evidence sources (all real, live event types with actual producers):
  - db_health_snapshot (DatabaseMonitorWorm, ~every 15 min)  -> 'database'
  - cluster_execution_completed (ClusterWORMZ.system)         -> cluster_type
  - nature_cluster_executed   (ClusterWORMZs)                -> nature
Feeding the health monitor is what gives the evidence channel a signal on
cadence even when no cluster batch is running.
"""
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from CORE.local_memory import get_memory_store

# Scheduler-visible failure (2026-09-10): this script is flat (no main),
# so an unhandled traceback went to stdout, which the scheduler
# discards. The hook lands fatals in metacog_cycle.log instead.
def _fatal_hook(_t, _v, _tb):
    try:
        import traceback as _tbmod
        import time as _timemod
        with open(r'C:\Azazael\azazael\logs\metacog_cycle.log',
                  'a', encoding='utf-8') as _f:
            _f.write('%s FATAL\n%s\n' % (
                _timemod.strftime('%Y-%m-%dT%H:%M:%S'),
                ''.join(_tbmod.format_exception(_t, _v, _tb))[-2000:]))
    except Exception:
        pass
sys.excepthook = _fatal_hook

# Single-instance guard (2026-09-10): overlapping metacog runs would
# double-observe and double-adapt. Non-blocking msvcrt lock held open
# for process life; OS releases on death, so no stale-lock cleanup.
import msvcrt
import os
import tempfile
try:
    _lock_fh = open(os.path.join(tempfile.gettempdir(), 'metacog_cycle.lock'), 'w', encoding="utf-8")
    msvcrt.locking(_lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
except (OSError, IOError):
    print('SKIP overlap: previous metacog cycle still running', flush=True)
    sys.exit(0)

from CORE.WORMS.expansion.meta_cognition import MetaCognitionWorm

mc = MetaCognitionWorm()
store = get_memory_store()

# Never re-observe a signal already fed in an earlier cycle: state is
# restored on construction (load_state in __init__), so dedupe against the
# restored observations' context ids. With a 5-minute cadence this keeps
# each snapshot/execution counted exactly once.
_known = set()
for _o in mc._observations:
    _cid = (_o.get('context') or {}).get('cluster_id')
    if _cid:
        _known.add(_cid)

recent = []
if store:
    for et in ('db_health_snapshot',
               'cluster_execution_completed', 'nature_cluster_executed',
               'training_run_complete', 'policy_applied'):
        try:
            recent += list(store.query_memories(event_type=et, limit=50) or [])
        except Exception:
            pass
# newest first, but stable within an event type so dedupe is deterministic
recent.sort(key=lambda r: str(r.get('timestamp', '')), reverse=True)

fed = 0
seen = {}
for r in recent:
    et = r.get('event_type', '')
    data = r.get('data', {}) or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            continue
    if not isinstance(data, dict):
        continue

    if et == 'db_health_snapshot':
        wt = 'database'
        healthy = bool(data.get('healthy'))
        outcome = 'success' if healthy else 'error'
        err = None if healthy else (
            f"db unhealthy: {data.get('db_size_mb')} MB, "
            f"{data.get('error_count_7d')} errors/7d, "
            f"{data.get('total_memories')} rows")
        # one snapshot per ~15 min window; truncate to the minute for dedupe
        cid = 'db_' + str(r.get('timestamp', ''))[:16]
        action = 'health_check'
    elif et == 'cluster_execution_completed':
        wt = str(data.get('cluster_type', 'cluster'))
        ok = int(data.get('success', 0) or 0)
        tot = int(data.get('results', 0) or 0) or max(ok, 1)
        outcome = 'success' if ok >= tot * 0.8 else 'error'
        err = None if outcome == 'success' else (
            f"cluster degraded: {ok}/{tot} worms succeeded")
        cid = 'cl_' + str(r.get('timestamp', ''))[:16]
        action = 'cluster_execute'
    elif et == 'nature_cluster_executed':
        wt = str(data.get('nature', 'nature'))
        n_fail = int(data.get('failures', 0) or 0)
        outcome = 'success' if n_fail == 0 else 'error'
        err = None if outcome == 'success' else (
            f"nature run: {n_fail} worm failures")
        cid = 'na_' + str(r.get('timestamp', ''))[:16]
        action = 'cluster_execute'
    elif et == 'training_run_complete':
        # (2026-09-10 improvement: metacog was blind to training — fed=0
        # most rounds while dozens of trainings completed. Only clear
        # success/failure feeds evidence; verdict-less rows are skipped.)
        terr = data.get('error')
        rw = data.get('eval_mean_reward', data.get('avg_reward'))
        wt = 'training'
        if terr:
            outcome, err = 'error', 'training failed: %s' % str(terr)[:120]
        elif isinstance(rw, (int, float)):
            outcome, err = 'success', None
        else:
            continue
        cid = 'tr_' + str(data.get('run_id') or r.get('timestamp', ''))[:24]
        action = 'model_train'
    elif et == 'policy_applied':
        # Dream policy verdicts. A SKIP is the designed outcome for tepid or
        # governed suggestions ('low-confidence', 'no-actionable-strategy',
        # 'governance-blocked' -- see dream_policy_bridge), NOT a failure. Only
        # a skip whose reason begins 'error' is a real fault. Scoring every
        # skip as an error fed a false "dreaming is failing" signal into
        # adaptation (measured 2026-09-28: 93 'dream round partial' rows, with
        # the active strategy swung to defensive / retry_with_backoff).
        det = data.get('details') or {}
        applied = det.get('applied', []) or []
        skipped = det.get('skipped', []) or []
        nap, nsk = len(applied), len(skipped)
        wt = 'dreaming'
        hard_fail = [s for s in skipped
                     if str((s or {}).get('reason', '')).startswith('error')]
        if hard_fail:
            outcome = 'error'
            err = 'dream policy failures: %d (applied %d, skipped %d)' % (
                len(hard_fail), nap, nsk)
        elif nap > 0:
            outcome, err = 'success', None
        else:
            # nothing applied and no hard failure: no evidence either way, so
            # feed nothing rather than a false success or a false error.
            continue
        cid = 'po_' + str(r.get('timestamp', ''))[:16]
        action = 'dream_integrate'
    else:
        continue

    if cid in _known:
        continue  # already observed in an earlier cycle
    mc.observe(worm_type=wt, action=action, outcome=outcome,
               context={'cluster_id': cid}, error=err)
    _known.add(cid)
    st = seen.setdefault(wt, {'ok': 0, 'err': 0})
    st['ok' if outcome == 'success' else 'err'] += 1
    fed += 1

adapted = None
adapted_worm = None
evidence = None
# Re-evaluate on accumulated evidence every cycle (not only when a new
# snapshot happens to fall inside this exact 5-minute window), so a real
# health change moves the strategy on the next tick. Only worm types that
# have actually been observed are considered.
observed_wts = sorted({o.get('worm_type') for o in mc._observations})
if observed_wts:
    for wt in observed_wts:
        mc.analyze_patterns(worm_type=wt)
    # worst = highest error ratio among observed worm types
    worst = None
    worst_err = -1.0
    for wt in observed_wts:
        obs = [o for o in mc._observations if o.get('worm_type') == wt]
        n_err = sum(1 for o in obs if o['outcome'] == 'error')
        ratio = n_err / max(1, len(obs))
        if ratio > worst_err:
            worst_err = ratio
            worst = wt
    adapted_worm = worst
    # Real-outcome evidence channel: analyze_patterns builds a confidence
    # model from observed outcomes; recommend_from_evidence turns it into a
    # strategy recommendation whose confidence clears the ADOPT_CONFIDENCE
    # bar only when the evidence is strong.
    if worst:
        rec = mc.recommend_from_evidence(worst)
        evidence = rec.get('evidence')
        if rec.get('recommended'):
            # Full strategy space so metacog can recommend any strategy the
            # dreamer/bridge can apply. Target 'global' -- the worm the
            # dream pipeline actually steers -- so a metacog-driven switch
            # reaches the dreamer instead of only recording per-cluster
            # scores.
            adapted = mc.adapt_strategy(
                'global', 'default',
                available_strategies=['default', 'conservative', 'defensive',
                                      'aggressive', 'adaptive', 'batched', 'staged'],
                recommended=rec['recommended'],
                confidence=rec['confidence'])

# Persist learned state (confidence models, observations, any strategy
# switch) so the next cycle resumes instead of restarting from empty.
try:
    mc.save_state()
except Exception as e:
    print('state save failed:', e)

if store is not None:
    try:
        store.record_event(source='MetaCognitionWorm', event_type='mc_cycle',
                           data={'fed': fed, 'worm': adapted_worm,
                                 'adapted': bool(adapted and adapted.get('should_adapt')),
                                 'recommendation': (adapted or {}).get('recommended_strategy', 'none'),
                                 'evidence': evidence},
                           severity='info', tags=['metacognition', 'cycle'])
    except Exception as e:
        print('cycle record failed:', e)

print(f'fed={fed} worm={adapted_worm} adapted={bool(adapted and adapted.get("should_adapt"))} '
      f'rec={adapted.get("recommended_strategy") if adapted else "none"}', flush=True)

import os
import time
os.makedirs(r'C:\Azazael\azazael\logs', exist_ok=True)
with open(r'C:\Azazael\azazael\logs\metacog_cycle.log', 'a', encoding='utf-8') as f:
    f.write('%s fed=%d worm=%s adapted=%s rec=%s\n' % (
        time.strftime('%Y-%m-%dT%H:%M:%S'), fed, adapted_worm,
        bool(adapted and adapted.get('should_adapt')),
        (adapted or {}).get('recommended_strategy', 'none')))
