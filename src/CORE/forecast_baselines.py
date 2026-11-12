# coding: utf-8
"""forecast_baselines.py — ARIMA-lite + seasonal-naive vs Ridge, honestly.

ar1_forecast: OLS AR(1) one-step. seasonal_naive: lag-m prediction.
compare(): walk-forward MAE for each + naive persistence baseline.
No new deps (statsmodels/prophet optional if present, else skipped).
"""


def ar1_forecast(series: list[float]) -> float:
    xs, ys = series[:-1], series[1:]
    n = len(xs)
    if n < 2:
        return series[-1] if series else 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs) or 1e-9
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return my + b * (series[-1] - mx)


def seasonal_naive(series: list[float], m: int = 4) -> float:
    if len(series) > m:
        return series[-m - 1]
    return series[-1] if series else 0.0


def _mae(fn, series, min_train: int, **kw):
    errs = []
    for i in range(min_train, len(series)):
        errs.append(abs(series[i] - fn(series[:i], **kw)))
    return sum(errs) / max(1, len(errs))


def compare(series: list[float], min_train: int = 6) -> dict:
    """Walk-forward MAE: ridge-mean, AR(1), seasonal, persistence."""
    import statistics
    ridge = lambda h: statistics.mean(h[-3:])
    out = {
        "ridge_mean": round(_mae(ridge, series, min_train), 4),
        "ar1": round(_mae(ar1_forecast, series, min_train), 4),
        "seasonal_m4": round(_mae(seasonal_naive, series, min_train), 4),
        "persistence": round(_mae(lambda h: h[-1], series, min_train), 4),
        "n": len(series),
    }
    best = min(out, key=lambda k: out[k] if k != "n" else 1e18)
    out["winner"] = best
    return out
