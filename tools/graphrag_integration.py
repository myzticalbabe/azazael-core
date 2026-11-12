#!/usr/bin/env python3
# coding: utf-8
"""
graphrag_integration.py — GraphRAG Integration for Code Search (K3)

Implements E²GraphRAG-style bidirectional graph retrieval for code search.
Replaces chunk-based embedding with symbol-level graph retrieval.

Architecture (E²GraphRAG-style):
1. Build entity graph from code (functions, classes, modules as nodes; calls/imports/inheritance as edges)
2. Build bidirectional indexes: entity ↔ source chunks
3. Dual retrieval: local (entity-centric) + global (community-centric)
4. Adaptive retrieval: select local vs global based on query type

Based on:
- E²GraphRAG (arXiv 2505.24226, May 2025): 10× faster indexing, 100× retrieval speedup
- Microsoft GraphRAG (2024): Hierarchical communities + dynamic selection
- LightRAG (2024): Simple/fast GraphRAG removing clustering overhead
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional, Any
from dataclasses import dataclass, field, asdict

import torch
import torch.nn.functional as F

# Try to import transformers for embeddings
try:
    from transformers import AutoTokenizer, AutoModel
    HAS_TRANSFORMERS = True
except ImportError:
    HAS_TRANSFORMERS = False

# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class CodeEntity:
    """A symbol in the codebase (function, class, module)."""
    id: str
    name: str
    entity_type: str  # function, class, module, variable
    file_path: str
    module: str
    line_start: int
    line_end: int
    docstring: str = ""
    signature: str = ""
    embedding: List[float] = field(default_factory=list)
    
    # Graph connections
    calls: List[str] = field(default_factory=list)       # function calls
    imports: List[str] = field(default_factory=list)     # module imports
    inherits: List[str] = field(default_factory=list)    # class inheritance
    contains: List[str] = field(default_factory=list)    # module contains


@dataclass
class SourceChunk:
    """A chunk of source code linked to entities."""
    id: str
    file_path: str
    content: str
    line_start: int
    line_end: int
    linked_entities: List[str] = field(default_factory=list)  # entity IDs


@dataclass
class Community:
    """A community of related entities (GraphRAG concept)."""
    id: str
    name: str
    entity_ids: List[str]
    summary: str = ""
    level: int = 0  # hierarchy level
    parent: Optional[str] = None
    children: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Graph Construction
# ---------------------------------------------------------------------------

class CodeGraphBuilder:
    """Build entity graph and bidirectional indexes from source code."""
    
    def __init__(self, src_root: Path):
        self.src_root = Path(src_root)
        self.entities: Dict[str, CodeEntity] = {}
        self.chunks: Dict[str, SourceChunk] = {}
        self.communities: Dict[str, Community] = {}
        self.entity_to_chunks: Dict[str, Set[str]] = defaultdict(set)
        self.chunk_to_entities: Dict[str, Set[str]] = defaultdict(set)
    
    def build(self) -> Dict[str, Any]:
        """Build complete graph with entities, chunks, and indexes."""
        print(f"Building code graph from {self.src_root}")
        
        # 1. Parse all Python files
        py_files = list(self.src_root.rglob("*.py"))
        py_files = [f for f in py_files if not any(skip in str(f) for skip in 
                    {".git", "__pycache__", ".venv", "venv", "node_modules", "build", "dist"})]
        
        print(f"Found {len(py_files)} Python files")
        
        # 2. Parse each file
        for fpath in py_files:
            self._parse_file(fpath)
        
        # 3. Build bidirectional indexes
        self._build_indexes()
        
        # 4. Detect communities (simple: modules as communities)
        self._detect_communities()
        
        print(f"Built: {len(self.entities)} entities, {len(self.chunks)} chunks, {len(self.communities)} communities")
        
        return {
            "entities": {k: asdict(v) for k, v in self.entities.items()},
            "chunks": {k: asdict(v) for k, v in self.chunks.items()},
            "communities": {k: asdict(v) for k, v in self.communities.items()},
            "entity_to_chunks": {k: list(v) for k, v in self.entity_to_chunks.items()},
            "chunk_to_entities": {k: list(v) for k, v in self.chunk_to_entities.items()},
        }
    
    def _parse_file(self, fpath: Path):
        """Parse a single Python file into entities and chunks."""
        try:
            rel_path = fpath.relative_to(self.src_root)
            module_name = str(rel_path.with_suffix("")).replace(os.sep, ".")
            
            content = fpath.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()
            tree = ast.parse(content, filename=str(fpath))
            
            # Module entity
            module_id = f"module:{module_name}"
            self.entities[module_id] = CodeEntity(
                id=module_id,
                name=module_name,
                entity_type="module",
                file_path=str(rel_path),
                module=module_name,
                line_start=1,
                line_end=len(lines),
                docstring=ast.get_docstring(tree) or "",
            )
            
            # Create chunk for whole file (or split by class/function)
            chunk_id = f"chunk:{module_name}:file"
            self.chunks[chunk_id] = SourceChunk(
                id=chunk_id,
                file_path=str(rel_path),
                content=content[:8000],  # Limit chunk size
                line_start=1,
                line_end=len(lines),
                linked_entities=[module_id],
            )
            self.entity_to_chunks[module_id].add(chunk_id)
            self.chunk_to_entities[chunk_id].add(module_id)
            
            # Parse functions and classes
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    self._parse_function(node, module_name, module_id, lines)
                elif isinstance(node, ast.ClassDef):
                    self._parse_class(node, module_name, module_id, lines)
                    
        except Exception as e:
            print(f"Warning: Failed to parse {fpath}: {e}", file=sys.stderr)
    
    def _parse_function(self, node: ast.FunctionDef, module_name: str, 
                        module_id: str, lines: List[str]):
        func_name = node.name
        func_id = f"function:{module_name}.{func_name}"
        
        # Get signature
        args = [a.arg for a in node.args.args]
        signature = f"{func_name}({', '.join(args)})"
        
        entity = CodeEntity(
            id=func_id,
            name=func_name,
            entity_type="function",
            file_path=self.entities[module_id].file_path,
            module=module_name,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            docstring=ast.get_docstring(node) or "",
            signature=signature,
        )
        self.entities[func_id] = entity
        
        # Module contains this function
        self.entities[module_id].contains.append(func_id)
        
        # Create chunk
        chunk_content = "\n".join(lines[node.lineno-1:node.end_lineno])
        chunk_id = f"chunk:{module_name}:{func_name}"
        self.chunks[chunk_id] = SourceChunk(
            id=chunk_id,
            file_path=self.entities[module_id].file_path,
            content=chunk_content[:4000],
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            linked_entities=[func_id, module_id],
        )
        self.entity_to_chunks[func_id].add(chunk_id)
        self.entity_to_chunks[module_id].add(chunk_id)
        self.chunk_to_entities[chunk_id].add(func_id)
        self.chunk_to_entities[chunk_id].add(module_id)
        
        # Extract calls
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Name):
                    self.entities[func_id].calls.append(f"function:{module_name}.{child.func.id}")
                elif isinstance(child.func, ast.Attribute):
                    # Could be cross-module
                    pass
    
    def _parse_class(self, node: ast.ClassDef, module_name: str,
                     module_id: str, lines: List[str]):
        class_name = node.name
        class_id = f"class:{module_name}.{class_name}"
        
        entity = CodeEntity(
            id=class_id,
            name=class_name,
            entity_type="class",
            file_path=self.entities[module_id].file_path,
            module=module_name,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            docstring=ast.get_docstring(node) or "",
        )
        self.entities[class_id] = entity
        
        # Module contains this class
        self.entities[module_id].contains.append(class_id)
        
        # Inheritance
        for base in node.bases:
            if isinstance(base, ast.Name):
                entity.inherits.append(f"class:{module_name}.{base.id}")
            elif isinstance(base, ast.Attribute):
                entity.inherits.append(f"class:{base.attr}")
        
        # Create chunk
        chunk_content = "\n".join(lines[node.lineno-1:node.end_lineno])
        chunk_id = f"chunk:{module_name}:{class_name}"
        self.chunks[chunk_id] = SourceChunk(
            id=chunk_id,
            file_path=self.entities[module_id].file_path,
            content=chunk_content[:4000],
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            linked_entities=[class_id, module_id],
        )
        self.entity_to_chunks[class_id].add(chunk_id)
        self.entity_to_chunks[module_id].add(chunk_id)
        self.chunk_to_entities[chunk_id].add(class_id)
        self.chunk_to_entities[chunk_id].add(module_id)
    
    def _build_indexes(self):
        """Build bidirectional entity↔chunk indexes."""
        # Already built during parsing, just ensure consistency
        for entity_id, chunks in self.entity_to_chunks.items():
            for chunk_id in chunks:
                self.chunk_to_entities[chunk_id].add(entity_id)
        print(f"Indexes built: {len(self.entity_to_chunks)} entity->chunks, "
              f"{len(self.chunk_to_entities)} chunk->entities")
    
    def _detect_communities(self):
        """Detect communities using simple module-based clustering."""
        # Group entities by top-level module
        communities_by_root = defaultdict(list)
        
        for entity_id, entity in self.entities.items():
            if entity.entity_type == "module":
                root = entity.name.split(".")[0]
                communities_by_root[root].append(entity_id)
            else:
                root = entity.module.split(".")[0]
                communities_by_root[root].append(entity_id)
        
        for i, (root, entity_ids) in enumerate(communities_by_root.items()):
            comm_id = f"community:{root}"
            self.communities[comm_id] = Community(
                id=comm_id,
                name=root,
                entity_ids=entity_ids,
                summary=f"Module group: {root} ({len(entity_ids)} entities)",
                level=0,
            )


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

class EntityEmbedder:
    """Generate embeddings for code entities."""
    
    def __init__(self, model_name: str = "microsoft/codebert-base", device: str = "cpu"):
        self.device = torch.device(device)
        if HAS_TRANSFORMERS:
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
            self.model = AutoModel.from_pretrained(model_name).to(device)
            self.model.eval()
        else:
            self.tokenizer = None
            self.model = None
    
    @torch.no_grad()
    def embed_entity(self, entity: CodeEntity) -> List[float]:
        if not HAS_TRANSFORMERS or self.model is None:
            torch.manual_seed(hash(entity.id) % (2**32))
            return torch.randn(768).tolist()
        
        # Build text representation
        parts = []
        if entity.docstring:
            parts.append(entity.docstring[:200])
        if entity.signature:
            parts.append(entity.signature)
        elif entity.entity_type == "function":
            parts.append(f"def {entity.name}")
        elif entity.entity_type == "class":
            parts.append(f"class {entity.name}")
        else:
            parts.append(entity.name)
        
        text = " ".join(parts)[:512]
        inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=512, padding=True)
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        
        with torch.no_grad():
            outputs = self.model(**inputs)
            embedding = outputs.last_hidden_state[:, 0, :].squeeze(0).cpu().numpy()
        
        return embedding.tolist()
    
    def embed_all(self, entities: Dict[str, CodeEntity]) -> Dict[str, List[float]]:
        embeddings = {}
        for eid, entity in entities.items():
            embeddings[eid] = self.embed_entity(entity)
        return embeddings


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

class GraphRAGRetriever:
    """GraphRAG-style retrieval with local + global modes."""
    
    def __init__(self, graph_data: Dict[str, Any], embeddings: Dict[str, List[float]]):
        self.entities = {k: CodeEntity(**v) for k, v in graph_data["entities"].items()}
        self.chunks = {k: SourceChunk(**v) for k, v in graph_data["chunks"].items()}
        self.communities = {k: Community(**v) for k, v in graph_data["communities"].items()}
        self.entity_to_chunks = {k: set(v) for k, v in graph_data["entity_to_chunks"].items()}
        self.chunk_to_entities = {k: set(v) for k, v in graph_data["chunk_to_entities"].items()}
        self.embeddings = embeddings
        
        # Build entity adjacency for traversal
        self.adjacency = defaultdict(set)
        for eid, entity in self.entities.items():
            for call in entity.calls:
                self.adjacency[eid].add(call)
                self.adjacency[call].add(eid)
            for imp in entity.imports:
                self.adjacency[eid].add(imp)
                self.adjacency[imp].add(eid)
            for inh in entity.inherits:
                self.adjacency[eid].add(inh)
                self.adjacency[inh].add(eid)
    
    def _cosine_sim(self, a: List[float], b: List[float]) -> float:
        if not a or not b:
            return 0.0
        a_t = torch.tensor(a)
        b_t = torch.tensor(b)
        return F.cosine_similarity(a_t.unsqueeze(0), b_t.unsqueeze(0)).item()
    
    def local_search(self, query: str, query_embedding: List[float], 
                     top_k: int = 10, hops: int = 2) -> List[Dict[str, Any]]:
        """Local retrieval: entity-centric, traverses graph from seed entities."""
        # Find seed entities by embedding similarity
        scored = []
        for eid, emb in self.embeddings.items():
            if eid in self.entities:
                sim = self._cosine_sim(query_embedding, emb)
                scored.append((eid, sim))
        
        scored.sort(key=lambda x: -x[1])
        seeds = [eid for eid, _ in scored[:5]]
        
        # BFS from seeds
        visited = set()
        results = []
        from collections import deque
        queue = deque([(eid, 0) for eid in seeds])
        
        while queue and len(results) < top_k:
            eid, depth = queue.popleft()
            if eid in visited or depth > 2:
                continue
            visited.add(eid)
            
            if eid in self.entities:
                entity = self.entities[eid]
                # Get linked chunks
                chunk_ids = self.entity_to_chunks.get(eid, set())
                chunks = [self.chunks[cid] for cid in chunk_ids if cid in self.chunks]
                
                results.append({
                    "entity": asdict(entity),
                    "chunks": [asdict(c) for c in chunks],
                    "depth": depth,
                    "retrieval_mode": "local",
                })
                
                # Traverse neighbors
                for neighbor in self.adjacency.get(eid, []):
                    if neighbor not in visited:
                        queue.append((neighbor, depth + 1))
        
        return results[:top_k]
    
    def global_search(self, query: str, query_embedding: List[float],
                      top_k: int = 10) -> List[Dict[str, Any]]:
        """Global retrieval: community-centric, uses community summaries."""
        # Score communities by similarity to query
        comm_scores = []
        for cid, comm in self.communities.items():
            # Aggregate entity embeddings for community
            comm_embs = [self.embeddings[eid] for eid in comm.entity_ids if eid in self.embeddings]
            if not comm_embs:
                continue
            
            # Mean pooling
            mean_emb = [sum(dim)/len(comm_embs) for dim in zip(*comm_embs)]
            sim = self._cosine_sim(query_embedding, mean_emb)
            comm_scores.append((cid, sim, comm))
        
        comm_scores.sort(key=lambda x: -x[1])
        
        results = []
        for cid, sim, comm in comm_scores[:top_k]:
            community = self.communities[cid]
            # Get representative chunks
            entity_chunks = []
            for eid in comm.entity_ids[:10]:
                chunk_ids = self.entity_to_chunks.get(eid, set())
                entity_chunks.extend([self.chunks[cid] for cid in chunk_ids if cid in self.chunks])
            
            results.append({
                "community": asdict(community),
                "similarity": sim,
                "chunks": [asdict(c) for c in entity_chunks[:5]],
                "retrieval_mode": "global",
            })
        
        return results
    
    def adaptive_search(self, query: str, query_embedding: List[float],
                        top_k: int = 10) -> List[Dict[str, Any]]:
        """Adaptive: choose local vs global based on query."""
        # Simple heuristic: specific queries -> local, broad queries -> global
        query_lower = query.lower()
        
        specific_indicators = ["function", "class", "method", "variable", "bug", "error", "fix"]
        broad_indicators = ["architecture", "overview", "design", "pattern", "how does", "what is"]
        
        is_specific = any(ind in query_lower for ind in specific_indicators)
        is_broad = any(ind in query_lower for ind in broad_indicators)
        
        if is_specific and not is_broad:
            return self.local_search(query, query_embedding, top_k)
        elif is_broad and not is_specific:
            return self.global_search(query, query_embedding, top_k)
        else:
            # Hybrid: combine both
            local = self.local_search(query, query_embedding, top_k//2)
            global_r = self.global_search(query, query_embedding, top_k//2)
            return local + global_r


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="GraphRAG Integration for Code Search")
    parser.add_argument("--src", type=Path, default=Path("C:/Azazael/azazael/src"),
                        help="Source root directory")
    parser.add_argument("--out", type=Path, default=Path("tools/graphrag_index.json"),
                        help="Output index file")
    parser.add_argument("--embed-model", default="microsoft/codebert-base",
                        help="Embedding model")
    parser.add_argument("--device", default="cpu", help="Device (cpu/cuda)")
    parser.add_argument("--query", help="Test query")
    parser.add_argument("--top-k", type=int, default=10, help="Results to return")
    
    args = parser.parse_args()
    
    print(f"Building GraphRAG index from {args.src}")
    
    # Build graph
    builder = CodeGraphBuilder(args.src)
    graph_data = builder.build()
    
    # Generate embeddings
    embedder = EntityEmbedder(model_name=args.embed_model, device=args.device)
    print("Generating entity embeddings...")
    entities = {k: CodeEntity(**v) for k, v in graph_data["entities"].items()}
    embeddings = embedder.embed_all(entities)
    
    # Update graph data with embeddings
    for eid, emb in embeddings.items():
        if eid in graph_data["entities"]:
            graph_data["entities"][eid]["embedding"] = emb
    
    # Save index
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        json.dump(graph_data, f, indent=2)
    print(f"GraphRAG index saved to {args.out}")
    
    # Test query if provided
    if args.query:
        embedder = EntityEmbedder(model_name=args.embed_model, device=args.device)
        query_emb = embedder.embed_entity(CodeEntity(
            id="query", name="", entity_type="query", file_path="", module="",
            line_start=0, line_end=0, docstring=args.query
        ))
        
        retriever = GraphRAGRetriever(graph_data, embeddings)
        results = retriever.adaptive_search(args.query, query_emb, args.top_k)
        
        print(f"\nQuery: {args.query}")
        print(f"Results ({len(results)}):")
        for i, r in enumerate(results):
            mode = r.get("retrieval_mode", r.get("community", {}).get("retrieval_mode", "unknown"))
            if "entity" in r:
                ent = r["entity"]
                print(f"  {i+1}. [{mode}] {ent['name']} ({ent['entity_type']}) - {ent['file_path']}")
            elif "community" in r:
                comm = r["community"]
                print(f"  {i+1}. [{mode}] Community: {comm['name']} ({len(comm['entity_ids'])} entities)")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())