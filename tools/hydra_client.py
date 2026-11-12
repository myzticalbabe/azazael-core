"""HYDRA Client Library — Python SDK for the HYDRA REST API.

Usage:
    from hydra_client import HYDRAClient
    client = HYDRAClient("http://localhost:8000")
    status = client.status()
"""

import requests


class HYDRAClient:
    """Client for the HYDRA REST API."""

    def __init__(self, base_url="http://localhost:8000"):
        self.base_url = base_url.rstrip("/")

    def status(self):
        """Get cluster status."""
        r = requests.get(f"{self.base_url}/status", timeout=30)
        r.raise_for_status()
        return r.json()

    def discover(self):
        """Trigger ML group discovery."""
        r = requests.post(f"{self.base_url}/discover", timeout=30)
        r.raise_for_status()
        return r.json()

    def run(self):
        """Run sequential processing pipeline."""
        r = requests.post(f"{self.base_url}/run", timeout=30)
        r.raise_for_status()
        return r.json()

    def adapt(self):
        """Trigger adaptive learning."""
        r = requests.post(f"{self.base_url}/adapt", timeout=30)
        r.raise_for_status()
        return r.json()

    def list_nodes(self):
        """List all registered nodes."""
        r = requests.get(f"{self.base_url}/nodes", timeout=30)
        r.raise_for_status()
        return r.json()["nodes"]

    def health(self):
        """Get API health."""
        r = requests.get(f"{self.base_url}/", timeout=30)
        r.raise_for_status()
        return r.json()
