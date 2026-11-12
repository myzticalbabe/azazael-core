# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: CONFIG (5 worms)
ConfigLinter, EnvValidator, DotfileSync, SecretRotator, FeatureFlagCtrl
"""

import json
import os
import shutil
import time
import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List

# Top-level, not per-call: secrets_manager touches no WORMS module, so there is
# no cycle to dodge, and an import inside the function is the shape that makes
# a missing dependency fail at USE time instead of at load time.
from CORE.secrets_manager import SecretsManager
from collections import defaultdict
import string
import sysconfig

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Worm Registry Import ---------------------------------------------
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("config:L20", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded, FreeThreadPool, ThreadSafeDict, ThreadSafeList
    _FREETHREAD = True
except ImportError as exc:
    note_failure("config:L29", exc)
    _FREETHREAD = False
    FreeThreadPool = None
    ThreadSafeDict = dict
    ThreadSafeList = list
    def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:
            note_failure("config:L38", exc)
            return False


@register_worm(category="config", priority=5, cooldown_seconds=45.0,
    description="Config linter — JSON/YAML/TOML schema validation and consistency checking")
class ConfigLinter:
    """Lints configuration files for schema violations and consistency."""

    VERSION = "1.0.0"

    def __init__(self):
        self._lint_results: dict[str, list[dict]] = defaultdict(list)

    def lint_json(self, filepath: str) -> list[dict]:
        """Validate JSON structure and common patterns."""
        issues = []
        try:
            content = Path(filepath).read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                for k in data:
                    if not k.isidentifier() and "-" in k:
                        issues.append({"key": k, "msg": "Key contains hyphen (not Python-compatible)", "severity": "info"})
        except json.JSONDecodeError as e:
            note_failure("config:L62", e)
            issues.append({"msg": f"Invalid JSON: {e}", "severity": "error"})
        self._lint_results[filepath] = issues
        return issues

    def lint_yaml(self, filepath: str) -> list[dict]:
        """Validate YAML structure."""
        issues = []
        try:
            content = Path(filepath).read_text(encoding="utf-8")
            if "\t" in content:
                issues.append({"msg": "YAML contains tab characters", "severity": "warning"})
        except Exception as e:
            note_failure("config:L74", e)
            issues.append({"msg": str(e), "severity": "error"})
        self._lint_results[filepath] = issues
        return issues



    def get_summary(self):
        return {"status":"ok","worm":"ConfigLinter","method":"lint_json"}
@register_worm(category="config", priority=5, cooldown_seconds=30.0,
    description="Environment validator — check env consistency across .env, system env, and docker-compose")
class EnvValidator:
    """Validates environment variable consistency across config sources."""

    VERSION = "1.0.0"

    def __init__(self):
        self._env_map: dict[str, dict[str, str]] = {}

    def scan_env_files(self, *paths: str) -> dict[str, Any]:
        """Scan .env files and compare variable sets."""
        envs: dict[str, dict[str, str]] = {}
        for p in paths:
            path = Path(p)
            if path.exists():
                vars_ = {}
                for line in path.read_text(encoding="utf-8").split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        vars_[k] = v
                envs[str(path)] = vars_
        all_keys = set()
        for vars_ in envs.values():
            all_keys.update(vars_.keys())
        missing = {str(p): all_keys - set(vars_.keys()) for p, vars_ in envs.items()}
        self._env_map = envs
        return {"files": len(envs), "total_keys": len(all_keys), "missing_per_file": {k: list(v) for k, v in missing.items() if v}}


@register_worm(category="config", priority=4, cooldown_seconds=60.0,
    description="Dotfile sync — sync .bashrc, .zshrc, .profile across machines")
class DotfileSync:
    """Syncs dotfiles across machines with conflict resolution."""

    VERSION = "1.0.0"

    def __init__(self):
        self._profiles: dict[str, str] = {}
        home = Path.home()
        for f in [".bashrc", ".zshrc", ".profile", ".gitconfig"]:
            p = home / f
            if p.exists():
                self._profiles[f] = p.read_text(encoding="utf-8", errors="replace")

    def get_checksums(self) -> dict[str, str]:
        """Get checksums of all tracked dotfiles."""
        return {f: hashlib.sha256(c.encode()).hexdigest()[:16] for f, c in self._profiles.items()}


@register_worm(category="config", priority=6, cooldown_seconds=120.0,
    description="Secret rotator — credential rotation automation with backup and validation")
class SecretRotator:
    """Automates credential rotation with backup and validation."""

    VERSION = "1.0.0"

    def __init__(self):
        self._rotation_log: list[dict] = []

    @staticmethod
    def _write_env_key(env: Path, name: str, value: str) -> None:
        """Set `name` in `env`, preserving every other line. Atomic replace."""
        lines = []
        if env.exists():
            lines = env.read_text(encoding="utf-8", errors="replace").splitlines()
        replaced = False
        out = []
        for line in lines:
            s = line.strip()
            if s and not s.startswith("#") and s.partition("=")[0].strip() == name:
                out.append("%s=%s" % (name, value))
                replaced = True
            else:
                out.append(line)
        if not replaced:
            out.append("%s=%s" % (name, value))
        env.parent.mkdir(parents=True, exist_ok=True)
        tmp = env.with_name(env.name + ".rot.tmp")
        tmp.write_text("\n".join(out).strip() + "\n", encoding="utf-8")
        tmp.replace(env)

    @staticmethod
    def _audit_path() -> Path:
        p = os.environ.get("AZAZAEL_SECRET_ROTATION_LOG")
        return Path(p) if p else Path.home() / ".azazael" / "secret_rotations.jsonl"

    def rotate_secret(self, name: str = "", generator: (Callable[[], str] | None) = None,
                      backup_path: str = "", create: bool = False,
                      env_path: (str | None) = None) -> dict[str, Any]:
        """Rotate a key in ~/.env, with backup and read-back verification.

        Contract (2026-10-30 rewrite). The previous body generated a password,
        recorded its checksum, and returned `success: True` while writing the
        value nowhere, ignoring `backup_path`, and never re-reading anything —
        a control whose success signal was independent of the thing it claimed
        to do. It had zero call sites, and wiring THAT would have produced
        scheduled "rotations" that changed no credential and logged success
        every time.

        So the success flag is now derived, not asserted: `success` is True only
        when the key is present on disk afterwards, its value equals the new
        secret, and its checksum differs from the previous one. Every failure
        path returns success=False with a `reason`; nothing is ever reported as
        rotated on the strength of having run.

        Rotation targets the real store (SecretsManager -> ~/.env) rather than
        an in-memory value, and refuses an unknown key unless `create=True`, so
        a typo cannot silently append a credential nobody reads.
        """
        generator = generator or self.generate_password
        env = Path(env_path) if env_path else (Path.home() / ".env")
        result: dict[str, Any] = {"name": name, "success": False, "verified": False,
                                  "changed": False, "backup": "", "reason": ""}
        if not name:
            result["reason"] = "no_name -- pass the env key to rotate (refusing to invent one)"
            return result

        def _read(p: Path) -> (str | None):
            if not p.exists():
                return None
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if s and not s.startswith("#") and s.partition("=")[0].strip() == name:
                    return s.partition("=")[2].strip().strip('"').strip("'")
            return None

        previous = _read(env)
        if previous is None and not create:
            result["reason"] = "unknown_secret -- %s has no %s; pass create=True to add it" % (env, name)
            return result

        new_secret = generator()
        if not new_secret:
            result["reason"] = "generator returned an empty value"
            return result
        prev_sum = hashlib.sha256(previous.encode()).hexdigest()[:12] if previous else ""
        new_sum = hashlib.sha256(new_secret.encode()).hexdigest()[:12]
        if previous is not None and new_sum == prev_sum:
            result["reason"] = "generator produced the existing value -- nothing would rotate"
            return result

        # backup BEFORE mutation; verified to exist and to contain the key.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = Path(backup_path) if backup_path else env.with_name(env.name + ".bak." + stamp)
        try:
            if env.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(env, backup)
                result["backup"] = str(backup)
                if _read(backup) != previous:
                    result["reason"] = "backup does not reproduce the previous value -- aborting before write"
                    return result
        except OSError as exc:
            result["reason"] = "backup failed (%s: %s) -- not writing" % (type(exc).__name__, exc)
            return result

        # Write to THIS path. `SecretsManager.add_to_env` targets its singleton's
        # `_env_path`, fixed at first construction, so calling it after reading
        # `env` could mutate a different file than the one about to be verified
        # -- which is exactly how a rotation reports success on a file it never
        # touched. The write is local and atomic for that reason.
        try:
            self._write_env_key(env, name, new_secret)
        except OSError as exc:
            result["reason"] = "write failed (%s: %s)" % (type(exc).__name__, exc)
            return result

        # Keep the shared cache honest when the default store was rotated. A
        # failure here is REPORTED rather than swallowed: the rotation stands,
        # but a stale cache is a fact the caller needs, and `pass` would have
        # made it invisible (and trips the silent-except ratchet).
        if env.resolve() == (Path.home() / ".env").resolve():
            try:
                SecretsManager().reload()
            except Exception as exc:  # noqa: BLE001 -- cache refresh is not the rotation
                result["cache_reload_error"] = f"{type(exc).__name__}: {exc}"

        # verification is a fresh read of the file, not a belief about the write
        on_disk = _read(env)
        result["changed"] = bool(on_disk and on_disk != previous)
        result["verified"] = bool(on_disk == new_secret)
        result["success"] = bool(result["verified"] and result["changed"])
        if not result["success"]:
            result["reason"] = ("read-back mismatch: on-disk value %s"
                                % ("absent" if on_disk is None else "differs from the new secret"))

        entry = {"name": name, "rotated_at": datetime.now(timezone.utc).isoformat(),
                 "previous_checksum": prev_sum, "checksum": new_sum,
                 "verified": result["verified"], "backup": result["backup"],
                 "env": str(env), "success": result["success"]}
        self._rotation_log.append(entry)
        try:
            audit = self._audit_path()
            audit.parent.mkdir(parents=True, exist_ok=True)
            with audit.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as exc:
            # The rotation stands; the trail is best-effort. Reported, not lost:
            # a rotation nobody can audit is a rotation nobody can review.
            result["audit_write_error"] = f"{type(exc).__name__}: {exc}"
        result["checksum_preview"] = new_sum
        return result

    def rotation_status(self, max_age_days: int = 90) -> dict[str, Any]:
        """Age of the last successful rotation per key, from the audit log.

        This is the part a schedule may run unattended: it reads the trail and
        reports, and never mutates a credential. A rotation that is never
        performed is indistinguishable from one that always succeeds unless
        something measures the age, which is what this exists for.
        """
        audit = self._audit_path()
        latest: dict[str, dict] = {}
        if audit.exists():
            for line in audit.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    # A corrupt audit line is exactly the record that would show
                    # a rotation happening; skipping it silently makes the
                    # staleness reading optimistic.
                    note_failure("config:rotation-audit-line", exc)
                    continue
                if not rec.get("success"):
                    continue
                name = rec.get("name")
                if name and (name not in latest or rec["rotated_at"] > latest[name]["rotated_at"]):
                    latest[name] = rec
        now = datetime.now(timezone.utc)
        stale = []
        for name, rec in latest.items():
            try:
                age = (now - datetime.fromisoformat(rec["rotated_at"])).days
            except (ValueError, TypeError):
                stale.append({"name": name, "age_days": None, "rotated_at": rec.get("rotated_at")})
                continue
            if age >= max_age_days:
                stale.append({"name": name, "age_days": age, "rotated_at": rec["rotated_at"]})
        return {"audit": str(audit), "keys_rotated": len(latest),
                "max_age_days": max_age_days, "stale": stale}

    def generate_password(self, length: int = 32) -> str:
        """Generate a strong random password."""
        import secrets
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        return ''.join(secrets.choice(alphabet) for _ in range(length))


@register_worm(category="config", priority=4, cooldown_seconds=30.0,
    description="Feature flag controller — runtime toggle management with percentage rollout")
class FeatureFlagCtrl:
    """Manages feature flags with percentage-based rollout."""

    VERSION = "1.0.0"

    def __init__(self):
        self._flags: dict[str, dict] = {}
        self._lock = threading.Lock()

    def set_flag(self, name: str, enabled: bool, rollout_pct: float = 100.0):
        with self._lock:
            self._flags[name] = {"enabled": enabled, "rollout_pct": rollout_pct, "updated": time.time()}

    def is_enabled(self, name: str, user_id: str = "") -> bool:
        """Check if a feature flag is enabled for a user."""
        with self._lock:
            flag = self._flags.get(name, {"enabled": False, "rollout_pct": 0})
        if not flag["enabled"]:
            return False
        if flag["rollout_pct"] >= 100:
            return True
        h = hashlib.sha256(f"{name}:{user_id}".encode()).digest()[0]
        return (h / 255.0 * 100) < flag["rollout_pct"]

    def get_summary(self):
        return {"status":"ok","worm":"FeatureFlagCtrl"}
