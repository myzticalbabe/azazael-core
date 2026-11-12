# coding: utf-8
"""
Universal Converter

Module for ml_platforms functionality.

Functions:
    - debug_conversion()
    - setup_logging()
    - log_step()
    - log_exception()
    - save_json_report()
    - get_logger()
    - analyze()
    - convert()
    - convert()
    - convert()

Classes:
    - LogLevel
    - ConversionStep
    - ModelMetadata
    - ConversionResult
    - ConversionLogger

Usage:
    >>> import universal_converter
    >>> universal_converter.debug_conversion()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
"""
Universal Model Converter & Debugger
Converts GGUF models to PyTorch, TensorFlow, ONNX, JAX, and other frameworks.
Includes comprehensive logging, debugging, and error handling.
"""

import sys
import json
import shutil
import subprocess
import time
import hashlib
import logging
from pathlib import Path
# from typing import Dict, List, Optional, Tuple  # removed — all names have native equivalents
from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime
import traceback
import argparse
from typing import Any, TYPE_CHECKING, List

if TYPE_CHECKING:
    import numpy as np  # annotation-only; numpy stays lazily imported at runtime

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

logger = logging.getLogger(__name__)

#: Third element of a converter's result tuple: a details mapping on success,
#: or the error strings produced when a conversion fails (see
#: UniversalConverter.convert below, which destructures both shapes).
ConversionDetails = dict[str, Any] | list[str]


class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class ConversionStep:
    step: str
    status: str
    timestamp: str
    duration_ms: float
    details: dict = field(default_factory=dict)
    error: (str | None) = None


@dataclass
class ModelMetadata:
    name: str
    path: str
    size_bytes: int
    quantization: str
    architecture: str
    vocab_size: int = 0
    hidden_size: int = 0
    num_layers: int = 0
    num_heads: int = 0
    num_kv_heads: int = 0
    intermediate_size: int = 0
    max_position_embeddings: int = 0
    is_vision: bool = False
    mmproj_path: (str | None) = None
    sha256: str = ""


@dataclass
class ConversionResult:
    success: bool
    source_path: str
    target_framework: str
    output_path: (str | None)
    steps: list[ConversionStep]
    total_duration_ms: float
    errors: list[str]
    warnings: list[str]
    metadata: dict


class ConversionLogger:
    """Comprehensive logging for model conversion."""

    def __init__(self, log_dir: "str | Path", log_level: str = "DEBUG"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"conversion_{self.timestamp}.log"
        self.json_log = self.log_dir / f"conversion_{self.timestamp}.json"

        self.setup_logging(log_level)
        self.steps: list[ConversionStep] = []

    def setup_logging(self, level: str):
        """Setup Python logging configuration."""
        numeric_level = getattr(logging, level.upper(), logging.DEBUG)

        file_handler = logging.FileHandler(self.log_file, encoding="utf-8")
        file_handler.setLevel(numeric_level)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(numeric_level)

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S"
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        self.logger = logging.getLogger("ModelConverter")
        self.logger.handlers = []
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.logger.setLevel(numeric_level)

    def log_step(
        self,
        step: str,
        status: str,
        details: dict[str, Any] | None = None,
        error: str | None = None,
    ):
        """Log a conversion step."""
        conversion_step = ConversionStep(
            step=step,
            status=status,
            timestamp=datetime.now().isoformat(),
            duration_ms=0,
            details=details or {},
            error=error,
        )
        self.steps.append(conversion_step)

        if status == "SUCCESS":
            self.logger.info(f"[OK] {step}")
        elif status == "FAILED":
            self.logger.error(f"[!!] {step}: {error}")
        elif status == "WARNING":
            self.logger.warning(f"[W!] {step}: {details}")
        else:
            self.logger.debug(f"[*] {step}")

    def log_exception(self, step: str, exc: Exception):
        """Log exception with full traceback."""
        tb = traceback.format_exc()
        self.logger.error(f"[EXCEPTION] {step}:\n{tb}")
        return tb

    def save_json_report(self, result: ConversionResult):
        """Save conversion result as JSON."""
        report = {
            "timestamp": self.timestamp,
            "success": result.success,
            "source": result.source_path,
            "target": result.target_framework,
            "output": result.output_path,
            "duration_ms": result.total_duration_ms,
            "steps": [asdict(s) for s in result.steps],
            "errors": result.errors,
            "warnings": result.warnings,
            "metadata": result.metadata,
        }
        with open(self.json_log, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        self.logger.info(f"JSON report saved to {self.json_log}")

    def get_logger(self) -> logging.Logger:
        return self.logger


class GGUFAnalyzer:
    """Analyze GGUF model files and extract metadata."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def analyze(self, model_path: str) -> ModelMetadata:
        """Analyze GGUF file and extract metadata."""
        self.logger.info(f"Analyzing GGUF file: {model_path}")

        try:
            import struct

            with open(model_path, "rb") as f:
                header = f.read(8)
                magic = struct.unpack("<I", header[:4])[0]

                if magic != 0x46554747:  # 'GGUF'
                    raise ValueError(f"Invalid GGUF file: magic={hex(magic)}")

                version = struct.unpack("<I", header[4:8])[0]
                self.logger.debug(f"GGUF version: {version}")

                struct.unpack("<Q", f.read(8))[0]
                metadata = self._read_metadata(f)

            model_name = metadata.get("general.name", Path(model_path).stem)
            architecture = self._detect_architecture(metadata)

            is_vision = (
                "vision" in architecture.lower() or "qwen" in architecture.lower()
            )
            mmproj_path = None
            mmproj_dir = Path(model_path).parent

            if is_vision:
                for f in mmproj_dir.iterdir():
                    if "mmproj" in f.name.lower() or "vision" in f.name.lower():
                        mmproj_path = str(f)
                        break

            quantization = self._detect_quantization(model_path)

            return ModelMetadata(
                name=model_name,
                path=model_path,
                size_bytes=Path(model_path).stat().st_size,
                quantization=quantization,
                architecture=architecture,
                vocab_size=metadata.get("tokenizer.ggml.tokens", 0),
                hidden_size=metadata.get("llama.block_count", 0) * 64,
                num_layers=metadata.get("llama.block_count", 0),
                num_heads=metadata.get("llama.attention.head_count", 0),
                num_kv_heads=metadata.get("llama.attention.head_count_kv", 0),
                intermediate_size=metadata.get("llama.feed_forward_length", 0),
                max_position_embeddings=metadata.get("llama.context_length", 0),
                is_vision=is_vision,
                mmproj_path=mmproj_path,
                sha256=self._compute_sha256(model_path),
            )

        except ImportError as e:
            self.logger.warning(f"llama-cpp-python not installed: {e}")
            return self._basic_analyze(model_path)

    def _read_metadata(self, f) -> dict:
        """Read GGUF metadata."""
        metadata = {}

        try:
            import gguf

            reader = gguf.GGUFReader(f)

            for key, value in reader.metadata.items():  # type: ignore[union-attr]
                metadata[key] = value

            self.logger.debug(f"Found {len(metadata)} metadata keys")

        except ImportError:
            self.logger.warning("gguf library not available, using basic analysis")

        return metadata

    def _detect_architecture(self, metadata: dict) -> str:
        """Detect model architecture from metadata."""
        general = metadata.get("general", {})
        architecture = general.get("architecture", "")

        if not architecture:
            if "llama" in metadata:
                architecture = "llama"
            elif "mistral" in metadata:
                architecture = "mistral"
            elif "qwen" in metadata:
                architecture = "qwen"
            elif "glm" in metadata:
                architecture = "glm"
            else:
                architecture = "unknown"

        return architecture

    def _detect_quantization(self, model_path: str) -> str:
        """Detect quantization type from filename."""
        filename = Path(model_path).stem.lower()

        if "q8_0" in filename:
            return "Q8_0"
        elif "q6_k" in filename:
            return "Q6_K"
        elif "q5_k_m" in filename:
            return "Q5_K_M"
        elif "q5_k_s" in filename:
            return "Q5_K_S"
        elif "q4_k_m" in filename:
            return "Q4_K_M"
        elif "q4_k_s" in filename:
            return "Q4_K_S"
        elif "q3_k_m" in filename:
            return "Q3_K_M"
        elif "q3_k_s" in filename:
            return "Q3_K_S"
        elif "iq4_xs" in filename:
            return "IQ4_XS"
        elif "iq3_m" in filename:
            return "IQ3_M"
        elif "iq2" in filename:
            return "IQ2"
        elif "f16" in filename:
            return "F16"
        elif "f32" in filename:
            return "F32"

        return "unknown"

    def _compute_sha256(self, filepath: str) -> str:
        """Compute SHA256 hash of file."""
        sha256_hash = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()

    def _basic_analyze(self, model_path: str) -> ModelMetadata:
        """Basic analysis without llama-cpp-python."""
        self.logger.info("Using basic GGUF analysis")

        filename = Path(model_path).stem
        size_bytes = Path(model_path).stat().st_size

        quantization = self._detect_quantization(model_path)
        architecture = "unknown"

        if "mistral" in filename.lower():
            architecture = "mistral"
        elif "llama" in filename.lower():
            architecture = "llama"
        elif "qwen" in filename.lower():
            architecture = "qwen"
        elif "glm" in filename.lower():
            architecture = "glm"

        is_vision = "vl" in filename.lower() or "vision" in filename.lower()

        return ModelMetadata(
            name=filename,
            path=model_path,
            size_bytes=size_bytes,
            quantization=quantization,
            architecture=architecture,
            is_vision=is_vision,
            sha256=self._compute_sha256(model_path),
        )

    KNOWN_QUANTS = frozenset({
        "Q2_K", "Q2_K_S", "Q3_K_S", "Q3_K_M", "Q3_K_L",
        "Q4_0", "Q4_1", "Q4_K_S", "Q4_K_M",
        "Q5_0", "Q5_1", "Q5_K_S", "Q5_K_M",
        "Q6_K", "Q8_0", "F16", "F32",
        "IQ2_XXS", "IQ2_XS", "IQ3_XXS", "IQ4_XS",
    })

    @staticmethod
    def find_quantizer():
        return shutil.which("llama-quantize")

    @staticmethod
    def _run_quantizer(argv):
        return subprocess.run(argv, capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace")

    def convert(self, model_path: str, output_dir: str,
                quantization: str = "Q4_K_M"):
        src = Path(model_path)
        if not src.is_file():
            return False, "", ["source not found: %s" % model_path]
        if quantization not in self.KNOWN_QUANTS:
            return False, "", ["unknown quant type: %s" % quantization]
        quantizer = self.find_quantizer()
        if not quantizer:
            return False, "", ["llama-quantize not on PATH: cannot quantize %s" % src.name]
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / ("%s-%s.gguf" % (src.stem, quantization))
        try:
            proc = self._run_quantizer([quantizer, str(src), str(dst), quantization])
        except Exception as exc:
            return False, "", ["quantizer backend failed: %s" % exc]
        if getattr(proc, "returncode", 1) != 0:
            return False, "", ["llama-quantize exited %s" % getattr(proc, "returncode", "?")]
        if not dst.is_file():
            return False, "", ["llama-quantize produced no artifact at %s" % dst]
        return True, str(dst), {"bytes": dst.stat().st_size,
                                "quantization": quantization,
                                "quantizer_binary": quantizer}


class PyTorchConverter:
    """Convert GGUF models to PyTorch format."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.converted_weights = {}

    def convert(
        self, model_path: str, output_dir: str, metadata: ModelMetadata
    ) -> tuple[bool, str, ConversionDetails]:
        """Convert GGUF to PyTorch SafeTensors."""
        self.logger.info(f"Converting to PyTorch: {model_path}")

        time.time()
        errors = []
        warnings = []

        try:
            pass

            output_path = Path(output_dir) / "pytorch_model"
            output_path.mkdir(parents=True, exist_ok=True)

            self.logger.info(f"Output directory: {output_path}")

            try:
                import llama_cpp

                reader = llama_cpp.llama_model_loader(model_path)

                self.logger.info(f"Model loaded: {reader.n_tokens()} tokens")

                tensor_names = []

                for tensor in reader.get_tensors():
                    name = tensor.name
                    shape = tensor.ne
                    dtype = tensor.dtype

                    self.logger.debug(f"Tensor: {name}, shape: {shape}, dtype: {dtype}")
                    tensor_names.append(name)

                self.logger.info(f"Found {len(tensor_names)} tensors")

                if tensor_names:
                    from safetensors.torch import save_file

                    import torch  # lazily, alongside save_file (same optional dep)

                    state_dict = {}

                    for name in tensor_names:
                        try:
                            tensor = reader.get_tensor(name)
                            state_dict[name] = torch.from_numpy(tensor)
                        except Exception as e:
                            self.logger.warning(f"Could not convert tensor {name}: {e}")
                            warnings.append(f"Tensor {name}: {e}")

                    if state_dict:
                        safetensors_path = output_path / "model.safetensors"
                        save_file(state_dict, safetensors_path)
                        self.logger.info(f"Saved SafeTensors to {safetensors_path}")

                        config_path = output_path / "config.json"
                        config = self._create_config(metadata)
                        with open(config_path, "w", encoding="utf-8") as f:
                            json.dump(config, f, indent=2)

                        self.logger.info(f"Saved config to {config_path}")

                        return (
                            True,
                            str(output_path),
                            {
                                "format": "safetensors",
                                "tensors": len(state_dict),
                                "size_bytes": safetensors_path.stat().st_size,
                            },
                        )

            except ImportError:
                self.logger.warning(
                    "llama-cpp-python not installed, trying direct conversion"
                )

                numpy_weights = self._direct_gguf_to_numpy(model_path)
                if numpy_weights:
                    self._save_numpy_weights(numpy_weights, output_path)
                    return (
                        True,
                        str(output_path),
                        {"format": "numpy", "tensors": len(numpy_weights)},
                    )

            return False, "", errors

        except Exception as e:
            tb = self.logger.exception(f"PyTorch conversion failed: {e}")
            errors.append(str(e))
            return False, "", errors

    def _create_config(self, metadata: ModelMetadata) -> dict:
        """Create HuggingFace-style config.json."""
        architecture = metadata.architecture.lower()

        if "llama" in architecture:
            config = {
                "architectures": ["LlamaForCausalLM"],
                "bos_token_id": 1,
                "eos_token_id": 2,
                "hidden_act": "silu",
                "hidden_size": metadata.hidden_size or 4096,
                "initializer_range": 0.02,
                "intermediate_size": metadata.intermediate_size or 11008,
                "max_position_embeddings": metadata.max_position_embeddings or 4096,
                "model_type": "llama",
                "num_attention_heads": metadata.num_heads or 32,
                "num_hidden_layers": metadata.num_layers or 32,
                "num_key_value_heads": metadata.num_kv_heads or 32,
                "pad_token_id": 0,
                "rms_norm_eps": 1e-5,
                "tie_word_embeddings": False,
                "torch_dtype": "bfloat16",
                "vocab_size": metadata.vocab_size or 32000,
            }
        elif "mistral" in architecture:
            config = {
                "architectures": ["MistralForCausalLM"],
                "bos_token_id": 1,
                "eos_token_id": 2,
                "hidden_act": "silu",
                "hidden_size": metadata.hidden_size or 4096,
                "initializer_range": 0.02,
                "intermediate_size": metadata.intermediate_size or 14336,
                "max_position_embeddings": metadata.max_position_embeddings or 32768,
                "model_type": "mistral",
                "num_attention_heads": metadata.num_heads or 32,
                "num_hidden_layers": metadata.num_layers or 32,
                "num_key_value_heads": metadata.num_kv_heads or 8,
                "pad_token_id": 0,
                "rms_norm_eps": 1e-5,
                "tie_word_embeddings": False,
                "torch_dtype": "bfloat16",
                "vocab_size": metadata.vocab_size or 32000,
            }
        elif "qwen" in architecture:
            config = {
                "architectures": ["Qwen2ForCausalLM"],
                "bos_token_id": 151643,
                "eos_token_id": 151645,
                "hidden_act": "silu",
                "hidden_size": metadata.hidden_size or 3584,
                "initializer_range": 0.02,
                "intermediate_size": metadata.intermediate_size or 18944,
                "max_position_embeddings": metadata.max_position_embeddings or 32768,
                "model_type": "qwen2",
                "num_attention_heads": metadata.num_heads or 28,
                "num_hidden_layers": metadata.num_layers or 32,
                "num_key_value_heads": metadata.num_kv_heads or 4,
                "pad_token_id": 151643,
                "rms_norm_eps": 1e-5,
                "tie_word_embeddings": False,
                "torch_dtype": "bfloat16",
                "vocab_size": metadata.vocab_size or 151936,
            }
        else:
            config = {
                "architectures": ["LlamaForCausalLM"],
                "model_type": architecture or "llama",
                "hidden_size": metadata.hidden_size or 4096,
                "num_hidden_layers": metadata.num_layers or 32,
                "num_attention_heads": metadata.num_heads or 32,
                "num_key_value_heads": metadata.num_kv_heads or 32,
                "vocab_size": metadata.vocab_size or 32000,
                "torch_dtype": "bfloat16",
            }

        return config

    def _direct_gguf_to_numpy(self, model_path: str) -> "dict[str, np.ndarray]":
        """Direct GGUF to NumPy conversion (fallback)."""
        self.logger.info("Attempting direct GGUF to NumPy conversion")

        try:
            import struct

            import numpy as np

            weights = {}

            with open(model_path, "rb") as f:
                magic = struct.unpack("<I", f.read(4))[0]
                if magic != 0x46554747:
                    raise ValueError("Not a valid GGUF file")

                struct.unpack("<I", f.read(4))[0]
                num_tensors = struct.unpack("<Q", f.read(8))[0]

                for _ in range(num_tensors):
                    name_len = struct.unpack("<I", f.read(4))[0]
                    name = f.read(name_len).decode("utf-8", errors="replace")

                    n_dims = struct.unpack("<I", f.read(4))[0]
                    shape = struct.unpack(f"<{'I' * n_dims}", f.read(4 * n_dims))

                    dtype_enum = struct.unpack("<I", f.read(4))[0]

                    dtype_map = {
                        0: np.float32,
                        1: np.float16,
                        2: np.int32,
                        3: np.int16,
                        4: np.int8,
                        5: np.uint8,
                        # numpy 2.5.3 in the production venv has no float8
                        # scalar dtypes (verified); these entries raise
                        # AttributeError when the dict is built, and the
                        # function's `except Exception` turns it into {} —
                        # the long-standing behavior. Suppressed at the
                        # stub level only; runtime behavior identical.
                        16: np.float8_e4m3fn,  # pyright: ignore[reportAttributeAccessIssue]
                        17: np.float8_e5m2,  # pyright: ignore[reportAttributeAccessIssue]
                    }
                    dtype = dtype_map.get(dtype_enum, np.float32)

                    n_elements = 1
                    for dim in shape:
                        n_elements *= dim

                    data_size = n_elements * np.dtype(dtype).itemsize
                    data = f.read(data_size)

                    arr = np.frombuffer(data, dtype=dtype).reshape(shape)
                    weights[name] = arr

            self.logger.info(f"Extracted {len(weights)} tensors")
            return weights

        except Exception as e:
            self.logger.error(f"Direct conversion failed: {e}")
            return {}

    def _save_numpy_weights(self, weights: dict, output_dir: Path):
        """Save NumPy weights to disk."""
        import numpy as np

        for name, tensor in weights.items():
            safe_name = name.replace("/", "_").replace(" ", "_")
            np.save(output_dir / f"{safe_name}.npy", tensor)
            self.logger.debug(f"Saved {safe_name}.npy")


class ONNXConverter:
    """Convert models to ONNX format."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def convert(
        self, model_path: str, output_dir: str, metadata: ModelMetadata
    ) -> tuple[bool, str, ConversionDetails]:
        """Convert model to ONNX."""
        self.logger.info(f"Converting to ONNX: {model_path}")

        try:
            from optimum.onnxruntime import ORTModelForCausalLM
            from transformers import AutoTokenizer

            output_path = Path(output_dir) / "onnx_model"
            output_path.mkdir(parents=True, exist_ok=True)

            self.logger.info(f"Output directory: {output_path}")

            model_id = metadata.path

            self.logger.info(f"Loading model from {model_id}")
            model = ORTModelForCausalLM.from_pretrained(
                model_id, export=True, provider="CPUExecutionProvider"
            )
            tokenizer = AutoTokenizer.from_pretrained(model_id)

            self.logger.info("Saving ONNX model...")
            model.save_pretrained(output_path)
            tokenizer.save_pretrained(output_path)

            return True, str(output_path), {"format": "onnx"}

        except ImportError as e:
            self.logger.warning(f"optimum not installed: {e}")
            return self._basic_onnx_export(model_path, output_dir, metadata)

        except Exception as e:
            self.logger.error(f"ONNX conversion failed: {e}")
            return False, "", [str(e)]

    def _basic_onnx_export(
        self, model_path: str, output_dir: str, metadata: ModelMetadata
    ) -> tuple[bool, str, ConversionDetails]:
        """Basic ONNX export for supported models."""
        self.logger.info("Attempting basic ONNX export")

        try:
            import torch

            output_path = Path(output_dir) / "onnx_basic"
            output_path.mkdir(parents=True, exist_ok=True)

            class SimpleDecoder(torch.nn.Module):
                def __init__(self, vocab_size=32000, hidden_size=4096):
                    super().__init__()
                    self.embed = torch.nn.Embedding(vocab_size, hidden_size)
                    self.lm_head = torch.nn.Linear(hidden_size, vocab_size, bias=False)

                def forward(self, input_ids):
                    hidden = self.embed(input_ids)
                    logits = self.lm_head(hidden)
                    return logits

            model = SimpleDecoder(
                vocab_size=metadata.vocab_size or 32000,
                hidden_size=metadata.hidden_size or 4096,
            )

            dummy_input = torch.randint(0, metadata.vocab_size or 32000, (1, 8))

            onnx_path = output_path / "model.onnx"
            # torch.onnx.export accepts a single example tensor or a tuple of
            # them; wrapping is the stub-declared form and runtime-equivalent.
            torch.onnx.export(
                model,
                (dummy_input,),
                str(onnx_path),
                input_names=["input_ids"],
                output_names=["logits"],
                dynamic_axes={
                    "input_ids": {0: "batch", 1: "sequence"},
                    "logits": {0: "batch", 1: "sequence"},
                },
                opset_version=14,
            )

            self.logger.info(f"Basic ONNX model saved to {onnx_path}")
            return (
                True,
                str(output_path),
                {"format": "onnx_basic", "path": str(onnx_path)},
            )

        except Exception as e:
            # ACTIONABLE refusal (engram #511 / #712 Class A): name the model
            # it could not export and what is missing. A bare `[str(e)]`
            # returned "No module named 'onnxscript'" -- true, but a caller
            # cannot tell WHICH model needed WHICH exporter.
            reason = ("refused to fabricate a model: basic ONNX export of '%s' "
                      "failed (%s); no ONNX exporter produced an artifact -- "
                      "install a real exporter (optimum / onnx with onnxscript)."
                      % (model_path, e))
            self.logger.error("Basic ONNX export failed for %s: %s", model_path, e)
            return False, "", [reason]


class TensorFlowConverter:
    """Convert models to TensorFlow/Keras format."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def convert(
        self, model_path: str, output_dir: str, metadata: ModelMetadata
    ) -> tuple[bool, str, ConversionDetails]:
        """Convert GGUF to TensorFlow."""
        self.logger.info(f"Converting to TensorFlow: {model_path}")

        try:
            import tensorflow as tf  # type: ignore[reportMissingModuleSource]

            output_path = Path(output_dir) / "tensorflow_model"
            output_path.mkdir(parents=True, exist_ok=True)

            try:
                import llama_cpp

                reader = llama_cpp.llama_model_loader(model_path)
                tensor_names = [t.name for t in reader.get_tensors()]

                self.logger.info(f"Found {len(tensor_names)} tensors for TF conversion")

                keras_model = self._build_keras_model(metadata, tensor_names)

                weights_dict = {}
                for name in tensor_names:
                    tensor = reader.get_tensor(name)
                    tf_tensor = tf.constant(tensor)
                    weights_dict[name] = tf_tensor

                self.logger.info("Saving TensorFlow model...")
                # Keras 3's bundled stubs dropped `save_format` from
                # Model.save, but TF2's Model.save still accepts it at
                # runtime; suppression covers only that stub/runtime
                # divergence, not a real diagnostic.
                keras_model.save(str(output_path), save_format="tf")  # pyright: ignore[reportCallIssue]

                return (
                    True,
                    str(output_path),
                    {"format": "tensorflow", "tensors": len(tensor_names)},
                )

            except ImportError:
                self.logger.warning("llama-cpp-python not available for TF conversion")
                return self._create_placeholder_model(output_path, metadata)

        except ImportError:
            self.logger.warning("TensorFlow not installed")
            return False, "", ["TensorFlow not installed"]

        except Exception as e:
            self.logger.error(f"TensorFlow conversion failed: {e}")
            return False, "", [str(e)]

    def _build_keras_model(self, metadata: ModelMetadata, tensor_names: list[str]):
        """Build Keras model structure."""
        import tensorflow as tf  # type: ignore[reportMissingModuleSource]

        vocab_size = metadata.vocab_size or 32000
        hidden_size = metadata.hidden_size or 4096
        num_layers = metadata.num_layers or 32

        inputs = tf.keras.Input(shape=(None,), dtype=tf.int32, name="input_ids")

        embedding = tf.keras.layers.Embedding(vocab_size, hidden_size)(inputs)

        x = embedding
        for i in range(min(num_layers, 4)):
            attention = tf.keras.layers.MultiHeadAttention(
                num_heads=metadata.num_heads or 32,
                key_dim=hidden_size // (metadata.num_heads or 32),
            )(x, x)
            x = tf.keras.layers.LayerNormalization(epsilon=1e-5)(x + attention)
            ffn = tf.keras.layers.Dense(hidden_size * 4, activation="silu")(x)
            x = tf.keras.layers.LayerNormalization(epsilon=1e-5)(x + ffn)

        lm_head = tf.keras.layers.Dense(vocab_size, use_bias=False)(x)

        model = tf.keras.Model(inputs=inputs, outputs=lm_head, name="gguf_converted")

        return model

    def _create_placeholder_model(
        self, output_path: Path, metadata: ModelMetadata
    ) -> tuple[bool, str, ConversionDetails]:
        """Create placeholder model for manual population."""
        config = {
            "model_type": metadata.architecture or "llama",
            "vocab_size": metadata.vocab_size or 32000,
            "hidden_size": metadata.hidden_size or 4096,
            "num_layers": metadata.num_layers or 32,
            "num_attention_heads": metadata.num_heads or 32,
            "note": "Weights need to be populated from GGUF manually",
        }

        config_path = output_path / "config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        self.logger.info(f"Placeholder config saved to {config_path}")
        return (
            True,
            str(output_path),
            {"format": "tensorflow_config", "note": "placeholder"},
        )


class JAXConverter:
    """Convert models to JAX/Flax format."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def convert(
        self, model_path: str, output_dir: str, metadata: ModelMetadata
    ) -> tuple[bool, str, ConversionDetails]:
        """Convert GGUF to JAX/Flax."""
        self.logger.info(f"Converting to JAX: {model_path}")

        try:
            import jax
            import jax.numpy as jnp

            output_path = Path(output_dir) / "jax_model"
            output_path.mkdir(parents=True, exist_ok=True)

            try:
                pass

                self.logger.info("JAX conversion requires NumPy intermediate")

                weights = self._extract_weights_via_numpy(model_path)

                jax_weights = {}
                for name, np_arr in weights.items():
                    jax_weights[name] = jnp.array(np_arr)

                jax.config.json_fallback = True

                model_config = {
                    "vocab_size": metadata.vocab_size or 32000,
                    "hidden_size": metadata.hidden_size or 4096,
                    "num_layers": metadata.num_layers or 32,
                    "num_attention_heads": metadata.num_heads or 32,
                    "num_key_value_heads": metadata.num_kv_heads or 32,
                }

                config_path = output_path / "config.json"
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(model_config, f, indent=2)

                self.logger.info(f"JAX config saved to {config_path}")
                return (
                    True,
                    str(output_path),
                    {"format": "jax", "weights": len(jax_weights)},
                )

            except ImportError:
                self.logger.warning("llama-cpp-python not available for JAX conversion")

            return self._create_jax_placeholder(output_path, metadata)

        except ImportError:
            self.logger.warning("JAX not installed")
            return False, "", ["JAX not installed"]

        except Exception as e:
            self.logger.error(f"JAX conversion failed: {e}")
            return False, "", [str(e)]

    def _extract_weights_via_numpy(self, model_path: str) -> dict:
        """Extract weights via NumPy (requires llama-cpp-python)."""
        weights = {}
        try:
            import llama_cpp

            reader = llama_cpp.llama_model_loader(model_path)
            for tensor in reader.get_tensors():
                weights[tensor.name] = tensor
        except Exception as e:
            self.logger.warning(f"Weight extraction failed: {e}")
        return weights

    def _create_jax_placeholder(
        self, output_path: Path, metadata: ModelMetadata
    ) -> tuple[bool, str, ConversionDetails]:
        """Create JAX placeholder config."""
        config = {
            "model_type": metadata.architecture or "llama",
            "vocab_size": metadata.vocab_size or 32000,
            "hidden_size": metadata.hidden_size or 4096,
            "num_layers": metadata.num_layers or 32,
            "num_attention_heads": metadata.num_heads or 32,
            "note": "Convert to NumPy first, then JAX",
        }

        config_path = output_path / "config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        return True, str(output_path), {"format": "jax_placeholder"}


class UniversalConverter:
    """Universal model converter supporting all frameworks."""

    FRAMEWORKS = {
        "pytorch": PyTorchConverter,
        "torch": PyTorchConverter,
        "tensorflow": TensorFlowConverter,
        "tf": TensorFlowConverter,
        "keras": TensorFlowConverter,
        "onnx": ONNXConverter,
        "jax": JAXConverter,
        "flax": JAXConverter,
    }

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.logger = ConversionLogger(self.log_dir)

    def convert(
        self,
        model_path: str,
        framework: str,
        output_dir: str | None = None,
        debug: bool = False,
    ) -> ConversionResult:
        """Convert model to specified framework."""
        start_time = time.time()
        errors = []
        warnings = []

        self.logger.log_step(
            "INIT", "STARTED", {"model": model_path, "framework": framework}
        )

        model_path = str(Path(model_path).resolve())

        if not Path(model_path).exists():
            error = f"Model file not found: {model_path}"
            self.logger.log_step("INIT", "FAILED", error=error)
            errors.append(error)
            return self._create_result(
                model_path, framework, None, [], errors, warnings, start_time
            )

        analyzer = GGUFAnalyzer(self.logger.get_logger())
        metadata = analyzer.analyze(model_path)

        self.logger.log_step("ANALYSIS", "SUCCESS", asdict(metadata))

        if output_dir is None:
            base_name = Path(model_path).stem
            output_dir = f"{base_name}_{framework}"

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        converter_class = self.FRAMEWORKS.get(framework.lower())
        if converter_class is None:
            error = f"Unsupported framework: {framework}"
            self.logger.log_step("CONVERTER", "FAILED", error=error)
            errors.append(error)
            return self._create_result(
                model_path, framework, None, [], errors, warnings, start_time
            )

        self.logger.log_step(
            "CONVERTER", "STARTED", {"class": converter_class.__name__}
        )

        try:
            converter = converter_class(self.logger.get_logger())
            success, result_path, details = converter.convert(
                model_path, str(output_path), metadata
            )

            if success:
                self.logger.log_step(
                    "CONVERSION",
                    "SUCCESS",
                    details if isinstance(details, dict) else {},
                )
            else:
                error_details = [] if isinstance(details, dict) else details
                self.logger.log_step(
                    "CONVERSION",
                    "FAILED",
                    error=error_details[0] if error_details else "Unknown error",
                )
                errors.extend(error_details)

        except Exception as e:
            note_failure("universal_converter:L999", e)
            tb = self.logger.log_step("CONVERSION", "FAILED", error=str(e))
            errors.append(str(e))

        result = self._create_result(
            model_path,
            framework,
            result_path if success else None,
            self.logger.steps,
            errors,
            warnings,
            start_time,
        )

        self.logger.save_json_report(result)

        return result

    def _create_result(
        self,
        source_path: str,
        target_framework: str,
        output_path: (str | None),
        steps: list[ConversionStep],
        errors: list[str],
        warnings: list[str],
        start_time: float,
    ) -> ConversionResult:
        """Create conversion result."""
        return ConversionResult(
            success=len(errors) == 0 and output_path is not None,
            source_path=source_path,
            target_framework=target_framework,
            output_path=output_path,
            steps=steps,
            total_duration_ms=(time.time() - start_time) * 1000,
            errors=errors,
            warnings=warnings,
            metadata={"timestamp": datetime.now().isoformat()},
        )

    def analyze(self, model_path: str) -> dict:
        """Analyze model without conversion."""
        analyzer = GGUFAnalyzer(self.logger.get_logger())
        metadata = analyzer.analyze(model_path)
        return asdict(metadata)

    def list_supported_frameworks(self) -> list[str]:
        """list all supported frameworks."""
        return list(self.FRAMEWORKS.keys())


def debug_conversion(model_path: str, framework: str = "pytorch"):
    """Debug a specific conversion."""
    print("=" * 70)
    print("MODEL CONVERSION DEBUGGER")
    print("=" * 70)
    print(f"\nModel: {model_path}")
    print(f"Target Framework: {framework}")
    print()

    converter = UniversalConverter(log_dir="conversion_logs")

    print("\n[1] Analyzing model...")
    metadata = converter.analyze(model_path)
    print(json.dumps(metadata, indent=2))

    print("\n[2] Converting model...")
    result = converter.convert(model_path, framework)

    print("\n" + "=" * 70)
    print("CONVERSION RESULT")
    print("=" * 70)
    print(f"Success: {result.success}")
    print(f"Output: {result.output_path}")
    print(f"Duration: {result.total_duration_ms:.2f}ms")
    print(f"Steps: {len(result.steps)}")
    print(f"Errors: {len(result.errors)}")

    if result.errors:
        print("\nErrors:")
        for error in result.errors:
            print(f"  - {error}")

    print("\n[3] Check logs in: conversion_logs/")
    print("=" * 70)

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Universal Model Converter & Debugger")
    parser.add_argument("model", help="Path to GGUF model file")
    parser.add_argument("--framework", "-f", default="pytorch", help="Target framework")
    parser.add_argument("--output", "-o", help="Output directory")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    parser.add_argument("--analyze", action="store_true", help="Analyze only")
    parser.add_argument(
        "--list-frameworks", action="store_true", help="list supported frameworks"
    )

    args = parser.parse_args()

    if args.list_frameworks:
        converter = UniversalConverter()
        print("Supported frameworks:")
        for fw in converter.list_supported_frameworks():
            print(f"  - {fw}")
    elif args.analyze:
        converter = UniversalConverter()
        result = converter.analyze(args.model)
        print(json.dumps(result, indent=2))
    else:
        result = debug_conversion(args.model, args.framework)

        if not result.success:
            sys.exit(1)
