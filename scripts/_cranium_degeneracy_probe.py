# coding: utf-8
"""Can Cranium report a CONFIDENT decision when its variance is unidentifiable?

The duplicate-collapse test surfaced `posterior_sd = 0.0` on a one-era,
one-observation input. sd 0 means `prob_above` is exactly 0.0 or 1.0, i.e. the
organ claims certainty from a single number. That is only harmless because
`decision` separately requires n >= 2. This probe checks the boundary: an era
count / observation count where the variance cannot be estimated yet the
decision gate is satisfied.
"""
import itertools
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from CORE.WORMS.expansion.proteus_hooks import cranium_decide  # noqa: E402


def row(era, m):
    return {"data": {"pool_id": era, "fitness": "v3-dual-robust", "margin": m}}


def main():
    bad = []
    print('%-26s %5s %5s %12s %9s  %s' % (
        'case', 'eras', 'obs', 'raw_sd', 'p_above', 'decision'))
    print('-' * 82)

    cases = []
    # one era, k distinct observations, all strongly positive
    for k in range(1, 6):
        cases.append(('1 era, %d positive' % k,
                      [row('e0', 3.0 + 0.01 * i) for i in range(k)], 'e0', 3.0))
    # two eras, one observation each
    cases.append(('2 eras, 1 obs each',
                  [row('e0', 3.0), row('e1', 3.0)], 'e2', 3.0))
    # one era, two observations, no within-era variance
    cases.append(('1 era, same margin x2',
                  [row('e0', 3.0), row('e0', 3.0)], 'e0', 3.0))
    # healthy: many eras
    cases.append(('6 eras, 1 obs each',
                  [row('e%d' % i, 2.5 + 0.05 * i) for i in range(6)], 'e9', 2.6))
    # noisy single era
    cases.append(('1 era, 4 noisy',
                  [row('e0', m) for m in (3.0, -1.0, 2.0, -0.5)], 'e0', 1.0))

    for name, rows, cur, now in cases:
        d = cranium_decide(rows, cur, now, threshold=0.75)
        raw = d.get('raw_posterior_sd', float('nan'))
        print('%-26s %5d %5d %12.3e %9.4f  %s%s' % (
            name, d['n_eras'], d['n_obs'], raw,
            d['p_above_threshold'], d['decision'],
            '  [degenerate]' if d.get('degenerate_variance') else ''))
        if d['decision'] in ('promote', 'reject') and raw < 1e-6:
            bad.append((name, d))

    print()
    if bad:
        print('DEGENERATE CONFIDENCE: %d case(s) decided with sd ~ 0' % len(bad))
        for name, d in bad:
            print('   %s -> %s (p=%.4f)' % (name, d['decision'],
                                            d['p_above_threshold']))
    else:
        print('OK: no case reports a promote/reject with unidentifiable '
              'variance (sd ~ 0).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
