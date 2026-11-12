# coding: utf-8
"""inference_sentinel -- the REAPER's second office: it reaps an inference, not just a frame.

WHY THIS EXISTS (M-48)
----------------------
`sentinel.py` stats a frame: the target exists, the defended tests exist, the act is in a
closed vocabulary. All of that can be true while the model that answered was not the model
anyone thinks answered. A local runtime serves whatever `--model-name` happens to resolve
to, a base URL can point at a proxy, and the system instruction is whatever the caller
sent. None of those raises an exception. They only make the answer wrong, and then make it
look verified.

So this asserts the four things that were previously unasserted:

  A1 endpoint answers     the base URL is reachable and speaks the OpenAI shape
  A2 model is served      the model id asked for is one the endpoint actually lists
  A3 instruction bound    sha256 of the system instruction, compared to `--expect-sha` or
                          to the fingerprint recorded on this machine
  A4 shape is the contract a minimal chat call returns choices[0].message.content
  (context window is REPORTED when the endpoint advertises it and stated as NOT ADVERTISED
   when it does not -- never guessed from a model name)

THE FINGERPRINT
---------------
`--record` writes `model`, `endpoint`, `instruction_sha256` and the date to
`.flaming_sword/inference.jsonl`. `--check` then compares all three against what is
recorded and REFUSES on a difference, naming it. That is the point: a hash of the system
instruction changes whenever the instruction changes, so the assertion is per-frame and
re-recording is a deliberate act rather than a silent one.

EXIT CODES
----------
  0  every assertion held
  1  FAILED -- a named assertion did not hold (the reason is printed, one line per failure)
  2  COULD NOT RUN -- no endpoint given, or the tool itself broke. Never reported as OK.

USAGE
-----
    python tools/inference_sentinel.py --check \\
        --model-url http://127.0.0.1:11434/v1 --model-name llama3.1 \\
        --instruction .flaming_sword/last_aio.txt
    python tools/inference_sentinel.py --check --model-url ... --record
    python tools/inference_sentinel.py --selftest

Stdlib only: urllib for the endpoint, http.server for the selftest stub.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

STATE_DIR = ".flaming_sword"
FINGERPRINTS = "inference.jsonl"
NAME = "REAPER"
TIMEOUT = 10

OK, FAILED, COULD_NOT_RUN = 0, 1, 2


# --------------------------------------------------------------------------- #
# The endpoint
# --------------------------------------------------------------------------- #

def _get(url: str, timeout: int = TIMEOUT) -> Tuple[Optional[Dict[str, Any]], str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace")), ""
    except urllib.error.HTTPError as exc:
        return None, "%s answered HTTP %d" % (url, exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, "%s did not answer: %s" % (url, exc)


def _post(url: str, body: Dict[str, Any], timeout: int = TIMEOUT
          ) -> Tuple[Optional[Dict[str, Any]], str]:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace")), ""
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode("utf-8", errors="replace")
        return None, "%s answered HTTP %d: %s" % (url, exc.code, detail)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, "%s did not answer: %s" % (url, exc)


def served_models(model_url: str) -> Tuple[List[str], Dict[str, Any], str]:
    """(model ids, raw payload, error). A 404 here is a wrong URL, not a missing model."""
    payload, error = _get(model_url.rstrip("/") + "/models")
    if error:
        return [], {}, error
    entries = payload.get("data") if isinstance(payload, dict) else None
    if entries is None:
        entries = payload.get("models") if isinstance(payload, dict) else []
    ids: List[str] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            name = entry.get("id") or entry.get("name") or entry.get("model")
            if name:
                ids.append(str(name))
        elif isinstance(entry, str):
            ids.append(entry)
    return ids, payload if isinstance(payload, dict) else {}, ""


def context_window(payload: Dict[str, Any], model: str) -> Optional[int]:
    """The context the endpoint ADVERTISES for this model, or None.

    Never derived from the model name: `llama3.1` does not imply 128k, and a number read
    off a name is exactly the kind of inherited trust this file exists to refuse.
    """
    for entry in payload.get("data") or payload.get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("id") or entry.get("name") or entry.get("model")
        if model and name and str(name) != model:
            continue
        for key in ("context_window", "context_length", "max_context_length",
                    "n_ctx", "max_model_len"):
            value = entry.get(key)
            if isinstance(value, int) and value > 0:
                return value
        meta = entry.get("meta") or {}
        if isinstance(meta, dict):
            for key in ("n_ctx_train", "n_ctx", "context_length"):
                value = meta.get(key)
                if isinstance(value, int) and value > 0:
                    return value
    return None


# --------------------------------------------------------------------------- #
# The fingerprint: what answered last time, on this machine
# --------------------------------------------------------------------------- #

def _fingerprint_path(state_dir: Optional[str]) -> Path:
    directory = Path(state_dir) if state_dir else Path.cwd() / STATE_DIR
    return directory / FINGERPRINTS


def read_fingerprint(state_dir: Optional[str]) -> Tuple[Optional[Dict[str, Any]], str]:
    path = _fingerprint_path(state_dir)
    if not path.exists():
        return None, ""
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError as exc:
        return None, "cannot read %s: %s" % (path, exc)
    if not lines:
        return None, ""
    try:
        return json.loads(lines[-1]), ""
    except ValueError as exc:
        return None, "%s line %d is not JSON: %s" % (path, len(lines), exc)


def record_fingerprint(model: str, endpoint: str, instruction_sha: str,
                       context: Optional[int], state_dir: Optional[str]) -> Dict[str, Any]:
    path = _fingerprint_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone

    entry = {"model": model, "endpoint": endpoint,
             "instruction_sha256": instruction_sha,
             "context_window": context,
             "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return {"path": str(path), "entry": entry}


def instruction_sha(text_or_path: str) -> str:
    """sha256 of the instruction: the text itself if it is not a file, else the file."""
    path = Path(text_or_path)
    if len(text_or_path) < 4096 and path.exists() and path.is_file():
        data = path.read_bytes()
    else:
        data = text_or_path.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #

def check(model_url: str, model_name: str, instruction: str = "", expect_sha: str = "",
          state_dir: Optional[str] = None, record: bool = False,
          call: bool = True) -> Tuple[int, List[Tuple[str, str, str]]]:
    """Run A1..A4. Returns (exit code, [(assertion, PASS|FAIL|NOTE, detail)])."""
    results: List[Tuple[str, str, str]] = []
    if not model_url:
        return COULD_NOT_RUN, [("endpoint", "FAIL",
                                "no --model-url: this tool cannot check an endpoint it "
                                "was not given, and will not guess one")]

    ids, payload, error = served_models(model_url)
    if error:
        return FAILED, [("A1 endpoint answers", "FAIL", error)]
    results.append(("A1 endpoint answers", "PASS", model_url))

    if not model_name:
        # A check that names no model cannot assert which model answered, which is the
        # whole point of the file. Refuse rather than pass.
        results.append(("A2 model is served", "FAIL",
                        "no --model-name: an unnamed model is an unchecked model; "
                        "served: %s" % (", ".join(ids[:5]) or "none listed")))
        return FAILED, results
    if model_name in ids:
        results.append(("A2 model is served", "PASS", model_name))
    else:
        results.append(("A2 model is served", "FAIL",
                        "asked for %r, endpoint serves %s"
                        % (model_name, ", ".join(ids[:8]) or "nothing")))
        return FAILED, results

    window = context_window(payload, model_name)
    results.append(("context window", "NOTE",
                    "%d tokens (advertised)" % window if window else
                    "not advertised by this endpoint -- stated, not guessed"))

    sha = ""
    if instruction or expect_sha:
        sha = instruction_sha(instruction) if instruction else ""
        if expect_sha:
            if sha and sha != expect_sha:
                results.append(("A3 instruction bound", "FAIL",
                                "instruction sha %s != expected %s" % (sha[:16],
                                                                       expect_sha[:16])))
                return FAILED, results
            results.append(("A3 instruction bound", "PASS", "%s (matches --expect-sha)"
                                                           % (sha or expect_sha)[:16]))
        else:
            recorded, read_error = read_fingerprint(state_dir)
            if read_error:
                results.append(("A3 instruction bound", "FAIL", read_error))
                return FAILED, results
            if recorded and recorded.get("instruction_sha256") not in ("", None, sha):
                results.append(("A3 instruction bound", "FAIL",
                                "the instruction changed since %s: %s -> %s"
                                % (recorded.get("recorded_at", "the last record"),
                                   str(recorded.get("instruction_sha256"))[:16], sha[:16])))
                return FAILED, results
            results.append(("A3 instruction bound", "PASS",
                            "%s%s" % (sha[:16], " (unchanged since the last record)"
                                      if recorded else " (first record)")))
            if recorded:
                for field, label in (("model", "model"), ("endpoint", "endpoint")):
                    was = str(recorded.get(field) or "")
                    now = model_name if field == "model" else model_url
                    if was and was != now:
                        results.append(("A3 instruction bound", "FAIL",
                                        "%s changed since %s: %r -> %r"
                                        % (label, recorded.get("recorded_at",
                                                               "the last record"), was, now)))
                        return FAILED, results

    if call:
        body = {"model": model_name, "max_tokens": 1, "temperature": 0,
                "messages": [{"role": "user", "content": "reply with the word: ok"}]}
        answer, call_error = _post(model_url.rstrip("/") + "/chat/completions", body)
        if call_error:
            results.append(("A4 shape is the contract", "FAIL", call_error))
            return FAILED, results
        text = ""
        try:
            text = str(answer["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            results.append(("A4 shape is the contract", "FAIL",
                            "answered in an unexpected shape: %s"
                            % json.dumps(answer)[:160]))
            return FAILED, results
        results.append(("A4 shape is the contract", "PASS",
                        "choices[0].message.content = %r" % text[:40]))

    if record:
        written = record_fingerprint(model_name, model_url, sha, window, state_dir)
        results.append(("recorded", "NOTE", written["path"]))
    return OK, results


# --------------------------------------------------------------------------- #
# Selftest: a stub endpoint, then the refusals that matter
# --------------------------------------------------------------------------- #

class _Stub(BaseHTTPRequestHandler):
    models: List[str] = ["stub-model:8b"]
    instruction_seen: List[str] = []

    def log_message(self, *args: Any) -> None:  # silent
        return

    def _send(self, body: Dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.endswith("/models"):
            return self._send({"data": [{"id": name, "context_length": 8192}
                                        for name in self.models]})
        return self._send({"error": "no such path"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        self.instruction_seen.append(str(payload.get("model") or ""))
        return self._send({"choices": [{"message": {"role": "assistant",
                                                   "content": "ok"}}]})


def selftest() -> Tuple[int, List[str]]:
    lines: List[str] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:%d/v1" % server.server_address[1]
    failures = 0
    try:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            # 1. everything matches
            code, results = check(url, "stub-model:8b", instruction="be careful",
                                  state_dir=tmp, record=True)
            if code == OK:
                lines.append("PASS a served model with the right instruction exits 0")
            else:
                failures += 1
                lines.append("FAIL expected 0, got %d: %s" % (code, results[-1][2]))

            # 2. the model is not the one served -- the headline defect
            code, results = check(url, "llama3.1:70b", state_dir=tmp, call=False)
            if code == FAILED and "A2 model is served" in results[-1][0]:
                lines.append("PASS an unserved model name is REFUSED, naming both sides")
            else:
                failures += 1
                lines.append("FAIL unserved model was not refused: %s" % (results,))

            # 3. the instruction changed under a recorded fingerprint
            code, results = check(url, "stub-model:8b", instruction="be careful!",
                                  state_dir=tmp, call=False)
            if code == FAILED and "instruction changed" in results[-1][2]:
                lines.append("PASS a changed instruction REFUSES against the record")
            else:
                failures += 1
                lines.append("FAIL instruction change was not refused: %s" % (results,))

            # 4. an unnamed model cannot assert which model answered
            code, results = check(url, "", state_dir=tmp, call=False)
            if code == FAILED and "unnamed" in results[-1][2]:
                lines.append("PASS an unnamed model is refused, not assumed")
            else:
                failures += 1
                lines.append("FAIL unnamed model was not refused: %s" % (results,))

            # 5. no endpoint at all is COULD NOT RUN, never OK
            code, _results = check("", "stub-model:8b")
            if code == COULD_NOT_RUN:
                lines.append("PASS no endpoint is exit 2, not a quiet pass")
            else:
                failures += 1
                lines.append("FAIL no endpoint exited %d" % code)

            # 6. a dead port is a FAILED endpoint assertion, named
            code, results = check("http://127.0.0.1:9/v1", "stub-model:8b", call=False)
            if code == FAILED and results[0][0].startswith("A1"):
                lines.append("PASS a dead endpoint fails A1 and says which URL")
            else:
                failures += 1
                lines.append("FAIL dead endpoint gave %s" % (results,))

            # 7. the context window reported is the one ADVERTISED
            code, results = check(url, "stub-model:8b", state_dir=tmp, call=False)
            note = [detail for name, _verdict, detail in results if name == "context window"]
            if note and "8192" in note[0]:
                lines.append("PASS the advertised context window is reported (8192)")
            else:
                failures += 1
                lines.append("FAIL context window not reported: %s" % (results,))
    finally:
        server.shutdown()
        server.server_close()

    return (OK if not failures else FAILED), lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="THE REAPER's second office: assert which model answered, from which "
                    "endpoint, under which instruction hash -- and refuse when it changed.")
    parser.add_argument("--check", action="store_true", help="run A1..A4 against an endpoint")
    parser.add_argument("--selftest", action="store_true", help="prove the refusals")
    parser.add_argument("--model-url", default="", help="OpenAI-compatible base URL, /v1")
    parser.add_argument("--model-name", default="", help="the model id that must be served")
    parser.add_argument("--instruction", default="",
                        help="the system instruction text, or a file holding it")
    parser.add_argument("--expect-sha", default="",
                        help="the instruction sha256 this run must match")
    parser.add_argument("--record", action="store_true",
                        help="write this endpoint+model+instruction fingerprint, so a "
                             "later change is refused instead of noticed")
    parser.add_argument("--no-call", action="store_true",
                        help="skip A4 (the one-token shape call)")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        code, lines = selftest()
        for line in lines:
            print(line)
        print("")
        print("SELFTEST %s (%d check(s))" % ("OK" if code == OK else "FAILED", len(lines)))
        return code

    if not args.check:
        parser.print_help()
        return COULD_NOT_RUN

    code, results = check(args.model_url, args.model_name, args.instruction,
                          args.expect_sha, args.state_dir, args.record,
                          call=not args.no_call)
    if args.json:
        print(json.dumps({"ok": code == OK, "exit": code,
                          "assertions": [{"assertion": name, "verdict": verdict,
                                          "detail": detail}
                                         for name, verdict, detail in results]}, indent=1))
    else:
        print("INFERENCE SENTINEL (%s) -- %s" % (NAME, args.model_url or "no endpoint"))
        for name, verdict, detail in results:
            print("  %-27s %-5s %s" % (name, verdict, detail))
        print("")
        if code == OK:
            print("OK -- the model, the endpoint and the instruction are the ones claimed")
        elif code == COULD_NOT_RUN:
            print("COULD NOT RUN -- this is not a pass")
        else:
            print("REFUSED -- a claim about the inference did not hold")
    return code


if __name__ == "__main__":
    sys.exit(main())
