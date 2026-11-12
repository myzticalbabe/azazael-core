"""
Eve Unleashed Inference Engine
===============================
Main inference engine supporting PyTorch, sklearn, transformers, ONNX,
GGUF (llama.cpp), and Vision-Language (VL) models.

Supports synchronous and async inference, streaming token generation,
and LRU+TTL inference caching.

Async mode uses a thread pool for non-blocking batch processing.
"""

import asyncio
import concurrent.futures
import functools
import os
import time
from pathlib import Path
from typing import Any, AsyncGenerator, List, cast

import numpy as np

from .core import InferenceResult, BatchResult, ModelLoader, timed_inference, HAS_PIL
from .config import EveConfig
from .cache import InferenceCache
from .streaming import (
    StreamToken,
    stream_gguf,
    stream_transformers,
    stream_native,
)
from .utils import (
    prepare_input_tensor,
    postprocess_output,
)

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

if HAS_PIL:
    from PIL import Image  # noqa: F811


class InferenceEngine:
    """High-performance multi-framework inference engine.

    Supports synchronous infer() and async infer_async() for
    non-blocking batch processing via a thread pool executor.
    """

    def __init__(self, config: (EveConfig | None) = None):
        self.config = config or EveConfig()

        # Auto-detect best device if set to "auto"
        if self.config.device == "auto":
            self.config.device = ModelLoader.get_best_device()
        self.loaded_models: dict[str, Any] = {}
        self.model_loaders = {
            "torch": self._load_torch_model,
            "sklearn": self._load_sklearn_model,
            "transformers": self._load_transformers_model,
            "onnx": self._load_onnx_model,
            "gguf": self._load_gguf_model,
            "vl": self._load_vl_model,
        }
        self._frameworks = ModelLoader.detect_frameworks()
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.config.num_workers or 1
        )
        self._inference_count: int = 0
        self._total_latency_ms: float = 0.0

        # Inference cache (LRU + TTL)
        if self.config.enable_cache:
            self._cache = InferenceCache(
                max_size_bytes=self.config.cache_size_mb * 1024 * 1024,
                ttl_seconds=self.config.cache_ttl_seconds,
            )
        else:
            self._cache = None

    @property
    def gpu_info(self) -> dict[str, Any]:
        """Get GPU hardware information."""
        return ModelLoader.get_gpu_info()

    @property
    def available_frameworks(self) -> list[str]:
        """list of detected ML frameworks."""
        return [k for k, v in self._frameworks.items() if v]

    @property
    def cache_stats(self) -> dict[str, Any]:
        """Cache usage statistics (hits, misses, hit rate)."""
        if self._cache is None:
            return {"enabled": False}
        return {"enabled": True, **self._cache.stats}

    @property
    def stats(self) -> dict[str, Any]:
        """Runtime statistics including cache."""
        avg_latency = (
            self._total_latency_ms / self._inference_count
            if self._inference_count > 0
            else 0
        )
        return {
            "inference_count": self._inference_count,
            "total_latency_ms": round(self._total_latency_ms, 2),
            "avg_latency_ms": round(avg_latency, 2),
            "loaded_models": len(self.loaded_models),
            "available_frameworks": self.available_frameworks,
            "cache": self.cache_stats,
        }

    def load_model(self, model_path: str, framework: str = "auto", **kwargs) -> str:
        """Load a model and return its handle.

        If framework is "auto", the framework is guessed from the file extension.

        The framework argument is validated BEFORE the path, because a bad
        framework name is a caller error that no file can satisfy: answering
        "Model not found" for it sends the operator to inspect a path when the
        argument actually at fault is the framework, and correcting the path
        only uncovers the real error on the next attempt. The consequence is
        that a missing path with an explicitly bad framework now raises
        ValueError (was FileNotFoundError); a missing path with a usable or
        "auto"-resolvable framework still raises FileNotFoundError, unchanged.
        """
        p = Path(model_path)

        if framework == "auto":
            framework = ModelLoader.guess_framework(p)
            if framework == "unknown":
                raise ValueError(
                    f"Cannot guess framework for: {model_path}. "
                    f"Specify explicitly from: {list(self.model_loaders.keys())}"
                )

        if framework not in self.model_loaders:
            raise ValueError(
                f"Unsupported framework: {framework}. "
                f"Available: {list(self.model_loaders.keys())}"
            )
        if framework not in self._frameworks or not self._frameworks[framework]:
            raise ImportError(f"Framework '{framework}' is not installed")

        if not p.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")

        loader = self.model_loaders[framework]
        model = loader(model_path, **kwargs)
        handle = f"{framework}:{model_path}"
        self.loaded_models[handle] = model

        # Warm up newly loaded model
        self._warmup_model(handle)
        return handle

    def infer(
        self,
        handle: str,
        inputs: np.ndarray | list | dict,
        batch: bool = False,
    ) -> InferenceResult | BatchResult:
        """Run inference on a loaded model.

        When caching is enabled, checks the LRU+TTL cache before
        running inference. Identical inputs return cached results.
        """
        if handle not in self.loaded_models:
            raise KeyError(f"Model not loaded: {handle}")

        framework = handle.split(":")[0]

        # Check cache (only for single, non-batch inference)
        if not batch and self._cache is not None:
            cached = self._cache.get(handle, inputs, framework)
            if cached is not None:
                return cached

        model = self.loaded_models[handle]

        if batch and isinstance(inputs, list):
            result = self._batch_infer(model, framework, inputs)
        else:
            result = self._single_infer(model, framework, inputs)

        # Track stats
        if isinstance(result, InferenceResult):
            self._inference_count += 1
            self._total_latency_ms += result.latency_ms
        elif isinstance(result, BatchResult):
            self._inference_count += len(result.results)
            self._total_latency_ms += result.total_time_ms

        # Learn: record outcome for evolution (best-effort, never raises)
        try:
            from .model_registry import get_registry
            _mpath = handle.split(":", 1)[1] if ":" in handle else handle
            if isinstance(result, InferenceResult):
                _lat = float(result.latency_ms or 0)
            else:
                _lat = float(result.total_time_ms) / max(1, len(result.results))
            get_registry().record_inference(_mpath, _lat, framework)
        except Exception as exc:
            note_failure("engine:L202", exc)

        # Store in cache
        if not batch and self._cache is not None:
            self._cache.put(handle, inputs, framework, result)

        return result

    @timed_inference
    def _single_infer(self, model, framework: str, inputs) -> InferenceResult:
        """Run a single inference pass."""
        start = time.perf_counter()

        if framework == "torch":
            import torch

            tensor = torch.from_numpy(prepare_input_tensor(inputs))
            with torch.no_grad():
                output = model(tensor)
            predictions = postprocess_output(output, "torch")
            result = InferenceResult(
                model_name=str(type(model).__name__),
                framework=framework,
                input_shape=tuple(tensor.shape),
                output_shape=tuple(output.shape),
                latency_ms=0,  # set by decorator
                predictions=predictions,
            )
        elif framework == "sklearn":
            arr = np.array(inputs).reshape(1, -1) if inputs is not None else None
            output = model.predict(arr)
            result = InferenceResult(
                model_name=str(type(model).__name__),
                framework=framework,
                input_shape=arr.shape if arr is not None else (0,),
                output_shape=output.shape,
                latency_ms=0,
                predictions=output.tolist(),
            )
        elif framework == "transformers":
            output = model(inputs if isinstance(inputs, (str, list)) else str(inputs))
            result = InferenceResult(
                model_name=model.model.config._name_or_path
                if hasattr(model, "model")
                else "unknown",
                framework=framework,
                input_shape=(len(inputs) if isinstance(inputs, list) else 1,),
                output_shape=(len(output) if isinstance(output, list) else 1,),
                latency_ms=0,
                predictions=output,
            )
        elif framework == "onnx":
            arr = np.array(inputs, dtype=np.float32)
            if arr.ndim == 1:
                arr = arr.reshape(1, -1)
            ort_inputs = {model.get_inputs()[0].name: arr}
            output = model.run(None, ort_inputs)
            preds = output[0].tolist()
            result = InferenceResult(
                model_name=str(type(model).__name__),
                framework=framework,
                input_shape=arr.shape,
                output_shape=output[0].shape,
                latency_ms=0,
                predictions=preds,
            )
        elif framework == "gguf":
            text = inputs if isinstance(inputs, str) else str(inputs)
            output = model(text, max_tokens=128, echo=False)
            choices = output.get("choices", [])
            preds = [choices[0].get("text", "")] if choices else [""]
            result = InferenceResult(
                model_name=str(type(model).__name__),
                framework=framework,
                input_shape=(1,),
                output_shape=(1,),
                latency_ms=0,
                predictions=preds,
            )
        elif framework == "vl":
            if not HAS_PIL:
                raise RuntimeError(
                    "PIL/Pillow required for VL inference. Install: pip install Pillow"
                )
            import torch
            import transformers
            from transformers import AutoProcessor

            # AutoModelForVision2Seq was removed from transformers 5.x (the
            # installed 5.19.0 raises ImportError on a static from-import), so
            # resolve it dynamically for a clear error instead of a dead import.
            AutoModelForVision2Seq = getattr(transformers, "AutoModelForVision2Seq", None)
            if AutoModelForVision2Seq is None:
                raise ImportError(
                    "VL inference requires transformers.AutoModelForVision2Seq, "
                    f"absent from installed transformers {transformers.__version__}"
                )

            if isinstance(inputs, dict):
                image_path = inputs.get("image", "")
                prompt = inputs.get("text", str(inputs))
            else:
                image_path = ""
                prompt = str(inputs)

            model_path = model  # VL stores path string, not loaded model object

            if image_path and os.path.exists(image_path):
                image = Image.open(image_path).convert("RGB")
                processor = AutoProcessor.from_pretrained(model_path)
                model_vl = AutoModelForVision2Seq.from_pretrained(model_path)
                proc_inputs = processor(text=prompt, images=image, return_tensors="pt")
                with torch.no_grad():
                    output_ids = model_vl.generate(**proc_inputs, max_new_tokens=128)
                preds = [processor.decode(output_ids[0], skip_special_tokens=True)]
            else:
                # Fall back to text-only pipeline
                from transformers import pipeline

                pipe = pipeline("text-generation", model=model_path, device=-1)
                preds = [pipe(prompt, max_new_tokens=128)[0]["generated_text"]]

            result = InferenceResult(
                model_name=str(Path(model_path).name) if isinstance(model_path, str) else "vl",
                framework=framework,
                input_shape=(1,),
                output_shape=(1,),
                latency_ms=0,
                predictions=preds,
            )
        else:
            raise ValueError(f"Unknown framework: {framework}")

        return result

    def _batch_infer(self, model, framework: str, inputs: list) -> BatchResult:
        """Run batched inference and return batch statistics."""
        results = []
        total_start = time.perf_counter()

        for inp in inputs:
            result = self._single_infer(model, framework, inp)
            results.append(result)

        total_time = (time.perf_counter() - total_start) * 1000
        n = len(inputs)
        return BatchResult(
            results=results,
            total_time_ms=round(total_time, 2),
            avg_latency_ms=round(total_time / n, 2) if n else 0,
            throughput_per_sec=round(n / (total_time / 1000), 2) if total_time > 0 else 0,
        )

    def unload_model(self, handle: str):
        """Unload a model from memory and invalidate its cache."""
        self.loaded_models.pop(handle, None)
        if self._cache is not None:
            self._cache.invalidate(handle)

    def list_models(self) -> list[str]:
        """list all loaded model handles."""
        return list(self.loaded_models.keys())

    def _load_torch_model(self, path: str, **kwargs):
        import torch

        try:
            model = torch.load(path, map_location="cpu", weights_only=False)
            model.eval()
            return model
        except FileNotFoundError:
            raise FileNotFoundError(f"Torch model not found: {path}")
        except RuntimeError as e:
            raise RuntimeError(f"Failed to load torch model '{path}': {e}")
        except Exception as e:
            raise RuntimeError(f"Unexpected error loading torch model '{path}': {e}")

    def _load_sklearn_model(self, path: str, **kwargs):
        import joblib

        try:
            return joblib.load(path)
        except FileNotFoundError:
            raise FileNotFoundError(f"Sklearn model not found: {path}")
        except Exception as e:
            raise RuntimeError(f"Failed to load sklearn model '{path}': {e}")

    def _load_transformers_model(self, path: str, **kwargs):
        from transformers import pipeline

        task = kwargs.get("task", "text-classification")
        try:
            return pipeline(task, model=path, device=-1)
        except FileNotFoundError:
            raise FileNotFoundError(f"Transformers model not found: {path}")
        except OSError as e:
            raise OSError(f"Failed to load transformers model '{path}' (network/disk error): {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to load transformers model '{path}': {e}")

    def _load_onnx_model(self, path: str, **kwargs):
        """Load an ONNX model via onnxruntime."""
        try:
            import onnxruntime as ort
        except ImportError:
            raise ImportError(
                "onnxruntime not installed. Install with: pip install onnxruntime"
            )
        providers = kwargs.get(
            "providers",
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if self.config.device == "cuda"
            else ["CPUExecutionProvider"],
        )
        try:
            return ort.InferenceSession(path, providers=providers)
        except FileNotFoundError:
            raise FileNotFoundError(f"ONNX model not found: {path}")
        except Exception as e:
            raise RuntimeError(f"Failed to load ONNX model '{path}': {e}")

    def _load_gguf_model(self, path: str, **kwargs):
        """Load a GGUF model via llama-cpp-python."""
        try:
            from llama_cpp import Llama
        except ImportError:
            raise ImportError(
                "llama-cpp-python not installed. Install with: pip install llama-cpp-python"
            )
        n_ctx = kwargs.get("n_ctx", self.config.max_batch_size or 2048)
        try:
            return Llama(model_path=path, n_ctx=n_ctx, verbose=False)
        except FileNotFoundError:
            raise FileNotFoundError(f"GGUF model not found: {path}")
        except Exception as e:
            raise RuntimeError(f"Failed to load GGUF model '{path}': {e}")

    def _load_vl_model(self, path: str, **kwargs):
        """Register a Vision-Language model path for on-demand loading.

        VL models are loaded per-inference call (image + text) rather than
        pre-loaded, because different inputs may use different processor/model
        combinations.
        """
        if not HAS_PIL:
            raise RuntimeError(
                "PIL/Pillow required for VL models. Install: pip install Pillow"
            )
        if not Path(path).exists():
            raise FileNotFoundError(f"VL model path not found: {path}")
        # Store the path string; actual model loads at inference time
        return path

    async def infer_async(
        self,
        handle: str,
        inputs: np.ndarray | list | dict,
        batch: bool = False,
    ) -> InferenceResult | BatchResult:
        """Async inference — runs synchronously in a thread pool.

        Use this for non-blocking batch processing in async serving contexts.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, functools.partial(cast(Any, self.infer), handle, inputs), batch
        )

    async def infer_stream(
        self,
        handle: str,
        inputs: str | list | dict,
        max_tokens: int = 256,
    ) -> AsyncGenerator[StreamToken, None]:
        """Stream inference results token by token.

        Supports native streaming for GGUF (llama.cpp) and transformers.
        Torch/sklearn/ONNX yield the full result as a single chunk.

        Usage:
            async for token in engine.infer_stream("gguf:model", "Hello"):
                print(token.text, end="", flush=True)
        """
        if handle not in self.loaded_models:
            raise KeyError(f"Model not loaded: {handle}")

        model = self.loaded_models[handle]
        framework = handle.split(":")[0]

        if framework == "gguf":
            async for token in stream_gguf(model, str(inputs), max_tokens):
                yield token
        elif framework == "transformers":
            # Extract tokenizer from pipeline for true token streaming
            tokenizer = model.tokenizer if hasattr(model, "tokenizer") else None
            if tokenizer is None:
                # No tokenizer available — fall back to single-infer + stream_native
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    self._executor, functools.partial(cast(Any, self.infer), handle, inputs)
                )
                async for token in stream_native(result):
                    yield token
            else:
                async for token in stream_transformers(model, tokenizer, str(inputs), max_tokens):
                    yield token
        elif framework in ("torch", "sklearn", "onnx"):
            # Non-streaming frameworks — run and yield full result
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self._executor, functools.partial(cast(Any, self.infer), handle, inputs)
            )
            async for token in stream_native(result):
                yield token
        elif framework == "vl":
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self._executor, functools.partial(cast(Any, self.infer), handle, inputs)
            )
            async for token in stream_native(result):
                yield token
        else:
            raise ValueError(f"Streaming not supported for framework: {framework}")

    def cache_invalidate(self, handle: (str | None) = None):
        """Invalidate cache entries for a handle or the entire cache."""
        if self._cache is not None:
            self._cache.invalidate(handle)

    def _warmup_model(self, handle: str):
        """Run warmup inference on a newly loaded model."""
        if not self.config.warmup_iterations or self.config.warmup_iterations <= 0:
            return
        model = self.loaded_models[handle]
        framework = handle.split(":")[0]
        warmup_input = np.zeros((1, 3), dtype=np.float32)
        for _ in range(self.config.warmup_iterations):
            try:
                if framework in ("torch", "sklearn", "onnx"):
                    self._single_infer(model, framework, warmup_input)
                elif framework in ("gguf", "transformers", "vl"):
                    self._single_infer(model, framework, "warmup")
            except Exception as exc:
                note_failure("engine:L534", exc) # Warmup failures are non-fatal

    def batch_infer(self, handle: str, inputs_list: list) -> BatchResult:
        """Run batched inference (public API for backward compatibility)."""
        # batch=True with a list input always takes infer()'s _batch_infer
        # path, whose result is BatchResult; cast asserts that proven fact.
        return cast(BatchResult, self.infer(handle, inputs_list, batch=True))

    def shutdown(self):
        """Clean up resources — call before discarding the engine."""
        self._executor.shutdown(wait=True)
        self.loaded_models.clear()

    def __del__(self):
        """Ensure executor is shut down on garbage collection."""
        try:
            self._executor.shutdown(wait=False)
        except Exception as exc:
            note_failure("engine:L550", exc)


def create_engine(config_path: (str | None) = None) -> InferenceEngine:
    """Factory function to create an inference engine."""
    config = EveConfig.from_file(config_path) if config_path else EveConfig()
    return InferenceEngine(config)
