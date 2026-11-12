"""Pythran kernel: the scoring heart of the dream agreement replay.

Pairing (URL/ts matching) stays in Python -- it is pointer-chasing pythran
cannot type. Scoring is a tight numeric loop over parallel int columns:

  pred_rung  predicted rung, or -1 when the guess recorded none
  pred_kind  0 = no guess recorded (legacy rule), 1 = guess, 2 = block
  ans_rung   the answer's rung
  ans_ok     1 when the answer state was ok, else 0

Won (mirrors intuition.dream exactly): legacy rows win on rung match + ok;
scored rows win on agreement ((kind == guess) == ok) plus the rung match
when a prediction exists. Per-rung cells key on the predicted rung.

Built with: pythran _replay_kernel.py  (MSVC on PATH via VsDevCmd).
Absent compiled module -> native_replay falls back to the same math in
Python; parity is tested, not asserted.
"""
import numpy as np


#pythran export score_pairs(int list, int list, int list, int list, int)
def score_pairs(pred_rung, pred_kind, ans_rung, ans_ok, n_rungs):
    pairs = len(pred_rung)
    hits = 0
    rung_pairs = np.zeros(n_rungs, dtype=np.int64)
    rung_hits = np.zeros(n_rungs, dtype=np.int64)
    for i in range(pairs):
        if pred_kind[i] == 0:
            won = (ans_rung[i] == pred_rung[i]) and (ans_ok[i] == 1)
        else:
            agree = ((pred_kind[i] == 1) == (ans_ok[i] == 1))
            won = agree and (pred_rung[i] < 0 or ans_rung[i] == pred_rung[i])
        if won:
            hits += 1
        r = pred_rung[i]
        if 0 <= r < n_rungs:
            rung_pairs[r] += 1
            if won:
                rung_hits[r] += 1
    return hits, pairs, rung_pairs, rung_hits
