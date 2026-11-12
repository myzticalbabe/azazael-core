"""timesfm_pilot_315t.py — TimesFM 3 second-opinion anomaly layer on BCC FY series.

Runs on the free-threaded 3.15t venv (venv-315t). Loads bcc_990_series.json
(FY2011-2025), forecasts the last point (FY2025) zero-shot with TimesFM 3 for
each metric (revenue, expenses, officer_comp), computes the forecast-vs-actual
residual, and compares it against the rolling z-score baseline.

Usage (3.15t venv):
    venv-315t/Scripts/python.exe timesfm_pilot_315t.py [--metrics revenue expenses officer_comp]
"""
import argparse
import json
import time

import numpy as np


def load_series(path, metric):
    ds = json.load(open(path, encoding='utf-8'))
    rows = sorted(ds['filings'], key=lambda r: r.get('tax_prd') or 0)
    years = [r['tax_prd'] for r in rows]
    vals = np.array([r.get(metric) for r in rows], dtype=np.float64)
    return years, vals


def zscore_baseline(vals, window=5):
    """Rolling z-score baseline: each point vs mean/std of trailing window."""
    n = len(vals)
    out = [None] * n
    for i in range(n):
        lo = max(0, i - window)
        if i - lo < 2:
            out[i] = None
            continue
        mu = vals[lo:i].mean()
        sd = vals[lo:i].std(ddof=1)
        out[i] = (vals[i] - mu) / sd if sd > 0 else 0.0
    return out


def run_metric(model, metric, years, vals):
    print('\n' + '=' * 64)
    print('METRIC: %s' % metric)
    print('values: %s' % ' '.join('%s=%.2f%s' % (
        str(y), v/1e6, 'M' if v > 1e6 else 'K' if v > 1e3 else '') for y, v in zip(years, vals)))

    zs = zscore_baseline(vals, window=5)
    z_last = zs[-1]
    print('\n--- baseline: rolling z-score (window=5) ---')
    for y, v, z in zip(years, vals, zs):
        if z is not None:
            print('  %s  %12.0f  z=%+5.2f%s' % (
                y, v, z, '  <-- FY2025' if y == years[-1] else ''))

    ctx = vals[:-1].reshape(1, -1)
    out = model.predict(ctx, horizon=1)
    fcast = float(np.asarray(out.forecast).reshape(-1)[-1])
    print('\nTimesFM forecast FY2025: %.0f (actual %.0f)' % (fcast, vals[-1]))

    resid = vals[-1] - fcast
    rel = resid / fcast * 100
    print('TimesFM  forecast error: %+.0f (%+.1f%% of forecast)' % (resid, rel))
    print('z-score baseline: FY2025 z = %+.2f' % (z_last if z_last is not None else float('nan')))

    tf_flag = abs(rel) > 10.0
    z_flag = (z_last is not None and abs(z_last) > 2.0)
    agree = 'CONSISTENT' if tf_flag == z_flag else 'DIVERGENT'
    print('TimesFM flags FY2025 anomaly (|err|>10%%): %s' % tf_flag)
    print('z-score  flags FY2025 anomaly (|z|>2):    %s' % z_flag)
    print('AGREEMENT: %s' % agree)
    return {'metric': metric, 'actual': float(vals[-1]), 'forecast': fcast,
            'resid': resid, 'rel_pct': rel, 'z': z_last,
            'tf_flag': tf_flag, 'z_flag': z_flag, 'agreement': agree}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--series', default='bcc_990_series.json')
    ap.add_argument('--metrics', nargs='+',
                    default=['revenue', 'expenses', 'officer_comp'])
    args = ap.parse_args()

    import torch
    torch.set_num_threads(8)
    from timesfm3 import TimesFM3Forecaster
    t0 = time.time()
    model = TimesFM3Forecaster.from_pretrained('google/timesfm-3.0-pytorch')
    print('model loaded in %.1fs' % (time.time() - t0))

    results = []
    for metric in args.metrics:
        years, vals = load_series(args.series, metric)
        results.append(run_metric(model, metric, years, vals))

    print('\n' + '=' * 64)
    print('SUMMARY')
    for r in results:
        print('  %-14s  TF err=%+6.1f%%  z=%+5.2f  -> %s' % (
            r['metric'], r['rel_pct'], r['z'] if r['z'] is not None else float('nan'),
            r['agreement']))


if __name__ == '__main__':
    main()