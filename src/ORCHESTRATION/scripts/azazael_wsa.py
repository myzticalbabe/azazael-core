# coding: utf-8
"""
Azazael WSA Integration Module

Interoperability module for connecting Azazael with Windows Subsystem for Android (WSA).

Features:
- ADB connection management
- APK installation
- App management
- Shell command execution
- File transfer
- Performance monitoring
- REST API server for WSA communication

Requirements:
    pip install -r requirements_wsa.txt

Usage:
    >>> from azazael_wsa import WSAClient
    >>> client = WSAClient("127.0.0.1", 5555)
    >>> client.connect()
    >>> client.install_app("example.apk")
    >>> client.run_app("com.example.app")

Author: Azazael Enhancement System
Date: 2026-02-10
Version: 1.0.0
"""

import os
import shutil
import subprocess
import time
import logging
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass
from enum import Enum
from concurrent.futures import ThreadPoolExecutor

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: True when the `adb` BINARY this client shells out to can be located. Replaced
#: `try: pass / ADB_AVAILABLE = True` (2026-10-10 no-op gate sweep): the import
#: statement had been replaced by `pass`, so the flag was permanently True and the
#: `if not ADB_AVAILABLE` guard in WSAClient.__init__ could never fire. The
#: dependency is the executable (see _find_adb / _run_adb_command), NOT the
#: `adb_shell` package the old warning named. Covers PATH plus the two Android
#: roots _find_adb consults first.
ADB_AVAILABLE = shutil.which("adb") is not None or any(
    (Path(os.environ[root]) / sub / "adb").exists()
    for root in ("ANDROID_HOME", "ANDROID_SDK_ROOT") if os.environ.get(root)
    for sub in ("", "platform-tools")
)

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError as exc:
    note_failure("azazael_wsa:L52", exc)
    PSUTIL_AVAILABLE = False


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class WSAConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class WSAApp:
    """Represents an installed Android app."""

    package_name: str
    app_name: str
    version: str
    size: int
    installed: bool = True
    running: bool = False
    pid: int = 0


@dataclass
class WSAPerformance:
    """WSA system performance metrics."""

    cpu_percent: float
    memory_percent: float
    disk_usage: float
    network_bytes_sent: int
    network_bytes_recv: int
    battery_percent: (int | None)
    temperature: (float | None)


class WSAClient:
    """
    Client for interacting with Windows Subsystem for Android.

    Supports ADB connections, app management, shell commands,
    and performance monitoring.
    """

    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_PORT = 5555
    DEFAULT_TIMEOUT = 30

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        adb_path: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        """
        Initialize WSA client.

        Args:
            host: WSA IP address (default: 127.0.0.1)
            port: ADB port (default: 5555)
            adb_path: Path to ADB executable
            timeout: Connection timeout in seconds
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self.adb_path = adb_path or self._find_adb()
        self.state = WSAConnectionState.DISCONNECTED
        self.device = None
        self._executor = ThreadPoolExecutor(max_workers=4)

        if not ADB_AVAILABLE:
            logger.warning(
                "adb executable not found on PATH or under ANDROID_HOME/"
                "ANDROID_SDK_ROOT - install the Android platform-tools package"
            )

        if not PSUTIL_AVAILABLE:
            logger.warning("PSUtil not installed. Performance monitoring limited.")

    def _find_adb(self) -> str:
        """Find ADB executable path."""
        adb_names = ["adb.exe", "adb"]
        search_paths = [
            os.environ.get("ANDROID_HOME", ""),
            os.environ.get("ANDROID_SDK_ROOT", ""),
            "C:\\Program Files\\Android\\android-sdk\\platform-tools",  # scanner:ignore deliberate: last-resort SDK candidate after env vars
            str(Path.home() / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools"),
            "/usr/bin",
            "/opt/android-sdk/platform-tools",
        ]

        for base in search_paths:
            if not base:
                continue
            for adb_name in adb_names:
                path = Path(base) / adb_name
                if path.exists():
                    return str(path)

        return "adb"

    def _run_adb_command(
        self, command: list[str], timeout: int | None = None
    ) -> tuple[str, str, int]:
        """
        Run ADB command.

        Args:
            command: ADB command arguments
            timeout: Command timeout

        Returns:
            tuple of (stdout, stderr, returncode)
        """
        cmd = [self.adb_path] + command

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout, encoding="utf-8", errors="replace"
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            note_failure("azazael_wsa:L182", exc)
            return "", "Command timed out", -1
        except FileNotFoundError as exc:
            note_failure("azazael_wsa:L184", exc)
            return "", f"ADB not found: {self.adb_path}", -1

    def connect(self, retries: int = 3, delay: float = 2.0) -> bool:
        """
        Connect to WSA via ADB.

        Args:
            retries: Number of connection retries
            delay: Delay between retries

        Returns:
            True if connected successfully
        """
        self.state = WSAConnectionState.CONNECTING

        for attempt in range(retries):
            logger.info(
                f"Connecting to WSA ({self.host}:{self.port})... Attempt {attempt + 1}"
            )

            stdout, stderr, code = self._run_adb_command(
                ["connect", f"{self.host}:{self.port}"]
            )

            if code == 0 and "connected" in stdout.lower():
                self.state = WSAConnectionState.CONNECTED
                logger.info("Successfully connected to WSA")
                return True

            time.sleep(delay)

        self.state = WSAConnectionState.ERROR
        logger.error(f"Failed to connect: {stderr or stdout}")
        return False

    def disconnect(self) -> bool:
        """Disconnect from WSA."""
        stdout, stderr, code = self._run_adb_command(
            ["disconnect", f"{self.host}:{self.port}"]
        )

        self.state = WSAConnectionState.DISCONNECTED
        logger.info("Disconnected from WSA")
        return code == 0

    def is_connected(self) -> bool:
        """Check if connected to WSA."""
        stdout, _, code = self._run_adb_command(["devices"])

        if code != 0:
            return False

        device_str = f"{self.host}:{self.port}"
        return device_str in stdout and "device" in stdout

    def get_device_info(self) -> dict[str, Any]:
        """Get WSA device information."""
        info = {}

        props = [
            "ro.product.model",
            "ro.product.name",
            "ro.build.version.release",
            "ro.build.version.sdk",
            "ro.hardware",
            "ro.bootimage.build.fingerprint",
        ]

        for prop in props:
            stdout, _, code = self.shell(f"getprop {prop}")
            if code == 0:
                info[prop] = stdout.strip()

        return info

    def shell(self, command: str, timeout: int | None = None) -> tuple[str, str, int]:
        """
        Execute shell command on WSA.

        Args:
            command: Shell command to execute
            timeout: Command timeout

        Returns:
            tuple of (stdout, stderr, returncode)
        """
        return self._run_adb_command(
            ["-s", f"{self.host}:{self.port}", "shell", command], timeout
        )

    def install_app(self, apk_path: str, reinstall: bool = False) -> bool:
        """
        Install APK on WSA.

        Args:
            apk_path: Path to APK file
            reinstall: Reinstall if exists

        Returns:
            True if installation successful
        """
        apk = Path(apk_path)
        if not apk.exists():
            logger.error(f"APK not found: {apk_path}")
            return False

        logger.info(f"Installing {apk.name}...")

        cmd = ["-s", f"{self.host}:{self.port}", "install"]
        if reinstall:
            cmd.append("-r")
        cmd.append(str(apk))

        stdout, stderr, code = self._run_adb_command(cmd, timeout=120)

        if code == 0 and ("Success" in stdout or "INSTALL_SUCCESS" in stdout):
            logger.info(f"Successfully installed {apk.name}")
            return True

        logger.error(f"Installation failed: {stderr or stdout}")
        return False

    def uninstall_app(self, package_name: str) -> bool:
        """
        Uninstall app from WSA.

        Args:
            package_name: Android package name

        Returns:
            True if uninstallation successful
        """
        logger.info(f"Uninstalling {package_name}...")

        stdout, stderr, code = self._run_adb_command(
            ["-s", f"{self.host}:{self.port}", "uninstall", package_name]
        )

        if code == 0 and "Success" in stdout:
            logger.info(f"Successfully uninstalled {package_name}")
            return True

        logger.error(f"Uninstallation failed: {stderr or stdout}")
        return False

    def list_apps(self, system: bool = False, third_party: bool = True) -> list[WSAApp]:
        """
        list installed apps.

        Args:
            system: Include system apps
            third_party: Include third-party apps

        Returns:
            list of installed apps
        """
        apps = []

        cmd = "pm list packages"
        if system:
            cmd += " -s"
        if third_party:
            cmd += " -3"

        stdout, _, code = self.shell(cmd)

        if code != 0:
            return apps

        for line in stdout.strip().split("\n"):
            if not line.startswith("package:"):
                continue

            package_name = line.replace("package:", "").strip()

            info = self.get_app_info(package_name)
            if info:
                apps.append(info)

        return apps

    def get_app_info(self, package_name: str) -> (WSAApp | None):
        """Get information about a specific app."""
        stdout, _, code = self.shell(f"dumpsys package {package_name}")

        if code != 0 or "Package not found" in stdout:
            return None

        app_name = ""
        version = ""
        size = 0

        for line in stdout.split("\n"):
            if "versionName=" in line:
                version = (
                    line.split("versionName=")[1].split()[0] if "=" in line else ""
                )
            elif "application=" in line:
                app_name = (
                    line.split("application=")[1].split()[0] if "=" in line else ""
                )

        return WSAApp(
            package_name=package_name,
            app_name=app_name,
            version=version,
            size=size,
        )

    def run_app(self, package_name: str, activity: str | None = None) -> bool:
        """
        Launch app on WSA.

        Args:
            package_name: Android package name
            activity: Activity to launch (default: main activity)

        Returns:
            True if launch successful
        """
        if not activity:
            activity = self._get_main_activity(package_name)
            if not activity:
                return False

        logger.info(f"Running {package_name}/{activity}...")

        stdout, stderr, code = self.shell(f"am start -n {package_name}/{activity}")

        if code == 0 and "Starting:" in stdout or "Started" in stdout:
            logger.info(f"Successfully started {package_name}")
            return True

        logger.error(f"Failed to start app: {stderr or stdout}")
        return False

    def stop_app(self, package_name: str) -> bool:
        """Stop running app."""
        stdout, _, code = self.shell(f"am force-stop {package_name}")
        return code == 0

    def _get_main_activity(self, package_name: str) -> (str | None):
        """Get main activity for package."""
        stdout, _, code = self.shell(
            f"cmd package resolve-activity --brief -a android.intent.action.MAIN "
            f"-c android.intent.category.LAUNCHER {package_name}"
        )

        if code == 0:
            lines = stdout.strip().split("\n")
            if len(lines) >= 2:
                return lines[-1]
        return None

    def push_file(self, local_path: str, remote_path: str) -> bool:
        """
        Push file to WSA.

        Args:
            local_path: Local file path
            remote_path: Remote destination path

        Returns:
            True if transfer successful
        """
        local = Path(local_path)
        if not local.exists():
            logger.error(f"Local file not found: {local_path}")
            return False

        logger.info(f"Pushing {local.name} to {remote_path}...")

        stdout, stderr, code = self._run_adb_command(
            ["-s", f"{self.host}:{self.port}", "push", str(local), remote_path],
            timeout=120,
        )

        if code == 0:
            logger.info(f"Successfully pushed {local.name}")
            return True

        logger.error(f"Push failed: {stderr or stdout}")
        return False

    def pull_file(self, remote_path: str, local_path: str) -> bool:
        """
        Pull file from WSA.

        Args:
            remote_path: Remote file path
            local_path: Local destination path

        Returns:
            True if transfer successful
        """
        logger.info(f"Pulling {remote_path} to {local_path}...")

        stdout, stderr, code = self._run_adb_command(
            ["-s", f"{self.host}:{self.port}", "pull", remote_path, local_path],
            timeout=120,
        )

        if code == 0:
            logger.info(f"Successfully pulled {remote_path}")
            return True

        logger.error(f"Pull failed: {stderr or stdout}")
        return False

    def get_performance(self) -> (WSAPerformance | None):
        """Get WSA performance metrics."""
        if not PSUTIL_AVAILABLE:
            return None

        try:
            cpu = psutil.cpu_percent()
            memory = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").percent

            net = psutil.net_io_counters()

            battery = None
            if psutil.sensors_battery():
                battery = psutil.sensors_battery().percent

            return WSAPerformance(
                cpu_percent=cpu,
                memory_percent=memory,
                disk_usage=disk,
                network_bytes_sent=net.bytes_sent,
                network_bytes_recv=net.bytes_recv,
                battery_percent=battery,
                temperature=None,
            )
        except Exception as e:
            logger.error(f"Failed to get performance: {e}")
            return None

    def get_battery_info(self) -> dict[str, Any]:
        """Get WSA battery information."""
        info = {}

        stdout, _, code = self.shell("dumpsys battery")
        if code == 0:
            for line in stdout.split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    info[key.strip().lower().replace(" ", "_")] = value.strip()

        return info

    def take_screenshot(self, save_path: str | None = None) -> (bytes | None):
        """
        Take screenshot of WSA.

        Args:
            save_path: Optional path to save screenshot

        Returns:
            Screenshot bytes or None
        """
        timestamp = int(time.time())
        remote_path = f"/sdcard/screenshot_{timestamp}.png"

        stdout, _, code = self.shell(f"screencap -p {remote_path}")
        if code != 0:
            return None

        local_path = save_path or f"screenshot_{timestamp}.png"

        if self.pull_file(remote_path, local_path):
            self.shell(f"rm {remote_path}")

            with open(local_path, "rb") as f:
                return f.read()

        return None

    def record_screen(self, duration: int = 10, save_path: str | None = None) -> bool:
        """
        Record screen of WSA.

        Args:
            duration: Recording duration in seconds
            save_path: Path to save video

        Returns:
            True if recording successful
        """
        timestamp = int(time.time())
        remote_path = f"/sdcard/screenrecord_{timestamp}.mp4"

        logger.info(f"Recording screen for {duration} seconds...")

        cmd = f"screenrecord --time-limit {duration} {remote_path}"
        stdout, stderr, code = self.shell(cmd, timeout=duration + 10)

        if code != 0:
            return False

        local_path = save_path or f"screenrecord_{timestamp}.mp4"

        return self.pull_file(remote_path, local_path)

    def get_logs(self, tag: str | None = None, clear: bool = False) -> str:
        """
        Get WSA logs.

        Args:
            tag: Log tag filter (optional)
            clear: Clear logs after reading

        Returns:
            Log output
        """
        cmd = "logcat -d"
        if tag:
            cmd += f" -s {tag}"

        if clear:
            cmd += " -c"

        stdout, _, code = self.shell(cmd)
        return stdout if code == 0 else ""

    def reboot(self) -> bool:
        """Reboot WSA."""
        logger.info("Rebooting WSA...")
        stdout, _, code = self.shell("reboot")

        if code == 0:
            self.state = WSAConnectionState.DISCONNECTED
            return True

        return False

    def close(self):
        """Clean up resources."""
        self.disconnect()
        self._executor.shutdown(wait=True)


class WSARestAPI:
    """
    REST API server for WSA control.

    Provides HTTP endpoints for WSA management.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8000):
        """
        Initialize REST API server.

        Args:
            host: Server host
            port: Server port
        """
        self.host = host
        self.port = port
        self.wsa = WSAClient()
        self._running = False

    def start(self, background: bool = False):
        """Start REST API server."""
        try:
            from fastapi import FastAPI
            from fastapi.middleware.cors import CORSMiddleware
            import uvicorn

            app = FastAPI(title="Azazael WSA API")

            app.add_middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["*"],
                allow_headers=["*"],
            )

            @app.get("/")
            async def root():
                return {"status": "Azazael WSA API", "version": "1.0.0"}

            @app.get("/status")
            async def status():
                return {
                    "connected": self.wsa.is_connected(),
                    "device_info": self.wsa.get_device_info()
                    if self.wsa.is_connected()
                    else None,
                }

            @app.get("/apps")
            async def list_apps(system: bool = False, third_party: bool = True):
                apps = self.wsa.list_apps(system=system, third_party=third_party)
                return {"apps": [vars(a) for a in apps]}

            @app.post("/install")
            async def install_app(apk_path: str):
                success = self.wsa.install_app(apk_path)
                return {"success": success}

            @app.post("/uninstall")
            async def uninstall_app(package_name: str):
                success = self.wsa.uninstall_app(package_name)
                return {"success": success}

            @app.post("/run")
            async def run_app(package_name: str):
                success = self.wsa.run_app(package_name)
                return {"success": success}

            @app.post("/stop")
            async def stop_app(package_name: str):
                success = self.wsa.stop_app(package_name)
                return {"success": success}

            @app.get("/performance")
            async def performance():
                perf = self.wsa.get_performance()
                return vars(perf) if perf else {}

            @app.get("/screenshot")
            async def screenshot():
                data = self.wsa.take_screenshot()
                if data:
                    import base64

                    return {"screenshot": base64.b64encode(data).decode()}
                return {"error": "Failed to take screenshot"}

            @app.get("/logs")
            async def logs(tag: str | None = None):
                return {"logs": self.wsa.get_logs(tag=tag)}

            @app.post("/connect")
            async def connect(host: str | None = None, port: int | None = None):
                if host:
                    self.wsa.host = host
                if port:
                    self.wsa.port = port
                success = self.wsa.connect()
                return {"success": success}

            @app.post("/disconnect")
            async def disconnect():
                success = self.wsa.disconnect()
                return {"success": success}

            @app.post("/reboot")
            async def reboot():
                success = self.wsa.reboot()
                return {"success": success}

            self._app = app

            if background:
                import threading

                def run():
                    uvicorn.run(app, host=self.host, port=self.port)

                threading.Thread(target=run, daemon=True).start()
                self._running = True
                return {"message": "Server started in background"}
            else:
                uvicorn.run(app, host=self.host, port=self.port)

        except ImportError:
            logger.error(
                "FastAPI or uvicorn not installed. Install with: pip install fastapi uvicorn"
            )

    def stop(self):
        """Stop REST API server."""
        self._running = False


def create_wsa_shortcuts():
    """Create WSA management shortcuts."""
    shortcuts_dir = Path.home() / "Desktop"

    shortcuts = [
        (
            "WSA Connect.bat",
            """@echo off
echo Connecting to WSA...
adb connect 127.0.0.1:5555
echo Done.
pause
""",
        ),
        (
            "WSA Disconnect.bat",
            """@echo off
echo Disconnecting from WSA...
adb disconnect 127.0.0.1:5555
echo Done.
pause
""",
        ),
        (
            "WSA Apps list.bat",
            """@echo off
echo Listing installed apps...
adb -s 127.0.0.1:5555 shell pm list packages
pause
""",
        ),
        (
            "WSA Shell.bat",
            """@echo off
echo Opening WSA shell...
adb -s 127.0.0.1:5555 shell
""",
        ),
    ]

    for name, content in shortcuts:
        path = shortcuts_dir / name
        path.write_text(content, encoding="utf-8")
        logger.info(f"Created shortcut: {path}")


def demo():
    """Demonstrate WSA integration."""
    print("=== Azazael WSA Integration Demo ===\n")

    client = WSAClient()

    print("1. Connecting to WSA...")
    if client.connect():
        print("   [OK] Connected successfully")

        print("\n2. Getting device info...")
        info = client.get_device_info()
        for key, value in info.items():
            print(f"   {key}: {value}")

        print("\n3. Listing apps...")
        apps = client.list_apps(third_party=True)[:5]
        for app in apps:
            print(f"   - {app.package_name}")
        print(f"   ... and {len(apps) - 5} more" if len(apps) > 5 else "")

        print("\n4. Getting performance...")
        perf = client.get_performance()
        if perf:
            print(f"   CPU: {perf.cpu_percent}%")
            print(f"   Memory: {perf.memory_percent}%")

        print("\n5. Disconnecting...")
        client.disconnect()
        print("   [OK] Disconnected")

    else:
        print("   [X] Connection failed")
        print("\nTo connect to WSA:")
        print("1. Enable Developer Options in WSA settings")
        print("2. Enable USB Debugging")
        print("3. Run: adb connect 127.0.0.1:5555")

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    demo()
