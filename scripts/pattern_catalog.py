"""
pattern_catalog.py — Scalable exhaustive pattern enumeration for large codebases.

Uses core decomposition + sampling for 44K+ node graphs.
Applies rich metadata schemas (d_grade, gap, lead, audit_loop) to all entity types.

Architecture:
  graph → k-core decomposition → core sampling → ESU per core → pattern aggregation
  → metadata enrichment → unified catalog
"""

import json
import sys
import time
import random
from pathlib import Path
from collections import defaultdict, Counter, deque
from itertools import combinations, islice
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field

try:
    import networkx as nx
except ImportError:
    nx = None


# ── Rich Metadata Schemas (from our 4 schemas) ─────────────────────────────

@dataclass
class PatternMetadata:
    """Unified metadata for all pattern types."""
    # Core identity
    pattern_id: str
    pattern_type: str  # "motif", "ast_pattern", "import_pattern", "decorator_pattern", etc.
    k_size: int
    
    # D-grade edge metadata fields
    source_type: str = ""           # portal, html, pdf, json, api, manual, code
    retrieval_method: str = ""      # headed_chrome, headless, api_call, static_analysis
    confidence_score: float = 0.0
    universe: str = ""              # UJS, DOS, IRS, codebase, graphify, etc.
    verifier: str = ""              # esu, ast, import_scan, decorator_scan
    tier_evidence: List[str] = field(default_factory=list)
    status: str = "unsaved"
    priority: int = 0
    
    # Gap metadata fields
    records_action: str = ""
    unblocker: str = ""
    evidence_completeness: float = 0.0
    corroboration_count: int = 0
    
    # Lead metadata fields
    corroboration_layers: List[str] = field(default_factory=list)
    records_action_lead: str = ""
    verification_method: str = ""
    evidence_strength: float = 0.0
    
    # Audit loop fields
    last_heartbeat: str = ""
    bound_violated: str = ""
    superseded_by: str = ""
    recovery_action: str = ""
    
    # Structural
    nodes: List[str] = field(default_factory=list)
    edges: List[Tuple[str, str]] = field(default_factory=list)
    example_instances: List[List[str]] = field(default_factory=list)
    count: int = 0
    
    def compute_priority(self) -> int:
        """Unified priority computation across all metadata types."""
        score = 0
        score += self.confidence_score * 40
        score += self.evidence_strength * 30
        score += min(self.corroboration_count * 5, 20)
        score += (1.0 - self.evidence_completeness) * 10  # gaps need action
        return min(int(score), 5)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "pattern_type": self.pattern_type,
            "k_size": self.k_size,
            "source_type": self.source_type,
            "retrieval_method": self.retrieval_method,
            "confidence_score": self.confidence_score,
            "universe": self.universe,
            "verifier": self.verifier,
            "tier_evidence": self.tier_evidence,
            "status": self.status,
            "priority": self.priority,
            "records_action": self.records_action,
            "unblocker": self.unblocker,
            "evidence_completeness": self.evidence_completeness,
            "corroboration_count": self.corroboration_count,
            "corroboration_layers": self.corroboration_layers,
            "records_action_lead": self.records_action_lead,
            "verification_method": self.verification_method,
            "evidence_strength": self.evidence_strength,
            "last_heartbeat": self.last_heartbeat,
            "bound_violated": self.bound_violated,
            "superseded_by": self.superseded_by,
            "recovery_action": self.recovery_action,
            "nodes": self.nodes,
            "edges": self.edges,
            "example_instances": self.example_instances[:3],
            "count": self.count,
        }


# ── Graph Loading ──────────────────────────────────────────────────────────

def load_graphify_graph(graph_path: Path) -> nx.DiGraph:
    """Load graphify-out graph (nodes/edges or nodes/links format)."""
    data = json.loads(graph_path.read_text(encoding="utf-8", errors="replace"))
    
    G = nx.DiGraph()
    
    if 'nodes' in data and 'edges' in data:
        for n in data['nodes']:
            nid = n.get('id') or n.get('label')
            if nid:
                G.add_node(nid, **{k: v for k, v in n.items() if k not in ('id', 'label')})
        for e in data['edges']:
            s, t = e.get('source'), e.get('target')
            if s in G and t in G:
                G.add_edge(s, t, **{k: v for k, v in e.items() if k not in ('source', 'target')})
    elif 'nodes' in data and 'links' in data:
        for n in data['nodes']:
            nid = n.get('id') or n.get('label')
            if nid:
                G.add_node(nid, **{k: v for k, v in n.items() if k not in ('id', 'label')})
        for e in data['links']:
            s, t = e.get('source'), e.get('target')
            if s in G and t in G:
                G.add_edge(s, t, **{k: v for k, v in e.items() if k not in ('source', 'target')})
    
    return G


# ── Core Decomposition + Sampling ──────────────────────────────────────────

def k_core_decomposition(G: nx.DiGraph, k: int = 3) -> nx.DiGraph:
    """Extract k-core of undirected version."""
    G_undir = G.to_undirected()
    core = nx.k_core(G_undir, k=k)
    return G.subgraph(core.nodes()).copy()


def sample_subgraph(G: nx.DiGraph, max_nodes: int = 500, 
                    strategy: str = "largest_component") -> nx.DiGraph:
    """Sample subgraph for tractable ESU enumeration."""
    # Remove self-loops first
    self_loops = list(nx.selfloop_edges(G))
    if self_loops:
        G = G.copy()
        G.remove_edges_from(self_loops)
    
    if G.number_of_nodes() <= max_nodes:
        return G
    
    if strategy == "largest_component":
        # Get largest weakly connected component - best for motif enumeration
        components = list(nx.weakly_connected_components(G))
        largest = max(components, key=len)
        if len(largest) > max_nodes:
            largest = random.sample(list(largest), max_nodes)
        return G.subgraph(largest).copy()
    
    elif strategy == "core_plus_random":
        # Get high-degree core (lower k for more connectivity)
        G_undir = G.to_undirected()
        # Try k=2 first for more nodes, fallback to k=1
        for k in [2, 1]:
            try:
                core = nx.k_core(G_undir, k=k)
                if core.number_of_nodes() >= 100:
                    core_nodes = list(core.nodes())
                    break
            except:
                pass
        else:
            core_nodes = list(G_undir.nodes())
        
        if len(core_nodes) >= max_nodes:
            core_nodes = random.sample(core_nodes, max_nodes)
        
        # Add random neighbors
        remaining = max_nodes - len(core_nodes)
        if remaining > 0:
            neighbors = set()
            for n in core_nodes:
                neighbors.update(G.predecessors(n))
                neighbors.update(G.successors(n))
            neighbors -= set(core_nodes)
            if neighbors:
                add = random.sample(list(neighbors), min(remaining, len(neighbors)))
                core_nodes.extend(add)
        
        return G.subgraph(core_nodes[:max_nodes]).copy()
    
    elif strategy == "degree_weighted":
        # Weighted by degree
        degrees = dict(G.degree())
        nodes = list(G.nodes())
        weights = [degrees.get(n, 1) for n in nodes]
        total = sum(weights)
        probs = [w/total for w in weights]
        sampled = random.choices(nodes, weights=probs, k=max_nodes)
        return G.subgraph(sampled).copy()
    
    else:
        # Uniform random
        sampled = random.sample(list(G.nodes()), max_nodes)
        return G.subgraph(sampled).copy()


# ── ESU Algorithm (Optimized) ──────────────────────────────────────────────

class ScalableMotifEnumerator:
    """ESU with early termination and canonical forms."""
    
    def __init__(self, G: nx.DiGraph, max_k: int = 5, max_motifs: int = 100000):
        self.G = G
        self.max_k = max_k
        self.max_motifs = max_motifs
        self.motif_counts = Counter()
        self.motif_examples = defaultdict(list)
        self.node_list = list(G.nodes())
        self.node_to_idx = {n: i for i, n in enumerate(self.node_list)}
        self.total_enumerated = 0
    
    def enumerate(self) -> Dict[str, Any]:
        """Run ESU up to max_k."""
        if nx is None:
            raise RuntimeError("networkx required")
        
        results = {}
        for k in range(3, self.max_k + 1):
            if self.total_enumerated >= self.max_motifs:
                break
            start = time.time()
            count = self._esu_k(k)
            elapsed = time.time() - start
            results[k] = {"count": count, "time": elapsed}
            print(f"  k={k}: {count} motifs in {elapsed:.1f}s (total: {self.total_enumerated})")
        
        return {
            "by_k": results,
            "total": self.total_enumerated,
            "motif_counts": dict(self.motif_counts),
            "examples": {k: v[:3] for k, v in self.motif_examples.items()}
        }
    
    def _esu_k(self, k: int) -> int:
        """ESU for fixed k with early termination."""
        count = 0
        
        def extend(subgraph: Set[int], extension: Set[int], depth: int):
            nonlocal count
            if self.total_enumerated >= self.max_motifs:
                return
            if depth == k:
                canonical = self._canonicalize(subgraph)
                self.motif_counts[canonical] += 1
                self.total_enumerated += 1
                if len(self.motif_examples[canonical]) < 3:
                    self.motif_examples[canonical].append(
                        [self.node_list[i] for i in subgraph]
                    )
                count += 1
                return
            
            # Extension candidates
            candidates = set()
            for v in subgraph:
                nbrs = set(self.G.predecessors(self.node_list[v])) | \
                       set(self.G.successors(self.node_list[v]))
                for nbr in nbrs:
                    if nbr in self.node_to_idx:
                        idx = self.node_to_idx[nbr]
                        if idx not in subgraph and idx not in extension:
                            candidates.add(idx)
            
            # Ordering constraint to avoid duplicates
            min_sub = min(subgraph)
            candidates = [c for c in candidates if c > min_sub]
            
            for c in sorted(candidates):
                extend(subgraph | {c}, extension | {c}, depth + 1)
        
        # Start from each node
        for v in range(len(self.node_list)):
            if self.total_enumerated >= self.max_motifs:
                break
            extend({v}, {v}, 1)
        
        return count
    
    def _canonicalize(self, node_set: Set[int]) -> str:
        """Canonical form: sorted edges with relabeled nodes."""
        subgraph = self.G.subgraph([self.node_list[i] for i in node_set])
        mapping = {old: new for new, old in enumerate(subgraph.nodes())}
        edges = tuple(sorted((mapping[u], mapping[v]) for u, v in subgraph.edges()))
        return f"k{len(node_set)}:{edges}"


# ── AST Pattern Extraction ─────────────────────────────────────────────────

class ASTPatternExtractor:
    """Extract structural patterns from Python AST."""
    
    def __init__(self):
        self.patterns = Counter()
        self.examples = defaultdict(list)
    
    def extract_from_file(self, file_path: Path) -> List[str]:
        """Extract patterns from a single Python file."""
        try:
            import ast
            source = file_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
            
            patterns = []
            
            # Class definitions
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    bases = [b.id if isinstance(b, ast.Name) else "?" for b in node.bases]
                    decorators = [d.id if isinstance(d, ast.Name) else "?" for d in node.decorator_list]
                    patterns.append(f"class:{','.join(bases)}|decorators:{','.join(decorators)}")
                
                # Function definitions
                elif isinstance(node, ast.FunctionDef):
                    decorators = [d.id if isinstance(d, ast.Name) else "?" for d in node.decorator_list]
                    args = len(node.args.args)
                    patterns.append(f"func:args={args}|decorators:{','.join(decorators)}")
                
                # Decorators
                elif isinstance(node, ast.Call) and hasattr(node.func, 'id') and node.func.id in ('register_agent', 'register_worm'):
                    patterns.append(f"decorator:{node.func.id}")
                
                # Imports
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    if isinstance(node, ast.ImportFrom):
                        module = node.module or ""
                        names = [n.name for n in node.names]
                        patterns.append(f"import_from:{module}|{','.join(names)}")
                    else:
                        names = [n.name for n in node.names]
                        patterns.append(f"import:{','.join(names)}")
            
            return patterns
        except Exception:
            return []
    
    def extract_from_directory(self, root: Path, max_files: int = 500) -> Dict[str, int]:
        """Extract patterns from all Python files in directory."""
        py_files = list(root.rglob("*.py"))[:max_files]
        for f in py_files:
            patterns = self.extract_from_file(f)
            for p in patterns:
                self.patterns[p] += 1
                if len(self.examples[p]) < 3:
                    self.examples[p].append(str(f))
        return dict(self.patterns)


# ── Import/Dependency Patterns ─────────────────────────────────────────────

class ImportPatternExtractor:
    """Extract import dependency patterns."""
    
    def __init__(self):
        self.patterns = Counter()
        self.examples = defaultdict(list)
    
    def extract_from_file(self, file_path: Path) -> List[str]:
        """Extract import patterns from a single Python file."""
        try:
            import ast
            source = file_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
            
            patterns = []
            imports = []
            
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    names = sorted([n.name for n in node.names])
                    imports.append((module, tuple(names)))
                elif isinstance(node, ast.Import):
                    for n in node.names:
                        imports.append((n.name, (n.name,)))
            
            # Group by module
            by_module = defaultdict(set)
            for mod, names in imports:
                by_module[mod].update(names)
            
            for mod, names in by_module.items():
                patterns.append(f"imports:{mod}|{','.join(sorted(names))}")
            
            return patterns
        except Exception:
            return []


# ── Decorator/Registration Patterns ────────────────────────────────────────

class DecoratorPatternExtractor:
    """Extract decorator and registration patterns."""
    
    def __init__(self):
        self.patterns = Counter()
        self.examples = defaultdict(list)
    
    def extract_from_file(self, file_path: Path) -> List[str]:
        """Extract decorator patterns."""
        try:
            import ast
            source = file_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
            
            patterns = []
            
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    for dec in node.decorator_list:
                        if isinstance(dec, ast.Call):
                            if isinstance(dec.func, ast.Name):
                                patterns.append(f"decorator:{dec.func.id}")
                            elif isinstance(dec.func, ast.Attribute):
                                patterns.append(f"decorator:{dec.func.attr}")
                        elif isinstance(dec, ast.Name):
                            patterns.append(f"decorator:{dec.id}")
                        elif isinstance(dec, ast.Attribute):
                            patterns.append(f"decorator:{dec.attr}")
            
            return patterns
        except Exception:
            return []


# ── Unified Pattern Catalog ────────────────────────────────────────────────

class PatternCatalog:
    """Unified catalog for all pattern types with rich metadata."""
    
    def __init__(self, graph_path: Path, source_root: Path):
        self.graph_path = graph_path
        self.source_root = source_root
        self.G = None
        self.graph_patterns = {}
        self.ast_patterns = {}
        self.import_patterns = {}
        self.decorator_patterns = {}
        self.unified_catalog = {}
    
    def load_graph(self):
        """Load and prepare graph."""
        print(f"Loading graph from {self.graph_path}...")
        self.G = load_graphify_graph(self.graph_path)
        print(f"  Loaded: {self.G.number_of_nodes()} nodes, {self.G.number_of_edges()} edges")
    
    def extract_all(self, max_k: int = 4, max_motifs: int = 50000):
        """Extract all pattern types."""
        
        # 1. Graph motifs (on sampled subgraph)
        print("\n1. Sampling subgraph for motif enumeration...")
        subgraph = sample_subgraph(self.G, max_nodes=300, strategy="core_plus_random")
        print(f"  Subgraph: {subgraph.number_of_nodes()} nodes, {subgraph.number_of_edges()} edges")
        
        print("2. Running ESU motif enumeration...")
        enumerator = ScalableMotifEnumerator(subgraph, max_k=max_k, max_motifs=max_motifs)
        self.graph_patterns = enumerator.enumerate()
        
        # 2. AST patterns
        print("\n3. Extracting AST patterns...")
        ast_extractor = ASTPatternExtractor()
        self.ast_patterns = ast_extractor.extract_from_directory(self.source_root, max_files=500)
        print(f"  Found {len(self.ast_patterns)} AST pattern types")
        
        # 3. Import patterns
        print("4. Extracting import patterns...")
        imp_extractor = ImportPatternExtractor()
        for f in list(self.source_root.rglob("*.py"))[:500]:
            patterns = imp_extractor.extract_from_file(f)
            for p in patterns:
                imp_extractor.patterns[p] += 1
                if len(imp_extractor.examples[p]) < 3:
                    imp_extractor.examples[p].append(str(f))
        self.import_patterns = dict(imp_extractor.patterns)
        print(f"  Found {len(self.import_patterns)} import pattern types")
        
        # 4. Decorator patterns
        print("5. Extracting decorator patterns...")
        dec_extractor = DecoratorPatternExtractor()
        for f in list(self.source_root.rglob("*.py"))[:500]:
            patterns = dec_extractor.extract_from_file(f)
            for p in patterns:
                dec_extractor.patterns[p] += 1
                if len(dec_extractor.examples[p]) < 3:
                    dec_extractor.examples[p].append(str(f))
        self.decorator_patterns = dict(dec_extractor.patterns)
        print(f"  Found {len(self.decorator_patterns)} decorator pattern types")
    
    def enrich_with_metadata(self):
        """Apply rich metadata schemas to all patterns."""
        print("\n6. Enriching with metadata...")
        
        # Graph motifs
        for motif, count in self.graph_patterns.get("motif_counts", {}).items():
            meta = PatternMetadata(
                pattern_id=motif,
                pattern_type="graph_motif",
                k_size=int(motif.split(':')[0][1:]),
                source_type="code",
                retrieval_method="static_analysis",
                confidence_score=0.9,
                universe="codebase",
                verifier="esu",
                tier_evidence=["graphify-out"],
                count=count,
                nodes=[],
                edges=[],
            )
            meta.priority = meta.compute_priority()
            self.unified_catalog[f"graph:{motif}"] = meta.to_dict()
        
        # AST patterns
        for pattern, count in self.ast_patterns.items():
            meta = PatternMetadata(
                pattern_id=pattern,
                pattern_type="ast_pattern",
                k_size=1,
                source_type="code",
                retrieval_method="static_analysis",
                confidence_score=0.85,
                universe="codebase",
                verifier="ast",
                tier_evidence=["ast_parse"],
                count=count,
            )
            meta.priority = meta.compute_priority()
            self.unified_catalog[f"ast:{pattern}"] = meta.to_dict()
        
        # Import patterns
        for pattern, count in self.import_patterns.items():
            meta = PatternMetadata(
                pattern_id=pattern,
                pattern_type="import_pattern",
                k_size=1,
                source_type="code",
                retrieval_method="static_analysis",
                confidence_score=0.95,
                universe="codebase",
                verifier="ast_import",
                tier_evidence=["ast_parse"],
                count=count,
            )
            meta.priority = meta.compute_priority()
            self.unified_catalog[f"import:{pattern}"] = meta.to_dict()
        
        # Decorator patterns
        for pattern, count in self.decorator_patterns.items():
            meta = PatternMetadata(
                pattern_id=pattern,
                pattern_type="decorator_pattern",
                k_size=1,
                source_type="code",
                retrieval_method="static_analysis",
                confidence_score=0.9,
                universe="codebase",
                verifier="ast_decorator",
                tier_evidence=["ast_parse"],
                count=count,
            )
            meta.priority = meta.compute_priority()
            self.unified_catalog[f"decorator:{pattern}"] = meta.to_dict()
        
        print(f"  Enriched {len(self.unified_catalog)} total patterns")
    
    def save_catalog(self, output: Path):
        """Save unified catalog."""
        result = {
            "metadata": {
                "graph_nodes": self.G.number_of_nodes() if self.G else 0,
                "graph_edges": self.G.number_of_edges() if self.G else 0,
                "pattern_types": {
                    "graph_motifs": len(self.graph_patterns.get("motif_counts", {})),
                    "ast_patterns": len(self.ast_patterns),
                    "import_patterns": len(self.import_patterns),
                    "decorator_patterns": len(self.decorator_patterns),
                },
                "total_patterns": len(self.unified_catalog),
            },
            "graph_motifs": self.graph_patterns,
            "ast_patterns": self.ast_patterns,
            "import_patterns": self.import_patterns,
            "decorator_patterns": self.decorator_patterns,
            "unified_catalog": self.unified_catalog,
        }
        output.write_text(json.dumps(result, indent=2, default=str))
        print(f"\nCatalog saved to {output}")


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Build unified pattern catalog')
    parser.add_argument('--graph', required=True, help='Path to graphify-out graph.json')
    parser.add_argument('--source', required=True, help='Source root directory')
    parser.add_argument('--output', required=True, help='Output JSON path')
    parser.add_argument('--max-k', type=int, default=4, help='Max k for motifs')
    parser.add_argument('--max-motifs', type=int, default=50000, help='Max motifs to enumerate')
    args = parser.parse_args()
    
    catalog = PatternCatalog(Path(args.graph), Path(args.source))
    catalog.load_graph()
    catalog.extract_all(max_k=args.max_k, max_motifs=args.max_motifs)
    catalog.enrich_with_metadata()
    catalog.save_catalog(Path(args.output))


if __name__ == '__main__':
    main()