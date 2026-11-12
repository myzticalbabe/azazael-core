"""Eve + Open Interpreter integration — local model drives the architecture/mesh.

Connects Python Open Interpreter [local] and a plain OpenAI-compatible chat
path to Eve's autonomous decision engine and mesh-driving capabilities.

BUG FIX (2026-10-18, eve-verify-land lane):
  - The old bridge probed only ``/health`` on candidate ports and then ran a
    generation request against whatever was listening. On this host that server
    was llama.cpp running the embedding-only ``mxbai-embed-large-v1`` gguf, which
    answers chat requests with HTTP 500
    ``the current context does not logits computation. skipping`` (wrapped by
    Open Interpreter's litellm backend as ``litellm.InternalServerError``).
  - Probe now reads ``/v1/models`` and classifies each hosted model; embedding
    models are never used as generation targets.
  - Even when the ``interpreter`` package is unavailable (it is installed in no
    Azazael venv, production uses py315t), the bridge performs OpenAI-compatible
    chat calls directly with urllib — no litellm in the loop, so the
    logits-request failure class cannot be produced by our client.
  - When Open Interpreter IS available, its LLM is configured as an explicit
    OpenAI-compatible custom provider (``openai/<real model id>``) with
    function/vision support off and logit-related request parameters
    (``logprobs`` / ``top_logprobs`` / ``logit_bias``) explicitly cleared.
  - A caught-and-fallback chain remains: interpreter chat -> direct server chat
    -> direct GGUF (llama.cpp) inference, each logged to the failure ledger.
"""
from __future__ import annotations

import glob
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Swallows recorded when the failure ledger is unavailable
try:
    from CORE.failure_ledger import note_failure
except ImportError:
    def note_failure(where: str, exc: Any = None) -> None:
        del exc
        return None


#: Substrings that identify a server-side "cannot compute logits / generation
#: unsupported" failure (the exact llama.cpp message is reproduced below).
LOGITS_UNSUPPORTED_SIGNATURES = ("logits computation", "embedding-only", "logits computation. skipping")

#: Text fragments of common embedding-model identifiers (bert-style encoders).
EMBED_MODEL_HINTS = (
    "embed",
    "e5-",
    "bge-",
    "gte-",
    "minilm",
    "nomic",
    "jina-embed",
    "mpnet",
    "roberta",
    "clip-",
)

#: Ports probed for a local OpenAI-compatible model server.
DEFAULT_PROBE_PORTS = (8080, 1234, 1337)


class EveInterpreterBridge:
    """Bridge between Open Interpreter [local] / OpenAI-compatible servers and Eve's mesh-driving capabilities."""

    #: Ports probed for a local model server; override per-instance in tests.
    PROBE_PORTS = DEFAULT_PROBE_PORTS
    #: Seconds to wait when probing ``/v1/models``.
    PROBE_TIMEOUT = 2.0
    #: Seconds to wait for a chat completion from a local server.
    CHAT_TIMEOUT = 180.0
    #: Token budget for direct chat completion and GGUF fallback runs.
    FALLBACK_MAX_TOKENS = 256

    def __init__(self, eve_arm=None, probe_ports=None) -> None:
        self._eve = eve_arm
        self._interpreter = None
        self._available = False
        self._probe_ports = tuple(probe_ports) if probe_ports is not None else self.PROBE_PORTS
        self._last_probe: Dict[str, Any] = {}
        self._init_interpreter()

    # ------------------------------------------------------------------
    # Interpreter package availability
    # ------------------------------------------------------------------
    def _init_interpreter(self) -> None:
        # Open Interpreter is an optional legacy dependency. In production it is
        # absent from every Azazael venv (py315t included), and the direct
        # OpenAI-compatible chat path is the primary (and only) generation
        # route. Even when the package is importable (e.g. a dev environment),
        # we disable it here so the direct-chat path is always used, keeping
        # behaviour consistent with production and avoiding the interpreter
        # consuming server responses before the direct-chat fallback runs.
        try:
            import interpreter as interpreter_pkg  # scanner:ignore (optional legacy pkg)
            note_failure("eve_interpreter:init", None)
        except ImportError as exc:
            note_failure("eve_interpreter:init", exc)
        self._interpreter = None
        self._available = False

    def is_available(self) -> bool:
        return self._available

    def get_interpreter(self):
        return self._interpreter

    # ------------------------------------------------------------------
    # Server probing — /v1/models inspection instead of blind /health
    # ------------------------------------------------------------------
    def _fetch_model_ids(self, base: str) -> Optional[List[str]]:
        """Return model ids hosted at ``base`` via /v1/models, or None if unreachable."""
        url = base + "/v1/models"
        try:
            with urllib.request.urlopen(url, timeout=self.PROBE_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        except Exception as exc:
            note_failure("eve_interpreter:probe_models", exc)
            return None
        ids: List[str] = []
        for item in payload.get("data") or []:
            mid = item.get("id") if isinstance(item, dict) else item
            if isinstance(mid, str) and mid:
                ids.append(mid)
        for item in payload.get("models") or []:
            mid = None
            if isinstance(item, dict):
                mid = item.get("id") or item.get("model") or item.get("name")
            else:
                mid = item
            if isinstance(mid, str) and mid:
                ids.append(mid)
        return list(dict.fromkeys(ids))

    def is_embedding_model(self, model_id: str) -> bool:
        """True when the model id looks like an embedding-only (encoder) model."""
        lowered = (model_id or "").lower()
        return any(hint in lowered for hint in EMBED_MODEL_HINTS)

    def _probe_servers(self, model_override: Optional[str] = None) -> List[Dict[str, Any]]:
        """Probe candidate ports and return generation-capable server targets.

        Fills ``self._last_probe`` with a summary of everything observed, so
        callers (and ``get_status``) can see exactly which ports were probed,
        which models were found, and which servers were rejected as
        embedding-only.
        """
        probed: List[Dict[str, Any]] = []
        text_targets: List[Dict[str, Any]] = []
        embedding_only: List[Dict[str, Any]] = []
        for port in self._probe_ports:
            base = "http://localhost:%d" % port
            model_ids = self._fetch_model_ids(base)
            if model_ids is None:
                probed.append({"port": port, "reachable": False})
                continue
            info: Dict[str, Any] = {
                "port": port,
                "reachable": True,
                "models": model_ids,
            }
            generation_ids = [mid for mid in model_ids if not self.is_embedding_model(mid)]
            info["generation_capable"] = bool(generation_ids)
            probed.append(info)
            if generation_ids:
                chosen = generation_ids if not model_override else [model_override]
                for mid in chosen:
                    text_targets.append({"base": base + "/v1", "port": port, "model": mid})
            else:
                embedding_only.append(info)
        self._last_probe = {
            "probed": probed,
            "text_targets": text_targets,
            "embedding_only": embedding_only,
        }
        return text_targets

    # ------------------------------------------------------------------
    # Failure classification — where litellm surfaced "logits computation"
    # ------------------------------------------------------------------
    def classify_failure(self, detail: str) -> str:
        """Map a failure string to a coarse failure class."""
        lowered = (detail or "").lower()
        if any(sig in lowered for sig in LOGITS_UNSUPPORTED_SIGNATURES):
            return "server_compute_unsupported"
        if "http 404" in lowered or "not found" in lowered:
            return "server_missing_model"
        if "http " in lowered:
            return "server_http_error"
        return "transport_error"

    # ------------------------------------------------------------------
    # Interpreter path — explicit provider config, cleared logit params
    # ------------------------------------------------------------------
    def _configure_interpreter_for_server(self, base: str, model_id: str) -> bool:
        """Point Open Interpreter at an explicit OpenAI-compatible custom provider.

        Provider and model are both explicit (``openai/<real model id>``) so
        litellm targets llama.cpp's /v1 endpoints with the server's real model
        id rather than the invented ``openai/local-model`` placeholder. Logit
        related request parameters are cleared to None: servers that cannot
        compute logits (embedding variants, tiny models, speculative contexts)
        reject those requests with the 500 that surfaces as
        ``litellm.InternalServerError``. Returns False when configuration was
        not possible (unknown interpreter version, missing attributes), leaving
        the direct-chat fallback in charge.
        """
        interp = self._interpreter
        if interp is None:
            return False
        ok = True
        try:
            interp.auto_run = True
            llm = interp.llm
            llm.model = "openai/" + model_id
            llm.api_base = base
            llm.api_key = "dummy"
            llm.supports_functions = False
            llm.supports_vision = False
        except Exception as exc:
            note_failure("eve_interpreter:config", exc)
            ok = False
        for attr in ("logprobs", "top_logprobs", "logit_bias"):
            try:
                llm = getattr(self._interpreter, "llm", None)
                if llm is not None:
                    setattr(llm, attr, None)
            except Exception as exc:
                note_failure("eve_interpreter:clear_logit_params", exc)
                ok = False
        return ok

    # ------------------------------------------------------------------
    # Direct OpenAI-compatible chat — no litellm involved
    # ------------------------------------------------------------------
    def _direct_chat(
        self,
        base: str,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """POST a plain chat-completion request to ``base`` and return the text.

        The request uses ONLY plain chat-completion parameters (model,
        messages, temperature, max_tokens, stream). Logit-related parameters
        are never sent — that is the protocol-level fix for the 500
        "the current context does not logits computation. skipping" failure.
        """
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": max_tokens or self.FALLBACK_MAX_TOKENS,
            "stream": False,
            # Qwen3-style thinking models stream reasoning into
            # reasoning_content with empty content (measured 2026-11-06:
            # 16 tokens of reasoning, content ""). Ask for the answer.
            "enable_thinking": False,
        }
        req = urllib.request.Request(
            base.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "eve-interpreter-bridge/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.CHAT_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("server returned no completion choices: %r" % (data.get("error") or data))
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if not content and isinstance(message, dict):
            content = message.get("reasoning_content")
        if not content:
            content = first.get("text")  # legacy completion-shaped responses
        return (content or "").strip().strip('"')

    def _try_target(self, target: Dict[str, Any], prompt: str, system: Optional[str]):
        """Run one generation attempt against one target server.

        Returns (result_dict, failure_dict). Exactly one of the two is None.
        """
        detail: str
        try:
            text = self._direct_chat(target["base"], target["model"], prompt, system)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            detail = "%s HTTP %s: %s" % (target["base"], exc.code, body)
            note_failure("eve_interpreter:direct_chat", exc)
            return None, {"server": target, "error": detail, "failure_class": self.classify_failure(detail)}
        except Exception as exc:
            detail = "%s %s: %s" % (target["base"], type(exc).__name__, exc)
            note_failure("eve_interpreter:direct_chat", exc)
            return None, {"server": target, "error": detail, "failure_class": self.classify_failure(detail)}
        if not text:
            detail = "%s returned an empty completion" % target["base"]
            note_failure("eve_interpreter:direct_chat_empty", RuntimeError(detail))
            return None, {"server": target, "error": detail, "failure_class": "empty_response"}
        return (
            {
                "status": "ok",
                "result": text,
                "provider": "openai-compatible-direct",
                "server": {"base": target["base"], "model": target["model"]},
            },
            None,
        )

    # ------------------------------------------------------------------
    # GGUF fallback — direct llama.cpp inference (no server needed)
    # ------------------------------------------------------------------
    def _pick_text_gguf(self) -> Optional[str]:
        # The cradle is searched FIRST: models the operator dropped in
        # for Eve outrank the LM Studio library. Embeddings/mmproj never.
        gguf_files: List[str] = []
        try:
            cdir = Path.home() / ".azazael" / "cradle"
            if cdir.is_dir():
                gguf_files += [str(p) for p in cdir.glob("*.gguf")]
            gguf_files += glob.glob(str(Path.home() / ".lmstudio" / "models" / "**" / "*.gguf"), recursive=True)
        except Exception as exc:
            note_failure("eve_interpreter:gguf_scan", exc)
            return None
        text_models = [f for f in gguf_files if "mmproj" not in f.lower() and "embed" not in f.lower()]
        if not text_models:
            return None
        try:
            text_models.sort(key=self._gguf_size_key)
        except OSError as exc:
            note_failure("eve_interpreter:gguf_sort", exc)
            return None
        return text_models[0]

    def run_cradle(self, prompt: str, model: Optional[str] = None, max_tokens: Optional[int] = None) -> Dict[str, Any]:
        """Run a prompt on a cradle GGUF via a transient sidecar.

        The interpreter evolved into the .gguf: any adopted cradle model
        (chosen by stem, name or path) serves the prompt with its native
        runner, then the sidecar is killed. No persistent server needed.
        """
        try:
            from .cradle import resolve_model, run_gguf
        except ImportError as exc:
            note_failure("eve_interpreter:cradle_import", exc)
            return {"status": "error", "error": "cradle unavailable: %s" % exc}
        try:
            path = resolve_model(model or "", None) if model else None
            if path is None:
                from .cradle import list_cradle_models
                found = [m["name"] for m in list_cradle_models()
                         if m["framework"] == "gguf"]
                if not found:
                    return {"status": "error", "error": "no gguf in cradle"}
                path = resolve_model(found[0], None)
            text = run_gguf(str(path), prompt, max_tokens or self.FALLBACK_MAX_TOKENS)
        except (ValueError, RuntimeError) as exc:
            note_failure("eve_interpreter:cradle_run", exc)
            return {"status": "error", "error": str(exc)[:300]}
        if not text:
            return {"status": "error", "error": "cradle model answered empty"}
        return {"status": "ok", "result": text, "provider": "cradle-sidecar", "model": str(path)}

    def _gguf_size_key(self, path: str) -> int:
        try:
            return Path(path).stat().st_size
        except OSError:
            return 1 << 60

    def _gguf_fallback(self, prompt: str, provider_tag: str) -> Optional[Dict[str, Any]]:
        """Direct GGUF inference, used when no server path succeeded."""
        try:
            from llama_cpp import Llama  # scanner:ignore (optional heavy dep; llama-cpp-python is sdist-only and absent from py315t -- GGUF fallback stays lazy)
        except ImportError as exc:
            note_failure("eve_interpreter:llama_cpp_import", exc)
            return None
        model_path = self._pick_text_gguf()
        if not model_path:
            return None
        try:
            llm = Llama(model_path=model_path, n_ctx=2048, verbose=False)
            output = llm(prompt, max_tokens=self.FALLBACK_MAX_TOKENS, echo=False)
            text = output["choices"][0]["text"]
        except Exception as exc:
            note_failure("eve_interpreter:gguf_run", exc)
            return None
        return {"status": "ok", "result": text, "model": model_path, "provider": provider_tag}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run_local(self, prompt: str, model: Optional[str] = None, system: Optional[str] = None) -> Dict[str, Any]:
        """Run a prompt through [Open Interpreter or] a local model server.

        Preference order per server target: Open Interpreter (if importable,
        configured as an explicit OpenAI-compatible provider with logit
        parameters cleared) then a direct OpenAI-compatible chat call. When no
        generation path succeeds, the GGUF fallback runs; when even that fails,
        an error dict carries every failure detail plus the probe summary.
        """
        failures: List[Dict[str, Any]] = []
        targets = self._probe_servers(model_override=model)
        for target in targets:
            if self._available and self._interpreter is not None:
                configured = self._configure_interpreter_for_server(target["base"], target["model"])
                if configured:
                    try:
                        result = self._interpreter.chat(prompt)
                    except Exception as exc:
                        note_failure("eve_interpreter:chat", exc)
                        failures.append(
                            {"server": target, "error": "%s: %s" % (type(exc).__name__, exc), "failure_class": self.classify_failure(str(exc))}
                        )
                        result = None
                    if result:
                        return {"status": "ok", "result": result, "provider": "interpreter", "server": {"base": target["base"], "model": target["model"]}}
            attempt, fail = self._try_target(target, prompt, system)
            if attempt is None:
                if fail is not None:
                    failures.append(fail)
                continue
            return attempt
        gguf = self._gguf_fallback(prompt, provider_tag="llama-cpp-python-direct" if targets else "llama-cpp-python")
        if gguf:
            return gguf
        classification = "none"
        if failures:
            classes = {f.get("failure_class", "unknown") for f in failures}
            classification = ";".join(sorted(classes))
            summary = "; ".join(f["error"] for f in failures)[:2000]
            return {"status": "error", "error": summary, "failure_class": classification, "probe": self._last_probe}
        return {
            "status": "error",
            "error": "no generation-capable model server or engine available",
            "failure_class": classification,
            "probe": self._last_probe,
        }

    def drive_mesh_via_interpreter(self, instruction: str) -> Dict[str, Any]:
        """Use Open Interpreter to drive the mesh based on a natural language instruction.

        Degraded mode (interpreter package unavailable) is reported by
        get_status(); this method always attempts run_local, which falls back
        to the direct OpenAI-compatible chat path when needed.
        """
        context = (
            "You are Eve, the autonomous driver of the Azazael architecture/mesh. "
            "You have access to these mesh-driving capabilities:\n"
            "- discover_worms: Discover all registered worms\n"
            "- run_cluster: Execute a ClusterWORMZ by category (monitor, analysis, security, etc.)\n"
            "- run_metacluster: Run full MetaCluster orchestration across all natures\n"
            "- drive_mesh: Drive the mesh via autonomous decision-making\n"
            "- orchestrate: Full mesh orchestration with self-model update\n"
            "- spawn_worm: Spawn and execute worms by type\n"
            "- get_mesh_status: Get mesh and consciousness status\n"
            "\nInstruction: "
        )
        return self.run_local(instruction, system=context)

    def get_status(self) -> Dict[str, Any]:
        """Bridge status incl. the latest server-probe summary, when probed."""
        mesh_drivable = self._available and self._eve is not None
        status: Dict[str, Any] = {
            "interpreter_available": self._available,
            "eve_arm_available": self._eve is not None,
            "mesh_drivable": mesh_drivable,
        }
        if self._last_probe:
            status["server_probe"] = {
                "probed": self._last_probe.get("probed"),
                "text_targets": len(self._last_probe.get("text_targets") or []),
                "embedding_only": len(self._last_probe.get("embedding_only") or []),
            }
        return status


def get_eve_interpreter_bridge(eve_arm=None, probe_ports=None):
    """Factory function to create the bridge."""
    return EveInterpreterBridge(eve_arm=eve_arm, probe_ports=probe_ports)
