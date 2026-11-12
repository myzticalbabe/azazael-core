"""
Eve Unleashed — Streaming Inference
=====================================
Async generator-based streaming for LLM text generation.

Supports:
- GGUF (llama.cpp): native streaming via model(..., stream=True)
- Transformers (HuggingFace): token-by-token generation
- VL: streaming text generation from vision-language models
- Torch/sklearn/ONNX: yield full result as single chunk (no native streaming)
"""

import time
from typing import AsyncGenerator, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here



class StreamToken:
    """A single token or chunk from a streaming inference call."""

    def __init__(self, text: str = "", token_id: int = -1,
                 index: int = 0, is_final: bool = False,
                 latency_ms: float = 0.0):
        self.text = text
        self.token_id = token_id
        self.index = index
        self.is_final = is_final
        self.latency_ms = latency_ms


class StreamResult:
    """Aggregated result of a streaming inference call."""

    def __init__(self):
        self.tokens: list[StreamToken] = []
        self.full_text: str = ""
        self.total_time_ms: float = 0.0
        self.token_count: int = 0

    def append(self, token: StreamToken):
        self.tokens.append(token)
        if token.text:
            self.full_text += token.text
        if token.is_final:
            self.total_time_ms = token.latency_ms
            self.token_count = len(self.tokens)

    @property
    def tokens_per_second(self) -> float:
        if self.total_time_ms > 0:
            return round(self.token_count / (self.total_time_ms / 1000), 1)
        return 0.0


async def stream_gguf(
    model,
    prompt: str,
    max_tokens: int = 256,
) -> AsyncGenerator[StreamToken, None]:
    """Stream tokens from a GGUF (llama.cpp) model."""
    import asyncio

    loop = asyncio.get_running_loop()
    start = time.perf_counter()
    index = 0

    def _generate():
        return model(
            prompt,
            max_tokens=max_tokens,
            echo=False,
            stream=True,
        )

    generator = await loop.run_in_executor(None, _generate)

    for output in generator:
        choices = output.get("choices", [])
        text = choices[0].get("text", "") if choices else ""
        yield StreamToken(
            text=text,
            token_id=-1,
            index=index,
            is_final=(output.get("usage", {}).get("completion_tokens", 0) >= max_tokens
                      or output.get("finish_reason") == "stop"),
        )
        index += 1

    elapsed = (time.perf_counter() - start) * 1000
    yield StreamToken(
        text="",
        token_id=-1,
        index=index,
        is_final=True,
        latency_ms=elapsed,
    )


async def stream_transformers(
    model,
    tokenizer,
    prompt: str,
    max_tokens: int = 256,
) -> AsyncGenerator[StreamToken, None]:
    """Stream tokens from a HuggingFace model using TextIteratorStreamer.

    Uses model.generate() with TextIteratorStreamer for true token-by-token
    streaming via a thread-to-asyncio bridge.

    Args:
        model: HuggingFace model (AutoModelForCausalLM) or pipeline
        tokenizer: Tokenizer for decoding token IDs to text
        prompt: Input text prompt
        max_tokens: Maximum new tokens to generate
    """
    import asyncio
    from threading import Thread

    try:
        from transformers import TextIteratorStreamer
    except ImportError:
        raise ImportError(
            "TextIteratorStreamer requires transformers>=4.36. "
            "Install: pip install transformers --upgrade"
        )

    loop = asyncio.get_running_loop()
    start = time.perf_counter()
    queue: asyncio.Queue = asyncio.Queue()

    # If given a pipeline, extract the underlying model
    if hasattr(model, "model") and hasattr(model, "tokenizer"):
        model = model.model

    inputs = tokenizer(prompt, return_tensors="pt")
    streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)

    def _generate():
        try:
            model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                streamer=streamer,
                do_sample=True,
                temperature=0.7,
                pad_token_id=tokenizer.eos_token_id,
            )
        except Exception as exc:
            note_failure("streaming:L148", exc)
        finally:
            # Ensure the async generator always unblocks, even on error
            loop.call_soon_threadsafe(queue.put_nowait, ("", True))

    # Thread 1: runs generate() which fills the streamer
    gen_thread = Thread(target=_generate, daemon=True)
    gen_thread.start()

    # Thread 2: iterates the blocking streamer, pushes tokens to async queue
    def _feed():
        for text in streamer:
            loop.call_soon_threadsafe(queue.put_nowait, (text, False))

    feed_thread = Thread(target=_feed, daemon=True)
    feed_thread.start()

    index = 0
    while True:
        text, is_done = await queue.get()
        if is_done:
            break
        yield StreamToken(text=text, token_id=-1, index=index, is_final=False)
        index += 1

    gen_thread.join(timeout=10)
    feed_thread.join(timeout=5)
    elapsed = (time.perf_counter() - start) * 1000
    yield StreamToken(
        text="",
        token_id=-1,
        index=index,
        is_final=True,
        latency_ms=elapsed,
    )



async def stream_native(
    result,  # already-computed InferenceResult or similar
) -> AsyncGenerator[StreamToken, None]:
    """Yield a pre-computed result as a single streamed chunk."""
    yield StreamToken(
        text=str(result.predictions) if hasattr(result, 'predictions') else str(result),
        token_id=-1,
        index=0,
        is_final=True,
        latency_ms=result.latency_ms if hasattr(result, 'latency_ms') else 0,
    )
