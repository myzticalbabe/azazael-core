# azazael-video-wasm — a music video, four ways through Rust/WASM

A `.mp4` cannot be handed to WASM the way an image can be handed to a JPEG
decoder. There is no single "video in Rust/WASM"; there is a choice about which
layer decodes and which layer owns the pixels. This crate implements all four
choices against the same track and measures each one, so the trade-offs are
readings instead of opinions.

| | who decodes | what Rust does | module | verdict |
|---|---|---|---|---|
| **A** browser + Rust | `<video>` (H.264/AAC) | 32-band DFT on PCM, aurora field | 120 KB | **the default.** Streams any length |
| **B** frames baked in | `jpeg-decoder` in-module | unpack + RGBA | 120 KB | works, wrong for songs (see size) |
| **C** pixel processing | the browser (canvas) | every pixel, every frame | 120 KB | **the useful one** for effects |
| **C+** pixel processing | WebCodecs `VideoFrame` | I420 → RGBA with real strides | 120 KB | 1.5 bytes/px instead of 4 |
| **D** packed stream | ffmpeg (offline) | RGB24 → RGBA in-module | 120 KB | correct container, honest limit |
| **D+** packed stream | `ffmpeg.wasm` **in the page** | RGB24 → RGBA in-module | 120 KB + 30.9 MB core | a full decode with no media element |

## Measured, not estimated

Source: `C:/Users/clint/Music/In The End [Official HD Music Video] - Linkin Park.mp4`
— 28.69 MB, **AV1 + Opus**, 1280×712, 218.2 s. Re-encoded for the demo because a
browser may not carry whatever codec a download happened to use.

```
sample.mp4    33.82 MB   218.2 s   1294x720  H.264 crf30 + AAC 96k, faststart
frames.bin     6.57 MB    12.1 s   640x360   145 JPEG frames @12fps   -> 0.54 MB/s
raw.bin       11.87 MB    12.0 s   320x180    72 RGB24 frames @6fps   -> 165 KB/frame
wasm            120 KB    both targets
spectrum.json   1.03 MB   6,546 frames @30fps   32 bands + RMS, full 218 s
```

`spectrum.json` is log power, five decades: **1.0 is the frame's peak band, 0.0 is
100 dB below it.** Linear values at 3 dp erased every band under 0.0005 — 34% of
the cells read exactly `0` in the first bake, which is why the field had a hole in
its middle.

Read the B row as the argument against B: 0.54 MB/s at 640×360
means **≈119 MB to carry this one track**, three-and-a-half times the 33.82 MB
mp4 that holds it at 720p — inter-frame prediction is doing real work, and
baking stills throws it away. (The hi-res re-cook of 2026-11-09 strengthened
this verdict: doubling resolution quadrupled the baking penalty.)

## Run it

```bash
cd src/RUST_VIDEO_WASM
wasm-pack build --target web    --out-dir pkg --release   # demo
wasm-pack build --target nodejs --out-dir pkg-node --release   # harness

python tools/make_assets.py "C:/Users/clint/Music/In The End [Official HD Music Video] - Linkin Park.mp4"
python tools/make_assets.py                                # or: synthesise a 6 s clip

node test.mjs                     # every export, real assets, exit 0
node tools/fetch_ffmpeg.mjs       # 30.9 MB core for mode D+, sha256-pinned
node tools/bake_spectrum.mjs      # full track -> demo/assets/spectrum.json
node tools/render_highlight.mjs --start 55 --seconds 20 --out C:/tmp/hl.mp4
node tools/verify_demo.mjs        # drives all four panels, asserts both decode paths
python -m http.server 8123 --bind 127.0.0.1
#  -> http://127.0.0.1:8123/demo/index.html
```

The ffmpeg core, `pkg/`, `pkg-node/` and `demo/assets/` are all generated and
gitignored; every tool rebuilds what it needs.

`python tools/make_assets.py <any.mp4>` is the whole swap-in: it re-encodes to
H.264/AAC and re-cooks both blobs. It needs only ffmpeg and Python's stdlib.

## The four architectures

**A — the browser decodes, Rust paints.** `<video>` handles the container,
H.264 and AAC, including seeking and buffering. Rust gets 1024 PCM samples from
an `AnalyserNode`, computes 32 band energies with a Hann-windowed DFT, smooths
them and renders an additive field composited with `mix-blend-mode: screen` so
black contributes nothing. This is the only architecture that streams a file of
any length, and it is the one to ship.

**B — frames baked into the module.** One `AVFB` blob: magic, count, w, h, fps,
an offset table, then concatenated JPEGs. `decode_frame_rgba` decodes in-module.
No media element, no network after load — and a payload that grows with every
frame rather than every shot.

**C — Rust owns every pixel.** `drawImage` → `getImageData` →
`process_frame_rgba` → `putImageData`. Three effects ship: beat tint, Sobel edge
glow, chroma split with scanlines; all amplitude-linked to the audio energy
measured by A. This is where Rust earns its place, because the work is per-pixel
and the browser is only a decoder.

**C+ — WebCodecs, no CPU readback.** `new VideoFrame(video)` → `copyTo` into an
I420 buffer → `i420_to_rgba` with the coded strides passed straight through. Rust
receives **1.5 bytes per pixel instead of 4**, and the two `getImageData`/
`putImageData` copies disappear. Measured in the demo readout: `webcodecs i420
862×480`, 620,640 bytes per frame against 1,655,040 for RGBA.

One behaviour worth knowing: Chrome **rejects an explicit `copyTo` to a
non-RGB format** — `allocationSize({format:"I420"})` throws `NotSupportedError`.
Use `allocationSize()` + `copyTo(buf)`, which returns per-plane `{offset, stride}`.

**D — a packed stream parsed in Rust.** `AVRD` blob: header plus contiguous
`w·h·3` RGB24 frames, parsed and widened to RGBA by `unpack_rgb_frame`.

**D+ — ffmpeg.wasm decodes the mp4 in the page.** The panel loads the UMD wrapper
plus its 30.9 MB core on demand, writes the mp4 into the wasm filesystem, runs
`-ss 30 -t 6 -an -vf scale=320:180 -f rawvideo -pix_fmt rgb24`, and plays the
result through `stream_frame_count` / `unpack_stream_frame`. No `<video>` element
is involved anywhere on that path. Measured: `wasm decode: 145 frames in 0.9s
(6.54x realtime, 24.2 fps)`.

Two traps, both paid for. Passing `classWorkerURL` makes `@ffmpeg/ffmpeg` create a
**module** worker, which cannot `importScripts()` the UMD core — omit it and the
wrapper finds its own worker beside `ffmpeg.js`. And the clip is 24 fps, not the
30 a `requestAnimationFrame` loop assumes, so D+ derives fps as
`frames / seconds` and plays 25% fast if it does not.

## Three defects this crate paid for, kept as regression tests

**The DFT phase.** The first version normalised the phase as `-2π·k·i/win` in
`f32`. At k≈1024, i≈1024 that angle reaches ~6.4e6 rad, where f32 resolves only
~0.5 rad — the transform degenerated into noise: a 3 kHz tone peaked at band 30
of 32, and DC peaked at band 22. It survived a "does it return numbers" check
because an impulse has a flat magnitude by construction, and flat looked correct.
The fix is an exact integer reduce, `(k*i) % win`, with `f64` accumulation.
`test.mjs` carries the control: **DC must peak at band 0**.

**The mirrored axis.** Bands were mapped over `0..win`, i.e. over `0..Fs`, so
band `b` and band `31-b` read the conjugate pair `k` and `win-k` — identical in
magnitude for a real signal. Half the field was a mirror image, both bright edges
were the same DC bin, and a single tone lit both ends of the frame. The axis is
now `0..win/2` (Nyquist), and `test.mjs` asserts that a tone in band 4 leaves its
mirror, band 27, dark. The old assertion (`3 kHz peaks at band 2`) had been
written to match the bug: 3000 Hz is 0.125 of the way to Nyquist, so it belongs
in band 4.

**The erased quiet bands.** One bin per band meant a band landing in a spectral
null read exactly `0.0` in every frame (band 15 did, in all 6,546). Each band now
means up to 5 taps across its own bin range. Separately, the bake stored linear
magnitudes at 3 dp, which rounded every band under 0.0005 to zero — 34% of the
baked cells — so the middle of the field was black by arithmetic, not by music. It
now stores log power, and the page decodes it back to linear before any display
maths.

## The rendered clip

`demo/aurora.html` is deterministic: every pixel is a function of the frame index
passed in `?f=`, nothing reads the clock, so frame N is byte-identical on a re-run
and a killed render resumes at N+1. `tools/render_highlight.mjs` drives it through
CDP, captures each frame with Chrome (never PIL), encodes with ffmpeg, then
verifies frame count, three distinct hashes and the decoded frames read back out
of the mp4.

One display decision is measured, not invented: this encode low-passes at ~16 kHz,
so 10 of the 32 bands measure -80 dB or lower in every frame. The field spans the
22 bands that carry the track and the frame says so (`16.5 kHz of spectrum,
22/32 bands`) instead of spending a third of the picture on silence.

## Layout

```
Cargo.toml               crate azazael-video-wasm (cdylib + rlib, edition 2024)
src/lib.rs               all exports: spectrum_bands, render_aurora,
                         frame_count, frame_dims, decode_frame_rgba,
                         process_frame_rgba, raw_frame_count, raw_frame_dims,
                         unpack_rgb_frame, stream_frame_count,
                         unpack_stream_frame, i420_to_rgba
test.mjs                 node harness, real assets, exit status
tools/make_assets.py     ffmpeg pipeline + both blob packers (stdlib only)
tools/fetch_ffmpeg.mjs   vendors the pinned ffmpeg.wasm core (sha256 + sizes)
tools/cdp.mjs            dependency-free Chrome DevTools helper
                         (static server, launch, evaluate, screenshot)
tools/verify_demo.mjs    CDP check: four panels paint, WebCodecs and ffmpeg.wasm
                         readouts both asserted
tools/bake_spectrum.mjs  full track -> demo/assets/spectrum.json
  demo/bake.html         the page that runs the Rust DFT over the decoded PCM
tools/render_highlight.mjs  deterministic frame capture -> mp4 + verification
  demo/aurora.html       the deterministic renderer behind that clip
demo/index.html          the four-tab demo
```

`pkg/`, `pkg-node/`, `target/` and `demo/assets/` are generated and gitignored;
this README's numbers are produced by the commands above, not stored with them.

## Law (ordered 2026-11-09: the harness prevents the drift)

A render made to be **watched** is a different artifact class than a render
made to **verify**. This crate produces the second and never the first:

- Instruments (`test.mjs`, `verify_demo.mjs`, `bake_*`, `make_assets.py`)
  measure. A capture exists to be counted, hashed, and read back — frame
  count, three distinct hashes, decoded frames out of the encoded file.
- No mp4 is a deliverable, is committed, is served, or is played as one.
  Baked mp4s live in gitignored `demo/assets/` and are deleted after
  verification. Anything with a Play button is not the goods: the 120 KB
  module and its proofs are.
- Showmanship drift — render, serve, play for an audience — is deleted on
  sight, not reviewed. The audience learns from numbers and annotations
  (panels carry verdicts, bands carry Hz, defects carry names), never
  from playback.

## A note on the source clip

The demo plays a licensed Linkin Park music video because that is the file in the
Music folder. Nothing from it is committed — `demo/assets/` is gitignored, and the
blob formats carry no copy-protection circumvention. Point `make_assets.py` at
your own file and the numbers re-measure.
