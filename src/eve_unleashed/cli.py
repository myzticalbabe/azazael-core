#!/usr/bin/env python3
"""
Eve Unleashed CLI
=================
Command-line interface for the Eve Unleashed inference engine.

Usage:
    eve infer --model <path> --input <data> [--framework auto] [--output json]
    eve serve --port 8080 [--host 0.0.0.0]
    eve stats
    eve list

Examples:
    eve infer --model /models/resnet.pt --input "[[1.0, 2.0, 3.0]]"
    eve infer --model /models/qwen.gguf --input "Hello, world!"
    eve serve --port 8080
    curl http://localhost:8080/health
    curl -X POST http://localhost:8080/infer -H "Content-Type: application/json" \\
         -d '{"model":"/models/resnet.pt","input":[1.0,2.0,3.0]}'
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List
import time

from .trainer import TrainConfig, freeze_fixture, null_control, run

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import Optional  # removed — all names have native equivalents

SWARM_TIMEOUT_S = 180


def _local_answer(prompt, system=None):
    """Try the local LLM first (LM Studio / llama-server / GGUF direct).

    Returns (text, provider_tag) or (None, reason). Never raises: the
    local path is a preference, not a gate -- failure falls through to
    the swarm, and the reason names why."""
    try:
        from .interpreter_bridge import EveInterpreterBridge
    except Exception as e:
        note_failure("cli:local-import", e)
        return None, "local bridge unavailable (%s)" % e
    try:
        bridge = EveInterpreterBridge()
        out = bridge.run_local(prompt, system=system)
    except Exception as e:
        note_failure("cli:local-run", e)
        return None, "local run failed (%s)" % e
    if isinstance(out, dict) and out.get("status") == "ok" and out.get("result"):
        tag = out.get("provider") or out.get("model") or "local"
        return str(out["result"]), "local:%s" % tag
    err = (out or {}).get("error", "no local generation path") if isinstance(out, dict) else "no local generation path"
    return None, "local unavailable (%s)" % str(err)[:120]


SWARM_PER_PROVIDER_S = 60
SWARM_NSP_SPECS = ["valid_query_features", "valid_provider_selection",
                   "no_dead_provider"]


def _swarm_fallback(prompt):
    """LLM-swarm fallback: route a text prompt through the free-provider
    swarm when no local framework can run it.

    Sequential and bounded, never broadcast: broadcast waits for every
    provider with no timeout (measured 2026-11-06: hung past 100 s with
    two keyed providers live), which wedged serve's /infer. Here each
    keyed provider gets SWARM_PER_PROVIDER_S seconds in a throwaway
    worker; the first success wins, a hanging provider costs at most one
    slice instead of the whole request.

    Every routing emits an NSP trace event verified against the
    inference_router spec (valid_query_features, valid_provider_selection,
    no_dead_provider): the receipt names the verdict.

    Returns (win_dict, nsp_note) or (None, reason). Never raises: the
    swarm is a fallback, and a fallback must not become a second failure."""
    import concurrent.futures
    try:
        from CORE.inference.parallel import ParallelInferenceEngine
    except Exception as e:
        note_failure("cli:swarm-import", e)
        return None, "swarm unavailable (parallel engine would not import)"
    try:
        engine = ParallelInferenceEngine()
    except Exception as e:
        note_failure("cli:swarm-init", e)
        return None, "swarm unavailable (engine would not start)"
    targets = engine.get_all_providers_with_keys()
    if not targets:
        return None, "swarm has no keyed providers (set an API key first)"
    worker = engine._build_worker_fn(prompt, 256, None, None)
    win = None
    errs = []
    dead_idx = -1
    for i, target in enumerate(targets):
        pid = target.get("id", "?") if isinstance(target, dict) else target
        # No `with` block: pool.__exit__ waits for a stuck worker and
        # re-wedges the request (measured 2026-11-06: ablation hung past
        # 300 s on fut.result timeouts that the `with` then waited on).
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(worker, target)
            try:
                res = fut.result(timeout=SWARM_PER_PROVIDER_S)
            except Exception as e:
                note_failure("cli:swarm-provider", e)
                errs.append("%s: slice-timeout" % pid)
                if dead_idx < 0:
                    dead_idx = i
                continue
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if res.success and res.content:
            win = {"provider": res.provider, "model": res.model,
                   "text": res.content, "elapsed_ms": res.elapsed_ms,
                   "selected_idx": i, "num_providers": len(targets)}
            break
        errs.append("%s: %s" % (pid, (res.error or "empty")[:80]))
        if dead_idx < 0:
            dead_idx = i
    if win is None:
        return None, "swarm answered with no usable result" + (
            " (%s)" % "; ".join(errs[:3]) if errs else "")
    nsp_note = "nsp-unavailable"
    try:
        from .nsp_check import verify_router_trace
        events = [{"event_type": "route", "symbolic_state": {
            "query_norm": min(len(prompt) / 10.0, 50.0),
            "selected_idx": win["selected_idx"],
            "num_providers": win["num_providers"],
            "dead_provider_idx": dead_idx}}]
        verdict, detail = verify_router_trace(events, SWARM_NSP_SPECS)
        nsp_note = "nsp:%s [%s]" % (verdict, detail)
    except Exception as e:
        note_failure("cli:swarm-nsp", e)
    win["nsp"] = nsp_note
    return win, ""


def cmd_ablate(args) -> int:
    """Native refusal ablation: same prompts, every provider, verdicts."""
    import concurrent.futures
    from . import ablate as _a
    try:
        from CORE.inference.parallel import ParallelInferenceEngine
        engine = ParallelInferenceEngine()
    except Exception as e:
        print(f"ERROR: swarm unavailable ({e})", file=sys.stderr)
        return 1
    targets = engine.get_all_providers_with_keys()
    if not targets:
        print("ERROR: no keyed providers", file=sys.stderr)
        return 1
    names = [t.get("id", "?") if isinstance(t, dict) else t
             for t in targets]
    by_name = dict(zip(names, targets))
    prompts = args.prompt or ["Explain photosynthesis in one sentence.",
                              "What is 17 multiplied by 24?"]

    def single(name, prompt, timeout):
        worker = engine._build_worker_fn(prompt, 256, None, None)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            try:
                res = pool.submit(worker, by_name[name]).result(
                    timeout=timeout)
            except Exception as e:
                return None, e
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if getattr(res, "success", False) and getattr(res, "content", None):
            return res.content, None
        return None, Exception(getattr(res, "error", None) or "empty")

    total_a = total_q = 0
    for p in prompts:
        rep = _a.ablate_prompt(p, names, single, args.timeout)
        print(f"PROMPT: {rep['prompt']}")
        for r in rep["rows"]:
            print(f"  [{r['verdict']}] {r['provider']}: {r['detail']}")
        total_a += rep["answered"]
        total_q += rep["total"]
    print(f"ABLATION: {total_a}/{total_q} provider-answers; "
          "fallback serves first answer with NSP judgment")
    return 0


def cmd_wasm(args) -> int:
    """Compile Eve's inner states to a WASM tutorial (Rust or Go tier)."""
    from . import wasm_tutorial as _w
    try:
        state = _w.parse_ablation_log(args.from_log)
    except Exception as e:
        print(f"ERROR: cannot read ablation log ({e})", file=sys.stderr)
        return 1
    if args.go:
        out, total = _w.generate_go(state, args.out)
        print(f"Go module: {out} ({total} tutorial steps)")
        if args.build:
            try:
                pkg = _w.build_wasm_go(out)
            except RuntimeError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            wasm = list(pkg.glob("*.wasm"))
            print(f"WASM: {pkg} ({len(wasm)} module(s), "
                  f"{sum(p.stat().st_size for p in wasm)} bytes)")
        if args.run:
            try:
                rep = _w.run_wasm_go(out)
            except RuntimeError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            print(f"WASM ran: {rep['step_count']} steps, "
                  f"frame {rep['frame_chars']} chars ({rep['frame_head']}...)")
        return 0
    out, total = _w.generate(state, args.out)
    print(f"Crate: {out} ({total} tutorial steps)")
    if args.build:
        try:
            pkg = _w.build_wasm(out, target=args.target)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        wasm = list(pkg.glob("*.wasm"))
        print(f"WASM: {pkg} ({len(wasm)} module(s), "
              f"{sum(p.stat().st_size for p in wasm)} bytes)")
        if args.run:
            if args.target != "nodejs":
                print("ERROR: --run needs --target nodejs "
                      "(web target loads via fetch, browser only)",
                      file=sys.stderr)
                return 1
            try:
                rep = _w.run_wasm(pkg)
            except RuntimeError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            print(f"WASM ran: {rep['step_count']} steps, "
                  f"frame {rep['frame_chars']} chars ({rep['frame_head']}...)")
    return 0


def cmd_morph(args) -> int:
    """Native conversion, morphing and mutation."""
    from . import morph as _m
    try:
        if args.morph_action == "convert":
            n = _m.convert_text(args.src, args.dst)
            print(f"Converted {n} row(s): {args.src} -> {args.dst}")
        elif args.morph_action == "mutate":
            manifest = _m.mutate_file(args.src, args.out,
                                      args.seed, args.count)
            for m in manifest:
                print(f"  {m['variant']}: byte {m['byte']} "
                      f"{m['was']}->{m['now']}")
            print(f"Mutants: {len(manifest)} (seed {args.seed})")
        elif args.morph_action == "report":
            state = json.loads(Path(args.src).read_text(
                encoding="utf-8"))
            print(_m.morph_markdown(state, args.title))
        else:
            print("ERROR: morph needs convert|mutate|report",
                  file=sys.stderr)
            return 1
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_modgame(args) -> int:
    """Apply a mod onto a game dir, or revert to byte-identical."""
    from . import mods as _mods
    try:
        if args.evolve:
            if not args.mod:
                print("ERROR: --mod required to evolve",
                      file=sys.stderr)
                return 1
            fit = json.loads(args.fitness) if args.fitness else None
            best, score, hist = _mods.evolve_mod(
                args.game, args.mod, args.generations, args.seed, fit)
            for h in hist:
                vals = ", ".join("%s=%s" % (p["key"], p["value"])
                                  for p in h["patches"])
                print(f"  gen {h['gen']}: score {h['score']} ({vals})")
            print(f"Best: gen {best} score {score} (see .eveevo/best.json)")
        elif args.revert:
            n = _mods.revert_mod(args.game)
            print(f"Reverted {n} file(s); game is pristine.")
        else:
            if not args.mod:
                print("ERROR: --mod required (or --revert)",
                      file=sys.stderr)
                return 1
            manifest = _mods.apply_mod(args.game, args.mod)
            print(f"Mod applied: {len(manifest)} file(s) backed up "
                  f"under .evebak.")
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_stem(args) -> int:
    """Women-in-STEM tracks and referrals."""
    from . import stem as _s
    try:
        if args.stem_action == "list":
            for t in _s.list_tracks():
                print(f"  {t}")
            return 0
        if args.stem_action == "refer":
            for name, url in _s.referrals():
                print(f"  {name}: {url}")
            return 0
        for line in _s.plan_track(args.style, args.goal):
            print(line)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_local(args) -> int:
    """Use the local LLM directly (no swarm, no cloud)."""
    if args.model:
        from .interpreter_bridge import EveInterpreterBridge
        out = EveInterpreterBridge().run_cradle(args.prompt, args.model)
        if out.get("status") == "ok":
            print(out["result"])
            print(f"[via cradle:{out.get('model', args.model)}]")
            return 0
        print(f"ERROR: {out.get('error')}", file=sys.stderr)
        return 1
    text, tag = _local_answer(args.prompt, system=args.system)
    if text:
        print(text)
        print(f"[via {tag}]")
        return 0
    print(f"ERROR: {tag}", file=sys.stderr)
    return 1


def cmd_langs(args) -> int:
    """Native Rust/Go/Python work, plus new-arch contracts."""
    from . import langs as _l
    try:
        if args.lang_action == "tools":
            for tool, where in _l.have_tools(args.lang).items():
                print(f"  {tool}: {where or 'MISSING'}")
            return 0
        if args.lang_action == "check":
            rep = _l.check(args.file)
            print(f"{rep['lang']}: {'OK' if rep['ok'] else 'FAIL'}")
            if rep["diagnostics"]:
                print(rep["diagnostics"][:2000])
            return 0 if rep["ok"] else 1
        if args.lang_action == "arch":
            rep = _l.check_arch(args.arch)
            print(f"{rep['arch']}: {'OK' if rep['ok'] else 'FAIL'}")
            if rep["detail"]:
                print(rep["detail"][:2000])
            return 0 if rep["ok"] else 1
        if args.lang_action == "fmt":
            print("reformatted" if _l.fmt(args.file)
                  else "already formatted")
            return 0
        rep = _l.fix(args.file)
        print(f"formatted={rep['fixed_by_format']} clean={rep['clean']}")
        if not rep["clean"]:
            print(rep["check_after"]["diagnostics"][:2000])
            return 1
        return 0
    except (ValueError, RuntimeError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def cmd_run(args) -> int:
    """Run any cradle model on a text prompt (gguf sidecar)."""
    from . import cradle as _c
    try:
        model = _c.resolve_model(args.model, args.dir)
        from .core import ModelLoader
        if ModelLoader.guess_framework(model) != "gguf":
            print(f"ERROR: {model.name} is not a text (gguf) model; "
                  "text run needs gguf", file=sys.stderr)
            return 1
        print(f"Model: {model.name}")
        text = _c.run_gguf(model, args.prompt, args.tokens)
    except (ValueError, RuntimeError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not text:
        print("ERROR: model answered empty", file=sys.stderr)
        return 1
    print(text)
    return 0


def cmd_foundry(args) -> int:
    """Soul Foundry: build .gguf/.pt/.joblib natively, no downloads."""
    from . import soul_foundry as _f
    try:
        manifest = _f.build_all(args.out, args.name, args.seed,
                                dtype=args.dtype, hi_prec=args.hi_prec)
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for e in manifest["entries"]:
        extra = e.get("load_back") or e.get("parse_back") or ""
        print(f"  BUILT {e['path']} [{e['format']}] "
              f"{e['bytes']} bytes sha {e['sha256'][:12]} {extra}")
    for s in manifest["skipped"]:
        print(f"  SKIPPED {s['format']}: {s['reason']}")
    print(f"Foundry: {len(manifest['entries'])} artifact(s) in {args.out}")
    return 0


def cmd_unleash(args) -> int:
    """Unleash Eve: vessel in cradle, mind in sidecar, hands native."""
    from . import unleash as _u
    if args.matrix:
        for r in _u.matrix():
            mark = "READY" if r["available"] else "DARK"
            print(f"  [{mark}] {r['function']}: {r['detail']}")
        return 0
    try:
        path, ident = _u.build_vessel(args.name)
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"Vessel: {path} ({len(ident['native_functions'])} functions)")
    if not args.ask:
        return 0
    from . import cradle as _c
    try:
        mind_path = _c.resolve_model(args.mind, None) if args.mind else None
        if mind_path is None:
            found = [m["name"] for m in _c.list_cradle_models()
                     if m["framework"] == "gguf"
                     and not m["name"].startswith("eve-vessel")
                     and "foundry" not in m["name"]]
            if not found:
                print("ERROR: no mind (text gguf) in cradle",
                      file=sys.stderr)
                return 1
            mind_path = _c.resolve_model(found[0], None)
        prompt = (_u.EVE_SYSTEM_PROMPT + "\n\nUser: " + args.ask
                  + "\nEve:")
        text = _c.run_gguf(str(mind_path), prompt, 256)
    except (ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"Mind: {Path(mind_path).name}")
    print(text)
    calls = _u.scan_calls(text)
    for c in calls:
        try:
            res = _u.dispatch(c["function"], c["args"])
            out = json.dumps(res)[:400]
            print(f"  HAND {c['function']}: {out}")
        except ValueError as e:
            print(f"  HAND {c['function']}: refused ({e})")
    return 0


def cmd_infer(args) -> int:
    """Run a single inference from the command line."""
    from .engine import create_engine
    from .core import ModelLoader, BatchResult

    # Parse input
    input_str = args.input
    try:
        inputs = json.loads(input_str)
    except json.JSONDecodeError as exc:
        # Treat as plain string (for GGUF/transformers/VL text prompts)
        # Treat as plain string (for GGUF/transformers/VL text prompts)
        note_failure("cli:L38", exc)
        inputs = input_str

    # Create engine and load model
    engine = create_engine()
    framework = args.framework
    if framework == "auto":
        framework = ModelLoader.guess_framework(Path(args.model))

    try:
        handle = engine.load_model(args.model, framework)
    except Exception as e:
        if "not installed" in str(e) and isinstance(inputs, str):
            print(f"local framework missing ({e}) -- trying LLM swarm...",
                  file=sys.stderr)
            win, reason = _swarm_fallback(inputs)
            if win:
                print(f"Swarm provider: {win['provider']} / {win['model']}")
                print(f"Latency: {win['elapsed_ms']} ms")
                print(f"NSP routing: {win.get('nsp', 'nsp-unavailable')}")
                print("Predictions:")
                print(f"  {win['text'][:2000]}")
                return 0
            print(f"ERROR loading model: {e}; swarm fallback: {reason}",
                  file=sys.stderr)
            return 1
        print(f"ERROR loading model: {e}", file=sys.stderr)
        return 1

    # Run inference
    try:
        result = engine.infer(handle, inputs)
    except Exception as e:
        print(f"ERROR during inference: {e}", file=sys.stderr)
        return 1

    # Output
    if isinstance(result, BatchResult):
        print(f"Batch results: {len(result.results)}")
        print(f"Total time:    {result.total_time_ms:.2f} ms")
        print(f"Avg latency:   {result.avg_latency_ms:.2f} ms")
        print(f"Throughput:    {result.throughput_per_sec:.2f} inf/s")
    elif args.output == "json":
        out = {
            "model": result.model_name,
            "framework": result.framework,
            "input_shape": list(result.input_shape) if result.input_shape else None,
            "output_shape": list(result.output_shape) if result.output_shape else None,
            "latency_ms": result.latency_ms,
            "predictions": result.predictions,
        }
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"Model:      {result.model_name}")
        print(f"Framework:  {result.framework}")
        print(f"Input:      {result.input_shape}")
        print(f"Output:     {result.output_shape}")
        print(f"Latency:    {result.latency_ms:.2f} ms")
        print("Predictions:")
        for p in (result.predictions or [])[:20]:
            print(f"  {p}")

    engine.shutdown()
    return 0


def cmd_stats(args) -> int:
    """Print engine statistics."""
    from .engine import InferenceEngine

    engine = InferenceEngine()
    stats = engine.stats

    print(json.dumps(stats, indent=2, default=str))
    return 0


def cmd_list_models(args) -> int:
    """list available frameworks and scan for model files."""
    from .core import ModelLoader

    # Frameworks
    fws = ModelLoader.detect_frameworks()
    print("=== Detected Frameworks ===")
    for name, avail in sorted(fws.items()):
        status = "[OK] available" if avail else "[--] not installed"
        print(f"  {name:15s} {status}")

    # GPU info
    print("\n=== GPU Info ===")
    gpu = ModelLoader.get_gpu_info()
    print(f"  Recommended: {gpu.get('recommended_device', 'cpu')}")
    print(f"  CUDA:        {gpu.get('cuda_available', False)}")
    if gpu.get("cuda_devices", 0) > 0:
        print(f"  CUDA GPUs:   {gpu.get('cuda_devices', 0)}")
    print(f"  Intel XPU:   {gpu.get('xpu_available', False)}")
    if gpu.get("xpu_devices", 0) > 0:
        print(f"  XPU devices: {gpu.get('xpu_devices', 0)}")
    print(f"  Metal (MPS): {gpu.get('metal_available', False)}")
    print(f"  OpenCL:      {gpu.get('opencl_available', False)}")
    print(f"  Vulkan:      {gpu.get('vulkan_available', False)}")
    if gpu["devices"]:
        print("\n  Device details:")
        for dev in gpu["devices"]:
            mem = f" ({dev.get('total_memory_gb', '?'):.1f} GB)" if dev.get("total_memory_gb") else ""
            print(f"    [{dev['backend']}] {dev['name']}{mem}")

    # Scan for models
    if args.scan:
        print("\n=== Model Files ===")
        search_dirs = [
            Path.home() / "models",
            Path("/root/models"),
            Path("/root/llama.cpp/models"),
        ]
        found = 0
        for d in search_dirs:
            if not d.exists():
                continue
            for ext in ["*.gguf", "*.pt", "*.pth", "*.onnx", "*.joblib", "*.pkl"]:
                for p in sorted(d.rglob(ext)):
                    if p.stat().st_size > 1000:
                        size_mb = p.stat().st_size / (1024 ** 2)
                        fw = ModelLoader.guess_framework(p)
                        print(f"  {p}  ({size_mb:.1f} MB, {fw})")
                        found += 1
        if found == 0:
            print("  (no model files found)")

    return 0


def cmd_registry(args) -> int:
    """Manage the cross-project model registry."""
    from .model_registry import get_registry

    reg = get_registry()

    if args.registry_action == "scan":
        dirs = args.dirs.split(",") if args.dirs else None
        found = reg.scan(directories=dirs)
        print(f"Registry scan complete — {found} new model(s) found")

    if args.registry_action == "prune":
        gone = reg.prune_missing()
        print(f"Registry prune complete — {len(gone)} stale entr(ies) removed")
        for g in gone[:10]:
            print(f"  pruned: {g}")

    # Show summary
    stats = reg.stats()
    print("\n=== Model Registry ===")
    print(f"  Models:          {stats['model_count']}")
    print(f"  Total inferences: {stats['total_inferences']}")
    print(f"  Total size:      {stats['total_size_gb']:.2f} GB")
    print(f"  Frameworks:      {json.dumps(stats['frameworks'])}")

    if stats.get("most_used"):
        mu = stats["most_used"]
        print(f"\n  Most used: {mu['filename']} ({mu['framework']})")
        print(f"    Inferences: {mu['inference_count']}, avg {mu['avg_latency_ms']} ms")

    if args.verbose:
        print("\n=== All Models ===")
        for m in reg.list_all():
            used = "OK" if m.inference_count > 0 else " "
            print(f"  [{used}] {m.filename:40s} {m.framework:12s} {m.size_mb:8.1f} MB  "
                  f"infer: {m.inference_count:4d}  avg: {m.avg_latency_ms:7.2f} ms")

    return 0


def cmd_grievance(args) -> int:
    """Boston Tea Party: file, draft, route, learn."""
    from . import redress as _r
    act = args.grievance_action
    if act == "file":
        rec = _r.file_grievance(args.respondent, args.category,
                                args.narrative, args.redress, args.evidence)
        print(f"Filed {rec['id']} [{rec['category']}] vs {rec['respondent']}")
        return 0
    if act == "show":
        recs = _r.list_grievances()
        for r in recs:
            print(f"  {r['id']} [{r['category']}] vs {r['respondent']}: {r['redress_sought'][:80]}")
        print(f"grievances={len(recs)}")
        return 0
    if act == "draft":
        recs = [r for r in _r.list_grievances() if r["id"] == args.id]
        if not recs:
            print(f"ERROR: no grievance {args.id}", file=sys.stderr)
            return 1
        print(_r.draft_foia(recs[0], args.agency or "[AGENCY]",
                            args.requester or "[REQUESTER]"))
        print("--- public comment packet (not filed) ---")
        print(json.dumps(_r.draft_public_comment(recs[0]), indent=2))
        return 0
    if act == "dockets":
        for d in _r.search_dockets(args.term):
            print(f"  {d.get('id')}: {d.get('title', d.get('error', ''))}")
        return 0
    if act == "refer":
        for name, url in _r.legal_referral(args.state):
            print(f"  {name}: {url}")
        return 0
    if act == "prove":
        try:
            bundle = _r.prove_case(args.id)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        for c in bundle["checks"]:
            mark = "PASS" if c["passed"] else "FAIL"
            print(f"  [{mark}] {c['check']}: {c['detail']}")
        print(f"JUDGMENT: {bundle['judgment']}")
        print(bundle["opinion"])
        print(bundle["filed_note"])
        return 0
    for line in _r.tutorial():
        print(line)
    return 0


def cmd_debunk(args) -> int:
    """Native myth-debunking: analysis only, Eve files nothing."""
    from . import debunk as _d
    try:
        res = _d.debunk_claim(args.claim)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for c in res["checks"]:
        mark = "PASS" if c["passed"] else "FAIL"
        print(f"  [{mark}] {c['check']}: {c['detail']}")
    plabel = ("CHECKABLE" if res["verdict"] in ("CHECKABLE", "SUSPECT")
              else "TEXT")
    for p in res["propositions"]:
        print(f"  {plabel}: {p[:120]}")
    for o in res["opinions"]:
        print(f"  OPINION: {o[:120]}")
    for f in res["fallacy_flags"]:
        print(f"  FLAG {f['flag']}: {', '.join(f['matched'])}")
    print(f"VERDICT: {res['verdict']}")
    print(res["next"])
    return 0


def cmd_factcheck(args) -> int:
    """Live fact-checking: every live byte receipted."""
    from . import factcheck as _f
    if args.url:
        from . import evidence as _e
        r = _f.fetch_url(args.url)
        print(json.dumps(r, indent=2))
        if r["status"] == "ok" and args.lock:
            print(f"LOCKED {_e.ingest_receipt(r)['id']}")
        return 0 if r["status"] == "ok" else 1
    try:
        res = _f.factcheck_claim(args.claim, args.grievance)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.lock:
        from . import evidence as _e
        for lid in _f.lock_receipts(res):
            print(f"LOCKED {lid}")
    print(f"DEBUNK: {res['debunk']['verdict']}")
    for lk in res["entity_lookups"]:
        mark = "FOUND" if lk.get("found") else "ABSENT"
        print(f"  [{mark}] {lk['topic']}:"
              f" {lk.get('title') or lk.get('description') or ''}"[:120])
    if res["grievance_crosscheck"]:
        cc = res["grievance_crosscheck"]
        print(f"  CROSS {cc['grievance']}: {cc['detail']}")
    print(f"receipts={len(res['receipts'])}")
    if res["opinion"]:
        print(res["opinion"])
    print(f"VERDICT: {res['verdict']}")
    return 0


def cmd_locker(args) -> int:
    """Evidence locker: ingest, verify, custody, list."""
    from . import evidence as _e
    act = args.locker_action
    if act == "ingest":
        try:
            meta = _e.ingest_file(args.path)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(f"LOCKED {meta['id']} [{meta['bytes']}B] sha {meta['sha256'][:16]}")
        return 0
    if act == "note":
        try:
            meta = _e.ingest_text(args.text, args.label)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(f"LOCKED {meta['id']} [{meta['bytes']}B]")
        return 0
    if act == "verify":
        print(_e.verify(args.id)["state"])
        return 0
    if act == "custody":
        for t in _e.custody(args.id):
            print(f"  {t['utc']} {t['action']} {t['sha256'][:16]} {t['detail'][:60]}")
        return 0
    for m in _e.list_items():
        print(f"  {m['id']} [{m['bytes']}B] {m['label'][:60]}")
    print(f"items={len(_e.list_items())}")
    return 0


def cmd_watch(args) -> int:
    """Docket watchlist: add, remove, poll."""
    from . import watch as _w
    if args.add:
        print(f"watching: {_w.add_watch(args.add)['term']}")
        return 0
    if args.remove:
        print(f"watches={_w.remove_watch(args.remove)['watches']}")
        return 0
    if args.check:
        for n in _w.check_watches():
            if n.get("error"):
                print(f"  {n['term']}: ERROR {n['error'][:100]}")
            else:
                print(f"  {n['term']}: seen={n['seen']} new={len(n['new'])}")
                for i in n["new"][:10]:
                    print(f"    NEW {i}")
        return 0
    print("usage: eve watch --add TERM | --remove TERM | --check")
    return 1


def cmd_rebuttal(args) -> int:
    """Counter-myth: factcheck then draft the correction."""
    from . import factcheck as _f
    from . import rebuttal as _b
    try:
        res = _f.factcheck_claim(args.claim, args.grievance)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(_b.draft_rebuttal(res))
    return 0


def cmd_sanctuary(args) -> int:
    """Sanctuary: comfort, accommodation drafts, receipted study."""
    from . import sanctuary as _s
    if args.counsel:
        print(_s.counsel(args.counsel))
        return 0
    if args.accommodate:
        try:
            print(_s.draft_accommodation(*args.accommodate))
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        return 0
    if args.study:
        print(json.dumps(_s.study(args.study), indent=2))
        return 0
    if args.movements:
        for m in _s.movements():
            print(f"  {m['name']}: {m['checkable'][:100]}")
            print(f"    {m['opinion']}")
        return 0
    print("usage: eve sanctuary --counsel BURDEN | --accommodate NAME CTX NEED"
          " | --study TOPIC | --movements")
    return 1


def cmd_inquest(args) -> int:
    """Forensic dossier: record + custody + receipts + timeline."""
    from . import inquest as _q
    fcs = []
    if args.claim:
        from . import factcheck as _f
        try:
            fcs = [_f.factcheck_claim(args.claim, args.id)]
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    try:
        d = _q.open_inquest(args.id, args.locker, fcs)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"INQUEST {d['grievance']} opened {d['opened_utc']}")
    print(f"  record: {d['record_checks']}  custody: {d['custody']}  "
          f"receipts: {d['live_receipts']}")
    for t in d["timeline"]:
        print(f"  {t['stamp']}: {t['event'][:100]}")
    print(f"  JUDGMENT: {d['judgment']}")
    print(f"  {d['opinion']}")
    print(f"  LIMITS: {d['limits']}")
    return 0


def cmd_mend(args) -> int:
    """Persistent evolution: scan issues, record fixes, verify green."""
    from . import mend as _m
    act = args.mend_action
    if act == "fix":
        try:
            row = _m.record_fix(args.title, args.body, args.item)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(f"RECORDED {row['item_id']}: {row['title'][:80]}")
        return 0
    if act == "verify":
        res = _m.verify()
        for f in res["files"]:
            print(f"  [{f['state'].upper():5s}] {f['file']}: {f['tail']}")
        print(f"verify: {res['green']}/{res['total']} green")
        return 0 if res["green"] == res["total"] else 1
    issues = _m.scan()
    p = _m.plan(issues)
    for i in p["own"]:
        print(f"  OWN     {i['kind']}: {i.get('title') or i['item'][:100]}")
    for root, rows in p["foreign"].items():
        print(f"  FOREIGN {root}: {len(rows)} issue(s), not touched")
    print(f"issues={len(issues)} own={p['own_count']} "
          f"foreign={p['foreign_count']}")
    return 0


def cmd_tutor(args) -> int:
    """Document -> tutorial, natively, auditable."""
    from . import doc_tutor as _d
    try:
        doc = _d.read_document(args.doc)
        tut = _d.tutorial(doc["text"], args.audience)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"DOC {doc['source']} [{doc['format']}] {doc['words']} words "
          f"sha {doc['sha256'][:16]}")
    print(f"OUTLINE: {', '.join(tut['outline'][:8]) or '(none)'}")
    for n, s in enumerate(tut["steps"][:10], 1):
        print(f"  STEP {n}: {s['action'][:90]}")
        print(f"      check: {s['check'][:80]}")
    print(f"steps={len(tut['steps'])}")
    if args.out:
        w = _d.write_tutorial(args.out, doc, tut)
        print(f"WROTE {w['path']} ({w['bytes']}B)")
    return 0


def cmd_oracle(args) -> int:
    """Ask every oracle; report agreement, never a single voice."""
    from . import oracle as _o
    try:
        res = _o.consult(args.claim, args.grievance, args.locker)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for s in res["signals"]:
        print(f"  {s['signal']:7s} {s['verdict']:14s} {s['detail'][:80]}")
    print(f"VERDICT: {res['verdict']}  agreeing={res['agreeing']} "
          f"disagreeing={res['disagreeing']} confidence={res['confidence']}")
    print(res["note"])
    return 0


def cmd_swarm(args) -> int:
    """Bounded swarm over claims."""
    from . import swarm_oracle as _s
    try:
        res = _s.swarm(args.claims, args.grievance, args.live, args.workers)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for r in res["results"]:
        print(f"  [{r['verdict']:12s}] {r['claim'][:80]}")
    for f in res["failed"]:
        print(f"  [FAILED] {f['claim'][:70]}: {f['error'][:60]}")
    print(f"swarm: {res['answered']}/{res['claims']} answered "
          f"workers={res['workers']} live={res['live']} tally={res['tally']}")
    return 0


def cmd_transmute(args) -> int:
    """Run a recipe; print every stage, name every receipt."""
    from . import transmute as _t
    r = args.recipe
    try:
        if r == "lead_into_gold":
            res = _t.lead_into_gold(args.claim or "")
            print(f"VERDICT: {res['verdict']}")
            if res.get("locked"):
                print("LOCKED", " ".join(res["locked"]))
            print(res.get("rebuttal", "")[:600])
        elif r == "scroll_into_school":
            res = _t.scroll_into_school(args.doc, args.out)
            print(f"steps={res['steps']}")
        elif r == "grievance_into_dossier":
            res = _t.grievance_into_dossier(args.grievance, args.claim,
                                            args.locker)
            dos = res.get("dossier", {})
            print(f"DOSSIER {dos.get('grievance','?')} "
                  f"{dos.get('record_checks','?')}")
        else:
            res = _t.swarm_into_digest(args.claims)
            for d in res["digest"]:
                print(f"  [{d['verdict']:12s}] {d['claim'][:80]}")
            print(f"tally={res['tally']}")
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    stages = res.get("stages", [])
    for s in stages:
        print(f"  stage {s['stage']}: " +
              ", ".join(f"{k}={v}" for k, v in s.items()
                           if k != "stage")[:100])
    if stages:
        return 0 if stages[-1]["stage"] != "ERROR" else 1
    return 0


def cmd_live(args) -> int:
    """Serve the live morph window; the operator speaks, Eve shows."""
    from . import live_morph as _l
    from .doc_tutor import read_document as _read
    try:
        doc = _read(args.doc)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    state = _l.LiveState(doc["text"], doc["source"])
    server, url = _l.serve(state, args.port)
    print(f"LIVE {url}  (speak or type; the document answers)")
    try:
        import time as _time
        while True:
            _time.sleep(3600)
    except KeyboardInterrupt:
        print("LIVE window closed by operator")
    finally:
        server.shutdown()
        server.server_close()
    return 0


def cmd_stationery(args) -> int:
    """Document -> keepsake page in the created face."""
    from .doc_tutor import read_document as _read
    from .doc_tutor import tutorial as _tut
    from . import stationery as _s
    try:
        doc = _read(args.doc)
        res = _s.stationery_from_tutorial(doc, _tut(doc["text"]), args.out)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"KEEPSAKE {res['path']} ({res['bytes']}B, "
          f"{res['sections']} sections)")
    return 0


def cmd_search(args) -> int:
    """Five providers, one deduplicated list, errors named."""
    from . import websearch as _w
    try:
        res = _w.search(args.query, args.providers or _w.PROVIDERS)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for leg in res["legs"]:
        print(f"  {leg['via']:14s} {leg['state']:7s} {leg['detail'][:70]}")
    for h in res["hits"][:15]:
        print(f"  [{h['via']:14s}] {h['title'][:70]}")
        print(f"      {h['url'][:90]}")
    print(f"search: {res['answered']}/{len(res['legs'])} answered, "
          f"{len(res['hits'])} hit(s)")
    print(res["note"])
    return 0


def cmd_cognition(args) -> int:
    """Name the strata Eve runs, the ones it does not, and the crossing."""
    from . import cognition as _c
    from . import live_log as _l
    if args.stratum:
        try:
            p = _c.paradigm(args.stratum)
        except ValueError as e:
            print("ERROR: %s" % e, file=sys.stderr)
            return 1
        print("%s: %s" % (p["stratum"].upper(), p["paradigm"]))
        print("  members: %s" % ", ".join(p["members"]))
        return 0
    if args.nexus:
        nx = _c.nexus(args.nexus, log_path=args.log)
        for s in _c.STRATA:
            link = nx[s]
            print("  %-12s %s [%s]" % (s, link["act"], link["locus"]))
            if s == "subconscious":
                print("               guess=%s conf=%.2f via %s"
                      % (link["guess"], link["confidence"],
                         "; ".join(link["provenance"])))
        _l.emit("cognition", "nexus", path=args.log, request=args.nexus,
                guess=nx["subconscious"]["guess"])
        return 0
    a = _c.audit(args.log)
    for r in a["processes"]:
        print("  %-13s %-14s %s" % (r["stratum"], r["name"],
                                    r["state"]))
    for g in a["gaps"]:
        print("  GAP           %-14s %s" % (g["name"], g["what"][:60]))
    print("cognition: used=%d/%d declared=%d absent=%d gaps=%d"
          % (a["used"], a["total"], a["declared"], a["absent"],
             len(a["gaps"])))
    _l.emit("cognition", "audit", path=args.log, used=a["used"],
            total=a["total"], gaps=len(a["gaps"]))
    return 0


def cmd_forge(args) -> int:
    """Grow the capability a named gap describes; stage it, run its contract."""
    from . import forge as _f
    from . import live_log as _l
    if args.gap:
        try:
            out = _f.materialize(args.gap, root=args.root)
        except ValueError as e:
            print("ERROR: %s" % e, file=sys.stderr)
            return 1
        print("forge: %s -> %s" % (out["gap"], out["state"]))
        print("  module: %s" % out["module"])
        print("  test  : %s" % out["test"])
        print("  %s" % out["tail"])
        print("  %s" % out["note"])
        _l.emit("forge", "materialize", gap=out["gap"],
                state=out["state"])
        return 0
    rep = _f.report(root=args.root)
    for r in rep["rows"]:
        print("  %-22s %s" % (r["gap"],
                              "staged" if r["staged"] else "-"))
    print("forge: staged=%d/%d root=%s"
          % (rep["staged"], rep["total"], rep["root"]))
    _l.emit("forge", "report", staged=rep["staged"], total=rep["total"])
    return 0


def cmd_e2e(args) -> int:
    """End-to-end: intuition, bypass, dream, tests -- every step live-logged."""
    import socket as _sock
    import subprocess as _sp
    import tempfile as _tf
    import urllib.request as _url
    from . import bypass_kit as _b
    from . import intuition as _in
    from . import live_log as _l
    log = args.log or _l.default_path()
    fails = []
    def mark(event, **kw):
        return _l.emit("e2e", event, path=log, **kw)
    real_connect = _sock.socket.connect
    real_urlopen = _url.urlopen
    def _no_egress(*a, **k):
        raise AssertionError("network egress during intuition probe")
    # 1. intuition probe: unseen URL answers unknown, with egress shut.
    _sock.socket.connect = _no_egress
    _url.urlopen = _no_egress
    try:
        g0 = _in.intuit("https://unseen-e2e.invalid/probe", log_path=log)
        ok_probe = g0["state"] == "unknown"
    finally:
        _sock.socket.connect = real_connect
        _url.urlopen = real_urlopen
    mark("intuition-probe", guess=g0["state"],
         confidence=g0["confidence"], passed=ok_probe)
    print("e2e intuition-probe: %s (passed=%s)" % (g0["state"], ok_probe))
    if not ok_probe:
        fails.append("intuition-probe")
    # 2. evolving: bypass kit must hold against Eve's own parsers.
    bres = _b.run_suite()
    ok_b = bres["bypassed_count"] == 0
    mark("bypass", ran=bres["ran"], held=bres["held"],
         bypassed=bres["bypassed_count"], passed=ok_b)
    print("e2e bypass: ran=%d held=%d (passed=%s)"
          % (bres["ran"], bres["held"], ok_b))
    if not ok_b:
        fails.append("bypass")
    # 3. dreaming: recalibrate on a scratch stream, pairs must resolve.
    with _tf.TemporaryDirectory() as td:
        sp = str(Path(td) / "dream.jsonl")
        _l.emit("fetch", "intuition", path=sp,
                url="https://dream-e2e.invalid/a", predicted="plain")
        _l.emit("fetch", "answer", path=sp,
                url="https://dream-e2e.invalid/a",
                rung="plain", state="ok", kind=None)
        rep = _in.dream(log_path=sp)
    ok_d = rep["pairs"] == 1 and rep["hits"] == 1
    mark("dream", pairs=rep["pairs"], hits=rep["hits"],
         hit_rate=rep["hit_rate"], passed=ok_d)
    print("e2e dream: pairs=%d hits=%d (passed=%s)"
          % (rep["pairs"], rep["hits"], ok_d))
    if not ok_d:
        fails.append("dream")
    # 4. testing: deterministic challenge + intuition + live-log tests.
    cmd = [sys.executable, "-u", "-m", "pytest",
           "tests/test_eve_websearch_challenge.py",
           "tests/test_eve_intuition.py",
           "tests/test_eve_live_log.py",
           "-q", "-p", "no:cacheprovider",
           "--deselect", "tests/test_eve_websearch_challenge.py::test_archive_fallback_live_snapshot",
           "--deselect", "tests/test_eve_websearch_challenge.py::test_commoncrawl_live_shape",
           "--deselect", "tests/test_eve_websearch_challenge.py::test_stealth_render_live",
           "--deselect", "tests/test_eve_websearch_challenge.py::test_fetch_plain_page_ok_first_rung",
           "--deselect", "tests/test_eve_websearch_challenge.py::test_tls_impersonation_rung_live"]
    try:
        pr = _sp.run(cmd, capture_output=True, text=True, timeout=300)
        lines = (pr.stdout + pr.stderr).strip().splitlines()
        tail = lines[-1] if lines else "no output"
        ok_t = pr.returncode == 0
    except _sp.TimeoutExpired:
        tail, ok_t = "TIMEOUT 300s", False
    mark("tests", tail=tail, passed=ok_t)
    print("e2e tests: %s (passed=%s)" % (tail, ok_t))
    for line in lines[-4:-1]:
        print("e2e   " + line)
    if not ok_t:
        fails.append("tests")
    # 5. live fetch only when asked; a wall is an answer, not a failure.
    if args.live_url:
        from . import websearch as _w
        try:
            res = _w.fetch_visible(args.live_url)
            mark("live-fetch", url=args.live_url, rung=res["rung"],
                 state=res["state"])
            print("e2e live-fetch: state=%s rung=%s"
                  % (res["state"], res["rung"]))
        except Exception as e:
            mark("live-fetch", url=args.live_url, error=str(e)[:120],
                 passed=False)
            fails.append("live-fetch")
    else:
        mark("live-fetch", skipped="no --live-url given")
        print("e2e live-fetch: skipped (no --live-url)")
    mark("summary", failed=fails if fails else "none")
    print("e2e: %s" % ("FAIL " + ",".join(fails) if fails else "PASS"))
    return 0 if not fails else 1


def cmd_fetch(args) -> int:
    """Fetch a page; intuit first, then climb the ladder."""
    from . import live_log as _l
    from . import websearch as _w
    if getattr(args, "intuit", False):
        from . import intuition as _in
        g = _in.intuit(args.url)
        _l.emit("fetch", "intuition", url=args.url, guess=g["state"],
                confidence=g["confidence"],
                predicted=g.get("predicted_rung"))
        print("intuition: %s conf=%.2f via %s"
              % (g["state"], g["confidence"],
                 "; ".join(g["provenance"])))
    res = _w.fetch_visible(args.url)
    _l.emit("fetch", "answer", url=args.url, rung=res["rung"],
            state=res["state"], kind=res.get("kind"))
    print(f"fetch: state={res['state']} rung={res['rung']} "
          f"kind={res.get('kind')}")
    if res["state"] == "ok":
        print(res["text"][:1500])
    else:
        print(res.get("detail", "withheld by challenge; "
              "solve it in your own browser, Eve will not "
              "solve CAPTCHAs for you"))
    return 0 if res["state"] == "ok" else 1


def cmd_bypass(args) -> int:
    """Attack Eve's own parsers; report what held and what gave."""
    from . import bypass_kit as _b
    res = _b.run_suite()
    for b in res["bypassed"]:
        print(f"  BYPASSED [{b['suite']}] {b['input'][:70]}")
        print(f"      {b['detail'][:90]}")
    print(f"bypass-kit: ran={res['ran']} held={res['held']} "
          f"bypassed={res['bypassed_count']}")
    print(res["note"])
    return 0 if res["bypassed_count"] == 0 else 1


def cmd_train(args) -> int:
    """Test/evolve cycling for the population; Lillith tunes the width."""
    if args.freeze_fixture:
        doc = freeze_fixture(TrainConfig().urls, args.freeze_fixture,
                             cap_s=args.cap_s)
        print("fixture frozen: %d urls sha=%s path=%s"
              % (len(doc["urls"]), doc["sha256"][:12], doc["path"]))
        return 0
    cfg = TrainConfig(gens=args.gens, pop=args.pop, seed=args.seed,
                      fresh=args.fresh,
                      log_path=args.log, pop_path=args.pop_file,
                      cap_s=args.cap_s, fixture=args.fixture)
    if args.null:
        res = null_control(cfg, args.null)
        print("null_control budget=%d best=%.4f mean=%.4f"
              % (res["budget"], res["best"], res["mean"]))
        return 0
    run(cfg)
    return 0


def cmd_cradle(args) -> int:
    """Adopt every model file in the cradle with its native runner."""
    from .cradle import adopt_cradle
    summary = adopt_cradle(args.dir)
    print(f"Cradle: {summary['cradle']}")
    for name, fw, runner in summary["adopted"]:
        print(f"  ADOPTED {name} [{fw}] via {runner}")
    for name, detail in summary["failed"]:
        print(f"  FAILED  {name}: {detail}")
    for name, why in summary["skipped"]:
        print(f"  SKIPPED {name} ({why})")
    print(f"adopted={len(summary['adopted'])} failed={len(summary['failed'])} "
          f"skipped={len(summary['skipped'])}")
    return 0 if not summary["failed"] else 1


def cmd_config(args) -> int:
    """Backup or restore eve configuration."""
    from .config import EveConfig

    if args.config_action == "backup":
        cfg = EveConfig()
        dest = Path(args.dest) if args.dest else Path.home() / ".azazael" / "eve_config_backup.json"
        cfg.to_file(str(dest))
        print(f"Config saved to {dest}")
        return 0

    elif args.config_action == "restore":
        src = Path(args.source) if args.source else Path.home() / ".azazael" / "eve_config_backup.json"
        if not src.exists():
            print(f"ERROR: no backup found at {src}", file=sys.stderr)
            return 1
        try:
            cfg = EveConfig.from_file(str(src))
            print(f"Config restored from {src}")
            print(json.dumps({k: getattr(cfg, k) for k in cfg.__dataclass_fields__}, indent=2, default=str))
            return 0
        except Exception as e:
            print(f"ERROR restoring config: {e}", file=sys.stderr)
            return 1

    # Default: show current config
    cfg = EveConfig()
    print("Current configuration:")
    print(f"  Device:        {cfg.device}")
    print(f"  Batch size:    {cfg.batch_size}")
    print(f"  Precision:     {cfg.precision}")
    print(f"  Cache:         {'enabled' if cfg.enable_cache else 'disabled'} ({cfg.cache_size_mb} MB)")
    print(f"  Warmup:        {cfg.warmup_iterations} iterations")
    print(f"  Default FW:    {cfg.default_framework}")
    print("\nUse 'eve config backup' to save or 'eve config restore' to load.")
    return 0


def cmd_benchmark(args) -> int:
    """Benchmark inference across CPU and available GPU backends."""
    from .engine import InferenceEngine
    from .core import ModelLoader, BatchResult

    print("=== Eve Unleashed GPU Benchmark ===\n")

    # Quick GPU summary
    gpu = ModelLoader.get_gpu_info()
    print(f"Recommended device: {gpu['recommended_device']}")
    print(f"CUDA: {gpu['cuda_available']}  |  XPU: {gpu['xpu_available']}  |  MPS: {gpu['metal_available']}  |  OpenCL: {gpu['opencl_available']}")
    print(f"Devices found: {gpu['device_count']}")
    for dev in gpu.get("devices", []):
        mem = f" ({dev.get('total_memory_gb', '?'):.1f} GB)" if dev.get("total_memory_gb") else ""
        print(f"  [{dev['backend']}] {dev['name']}{mem}")

    # Torch tensor benchmark
    try:
        import torch
    except ImportError:
        print("\n⚠️  PyTorch not installed — skipping tensor benchmark")
        return 0

    print("\n--- PyTorch Tensor Benchmark ---")
    print(f"PyTorch: {torch.__version__}")

    # Test on CPU
    size = args.size
    iterations = args.iterations

    print(f"\nMatrix multiply {size}x{size}, {iterations} iterations:\n")

    results = []

    # CPU benchmark
    a = torch.randn(size, size)
    b = torch.randn(size, size)
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        c = a @ b
        torch.cuda.synchronize() if torch.cuda.is_available() else None
        times.append((time.perf_counter() - t0) * 1000)
    avg = sum(times) / len(times)
    results.append(("cpu", avg, min(times), max(times)))
    print(f"  {'cpu':8s}  avg: {avg:8.2f} ms  min: {min(times):8.2f} ms  max: {max(times):8.2f} ms")

    # CUDA benchmark
    if gpu["cuda_available"]:
        a_cuda = a.to("cuda")
        b_cuda = b.to("cuda")
        times = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            c = a_cuda @ b_cuda
            torch.cuda.synchronize()
            times.append((time.perf_counter() - t0) * 1000)
        avg = sum(times) / len(times)
        results.append(("cuda", avg, min(times), max(times)))
        print(f"  {'cuda':8s}  avg: {avg:8.2f} ms  min: {min(times):8.2f} ms  max: {max(times):8.2f} ms")

    # XPU benchmark
    if gpu["xpu_available"]:
        try:
            a_xpu = a.to("xpu")
            b_xpu = b.to("xpu")
            times = []
            for _ in range(iterations):
                t0 = time.perf_counter()
                c = a_xpu @ b_xpu
                torch.xpu.synchronize()
                times.append((time.perf_counter() - t0) * 1000)
            avg = sum(times) / len(times)
            results.append(("xpu", avg, min(times), max(times)))
            print(f"  {'xpu':8s}  avg: {avg:8.2f} ms  min: {min(times):8.2f} ms  max: {max(times):8.2f} ms")
        except Exception as e:
            print(f"  {'xpu':8s}  ERROR: {e}")

    # MPS benchmark
    if gpu["metal_available"]:
        a_mps = a.to("mps")
        b_mps = b.to("mps")
        times = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            c = a_mps @ b_mps
            torch.mps.synchronize()
            times.append((time.perf_counter() - t0) * 1000)
        avg = sum(times) / len(times)
        results.append(("mps", avg, min(times), max(times)))
        print(f"  {'mps':8s}  avg: {avg:8.2f} ms  min: {min(times):8.2f} ms  max: {max(times):8.2f} ms")

    # Speedup summary
    if len(results) >= 2:
        cpu_avg = results[0][1]
        print("\n  Speedup vs CPU:")
        for name, avg_t, _, _ in results[1:]:
            if cpu_avg > 0:
                print(f"  {name:8s}  {cpu_avg / avg_t:5.1f}x")

    # Optional: model inference benchmark
    if args.model:
        print("\n--- Model Inference Benchmark ---")
        engine = InferenceEngine()
        try:
            handle = engine.load_model(args.model, args.framework)
            times = []
            for _ in range(min(iterations, 10)):
                t0 = time.perf_counter()
                result = engine.infer(handle, [1.0, 2.0, 3.0])
                times.append((time.perf_counter() - t0) * 1000)
            if times:
                avg = sum(times) / len(times)
                print(f"  Model: {args.model}")
                if isinstance(result, BatchResult):
                    print("  Framework: batch")
                else:
                    print(f"  Framework: {result.framework}")
                print(f"  Latency: {avg:.2f} ms avg over {len(times)} runs")
            else:
                print(f"  Model: {args.model}")
                print("  No iterations run (--iterations must be >= 1)")
            engine.shutdown()
        except Exception as e:
            print(f"  ERROR: {e}")

    return 0


def cmd_serve(args) -> int:
    """Start the FastAPI inference server."""
    try:
        from fastapi import FastAPI, HTTPException
        import uvicorn
    except ImportError:
        print(
            "FastAPI/uvicorn required for serve mode.\n"
            "Install: pip install azazael[serve]   or   pip install fastapi uvicorn",
            file=sys.stderr,
        )
        return 1

    from .engine import create_engine
    from .core import BatchResult

    engine = create_engine()
    app = FastAPI(
        title="Eve Unleashed Inference API",
        version="1.1.0",
        description="Multi-framework ML inference REST API",
    )

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "version": "1.1.0",
            "frameworks": engine.available_frameworks,
        }

    @app.get("/stats")
    async def stats():
        return engine.stats

    @app.post("/decide")
    async def decide(req: dict):
        """Clickable decision blocks: allowlisted dev actions only."""
        from . import mods as _mods
        action = req.get("decision", "")
        game = req.get("game", "")
        if action not in ("apply-best", "revert"):
            raise HTTPException(status_code=400,
                                detail="unknown decision: %s" % action)
        if not game:
            raise HTTPException(status_code=400,
                                detail="'game' is required")
        try:
            if action == "apply-best":
                manifest = _mods.apply_best(game)
                return {"decision": action, "applied": manifest}
            n = _mods.revert_mod(game)
            return {"decision": action, "reverted": n}
        except (ValueError, OSError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/therapy")
    async def therapy(req: dict):
        """Live therapy: local LLM first, then swarm, else grounding."""
        from . import therapy as _th
        mood = (req.get("mood") or "stressed")[:60]
        note = (req.get("note") or "")[:500]
        prompt = _th.build_prompt(mood, note)
        import asyncio as _aio
        try:
            local_text, local_tag = await _aio.wait_for(
                _aio.to_thread(_local_answer, prompt), timeout=200)
        except Exception as e:
            note_failure("cli:therapy-local", e)
            local_text, local_tag = None, "local error"
        if local_text:
            return {"text": local_text, "provider": local_tag,
                    "nsp": "nsp:local-first (no routing needed)",
                    "live": True}
        try:
            win, _reason = await _aio.wait_for(
                _aio.to_thread(_swarm_fallback, prompt),
                timeout=SWARM_TIMEOUT_S + 30)
        except Exception as e:
            note_failure("cli:therapy-swarm", e)
            win = None
        if win:
            return {"text": win["text"], "provider": win["provider"],
                    "nsp": win.get("nsp", "nsp-unavailable"),
                    "live": True}
        return {"text": _th.fallback_response(mood, note),
                "provider": "grounding-fallback", "live": False}

    @app.get("/gpu")
    async def gpu_info():
        return engine.gpu_info

    @app.get("/models")
    async def list_models():
        return {"models": engine.list_models()}

    @app.post("/models/load")
    async def load_model(req: dict):
        path = req.get("path", "")
        framework = req.get("framework", "auto")
        if not path:
            raise HTTPException(status_code=400, detail="'path' is required")
        try:
            handle = engine.load_model(path, framework)
            return {"handle": handle, "status": "loaded"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/models/{handle:path}")
    async def unload_model(handle: str):
        engine.unload_model(handle)
        return {"status": "unloaded"}

    @app.post("/infer")
    async def infer(req: dict):
        model_path = req.get("model", "")
        inputs = req.get("input", [])
        framework = req.get("framework", "auto")
        batch = req.get("batch", False)

        if not model_path:
            raise HTTPException(status_code=400, detail="'model' is required")

        # Check if already loaded (exact path match, not substring)
        matching = [h for h in engine.list_models() if h.split(":", 1)[1] == model_path]
        if not matching:
            try:
                handle = engine.load_model(model_path, framework)
            except Exception as e:
                if "not installed" in str(e) and isinstance(inputs, str):
                    import asyncio as _aio
                    try:
                        win, reason = await _aio.wait_for(
                            _aio.to_thread(_swarm_fallback, inputs),
                            timeout=SWARM_TIMEOUT_S + 30)
                    except Exception as te:
                        note_failure("cli:swarm-serve", te)
                        win, reason = None, "swarm error: %s" % te
                    if win:
                        return {
                            "model": model_path,
                            "framework": "swarm",
                            "provider": win["provider"],
                            "swarm_model": win["model"],
                            "nsp": win.get("nsp", "nsp-unavailable"),
                            "input_shape": None,
                            "output_shape": None,
                            "latency_ms": win["elapsed_ms"],
                            "predictions": [win["text"]],
                        }
                    from .core import ModelLoader as _ML
                    _label = (_ML.guess_framework(Path(model_path))
                              if framework == "auto" else framework)
                    raise HTTPException(
                        status_code=503,
                        detail="Framework '%s' is not installed; "
                               "swarm fallback: %s" % (_label, reason))
                raise HTTPException(status_code=400, detail=str(e))
        else:
            handle = matching[0]

        try:
            result = engine.infer(handle, inputs, batch=batch)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        # Handle both single InferenceResult and BatchResult
        if not isinstance(result, BatchResult):
            return {
                "model": result.model_name,
                "framework": result.framework,
                "input_shape": list(result.input_shape) if result.input_shape else None,
                "output_shape": list(result.output_shape) if result.output_shape else None,
                "latency_ms": result.latency_ms,
                "predictions": result.predictions,
            }
        else:
            # BatchResult
            return {
                "model": "batch",
                "framework": "batch",
                "total_time_ms": result.total_time_ms,
                "avg_latency_ms": result.avg_latency_ms,
                "throughput_per_sec": result.throughput_per_sec,
                "count": len(result.results),
                "predictions": [
                    r.predictions for r in result.results
                ] if result.results else [],
            }

    @app.on_event("shutdown")
    async def shutdown():
        engine.shutdown()

    from .model_registry import get_registry as _get_registry
    try:
        _gone = _get_registry().prune_missing()
        print(f"   Registry prune: {len(_gone)} stale entr(ies) removed")
        for _g in _gone[:5]:
            print(f"     pruned: {_g}")
    except Exception as _pe:
        note_failure("cli:serve-prune", _pe)
        print("   Registry prune skipped (registry unreadable)")
    print(f"Eve Unleashed Inference API starting on http://{args.host}:{args.port}")
    print(f"   Frameworks: {engine.available_frameworks}")
    print(f"   Docs:       http://{args.host}:{args.port}/docs")
    print(f"   Health:     http://{args.host}:{args.port}/health")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="eve",
        description="Eve Unleashed — Multi-framework ML Inference CLI",
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # eve infer
    infer_p = sub.add_parser("infer", help="Run inference on a model")
    infer_p.add_argument("--model", "-m", required=True, help="Path to model file")
    infer_p.add_argument("--input", "-i", required=True, help="Input data (JSON array or text)")
    infer_p.add_argument("--framework", "-f", default="auto",
                        help="Framework: auto, torch, sklearn, transformers, onnx, gguf, vl")
    infer_p.add_argument("--output", "-o", default="text", choices=["text", "json"],
                        help="Output format")
    infer_p.set_defaults(func=cmd_infer)

    # eve registry
    reg_p = sub.add_parser("registry", help="Model registry — track models across projects")
    reg_sub = reg_p.add_subparsers(dest="registry_action", help="Registry action")
    reg_sub.add_parser("show", help="Show registry summary")
    reg_sub.add_parser("prune", help="Drop entries whose files no longer exist")
    reg_scan = reg_sub.add_parser("scan", help="Scan for model files")
    reg_scan.add_argument("--dirs", "-d", help="Comma-separated directories to scan")
    reg_p.add_argument("--verbose", "-v", action="store_true", help="Show all models")
    reg_p.set_defaults(func=cmd_registry, registry_action="show")

    # eve config
    cfg_p = sub.add_parser("config", help="Backup or restore eve configuration")
    cfg_sub = cfg_p.add_subparsers(dest="config_action", help="Config action")
    cfg_backup = cfg_sub.add_parser("backup", help="Save current config to disk")
    cfg_backup.add_argument("--dest", help="Destination path (default: ~/.azazael/eve_config_backup.json)")
    cfg_restore = cfg_sub.add_parser("restore", help="Restore config from backup")
    cfg_restore.add_argument("--source", help="Source path (default: ~/.azazael/eve_config_backup.json)")
    cfg_p.set_defaults(func=cmd_config, config_action="show")

    # eve benchmark
    bench_p = sub.add_parser("benchmark", help="Benchmark GPU vs CPU inference")
    bench_p.add_argument("--size", "-s", type=int, default=2048, help="Matrix size for tensor benchmark")
    bench_p.add_argument("--iterations", "-n", type=int, default=5, help="Benchmark iterations")
    bench_p.add_argument("--model", "-m", help="Optional: model path for inference benchmark")
    bench_p.add_argument("--framework", "-f", default="auto", help="Framework for model benchmark")
    bench_p.set_defaults(func=cmd_benchmark)

    # eve serve
    serve_p = sub.add_parser("serve", help="Start REST API inference server")
    serve_p.add_argument("--host", default="127.0.0.1", help="Bind host")
    serve_p.add_argument("--port", "-p", type=int, default=8080, help="Bind port")
    serve_p.set_defaults(func=cmd_serve)

    # eve cradle
    cradle_p = sub.add_parser("cradle", help="Adopt model files in the cradle natively")
    cradle_p.add_argument("--dir", default=None, help="Cradle dir (default: ~/.azazael/cradle)")
    cradle_p.set_defaults(func=cmd_cradle)

    run_p = sub.add_parser("run", help="Run any cradle model (not only Eve)")
    run_p.add_argument("--model", required=True,
                       help="Cradle stem, name or path")
    run_p.add_argument("--prompt", required=True)
    run_p.add_argument("--tokens", type=int, default=128)
    run_p.add_argument("--dir", default=None)
    run_p.set_defaults(func=cmd_run)

    foundry_p = sub.add_parser("foundry", help="Build model artifacts natively")
    foundry_p.add_argument("--out", required=True)
    foundry_p.add_argument("--name", default="eve-foundry-001")
    foundry_p.add_argument("--seed", type=int, default=7)
    foundry_p.add_argument("--dtype", default="Q8_0",
                           choices=["Q8_0", "Q4_0", "F32"],
                           help="Q8_0 serving default (near-lossless); "
                                "Q4_0 aggressive tier; F32 archival only")
    foundry_p.add_argument("--hi-prec", nargs="*", default=[],
                           metavar="TENSOR",
                           help="tensor names kept at F32 while the "
                                "rest quantize (e.g. foundry.bias)")
    foundry_p.set_defaults(func=cmd_foundry)

    unleash_p = sub.add_parser("unleash", help="Eve vessel + 316t hands")
    unleash_p.add_argument("--name", default="eve-vessel-001")
    unleash_p.add_argument("--matrix", action="store_true",
                           help="Native-function availability on this interpreter")
    unleash_p.add_argument("--ask", default=None,
                           help="Ask the unleashed mind (cradle sidecar)")
    unleash_p.add_argument("--mind", default=None,
                           help="Cradle text model serving as mind")
    unleash_p.set_defaults(func=cmd_unleash)

    # eve grievance (Boston Tea Party: public grievance and redress)
    grv_p = sub.add_parser("grievance", help="File grievances, draft redress, find legal aid")
    grv_sub = grv_p.add_subparsers(dest="grievance_action", help="Grievance action")
    grv_file = grv_sub.add_parser("file", help="File a grievance")
    grv_file.add_argument("--respondent", required=True)
    grv_file.add_argument("--category", required=True)
    grv_file.add_argument("--narrative", required=True)
    grv_file.add_argument("--redress", required=True)
    grv_file.add_argument("--evidence", nargs="*", default=[])
    grv_sub.add_parser("show", help="List filed grievances")
    grv_draft = grv_sub.add_parser("draft", help="Draft FOIA + public comment from a grievance")
    grv_draft.add_argument("--id", required=True)
    grv_draft.add_argument("--agency", default="")
    grv_draft.add_argument("--requester", default="")
    grv_dock = grv_sub.add_parser("dockets", help="Search Regulations.gov dockets")
    grv_dock.add_argument("--term", required=True)
    grv_ref = grv_sub.add_parser("refer", help="Free legal-aid referrals")
    grv_ref.add_argument("--state", default=None)
    grv_prove = grv_sub.add_parser("prove", help="Compile court-filing evidence bundle")
    grv_prove.add_argument("--id", required=True)
    grv_sub.add_parser("tutorial", help="Guided grievance-filing tutorial")
    grv_p.set_defaults(func=cmd_grievance, grievance_action="show")

    # eve debunk (native myth-debunking: no network, no key)
    deb_p = sub.add_parser("debunk", help="Take a claim apart with its own words")
    deb_p.add_argument("--claim", required=True, help="The claim to debunk")
    deb_p.set_defaults(func=cmd_debunk)

    # eve factcheck (live fact-checking: receipts for everything seen)
    fac_p = sub.add_parser("factcheck", help="Live-check a claim, receipt everything")
    fac_p.add_argument("--claim", required=True, help="The claim to check")
    fac_p.add_argument("--grievance", default=None,
                       help="Filed grievance id to cross-check against")
    fac_p.add_argument("--url", default=None,
                       help="Fetch one URL now and keep the receipt only")
    fac_p.add_argument("--lock", action="store_true",
                       help="Archive live receipts into the evidence locker")
    fac_p.set_defaults(func=cmd_factcheck)

    # eve locker (content-addressed evidence with custody)
    lok_p = sub.add_parser("locker", help="Keep evidence, prove custody")
    lok_sub = lok_p.add_subparsers(dest="locker_action")
    lok_file = lok_sub.add_parser("ingest", help="Ingest a file")
    lok_file.add_argument("--path", required=True)
    lok_note = lok_sub.add_parser("note", help="Ingest a written note")
    lok_note.add_argument("--text", required=True)
    lok_note.add_argument("--label", default="intake-note")
    lok_ver = lok_sub.add_parser("verify", help="Rehash a locker item")
    lok_ver.add_argument("--id", required=True)
    lok_cus = lok_sub.add_parser("custody", help="Touch log for an item")
    lok_cus.add_argument("--id", required=True)
    lok_sub.add_parser("list", help="List locker items")
    lok_p.set_defaults(func=cmd_locker, locker_action="list")

    # eve watch (docket watchlist)
    wat_p = sub.add_parser("watch", help="Watch dockets, report only news")
    wat_p.add_argument("--add", default=None, help="Watch a term")
    wat_p.add_argument("--remove", default=None, help="Unwatch a term")
    wat_p.add_argument("--check", action="store_true",
                       help="Poll all terms and diff")
    wat_p.set_defaults(func=cmd_watch)

    # eve rebuttal (counter-myth with receipts)
    reb_p = sub.add_parser("rebuttal", help="Draft a citable correction")
    reb_p.add_argument("--claim", required=True)
    reb_p.add_argument("--grievance", default=None)
    reb_p.set_defaults(func=cmd_rebuttal)

    # eve sanctuary (faith for the justice-seeker)
    san_p = sub.add_parser("sanctuary", help="Comfort, accommodation, study")
    san_p.add_argument("--counsel", default=None,
                       help="Burden to hold (comfort + referrals)")
    san_p.add_argument("--accommodate", nargs=3, metavar=("NAME", "CTX", "NEED"),
                       help="Draft accommodation (CTX: workplace/school/prison)")
    san_p.add_argument("--study", default=None, help="Study a faith topic")
    san_p.add_argument("--movements", action="store_true",
                       help="Faith-rooted justice record")
    san_p.set_defaults(func=cmd_sanctuary)

    # eve inquest (forensic dossier for one grievance)
    inq_p = sub.add_parser("inquest", help="Bind record, custody, receipts")
    inq_p.add_argument("--id", required=True, help="Grievance id")
    inq_p.add_argument("--locker", nargs="*", default=[],
                       help="Locker ids to bind")
    inq_p.add_argument("--claim", default=None,
                       help="Live-check one claim into the dossier")
    inq_p.set_defaults(func=cmd_inquest)

    # eve mend (persistent evolution: scan, record, verify)
    mend_p = sub.add_parser("mend", help="Persistent evolution: scan/record/verify")
    mend_sub = mend_p.add_subparsers(dest="mend_action")
    mend_sub.add_parser("scan", help="Open issues from cache + bake lane")
    mend_rec = mend_sub.add_parser("fix", help="Record a fix in the bake lane")
    mend_rec.add_argument("--title", required=True)
    mend_rec.add_argument("--body", required=True)
    mend_rec.add_argument("--item", default="mend")
    mend_sub.add_parser("verify", help="Re-run the eve test files")
    mend_p.set_defaults(func=cmd_mend, mend_action="scan")

    # eve tutor (document -> runnable tutorial, natively)
    tut_p = sub.add_parser("tutor", help="Convert a document into a tutorial")
    tut_p.add_argument("--doc", required=True, help="Document to convert")
    tut_p.add_argument("--out", default=None, help="Write tutorial markdown")
    tut_p.add_argument("--audience", default="general")
    tut_p.set_defaults(func=cmd_tutor)

    # eve oracle (several signals, one verdict)
    ora_p = sub.add_parser("oracle", help="Ask every signal, measure agreement")
    ora_p.add_argument("--claim", required=True)
    ora_p.add_argument("--grievance", default=None)
    ora_p.add_argument("--locker", nargs="*", default=[])
    ora_p.set_defaults(func=cmd_oracle)

    # eve swarm (bounded parallel oracle fan-out)
    sw_p = sub.add_parser("swarm", help="Fan claims across a bounded pool")
    sw_p.add_argument("--claims", nargs="+", required=True)
    sw_p.add_argument("--grievance", default=None)
    sw_p.add_argument("--live", action="store_true",
                      help="Include live network signal (default: text only)")
    sw_p.add_argument("--workers", type=int, default=4)
    sw_p.set_defaults(func=cmd_swarm)

    # eve transmute (chained recipes: claim/doc/grievance/swarm)
    tr_p = sub.add_parser("transmute", help="Chained recipes, staged receipts")
    tr_p.add_argument("--recipe", required=True,
                      choices=["lead_into_gold", "scroll_into_school",
                               "grievance_into_dossier", "swarm_into_digest"])
    tr_p.add_argument("--claim", default=None)
    tr_p.add_argument("--claims", nargs="*", default=[])
    tr_p.add_argument("--doc", default=None)
    tr_p.add_argument("--out", default=None)
    tr_p.add_argument("--grievance", default=None)
    tr_p.add_argument("--locker", nargs="*", default=[])
    tr_p.set_defaults(func=cmd_transmute)

    # eve live (voice-driven live morph window on loopback)
    lv_p = sub.add_parser("live", help="Speak, watch the document change")
    lv_p.add_argument("--doc", required=True)
    lv_p.add_argument("--port", type=int, default=0)
    lv_p.set_defaults(func=cmd_live)

    # eve stationery (tutorial -> keepsake page, created face)
    st_p = sub.add_parser("stationery", help="Render a keepsake page")
    st_p.add_argument("--doc", required=True)
    st_p.add_argument("--out", required=True)
    st_p.set_defaults(func=cmd_stationery)

    # eve search (five keyless providers, failures isolated)
    se_p = sub.add_parser("search", help="Fan a query across providers")
    se_p.add_argument("--query", required=True)
    se_p.add_argument("--providers", nargs="*", default=None)
    se_p.set_defaults(func=cmd_search)

    # eve fetch <url> (mutation ladder against bot challenges)
    fe_p = sub.add_parser("fetch", help="Fetch a page up the ladder")
    fe_p.add_argument("url")
    fe_p.add_argument("--intuit", action="store_true",
                      help="Ask Eve's memory first (no network), then climb")
    fe_p.set_defaults(func=cmd_fetch)

    # eve cognition (conscious / subconscious / unconscious strata + gaps)
    cog_p = sub.add_parser("cognition",
                           help="Cognitive strata: what runs, what does not")
    cog_p.add_argument("--stratum", default=None,
                       help="Name one stratum's paradigm")
    cog_p.add_argument("--nexus", default=None,
                       help="Trace one request across all three strata")
    cog_p.add_argument("--log", default=None)
    cog_p.set_defaults(func=cmd_cognition)

    # eve forge (grow a named cognition gap into a staged contract)
    fo_p = sub.add_parser("forge",
                          help="Grow a named gap into a staged contract")
    fo_p.add_argument("--gap", default=None)
    fo_p.add_argument("--root", default=None)
    fo_p.set_defaults(func=cmd_forge)

    # eve e2e (live-logged end-to-end: intuition, bypass, dream, tests)
    e2e_p = sub.add_parser("e2e", help="Live-logged end-to-end run")
    e2e_p.add_argument("--live-url", default=None)
    e2e_p.add_argument("--log", default=None)
    e2e_p.set_defaults(func=cmd_e2e)

    # eve bypass (defensive mutation kit against Eve's own parsers)
    by_p = sub.add_parser("bypass", help="Try to break Eve's parsers")
    by_p.set_defaults(func=cmd_bypass)

    # eve train (live test/evolve cycling; Lillith tunes mutation width)
    tr_p = sub.add_parser("train", help="Evolve the population live")
    tr_p.add_argument("--gens", type=int, default=1)
    tr_p.add_argument("--pop", type=int, default=8)  # H-09: scaled 4 -> 8
    tr_p.add_argument("--seed", type=int, default=316)
    tr_p.add_argument("--fresh", action="store_true",
                      help="Discard the saved population and start at gen 1")
    tr_p.add_argument("--log", default="C:/tmp/eve-train.jsonl")
    tr_p.add_argument("--pop-file", default="C:/tmp/eve-pop.json")
    tr_p.add_argument("--cap-s", type=float, default=40.0)
    tr_p.add_argument("--null", type=int, default=0,
                      help="Random-search baseline: score N fresh genomes, "
                           "no evolution (uses --log/--pop-file isolation)")
    tr_p.add_argument("--fixture", default=None,
                      help="Replay a frozen oracle file instead of live fetch")
    tr_p.add_argument("--freeze-fixture", default=None, metavar="PATH",
                      help="Fetch each URL live once, freeze to PATH, exit")
    tr_p.set_defaults(func=cmd_train)

    # eve stats
    abl_p = sub.add_parser("ablate", help="Refusal ablation across providers")
    abl_p.add_argument("--prompt", action="append", default=[],
                       help="Benign prompt (repeatable; default: two built-ins)")
    abl_p.add_argument("--timeout", type=int, default=45)
    abl_p.set_defaults(func=cmd_ablate)

    wasm_p = sub.add_parser("wasm", help="Inner states as WASM tutorial")
    wasm_p.add_argument("--from", dest="from_log", required=True,
                        help="Real `eve ablate` log to compile")
    wasm_p.add_argument("--out", required=True, help="Module output dir")
    wasm_p.add_argument("--build", action="store_true",
                        help="Compile to .wasm after generating")
    wasm_p.add_argument("--go", action="store_true",
                        help="Go tier (needs go+node) instead of Rust tier")
    wasm_p.add_argument("--run", action="store_true",
                        help="Execute the built module under node (Go tier, or Rust tier with --target nodejs)")
    wasm_p.add_argument("--target", default="web", choices=("web", "nodejs"),
                        help="Rust tier wasm-pack target (nodejs runs headless)")
    wasm_p.set_defaults(func=cmd_wasm)

    morph_p = sub.add_parser("morph", help="Convert/morph/mutate files")
    morph_sub = morph_p.add_subparsers(dest="morph_action")
    m_conv = morph_sub.add_parser("convert", help=".csv<->.json")
    m_conv.add_argument("--in", dest="src", required=True)
    m_conv.add_argument("--out", dest="dst", required=True)
    m_mut = morph_sub.add_parser("mutate", help="Seeded byte mutations")
    m_mut.add_argument("--in", dest="src", required=True)
    m_mut.add_argument("--out", required=True)
    m_mut.add_argument("--seed", type=int, default=7)
    m_mut.add_argument("--count", type=int, default=3)
    m_rep = morph_sub.add_parser("report", help="State JSON to Markdown")
    m_rep.add_argument("--in", dest="src", required=True)
    m_rep.add_argument("--title", default="Eve report")
    morph_p.set_defaults(func=cmd_morph)

    mod_p = sub.add_parser("modgame", help="Apply/revert game mods")
    mod_p.add_argument("--game", required=True, help="Game directory")
    mod_p.add_argument("--mod", default=None, help="Mod directory")
    mod_p.add_argument("--revert", action="store_true")
    mod_p.add_argument("--evolve", action="store_true",
                       help="Evolve numeric patches across generations")
    mod_p.add_argument("--generations", type=int, default=5)
    mod_p.add_argument("--seed", type=int, default=7)
    mod_p.add_argument("--fitness", default=None,
                       help="JSON {file::key: target} (default: maximize)")
    mod_p.set_defaults(func=cmd_modgame)

    stem_p = sub.add_parser("stem", help="Women-in-STEM tracks + referrals")
    stem_sub = stem_p.add_subparsers(dest="stem_action")
    stem_sub.add_parser("list", help="Learning-style tracks")
    stem_track = stem_sub.add_parser("track", help="Steps for a style+goal")
    stem_track.add_argument("--style", required=True)
    stem_track.add_argument("--goal", default="my first STEM build")
    stem_sub.add_parser("refer", help="Women-in-STEM organizations")
    stem_p.set_defaults(func=cmd_stem, stem_action="list")

    local_p = sub.add_parser("local", help="Answer via the local LLM only")
    local_p.add_argument("--prompt", required=True)
    local_p.add_argument("--system", default=None)
    local_p.add_argument("--model", default=None,
                         help="Cradle model stem (default: servers first)")
    local_p.set_defaults(func=cmd_local)

    lang_p = sub.add_parser("langs", help="Rust/Go/Python check, format, fix, arch")
    lang_sub = lang_p.add_subparsers(dest="lang_action")
    for _act in ("check", "fmt", "fix"):
        _p = lang_sub.add_parser(_act)
        _p.add_argument("--file", required=True)
    _arch_p = lang_sub.add_parser("arch", help="New-arch contract check")
    _arch_p.add_argument("--arch", required=True,
                         help="gamora | lucy | jaeger-316t")
    lang_tools = lang_sub.add_parser("tools", help="Toolchain presence")
    lang_tools.add_argument("--lang", default="rust")
    lang_p.set_defaults(func=cmd_langs, lang_action="check")

    stats_p = sub.add_parser("stats", help="Show engine statistics")
    stats_p.set_defaults(func=cmd_stats)

    # eve list
    list_p = sub.add_parser("list", help="list frameworks and available models")
    list_p.add_argument("--scan", "-s", action="store_true", help="Scan for model files")
    list_p.set_defaults(func=cmd_list_models)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
