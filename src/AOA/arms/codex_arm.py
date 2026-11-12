"""CODEX Arm - Adapter for video generation and rendering system."""
import subprocess
import sys
from pathlib import Path
from typing import Dict, List
from core.arm_registry import BaseArm, ArmCapability, register_arm

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

_CODEX_STUDIO = Path(__file__).resolve().parents[3] / 'src' / 'CODEX' / 'CODEX_Studio.py'
#: Production interpreter (3.15t). The former '.venv-3.14t' target never existed
#: in this tree, and 3.14t was retired 2026-10-19, so there is no version
#: fallback: a missing production venv means run under this interpreter.
_PRODUCTION_PYTHON = Path("C:/Azazael/.venvs/py315t/Scripts/python.exe")

def _get_python():
    if _PRODUCTION_PYTHON.exists():
        return str(_PRODUCTION_PYTHON)
    return sys.executable

@register_arm(arm_id="codex",name="CODEX Video Engine",version="3.1.0",description="HTML-to-MP4 video generation with ffmpeg encoding, PIL frame rendering, and psychoanalysis video pipeline",category="quartet",priority=8,dependencies=["azazael","pams"])
class CodexArm(BaseArm):
    def initialize(self)->bool:
        self._python = _get_python()
        if not _CODEX_STUDIO.exists():
            print(f"CodexArm: CODEX_Studio.py not found at {_CODEX_STUDIO}")
            self._initialized = False
            return False
        self._initialized = True
        return True

    def execute(self,capability,payload=None)->dict:
        payload = payload or {}
        try:
            if capability == "render_video":
                html = payload.get("html_file", "")
                out = payload.get("output", "output.mp4")
                if not html:
                    return {"status": "error", "error": "No html_file specified"}
                if not Path(html).exists():
                    return {"status": "error", "error": f"HTML file not found: {html}"}
                fps = payload.get("fps", 30)
                duration = payload.get("duration", 60)
                resolution = payload.get("resolution", "1920x1080")
                cmd = [
                    self._python, str(_CODEX_STUDIO), "--cli",
                    "--input", html, "--output", out,
                    "--fps", str(fps), "--duration", str(duration),
                    "--resolution", resolution
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
                return {
                    "status": "ok" if result.returncode == 0 else "error",
                    "data": {
                        "output": out, "returncode": result.returncode,
                        "stdout": result.stdout[-500:], "stderr": result.stderr[-500:]
                    }
                }
            elif capability == "render_mermaid":
                out = payload.get("output", "aoa_structure.mp4")
                html = payload.get("html_file", str(Path.home() / 'Desktop' / 'AOA' / 'AOA_STRUCTURE.html'))
                return self.execute("render_video", {**payload, "html_file": html, "output": out})
            elif capability == "get_status":
                return {
                    "status": "ok",
                    "data": {
                        "initialized": self._initialized,
                        "codex_studio_path": str(_CODEX_STUDIO),
                        "codex_studio_exists": _CODEX_STUDIO.exists(),
                        "python": self._python if self._initialized else None
                    }
                }
            return {"status": "error", "error": "Unknown: " + capability}
        except subprocess.TimeoutExpired as exc:
            note_failure("codex_arm:L69", exc)
            return {"status": "error", "error": "Video rendering timed out (600s)"}
        except Exception as e:
            note_failure("codex_arm:L71", e)
            return {"status": "error", "error": str(e)}

    def get_capabilities(self)->List[ArmCapability]:
        return [
            ArmCapability("render_video", "Render HTML to MP4 video via CODEX_Studio.py", category="rendering"),
            ArmCapability("render_mermaid", "Render Mermaid graph HTML to scrolling MP4", category="rendering"),
            ArmCapability("get_status", "Get CODEX status and paths", category="monitoring")
        ]

    def shutdown(self)->bool:
        self._initialized = False
        return True
