---
title: Ray Integration API
version: 1.0.0
last_updated: 2026-02-10
status: REVIEW
authors: Azazael System
---

# ray_integration.py

**Location:** `ml_platforms/improvements/ray_integration.py`

**Purpose:** Distributed computing wrapper for Ray cluster integration

---

## What This File Does

`ray_integration.py` provides a unified interface for distributed computing using Ray. It enables parallel execution, distributed training, and hyperparameter tuning across multiple CPUs/GPUs.

```
┌─────────────────────────────────────────────────────────────────┐
│                RAY_INTEGRATION.PY                              │
│                                                                 │
│   Distributed Computing with Ray:                              │
│                                                                 │
│   ┌───────────────────────────────────────────────────────┐   │
│   │                    FEATURES                           │   │
│   │   • @ray_remote decorator for parallel execution      │   │
│   │   • parallel_map() for batch processing               │   │
│   │   • DistributedTrainer for ML training                │   │
│   │   • Automatic resource allocation (CPU/GPU)           │   │
│   │   • Cluster support                                   │   │
│   └───────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Classes

### RayIntegration

```python
class RayIntegration:
    """
    Universal Ray distributed computing integration.
    
    Why this matters:
    - 27 platforms lack Ray distributed support
    - Distributed training requires complex setup
    - Ray provides unified distributed Python API
    """
    
    def __init__(self, address: Optional[str] = None):
        """Initialize Ray integration."""
        
    def remote(
        self,
        num_cpus: int = 1,
        num_gpus: float = 0.0,
        memory: Optional[int] = None,
    ):
        """Decorator to make a function remote."""
        
    def parallel_map(
        self,
        func: Callable,
        items: List[Any],
        **ray_kwargs,
    ) -> List[Any]:
        """Apply function in parallel to list."""
        
    def distribute_data(
        self,
        data: List[Any],
        num_partitions: int,
    ) -> List[Any]:
        """Distribute data across cluster."""
        
    def create_actor(self, cls, *args, **kwargs):
        """Create a Ray actor."""
```

---

## Usage Examples

### Basic Remote Execution

```python
from improvements.ray_integration import RayIntegration

# Initialize
ray_int = RayIntegration()

# Define remote function
@ray_int.remote(num_cpus=4, num_gpus=1)
def train_model(data_chunk, lr=0.001):
    # Your training code
    model.fit(data_chunk)
    return model

# Execute remotely
result = train_model.remote(training_data, lr=0.01)
```

### Parallel Map

```python
from improvements.ray_integration import RayIntegration

ray_int = RayIntegration()

def process_item(item):
    # Process single item
    return item * 2

# Process list in parallel
items = [1, 2, 3, 4, 5]
results = ray_int.parallel_map(process_item, items)

print(results)  # [2, 4, 6, 8, 10]
```

### Parallel Training

```python
from improvements.ray_integration import RayIntegration

ray_int = RayIntegration()

def train_epoch(epoch, lr, batch_size):
    # Training for one epoch
    return {"epoch": epoch, "loss": loss}

# Run multiple training epochs in parallel
epochs = range(10)
results = ray_int.parallel_map(
    train_epoch,
    epochs,
    num_cpus=2  # Use 2 CPUs
)

# Collect results
for r in results:
    print(r)
```

### Distributed Data

```python
from improvements.ray_integration import RayIntegration

ray_int = RayIntegration()

# Large dataset
large_data = list(range(10000))

# Split across cluster
partitions = ray_int.distribute_data(large_data, num_partitions=4)

# Each partition can be processed separately
results = ray_int.parallel_map(process_data, partitions)
```

### With GPU

```python
@ray_int.remote(num_gpus=1)
def train_on_gpu(data):
    import torch
    # Model training with GPU
    return model

# Schedule on GPU
future = train_on_gpu.remote(training_data)
result = ray.get(future)
```

### Hyperparameter Tuning

```python
from improvements.ray_integration import RayIntegration

ray_int = RayIntegration()

def train_config(config):
    lr = config["lr"]
    batch_size = config["batch_size"]
    # Train model
    return accuracy

# Define search space
configs = [
    {"lr": 0.001, "batch_size": 32},
    {"lr": 0.01, "batch_size": 32},
    {"lr": 0.001, "batch_size": 64},
]

# Run trials in parallel
results = ray_int.parallel_map(train_config, configs)

# Find best
best = max(results, key=lambda r: r["accuracy"])
```

---

## Decorator Syntax

```python
@ray_int.remote(num_cpus=4, num_gpus=0.5, memory=8_000_000_000)
def my_function(data):
    """Remote function with resource specs."""
    return processed(data

# Call remotely
future = my_function.remote(data)

# Get result
result = ray.get(future)
```

---

## Resource Specifications

```python
# CPU only
@ray_int.remote(num_cpus=4)
def cpu_task():
    return compute()

# Single GPU
@ray_int.remote(num_gpus=1)
def gpu_task():
    return train()

# Fractional GPU
@ray_int.remote(num_gpus=0.5)
def half_gpu_task():
    return infer()

# Memory limit
@ray_int.remote(memory=8_000_000_000)  # 8GB
def large_mem_task():
    return load_big_data()
```

---

## Relationship to Other Files

```
ray_integration.py
    │
    ├── Imports: ray
    │       └── Ray distributed computing library
    │
    ├── Used by: lm_studio_integration.py
    │       └── 'distribute' function
    │
    └── Used by: verify_moe.py
            └── ray_expert uses this
```

---

## Installation Requirements

```bash
pip install ray
```

---

## Quick Reference

```python
from improvements.ray_integration import RayIntegration

# Initialize
ray_int = RayIntegration()  # Or RayIntegration(address="ray://...")

# Make function remote
@ray_int.remote(num_cpus=2, num_gpus=0.5)
def process(data):
    return transform(data)

# Execute
future = process.remote(large_data)
result = ray.get(future)

# Parallel map
results = ray_int.parallel_map(func, items)
```
