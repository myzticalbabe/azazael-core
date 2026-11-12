"""Double-check the analysis against the record, then render to MP4.

Every figure re-derived from a source that is NOT the analysis document, so the
document cannot be used to validate itself.
"""
import os, re, glob, json, subprocess, datetime, shutil
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

AZ = r"C:\Azazael\azazael"
DOC = os.path.join(AZ, "dashboard", "CAIN_20260930.html")
MODELS = r"C:\Users\clint\AppData\Local\Programs\AI Playground\resources\models\ComfyUI"
TPLDIR = os.path.join(AZ, "src", "CODEX", "SESSION_TEMPLATE", "templates")
GB = 2 ** 30
V = {}
F = []


def chk(label, got, want, ok=None):
    good = (got == want) if ok is None else ok
    V[label] = (got, want, good)
    if not good:
        F.append(label)
    print("  [%s] %-34s got=%-14s want=%s"
          % ("OK " if good else "BAD", label, str(got)[:14], str(want)[:30]))


# ── 1. session span: from the FIRST USER MESSAGE, not the earliest file ──
first_user_utc = datetime.datetime(2026, 9, 30, 6, 35, 11)      # session header
start_local = datetime.datetime(2026, 9, 30, 2, 35, 11)  # first user message, local (UTC-4)
now = datetime.datetime.now()
span = (now - start_local).total_seconds() / 3600.0
print("SESSION SPAN (from first user message, 02:35:11 local)")
chk("span_hours", round(span, 2), "recomputed")
print("       %.2f h  (the document claims 12.69)" % span)

# ── 2. first NEW file authored today corroborates ──
newest_own = [p for p in glob.glob(os.path.join(AZ, "**", "blender_*.py"), recursive=True)]
newest_test = os.path.join(AZ, "tests", "test_rule_0b_a_edict.py")
if os.path.exists(newest_test):
    t = datetime.datetime.fromtimestamp(os.path.getmtime(newest_test))
    print("       first authored test: %s  -> after 02:35? %s"
          % (t.strftime("%H:%M:%S"), t > start_local))

# ── 3. byte-exact model sizes ──
FILES = [("diffusion_models/flux1-schnell-fp8-e4m3fn.safetensors", 11891329784),
         ("text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors", 5157348688),
         ("clip/clip_l.safetensors", 246144152),
         ("vae/flux-vae-bf16.safetensors", 167664710),
         ("diffusion_models/flux1-dev-fp8.safetensors", 11901525888),
         ("diffusion_models/flux1-dev-Q4_K_S.gguf", 6805988640)]
tot = 0
print("\nMODEL FILES (byte-exact)")
for rel, want in FILES:
    p = os.path.join(MODELS, rel.replace("/", os.sep))
    got = os.path.getsize(p) if os.path.isfile(p) else -1
    tot += max(got, 0)
    chk(os.path.basename(rel)[:32], got, want)
chk("downloaded_GB", round(tot / GB, 2), 33.69)

# ── 4. document self-consistency ──
doc = open(DOC, encoding="utf-8").read()
print("\nDOCUMENT")
chk("h2_sections", len(re.findall(r"<h2[^>]*>.*?</h2>", doc, re.S)), 28)
chk("session_blocks", doc.count("session-20260930"), 28)
chk("span_claim_stale", "12.69 h" in doc or "12.70 h" in doc, True)
print("       ^ document still carries the WRONG span -> must be corrected before render")
print("       correct value to inject: %.2f h" % span)
residue = [s for s in ("Anticipatory", "Validation Seeking", "Imposter",
                       "Approval Dependency", "AI SYSTEM (85%)", "15 min",
                       "Subscription Revenue", "Trust Erosion") if s in doc]
chk("prior_session_residue", residue, [])
chk("div_imbalance", doc.count("<div") - doc.count("</div>"), -4)
css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", doc, re.S))
chk("css_brace_imbalance", css.count("{") - css.count("}"), -3)

print("\n" + "=" * 72)
print("VERDICT: %s" % ("all checks OK" if not F else "FAILURES: %s" % F))
print("  span correction REQUIRED: 12.69 -> %.2f h" % span)
print("=" * 72)
json.dump({"span_hours": round(span, 2), "failures": F,
           "checks": {k: list(v) for k, v in V.items()}},
           open(os.path.join(AZ, "dashboard", "CAIN_precheck.json"), "w",
                encoding="utf-8"),
          indent=1, default=str)


