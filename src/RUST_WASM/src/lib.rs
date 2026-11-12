use wasm_bindgen::prelude::*;
use js_sys::Float32Array;

/// Force simulation for 3D graph layout, compiled to WASM.
/// Runs N-body repulsion + edge attraction + center gravity.
#[wasm_bindgen]
pub fn force_simulate_wasm(
    positions: &Float32Array,
    velocities: &Float32Array,
    edges_src: &[u32],
    edges_tgt: &[u32],
    n: usize,
    repulsion: f32,
    attraction: f32,
    center_gravity: f32,
    damping: f32,
    ticks: u32,
) -> Float32Array {
    let mut pos: Vec<f32> = (0..positions.length()).map(|i| positions.get_index(i)).collect();
    let mut vel: Vec<f32> = (0..velocities.length()).map(|i| velocities.get_index(i)).collect();

    for _tick in 0..ticks {
        // Repulsion: O(n²)
        let mut fx_all = vec![0.0f32; n];
        let mut fy_all = vec![0.0f32; n];
        let mut fz_all = vec![0.0f32; n];

        for i in 0..n {
            let mut fx = 0.0f32;
            let mut fy = 0.0f32;
            let mut fz = 0.0f32;
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
            fx_all[i] = fx;
            fy_all[i] = fy;
            fz_all[i] = fz;
        }

        // Attraction: O(edges)
        for k in 0..edges_src.len() {
            let s = edges_src[k] as usize;
            let t = edges_tgt[k] as usize;
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

        // Apply repulsion forces
        for i in 0..n {
            vel[i*3] += fx_all[i];
            vel[i*3+1] += fy_all[i];
            vel[i*3+2] += fz_all[i];
        }

        // Center gravity + damping + update
        for i in 0..n {
            vel[i*3] -= pos[i*3] * center_gravity;
            vel[i*3+1] -= pos[i*3+1] * center_gravity;
            vel[i*3+2] -= pos[i*3+2] * center_gravity;
            vel[i*3] *= damping;
            vel[i*3+1] *= damping;
            vel[i*3+2] *= damping;
            pos[i*3] += vel[i*3];
            pos[i*3+1] += vel[i*3+1];
            pos[i*3+2] += vel[i*3+2];
        }
    }

    let result = Float32Array::new_with_length(pos.len() as u32);
    for (i, &v) in pos.iter().enumerate() {
        result.set_index(i as u32, v);
    }
    result
}

// ---- real SIMD, 2026-11-01 ---------------------------------------------------
//
// The two exported functions used to be THE SAME SCALAR LOOP, differing only in an
// `i as u32` cast, both documented as "SIMD via auto-vectorization". Measured on
// this crate before the change: cosine_similarity_simd 14.43 us vs
// cosine_similarity_wasm 14.79 us per 1536-dim call (ratio 1.02) -- identical work,
// and 97% of the time went on `Float32Array::get_index`, which is a call out to the
// JS heap per element, not arithmetic.
//
// Three things were wrong and only one of them was the missing target feature:
//   1. no simd128 (fixed in .cargo/config.toml),
//   2. the per-element JS-heap access made the loop memory-bound, so no vectoriser
//      could have helped: the slice is copied once, then the kernel is tight,
//   3. FP reduction is non-associative, so LLVM will not auto-vectorise `dot += a*b`
//      even WITH +simd128. SIMD here has to be written, not requested.
//
// Lane order is fixed (four partial sums, then a pairwise horizontal sum), so the
// result is deterministic for a given input -- and it differs from the scalar sum in
// the last bits, which is why bench.mjs checks numerics against a JS reference on
// every run instead of trusting the name.
#[cfg(target_feature = "simd128")]
#[inline]
fn hsum(v: core::arch::wasm32::v128) -> f32 {
    use core::arch::wasm32::*;
    let pairs = f32x4_add(v, i32x4_shuffle::<1, 0, 3, 2>(v, v));
    let quad = f32x4_add(pairs, i32x4_shuffle::<2, 3, 0, 1>(pairs, pairs));
    f32x4_extract_lane::<0>(quad)
}

#[cfg(target_feature = "simd128")]
#[inline]
fn dot3_simd(av: &[f32], bv: &[f32]) -> (f32, f32, f32) {
    use core::arch::wasm32::*;
    let n = av.len().min(bv.len());
    let mut acc_dot = f32x4_splat(0.0);
    let mut acc_a = f32x4_splat(0.0);
    let mut acc_b = f32x4_splat(0.0);
    let mut i = 0usize;
    while i + 4 <= n {
        // SAFETY: `i + 4 <= n <= av.len()` and `n <= bv.len()`, so both 4-lane
        // loads are fully inside their slices.
        let va = unsafe { v128_load(av.as_ptr().add(i) as *const v128) };
        let vb = unsafe { v128_load(bv.as_ptr().add(i) as *const v128) };
        acc_dot = f32x4_add(acc_dot, f32x4_mul(va, vb));
        acc_a = f32x4_add(acc_a, f32x4_mul(va, va));
        acc_b = f32x4_add(acc_b, f32x4_mul(vb, vb));
        i += 4;
    }
    let (mut dot, mut na, mut nb) = (hsum(acc_dot), hsum(acc_a), hsum(acc_b));
    while i < n {
        dot += av[i] * bv[i];
        na += av[i] * av[i];
        nb += bv[i] * bv[i];
        i += 1;
    }
    (dot, na, nb)
}

#[inline]
fn dot3_scalar(av: &[f32], bv: &[f32]) -> (f32, f32, f32) {
    let n = av.len().min(bv.len());
    let (mut dot, mut na, mut nb) = (0.0f32, 0.0f32, 0.0f32);
    for i in 0..n {
        dot += av[i] * bv[i];
        na += av[i] * av[i];
        nb += bv[i] * bv[i];
    }
    (dot, na, nb)
}

#[inline]
fn cos_from(dot: f32, na: f32, nb: f32) -> f32 {
    let denom = (na.sqrt() * nb.sqrt()).max(1e-10);
    dot / denom
}

/// Cosine similarity over a contiguous copy of both inputs, using 128-bit SIMD when
/// the build enables `simd128` and the same lane-ordered scalar kernel otherwise.
///
/// The copy is the point: it replaces `len` calls into the JS heap with one memcpy.
#[wasm_bindgen]
pub fn cosine_similarity_simd(a: &Float32Array, b: &Float32Array) -> f32 {
    let av = a.to_vec();
    let bv = b.to_vec();
    if av.is_empty() || bv.is_empty() {
        return 0.0;
    }
    #[cfg(target_feature = "simd128")]
    let (dot, na, nb) = dot3_simd(&av, &bv);
    #[cfg(not(target_feature = "simd128"))]
    let (dot, na, nb) = dot3_scalar(&av, &bv);
    cos_from(dot, na, nb)
}
#[wasm_bindgen]
pub fn cosine_similarity_wasm(a: &Float32Array, b: &Float32Array) -> f32 {
    let len = a.length().min(b.length());
    if len == 0 { return 0.0; }
    let mut dot = 0.0f32;
    let mut na = 0.0f32;
    let mut nb = 0.0f32;
    for i in 0..len {
        let ai = a.get_index(i);
        let bi = b.get_index(i);
        dot += ai * bi;
        na += ai * ai;
        nb += bi * bi;
    }
    let denom = (na.sqrt() * nb.sqrt()).max(1e-10);
    dot / denom
}
