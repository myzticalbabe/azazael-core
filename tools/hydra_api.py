"""HYDRA REST API — FastAPI wrapper for the HYDRA cluster."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / "Desktop/azazael-project-e"))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="HYDRA REST API", version="1.0.0")


class NodeCreate(BaseModel):
    name: str
    group: str
    dependencies: list[str] = []
    priority: int = 0


def get_cluster():
    from HYDRA import HYDRACluster
    cluster = HYDRACluster()
    cluster.load_nodes()
    return cluster


@app.get("/")
def root():
    return {"service": "HYDRA REST API", "status": "running"}


@app.get("/status")
def status():
    cluster = get_cluster()
    return cluster.get_cluster_status()


@app.post("/discover")
def discover():
    cluster = get_cluster()
    groups = cluster.auto_discover_ml_groups()
    return {"groups": {g: len(m) for g, m in groups.items()}}


@app.post("/run")
def run():
    cluster = get_cluster()
    cluster.build_sequence()
    results = cluster.run_sequential()
    return {"stages_completed": len(results["stages"])}


@app.post("/adapt")
def adapt():
    cluster = get_cluster()
    adaptations = cluster.adaptive_learn()
    return {"adaptations_applied": len(adaptations)}


@app.get("/nodes")
def list_nodes():
    cluster = get_cluster()
    return {
        "nodes": [
            {"name": n.name, "group": n.group, "status": n.status, "priority": n.priority}
            for n in cluster.nodes.values()
        ]
    }


@app.post("/nodes")
def create_node(node: NodeCreate):
    from HYDRA import ClusterNode
    cluster = get_cluster()
    new_node = ClusterNode(name=node.name, group=node.group, dependencies=node.dependencies, priority=node.priority)
    cluster.register_node(new_node)
    return {"created": node.name}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
