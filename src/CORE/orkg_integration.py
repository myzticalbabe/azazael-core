"""ORKG integration — reference implementation for Open Research Knowledge Graph.

This is a REFERENCE module demonstrating the integration pattern for the Open Research
Knowledge Graph (ORKG). ORKG is a structured knowledge graph for research papers that
stores not just metadata but also methodology, results, claims, and comparisons.

This module follows the exact same pattern as arxiv_integration.py and openalex_integration.py
so all three can be used interchangeably through the knowledge_patterns table.

Usage:
    From ORKG, papers are indexed via register_pattern() into knowledge_patterns,
    then queryable via query_knowledge() across all sources.

Note: ORKG API access requires authentication and is not freely open like arXiv/OpenAlex.
This file serves as the pattern/structure reference for future ORKG integration.
"""

from typing import Any, Dict, List, Optional
import json
import re
import time
import math
import zlib

# ORKG API — requires authentication, not freely open like arXiv/OpenAlex
# This is a placeholder/reference module. Actual ORKG integration would need:
# - ORKG account credentials
# - API token from orkg.org
# - Proper error handling for auth failures

OPENALEX_FIELDS = {
    "core": ["id", "title", "abstract", "publication_year", "authors", "fields_of_study"],
    "extended": ["cited_by_count", "openalex_cited_by_count", "venue", "concepts"],
}


class OrkGIntegration:
    """REFERENCE: ORKG integration class.

    This class follows the same interface as ArxivIntegration and
    OpenAlexIntegration so it can be used interchangeably through
    the knowledge_patterns table.

    Note: Actual ORKG API calls are not implemented here because
    ORKG requires authentication. The class structure is provided
    so future developers can implement the API calls following
    the same patterns established by arxiv_integration.py
    and openalex_integration.py.
    """

    def __init__(self, *args, db_path=None, **kwargs):
        """Initialize ORKG integration.

        Note: This is a reference implementation. In production, this would
        need ORKG API credentials and authentication setup.
        """
        self.db_path = db_path

    def _extract_keywords_from_title_abstract(self, title: str, abstract: str) -> List[str]:
        """Extract simple keywords from paper title + abstract.

        Very naive: lowercases, removes stop words, keeps alphanumeric tokens
        of 4+ characters, deduplicates while preserving order.
        """
        stop_words = {
            "the", "and", "or", "but", "is", "are", "was", "were", "be", "been",
            "a", "an", "as", "at", "by", "for", "from", "of", "on", "to", "in",
            "it", "its", "we", "our", "you", "your", "that", "this", "with",
            "have", "has", "had", "will", "would", "could", "should",
        }

        text = f"{title} {abstract}".lower()
        tokens = re.findall(r"\b[a-z]{4,}\b", text)
        keywords = [t for t in tokens if t not in stop_words]
        # Deduplicate while preserving order
        seen = set()
        deduped = []
        for k in keywords:
            if k not in seen:
                seen.add(k)
                deduped.append(k)
        return deduped[:20]

    def _keywords_to_vector(self, keywords: List[str], dim: int = 64) -> List[float]:
        """Deterministic bag-of-keywords feature vector for register_pattern.

        register_pattern expects a numeric feature vector and JSON-encodes its
        argument itself, so passing it a pre-encoded JSON *string* produced
        double-encoded rows that broke query_similar_patterns' cosine
        comparison. Each keyword hashes (CRC32) into one of `dim` buckets; the
        vector is L2-normalized so identical keyword sets compare at 1.0 and
        disjoint sets at 0.0. Empty keyword lists yield an all-zero vector
        (which _cosine_similarity scores as 0.0, never raising).
        """
        vec = [0.0] * dim
        for kw in keywords:
            bucket = zlib.crc32(kw.lower().encode("utf-8")) % dim
            vec[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [round(v / norm, 6) for v in vec]

    def _orkg_api_query(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """Query the ORKG API for papers.

        Reference implementation — returns empty result since ORKG requires
        authentication. Override or patch in tests.
        """
        return {
            "methodologies": [],
            "results": [],
            "claims": [],
        }

    def index_paper(
        self,
        paper: Dict[str, Any],
        source_agent: str = "orkg_integration",
        pattern_type: str = "research_paper",
    ) -> Optional[int]:
        """Index a single ORKG paper into the knowledge_patterns table.

        Paper metadata (title, abstract, methodology, results, and claims) is
        stored in the metadata field and a deterministic keyword-hash feature
        vector in the embedding field, so papers can be searched later via
        the existing knowledge graph query interface.

        Returns the memory ID (rowid), or None if indexing failed.
        """
        try:
            from CORE.local_memory import LocalMemoryStore

            store = LocalMemoryStore(db_path=self.db_path)

            # Build embedding from title + abstract keywords
            keywords = self._extract_keywords_from_title_abstract(
                paper.get("title", "") or "",
                paper.get("abstract", "") or "",
            )
            vector = self._keywords_to_vector(keywords)

            # Build metadata with all available fields
            metadata: Dict[str, Any] = {
                "title": paper.get("title"),
                "abstract": paper.get("abstract"),
                "methodology": paper.get("methodology"),
                "results": paper.get("results"),
                "claims": paper.get("claims"),
                "source": "orkg_integration",
            }

            # Use the existing register_pattern infrastructure
            # (it serializes metadata itself; pre-encoding would double-encode)
            pid = store.register_pattern(
                source_agent=source_agent,
                pattern_type=pattern_type,
                embedding=vector,
                metadata=metadata,
                confidence=1.0,
            )

            return pid
        except Exception as exc:
            import logging
            logging.warning(f"Failed to index ORKG paper: {exc}")
            return None

    def fetch_and_index(
        self,
        query: str = "machine learning",
        limit: int = 10,
        max_age_days: int = 365,
        source_agent: str = "orkg_integration",
    ) -> Dict[str, Any]:
        """Fetch papers from ORKG and index them into the knowledge graph.

        Args:
            query: ORKG search query string.
            limit: Maximum number of papers to fetch and index.
            max_age_days: Only fetch papers newer than this many days (0 = no age filter).
            source_agent: Source agent name for the knowledge_patterns entry.

        Returns:
            Dict with counts: {"fetched": N, "indexed": M, "skipped": K}
        """
        # Query ORKG API
        api_result = self._orkg_api_query(query=query, limit=limit)

        fetched = 1 if any(api_result.values()) else 0
        indexed = 0
        skipped = 0

        # Build a paper dict from the API result and index it
        paper = {
            "title": query,
            "abstract": "",
            "methodology": "; ".join(api_result.get("methodologies", [])),
            "results": "; ".join(api_result.get("results", [])),
            "claims": "; ".join(api_result.get("claims", [])),
        }

        pid = self.index_paper(paper, source_agent=source_agent)
        if pid is not None:
            indexed += 1
        else:
            skipped += 1

        return {
            "fetched": fetched,
            "indexed": indexed,
            "skipped": skipped,
        }

    def query_knowledge(
        self,
        query: str,
        limit: int = 50,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query the knowledge graph for ORKG papers matching the search query.

        Searches the knowledge_patterns table (where index_paper/register_pattern
        writes) using LIKE matching on the metadata JSON and source_agent.

        Args:
            query: Search text (matched against title, abstract, methodology, results, claims).
            limit: Maximum results to return.
            source: Optional filter by source (e.g., "orkg_integration").

        Returns:
            List of pattern dicts, ordered by id DESC.
        """
        from CORE.local_memory import LocalMemoryStore

        store = LocalMemoryStore(db_path=self.db_path)

        conn = store._get_conn()
        try:
            like = f"%{query}%"
            if source:
                rows = conn.execute(
                    "SELECT id, source_agent, pattern_type, embedding, metadata, confidence, created_at "
                    "FROM knowledge_patterns "
                    "WHERE (metadata LIKE ? OR source_agent LIKE ?) AND metadata LIKE ? "
                    "ORDER BY id DESC LIMIT ?",
                    (like, like, f"%{source}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, source_agent, pattern_type, embedding, metadata, confidence, created_at "
                    "FROM knowledge_patterns "
                    "WHERE metadata LIKE ? OR source_agent LIKE ? "
                    "ORDER BY id DESC LIMIT ?",
                    (like, like, limit),
                ).fetchall()
        finally:
            conn.close()

        results: List[Dict[str, Any]] = []
        for r in rows:
            try:
                metadata = json.loads(r["metadata"]) if isinstance(r["metadata"], str) else (r["metadata"] or {})
            except Exception:
                metadata = {}
            try:
                stored_embedding = (
                    json.loads(r["embedding"])
                    if isinstance(r["embedding"], str) else (r["embedding"] or [])
                )
            except Exception:
                stored_embedding = []
            # Legacy rows were double-encoded (json.dumps of a JSON string);
            # unwrap the extra layer so reads see the stored content.
            if isinstance(stored_embedding, str):
                try:
                    stored_embedding = json.loads(stored_embedding)
                except Exception:
                    stored_embedding = []
            results.append({
                "id": r["id"],
                "source_agent": r["source_agent"],
                "pattern_type": r["pattern_type"],
                "embedding": stored_embedding,
                "metadata": metadata,
                "confidence": r["confidence"],
                "created_at": r["created_at"],
                "source": r["source_agent"],
                "event_type": r["pattern_type"],
                "data": json.dumps(metadata),
            })

        return results

    def get_knowledge_graph_stats(self) -> Dict[str, Any]:
        """Get statistics about the knowledge graph, including indexed ORKG papers.

        Returns:
            Dict with statistics about knowledge_patterns and total memories.
        """
        from CORE.local_memory import LocalMemoryStore

        store = LocalMemoryStore(db_path=self.db_path)

        # Get knowledge_patterns stats from the store
        kp_stats = store.get_knowledge_graph_stats()

        # Add ORKG-specific stats
        conn = None
        try:
            conn = store._get_conn()
            orkg_count = conn.execute(
                "SELECT COUNT(*) as n FROM knowledge_patterns WHERE source = 'orkg_integration'"
            ).fetchone()["n"]
            kp_stats["orkg_integration_count"] = orkg_count
        except Exception:
            kp_stats["orkg_integration_count"] = 0
        finally:
            if conn is not None:
                conn.close()

        return kp_stats

    def fetch_papers(self, *args, **kwargs):
        """REFERENCE: Fetch papers from ORKG.

        Returns empty list — actual implementation would call ORKG API.
        """
        return []


# Alias for test compatibility — tests import ORKGIntegration (all caps)
ORKGIntegration = OrkGIntegration


def demo():
    """REFERENCE: Demo function for ORKG integration.

    Shows the pattern — actual ORKG fetching would require authentication.
    """
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    integration = OrkGIntegration()

    print("=" * 60)
    print("Step 1: ORKG integration (reference only)")
    print("=" * 60)

    result = integration.fetch_and_index(
        query="machine learning", limit=10, max_age_days=365
    )

    print(f"Fetched: {result['fetched']}")
    print(f"Indexed: {result['indexed']}")
    print(f"Skipped: {result['skipped']}")

    print()
    print("=" * 60)
    print("Step 2: Query the knowledge graph")
    print("=" * 60)

    results = integration.query_knowledge("reinforcement learning", limit=5)
    print(f"Found {len(results)} results for 'reinforcement learning'")

    print()
    print("=" * 60)
    print("Step 3: Knowledge graph stats")
    print("=" * 60)

    stats = integration.get_knowledge_graph_stats()
    print(f"Knowledge graph stats: {json.dumps(stats, indent=2)}")


if __name__ == "__main__":
    demo()
