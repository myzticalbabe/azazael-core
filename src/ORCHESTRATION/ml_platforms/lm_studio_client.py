# coding: utf-8
"""
LM Studio Local LLM Integration

Module for connecting Azazael to local LLMs via LM Studio or Ollama.

Usage:
    >>> from lm_studio_client import LMStudioClient
    >>> client = LMStudioClient(base_url="http://localhost:1234")
    >>> response = client.generate("Explain quantum computing")
"""

import requests  # pyright: ignore[reportMissingModuleSource]
import json
from typing import Generator, List, Optional
from dataclasses import dataclass

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class ModelInfo:
    """Information about a loaded model."""

    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "local"


@dataclass
class ChatMessage:
    """Chat message."""

    role: str  # "system", "user", "assistant"
    content: str


class LMStudioClient:
    """Client for LM Studio local inference."""

    def __init__(self, base_url: str = "http://localhost:1234", api_key: Optional[str] = None):
        """Initialize client.

        Args:
            base_url: LM Studio server URL (default: http://localhost:1234)
            api_key: Optional API key (LM Studio usually doesn't require one)
        """
        self.base_url = base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

    def list_models(self) -> list[ModelInfo]:
        """list available models."""
        try:
            response = requests.get(
                f"{self.base_url}/v1/models", headers=self.headers, timeout=5
            )
            response.raise_for_status()
            data = response.json()
            return [ModelInfo(**m) for m in data.get("data", [])]
        except requests.exceptions.RequestException as e:
            note_failure("lm_studio_client:L61", e)
            return [ModelInfo(id=f"Error: {e}")]

    def chat_complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        stream: bool = False,
    ) -> dict:
        """Send chat completion request.

        Args:
            messages: list of chat messages
            model: Model ID (optional, uses loaded model)
            temperature: Response creativity (0.0-1.0)
            max_tokens: Maximum response length
            stream: Stream response

        Returns:
            API response dictionary
        """
        payload = {
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if model:
            payload["model"] = model

        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self.headers,
                json=payload,
                stream=stream,
                timeout=60,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            note_failure("lm_studio_client:L103", e)
            return {"error": str(e)}

    def complete(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        stream: bool = False,
    ) -> dict:
        """Send text completion request.

        Args:
            prompt: Input text
            model: Model ID
            temperature: Response creativity
            max_tokens: Maximum response length
            stream: Stream response

        Returns:
            API response dictionary
        """
        payload = {
            "prompt": prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if model:
            payload["model"] = model

        try:
            response = requests.post(
                f"{self.base_url}/v1/completions",
                headers=self.headers,
                json=payload,
                stream=stream,
                timeout=60,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            note_failure("lm_studio_client:L145", e)
            return {"error": str(e)}

    def stream_chat(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
    ) -> Generator[str, None, None]:
        """Stream chat response.

        Yields:
            Chunks of the response
        """
        payload = {
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "stream": True,
        }
        if model:
            payload["model"] = model

        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self.headers,
                json=payload,
                stream=True,
                timeout=60,
            )
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    line = line.decode("utf-8")
                    if line.startswith("data: "):
                        data = line[6:]
                        if data != "[DONE]":
                            chunk = json.loads(data)
                            content = (
                                chunk.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content", "")
                            )
                            if content:
                                yield content
        except requests.exceptions.RequestException as e:
            note_failure("lm_studio_client:L190", e)
            yield f"Error: {e}"

    def embeddings(self, input_text: str, model: Optional[str] = None) -> dict:
        """Get text embeddings.

        Args:
            input_text: Text to embed
            model: Model ID

        Returns:
            Embedding response
        """
        payload = {"input": input_text}
        if model:
            payload["model"] = model

        try:
            response = requests.post(
                f"{self.base_url}/v1/embeddings",
                headers=self.headers,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            note_failure("lm_studio_client:L216", e)
            return {"error": str(e)}


class OllamaClient:
    """Client for Ollama local inference."""

    def __init__(self, base_url: str = "http://localhost:11434"):
        """Initialize client.

        Args:
            base_url: Ollama server URL
        """
        self.base_url = base_url.rstrip("/")

    def list_models(self) -> list[str]:
        """list available models."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            data = response.json()
            return [m["name"] for m in data.get("models", [])]
        except requests.exceptions.RequestException as e:
            note_failure("lm_studio_client:L238", e)
            return [f"Error: {e}"]

    def generate(
        self,
        prompt: str,
        model: str,
        stream: bool = False,
        options: Optional[dict] = None,
    ) -> dict:
        """Generate response.

        Args:
            prompt: Input text
            model: Model name
            stream: Stream response
            options: Model options (temperature, etc.)

        Returns:
            API response
        """
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
        }
        if options:
            payload["options"] = options

        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                stream=stream,
                timeout=120,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            note_failure("lm_studio_client:L276", e)
            return {"error": str(e)}

    def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        stream: bool = False,
    ) -> dict:
        """Chat completion.

        Args:
            messages: Chat messages
            model: Model name
            stream: Stream response

        Returns:
            API response
        """
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": stream,
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                stream=stream,
                timeout=120,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            note_failure("lm_studio_client:L310", e)
            return {"error": str(e)}


def detect_local_llm() -> (str | None):
    """Detect available local LLM providers.

    Returns:
        URL of detected provider or None
    """
    providers = [
        ("LM Studio", "http://localhost:1234"),
        ("Ollama", "http://localhost:11434"),
    ]

    for name, url in providers:
        try:
            response = requests.get(f"{url}/v1/models", timeout=2)
            if response.status_code == 200:
                print(f"[OK] {name} detected at {url}")
                return url
        except requests.exceptions.RequestException as exc:
            note_failure("lm_studio_client:L331", exc)

    print("[WARN] No local LLM provider detected")
    print("       Start LM Studio or Ollama and try again")
    return None


if __name__ == "__main__":
    print("=" * 50)
    print("LM Studio / Ollama Local LLM Client")
    print("=" * 50)
    print()

    url = detect_local_llm()

    if url:
        print()
        if "1234" in url:
            client = LMStudioClient(url)
        else:
            client = OllamaClient(url)

        models = client.list_models()
        print(f"Models: {models}")
