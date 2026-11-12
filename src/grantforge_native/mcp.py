"""MCP over stdio: the interop surface.

A local client (Kilo, opencode, Freebuff, Claude Desktop) spawns
`grantforge.exe mcp` and gets grant work as tools. The transport is
line-delimited JSON-RPC 2.0 — MCP's stdio shape, no Content-Length framing.

**stdout carries JSON-RPC and nothing else.** One stray `print` corrupts the
stream and the client marks the server failed for the whole session, which is
indistinguishable from a crash. Every diagnostic therefore goes to stderr, and
the test suite asserts stdout parses as JSON on every line.
"""
from __future__ import annotations

import json
import sys
import traceback

from . import __version__
from . import errors
from . import grants
from .llm import LLMError, LocalLLM

PROTOCOL_VERSION = "2024-11-05"
_MISSING = object()


def log(message):
    sys.stderr.write("[grantforge] %s\n" % message)
    sys.stderr.flush()


def _llm(offline, base=None):
    return None if offline else LocalLLM(base=base)


TOOLS = [
    {
        "name": "grantforge_doctor",
        "description": "Probe the local model endpoint: URL, models, chosen model, round trip.",
        "inputSchema": {"type": "object", "properties": {"base": {"type": "string"}}},
    },
    {
        "name": "grantforge_assess",
        "description": ("Assess an applicant against an RFP. Deadline, year cap, citizenship and "
                        "prior mechanism are decided by rules; textual criteria only count when "
                        "the model quotes the RFP verbatim, otherwise they are `unjudged`."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "rfp": {"type": ["object", "string"], "description": "RFP dict or path"},
                "profile": {"type": ["object", "string"], "description": "applicant dict or path"},
                "offline": {"type": "boolean", "default": False},
                "base": {"type": "string", "description": "override the model endpoint"},
            },
            "required": ["rfp", "profile"],
        },
    },
    {
        "name": "grantforge_draft",
        "description": ("Draft one proposal section from the RFP and profile, then scan it for "
                        "numbers and placeholders the sources cannot support."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "rfp": {"type": ["object", "string"]},
                "profile": {"type": ["object", "string"]},
                "section": {"type": "string"},
                "references": {"type": "string", "default": ""},
                "max_tokens": {"type": "integer", "default": 900},
                "base": {"type": "string"},
            },
            "required": ["rfp", "profile", "section"],
        },
    },
    {
        "name": "grantforge_review",
        "description": "Deterministic review: required sections, word limits, placeholders, unsourced numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal": {"type": ["object", "string"]},
                "rfp": {"type": ["object", "string"]},
                "profile": {"type": ["object", "string"]},
            },
            "required": ["proposal", "rfp"],
        },
    },
    {
        "name": "grantforge_sections",
        "description": ("Every proposal section the RFP declares, with its word limit and "
                        "whether the limit makes it encodable; names sections the RFP leaves "
                        "unbounded rather than inventing a set."),
        "inputSchema": {
            "type": "object",
            "properties": {"rfp": {"type": ["object", "string"]}},
            "required": ["rfp"],
        },
    },
    {
        "name": "grantforge_therapy",
        "description": ("The therapy button: run the live checks and return an ordered "
                        "treatment plan (cause, prescription, evidence needed, recheck "
                        "command per defect). Pass a proposal to treat draft defects too."),
        "inputSchema": {"type": "object", "properties": {
            "rfp": {"type": "string"},
            "profile": {"type": "string"},
            "proposal": {"type": "string"},
            "offline": {"type": "boolean"},
            "base": {"type": "string"},
        }, "required": ["rfp", "profile"]},
    },
    {
        "name": "grantforge_harness",
        "description": ("The operating contract an agent should follow, as data: ordered "
                        "calls with stopping conditions, exit codes, and the next action "
                        "for every error code. Call this before planning a multi-step run."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "grantforge_capabilities",
        "description": ("The seam registry this build declares: rule names, tool names, error "
                        "codes and the extension point for each — evolution enumerable at runtime."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "grantforge_verify",
        "description": "Recompute a sealed artefact's sha256 and report a tamper or a missing seal.",
        "inputSchema": {
            "type": "object",
            "properties": {"artefact": {"type": ["object", "string"]}},
            "required": ["artefact"],
        },
    },
]


def capabilities():
    """The seam registry: what this build declares, and where a new thing attaches."""
    return {
        "version": __version__,
        "rules": [rule.__name__.lstrip("_") for rule in grants.RULES],
        "tools": [tool["name"] for tool in TOOLS],
        "error_codes": sorted(errors.ERRORS),
        "verdicts": list(grants.VERDICTS),
        "statuses": ["READY", "GAPS", "BLIND"],
        "seams": {
            "rules": "grants.RULES — append a function returning the same row shape",
            "sections": "rfp.sections — the RFP declares its own section set",
            "tools": "mcp.TOOLS plus a branch in call_tool",
            "errors": "errors.ERRORS — a new failure is a new code plus its remedy",
            "seals": "grants.seal/verify_seal — content-addressed, recomputed on verify",
        },
    }


def call_tool(name, arguments):
    """Dispatch one tool. Raises nothing the client cannot read: errors are values."""
    arguments = arguments or {}
    if name == "grantforge_doctor":
        return _llm(False, arguments.get("base")).probe()
    if name == "grantforge_assess":
        model = _llm(bool(arguments.get("offline")), arguments.get("base"))
        return grants.assess(arguments["rfp"], arguments["profile"], llm=model)
    if name == "grantforge_draft":
        model = _llm(False, arguments.get("base"))
        return grants.draft(arguments["rfp"], arguments["profile"], arguments["section"],
                            model, max_tokens=int(arguments.get("max_tokens", 900)),
                            references=arguments.get("references", ""))
    if name == "grantforge_review":
        return grants.review(arguments["proposal"], arguments["rfp"],
                             arguments.get("profile") or {})
    if name == "grantforge_sections":
        return grants.section_index(arguments["rfp"])
    if name == "grantforge_capabilities":
        return capabilities()
    if name == "grantforge_therapy":
        from . import therapy
        model = _llm(bool(arguments.get("offline")), arguments.get("base"))
        return therapy.diagnose(arguments["rfp"], arguments["profile"],
                                proposal=arguments.get("proposal"), llm=model)
    if name == "grantforge_harness":
        from . import harness
        return harness.contract()
    if name == "grantforge_verify":
        ok, detail = grants.verify_seal(grants.load_json(arguments["artefact"]))
        return {"ok": ok, "detail": detail}
    raise KeyError("unknown tool %r" % name)


def handle(request, offline=False):
    """One request dict in, one response dict out — or None for a notification."""
    if not isinstance(request, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "invalid request"}}
    method = request.get("method")
    request_id = request.get("id", _MISSING)
    if request_id is _MISSING:
        return None  # notification: initialized, cancelled, ...
    try:
        if method == "initialize":
            result = {"protocolVersion": PROTOCOL_VERSION,
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "grantforge", "version": __version__}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = request.get("params") or {}
            name = params.get("name")
            arguments = dict(params.get("arguments") or {})
            if offline and "offline" not in arguments:
                arguments["offline"] = True
            try:
                payload = call_tool(name, arguments)
                result = {"content": [{"type": "text", "text": json.dumps(payload)}],
                          "isError": False}
            except (LLMError, KeyError, ValueError) as exc:
                # The calling model gets the code and the remedy, not a traceback: a
                # tool result it can act on is the difference between a retry that
                # works and one that repeats the same request.
                entry = errors.explain(exc)
                errors.record(entry)
                result = {"content": [{"type": "text", "text": json.dumps(entry)}],
                          "isError": True}
        elif method == "ping":
            result = {}
        else:
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32601, "message": "method not found: %s" % method}}
    except Exception as exc:  # a handler bug must not kill the session
        log("handler failed: %s" % traceback.format_exc().strip().splitlines()[-1])
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32603, "message": "internal error: %s" % exc}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(stdin=None, stdout=None, offline=False, base=None):
    """Read JSON-RPC lines until EOF. Returns the number of requests answered."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    answered = 0
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError as exc:
            _ = exc
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "parse error"}}
        else:
            response = handle(request, offline=offline)
        if response is None:
            continue
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()
        answered += 1
    log("mcp: answered %d request(s)" % answered)
    return answered
