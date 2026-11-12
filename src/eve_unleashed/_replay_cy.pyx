# cython: boundscheck=False, wraparound=False, cdivision=True
# cython: freethreading_compatible=True
"""Native kernel: the scoring heart of the dream agreement replay.

Evolved GIL-free: conversion (Python lists -> C arrays) happens under the
GIL, the scoring loop runs `nogil`, and the module declares
freethreading_compatible so importing it never re-enables the interpreter
lock for the whole training process. Before this, the first import printed
the GIL warning and silently gave up free-threading process-wide -- the
exact class of defect this file exists to prevent.

Contract (unchanged): parallel int columns over PAIRED guesses/answers,
per-rung aggregation. Pairing stays in Python (native_replay.pair_columns).
"""
from libc.stdlib cimport malloc, free
from cpython.long cimport PyLong_AsLongLong


def score_pairs(list pred_rung, list pred_kind, list ans_rung, list ans_ok,
                int n_rungs):
    cdef Py_ssize_t n = len(pred_rung)
    cdef Py_ssize_t i
    if n != <Py_ssize_t>len(pred_kind) or n != <Py_ssize_t>len(ans_rung) \
            or n != <Py_ssize_t>len(ans_ok):
        raise ValueError("ragged replay columns")
    if n_rungs <= 0:
        raise ValueError("n_rungs must be positive")
    cdef long long *pr = <long long *>malloc(n * sizeof(long long))
    cdef long long *pk = <long long *>malloc(n * sizeof(long long))
    cdef long long *ar = <long long *>malloc(n * sizeof(long long))
    cdef long long *ok = <long long *>malloc(n * sizeof(long long))
    cdef long long *rp = <long long *>malloc(n_rungs * sizeof(long long))
    cdef long long *rh = <long long *>malloc(n_rungs * sizeof(long long))
    if not pr or not pk or not ar or not ok or not rp or not rh:
        free(pr); free(pk); free(ar); free(ok); free(rp); free(rh)
        raise MemoryError("replay column alloc failed")
    cdef long long hits = 0
    cdef long long r
    cdef int won
    for i in range(n):
        pr[i] = PyLong_AsLongLong(pred_rung[i])
        pk[i] = PyLong_AsLongLong(pred_kind[i])
        ar[i] = PyLong_AsLongLong(ans_rung[i])
        ok[i] = PyLong_AsLongLong(ans_ok[i])
    for r in range(n_rungs):
        rp[r] = 0
        rh[r] = 0
    with nogil:
        for i in range(n):
            if pk[i] == 0:
                won = (ar[i] == pr[i]) and (ok[i] == 1)
            else:
                won = ((pk[i] == 1) == (ok[i] == 1)) and \
                    (pr[i] < 0 or ar[i] == pr[i])
            if won:
                hits += 1
            r = pr[i]
            if 0 <= r < n_rungs:
                rp[r] += 1
                if won:
                    rh[r] += 1
    out_rp = [rp[r] for r in range(n_rungs)]
    out_rh = [rh[r] for r in range(n_rungs)]
    free(pr); free(pk); free(ar); free(ok); free(rp); free(rh)
    return int(hits), int(n), out_rp, out_rh
