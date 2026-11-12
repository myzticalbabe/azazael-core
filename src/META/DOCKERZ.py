# coding: utf-8
"""
DOCKERZ - Container Management for Azazael Ecosystem

Docker/Podman container orchestration for isolated worm execution,
dependency management, and portable deployment.

Features:
- Container lifecycle management
- Image building and registry
- Volume mounting
- Network configuration
- Container monitoring
- Compose support

Usage:
    >>> from METAWORMS.DOCKERZ import DOCKERZ
    >>> dz = DOCKERZ()
    >>> dz.list_containers()
    >>> dz.run_container("azazael/wormz:latest")
    >>> dz.build_image("C:/Users/clint/Desktop")
"""

import subprocess
import json
import time
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk, scrolledtext
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class ContainerInfo:
    """Container information."""

    id: str
    name: str
    image: str
    status: str
    ports: dict[str, str]
    created: str


@dataclass
class ImageInfo:
    """Container image information."""

    id: str
    repository: str
    tag: str
    size: str
    created: str


class DOCKERZConfig:
    """Configuration for DOCKERZ."""

    OUTPUT_PATH = "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/META/output/dockerz"
    DOCKERFILE_TEMPLATE = "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/META/output/dockerz/Dockerfile"
    COMPOSE_TEMPLATE = (
        "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/META/output/dockerz/docker-compose.yml"
    )

    DEFAULT_REGISTRY = "azazael"
    DEFAULT_TAG = "latest"

    WORKSPACE_PATH = "C:/Users/clint/Desktop"


class DOCKERZ:
    """Docker Container Management."""

    VERSION = "1.0.0"

    def __init__(self):
        self.config = DOCKERZConfig()
        self.output_path = Path(self.config.OUTPUT_PATH)
        self._ensure_directories()

    def _ensure_directories(self):
        """Create output directories."""
        self.output_path.mkdir(parents=True, exist_ok=True)

    def _run_docker(self, args: list[str], timeout: int = 300) -> tuple:
        """Run docker command."""
        cmd = ["docker"] + args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout,
            )
            return result.returncode == 0, result.stdout, result.stderr
        except subprocess.TimeoutExpired as exc:
            note_failure("DOCKERZ:L97", exc)
            return False, "", "Command timed out"
        except FileNotFoundError as exc:
            note_failure("DOCKERZ:L99", exc)
            return False, "", "Docker not found"
        except Exception as e:
            note_failure("DOCKERZ:L101", e)
            return False, "", str(e)

    def is_available(self) -> bool:
        """Check if Docker is available."""
        success, _, _ = self._run_docker(["--version"])
        return success

    def list_containers(self, all: bool = True) -> list[ContainerInfo]:
        """list containers."""
        args = [
            "ps",
            "-a",
            "--format",
            "{{.ID}}\\|{{.Names}}\\|{{.Image}}\\|{{.Status}}\\|{{.Ports}}\\|{{.CreatedAt}}",
        ]
        if all:
            args.insert(2, "-a")

        success, output, _ = self._run_docker(args)
        containers = []

        for line in output.strip().split("\n"):
            if line:
                parts = line.split("|")
                if len(parts) >= 6:
                    ports = {}
                    if len(parts) > 4 and parts[4]:
                        for p in parts[4].split(", "):
                            if "->" in p:
                                host, container = p.split("->")
                                ports[host.strip()] = container.strip()

                    containers.append(
                        ContainerInfo(
                            id=parts[0],
                            name=parts[1],
                            image=parts[2],
                            status=parts[3],
                            ports=ports,
                            created=parts[5],
                        )
                    )

        return containers

    def list_images(self) -> list[ImageInfo]:
        """list container images."""
        success, output, _ = self._run_docker(
            [
                "images",
                "--format",
                "{{.ID}}\\|{{.Repository}}\\|{{.Tag}}\\|{{.Size}}\\|{{.CreatedSince}}",
            ]
        )

        images = []
        for line in output.strip().split("\n"):
            if line:
                parts = line.split("|")
                if len(parts) >= 5:
                    images.append(
                        ImageInfo(
                            id=parts[0],
                            repository=parts[1],
                            tag=parts[2],
                            size=parts[3],
                            created=parts[4],
                        )
                    )

        return images

    def run_container(
        self,
        image: str,
        name: (str | None) = None,
        ports: (dict[str, str] | None) = None,
        volumes: (dict[str, str] | None) = None,
        environment: (dict[str, str] | None) = None,
        detach: bool = True,
        remove: bool = False,
    ) -> dict[str, Any]:
        """Run a container."""
        args = ["run"]

        if detach:
            args.append("-d")
        if remove:
            args.append("--rm")

        if name:
            args.extend(["--name", name])

        if ports:
            for host_port, container_port in ports.items():
                args.extend(["-p", f"{host_port}:{container_port}"])

        if volumes:
            for host_path, container_path in volumes.items():
                args.extend(["-v", f"{host_path}:{container_path}"])

        if environment:
            for key, value in environment.items():
                args.extend(["-e", f"{key}={value}"])

        args.append(image)

        success, output, error = self._run_docker(args)

        return {
            "success": success,
            "container_id": output.strip() if success else "",
            "error": error,
        }

    def stop_container(self, container_id: str, timeout: int = 10) -> bool:
        """Stop a container."""
        success, _, error = self._run_docker(["stop", "-", str(timeout), container_id])
        return success

    def start_container(self, container_id: str) -> bool:
        """Start a container."""
        success, _, error = self._run_docker(["start", container_id])
        return success

    def remove_container(self, container_id: str, force: bool = False) -> bool:
        """Remove a container."""
        args = ["rm"]
        if force:
            args.append("-f")
        args.append(container_id)

        success, _, error = self._run_docker(args)
        return success

    def get_container_logs(self, container_id: str, tail: int = 100) -> str:
        """Get container logs."""
        success, output, error = self._run_docker(
            ["logs", "--tail", str(tail), container_id]
        )
        return output if success else error

    def get_container_stats(
        self, container_id: str, stream: bool = False
    ) -> dict[str, Any]:
        """Get container statistics."""
        args = [
            "stats",
            "--no-stream",
            "--format",
            "{{.Container}}\\|{{.CPUPerc}}\\|{{.MemUsage}}\\|{{.NetIO}}\\|{{.BlockIO}}",
        ]
        success, output, error = self._run_docker(args + [container_id])

        if not success or not output:
            return {}

        parts = output.strip().split("|")
        if len(parts) >= 5:
            return {
                "container": parts[0],
                "cpu_percent": parts[1],
                "memory_usage": parts[2],
                "net_io": parts[3],
                "block_io": parts[4],
            }

        return {}

    def build_image(
        self,
        path: str,
        tag: str,
        dockerfile: (str | None) = None,
        build_args: (dict[str, str] | None) = None,
    ) -> dict[str, Any]:
        """Build a container image."""
        args = ["build"]

        if dockerfile:
            args.extend(["-f", dockerfile])
        else:
            dockerfile_path = Path(path) / "Dockerfile"
            if dockerfile_path.exists():
                args.extend(["-f", str(dockerfile_path)])

        args.extend(["-", tag, path])

        if build_args:
            for key, value in build_args.items():
                args.extend(["--build-arg", f"{key}={value}"])

        success, output, error = self._run_docker(args, timeout=600)

        return {
            "success": success,
            "output": output,
            "error": error,
        }

    def pull_image(self, image: str) -> dict[str, Any]:
        """Pull a container image."""
        success, output, error = self._run_docker(["pull", image], timeout=600)
        return {
            "success": success,
            "output": output,
            "error": error,
        }

    def push_image(self, image: str) -> dict[str, Any]:
        """Push a container image."""
        success, output, error = self._run_docker(["push", image], timeout=600)
        return {
            "success": success,
            "output": output,
            "error": error,
        }

    def remove_image(self, image_id: str, force: bool = False) -> bool:
        """Remove a container image."""
        args = ["rmi"]
        if force:
            args.append("-f")
        args.append(image_id)

        success, _, error = self._run_docker(args)
        return success

    def generate_dockerfile(self, base_image: str = "python:3.15-slim") -> str:
        """Generate a Dockerfile for Azazael."""
        dockerfile_content = f"""# Azazael Ecosystem Dockerfile
# Generated by DOCKERZ

FROM {base_image}

WORKDIR /app

COPY requirements-3.15t.txt .
RUN pip install --no-cache-dir -r requirements-3.15t.txt

COPY . .

ENV PYTHONPATH=/app
ENV Azazael_ROOT=/app

CMD ["python", "Azazael/Azazael_INTEGRATION.py"]
"""

        path = Path(self.config.DOCKERFILE_TEMPLATE)
        with open(path, "w", encoding="utf-8") as f:
            f.write(dockerfile_content)

        return str(path)

    def generate_compose(
        self,
        services: (dict[str, dict] | None) = None,
    ) -> str:
        """Generate docker-compose.yml."""
        default_services = {
            "azazael": {
                "build": ".",
                "ports": ["5000:5000"],
                "volumes": ["./:/app"],
                "environment": ["PYTHONPATH=/app"],
                "restart": "unless-stopped",
            },
            "metaworms": {
                "image": "azazael/metaworms:latest",
                "depends_on": ["azazael"],
                "restart": "unless-stopped",
            },
        }

        services = services or default_services

        compose = "version: '3.8'\n\nservices:\n"
        for name, config in services.items():
            compose += f"  {name}:\n"
            if "build" in config:
                compose += f"    build: {config['build']}\n"
            if "image" in config:
                compose += f"    image: {config['image']}\n"
            if "ports" in config:
                for port in config["ports"]:
                    compose += f'    ports:\n      - "{port}"\n'
            if "volumes" in config:
                for volume in config["volumes"]:
                    compose += f'    volumes:\n      - "{volume}"\n'
            if "environment" in config:
                for env in config["environment"]:
                    compose += f"    environment:\n      - {env}\n"
            if "depends_on" in config:
                compose += "    depends_on:\n"
                for dep in config["depends_on"]:
                    compose += f"      - {dep}\n"
            if "restart" in config:
                compose += f"    restart: {config['restart']}\n"
            compose += "\n"

        path = Path(self.config.COMPOSE_TEMPLATE)
        with open(path, "w", encoding="utf-8") as f:
            f.write(compose)

        return str(path)

    def run_compose(self, action: str = "up", detach: bool = True) -> dict[str, Any]:
        """Run docker-compose commands."""
        args = ["docker-compose"]

        if action == "up":
            args.append("up")
            if detach:
                args.append("-d")
        elif action == "down":
            args.append("down")
        elif action == "build":
            args.append("build")
        elif action == "logs":
            args.append("logs")
            args.append("--tail=100")
        elif action == "ps":
            args.append("ps")

        success, output, error = self._run_docker(args[1:], timeout=300)
        return {
            "success": success,
            "output": output,
            "error": error,
        }

    def exec_command(self, container_id: str, command: str) -> dict[str, Any]:
        """Execute command in container."""
        success, output, error = self._run_docker(
            ["exec", container_id, "python", "-c", command]
        )
        return {
            "success": success,
            "output": output,
            "error": error,
        }

    def get_status(self) -> dict[str, Any]:
        """Get Docker status."""
        docker_ok = self.is_available()
        containers = self.list_containers() if docker_ok else []
        images = self.list_images() if docker_ok else []

        return {
            "version": self.VERSION,
            "docker_available": docker_ok,
            "containers_running": len([c for c in containers if c.status == "running"]),
            "containers_total": len(containers),
            "images_total": len(images),
        }

    def run_popup(self):
        """Run with GUI popup."""
        root = tk.Tk()
        root.title("DOCKERZ - Container Management")
        root.geometry("800x600")

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        containers_tab = ttk.Frame(notebook)
        notebook.add(containers_tab, text="Containers")

        images_tab = ttk.Frame(notebook)
        notebook.add(images_tab, text="Images")

        build_tab = ttk.Frame(notebook)
        notebook.add(build_tab, text="Build")

        status_label = ttk.Label(
            containers_tab, text="Initializing...", font=("Arial", 12)
        )
        status_label.pack(pady=10)

        containers_text = scrolledtext.ScrolledText(containers_tab, width=90, height=20)
        containers_text.pack(fill="both", expand=True, padx=10, pady=10)

        def refresh():
            status = self.get_status()
            status_label.config(
                text=f"Docker: {'Available' if status['docker_available'] else 'Not Found'} | "
                f"Containers: {status['containers_running']}/{status['containers_total']} | "
                f"Images: {status['images_total']}"
            )

            containers = self.list_containers()
            containers_text.delete("1.0", "end")
            containers_text.insert(
                "end",
                json.dumps(
                    [
                        {
                            "name": c.name,
                            "image": c.image,
                            "status": c.status,
                        }
                        for c in containers
                    ],
                    indent=2,
                ),
            )

        ttk.Button(containers_tab, text="Refresh", command=refresh).pack(pady=5)
        ttk.Button(
            containers_tab,
            text="Stop All",
            command=lambda: [self.run_compose("down"), refresh()],
        ).pack(pady=5)
        ttk.Button(
            build_tab, text="Generate Dockerfile", command=self.generate_dockerfile
        ).pack(pady=5)
        ttk.Button(
            build_tab, text="Generate Compose", command=self.generate_compose
        ).pack(pady=5)

        root.mainloop()


def main():
    """CLI entry point."""

    parser = argparse.ArgumentParser(description="DOCKERZ - Container Management")
    parser.add_argument("--version", action="version", version="1.0.0")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--containers", action="store_true", help="list containers")
    parser.add_argument("--images", action="store_true", help="list images")
    parser.add_argument("--build", help="Build image from path")
    parser.add_argument("--run", help="Run container from image")
    parser.add_argument("--stop", help="Stop container")
    parser.add_argument("--logs", help="Get container logs")
    parser.add_argument(
        "--compose", choices=["up", "down", "build", "logs"], help="Compose action"
    )
    parser.add_argument(
        "--generate-dockerfile", action="store_true", help="Generate Dockerfile"
    )
    parser.add_argument(
        "--generate-compose", action="store_true", help="Generate compose file"
    )
    parser.add_argument("--popup", action="store_true", help="Launch GUI")

    args = parser.parse_args()
    dz = DOCKERZ()

    if args.status:
        print(json.dumps(dz.get_status(), indent=2))
    elif args.containers:
        print(
            json.dumps(
                [
                    {
                        "name": c.name,
                        "image": c.image,
                        "status": c.status,
                    }
                    for c in dz.list_containers()
                ],
                indent=2,
            )
        )
    elif args.images:
        print(
            json.dumps(
                [
                    {
                        "id": i.id,
                        "repository": i.repository,
                        "tag": i.tag,
                    }
                    for i in dz.list_images()
                ],
                indent=2,
            )
        )
    elif args.build:
        result = dz.build_image(args.build, f"azazael/custom:{int(time.time())}")
        print(f"Build: {result['success']}")
    elif args.run:
        result = dz.run_container(args.run)
        print(
            f"Run: {result['success']}, Container: {result.get('container_id', 'N/A')}"
        )
    elif args.stop:
        print(f"Stop: {dz.stop_container(args.stop)}")
    elif args.logs:
        print(dz.get_container_logs(args.logs))
    elif args.compose:
        result = dz.run_compose(args.compose)
        print(f"Compose {args.compose}: {result['success']}")
    elif args.generate_dockerfile:
        print(f"Dockerfile: {dz.generate_dockerfile()}")
    elif args.generate_compose:
        print(f"Compose: {dz.generate_compose()}")
    elif args.popup:
        dz.run_popup()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()


