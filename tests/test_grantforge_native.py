"""Tests for grantforge_native — the claims each module makes, checked.

The fake server is a fixture written to the shape the *producer* actually
returns (llama-server's `{"models":[{"name":...}]}` and OpenAI's
`{"data":[{"id":...}]}`), captured from the live endpoints in this session —
not from what I assumed they return.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from grantforge_native import case as case_mod  # noqa: E402
from grantforge_native import cli as cli_mod
from grantforge_native import errors, grants, harness, llm as llm_mod, mcp, therapy  # noqa: E402


# -- fake endpoint ----------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep pytest output clean
        return

    def _send(self, status, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send(200, self.server.models_payload)
        else:
            self._send(404, {"error": "no such path"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        self.server.requests.append(json.loads(raw.decode() or "{}"))
        if not self.server.chat_replies:
            self._send(500, {"error": "no scripted reply"})
            return
        status, payload = self.server.chat_replies.pop(0)
        self._send(status, payload)


class FakeServer:
    def __init__(self, models_payload=None, chat_replies=None):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.models_payload = models_payload or {"data": [{"id": "fake-1"}]}
        self.httpd.chat_replies = list(chat_replies or [])
        self.httpd.requests = []
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        self.base = "http://%s:%d" % (host, port)

    @property
    def requests(self):
        return self.httpd.requests

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def reply(text, finish="stop"):
    return (200, {"choices": [{"index": 0, "finish_reason": finish,
                               "message": {"role": "assistant", "content": text}}],
                  "usage": {"total_tokens": 7},
                  "model": "fake-1"})


@pytest.fixture
def server():
    made = []

    def build(models_payload=None, chat_replies=None):
        instance = FakeServer(models_payload, chat_replies)
        made.append(instance)
        return instance

    yield build
    for instance in made:
        instance.close()


class FakeLLM:
    """A model that answers with whatever the test scripted, no HTTP."""

    model = "fake-1"

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []
        self.budgets = []
        self.last = {}

    def json_object(self, messages, required, **kwargs):
        self.prompts.append(messages)
        return self.answers.pop(0)

    def complete(self, messages, **kwargs):
        self.prompts.append(messages)
        self.budgets.append(kwargs.get("max_tokens"))
        answer = self.answers.pop(0)
        return json.dumps(answer) if isinstance(answer, dict) else answer


# -- endpoint normalisation and discovery -----------------------------------

@pytest.mark.parametrize("given", [
    "127.0.0.1:8081",
    "http://127.0.0.1:8081",
    "http://127.0.0.1:8081/",
    "http://127.0.0.1:8081/v1",
    "http://127.0.0.1:8081/v1/chat/completions",
])
def test_normalise_accepts_every_form_a_config_holds(given):
    root, chat = llm_mod.normalise(given)
    assert root == "http://127.0.0.1:8081/v1"
    assert chat == "http://127.0.0.1:8081/v1/chat/completions"


@pytest.mark.parametrize("payload,expected", [
    ({"models": [{"name": "Qwen3-4B.gguf", "model": "llama"}]}, ["Qwen3-4B.gguf"]),
    ({"data": [{"id": "gpt-oss:20b"}]}, ["gpt-oss:20b"]),
    ({"data": [{"id": "a"}, {"id": "a"}], "method": "x"}, ["a"]),
])
def test_models_reads_both_shapes(server, payload, expected):
    host = server(models_payload=payload)
    assert llm_mod.LocalLLM(base=host.base).models() == expected


def test_models_unreachable_is_named_not_empty(tmp_path):
    client = llm_mod.LocalLLM(base="http://127.0.0.1:9", timeout=0.5, retries=0)
    with pytest.raises(llm_mod.LLMUnavailable) as excinfo:
        client.models()
    assert "could not list models" in str(excinfo.value)


# -- transport behaviour ----------------------------------------------------

def test_5xx_is_retried_and_attempts_are_recorded(server):
    host = server(chat_replies=[(503, {"error": "loading"}), reply("ok")])
    client = llm_mod.LocalLLM(base=host.base, retries=2, backoff=0.0)
    assert client.complete([{"role": "user", "content": "hi"}]) == "ok"
    assert client.last["attempts"] == 2
    assert len(host.requests) == 2


def test_400_is_not_retried(server):
    host = server(chat_replies=[(400, {"error": "bad request"})])
    client = llm_mod.LocalLLM(base=host.base, retries=3, backoff=0.0)
    with pytest.raises(llm_mod.LLMUnavailable) as excinfo:
        client.complete([{"role": "user", "content": "hi"}])
    assert "HTTP 400" in str(excinfo.value)
    assert len(host.requests) == 1, "a 400 re-sent is the same wrong answer twice"


def test_json_object_repairs_once_and_succeeds(server):
    host = server(chat_replies=[reply("Sure, here you go."), reply('{"verdict": "eligible"}')])
    client = llm_mod.LocalLLM(base=host.base, retries=0, backoff=0.0)
    assert client.json_object([{"role": "user", "content": "q"}], ("verdict",)) == {
        "verdict": "eligible"}
    assert client.last["json_attempts"] == 2
    assert "rejected" in host.requests[-1]["messages"][-1]["content"]


def test_json_object_gives_up_with_both_defects_named(server):
    host = server(chat_replies=[reply("no json here"), reply("still no json")])
    client = llm_mod.LocalLLM(base=host.base, retries=0, backoff=0.0)
    with pytest.raises(llm_mod.LLMBadReply) as excinfo:
        client.json_object([{"role": "user", "content": "q"}], ("verdict",))
    message = str(excinfo.value)
    assert "after 2 attempt(s)" in message
    assert message.count("no JSON object") == 2
    assert len(host.requests) == 2


def test_truncation_raises_the_budget_instead_of_blaming_the_model(server):
    host = server(chat_replies=[reply('{"a": 1', finish="length"), reply('{"a": 1}')])
    client = llm_mod.LocalLLM(base=host.base, retries=0, backoff=0.0)
    assert client.json_object([{"role": "user", "content": "q"}], ("a",),
                              max_tokens=10) == {"a": 1}
    assert [request["max_tokens"] for request in host.requests] == [10, 30]
    assert client.last["json_attempts"] == 2


def test_double_truncation_is_reported_as_truncation_not_bad_json(server):
    host = server(chat_replies=[reply('{"a": 1', finish="length"),
                                reply('{"a": 1', finish="length")])
    client = llm_mod.LocalLLM(base=host.base, retries=0, backoff=0.0)
    with pytest.raises(llm_mod.LLMBadReply) as excinfo:
        client.json_object([{"role": "user", "content": "q"}], ("a",), max_tokens=10)
    message = str(excinfo.value)
    assert "truncated at a 30-token budget" in message
    assert "no JSON object" not in message


def test_json_contract_is_a_leading_system_turn(server):
    host = server(chat_replies=[reply('{"a": 1}')])
    client = llm_mod.LocalLLM(base=host.base, retries=0, backoff=0.0)
    client.json_object([{"role": "user", "content": "q"}], ("a",))
    messages = host.requests[0]["messages"]
    assert messages[0]["role"] == "system" and "JSON object" in messages[0]["content"]


def test_extract_json_handles_fences_and_braces_inside_strings():
    text = 'prelude ```json\n{"a": "a } brace", "b": {"c": 1}}\n``` trailing'
    assert llm_mod.extract_json(text) == {"a": "a } brace", "b": {"c": 1}}


# -- rules ------------------------------------------------------------------

BASE_RFP = {
    "id": "T-1", "deadline": "2026-12-15", "max_years_post_phd": 10,
    "citizenship": "us_citizen", "mechanism": "R21", "no_prior_mechanism": True,
    "criteria_text": "Applicants must document institutional support for the project.",
}
BASE_PROFILE = {"name": "A", "years_post_phd": 6, "citizenship": "us_citizen",
                "prior_mechanisms": ["R01"]}


def test_rules_pass_for_an_eligible_applicant():
    result = grants.assess(BASE_RFP, BASE_PROFILE, today="2026-11-05")
    assert result["verdict"] == "eligible"
    assert result["engine"] == "rules"
    assert {row["rule"] for row in result["rows"]} == {
        "deadline", "max_years_post_phd", "citizenship", "no_prior_mechanism"}


@pytest.mark.parametrize("rfp_patch,profile_patch,rule", [
    ({"deadline": "2026-01-01"}, {}, "deadline"),
    ({"max_years_post_phd": 3}, {}, "max_years_post_phd"),
    ({"citizenship": "permanent_resident"}, {}, "citizenship"),
    ({}, {"prior_mechanisms": ["R21"]}, "no_prior_mechanism"),
])
def test_each_rule_can_fail_and_says_which(rfp_patch, profile_patch, rule):
    rfp = dict(BASE_RFP, **rfp_patch)
    profile = dict(BASE_PROFILE, **profile_patch)
    result = grants.assess(rfp, profile, today="2026-11-05")
    assert result["verdict"] == "ineligible"
    failing = [row for row in result["rows"] if row["verdict"] == "ineligible"]
    assert [row["rule"] for row in failing] == [rule]


def test_missing_profile_fact_is_unjudged_not_eligible():
    result = grants.assess(BASE_RFP, {"name": "A"}, today="2026-11-05")
    assert result["verdict"] == "unjudged"
    assert any(row["verdict"] == "unjudged" for row in result["rows"])


# -- the admissibility rule -------------------------------------------------

def test_model_verdict_with_a_real_quote_is_accepted():
    quote = "Applicants must document institutional support for the project."
    model = FakeLLM([{"verdict": "eligible", "quote": quote}])
    rfp = dict(BASE_RFP, text_criteria=[{"id": "support", "question": "support?"}])
    result = grants.assess(rfp, BASE_PROFILE, llm=model, today="2026-11-05")
    assert result["engine"] == "rules+llm"
    assert result["llm"]["accepted"] == 1
    assert result["llm"]["model"] == "fake-1"
    row = [r for r in result["rows"] if r["criterion"] == "support"][0]
    assert row["verdict"] == "eligible"
    assert row["evidence"].startswith("quote: verbatim span found at offset")
    assert row["pin"]["quote"] == quote.lower()   # the span, not the word "verbatim"


def test_invented_quote_is_downgraded_to_unjudged():
    model = FakeLLM([{"verdict": "eligible",
                      "quote": "The applicant has exceptional institutional support."}])
    rfp = dict(BASE_RFP, text_criteria=[{"id": "support", "question": "support?"}])
    result = grants.assess(rfp, BASE_PROFILE, llm=model, today="2026-11-05")
    row = [r for r in result["rows"] if r["criterion"] == "support"][0]
    assert row["verdict"] == "unjudged"
    assert "does not appear in the RFP" in row["evidence"]
    assert result["llm"]["downgraded"] == 1
    assert result["verdict"] == "unjudged"


def test_short_quote_cannot_prove_anything():
    model = FakeLLM([{"verdict": "eligible", "quote": "the"}])
    rfp = dict(BASE_RFP, text_criteria=[{"id": "support", "question": "support?"}])
    result = grants.assess(rfp, BASE_PROFILE, llm=model, today="2026-11-05")
    row = [r for r in result["rows"] if r["criterion"] == "support"][0]
    assert row["verdict"] == "unjudged" and "shorter than" in row["evidence"]


def test_textual_criteria_without_a_model_are_unjudged():
    rfp = dict(BASE_RFP, text_criteria=[{"id": "support", "question": "support?"}])
    result = grants.assess(rfp, BASE_PROFILE, today="2026-11-05")
    assert result["verdict"] == "unjudged"
    assert "no model configured" in result["rows"][-1]["evidence"]


# -- drafting ---------------------------------------------------------------

DRAFT_RFP = dict(BASE_RFP, sections={"significance": {"max_words": 60}})


def test_draft_flags_a_number_no_source_contains():
    model = FakeLLM([{"text": "Our pilot reached 42% enrolment across 3 clinics.",
                      "notes": "none"}])
    result = grants.draft(DRAFT_RFP, BASE_PROFILE, "significance", model)
    assert result["grade"] == "unsupported"
    assert any(f["code"] == "UNSUPPORTED_NUMBER" and "42" in f["detail"]
               for f in result["findings"])


def test_draft_accepts_prose_because_prose_is_the_deliverable():
    prose = "**Significance.** Demo University proposes a point-of-care optical sensing pilot."
    model = FakeLLM([prose])
    result = grants.draft(DRAFT_RFP, BASE_PROFILE, "significance", model)
    assert result["format"] == "prose"
    assert result["text"] == prose and result["grade"] == "supported"


def test_draft_with_only_sourced_facts_is_supported():
    text = "Demo University proposes a pilot within 6 years of the terminal degree."
    model = FakeLLM([{"text": text, "notes": "none"}])
    result = grants.draft(DRAFT_RFP, BASE_PROFILE, "significance", model)
    assert result["grade"] == "supported", result["findings"]
    assert result["words"] == len(text.split())


def test_draft_without_a_model_refuses_rather_than_inventing():
    with pytest.raises(ValueError) as excinfo:
        grants.draft(DRAFT_RFP, BASE_PROFILE, "significance", None)
    assert "requires a model" in str(excinfo.value)


# -- review and sealing -----------------------------------------------------

TWO_SECTION_RFP = dict(BASE_RFP, sections={"significance": {"max_words": 60},
                                          "approach": {"max_words": 40}})


def test_review_finds_every_defect_it_can_see():
    proposal = {"sections": {
        "significance": {"text": "We will enrol [N] patients. " + "word " * 70},
        "approach": {"text": ""},
    }}
    result = grants.review(proposal, TWO_SECTION_RFP, BASE_PROFILE)
    codes = [finding["code"] for finding in result["findings"]]
    assert result["ok"] is False
    assert "PLACEHOLDER" in codes and "OVER_LIMIT" in codes and "EMPTY_SECTION" in codes
    assert "UNKNOWN_SECTION" not in codes


def test_review_of_a_complete_section_passes():
    proposal = {"sections": {"significance": {"text": "A short sourced paragraph."},
                             "approach": {"text": "A short sourced approach."}}}
    result = grants.review(proposal, DRAFT_RFP, BASE_PROFILE)
    assert result["ok"] is True and result["errors"] == 0


def test_seal_is_stable_and_detects_tampering():
    body = {"rfp": "T-1", "verdict": "eligible"}
    sealed = grants.seal(body)
    assert grants.verify_seal(sealed)[0] is True
    tampered = json.loads(json.dumps(sealed))
    tampered["verdict"] = "ineligible"
    ok, detail = grants.verify_seal(tampered)
    assert ok is False and "mismatch" in detail
    assert grants.verify_seal({"verdict": "eligible"}) == (False, "no seal present")


# -- MCP and CLI as a client sees them --------------------------------------

def _run(args, stdin=None, env_extra=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "grantforge_native"] + args,
                          input=stdin, capture_output=True, text=True, env=env,
                          timeout=120, cwd=str(ROOT))


def test_mcp_stdio_is_json_only_and_answers_every_request():
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "grantforge_review",
            "arguments": {"proposal": {"sections": {}}, "rfp": DRAFT_RFP}}}),
        "{not json",
    ]
    run = _run(["mcp", "--offline"], stdin="\n".join(lines) + "\n")
    assert run.returncode == 0, run.stderr
    responses = [json.loads(line) for line in run.stdout.splitlines() if line.strip()]
    assert [r["id"] for r in responses] == [1, 2, 3, None]
    assert responses[0]["result"]["serverInfo"]["name"] == "grantforge"
    assert len(responses[1]["result"]["tools"]) == len(mcp.TOOLS) == 9
    review_payload = json.loads(responses[2]["result"]["content"][0]["text"])
    assert review_payload["ok"] is False
    assert responses[3]["error"]["code"] == -32700


def test_cli_offline_assess_is_a_successful_run(tmp_path):
    rfp_path = tmp_path / "rfp.json"
    profile_path = tmp_path / "profile.json"
    rfp_path.write_text(json.dumps(BASE_RFP), encoding="utf-8")
    profile_path.write_text(json.dumps(BASE_PROFILE), encoding="utf-8")
    run = _run(["assess", str(rfp_path), str(profile_path), "--offline", "--json"])
    assert run.returncode == 0, run.stderr
    payload = json.loads(run.stdout)
    assert payload["verdict"] == "eligible" and payload["engine"] == "rules"


def test_cli_review_failure_exits_one(tmp_path):
    proposal = tmp_path / "proposal.json"
    rfp = tmp_path / "rfp.json"
    proposal.write_text(json.dumps({"sections": {}}), encoding="utf-8")
    rfp.write_text(json.dumps(DRAFT_RFP), encoding="utf-8")
    run = _run(["review", str(proposal), str(rfp), "--json"])
    assert run.returncode == 1
    assert json.loads(run.stdout)["ok"] is False


def test_cli_doctor_on_a_dead_endpoint_exits_three():
    run = _run(["doctor", "--base", "http://127.0.0.1:9", "--json"],
               env_extra={"GRANTFORGE_LLM_TIMEOUT": "0.5"})
    assert run.returncode == 3
    # The contract changed from a bare message to a code plus its remedy: a failure
    # the operator cannot act on is the defect this module was added to remove.
    assert "[model_unreachable]" in run.stderr
    assert "remedy:" in run.stderr


# ── the error taxonomy: every failure carries a code, a remedy and a record ──

@pytest.mark.parametrize("text,expected", [
    ("LLMUnavailable: no model endpoint answered at http://127.0.0.1:9", "model_unreachable"),
    ("LLMBadReply: no usable JSON object after 2 attempt(s): reply was prose",
     "model_bad_reply"),
    ("LLMBadReply: reply hit finish_reason=length after 900 tokens", "model_truncated"),
    ("ValueError: draft requires a model; use --offline for rules only",
     "draft_needs_model"),
    ("No such file or directory: 'rfp.json'", "input_unreadable"),
    ("something nobody has classified", "unknown"),
])
def test_every_failure_maps_to_one_code(text, expected):
    assert errors.code_for(text) == expected


def test_explain_carries_the_remedy_not_just_the_message():
    entry = errors.explain(ValueError("draft requires a model"))
    assert entry["ok"] is False
    assert entry["code"] == "draft_needs_model"
    assert entry["remedy"] and entry["remedy"] != entry["detail"]


def test_ledger_roundtrip_is_append_only(tmp_path):
    target = tmp_path / "errors.jsonl"
    first = errors.explain("No such file or directory: 'a.json'")
    second = errors.explain("LLMUnavailable: no model endpoint answered")
    assert errors.record(first, path=target) == str(target)
    errors.record(second, path=target)
    entries = errors.read_ledger(target)
    assert [e["code"] for e in entries] == ["input_unreadable", "model_unreachable"]


def test_ledger_write_failure_does_not_raise(tmp_path):
    # A diagnostics write must never be the thing that fails the run.
    blocked = tmp_path / "as-a-directory" / "errors.jsonl"
    (tmp_path / "as-a-directory").write_text("", encoding="utf-8")
    assert errors.record({"code": "unknown", "detail": "x"}, path=blocked)


def test_cli_records_the_failure_and_errors_lists_it(tmp_path):
    ledger = tmp_path / "cli-errors.jsonl"
    run = _run(["doctor", "--base", "http://127.0.0.1:9", "--json"],
               env_extra={"GRANTFORGE_LLM_TIMEOUT": "0.5",
                          "GRANTFORGE_ERRLOG": str(ledger)})
    assert run.returncode == 3
    assert ledger.exists(), "the CLI did not write the ledger it was pointed at"
    listed = _run(["errors", "--json"], env_extra={"GRANTFORGE_ERRLOG": str(ledger)})
    assert listed.returncode == 0, listed.stderr
    payload = json.loads(listed.stdout)
    assert payload["entries"][-1]["code"] == "model_unreachable"
    assert payload["entries"][-1]["remedy"]


# ── sections come from the RFP; verdicts carry a pin (ELIZA adopted natively) ──

def test_section_index_reads_the_rfp_not_a_hardcoded_list():
    rfp = {"id": "R-1", "sections": {"significance": {"max_words": 300},
                                     "innovation": {},
                                     "approach": "300 words"}}
    index = grants.section_index(rfp)
    assert [row["section"] for row in index["sections"]] == \
        ["approach", "innovation", "significance"]
    assert index["encodable"] == 1
    assert index["unbounded"] == ["approach", "innovation"]
    states = {row["section"]: row["state"] for row in index["sections"]}
    assert states == {"approach": "unreadable", "innovation": "unbounded",
                      "significance": "bounded"}


def test_drafting_an_undeclared_section_is_refused_by_name():
    rfp = {"sections": {"significance": {"max_words": 300}}}
    with pytest.raises(ValueError) as caught:
        grants.draft(rfp, {"name": "A"}, "budget", object())
    assert "not declared in the RFP" in str(caught.value)
    assert "significance" in str(caught.value)


def test_every_verdict_row_carries_a_recheckable_pin():
    rfp = {"id": "R-2", "deadline": "2099-01-01", "sections": {},
           "text_criteria": [{"id": "fit", "question": "Is it a fit?"}]}
    profile = {"name": "A", "years_post_phd": 2}
    result = grants.assess(rfp, profile, llm=None)
    assert result["status"] == "GAPS"          # the text criterion was not read
    assert result["rows"]
    for row in result["rows"]:
        assert row["pin"]["kind"] in ("rule", "quote", "none")
    rule_rows = [r for r in result["rows"] if r["pin"]["kind"] == "rule"]
    assert rule_rows and all(r["pin"]["recheck"].startswith("grantforge assess")
                             for r in rule_rows)


def test_ready_only_when_nothing_is_unjudged():
    rfp = {"id": "R-3", "deadline": "2099-01-01", "sections": {}}
    profile = {"name": "A", "years_post_phd": 2}
    assert grants.assess(rfp, profile, llm=None)["status"] == "READY"
    assert grants.assess({"id": "R-4", "sections": {}}, profile, llm=None)["status"] == "BLIND"


def test_capabilities_enumerates_every_seam():
    caps = mcp.capabilities()
    assert "rule_deadline" in caps["rules"] and "grantforge_assess" in caps["tools"]
    assert set(caps["error_codes"]) >= {"model_unreachable", "input_unreadable"}
    assert set(caps["seams"]) == {"rules", "sections", "tools", "errors", "seals"}


def test_cli_sections_and_blind_exit_code(tmp_path):
    rfp = tmp_path / "rfp.json"
    rfp.write_text(json.dumps({"id": "R-5", "sections": {"approach": {"max_words": 400}}}),
                   encoding="utf-8")
    listed = _run(["sections", str(rfp), "--json"])
    assert listed.returncode == 0, listed.stderr
    assert json.loads(listed.stdout)["sections"][0]["section"] == "approach"

    blind = tmp_path / "blind.json"
    blind.write_text(json.dumps({"id": "R-6", "sections": {}}), encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"name": "A"}), encoding="utf-8")
    run = _run(["assess", str(blind), str(profile), "--offline", "--json"])
    assert run.returncode == 2, run.stdout + run.stderr
    assert json.loads(run.stdout)["status"] == "BLIND"


# ── forensic custody: the chain records what was read and breaks where it is edited ──

def test_case_hashes_inputs_and_verifies(tmp_path):
    rfp = tmp_path / "rfp.json"
    profile = tmp_path / "profile.json"
    rfp.write_text(json.dumps(DRAFT_RFP), encoding="utf-8")
    profile.write_text(json.dumps(BASE_PROFILE), encoding="utf-8")
    case_dir = tmp_path / "case-1"
    manifest = case_mod.init_case(case_dir, {"rfp": rfp, "profile": profile}, case_id="C-1")
    assert manifest["case_id"] == "C-1"
    assert [item["name"] for item in manifest["inputs"]] == ["profile", "rfp"]
    assert all(len(item["sha256"]) == 64 for item in manifest["inputs"])
    verified = case_mod.verify_case(case_dir)
    assert verified["ok"] is True and verified["entries"] == 1


def test_editing_a_custody_entry_breaks_the_chain_at_that_link(tmp_path):
    rfp = tmp_path / "rfp.json"
    rfp.write_text("{}", encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "case-2"
    case_mod.init_case(case_dir, {"rfp": rfp, "profile": profile})
    case_mod.record(case_dir, "assessment", {"verdict": "eligible"})
    ledger = case_dir / "custody.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[1])
    entry["payload_digest"] = "0" * 64
    lines[1] = json.dumps(entry)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = case_mod.verify_case(case_dir)
    assert result["ok"] is False
    assert "altered" in result["detail"] or "hash mismatch" in result["detail"]


def test_a_case_refuses_to_overwrite_a_live_chain(tmp_path):
    rfp = tmp_path / "rfp.json"
    profile = tmp_path / "profile.json"
    rfp.write_text("{}", encoding="utf-8")
    profile.write_text("{}", encoding="utf-8")
    case_dir = tmp_path / "case-3"
    case_mod.init_case(case_dir, {"rfp": rfp, "profile": profile})
    with pytest.raises(ValueError) as caught:
        case_mod.init_case(case_dir, {"rfp": rfp, "profile": profile})
    assert "refusing to overwrite" in str(caught.value)


def test_cli_assess_writes_into_the_case_and_verify_reads_it_back(tmp_path):
    rfp = tmp_path / "rfp.json"
    profile = tmp_path / "profile.json"
    rfp.write_text(json.dumps(BASE_RFP), encoding="utf-8")
    profile.write_text(json.dumps(BASE_PROFILE), encoding="utf-8")
    case_dir = tmp_path / "case-4"
    opened = _run(["case", "init", str(case_dir), str(rfp), str(profile), "--id", "C-4", "--json"])
    assert opened.returncode == 0, opened.stderr
    run = _run(["assess", str(rfp), str(profile), "--offline", "--json",
                "--case", str(case_dir)])
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["custody"]
    checked = _run(["case", "verify", str(case_dir), "--json"])
    assert checked.returncode == 0, checked.stdout + checked.stderr
    payload = json.loads(checked.stdout)
    assert payload["ok"] is True and payload["entries"] == 2


def test_cli_case_verify_on_a_missing_case_exits_one(tmp_path):
    run = _run(["case", "verify", str(tmp_path / "nothing-here"), "--json"])
    assert run.returncode == 1
    assert json.loads(run.stdout)["ok"] is False


def test_an_accepted_quote_pin_carries_the_span_not_the_word_verbatim():
    class FakeLLM:
        model = "fake-model"
        last = {}

        def json_object(self, messages, required=()):
            return {"verdict": "eligible",
                    "quote": "Awards support early career investigators."}

    # The quotable sentence lives in the RFP's own text, not in the question asked.
    rfp = {"id": "R-7", "summary": "Awards support early career investigators.",
           "sections": {},
           "text_criteria": [{"id": "fit", "question": "Is this a fit?"}]}
    row = [r for r in grants.assess(rfp, {"name": "A"}, llm=FakeLLM())["rows"]
           if r["criterion"] == "fit"][0]
    assert row["verdict"] == "eligible"
    pin = row["pin"]
    assert pin["kind"] == "quote"
    assert pin["quote"] != "verbatim"
    source = grants.flat_text(rfp)
    assert pin["quote"] in source
    assert source.find(pin["quote"]) == pin["offset"]
    assert "offset" in row["evidence"]


def test_a_quote_taken_from_the_question_is_not_evidence():
    """The question is ours; quoting it proves we asked, not that the RFP decides."""
    class QuestionQuoter:
        model = "fake-model"
        last = {}

        def json_object(self, messages, required=()):
            return {"verdict": "eligible", "quote": "Is this a fit?"}

    rfp = {"id": "R-8", "summary": "General text with no decision in it.",
           "sections": {},
           "text_criteria": [{"id": "fit", "question": "Is this a fit?"}]}
    row = [r for r in grants.assess(rfp, {"name": "A"}, llm=QuestionQuoter())["rows"]
           if r["criterion"] == "fit"][0]
    assert row["verdict"] == "unjudged"
    assert "criterion text" in row["evidence"]
    assert row["pin"]["kind"] == "none"


# ── the harness: evolution pushes the agent, and every code carries its next step ──

def test_every_error_code_carries_a_next_action():
    assert set(errors.NEXT) == set(errors.ERRORS), "a code without a push is a hole"
    for code, action in errors.NEXT.items():
        assert len(action) > 30, code
    contract = harness.contract()
    assert set(contract["on_error"]) == set(errors.ERRORS)


def test_harness_sequence_names_only_real_tools_and_ends_each_step():
    contract = harness.contract()
    names = {tool["name"] for tool in mcp.TOOLS}
    assert [step["step"] for step in contract["sequence"]] == list(range(1, 8))
    for step in contract["sequence"]:
        assert step["call"] in names, step["call"]
        assert step["when"] and step["stop_if"] and step["return_evidence"]
    assert set(contract["exit_codes"]) == {"0", "1", "2", "3", "4"}
    assert contract["ledger"]["path"]


def test_cli_harness_prints_the_sequence_and_mcp_exposes_it():
    run = _run(["harness", "--json"])
    assert run.returncode == 0, run.stderr
    payload = json.loads(run.stdout)
    assert payload["sequence"][0]["call"] == "grantforge_doctor"
    assert "grantforge_harness" in [tool["name"] for tool in mcp.TOOLS]

    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "grantforge_harness", "arguments": {}}})]
    served = _run(["mcp", "--offline"], stdin="\n".join(lines) + "\n")
    assert served.returncode == 0, served.stderr
    reply = json.loads(served.stdout.splitlines()[0])
    text = json.loads(reply["result"]["content"][0]["text"])
    assert text["sequence"][1]["call"] == "grantforge_capabilities"
    assert reply["result"]["isError"] is False


# ── therapy: the button an operator presses, not a score they interpret ──

def test_therapy_offline_returns_a_plan_with_a_recheck_per_finding():
    plan = therapy.diagnose(grants.load_json(BASE_RFP), grants.load_json(BASE_PROFILE))
    assert plan["prognosis"] in {"TREATABLE", "HEALTHY", "BLIND"}
    assert plan["status"] in {"READY", "GAPS", "BLIND"}
    for item in plan["findings"]:
        for field in ("code", "where", "severity", "cause", "prescription",
                      "evidence_needed", "recheck"):
            assert item[field], (item["code"], field)
        assert item["recheck"].split()[0] == "grantforge", item["recheck"]
        assert item["recheck"].split()[1] in cli_mod.verbs(), item["recheck"]
    if plan["findings"]:
        assert plan["prognosis"] == "TREATABLE"
        assert plan["next_call"]


def test_every_treatment_is_a_triple_and_covers_the_live_statuses():
    for code, triple in therapy.TREATMENTS.items():
        assert len(triple) == 3, code
        assert all(isinstance(part, str) and len(part) > 20 for part in triple), code
    assert {"ineligible", "unjudged", "BLIND"} <= set(therapy.STATUS_TREATMENTS)
    for key, triple in therapy.STATUS_TREATMENTS.items():
        assert len(triple) == 3 and all(triple), key


def test_therapy_treats_a_draft_defect_on_the_section_that_owns_it():
    # BASE_RFP carries rules only; a sectioned RFP is what review can speak about.
    rfp = dict(BASE_RFP, sections={"approach": {"max_words": 120},
                                  "impact": {"max_words": 80}})
    required = list(rfp["sections"])
    assert required, "the fixture must declare sections"
    proposal = {"sections": {}}
    plan = therapy.diagnose(rfp, grants.load_json(BASE_PROFILE), proposal=proposal)
    codes = {item["code"] for item in plan["findings"]}
    assert "MISSING_SECTION" in codes, codes
    missing = [item for item in plan["findings"] if item["code"] == "MISSING_SECTION"]
    assert len(missing) == len(required)
    assert all(item["where"] in required for item in missing)
    assert plan["review"]["ok"] is False


def test_cli_therapy_exits_by_prognosis_and_mcp_serves_it(tmp_path):
    rfp_path = tmp_path / "rfp.json"
    rfp_path.write_text(json.dumps(BASE_RFP), encoding="utf-8")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(BASE_PROFILE), encoding="utf-8")
    BASE_RFP_PATH, BASE_PROFILE_PATH = str(rfp_path), str(profile_path)

    run = _run(["therapy", BASE_RFP_PATH, BASE_PROFILE_PATH, "--offline", "--json"])
    assert run.returncode in (0, 1), run.stderr
    plan = json.loads(run.stdout)
    assert run.returncode == (1 if plan["findings"] else 0), plan["prognosis"]

    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "grantforge_therapy", "arguments": {
                             "rfp": BASE_RFP_PATH, "profile": BASE_PROFILE_PATH,
                             "offline": True}}})]
    served = _run(["mcp", "--offline"], stdin=lines[0] + "\n")
    assert served.returncode == 0, served.stderr
    reply = json.loads(served.stdout.splitlines()[0])
    served_plan = json.loads(reply["result"]["content"][0]["text"])
    assert served_plan["prognosis"] == plan["prognosis"]
    assert reply["result"]["isError"] is False


def test_human_output_survives_a_cp1252_console(tmp_path):
    # Every module may print typographic punctuation; the boundary makes it ASCII so a
    # cp1252 terminal shows a dash instead of a replacement character.
    assert cli_mod.ascii_safe("a — b ’c’ …") == "a - b 'c' ..."
    assert cli_mod.ascii_safe("€") == "?"
    rfp_path = tmp_path / "rfp.json"
    rfp_path.write_text(json.dumps(BASE_RFP), encoding="utf-8")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(BASE_PROFILE), encoding="utf-8")
    run = _run(["therapy", str(rfp_path), str(profile_path), "--offline"])
    assert run.returncode in (0, 1), run.stdout + run.stderr
    assert run.stdout.isascii(), [line for line in run.stdout.splitlines() if not line.isascii()]
    assert "�" not in run.stdout


def test_a_missing_case_is_diagnosed_as_a_missing_case(tmp_path):
    # exit 4 with the creating command, not input_unreadable pointing at a file that
    # was never written.
    rfp_path = tmp_path / "rfp.json"
    rfp_path.write_text(json.dumps(BASE_RFP), encoding="utf-8")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(BASE_PROFILE), encoding="utf-8")
    absent = tmp_path / "case_absent"
    run = _run(["therapy", str(rfp_path), str(profile_path), "--offline",
                "--case", str(absent), "--json"])
    assert run.returncode == 4, run.stdout + run.stderr
    assert "grantforge case init" in run.stderr, run.stderr

    made = _run(["case", "init", str(absent), str(rfp_path), str(profile_path), "--json"])
    assert made.returncode == 0, made.stderr
    again = _run(["therapy", str(rfp_path), str(profile_path), "--offline",
                  "--case", str(absent), "--json"])
    assert again.returncode in (0, 1), again.stderr
    assert json.loads(again.stdout)["custody"]


def test_a_torn_ledger_line_is_counted_not_swallowed(tmp_path):
    ledger = tmp_path / "errors.jsonl"
    torn = ('{"code": "model_unreachable"}' + chr(10) + '{"code": "model_trun')
    ledger.write_text(torn, encoding="utf-8")
    entries = errors.read_ledger(str(ledger))
    assert len(entries) == 1 and entries[0]["code"] == "model_unreachable"
    assert errors.LAST_READ_DEFECTS == 1
    clean = tmp_path / "clean.jsonl"
    clean.write_text('{"code": "unknown"}' + chr(10), encoding="utf-8")
    errors.read_ledger(str(clean))
    assert errors.LAST_READ_DEFECTS == 0
