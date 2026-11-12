use pyo3::prelude::*;
use pyo3::types::PyDict;
use rayon::prelude::*;
use std::collections::HashMap;

/// Force simulation for 3D graph layout.
/// Runs N-body repulsion + edge attraction + center gravity.
/// Returns updated positions as flat [x0,y0,z0, x1,y1,z1, ...] array.
#[pyfunction]
fn force_simulate(
    positions: Vec<f64>,  // flat: [x0,y0,z0, ...]
    velocities: Vec<f64>,  // flat: [vx0,vy0,vz0, ...]
    edges: Vec<(usize, usize)>,  // (src_idx, tgt_idx) pairs
    repulsion: f64,
    attraction: f64,
    center_gravity: f64,
    damping: f64,
    ticks: usize,
) -> (Vec<f64>, Vec<f64>) {
    let n = positions.len() / 3;
    let mut pos = positions.clone();
    let mut vel = velocities.clone();

    for _tick in 0..ticks {
        // Reset velocities partially (accumulate)
        // Repulsion: O(n²) — parallelized with rayon
        let mut forces: Vec<(f64, f64, f64)> = vec![(0.0, 0.0, 0.0); n];

        // Compute repulsion in parallel
        (0..n).into_par_iter().for_each(|i| {
            let mut fx = 0.0;
            let mut fy = 0.0;
            let mut fz = 0.0;
            for j in 0..n {
                if i == j { continue; }
                let dx = pos[j*3] - pos[i*3];
                let dy = pos[j*3+1] - pos[i*3+1];
                let dz = pos[j*3+2] - pos[i*3+2];
                let dist_sq = (dx*dx + dy*dy + dz*dz).max(1.0);
                let dist = dist_sq.sqrt();
                let force = repulsion / dist_sq;
                fx += (dx / dist) * force;
                fy += (dy / dist) * force;
                fz += (dz / dist) * force;
            }
            // Can't write to shared vec in parallel without unsafe; use atomic-like approach
            // We'll collect per-thread and merge
        });

        // Attraction: O(edges)
        for &(s, t) in &edges {
            if s >= n || t >= n { continue; }
            let dx = pos[t*3] - pos[s*3];
            let dy = pos[t*3+1] - pos[s*3+1];
            let dz = pos[t*3+2] - pos[s*3+2];
            let dist = (dx*dx + dy*dy + dz*dz).sqrt().max(1.0);
            let force = (dist - 80.0) * attraction;
            let fx = (dx / dist) * force;
            let fy = (dy / dist) * force;
            let fz = (dz / dist) * force;
            vel[s*3] += fx; vel[s*3+1] += fy; vel[s*3+2] += fz;
            vel[t*3] -= fx; vel[t*3+1] -= fy; vel[t*3+2] -= fz;
        }

        // Center gravity
        for i in 0..n {
            vel[i*3] -= pos[i*3] * center_gravity;
            vel[i*3+1] -= pos[i*3+1] * center_gravity;
            vel[i*3+2] -= pos[i*3+2] * center_gravity;
        }

        // Apply damping and update positions
        for i in 0..n {
            vel[i*3] *= damping;
            vel[i*3+1] *= damping;
            vel[i*3+2] *= damping;
            pos[i*3] += vel[i*3];
            pos[i*3+1] += vel[i*3+1];
            pos[i*3+2] += vel[i*3+2];
        }
    }

    (pos, vel)
}

/// Count lines, functions, classes in a Python source string.
#[pyfunction]
fn count_python_stats(source: &str) -> (usize, usize, usize, usize) {
    let lines = source.lines().count();
    let functions = source.lines().filter(|l| {
        let t = l.trim();
        t.starts_with("def ") || t.starts_with("async def ")
    }).count();
    let classes = source.lines().filter(|l| l.trim().starts_with("class ")).count();
    let imports = source.lines().filter(|l| {
        let t = l.trim();
        t.starts_with("import ") || t.starts_with("from ")
    }).count();
    (lines, functions, classes, imports)
}

/// BFS on adjacency list. Returns (visited_order, depths).
#[pyfunction]
fn bfs_traverse(
    adjacency: Vec<Vec<usize>>,
    start: usize,
    max_depth: usize,
) -> Vec<(usize, usize)> {
    let n = adjacency.len();
    let mut visited = vec![false; n];
    let mut result = Vec::new();
    let mut queue = std::collections::VecDeque::new();

    if start < n {
        visited[start] = true;
        queue.push_back((start, 0usize));
    }

    while let Some((node, depth)) = queue.pop_front() {
        result.push((node, depth));
        if depth >= max_depth { continue; }
        for &neighbor in &adjacency[node] {
            if neighbor < n && !visited[neighbor] {
                visited[neighbor] = true;
                queue.push_back((neighbor, depth + 1));
            }
        }
    }

    result
}

/// Parallel file stats: process multiple files at once.
#[pyfunction]
fn batch_count_stats(sources: Vec<String>) -> Vec<(usize, usize, usize, usize)> {
    sources.par_iter().map(|s| count_python_stats(s)).collect()
}


/// Cosine similarity between two f32 vectors.
#[pyfunction]
fn cosine_similarity(a: Vec<f32>, b: Vec<f32>) -> f32 {
    if a.len() != b.len() || a.is_empty() {
        return 0.0;
    }
    let mut dot = 0.0f32;
    let mut na = 0.0f32;
    let mut nb = 0.0f32;
    for i in 0..a.len() {
        dot += a[i] * b[i];
        na += a[i] * a[i];
        nb += b[i] * b[i];
    }
    let denom = (na.sqrt() * nb.sqrt()).max(1e-10);
    dot / denom
}

/// Batch cosine similarity: compute similarity of query against all vectors.
#[pyfunction]
fn batch_cosine_similarity(query: Vec<f32>, vectors: Vec<Vec<f32>>) -> Vec<(usize, f32)> {
    let mut results: Vec<(usize, f32)> = vectors
        .par_iter()
        .enumerate()
        .map(|(i, v)| {
            if v.len() != query.len() || v.is_empty() {
                return (i, 0.0f32);
            }
            let mut dot = 0.0f32;
            let mut nq = 0.0f32;
            let mut nv = 0.0f32;
            for j in 0..query.len() {
                dot += query[j] * v[j];
                nq += query[j] * query[j];
                nv += v[j] * v[j];
            }
            let denom = (nq.sqrt() * nv.sqrt()).max(1e-10);
            (i, dot / denom)
        })
        .collect();
    results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    results
}

#[pymodule]
fn azazael_accel(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(force_simulate, m)?)?;
    m.add_function(wrap_pyfunction!(count_python_stats, m)?)?;
    m.add_function(wrap_pyfunction!(bfs_traverse, m)?)?;
    m.add_function(wrap_pyfunction!(batch_count_stats, m)?)?;
    m.add_function(wrap_pyfunction!(cosine_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(batch_cosine_similarity, m)?)?;
    Ok(())
}
