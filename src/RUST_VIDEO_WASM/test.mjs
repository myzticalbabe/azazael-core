// Run every WASM export against the generated assets. No browser needed.
//   node test.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const require = createRequire(import.meta.url);
const wasm = require("./pkg-node/azazael_video_wasm.js");

let fails = 0;
const check = (name, cond, detail = "") => {
  if (!cond) fails++;
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}${detail ? "  " + detail : ""}`);
};

const frames = readFileSync("demo/assets/frames.bin");
const raw = readFileSync("demo/assets/raw.bin");
const mp4 = readFileSync("demo/assets/sample.mp4");

// ---- mode B: frames baked into the blob
const nB = wasm.frame_count(frames);
const dimsB = wasm.frame_dims(frames);
check("B frame_count", nB > 0, `${nB} frames`);
check("B frame_dims", dimsB[0] === 640 && dimsB[1] === 360, `${dimsB.join("x")}`);

const f0 = wasm.decode_frame_rgba(frames, 0);
check("B decode size", f0.length === 640 * 360 * 4, `${f0.length} bytes`);
const fmid = wasm.decode_frame_rgba(frames, Math.floor(nB / 2));
let differ = 0;
for (let i = 0; i < Math.min(f0.length, fmid.length); i += 4) if (f0[i] !== fmid[i]) differ++;
check("B decodes distinct frames", differ > 100, `${differ} differing red samples`);
check("B rejects out-of-range index", wasm.decode_frame_rgba(frames, nB).length === 0);

// ---- mode D: packed RGB24 stream
const nD = wasm.raw_frame_count(raw);
check("D raw_frame_count", nD > 0, `${nD} frames`);
const d0 = wasm.unpack_rgb_frame(raw, 0);
check("D unpack size", d0.length === 320 * 180 * 4, `${d0.length} bytes`);
check("D alpha opaque", d0[3] === 255);

// ---- D+: headerless RGB24 stream (ffmpeg.wasm rawvideo output)
const W = 8, H = 4;
const stream = new Uint8Array(W * H * 3 * 3);
for (let f = 0; f < 3; f++) {
  for (let i = 0; i < W * H; i++) {
    const p = f * W * H * 3 + i * 3;
    stream[p] = f * 80; stream[p + 1] = 40 + f; stream[p + 2] = 200 - f;
  }
}
check("D+ stream_frame_count", wasm.stream_frame_count(stream.length, W, H) === 3,
  `${wasm.stream_frame_count(stream.length, W, H)} frames`);
const s1 = wasm.unpack_stream_frame(stream, W, H, 1);
check("D+ unpack picks the right frame", s1.length === W * H * 4 && s1[0] === 80 && s1[1] === 41 && s1[2] === 199,
  `px0=${s1[0]},${s1[1]},${s1[2]}`);
check("D+ alpha opaque", s1[3] === 255);
check("D+ rejects past the end", wasm.unpack_stream_frame(stream, W, H, 3).length === 0);
check("D+ truncated tail is not a frame", wasm.stream_frame_count(stream.length - 1, W, H) === 2);

// ---- C+: I420 conversion with real video strides
// 4x2 frame, black (Y=16,U=V=128) on the left half, white (Y=235) on the right.
const yw = 4, yh = 2, yStride = 6;          // 2 bytes of row padding, as Chrome emits
const cStride = 4;                          // 2 bytes of chroma padding
const i420 = new Uint8Array(yStride * yh + cStride * yh);
for (let y = 0; y < yh; y++) {
  i420[y * yStride + 0] = 16; i420[y * yStride + 1] = 16;
  i420[y * yStride + 2] = 235; i420[y * yStride + 3] = 235;
}
const uOff = yStride * yh, vOff = uOff + cStride * (yh / 2);
i420.fill(128, uOff, vOff);
i420.fill(128, vOff, vOff + cStride * (yh / 2));
const rgb = wasm.i420_to_rgba(i420, yw, yh, 0, yStride, uOff, cStride, vOff, cStride);
check("C+ i420 size", rgb.length === yw * yh * 4, `${rgb.length} bytes`);
check("C+ black stays black", rgb[0] === 0 && rgb[1] === 0 && rgb[2] === 0, `${rgb[0]},${rgb[1]},${rgb[2]}`);
check("C+ white stays white", rgb[8] === 255 && rgb[9] === 255 && rgb[10] === 255, `${rgb[8]},${rgb[9]},${rgb[10]}`);
check("C+ padding is not read as pixels", wasm.i420_to_rgba(i420, yw, yh, 0, yStride, uOff, cStride, vOff, cStride).length === yw * yh * 4);
check("C+ short buffer returns empty", wasm.i420_to_rgba(i420.slice(0, 4), yw, yh, 0, yStride, uOff, cStride, vOff, cStride).length === 0);

// ---- mode A: spectrum + aurora
const N = 1024, sr = 48000, hz = 3000; // bin 64 of 1024 == 3000 Hz
const pcm = new Float32Array(N);
for (let i = 0; i < N; i++) pcm[i] = Math.sin((2 * Math.PI * hz * i) / sr);
const bands = wasm.spectrum_bands(pcm, 32);
const peak = bands.indexOf(Math.max(...bands));
// 3000 Hz is 3000/24000 = 0.125 of the way to Nyquist, and the bands now span
// 0..Nyquist in 32 equal slices of 16 bins: bin 64 lands in band 4. This
// assertion read `peak === 2` until 2026-11-03 because the axis was mapped
// over 0..Fs; the expectation had been written to match the mirror bug.
check("A spectrum peaks at the right band", peak === 4, `peak band ${peak} (expected 4)`);
check("A bands normalised", Math.abs(Math.max(...bands) - 1) < 1e-6 && Math.min(...bands) >= 0);

// Negative control for the mirrored axis (fixed 2026-11-03): band b and band
// 31-b read conjugate bins of a real signal, so the mirror had identical
// magnitudes and a single tone lit BOTH edges of the field. A tone at band 4
// must leave its mirror (band 27) dark.
const mirror = Array.from(bands);
check("A spectrum is not mirrored", mirror[4] > 0.5 && mirror[27] < 0.05,
  `band4 ${mirror[4].toFixed(3)} band27 ${mirror[27].toFixed(3)}`);

// Mid and high bands must carry signal: one bin per band read 0 in every frame
// whenever that bin sat in a null of the chord. Three tones in three registers,
// each landing in its own band, must all register.
const multi = new Float32Array(N);
for (const f of [1500, 9000, 18000]) {
  for (let i = 0; i < N; i++) multi[i] += Math.sin((2 * Math.PI * f * i) / sr) / 3;
}
const mBands = Array.from(wasm.spectrum_bands(multi, 32));
check("A mid/high bands carry signal",
  mBands[2] > 0.2 && mBands[12] > 0.2 && mBands[24] > 0.2,
  `bands 2/12/24 = ${mBands[2].toFixed(2)}/${mBands[12].toFixed(2)}/${mBands[24].toFixed(2)}`);

// Negative control for the f32-phase bug fixed on 2026-11-03: DC must peak in
// band 0. Before the integer-modulo fix this peaked at band 22.
const dc = new Float32Array(1024).fill(1.0);
const dcBands = Array.from(wasm.spectrum_bands(dc, 32));
check("A DC peaks at band 0", dcBands.indexOf(Math.max(...dcBands)) === 0,
  `peak band ${dcBands.indexOf(Math.max(...dcBands))}`);

const aurora = wasm.render_aurora(128, 72, new Float32Array(bands), 0.5);
check("A aurora size", aurora.length === 128 * 72 * 4, `${aurora.length} bytes`);
let lit = 0;
for (let i = 3; i < aurora.length; i += 4) if (aurora[i] > 8) lit++;
check("A aurora glows", lit > 200, `${lit} lit pixels`);

// ---- mode C: per-frame processing
const snapshot = wasm.decode_frame_rgba(frames, 0);
for (const mode of [0, 1, 2]) {
  const buf = new Uint8Array(snapshot);
  wasm.process_frame_rgba(buf, 640, 360, mode, 1.0, 0.8);
  let changed = 0;
  for (let i = 0; i < buf.length; i++) if (buf[i] !== snapshot[i]) changed++;
  check(`C mode ${mode} mutates the frame`, changed > 1000, `${changed} bytes changed`);
}

// ---- size reality check: report rates, never a total-vs-total ratio.
// The blob and the mp4 can cover different spans (the blob is cooked from an
// excerpt), so only bytes-per-second is a like-for-like number.
const blobSec = nB / dimsB[2];
const blobRate = frames.length / blobSec;
check("payload: assets are real", mp4.length > 1000 && frames.length > 1000,
  `mp4 ${(mp4.length / 1048576).toFixed(2)} MB total, blob ${(frames.length / 1048576).toFixed(2)} MB for ${blobSec.toFixed(0)}s`);
console.log(`INFO  baked frames cost ${(blobRate / 1048576).toFixed(2)} MB/s at 640x360/12fps ` +
  `(${((blobRate / 1048576) * 218).toFixed(0)} MB for a 3m38s track)`);

console.log(fails === 0 ? "\nALL CHECKS PASSED" : `\n${fails} CHECK(S) FAILED`);
process.exit(fails === 0 ? 0 : 1);
