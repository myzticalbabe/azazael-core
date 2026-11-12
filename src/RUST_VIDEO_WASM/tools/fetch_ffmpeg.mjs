// Vendor ffmpeg.wasm (single-threaded core) into demo/vendor/ffmpeg/.
//
//   node tools/fetch_ffmpeg.mjs
//
// 32 MB does not belong in git, so the demo loads these from disk and reports
// "not vendored" instead of pretending the panel works. Each file is pinned by
// sha256: a truncated download here is a wasm trap at runtime, far from here.
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync, existsSync, readFileSync, statSync } from "node:fs";

const BASE = "https://unpkg.com";
const FILES = [
  ["@ffmpeg/ffmpeg@0.12.15", "dist/umd/ffmpeg.js", "ad4cfe957589995dea03fc8de1fd5e9f5cb4558a7282913172203082a65bbfaa", 4420],
  ["@ffmpeg/ffmpeg@0.12.15", "dist/umd/814.ffmpeg.js", "976f4174ae7da80c0d4f9523ee6dde3ecbce7dc2ee392b2a5322049abb9b8627", 3177],
  ["@ffmpeg/core@0.12.10", "dist/umd/ffmpeg-core.js", "b266ab5b952555881dd6310663986994a182acb2b7ff25cf10a25f7a37ac2b21", 112059],
  ["@ffmpeg/core@0.12.10", "dist/umd/ffmpeg-core.wasm", "9f57947a5bd530d8f00c5b3f2cb2a3492faa7e5d823315342d6a8656d0a6b7b7", 32232419],
];

const OUT = new URL("../demo/vendor/ffmpeg/", import.meta.url);
mkdirSync(OUT, { recursive: true });

let bad = 0;
for (const [pkg, path, sha, bytes] of FILES) {
  const dest = new URL(path.split("/").pop(), OUT);
  if (existsSync(dest)) {
    const got = readFileSync(dest);
    if (got.length === bytes && createHash("sha256").update(got).digest("hex") === sha) {
      console.log(`cached  ${path.split("/").pop()}  ${got.length} bytes`);
      continue;
    }
    console.log(`stale   ${path.split("/").pop()} — refetching`);
  }
  const url = `${BASE}/${pkg}/${path}`;
  const res = await fetch(url);
  if (!res.ok) {
    console.log(`FAIL    ${url} -> ${res.status}`);
    bad++;
    continue;
  }
  const buf = Buffer.from(await res.arrayBuffer());
  const digest = createHash("sha256").update(buf).digest("hex");
  if (digest !== sha || buf.length !== bytes) {
    console.log(`FAIL    ${url} sha/bytes mismatch (${buf.length} bytes, ${digest.slice(0, 12)})`);
    bad++;
    continue;
  }
  writeFileSync(dest, buf);
  console.log(`fetched ${path.split("/").pop()}  ${buf.length} bytes  sha ok`);
}

if (bad) {
  console.log(`\n${bad} file(s) failed — the in-page decoder will stay disabled.`);
  process.exit(1);
}
console.log(`\nvendored ${FILES.length} files into demo/vendor/ffmpeg/ ` +
  `(${(FILES.reduce((a, f) => a + f[3], 0) / 1048576).toFixed(1)} MB)`);
