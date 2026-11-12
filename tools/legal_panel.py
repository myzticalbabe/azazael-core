#!/usr/bin/env python3
"""Run an isolated multi-provider panel over a document, one angle per seat.

The review brief (docs/REVIEW_BRIEF_KILO_LEGAL_PANEL_2026-11-03.md §5) asks for
>=5 providers, distinct angles assigned before any seat sees the memo, no seat
seeing another's output, and dissent preserved verbatim. This script is that
mechanism and nothing more: it fans one document out to N seats, records every
raw reply to disk, and prints a compact table. It does not synthesise, rank or
"agree" on anything - synthesis is a human step and must cite the per-seat rows.

    python tools/legal_panel.py                       # all seats, writes logs/
    python tools/legal_panel.py --seats gemini,cohere # subset
    python tools/legal_panel.py --dry-run             # show prompts, call nothing

Credentials are read from ~/.azazael/<provider>_config.json (api_key/base_url/
model) or OPENROUTER_API_KEY. Keys are never printed.

Two facts this harness refuses to paper over:

* A seat that cannot answer is RECORDED AS FAILED with its HTTP code, not dropped.
  A panel of five reported as seven is a false reading. Measured 2026-11-03:
  gemini-2.0-flash and meta/llama-3.3-70b-instruct on NIM were both retired (404,
  410); cerebras/zhipu/huggingface/deepinfra were credit walls (402); mistral's
  free tier rate-limited (429 code 1300); Anthropic Opus via OpenRouter could
  "afford 327 tokens" of completion - one verdict short, so it is unavailable,
  not approximated.
* An LLM seat is a reasoning source, not a citator. A model can invent a plausible
  case. Every authority a seat names is UNVERIFIED until located independently,
  and must be marked as such wherever its output is quoted.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HOME = pathlib.Path.home()
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Angles are fixed here, before any seat sees the document. Two seats must never
# share an angle: identical angles on different models measure model variance,
# not legal disagreement, and the brief asks for the latter.
SEATS = [
    {
        "seat": "gemini",
        "provider": "Google",
        "model_default": "gemini-3.1-flash-lite",
        "base_url_default": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "angle": "labour and employment law",
        "focus": (
            "You are a labour-law specialist. Interrogate the featherbedding claim against "
            "NLRA s.8(b)(6) and American Newspaper Publishers Assn. v. NLRB, 345 U.S. 100 (1953). "
            "Is the resemblance to regulator-imposed substitution requirements misleading? Is there "
            "a better term of art in labour law, licensing, or EU professional-qualification doctrine?"
        ),
    },
    {
        "seat": "groq",
        "config": "groq",
        "provider": "Groq (OpenAI gpt-oss model)",
        "model_default": "openai/gpt-oss-120b",
        "base_url_default": "https://api.groq.com/openai/v1",
        "angle": "antitrust",
        "focus": (
            "You are an antitrust litigator. Test whether Goldfarb v. Virginia State Bar and "
            "N.C. State Bd. of Dental Examiners v. FTC actually reach a state board adopting an "
            "AI-substitution rule. Address Parker immunity, active supervision, antitrust injury and "
            "standing for a plaintiff not yet in the market."
        ),
    },
    {
        "seat": "cohere",
        "provider": "Cohere",
        "model_default": "command-r-plus-08-2024",
        "base_url_default": "https://api.cohere.ai/compatibility/v1",
        "angle": "intellectual property (copyright and patent)",
        "focus": (
            "You are an IP specialist. Decide whether the authorship rule and the inventorship rule "
            "are the same rule for the same reason, given 17 U.S.C. s.102(a) and 35 U.S.C. s.100(f)-(g), "
            "and whether the statutory-definition route to inventorship differs in a way that matters."
        ),
    },
    {
        "seat": "qwen",
        "config": "openrouter",
        "provider": "OpenRouter (Alibaba Qwen model)",
        "model_default": "qwen/qwen3-32b",
        "base_url_default": "https://openrouter.ai/api/v1",
        "angle": "antitrust",
        "focus": (
            "You are an antitrust litigator. Test whether Goldfarb v. Virginia State Bar and "
            "N.C. State Bd. of Dental Examiners v. FTC actually reach a state board adopting an "
            "AI-substitution rule. Address Parker immunity, active supervision, antitrust injury and "
            "standing for a plaintiff not yet in the market."
        ),
    },
    {
        "seat": "mistral",
        "provider": "Mistral",
        "model_default": "mistral-small-latest",
        "base_url_default": "https://api.mistral.ai/v1",
        "angle": "EU law and data protection",
        "focus": (
            "You are an EU/data-protection lawyer. Check the GDPR Article 22 reading: does inserting a "
            "human extinguish the data subject's recourse, on the text, EDPB guidance and the case law? "
            "Say exactly what the withdrawn AI Liability Directive can and cannot prove."
        ),
    },
    {
        "seat": "openrouter",
        "provider": "OpenRouter (Meta model)",
        "model_default": "meta-llama/llama-3.3-70b-instruct",
        "base_url_default": "https://openrouter.ai/api/v1",
        "angle": "adversarial: break this memorandum",
        "focus": (
            "You are opposing counsel. Your only job is to break the memorandum: name the claim a court "
            "would reject first, the authority that kills it, and the strongest counter-example to the "
            "substitution axis (a regime that bites a non-substituting use as hard as a substituting one)."
        ),
    },
    {
        "seat": "deepseek",
        "config": "openrouter",
        "provider": "OpenRouter (DeepSeek model)",
        "model_default": "deepseek/deepseek-v3.2",
        "base_url_default": "https://openrouter.ai/api/v1",
        "zero_shot": True,
        "angle": "professional regulation and licensing",
        "focus": (
            "You are a professional-regulation specialist. Assess the five-mechanism convergence claim "
            "through licensing and unauthorised-practice law: does a professional-responsibility rule install "
            "a human for the client's protection, or for the profession's market?"
        ),
    },
    {
        "seat": "opus",
        "config": "openrouter",
        "provider": "Anthropic via OpenRouter",
        "model_default": "anthropic/claude-opus-5.5",
        "base_url_default": "https://openrouter.ai/api/v1",
        "zero_shot": True,
        "max_tokens": 1400,
        "angle": "zero-shot appellate bench",
        "focus": (
            "You are a senior appellate judge reading this memorandum as a reviewing court would. "
            "No other seat sees your reasoning and you see no other seat's answer. Rule on each of the "
            "six questions as a disposition, not a commentary: state the ruling, the authority that carries "
            "it, and the point on which your ruling would be reversed if the opposing side is right."
        ),
    },
]

SCHEMA = """Return ONE JSON object and nothing else, in this exact shape:
{
  "seat": "<your seat name>",
  "verdicts": [
    {"q": 1, "verdict": "one or two sentences, <=45 words",
     "authority": "authority you rely on, or 'none'",
     "confidence": "high|medium|low"},
    {... for q 2..6 ...}
  ],
  "strongest_counterexample": "the single best counter-example to the memo's substitution axis, <=40 words",
  "memo_killer": "the one claim most likely to be rejected and the authority that kills it, or null",
  "dissent": "the point where you expect to disagree with the other seats, <=40 words",
  "verbatim": "one sentence from the memorandum you would strike, quoted exactly, or empty string"
}"""

QUESTIONS = """The six questions:
1. Is `featherbedding` the right word, or does the NLRA s.8(b)(6) narrowing make it affirmatively
   misleading when applied to regulator-imposed (not union-exacted) requirements? Is there a better term?
2. Are the five mechanisms actually one mechanism? Do authorship and inventorship reach the same rule
   for the same reason, or does the statutory-definition route (35 U.S.C. s.100(f)) differ materially?
3. Does the substitution axis survive? Find the strongest counter-example: a regime that bites a
   non-substituting use as hard as a substituting one.
4. Is the antitrust lever live or academic? Would Goldfarb / Dental Examiners reach a state board
   adopting an AI-substitution rule? What does Parker active supervision look like here? Standing?
5. Is the humanwashing / GDPR Art. 22 reading correct? Does the inserted human extinguish recourse?
   Is the withdrawn AI Liability Directive proving more than a withdrawn instrument can prove?
6. Is the memo's honesty section complete as of 2026-11-03, in both directions?"""


def load_config(seat: dict) -> dict:
    # The credential file is named by the SEAT'S PROVIDER, not by the seat: a seat
    # called "gptoss" has no gptoss_config.json, and the old env fallback then
    # handed it the OpenRouter key (measured: HTTP 401 Invalid API Key, which reads
    # as a dead provider instead of a mis-wired seat).
    credential = seat.get("config", seat["seat"])
    path = HOME / ".azazael" / f"{credential}_config.json"
    cfg = {}
    if path.exists():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            cfg = {"_error": f"{type(exc).__name__}: {exc}"}
    key = cfg.get("api_key", "")
    if not key and credential == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY", "")
    return {
        "api_key": key,
        "base_url": (cfg.get("base_url") or seat["base_url_default"]).rstrip("/"),
        "model": seat["model_default"],
        "config_error": cfg.get("_error", ""),
    }


def build_prompt(seat: dict, document: str) -> str:
    if seat.get("zero_shot"):
        # Zero-shot shape: the whole brief, once, with no worked example, no
        # scaffolded reasoning steps and no partial-credit framing. Deliberately
        # different in form from the analytical seats so a strong model is not
        # being asked to imitate the structure of the weaker ones.
        return (
            f"{seat['focus']}\n\n"
            "Read the whole memorandum once and rule. Do not restate the memorandum, do not offer to "
            "help further, and do not soften a ruling you believe is right.\n\n"
            f"=== MEMORANDUM (verbatim) ===\n{document}\n=== END MEMORANDUM ===\n\n"
            f"{QUESTIONS}\n\n{SCHEMA}"
        )
    return (
        f"You are one seat on an adversarial review panel. Your assigned angle is: {seat['angle']}.\n"
        f"{seat['focus']}\n\n"
        "You are seeing this memorandum alone. You have no other panel member's answer and must not "
        "assume agreement. Be specific and adversarial; a polite review is worth nothing.\n\n"
        f"=== MEMORANDUM (verbatim) ===\n{document}\n=== END MEMORANDUM ===\n\n"
        f"{QUESTIONS}\n\n{SCHEMA}"
    )


def call(seat: dict, prompt: str, timeout: int) -> dict:
    cfg = load_config(seat)
    started = time.time()
    result = {
        "seat": seat["seat"],
        "provider": seat["provider"],
        "model": cfg["model"],
        "angle": seat["angle"],
        "status": "failed",
        "error": "",
        "seconds": 0.0,
        "attempts": 0,
        "raw": "",
        "parsed": None,
    }
    if not cfg["api_key"]:
        result["error"] = "no api_key in config or environment"
        return result
    body = json.dumps(
        {
            "model": cfg["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": seat.get("max_tokens", 2200),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
            # A default urllib User-Agent is refused by Cloudflare in front of some
            # of these APIs (Cerebras answered 403 "error code: 1010", a
            # browser-integrity block, not an auth failure).
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    while True:
        result["attempts"] += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            text = payload["choices"][0]["message"]["content"] or ""
            result["raw"] = text
            result["status"] = "ok" if text.strip() else "empty"
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end > start:
                try:
                    result["parsed"] = json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    result["parsed"] = None
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            result["error"] = f"HTTP {exc.code}: {detail}"
            # One backoff retry for "ask again" codes only, never for a 4xx that
            # means the credential, model or balance is wrong.
            if exc.code in (429, 403, 503) and result["attempts"] < 2:
                time.sleep(12)
                continue
            break
        except Exception as exc:  # noqa: BLE001 - every failure is recorded per seat
            result["error"] = f"{type(exc).__name__}: {exc}"
            break
    result["seconds"] = round(time.time() - started, 1)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--document", default="docs/MEMORANDUM_WHEN_THE_MACHINE_DOES_THE_WORK_2026-11-03.md")
    ap.add_argument("--seats", default="", help="comma-separated seat names; default all")
    ap.add_argument("--out", default="", help="output dir; default logs/legal_panel")
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    doc_path = pathlib.Path(args.document)
    if not doc_path.exists():
        print(f"FAIL: document not found: {doc_path}")
        return 2
    document = doc_path.read_text(encoding="utf-8")

    wanted = [s.strip() for s in args.seats.split(",") if s.strip()]
    seats = [s for s in SEATS if not wanted or s["seat"] in wanted]
    if not seats:
        print(f"FAIL: no known seat in {wanted}; known: {[s['seat'] for s in SEATS]}")
        return 2

    if args.dry_run:
        for seat in seats:
            cfg = load_config(seat)
            prompt = build_prompt(seat, document)
            print(f"--- {seat['seat']} ({seat['provider']}, {cfg['model']}) ---")
            print(f"    endpoint: {cfg['base_url']}/chat/completions")
            print(f"    key present: {bool(cfg['api_key'])}   prompt chars: {len(prompt)}")
        return 0

    # One directory per run. A shared directory let seat files from an earlier,
    # failed run (cerebras.json, siliconflow.json, ...) sit beside the current
    # panel, so a reader counting files measured a panel that never happened.
    out_dir = pathlib.Path(args.out or f"logs/legal_panel/{time.strftime('%Y-%m-%d')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    prompts = {s["seat"]: build_prompt(s, document) for s in seats}

    with ThreadPoolExecutor(max_workers=len(seats)) as pool:
        futures = [pool.submit(call, s, prompts[s["seat"]], args.timeout) for s in seats]
        results = [f.result() for f in futures]

    for res in results:
        (out_dir / f"{res['seat']}.json").write_text(json.dumps(res, indent=2), encoding="utf-8")

    print(f"panel: {len(results)} seats, document {doc_path} ({len(document)} chars)")
    for res in results:
        verdicts = (res["parsed"] or {}).get("verdicts", []) if res["parsed"] else []
        print(
            f"  {res['seat']:11s} {res['provider']:28s} {res['status']:7s} "
            f"{res['seconds']:6.1f}s  q={len(verdicts)}/6  raw={len(res['raw']):5d}ch  {res['error'][:70]}"
        )
    answered = sum(1 for r in results if r["status"] == "ok")
    parsed = sum(1 for r in results if r["parsed"])
    print(f"seats answering: {answered}/{len(results)}   parseable JSON: {parsed}/{len(results)}")
    print(f"raw replies: {out_dir}")
    return 0 if answered == len(results) and parsed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
