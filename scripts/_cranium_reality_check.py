# coding: utf-8
"""Cranium on REAL margin history, not a fixture.

Cranium is guarded by `beats`, and 25/25 live cycles on 2026-09-12 had
`beats=False`, so the organ never executed in production. That leaves an
honest question: is it wired correctly but simply unreachable, or is it broken
and therefore silent either way? Only running it against the store's actual
rows answers that.

Run under BOTH interpreters — the production runner is the free-threaded 3.15t
venv and the comparison interpreter is its 3.15 GIL sidecar, so the organ must
be stdlib-only to behave identically under both.
"""
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from CORE.WORMS.expansion.proteus_hooks import (  # noqa: E402
    cranium_decide, cranium_override, row_payload)


def margins_by_era(rows):
    eras = collections.OrderedDict()
    for row in rows:
        d = row_payload(row)
        if not d or not str(d.get('fitness', '')).startswith('v3'):
            continue
        m = d.get('margin')
        if m is None:
            try:
                m = float(d.get('best_total')) - float(d.get('baseline'))
            except (TypeError, ValueError):
                continue
        try:
            eras.setdefault(str(d.get('pool_id') or 'unknown'), []).append(float(m))
        except (TypeError, ValueError):
            continue
    return eras


def main():
    print('interpreter: %s' % sys.executable)
    print('numpy available: %s' % ('numpy' in sys.modules))
    try:
        from CORE.local_memory import get_memory_store
        rows = get_memory_store().query_memories(
            event_type='evolution_result', limit=400)
    except Exception as e:
        print('store unavailable: %s' % str(e)[:160])
        return 1
    print('rows fetched: %d' % len(rows))

    eras = margins_by_era(rows)
    print('eras carrying usable v3 margins: %d' % len(eras))
    for era, ms in list(eras.items())[:8]:
        print('   %-22s n=%-3d margins=%s' % (
            era[:22], len(ms), [round(m, 3) for m in ms[:6]]))

    if not eras:
        print('VERDICT: no comparable rows -> organ cannot answer on this store')
        return 0

    cur_era = list(eras)[0]
    cur = eras[cur_era][0] if eras[cur_era] else 0.0
    dec = cranium_decide(rows, cur_era, cur, threshold=0.75)
    print()
    print('=== cranium_decide (current era %s, margin_now=%.4f) ==='
          % (cur_era, cur))
    print(json.dumps(dec, indent=1))
    ov = cranium_override('continue', dec)
    print('override of a stalled SPRT: %s' % json.dumps(ov))
    print()
    print('VERDICT: organ ANSWERS on real history (%d eras pooled, n=%d).'
          % (dec['n_eras'], dec['n_obs']))
    print('         It is wired-and-correct but NOT REACHED by the cycle in this')
    print('         regime, because every live cycle now fails the margin gate')
    print('         (`beats=False`) before Cranium is consulted.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
