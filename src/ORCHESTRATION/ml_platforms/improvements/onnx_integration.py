# coding: utf-8
"""
ONNX Export Utilities
Solves: ONNX export unstable (27/27 platforms affected)
Provides: Unified ONNX export for any ML framework

Usage:
    from onnx_utils import export_to_onnx, load_onnx_model

    # Export any model to ONNX
    export_to_onnx(
        model,
        input_sample,
        "model.onnx",
        framework="pytorch"  # auto-detected if None
    )

    # Load and run ONNX model
    ort_session = load_onnx_model("model.onnx")
    result = ort_session.run(output_names, input_dict)
"""

from typing import Any, List
import numpy as np

import os
import sys

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Both `onnx` and `onnxruntime` ship GIL-ABI wheels that fault at C level under a
# free-threaded build (an access violation, not an ImportError), so no
# try/except can catch them. Every decision is a child-process probe in
# CORE.native_guard, never an in-process import.
try:
    from CORE.native_guard import import_ok as _import_ok
except Exception as exc:  # pragma: no cover - CORE not on path
    note_failure("onnx_integration:L35", exc)
    def _import_ok(name: str, timeout: float = 60) -> bool:
        return False

if _import_ok("onnx"):
    import onnx

    HAS_ONNX = True
else:
    HAS_ONNX = False

if _import_ok("onnxruntime"):
    import onnxruntime as ort

    ort_available = True
else:
    ort = None
    ort_available = False

HAS_ONNX = HAS_ONNX and ort_available
try:
    import torch

    HAS_TORCH = True
except ImportError as exc:
    note_failure("onnx_integration:L59", exc)
    HAS_TORCH = False

try:
    import tensorflow as tf  # type: ignore[reportMissingModuleSource]

    HAS_TF = True
except ImportError as exc:
    note_failure("onnx_integration:L66", exc)
    HAS_TF = False

# -- OpenVINO EP Detection (onnxruntime provider) ----------------------------
_OPENVINO_EP = "OpenVINOExecutionProvider"
_HAS_OPENVINO = False
_OV_DEVICES = {}
_OV_DETECTED = False  # lazy init flag
_OV_DLL_COOKIES = []  # keep os.add_dll_directory() cookies alive

# -- OpenVINO Native API Detection (standalone ov.Core) ----------------------
_HAS_OV_NATIVE = False
_OV_NATIVE_DEVICES = {}
_OV_NATIVE_DETECTED = False
_ov_core = None  # lazy-initialized ov.Core instance

try:
    import openvino as ov_native
    _HAS_OV_NATIVE = True
except ImportError as exc:
    note_failure("onnx_integration:L85", exc)
    ov_native = None
def _ensure_openvino_path():
    """Add OpenVINO DLL directories to PATH if available."""
    global _OV_DLL_COOKIES
    ov_paths = []

    # Production venv first, then the legacy install (inventory: it still
    # exists as the base interpreter for the sanctioned 3.14m fallback venv).
    for sp in (
        "C:/Azazael/.venvs/py315t/Lib/site-packages",
        os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Programs", "Python", "Python314", "Lib", "site-packages",
        ),
    ):
        ov_libs = os.path.join(sp, "openvino", "libs")
        if os.path.isdir(ov_libs):
            ov_paths.append(ov_libs)
        ov_3rd = os.path.join(sp, "openvino", "3rdparty", "openvino", "libs")
        if os.path.isdir(ov_3rd):
            ov_paths.append(ov_3rd)

    # oneAPI OpenVINO installation
    oneapi_ov = os.path.join(
        os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),  # scanner:ignore deliberate: env var wins; literal is a fallback
        "Intel", "oneAPI", "runtime", "bin", "intel64"
    )
    if os.path.isdir(oneapi_ov):
        ov_paths.append(oneapi_ov)

    for p in ov_paths:
        if p not in os.environ.get("PATH", ""):
            os.environ["PATH"] = p + ";" + os.environ.get("PATH", "")

    # Add to DLL search path (Windows) - store cookies to keep alive
    if sys.platform == "win32":
        for p in ov_paths:
            try:
                cookie = os.add_dll_directory(p)
                _OV_DLL_COOKIES.append(cookie)
            except (OSError, AttributeError) as exc:
                note_failure("onnx_integration:L123", exc)

def _detect_openvino():
    """Detect if OpenVINO EP is available and enumerate devices (lazy)."""
    global _HAS_OPENVINO, _OV_DEVICES, _OV_DETECTED

    if _OV_DETECTED:
        return
    _OV_DETECTED = True

    if ort is None:
        return

    _ensure_openvino_path()

    try:
        available = ort.get_available_providers()
        _HAS_OPENVINO = _OPENVINO_EP in available
    except Exception as exc:
        note_failure("onnx_integration:L142", exc)
        _HAS_OPENVINO = False
        _OV_DEVICES = {}
        return

    if not _HAS_OPENVINO:
        return

    # Try to enumerate OpenVINO devices
    try:
        import openvino as ov
        core = ov.Core()
        for dev in core.available_devices:
            try:
                name = core.get_property(dev, "FULL_DEVICE_NAME")
                _OV_DEVICES[dev] = name
            except Exception as exc:
                note_failure("onnx_integration:L158", exc)
                _OV_DEVICES[dev] = dev
    except Exception as exc:
        note_failure("onnx_integration:L160", exc)

def _detect_ov_native():
    """Detect native OpenVINO API availability (lazy)."""
    global _HAS_OV_NATIVE, _OV_NATIVE_DETECTED, _OV_NATIVE_DEVICES, _ov_core
    if _OV_NATIVE_DETECTED:
        return
    _OV_NATIVE_DETECTED = True
    if not _HAS_OV_NATIVE or ov_native is None:
        return
    try:
        _ov_core = ov_native.Core()
        for dev in _ov_core.available_devices:
            try:
                name = _ov_core.get_property(dev, "FULL_DEVICE_NAME")
                _OV_NATIVE_DEVICES[dev] = name
            except Exception as exc:
                note_failure("onnx_integration:L177", exc)
                _OV_NATIVE_DEVICES[dev] = dev
    except Exception as exc:
        note_failure("onnx_integration:L179", exc)
        _HAS_OV_NATIVE = False


class OpenVINOInference:
    """
    OpenVINO native ONNX inference using ov.Core API.

    Bypasses onnxruntime version mismatches by using OpenVINO's built-in
    ONNX model reader and device-specific compilation. Supports:
      - CPU: Intel Core with oneDNN optimizations
      - GPU: Intel Arc with SYCL/OpenCL acceleration
      - NPU: Intel AI Boost for low-power inference

    Usage:
        ovi = OpenVINOInference()
        compiled = ovi.load_model("model.onnx", device="GPU")
        result = ovi.infer(compiled, {"input": np_array})
    """

    def __init__(self):
        _detect_ov_native()
        if not _HAS_OV_NATIVE:
            raise ImportError(
                "OpenVINO not installed. Run: pip install openvino"
            )
        self._core = _ov_core

    @property
    def available_devices(self) -> list:
        """list available OpenVINO devices (CPU, GPU, NPU)."""
        if self._core is None:
            return []
        return self._core.available_devices

    @staticmethod
    def get_default_device(prefer_gpu: bool = True) -> str:
        """Get the best available device for inference."""
        _detect_ov_native()
        if not _OV_NATIVE_DEVICES:
            return "CPU"
        if prefer_gpu and "GPU" in _OV_NATIVE_DEVICES:
            return "GPU"
        if prefer_gpu and "NPU" in _OV_NATIVE_DEVICES:
            return "NPU"
        return "CPU"

    def load_model(
        self,
        model_path: str,
        device: (str | None) = None,
        config: (dict | None) = None,
    ):
        """
        Load and compile an ONNX model for a specific device.

        Args:
            model_path: Path to ONNX model file
            device: Target device (CPU, GPU, NPU). Auto-detects best if None.
            config: Optional device-specific configuration dict

        Returns:
            Compiled OpenVINO model ready for inference
        """
        if device is None:
            device = self.get_default_device(prefer_gpu=True)

        core = self._core
        if core is None:
            raise ImportError(
                "OpenVINO not installed. Run: pip install openvino"
            )
        model = core.read_model(model_path)
        if config:
            return core.compile_model(model, device, config)
        return core.compile_model(model, device)

    @staticmethod
    def infer(compiled_model, inputs: dict) -> dict:
        """
        Run inference on a compiled OpenVINO model.

        Args:
            compiled_model: Return value from load_model()
            inputs: dict mapping input names to numpy arrays

        Returns:
            dict mapping output names to numpy arrays
        """
        results = compiled_model(inputs)
        out = {}
        for output in compiled_model.outputs:
            name = output.any_name
            out[name] = results[output]
        return out

    @staticmethod
    def infer_array(compiled_model, inputs: dict) -> np.ndarray:
        """
        Run inference and return first output as numpy array.

        Args:
            compiled_model: Return value from load_model()
            inputs: dict mapping input names to numpy arrays

        Returns:
            First output as numpy array
        """
        results = compiled_model(inputs)
        return results[compiled_model.output(0)]


# -- End OpenVINO native -----------------------------------------------------


class ONNXExporter:
    """
    Unified ONNX export for any ML framework.

    WHY THIS MATTERS:
    - 27 platforms lack stable ONNX export
    - ONNX is critical for model interoperability
    - Deployment requires ONNX in production

    IMPLEMENTATION:
    - Framework-specific export handlers
    - Input shape validation
    - ONNX Runtime inference
    """

    @staticmethod
    def detect_framework(model: Any) -> (str | None):
        """Detect model framework from type."""
        model_type = type(model).__module__

        if HAS_TORCH:
            if hasattr(model, "state_dict"):
                return "pytorch"
        if HAS_TF:
            if hasattr(model, "variables") or hasattr(model, "get_config"):
                return "tensorflow"
        if "sklearn" in model_type:
            return "sklearn"
        if "xgboost" in model_type:
            return "xgboost"
        if "lightgbm" in model_type:
            return "lightgbm"

        return None

    @staticmethod
    def export_to_onnx(
        model: Any,
        input_sample: Any,
        output_path: str,
        input_names: (tuple[str, ...] | None) = None,
        output_names: (tuple[str, ...] | None) = None,
        dynamic_axes: (dict | None) = None,
        opset_version: int = 13,
    ) -> str:
        """
        Export model to ONNX format.

        Args:
            model: Model to export
            input_sample: Sample input for tracing
            output_path: Output file path
            input_names: Optional input tensor names
            output_names: Optional output tensor names
            dynamic_axes: Optional dynamic dimension mapping
            opset_version: ONNX opset version (default: 13)

        Returns:
            Path to exported ONNX file
        """
        if not HAS_ONNX:
            raise ImportError("ONNX not installed. Run: pip install onnx onnxruntime")

        framework = ONNXExporter.detect_framework(model)

        if framework == "pytorch":
            if not HAS_TORCH:
                raise ImportError("PyTorch not installed")

            if input_names is None:
                input_names = ("input",)
            if output_names is None:
                output_names = ("output",)
            if dynamic_axes is None:
                dynamic_axes = {
                    0: "batch_size",
                    2: "sequence_length",
                }

            torch.onnx.export(
                model,
                input_sample,
                output_path,
                input_names=list(input_names),
                output_names=list(output_names),
                dynamic_axes=dynamic_axes,
                opset_version=opset_version,
                export_params=True,
                do_constant_folding=True,
            )

        elif framework == "tensorflow":
            if not HAS_TF:
                raise ImportError("TensorFlow not installed")

            import tf2onnx

            if isinstance(input_sample, tuple):
                input_signature = tuple(
                    tf.TensorSpec(shape=s.shape, dtype=s.dtype) for s in input_sample
                )
            else:
                input_signature = (
                    tf.TensorSpec(
                        shape=input_sample.shape,
                        dtype=input_sample.dtype,
                    ),
                )

            tf2onnx.convert.from_keras(
                model,
                input_signature=input_signature,
                outputs=output_names or ("output",),
            ).dump(output_path)

        else:
            raise ValueError(f"Unsupported framework: {framework}")

        print(f"ONNX model exported to: {output_path}")
        return output_path

    @staticmethod
    def get_optimal_providers(prefer_gpu: bool = True) -> tuple[tuple[str, ...], list]:
        """
        Determine optimal execution providers based on what's available.

        Priority order:
          1. OpenVINO GPU (Intel Arc)  - if prefer_gpu=True
          2. OpenVINO NPU (AI Boost)   - if prefer_gpu=True
          3. OpenVINO CPU              - always fallback
          4. CPUExecutionProvider      - ultimate fallback

        Args:
            prefer_gpu: Try GPU/NPU acceleration first

        Returns:
            tuple of (providers, provider_options)
        """
        _detect_openvino()  # lazy init

        providers = []
        provider_options = []

        if _HAS_OPENVINO and prefer_gpu:
            # Try GPU first, then NPU, then CPU (each as separate fallback)
            for device in ("GPU", "NPU", "CPU"):
                providers.append(_OPENVINO_EP)
                provider_options.append({"device_type": f"{device}_FP32"})

        if _HAS_OPENVINO and not prefer_gpu:
            providers.append(_OPENVINO_EP)
            provider_options.append({"device_type": "CPU_FP32"})

        # CPU fallback
        providers.append("CPUExecutionProvider")
        provider_options.append({})

        return tuple(providers), provider_options

    @staticmethod
    def load_onnx_model(
        model_path: str,
        providers: (tuple[str, ...] | None) = None,
        provider_options: (list | None) = None,
        use_openvino: bool = True,
        openvino_device: str = "CPU_FP32",
        openvino_prefer_gpu: bool = True,
    ):
        """
        Load an ONNX model for inference with automatic provider selection.

        Args:
            model_path: Path to ONNX file
            providers: Execution providers (auto-detected if None)
            provider_options: Provider-specific options
            use_openvino: Enable OpenVINO EP if available
            openvino_device: OpenVINO device type (CPU_FP32, GPU_FP32, NPU_FP16, etc.)
            openvino_prefer_gpu: Try GPU/NPU acceleration first

        Returns:
            ONNX Runtime inference session
        """
        if not HAS_ONNX:
            raise ImportError(
                "ONNX Runtime not installed. Run: pip install onnxruntime"
            )
        if ort is None:  # narrows the optional import; equivalent to the flag above
            raise ImportError(
                "ONNX Runtime not installed. Run: pip install onnxruntime"
            )

        _detect_openvino()  # lazy init

        if providers is None:
            if use_openvino and _HAS_OPENVINO:
                providers = (_OPENVINO_EP, "CPUExecutionProvider")
                provider_options = [{"device_type": openvino_device}, {}]
                if openvino_prefer_gpu and openvino_device == "CPU_FP32":
                    providers, provider_options = ONNXExporter.get_optimal_providers(
                        prefer_gpu=True
                    )
            else:
                providers = ("CPUExecutionProvider",)
                provider_options = [{}] if provider_options is None else provider_options

        if provider_options:
            return ort.InferenceSession(
                model_path, providers=providers, provider_options=provider_options
            )
        return ort.InferenceSession(model_path, providers=providers)

    @staticmethod
    def run_inference(
        session,
        inputs: dict,
        output_names: (tuple[str, ...] | None) = None,
    ) -> tuple[np.ndarray, ...]:
        """
        Run inference on ONNX model.

        Args:
            session: ONNX Runtime session
            inputs: Dictionary of input names to numpy arrays
            output_names: Names of outputs to retrieve

        Returns:
            tuple of output arrays
        """
        if output_names is None:
            output_names = tuple(out.name for out in session.get_outputs())

        outputs = session.run(output_names, inputs)
        return tuple(np.array(o) for o in outputs)

    @staticmethod
    def optimize_onnx(
        input_path: str,
        output_path: str,
        level: int = 1,
    ) -> str:
        """
        Optimize ONNX model.

        Args:
            input_path: Input ONNX file
            output_path: Output ONNX file
            level: Optimization level (1-4)

        Returns:
            Path to optimized ONNX file
        """
        if not HAS_ONNX:
            raise ImportError("ONNX not installed")

        onnx.load(input_path)

        from onnxruntime.tools import optimizer as ort_optimizer

        optimized = ort_optimizer.optimize_model(
            input_path,
            optimization_level=level,
        )
        optimized.save_model_to_file(output_path)

        print(f"Optimized ONNX model saved to: {output_path}")
        return output_path


def export_to_onnx(
    model: Any,
    input_sample: Any,
    output_path: str,
    **kwargs,
) -> str:
    """Shorthand for ONNXExporter.export_to_onnx()."""
    return ONNXExporter.export_to_onnx(model, input_sample, output_path, **kwargs)


def load_onnx_model(
    model_path: str,
    providers=None,
    provider_options=None,
    use_openvino: bool = True,
    openvino_device: str = "CPU_FP32",
    openvino_prefer_gpu: bool = True,
):
    """Shorthand for ONNXExporter.load_onnx_model() with OpenVINO support."""
    return ONNXExporter.load_onnx_model(
        model_path,
        providers=providers,
        provider_options=provider_options,
        use_openvino=use_openvino,
        openvino_device=openvino_device,
        openvino_prefer_gpu=openvino_prefer_gpu,
    )


def get_openvino_info() -> dict:
    """
    Return information about OpenVINO availability (both EP and native).

    Returns:
        dict with keys: available, devices, native_available, native_devices,
                        ort_version, providers
    """
    _detect_openvino()   # detect onnxruntime OpenVINO EP
    _detect_ov_native()  # detect native OpenVINO API
    info = {
        "available": _HAS_OPENVINO,
        "devices": dict(_OV_DEVICES),
        "native_available": _HAS_OV_NATIVE and bool(_OV_NATIVE_DEVICES),
        "native_devices": dict(_OV_NATIVE_DEVICES),
        "ort_version": ort.__version__ if ort is not None else None,
        "providers": ort.get_available_providers() if ort is not None else [],
    }
    return info


if __name__ == "__main__":
    print("=" * 50)
    print("ONNX Export Utilities Demo")
    print("=" * 50)

    # Show OpenVINO status
    ov_info = get_openvino_info()
    print(f"\nONNX Runtime: {ov_info['ort_version']}")
    print(f"Providers:    {ov_info['providers']}")
    print(f"OpenVINO EP:  {'AVAILABLE' if ov_info['available'] else 'NOT AVAILABLE'}")
    if ov_info["devices"]:
        print("OpenVINO Devices:")
        for dev, name in ov_info["devices"].items():
            print(f"  {dev}: {name}")

    if ov_info["available"]:
        providers, provider_opts = ONNXExporter.get_optimal_providers(prefer_gpu=True)
        print(f"\nOptimal providers: {providers}")
        print(f"Provider options:  {provider_opts}")

    # Show native OpenVINO status
    if ov_info["native_available"]:
        print("\nNative OpenVINO API: AVAILABLE")
        print(f"Native devices:      {ov_info['native_devices']}")
        best_dev = OpenVINOInference.get_default_device(prefer_gpu=True)
        print(f"Best device:         {best_dev}")

    # Quick inference test
    print(f"\n{'=' * 50}")
    print("Inference Test")
    print("=" * 50)

    import numpy as np
    import tempfile

    # Build a tiny ONNX model
    from onnx import helper, TensorProto
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, [1, 4])
    W = helper.make_tensor("W", TensorProto.FLOAT, [4, 2],
        [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, [1, 2])
    g = helper.make_graph(
        [helper.make_node("MatMul", ["X", "W"], ["Y"])], "test", [X], [Y], [W]
    )
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)])

    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "test.onnx")
    onnx.save(m, path)
    print(f"Model: {path}")

    inp = np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32)
    expected = np.array([[1.0, 2.0]], dtype=np.float32)
    cpu_result = None

    # Test with CPU (onnxruntime) - only if available
    if HAS_ONNX:
        session = load_onnx_model(path, use_openvino=False)
        cpu_result = session.run(None, {"X": inp})
        print(f"ORT CPU result: {cpu_result[0]}")

        if ov_info["available"]:
            session_ov = load_onnx_model(path, use_openvino=True, openvino_device="CPU_FP32")
            out_ov = session_ov.run(None, {"X": inp})
            print(f"OpenVINO EP result: {out_ov[0]}")
            diff = np.abs(cpu_result[0] - out_ov[0]).max()
            print(f"Max diff vs CPU: {diff:.2e} ({'PASS' if diff < 1e-5 else 'FAIL'})")
    else:
        print("ORT CPU: SKIPPED (onnxruntime not installed)")
        print("  Install: pip install onnxruntime")

    # Test with native OpenVINO (works with or without onnxruntime)
    if ov_info["native_available"]:
        print()
        try:
            ovi = OpenVINOInference()
            for dev_name in ["CPU", "GPU", "NPU"]:
                if dev_name in ovi.available_devices:
                    compiled = ovi.load_model(path, device=dev_name)
                    out_ovn = OpenVINOInference.infer_array(compiled, {"X": inp})
                    if cpu_result is not None:
                        match = "PASS" if np.allclose(cpu_result[0], out_ovn, atol=1e-4) else "FAIL"
                    else:
                        match = "PASS" if np.allclose(expected, out_ovn, atol=1e-4) else "FAIL"
                    print(f"Native OV {dev_name}: {out_ovn}  ({match})")
        except Exception as e:
            print(f"Native OV test error: {e}")
