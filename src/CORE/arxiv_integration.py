"""arXiv integration — fetch and index research papers into the knowledge graph.

This module integrates arXiv research papers with Azazael's existing knowledge graph
infrastructure (knowledge_patterns table, register_pattern, get_knowledge_graph_stats).

When run, it queries arXiv for papers matching topical keywords relevant to the
codebase (machine learning, AI, reinforcement learning, etc.), extracts metadata,
and stores it in the knowledge_patterns table for later querying via the existing
knowledge graph query interface.

Usage:
    from CORE.arxiv_integration import ArxivIntegration
    integration = ArxivIntegration()
    integration.fetch_and_index(limit=50, max_age_days=30)
    results = integration.query_knowledge("reinforcement learning", limit=10)
"""

from typing import Any, Dict, List, Optional
import urllib.parse
import urllib.request
import json
import time
import ssl
import re
import math
import zlib

try:
    from CORE.failure_ledger import note_failure
except ImportError:
    def note_failure(where, exc=None):
        pass

# arXiv API is free, no key required
ARXIV_API_BASE = "http://export.arxiv.org/api/query"
ARXIV_SEARCH_ENDPOINT = "http://export.arxiv.org/api/search"

# Create an SSL context that skips verification (arXiv uses valid certs but
# some Python/Windows environments have issues). Use http:// to avoid SSL entirely.
_ssl_context = ssl.create_default_context()
_ssl_context.check_hostname = False
_ssl_context.verify_mode = ssl.CERT_NONE


class ArxivIntegration:
    """Fetch arXiv papers and index them into the knowledge graph."""

    def __init__(self, max_retries: int = 3, backoff_factor: float = 1.0, db_path: Optional[str] = None):
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.db_path = db_path
        self.session_count = 0

    def _arxiv_api_query(
        self,
        search_query: str,
        start: int = 0,
        max_results: int = 30,
        sort_by: str = "submittedDate",
        sort_order: str = "descending",
    ) -> dict:
        """Query the arXiv API and return parsed XML/JSON response.

        The arXiv API returns Atom XML; we parse the essential fields manually
        to avoid extra dependencies.
        """
        params = {
            "search_query": search_query,
            "start": start,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
            "format": "export",
        }
        url = f"{ARXIV_API_BASE}?{urllib.parse.urlencode(params)}"

        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Azazael/1.0"})
                with urllib.request.urlopen(req, timeout=30, context=_ssl_context) as resp:
                    content = resp.read().decode("utf-8")
                return self._parse_arxiv_atom(content)
            except Exception as exc:
                if attempt < self.max_retries - 1:
                    wait = self.backoff_factor * (2 ** attempt)
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"arXiv API query failed: {exc}") from exc

        return {"entries": []}

    def _parse_arxiv_atom(self, xml_content: str) -> dict:
        """Parse arXiv API Atom XML into a dict of entries."""

        entries: List[Dict[str, Any]] = []
        # Split by <entry> tags
        entry_blocks = re.split(r"<entry>", xml_content)
        for block in entry_blocks[1:]:  # skip before first <entry>
            # Extract title
            title_match = re.search(r"<title>(.*?)</title>", block, re.DOTALL)
            title = title_match.group(1).strip() if title_match else None

            # Extract ID
            id_match = re.search(r"<id>(.*?)</id>", block, re.DOTALL)
            entry_id = id_match.group(1).strip() if id_match else None

            # Extract summary/abstract
            summary_match = re.search(r"<summary>(.*?)</summary>", block, re.DOTALL)
            summary = summary_match.group(1).strip() if summary_match else None

            # Extract published date
            published_match = re.search(r"<published>(.*?)</published>", block, re.DOTALL)
            published = published_match.group(1).strip() if published_match else None

            # Extract updated date
            updated_match = re.search(r"<updated>(.*?)</updated>", block, re.DOTALL)
            updated = updated_match.group(1).strip() if updated_match else None

            # Extract authors
            authors: List[str] = []
            author_blocks = re.findall(r"<name>(.*?)</name>", block)
            authors = author_blocks

            # Extract arXiv ID from the entry ID (format: http://arxiv.org/abs/XXXXXXXXX)
            arxiv_id = None
            if entry_id and "/abs/" in entry_id:
                arxiv_id = entry_id.split("/abs/")[-1].split("v")[0]

            # Extract categories
            categories: List[str] = []
            category_matches = re.findall(r'<category term="([^"]+)"', block)
            categories = [m[1] for m in category_matches]

            # Extract primary category (first)
            primary_category = categories[0] if categories else None

            entries.append(
                {
                    "id": entry_id,
                    "arxiv_id": arxiv_id,
                    "title": title,
                    "abstract": summary,
                    "published": published,
                    "updated": updated,
                    "authors": authors,
                    "categories": categories,
                    "primary_category": primary_category,
                }
            )

        return {"entries": entries}

    def fetch_papers(
        self,
        query: str = "all",
        max_results: int = 50,
        sort_by: str = "submittedDate",
        sort_order: str = "descending",
        concept_filter: Optional[List[str]] = None,
        author_filter: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch papers from arXiv matching the given query.

        Args:
            query: arXiv search query string (e.g., '"machine learning" OR "reinforcement learning"').
                Default searches all categories.
            max_results: Maximum number of papers to return.
            sort_by: Sort criterion ("submittedDate", "relevance", "lastUpdatedDate").
            sort_order: " ascending" or "descending".
            concept_filter: Optional list of concept keywords to filter by (e.g., ['reinforcement', 'learning']).
                Papers must match at least one concept in title/abstract.
            author_filter: Optional list of author names to filter by.
                Papers must have at least one matching author (case-insensitive substring).

        Returns:
            List of paper dicts with arXiv metadata.
        """
        result = self._arxiv_api_query(
            search_query=query,
            start=0,
            max_results=max_results,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        entries = result.get("entries", [])

        # Apply concept filter
        if concept_filter:
            concept_lower = [c.lower() for c in concept_filter]
            filtered = []
            for entry in entries:
                text = ""
                if entry.get("title"):
                    text += entry["title"] + " "
                if entry.get("abstract"):
                    text += entry["abstract"] + " "
                if any(c in text.lower() for c in concept_lower):
                    filtered.append(entry)
            entries = filtered

        # Apply author filter
        if author_filter:
            author_lower = [a.lower() for a in author_filter]
            filtered = []
            for entry in entries:
                entry_authors = [a.lower() for a in entry.get("authors", [])]
                if any(any(al in ea for al in author_lower for ea in entry_authors) for _ in [0]):
                    filtered.append(entry)
            entries = filtered

        return entries

    def _extract_keywords_from_title_abstract(self, title: str, abstract: str) -> List[str]:
        """Extract simple keywords from paper title + abstract.

        Very naive: lowercases, removes stop words, keeps alphanumeric tokens.
        Subclasses may override for domain-specific keyword extraction.
        """

        stop_words = {
            "the", "and", "or", "but", "is", "are", "was", "were", "be", "been",
            "a", "an", "as", "at", "by", "for", "from", "of", "on", "to", "in",
            "it", "its", "we", "our", "you", "your", "that", "this", "with",
            "for", "was", "have", "has", "had", "will", "would", "could", "should",
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

    def index_paper(
        self,
        paper: Dict[str, Any],
        source_agent: str = "arxiv_integration",
        pattern_type: str = "research_paper",
    ) -> Optional[int]:
        """Index a single arXiv paper into the knowledge_patterns table.

        Paper metadata (title, abstract, authors, and categories) is stored in
        the metadata field and a deterministic keyword-hash feature vector in
        the embedding field, so papers can be searched later via the existing
        knowledge graph query interface.

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
                "arxiv_id": paper.get("arxiv_id"),
                "title": paper.get("title"),
                "abstract": paper.get("abstract"),
                "published": paper.get("published"),
                "updated": paper.get("updated"),
                "authors": paper.get("authors", [])[:10],  # top 10 authors
                "categories": paper.get("categories", []),
                "primary_category": paper.get("primary_category"),
                "source": "arxiv",
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
            logging.warning(f"Failed to index arXiv paper: {exc}")
            return None

    def fetch_and_index(
        self,
        query: str = '"machine learning" OR "reinforcement learning" OR "deep landing"',
        limit: int = 100,
        max_age_days: int = 90,
        source_agent: str = "arxiv_integration",
    ) -> Dict[str, Any]:
        """Fetch papers from arXiv and index them into the knowledge graph.

        Args:
            query: arXiv search query string.
            limit: Maximum number of papers to fetch and index.
            max_age_days: Only fetch papers newer than this many days (0 = no age filter).
            source_agent: Source agent name for the knowledge_patterns entry.

        Returns:
            Dict with counts: {"fetched": N, "indexed": M, "skipped": K}
        """
        # Fetch papers from arXiv
        papers = self.fetch_papers(query=query, max_results=limit)

        fetched = len(papers)
        indexed = 0
        skipped = 0

        for paper in papers:
            # Skip if very old (optional age filter)
            if max_age_days and paper.get("published"):
                from datetime import datetime, timezone

                try:
                    pub_date = datetime.fromisoformat(
                        paper["published"].replace("Z", "+00:00")
                    )
                    age_days = (datetime.now(timezone.utc) - pub_date).days
                    if age_days > max_age_days:
                        skipped += 1
                        continue
                except (ValueError, TypeError) as exc:
                    note_failure("arxiv_integration:parse_date", exc)  # If we can't parse the date, don't skip

            # Index the paper
            pid = self.index_paper(paper, source_agent=source_agent)
            if pid is not None:
                indexed += 1
            else:
                skipped += 1

        return {
            "fetched": fetched,
            "indexed": indexed,
            "skipped": skipped,
            "query": query,
            "source_agent": source_agent,
        }

    def query_knowledge(
        self,
        query: str,
        limit: int = 50,
        source: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query the knowledge graph for papers matching the search query.

        Uses the existing query_memories infrastructure to search across
        indexed arXiv papers plus any other memories in the store.

        Args:
            query: Search text (matched against title, abstract, authors in metadata).
            limit: Maximum results to return.
            source: Optional filter by source (e.g., "arxiv_integration").
            event_type: Optional filter by event type.

        Returns:
            List of memory dicts, ranked by relevance (FTS5 rank).
        """
        from CORE.local_memory import LocalMemoryStore

        store = LocalMemoryStore(db_path=self.db_path)

        # Use the existing search_memories which does FTS5 search
        results = store.search_memories(
            query=query,
            source=source,
            limit=limit,
        )

        return results

    def get_knowledge_graph_stats(self) -> Dict[str, Any]:
        """Get statistics about the knowledge graph, including indexed arXiv papers.

        Returns:
            Dict with statistics about knowledge_patterns and total memories.
        """
        from CORE.local_memory import LocalMemoryStore

        store = LocalMemoryStore(db_path=self.db_path)

        # Get knowledge_patterns stats
        kp_stats = store.get_knowledge_graph_stats()

        # Add arXiv-specific stats
        conn = None
        try:
            conn = store._get_conn()
            kp_count = conn.execute(
                "SELECT COUNT(*) as n FROM knowledge_patterns WHERE pattern_type = 'research_paper'"
            ).fetchone()["n"]
            arxiv_count = conn.execute(
                "SELECT COUNT(*) as n FROM knowledge_patterns WHERE source = 'arxiv_integration'"
            ).fetchone()["n"]
        except Exception:
            kp_count = 0
            arxiv_count = 0
        finally:
            if conn is not None:
                conn.close()
            kp_stats["research_paper_count"] = kp_count
            kp_stats["arxiv_integration_count"] = arxiv_count

        return kp_stats


def demo():
    """Demonstration: fetch a few arXiv papers and index them, then query."""
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    integration = ArxivIntegration()

    print("=" * 60)
    print("Step 1: Fetch arXiv papers on ML topics")
    print("=" * 60)

    result = integration.fetch_and_index(
        query='"machine learning" OR "deep learning"',
        limit=10,
        max_age_days=365,
        source_agent="azazael_demo",
    )

    print(f"Fetched: {result['fetched']}")
    print(f"Indexed: {result['indexed']}")
    print(f"Skipped: {result['skipped']}")

    print()
    print("=" * 60)
    print("Step 2: Query the knowledge graph")
    print("=" * 60)

    results = integration.query_knowledge("reinforcement learning", limit=5)
    print(f"Found {len(results)} results for 'reinforcement learning':")
    for r in results[:3]:
        print(f"  - ID {r.get('id', '?')}: {r.get('source', '?')}/{r.get('event_type', '?')}")
        # Try to decode metadata
        try:
            meta = json.loads(r.get("data", "{}") or "{}")
            print(f"    metadata keys: {list(meta.keys())[:5]}")
        except Exception as exc:
            note_failure("arxiv_integration:demo_metadata", exc)
            pass

    print()
    print("=" * 60)
    print("Step 3: Knowledge graph stats")
    print("=" * 60)

    stats = integration.get_knowledge_graph_stats()
    print(f"Knowledge graph stats: {json.dumps(stats, indent=2)}")


if __name__ == "__main__":
    demo()