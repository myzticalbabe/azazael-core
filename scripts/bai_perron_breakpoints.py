"""bai_perron_breakpoints.py — Formal Bai-Perron structural-break dating on BCC FY series.

Implements the Bai & Perron (1998, 2003) sequential breakpoint procedure:

  1. Dynamic-programming globally optimal partitions for m = 0..M breaks
     (pure mean-shift model: y_t = mu_j + u_t within each segment).
  2. supF(l|0) tests (0 breaks vs l breaks, l = 1..M) and the UDmax test.
  3. Sequential supF(l+1|l) tests — adds one break per existing segment and
     takes the max over all candidate break dates (with h-observation margins).
  4. Finite-sample critical values via MONTE CARLO (10,000 iid N(0,1) draws on
     the same T=15 grid, same procedure) — NOT asymptotic tables, because at
     N=15 the Bai-Perron asymptotic tables are not defensible. p-values =
     simulation rejection rates.

Runs on the 3.15t production venv (numpy only — no statsmodels dependency;
statsmodels 0.15.0 does not ship a structural_break/BaiPerron module anyway).

Usage (3.15t venv):
    C:/Azazael/.venvs/py315t/Scripts/python.exe azazael/scripts/bai_perron_breakpoints.py [--series bcc_990_series.json]
"""
import argparse
import json

import numpy as np


def ssr_single(seg):
    """SSR of a constant-only model on a 1-D segment (mean-shift)."""
    n = seg.shape[0]
    if n < 1:
        return 0.0
    mu = seg.mean()
    return float(((seg - mu) ** 2).sum())


def ssr_matrix(y, h):
    """Upper-triangular SSR[i][j] = SSR of segment y[i:j+1] (both inclusive), only j-i+1 >= h."""
    T = len(y)
    ssr = np.full((T, T), np.inf)
    for i in range(T):
        run = 0.0
        cnt = 0
        for j in range(i, T):
            cnt += 1
            run += y[j]
            if cnt >= h:
                mu = run / cnt
                # recompute exact SSR for this segment (O(n^2) overall, fine for T=15)
                seg = y[i:j + 1]
                ssr[i][j] = float(((seg - mu) ** 2).sum())
    return ssr


def optimal_partitions(y, h, M):
    """DP: minimal SSR for each m=0..M breaks, with optimal break index ['break_dates'] (0-based, exclusive end of each segment)."""
    T = len(y)
    ssr = ssr_matrix(y, h)
    # dp[m][j] = min SSR partitioning y[0:j+1] (last index j inclusive) into (m+1) segments
    INF = np.inf
    dp = np.full((M + 1, T), INF)
    back = np.full((M + 1, T), -1, dtype=int)
    # 0 breaks: single segment 0..j
    for j in range(T):
        dp[0][j] = ssr[0][j]
    for m in range(1, M + 1):
        for j in range((m + 1) * h - 1, T):
            best = INF
            besti = -1
            # last segment starts at i+1 .. j, so previous segments cover 0..i with i >= (m*h - 1)
            for i in range(m * h - 1, j - h + 1):
                if dp[m - 1][i] == INF or ssr[i + 1][j] == INF:
                    continue
                val = dp[m - 1][i] + ssr[i + 1][j]
                if val < best:
                    best = val
                    besti = i
            dp[m][j] = best
            back[m][j] = besti
    # reconstruct breaks for each m ending at T-1
    result = {}
    for m in range(M + 1):
        if dp[m][T - 1] == INF:
            continue
        breaks = []
        j = T - 1
        mm = m
        while mm > 0:
            i = back[mm][j]
            breaks.append(i + 1)  # break date = first index of the last segment
            j = i
            mm -= 1
        breaks.reverse()
        result[m] = {"ssr": float(dp[m][T - 1]), "break_ix": breaks}
    return result


def seg_means_from_breaks(y, break_ix):
    """Mean of each segment given break indices (first index of each segment)."""
    bounds = [0] + [int(b) for b in break_ix] + [len(y)]
    means = []
    for a, b_ in zip(bounds[:-1], bounds[1:]):
        means.append(float(y[a:b_].mean()))
    return means


def f_add_break(seg, alt_breaks, h, q=1):
    """sup F for adding a single break inside one segment. seg = np array.

    Restricted: one mean. Unrestricted: two means split at candidate alt_breaks.
    F = (SSR_r - SSR_u)/q / (SSR_u/(n - 2q))  (standard Bai-Perron single-break F).
    Returns best (F, break_ix).
    """
    n = seg.shape[0]
    ssr_r = ssr_single(seg)
    best_f = -np.inf
    best_ix = None
    for b in alt_breaks:
        if b < 1 or b >= n:
            continue
        left = seg[:b]
        right = seg[b:]
        if len(left) < h or len(right) < h:
            continue
        ssr_u = ssr_single(left) + ssr_single(right)
        if ssr_u <= 0:
            return np.inf, b
        f = (ssr_r - ssr_u) / q / (ssr_u / (n - 2 * q))
        if f > best_f:
            best_f = f
            best_ix = b
    return float(best_f), best_ix


def sequential_supf(y, h, M):
    """Bai-Perron sequential supF(l+1|l) using the globally optimal l-break partition.

    Returns list of (l, supF, global_offset_of_added_break) for l=0..M-1.
    Also returns the (M+1)-break partition it eventually picks if we keep adding.
    """
    T = len(y)
    opts = optimal_partitions(y, h, M)
    seq = []
    # start from 0 breaks
    cur_breaks = []
    for l in range(M):
        # current segments from cur_breaks
        bounds = [0] + cur_breaks + [T]
        best_f = -np.inf
        best_global_ix = None
        for si in range(len(bounds) - 1):
            a, b_ = bounds[si], bounds[si + 1]
            seg = y[a:b_]
            nseg = len(seg)
            if nseg < 2 * h:
                continue
            cands = list(range(h, nseg - h + 1))  # +1 -> alt break index in seg
            f, bix = f_add_break(seg, cands, h)
            if f > best_f:
                best_f = f
                best_global_ix = a + bix if bix is not None else None
        seq.append((l, best_f, best_global_ix))
        if best_global_ix is not None:
            cur_breaks = sorted(cur_breaks + [best_global_ix])
        else:
            break
    return seq, cur_breaks


def udmax(y, h, M):
    """UDmax = max_l supF(l|0)."""
    T = len(y)
    ssr0 = ssr_matrix(y, h)[0][T - 1]
    opts = optimal_partitions(y, h, M)
    supf_max = -np.inf
    for l in range(1, M + 1):
        if l not in opts:
            continue
        ssr_l = opts[l]["ssr"]
        n = T
        q = 1
        # supF(l|0) = (SSR0 - SSRL)/l / (SSRL/(T - (l+1)*q))
        f = (ssr0 - ssr_l) / l / (ssr_l / (n - (l + 1) * q))
        if f > supf_max:
            supf_max = f
    return supf_max


def simulate_argmax_quantiles(nsim=60000, max_h=4000, seed=20260904, levels=(0.10, 0.05, 0.01)):
    """Monte-Carlo quantiles of the Bai (1997) argmax distribution.

    The limiting distribution of the normalized break-date estimator is
    eta = argmax_s { W(s) - |s|/2 }, a two-sided random walk with drift -1/2.
    A symmetric (1-alpha) CI half-width in normalized time units is the
    (1-alpha) quantile of |eta|. Simulated directly (60k walks, max |s|=4000)
    so we never rely on an asymptotic table at N=15.
    Returns {alpha: c_alpha}.
    """
    rng = np.random.default_rng(seed)
    c_s = {a: 0.0 for a in levels}
    abs_et = np.empty(nsim)
    for i in range(nsim):
        # right side: W(s) - s/2 for s >= 0
        e_r = rng.standard_normal(max_h + 1)
        w_r = np.cumsum(e_r) - np.arange(max_h + 1) * 0.5
        # left side mirrored: same construction on -s
        e_l = rng.standard_normal(max_h + 1)
        w_l = np.cumsum(e_l) - np.arange(max_h + 1) * 0.5
        grid = np.concatenate([w_l[:0:-1], w_r])  # positions -H..0..H
        abs_et[i] = abs(np.argmax(grid) - max_h)
    for a in levels:
        c_s[a] = float(np.quantile(abs_et, 1 - a))
    return c_s


def break_confidence_intervals(y, break_ix, seg_means, c_quantiles, h):
    """Bai (1997) confidence intervals for each break date.

    For each break at index k (first year of the right segment), the break
    magnitude is delta = mu_next - mu_prev; sigma2 is the mean squared residual
    of the fitted segmented-mean model. The normalized drift
    drift = delta^2 / sigma2 scales the argmax quantile into observation units:
        halfwidth = ceil(c_alpha / drift)
        CI = [k - halfwidth, k + halfwidth]  (clipped to the segment bounds)
    Returns a list of dicts including FY labels (series starts FY2011).
    """
    bounds = [0] + [int(b) for b in break_ix] + [len(y)]
    resid_sq = 0.0
    n = len(y)
    for a, b_ in zip(bounds[:-1], bounds[1:]):
        seg = y[a:b_]
        resid_sq += float(((seg - seg.mean()) ** 2).sum())
    sigma2 = resid_sq / max(1, n - len(break_ix))
    out = []
    for i, k in enumerate(break_ix):
        mu_prev = seg_means[i]
        mu_next = seg_means[i + 1]
        delta = mu_next - mu_prev
        drift = (delta * delta) / sigma2 if sigma2 > 0 else np.inf
        row = {"break_ix": int(k), "fy": int(k + 2011),
               "delta": float(delta), "sigma2": float(sigma2),
               "drift": float(drift), "level_cis": {}}
        for a, c in c_quantiles.items():
            if not np.isfinite(drift) or drift == 0:
                hw = 0
            else:
                hw = int(np.ceil(c / drift))
            lo = max(bounds[i] + 1, k - hw) if i > 0 else max(0, k - hw)
            hi = min(bounds[i + 2] - 1, k + hw)
            row["level_cis"][a] = {"low_ix": lo, "high_ix": hi,
                                   "low_fy": int(lo + 2011), "high_fy": int(hi + 2011),
                                   "halfwidth": hw}
        out.append(row)
    return out


def run_breakpoints(y, h, M, nsim=10000, c_quantiles=None):
    """Full formal procedure on one observed series; returns dict of results."""
    T = len(y)
    opts = optimal_partitions(y, h, M)
    seq, final_breaks = sequential_supf(y, h, M)

    # observed UDmax + sequential stats
    ud = udmax(y, h, M)
    stat_s0 = {l: (opts[l]["ssr"] if l in opts else None) for l in range(M + 1)}

    # Monte Carlo finite-sample critical values on iid N(0,1), same T/h/M
    rng = np.random.default_rng(20260904)
    mc_ud = np.empty(nsim)
    mc_seq = {l: np.empty(nsim) for l in range(M)}
    for s in range(nsim):
        yy = rng.standard_normal(T)
        mc_ud[s] = udmax(yy, h, M)
        seq_s, _ = sequential_supf(yy, h, M)
        for (l, f, _ix) in seq_s:
            mc_seq[l][s] = f

    def crit(stat_arr, alpha):
        return float(np.quantile(stat_arr, 1 - alpha))

    def pval(stat_arr, val):
        if val is None or not np.isfinite(val):
            return 1.0
        return float((stat_arr >= val).mean())

    if c_quantiles is None:
        c_quantiles = simulate_argmax_quantiles()
    final_means = seg_means_from_breaks(np.asarray(y), final_breaks)
    cis = break_confidence_intervals(np.asarray(y), final_breaks, final_means, c_quantiles, h)
    result = {
        "T": T,
        "h": h,
        "M": M,
        "optimal_partitions": {m: {"ssr": opts[m]["ssr"], "break_ix": opts[m]["break_ix"]} for m in opts},
        "seg_means_per_partition": {m: seg_means_from_breaks(np.asarray(y), opts[m]["break_ix"]) for m in opts},
        "udmax": ud,
        "udmax_pval": pval(mc_ud, ud),
        "udmax_crit": {a: crit(mc_ud, a) for a in (0.10, 0.05, 0.01)},
        "sequential": [],
        "final_break_ix": final_breaks,
        "final_break_fy": [int(b + 2011) for b in final_breaks],
        "argmax_quantiles": c_quantiles,
        "break_cis": cis,
    }
    for (l, f, ix) in seq:
        entry = {
            "test": f"supF({l + 1}|{l})",
            "stat": f,
            "pval": pval(mc_seq[l], f),
            "crit": {a: crit(mc_seq[l], a) for a in (0.10, 0.05, 0.01)},
            "added_break_ix": ix,
        }
        result["sequential"].append(entry)
    return result


def fmt_report(metric, y, res):
    T, h = res["T"], res["h"]
    lines = []
    lines.append(f"=== {metric} (T={T}, h={h}, max breaks M={res['M']}) ===")
    lines.append("Global optimal partitions:")
    for m in sorted(res["optimal_partitions"]):
        bp = res["optimal_partitions"][m]
        means = res["seg_means_per_partition"][m]
        ix = ", ".join(str(i + 2011) for i in bp["break_ix"]) if bp["break_ix"] else "none"
        ms = ", ".join(f"{v/1e3:.1f}K" if metric != "revenue" and metric != "expenses" else f"{v/1e6:.2f}M" for v in means)
        lines.append(f"  m={m}: SSR={bp['ssr']:.0f}  breaks(FY)={ix}  seg-means={ms}")
    ud = res["udmax"]
    lines.append(f"UDmax = {ud:.2f}  (p={res['udmax_pval']:.3f}; crit 10/5/1% = "
                 + "/".join(f"{res['udmax_crit'][a]:.2f}" for a in (0.10, 0.05, 0.01)) + ")")
    for e in res["sequential"]:
        if not np.isfinite(e['stat']):
            continue  # no feasible additional break in this segment
        lines.append(f"  {e['test']}: stat={e['stat']:.2f}  (p={e['pval']:.3f}; crit 10/5/1% = "
                     + "/".join(f"{e['crit'][a]:.2f}" for a in (0.10, 0.05, 0.01))
                     + (f"; added break FY={e['added_break_ix']+2011}" if e['added_break_ix'] is not None else ""))
    lines.append(f"FINAL sequential break dates (FY): "
                 + ", ".join(str(b + 2011) for b in res["final_break_ix"]) if res["final_break_ix"] else "none")
    if res.get("break_cis"):
        lines.append("Bai (1997) break-date CIs (argmax quantiles MC-simulated from 60k walks):")
        for ci in res["break_cis"]:
            parts = []
            for a in (0.10, 0.05, 0.01):
                iv = ci["level_cis"][a]
                parts.append(f"{int(a*100)}%=[FY{iv['low_fy']}..FY{iv['high_fy']}]")
            lines.append(f"  FY{ci['fy']}  delta={ci['delta']/1e6:+.2f}M  drift={ci['drift']:.1f}  "
                         + "  ".join(parts))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", default="bcc_990_series.json")
    ap.add_argument("--h", type=int, default=3, help="minimum observations per segment (trimming)")
    ap.add_argument("--nsim", type=int, default=10000)
    ap.add_argument("--metrics", nargs="+", default=["revenue", "expenses", "officer_comp"])
    ap.add_argument("--out", default=None, help="optional JSON dump of all results")
    args = ap.parse_args()

    ds = json.load(open(args.series, encoding="utf-8"))
    rows = sorted(ds["filings"], key=lambda r: r.get("tax_prd") or 0)
    years = np.array([int(str(r["tax_prd"])[:4]) for r in rows])
    y_full = {m: np.array([r.get(m) for r in rows], dtype=np.float64) for m in args.metrics}

    M = max(1, min(4, len(rows) // args.h - 1))  # max breaks given trimming
    print(f"Bai-Perron sequential breakpoint dating — FY{int(years.min())}–{int(years.max())}  (h={args.h}, M={M})")
    print("=" * 78)
    all_res = {}
    c_quantiles = simulate_argmax_quantiles()
    for m in args.metrics:
        res = run_breakpoints(y_full[m], args.h, M, nsim=args.nsim, c_quantiles=c_quantiles)
        all_res[m] = res
        print(fmt_report(m, y_full[m], res))
        print()
    if args.out:
        def _py(o):
            if isinstance(o, dict):
                return {str(k): _py(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [_py(x) for x in o]
            if isinstance(o, np.integer):
                return int(o)
            if isinstance(o, np.floating):
                return float(o)
            if isinstance(o, np.ndarray):
                return _py(o.tolist())
            return o
        ser = {m: _py({"optimal_partitions": res["optimal_partitions"],
                        "seg_means_per_partition": res["seg_means_per_partition"],
                        "udmax": res["udmax"], "udmax_pval": res["udmax_pval"],
                        "udmax_crit": res["udmax_crit"], "sequential": res["sequential"],
                        "final_break_ix": res["final_break_ix"], "final_break_fy": res["final_break_fy"],
                        "argmax_quantiles": res["argmax_quantiles"], "break_cis": res["break_cis"],
                        "T": res["T"], "h": res["h"], "M": res["M"]})
               for m, res in all_res.items()}
        json.dump(ser, open(args.out, "w", encoding="utf-8"), indent=2)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()