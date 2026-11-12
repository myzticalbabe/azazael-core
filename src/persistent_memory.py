# coding: utf-8
"""
Persistent Memory - SQL Database Connection Module

Connects to the persistent memory SQLite database (graph.db) and provides
structured access to code structure graphs, execution flows, communities,
and risk analysis data.

Features:
- Typed access to all tables: nodes, edges, flows, communities, risk_index
- Full-text search across code entities
- Flow and community traversal queries
- Risk analysis helpers
- Read/write operations with connection management

Usage:
    >>> from persistent_memory import PersistentMemoryDB
    >>> db = PersistentMemoryDB("workspace/graph.db")
    >>> funcs = db.get_functions(limit=10)
    >>> flow = db.get_flow_by_name("Scan")
    >>> risks = db.get_high_risk_nodes(threshold=0.7)
"""

import sqlite3
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass
from contextlib import contextmanager
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class Node:
    """A code structure node (Function, Class, File, etc.)."""

    id: int
    kind: str
    name: str
    qualified_name: str
    file_path: str
    line_start: int
    line_end: int
    language: str
    parent_name: str
    params: str
    return_type: str
    modifiers: str
    is_test: bool = False
    file_hash: str = ""
    extra: str = "{}"
    updated_at: (float | None) = None
    signature: str = ""
    community_id: int = 0


@dataclass
class Edge:
    """A relationship between two nodes (CALLS, CONTAINS, IMPORTS_FROM, etc.)."""

    id: int
    kind: str
    source_qualified: str
    target_qualified: str
    file_path: str
    line: int = 0
    extra: str = "{}"
    confidence: float = 1.0
    confidence_tier: str = "EXTRACTED"
    updated_at: (float | None) = None


@dataclass
class Flow:
    """An execution or logical flow through nodes."""

    id: int
    name: str
    entry_point_id: (int | None)
    depth: int
    node_count: int
    file_count: int
    criticality: float = 0.0
    path_json: (str | None) = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class FlowMembership:
    """Membership of a node in a flow."""

    flow_id: int
    node_id: int
    position: int


@dataclass
class Community:
    """A community (cluster) of related nodes."""

    id: int
    name: str
    level: int = 0
    parent_id: int = 0
    cohesion: float = 0.0
    size: int = 0
    dominant_language: str = ""
    description: str = ""
    created_at: str = ""


@dataclass
class CommunitySummary:
    """Human-readable summary of a community."""

    community_id: int
    name: str
    purpose: str = ""
    key_symbols: str = "[]"
    risk: str = "unknown"
    size: int = 0
    dominant_language: str = ""


@dataclass
class FlowSnapshot:
    """Snapshot of a flow's state and critical path."""

    flow_id: int
    name: str
    entry_point: str
    critical_path: str = "[]"
    criticality: float = 0.0
    node_count: int = 0
    file_count: int = 0


@dataclass
class RiskEntry:
    """Security risk metrics for a node."""

    node_id: int
    qualified_name: str
    risk_score: float = 0.0
    caller_count: int = 0
    test_coverage: str = "unknown"
    security_relevant: bool = False
    last_computed: str = ""


# ---------------------------------------------------------------------------
# Database Connection Manager
# ---------------------------------------------------------------------------


class PersistentMemoryDB:
    """Persistent memory database connection manager for graph.db.

    Provides structured access to the code structure graph database
    including nodes, edges, flows, communities, and risk data.
    """

    VERSION = "1.0.0"

    def __init__(self, db_path: str = "workspace/graph.db"):
        """Initialize the persistent memory database connection.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")

    @contextmanager
    def _conn(self):
        """Get a database connection with proper cleanup."""
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_metadata(self) -> dict[str, str]:
        """Get all metadata key-value pairs."""
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM metadata").fetchall()
            return {row["key"]: row["value"] for row in rows}

    def get_schema_version(self) -> int:
        """Get the database schema version."""
        meta = self.get_metadata()
        return int(meta.get("schema_version", 0))

    def get_last_updated(self) -> (str | None):
        """Get when the database was last updated."""
        meta = self.get_metadata()
        return meta.get("last_updated")

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    def get_total_nodes(self) -> int:
        """Get the total number of nodes."""
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    def get_node_kinds(self) -> dict[str, int]:
        """Get a count of nodes by kind."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT kind, COUNT(*) as cnt FROM nodes GROUP BY kind ORDER BY cnt DESC"
            ).fetchall()
            return {row["kind"]: row["cnt"] for row in rows}

    def get_nodes(
        self,
        kind: (str | None) = None,
        language: (str | None) = None,
        file_path: (str | None) = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Node]:
        """Get nodes with optional filters.

        Args:
            kind: Filter by node kind (e.g., 'Function', 'Class', 'File').
            language: Filter by programming language.
            file_path: Filter by file path (SQL LIKE pattern).
            limit: Maximum number of results.
            offset: Pagination offset.
        """
        query = "SELECT * FROM nodes WHERE 1=1"
        params: list[Any] = []

        if kind:
            query += " AND kind = ?"
            params.append(kind)
        if language:
            query += " AND language = ?"
            params.append(language)
        if file_path:
            query += " AND file_path LIKE ?"
            params.append(file_path)

        query += " ORDER BY id LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_node(row) for row in rows]

    def get_functions(self, limit: int = 100, offset: int = 0) -> list[Node]:
        """Get function nodes."""
        return self.get_nodes(kind="Function", limit=limit, offset=offset)

    def get_classes(self, limit: int = 100, offset: int = 0) -> list[Node]:
        """Get class nodes."""
        return self.get_nodes(kind="Class", limit=limit, offset=offset)

    def get_files(self, limit: int = 100, offset: int = 0) -> list[Node]:
        """Get file nodes."""
        return self.get_nodes(kind="File", limit=limit, offset=offset)

    def get_node_by_qualified_name(self, qualified_name: str) -> (Node | None):
        """Get a node by its qualified name."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM nodes WHERE qualified_name = ?", (qualified_name,)
            ).fetchone()
            return self._row_to_node(row) if row else None

    def get_node_by_id(self, node_id: int) -> (Node | None):
        """Get a node by its ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM nodes WHERE id = ?", (node_id,)
            ).fetchone()
            return self._row_to_node(row) if row else None

    def search_nodes(
        self, query_str: str, limit: int = 50
    ) -> list[tuple[Node, float]]:
        """Full-text search across node names and qualified names.

        Args:
            query_str: The search text.
            limit: Maximum results.

        Returns:
            list of (Node, relevance_score) tuples.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT n.*, rank
                   FROM nodes_fts fts
                   JOIN nodes n ON n.id = fts.rowid
                   WHERE nodes_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (query_str, limit),
            ).fetchall()
            return [(self._row_to_node(row), row["rank"]) for row in rows]

    def _row_to_node(self, row: sqlite3.Row) -> Node:
        """Convert a database row to a Node dataclass."""
        return Node(
            id=row["id"],
            kind=row["kind"],
            name=row["name"],
            qualified_name=row["qualified_name"],
            file_path=row["file_path"],
            line_start=row["line_start"],
            line_end=row["line_end"],
            language=row["language"],
            parent_name=row["parent_name"],
            params=row["params"],
            return_type=row["return_type"],
            modifiers=row["modifiers"],
            is_test=bool(row["is_test"]),
            file_hash=row["file_hash"],
            extra=row["extra"],
            updated_at=row["updated_at"],
            signature=row["signature"],
            community_id=row["community_id"],
        )

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------

    def get_total_edges(self) -> int:
        """Get the total number of edges."""
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    def get_edge_kinds(self) -> dict[str, int]:
        """Get a count of edges by kind."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT kind, COUNT(*) as cnt FROM edges GROUP BY kind ORDER BY cnt DESC"
            ).fetchall()
            return {row["kind"]: row["cnt"] for row in rows}

    def get_edges(
        self,
        kind: (str | None) = None,
        source: (str | None) = None,
        target: (str | None) = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Edge]:
        """Get edges with optional filters."""
        query = "SELECT * FROM edges WHERE 1=1"
        params: list[Any] = []

        if kind:
            query += " AND kind = ?"
            params.append(kind)
        if source:
            query += " AND source_qualified = ?"
            params.append(source)
        if target:
            query += " AND target_qualified = ?"
            params.append(target)

        query += " ORDER BY id LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_edge(row) for row in rows]

    def get_callers(self, qualified_name: str, limit: int = 50) -> list[Edge]:
        """Get all edges that CALL a given node."""
        return self.get_edges(
            kind="CALLS", target=qualified_name, limit=limit
        )

    def get_callees(self, qualified_name: str, limit: int = 50) -> list[Edge]:
        """Get all edges where a given node CALLS another."""
        return self.get_edges(
            kind="CALLS", source=qualified_name, limit=limit
        )

    def get_imports_of(self, qualified_name: str, limit: int = 50) -> list[Edge]:
        """Get all imports that a node makes (where it is the source/importer)."""
        return self.get_edges(
            kind="IMPORTS_FROM", source=qualified_name, limit=limit
        )

    def get_imported_by(self, qualified_name: str, limit: int = 50) -> list[Edge]:
        """Get all nodes that import a given node (where it is the target/importee)."""
        return self.get_edges(
            kind="IMPORTS_FROM", target=qualified_name, limit=limit
        )

    def _row_to_edge(self, row: sqlite3.Row) -> Edge:
        """Convert a database row to an Edge dataclass."""
        return Edge(
            id=row["id"],
            kind=row["kind"],
            source_qualified=row["source_qualified"],
            target_qualified=row["target_qualified"],
            file_path=row["file_path"],
            line=row["line"],
            extra=row["extra"],
            confidence=row["confidence"],
            confidence_tier=row["confidence_tier"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Flows
    # ------------------------------------------------------------------

    def get_flows(self, limit: int = 50) -> list[Flow]:
        """Get all flows ordered by criticality."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM flows ORDER BY criticality DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_flow(row) for row in rows]

    def get_flow_by_name(self, name: str) -> (Flow | None):
        """Get a flow by name.

        Note: Flow names are not unique in the schema. Returns the first match.
        Use get_flows() and filter if you need all flows with a given name.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM flows WHERE name = ?", (name,)
            ).fetchone()
            return self._row_to_flow(row) if row else None

    def get_flow_nodes(self, flow_id: int) -> list[int]:
        """Get the ordered node IDs in a flow.

        Returns:
            list of node IDs ordered by position in the flow.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT node_id FROM flow_memberships WHERE flow_id = ? ORDER BY position",
                (flow_id,),
            ).fetchall()
            return [row["node_id"] for row in rows]

    def get_flows_for_node(self, node_id: int) -> list[Flow]:
        """Get all flows that include a given node."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT f.* FROM flows f
                   JOIN flow_memberships fm ON f.id = fm.flow_id
                   WHERE fm.node_id = ?
                   ORDER BY f.criticality DESC""",
                (node_id,),
            ).fetchall()
            return [self._row_to_flow(row) for row in rows]

    def _row_to_flow(self, row: sqlite3.Row) -> Flow:
        """Convert a database row to a Flow dataclass."""
        return Flow(
            id=row["id"],
            name=row["name"],
            entry_point_id=row["entry_point_id"],
            depth=row["depth"],
            node_count=row["node_count"],
            file_count=row["file_count"],
            criticality=row["criticality"],
            path_json=row["path_json"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Communities
    # ------------------------------------------------------------------

    def get_communities(self, limit: int = 50) -> list[Community]:
        """Get all communities."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM communities ORDER BY size DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_community(row) for row in rows]

    def get_community_summaries(self, limit: int = 50) -> list[CommunitySummary]:
        """Get all community summaries."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM community_summaries ORDER BY size DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_community_summary(row) for row in rows]

    def get_nodes_in_community(self, community_id: int, limit: int = 200) -> list[Node]:
        """Get all nodes belonging to a community."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM nodes WHERE community_id = ? LIMIT ?",
                (community_id, limit),
            ).fetchall()
            return [self._row_to_node(row) for row in rows]

    def get_high_risk_communities(self) -> list[CommunitySummary]:
        """Get communities flagged with high risk."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM community_summaries WHERE risk = 'high' ORDER BY size DESC"
            ).fetchall()
            return [self._row_to_community_summary(row) for row in rows]

    def _row_to_community(self, row: sqlite3.Row) -> Community:
        """Convert a database row to a Community dataclass."""
        return Community(
            id=row["id"],
            name=row["name"],
            level=row["level"],
            parent_id=row["parent_id"],
            cohesion=row["cohesion"],
            size=row["size"],
            dominant_language=row["dominant_language"],
            description=row["description"],
            created_at=row["created_at"],
        )

    def _row_to_community_summary(self, row: sqlite3.Row) -> CommunitySummary:
        """Convert a database row to a CommunitySummary dataclass."""
        return CommunitySummary(
            community_id=row["community_id"],
            name=row["name"],
            purpose=row["purpose"],
            key_symbols=row["key_symbols"],
            risk=row["risk"],
            size=row["size"],
            dominant_language=row["dominant_language"],
        )

    # ------------------------------------------------------------------
    # Risk Index
    # ------------------------------------------------------------------

    def get_risk_entries(
        self,
        min_score: float = 0.0,
        security_only: bool = False,
        limit: int = 100,
    ) -> list[RiskEntry]:
        """Get risk index entries with optional filters.

        Args:
            min_score: Minimum risk score (0.0 to 1.0).
            security_only: Only return security-relevant entries.
            limit: Maximum results.
        """
        query = "SELECT * FROM risk_index WHERE risk_score >= ?"
        params: list[Any] = [min_score]

        if security_only:
            query += " AND security_relevant = 1"

        query += " ORDER BY risk_score DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_risk_entry(row) for row in rows]

    def get_high_risk_nodes(self, threshold: float = 0.7) -> list[RiskEntry]:
        """Get nodes with risk score above a threshold."""
        return self.get_risk_entries(min_score=threshold)

    def get_untested_nodes(self, limit: int = 100) -> list[RiskEntry]:
        """Get nodes that lack test coverage (test_coverage = 'unknown')."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM risk_index WHERE test_coverage = 'unknown' ORDER BY risk_score DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_risk_entry(row) for row in rows]

    def _row_to_risk_entry(self, row: sqlite3.Row) -> RiskEntry:
        """Convert a database row to a RiskEntry dataclass."""
        return RiskEntry(
            node_id=row["node_id"],
            qualified_name=row["qualified_name"],
            risk_score=row["risk_score"],
            caller_count=row["caller_count"],
            test_coverage=row["test_coverage"],
            security_relevant=bool(row["security_relevant"]),
            last_computed=row["last_computed"],
        )

    # ------------------------------------------------------------------
    # Flow Snapshots
    # ------------------------------------------------------------------

    def get_flow_snapshots(self, limit: int = 50) -> list[FlowSnapshot]:
        """Get all flow snapshots."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM flow_snapshots ORDER BY criticality DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_flow_snapshot(row) for row in rows]

    def _row_to_flow_snapshot(self, row: sqlite3.Row) -> FlowSnapshot:
        """Convert a database row to a FlowSnapshot dataclass."""
        return FlowSnapshot(
            flow_id=row["flow_id"],
            name=row["name"],
            entry_point=row["entry_point"],
            critical_path=row["critical_path"],
            criticality=row["criticality"],
            node_count=row["node_count"],
            file_count=row["file_count"],
        )

    # ------------------------------------------------------------------
    # Statistics & Summary
    # ------------------------------------------------------------------

    def get_statistics(self) -> dict[str, Any]:
        """Get comprehensive database statistics."""
        with self._conn() as conn:
            return {
                "database_path": str(self.db_path),
                "schema_version": self.get_schema_version(),
                "last_updated": self.get_last_updated(),
                "nodes": {
                    "total": self.get_total_nodes(),
                    "by_kind": self.get_node_kinds(),
                },
                "edges": {
                    "total": self.get_total_edges(),
                    "by_kind": self.get_edge_kinds(),
                },
                "flows": conn.execute("SELECT COUNT(*) FROM flows").fetchone()[0],
                "communities": conn.execute(
                    "SELECT COUNT(*) FROM communities"
                ).fetchone()[0],
                "risk_entries": conn.execute(
                    "SELECT COUNT(*) FROM risk_index"
                ).fetchone()[0],
            }

    def print_summary(self) -> None:
        """Print a human-readable summary of the database."""
        stats = self.get_statistics()

        print("=" * 60)
        print("  PERSISTENT MEMORY DATABASE")
        print("=" * 60)
        print(f"  Path:      {stats['database_path']}")
        print(f"  Schema:    v{stats['schema_version']}")
        print(f"  Updated:   {stats['last_updated']}")
        print()
        print(f"  Nodes:     {stats['nodes']['total']:,}")
        for kind, count in stats["nodes"]["by_kind"].items():
            print(f"    {kind}: {count:,}")
        print(f"  Edges:     {stats['edges']['total']:,}")
        for kind, count in stats["edges"]["by_kind"].items():
            print(f"    {kind}: {count:,}")
        print(f"  Flows:     {stats['flows']}")
        print(f"  Communities: {stats['communities']}")
        print(f"  Risk entries: {stats['risk_entries']}")
        print("=" * 60)


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def connect(db_path: str = "workspace/graph.db") -> PersistentMemoryDB:
    """Create a connection to the persistent memory database.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A PersistentMemoryDB instance ready for queries.

    Raises:
        FileNotFoundError: If the database file does not exist.
    """
    return PersistentMemoryDB(db_path)


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def main():
    """Command-line entry point for exploring the persistent memory database."""

    parser = argparse.ArgumentParser(
        description="Persistent Memory - SQL Database Explorer"
    )
    parser.add_argument(
        "--db", type=str, default="workspace/graph.db", help="Path to SQLite database"
    )
    parser.add_argument(
        "--summary", action="store_true", help="Print database summary"
    )
    parser.add_argument(
        "--search", type=str, help="Full-text search nodes", metavar="QUERY"
    )
    parser.add_argument(
        "--flow", type=str, help="Show flow details by name", metavar="NAME"
    )
    parser.add_argument(
        "--high-risk", action="store_true", help="Show high-risk nodes (score >= 0.7)"
    )
    parser.add_argument(
        "--communities", action="store_true", help="list community summaries"
    )
    parser.add_argument(
        "--limit", type=int, default=20, help="Maximum results (default: 20)"
    )

    args = parser.parse_args()

    db = PersistentMemoryDB(args.db)

    if args.summary or not any([args.search, args.flow, args.high_risk, args.communities]):
        db.print_summary()

    if args.search:
        print("\n--- Search Results ---")
        results = db.search_nodes(args.search, limit=args.limit)
        for node, rank in results:
            print(f"  [{node.kind}] {node.qualified_name} ({node.file_path})")

    if args.flow:
        flow = db.get_flow_by_name(args.flow)
        if flow:
            print(f"\n--- Flow: {flow.name} ---")
            print(f"  Criticality: {flow.criticality}")
            print(f"  Depth: {flow.depth}, Nodes: {flow.node_count}, Files: {flow.file_count}")
            members = db.get_flow_nodes(flow.id)
            if members:
                print("  Node sequence:")
                for nid in members:
                    node = db.get_node_by_id(nid)
                    if node:
                        print(f"    [{node.kind}] {node.qualified_name}")
        else:
            print(f"Flow '{args.flow}' not found.")

    if args.high_risk:
        entries = db.get_high_risk_nodes()
        print(f"\n--- High-Risk Nodes ({len(entries)}) ---")
        for entry in entries:
            print(
                f"  [{entry.risk_score:.2f}] {entry.qualified_name} "
                f"(callers: {entry.caller_count}, tested: {entry.test_coverage})"
            )

    if args.communities:
        summaries = db.get_community_summaries(limit=args.limit)
        print(f"\n--- Communities ({len(summaries)}) ---")
        for s in summaries:
            print(f"  [{s.risk.upper():>6}] {s.name} ({s.dominant_language}) - {s.purpose[:80]}")


if __name__ == "__main__":
    main()
