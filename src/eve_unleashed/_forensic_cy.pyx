# cython: boundscheck=False, wraparound=False, cdivision=True
# cython: freethreading_compatible=True
"""Native kernel: forensic graph invariants at C speed.

The resweep recomputes every resilience number from the neighbor
functions; fault_stats alone runs n BFS traversals per solid. In
Python that is fine at n<=40 but it prices the scheduled sweep in
seconds; here it is microseconds. Contract: byte-identical results to
the Python path (test_eve_native asserts equality on all 7 solids).

Conversion (Python lists -> C arrays) happens under the GIL; every BFS
loop runs `nogil`. freethreading_compatible so importing never
re-enables the interpreter lock (the _replay_cy lesson).
"""
from libc.stdlib cimport malloc, free


cdef void _bfs(int n, int *off, int *flat, int src, int kill,
               int *dist, int *queue) noexcept nogil:
    cdef int i, head, tail, x, y
    for i in range(n):
        dist[i] = -1
    if src == kill:
        return
    dist[src] = 0
    queue[0] = src
    head = 0
    tail = 1
    while head < tail:
        x = queue[head]
        head += 1
        for i in range(off[x], off[x + 1]):
            y = flat[i]
            if y == kill or dist[y] != -1:
                continue
            dist[y] = dist[x] + 1
            queue[tail] = y
            tail += 1


def bfs_all(int n, list nbr):
    """(diameter, total_distance, n_pairs) over all ordered pairs."""
    cdef int *off = <int *>malloc((n + 1) * sizeof(int))
    cdef int total_e = 0
    cdef int v
    for v in range(n):
        total_e += len(nbr[v])
    cdef int *flat = <int *>malloc(total_e * sizeof(int))
    cdef int *dist = <int *>malloc(n * sizeof(int))
    cdef int *queue = <int *>malloc(n * sizeof(int))
    cdef int i, j, dmax = 0
    cdef long long total = 0
    cdef long long count = 0
    cdef int u, w
    if not off or not flat or not dist or not queue:
        free(off); free(flat); free(dist); free(queue)
        raise MemoryError("forensic kernel alloc failed")
    j = 0
    for v in range(n):
        off[v] = j
        for w in nbr[v]:
            flat[j] = w
            j += 1
    off[n] = j
    with nogil:
        for u in range(n):
            _bfs(n, off, flat, u, -1, dist, queue)
            for v in range(n):
                if v != u:
                    dmax = dist[v] if dist[v] > dmax else dmax
                    total += dist[v]
                    count += 1
    free(off); free(flat); free(dist); free(queue)
    return (dmax, total, count)


def fault_worst(int n, list nbr):
    """(worst_diameter, disconnects): kill every vertex, worst survives."""
    cdef int *off = <int *>malloc((n + 1) * sizeof(int))
    cdef int total_e = 0
    cdef int v
    for v in range(n):
        total_e += len(nbr[v])
    cdef int *flat = <int *>malloc(total_e * sizeof(int))
    cdef int *dist = <int *>malloc(n * sizeof(int))
    cdef int *queue = <int *>malloc(n * sizeof(int))
    cdef int i, j, kill, u, reach, mx, worst, disc
    cdef int w
    if not off or not flat or not dist or not queue:
        free(off); free(flat); free(dist); free(queue)
        raise MemoryError("forensic kernel alloc failed")
    j = 0
    for v in range(n):
        off[v] = j
        for w in nbr[v]:
            flat[j] = w
            j += 1
    off[n] = j
    worst = 0
    disc = 0
    with nogil:
        for kill in range(n):
            reach = 0
            mx = 0
            for u in range(n):
                if u == kill:
                    continue
                _bfs(n, off, flat, u, kill, dist, queue)
                for v in range(n):
                    if v == kill or v == u:
                        continue
                    if dist[v] == -1:
                        reach = -1
                        break
                    reach += 1
                    mx = dist[v] if dist[v] > mx else mx
                if reach == -1:
                    break
            if reach == -1:
                disc += 1
            else:
                worst = mx if mx > worst else worst
    free(off); free(flat); free(dist); free(queue)
    return (worst, disc)
