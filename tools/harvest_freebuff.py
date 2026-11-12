"""harvest_freebuff.py — Freebuff chat transcripts -> bake JSONL pairs.

Streams each session's chat-messages.json (up to ~500MB single JSON
array) with an incremental raw_decode parser — never loads a whole
session into memory. Extracts:
  1. tool blocks with failure signals (nonzero exitCode, non-empty
     stderr, error-pattern output) -> error_fix.
  2. ai text blocks matching fix language -> error_fix (narrative
     window around the match).
Session context = directory timestamp + first user prompt.

Dedupes against bake_corpus + bake_harvest_*.jsonl by normalized title.
Output (harvest family, auto-joins merge base):
  state/bake_harvest_freebuff_2026-10-15.jsonl + report.

Usage: python tools/harvest_freebuff.py [--apply] [--session DIR]
  default = dry run over all sessions. --apply writes files.
"""
import json
import re
import sys
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parents[1]
CHATS = (Path.home() / ".config" / "manicode" / "projects" / "azazael"
         / "chats")
OUT_JSONL = ROOT / "state" / "bake_harvest_freebuff_2026-10-15.jsonl"
OUT_REPORT = ROOT / "state" / "bake_harvest_freebuff_report.txt"

FIX_RE = re.compile(r"fix|repair|resolv|root cause|error|fail|wrong|broken|"
                    r"workaround|patch|revert|crash|blocked|stuck",
                    re.IGNORECASE)
# Precision gate for narrative text: a fix claim needs a completed-action
# verb AND checkable evidence (file, line ref, SHA, or verdict) in the
# same window. Without it every "check for failures" aside becomes a row.
FIX_DONE_RE = re.compile(r"\bfixed\b|\brepaired\b|\bresolved\b|workaround|"
                         r"\bpatched\b|\blanded\b|\bverified\b|\bclosed\b",
                         re.IGNORECASE)
EVID_RE = re.compile(r"[A-Za-z_][\w\-]*\.(py|md|json|jsonl|ts|tsx|ps1|bat)|"
                     r":\d+|[0-9a-f]{7,40}|\bPASS\b|\bFAIL\b|\bgreen\b|"
                     r"SHAs?|commit", re.IGNORECASE)
ERR_RE = re.compile(r"error|fail|exception|traceback|E030|denied|refused|"
                    r"timeout|crash|not found|invalid|blocked",
                    re.IGNORECASE)
# Strong failure markers only: bare "error" substrings appear in
# routine long outputs. A tool call counts as failed on nonzero exit,
# substantial stderr, or an explicit failure marker.
STRONG_ERR_RE = re.compile(
    r"Traceback \(most recent|FAILED\b|\bError:|Errno \d+|"
    r"failed with exit|Command failed|exit code [1-9]",
    re.IGNORECASE)


def _sig_line(text):
    for line in (text or "").strip().splitlines():
        s = line.strip()
        if s and not re.match(r"^[=\-#>*>]{2,}\s*$", s):
            return s[:60]
    return "no output"


def _tool_failed(block):
    ec = block.get("exitCode", block.get("exit_code", None))
    if ec not in (None, 0, "0", ""):
        return True, "exit=%s" % ec
    stderr = str(block.get("stderr", "") or "").strip()
    if len(stderr) > 20:
        return True, "stderr"
    if STRONG_ERR_RE.search(block_text(block)[:4000]):
        return True, "marker"
    return False, ""


def norm_title(t):
    return re.sub(r"\s+", " ", str(t).strip().lower())


def load_titles():
    titles = set()
    pats = [ROOT / "state" / "bake_corpus.jsonl"]
    pats += sorted((ROOT / "state").glob("bake_harvest_*.jsonl"))
    for fp in pats:
        if fp == OUT_JSONL or not fp.exists():
            continue
        for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                titles.add(norm_title(json.loads(line).get("title", "")))
            except (ValueError, AttributeError):
                continue
    return titles


def iter_messages(fp):
    """Yield message dicts from a top-level JSON array, streaming."""
    dec = json.JSONDecoder()
    buf = ""
    started = False
    with open(fp, encoding="utf-8", errors="replace") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            buf += chunk
            pos = 0
            n = len(buf)
            while pos < n:
                while pos < n and buf[pos] in " \t\r\n[,]":
                    if buf[pos] == "[" and not started:
                        started = True
                    pos += 1
                if pos >= n:
                    break
                if buf[pos] == "]":
                    return
                try:
                    obj, end = dec.raw_decode(buf, pos)
                except json.JSONDecodeError:
                    break
                yield obj
                pos = end
            buf = buf[pos:]


def block_text(block):
    if not isinstance(block, dict):
        return ""
    for key in ("content", "text", "output", "result"):
        val = block.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def harvest_session(sdir, titles, records, dropped):
    first_prompt = ""
    n_msg = 0
    for msg in iter_messages(sdir / "chat-messages.json"):
        if not isinstance(msg, dict):
            continue
        n_msg += 1
        variant = msg.get("variant", "")
        blocks = msg.get("blocks", []) or []
        if variant == "user" and not first_prompt:
            for b in blocks:
                t = block_text(b)
                if t.strip():
                    first_prompt = t[:200]
                    break
        tag = "%s %s" % (sdir.name[:10], (first_prompt or "no-prompt")[:50])
        for b in blocks:
            if not isinstance(b, dict):
                continue
            btype = str(b.get("type", ""))
            if "tool" in btype.lower() or b.get("toolName"):
                tool = b.get("toolName", btype)
                failed, how = _tool_failed(b)
                if not failed:
                    continue
                stderr = str(b.get("stderr", "") or "")
                out = block_text(b)
                exit_code = b.get("exitCode", b.get("exit_code", None))
                sig = _sig_line(stderr) or _sig_line(out)
                title = "fb tool %s failed [%s]: %s (%s)" % (
                    tool, how, sig[:60], tag[:40])
                body = ("FREEBUFF SESSION %s\nTOOL: %s\nEXIT: %s\nSTDERR: %s\n"
                        "OUTPUT: %s" % (
                            sdir.name, tool, exit_code, stderr[:1500],
                            out[:2000]))[:4000]
                kind = "error_fix"
            elif variant == "ai":
                t = block_text(b)
                if not t:
                    continue
                m = FIX_DONE_RE.search(t)
                if not m or not EVID_RE.search(t):
                    continue
                i = max(0, m.start() - 1200)
                matchline = t[m.start():m.start() + 80].replace("\n", " ")
                title = "fb fix narrative: %s (%s)" % (
                    matchline[:70], tag[:35])
                body = ("FREEBUFF SESSION %s\n%s" % (sdir.name, t[i:i + 2800]))[:4000]
                kind = "error_fix"
            else:
                continue
            if len(body) < 40:
                dropped.append((sdir.name, "too-short", title[:60]))
                continue
            if norm_title(title) in titles:
                dropped.append((sdir.name, "dup", title[:60]))
                continue
            titles.add(norm_title(title))
            records.append({"source": "fb#%s" % sdir.name[:10],
                            "item_id": "fb#%s-%d" % (sdir.name[:10],
                                                     len(records)),
                            "title": title[:120], "body": body, "kind": kind})
    return n_msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--session", default=None)
    args = ap.parse_args()
    titles = load_titles()
    records, dropped = [], []
    sdirs = sorted(CHATS.iterdir()) if CHATS.exists() else []
    if args.session:
        sdirs = [CHATS / args.session]
    for sdir in sdirs:
        if not sdir.is_dir():
            continue
        fp = sdir / "chat-messages.json"
        if not fp.exists():
            continue
        try:
            n = harvest_session(sdir, titles, records, dropped)
            print("session %s msgs=%d rows=%d" % (
                sdir.name, n, len(records)))
        except Exception as e:
            dropped.append((sdir.name, "SESSION-ERR", str(e)[:80]))
            continue
    kinds = {}
    for r in records:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print("HARVEST=%d %s" % (len(records), json.dumps(kinds)))
    print("DROPPED=%d" % len(dropped))
    if args.apply:
        sys.path.insert(0, str(ROOT / "src"))
        from CORE.sentinel_enforcer import permit_here
        with permit_here("bake freebuff harvest output"):
            with open(OUT_JSONL, "w", encoding="utf-8") as fh:
                for r in records:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            with open(OUT_REPORT, "w", encoding="utf-8") as fh:
                for f, i, w in dropped:
                    fh.write("%s\t%s\t%s\n" % (f, i, w))
        print("WROTE %s + %s" % (OUT_JSONL.name, OUT_REPORT.name))


if __name__ == "__main__":
    main()
