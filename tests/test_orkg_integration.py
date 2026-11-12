# coding: utf-8
"""Functional tests for CORE/orkg_integration.py — actual method behavior.

Uses unittest (compatible with Python 3.14t where pytest segfaults).
"""

import sys
import os
import json
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestORKGIntegrationKeywords(unittest.TestCase):
    """Test the _extract_keywords_from_title_abstract method."""

    def setUp(self):
        from CORE.orkg_integration import ORKGIntegration
        self.orkg = ORKGIntegration(db_path=(self.tmp.name if hasattr(self, "tmp") else None))

    def test_keyword_extraction_basic(self):
        """Basic keyword extraction from title + abstract."""
        title = "Reinforcement Learning for Robot Control"
        abstract = "We propose a new RL method for robot control that improves navigation accuracy."
        keywords = self.orkg._extract_keywords_from_title_abstract(title, abstract)
        self.assertIsInstance(keywords, list)
        self.assertGreater(len(keywords), 0)
        kw_lower = [k.lower() for k in keywords]
        self.assertIn("reinforcement", kw_lower)
        self.assertIn("learning", kw_lower)

    def test_keyword_extraction_no_stopwords(self):
        """Stop words should be filtered out."""
        title = "The quick brown fox jumps"
        abstract = "A test of stop word filtering in the abstract."
        keywords = self.orkg._extract_keywords_from_title_abstract(title, abstract)
        kw_lower = [k.lower() for k in keywords]
        self.assertNotIn("the", kw_lower)

    def test_keyword_min_length(self):
        """Only tokens with 4+ characters should be kept."""
        title = "AI"
        abstract = "I"
        keywords = self.orkg._extract_keywords_from_title_abstract(title, abstract)
        self.assertEqual(len(keywords), 0)

    def test_keyword_deduplication(self):
        """Keywords should be deduplicated."""
        title = "machine learning machine learning research"
        abstract = "machine learning"
        keywords = self.orkg._extract_keywords_from_title_abstract(title, abstract)
        self.assertEqual(len(keywords), len(set(keywords)))


class TestORKGIntegrationIndexPaper(unittest.TestCase):
    """Test index_paper method - indexes paper into knowledge_patterns table."""

    def setUp(self):
        from CORE.local_memory import LocalMemoryStore
        from CORE.orkg_integration import ORKGIntegration

        import tempfile
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = LocalMemoryStore(db_path=self.tmp.name)
        self.orkg = ORKGIntegration(db_path=(self.tmp.name if hasattr(self, "tmp") else None))

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_index_paper_with_full_data(self):
        """index_paper should store ORKG-structured paper."""
        paper = {
            "methodology": "New RL algorithm using graph networks",
            "results": "Improved navigation accuracy on benchmark tasks",
            "claims": "RL with graph nets achieves SOTA on navigation",
            "title": "Graph-based RL for Robot Navigation",
        }

        pid = self.orkg.index_paper(paper, source_agent="test_orkg")
        self.assertIsNotNone(pid, "index_paper should return a memory ID")
        self.assertIsInstance(pid, int, "index_paper should return an int ID")
        self.assertGreater(pid, 0, "index_paper should return a positive ID")

    def test_index_paper_handles_minimal_data(self):
        """index_paper should handle papers with minimal fields."""
        paper = {
            "title": "Minimal ORKG Paper",
        }

        pid = self.orkg.index_paper(paper, source_agent="test_orkg_minimal")
        self.assertIsNotNone(pid)


class TestORKGIntegrationFetchAndIndex(unittest.TestCase):
    """Test fetch_and_index method - end-to-end fetch and index."""

    def setUp(self):
        from CORE.local_memory import LocalMemoryStore
        from CORE.orkg_integration import ORKGIntegration

        import tempfile
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = LocalMemoryStore(db_path=self.tmp.name)
        self.orkg = ORKGIntegration(db_path=(self.tmp.name if hasattr(self, "tmp") else None))

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_fetch_and_index_basic(self):
        """fetch_and_index should fetch and index papers, return counts."""
        from CORE.orkg_integration import ORKGIntegration

        with patch.object(self.orkg, "_orkg_api_query", return_value={
            "methodologies": ["graph-based RL"],
            "results": ["improved accuracy"],
            "claims": ["achieves SOTA"],
        }):
            result = self.orkg.fetch_and_index(
                query="all",
                limit=10,
                max_age_days=365,
                source_agent="test_fetch_and_index",
            )

        self.assertIn("fetched", result)
        self.assertIn("indexed", result)
        self.assertIn("skipped", result)
        self.assertEqual(result["fetched"], 1)
        self.assertEqual(result["indexed"], 1)


class TestORKGIntegrationQueryKnowledge(unittest.TestCase):
    """Test query_knowledge method - queries knowledge graph."""

    def setUp(self):
        from CORE.local_memory import LocalMemoryStore
        from CORE.orkg_integration import ORKGIntegration

        import tempfile
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = LocalMemoryStore(db_path=self.tmp.name)
        self.orkg = ORKGIntegration(db_path=(self.tmp.name if hasattr(self, "tmp") else None))

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_query_knowledge_basic(self):
        """query_knowledge should search the knowledge graph."""
        paper = {
            "methodology": "graph-based RL",
            "results": "improved accuracy",
            "claims": "achieves SOTA",
            "title": "Graph-based RL for Navigation",
        }

        pid = self.orkg.index_paper(paper, source_agent="test_query_knowledge")
        self.assertIsNotNone(pid)

        with patch.object(self.orkg, "_orkg_api_query", return_value={}):
            results = self.orkg.query_knowledge(
                query="graph-based",
                limit=10,
                source="orkg_integration",
            )

        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0, "Should find indexed papers")


class TestORKGIntegrationStats(unittest.TestCase):
    """Test get_knowledge_graph_stats method."""

    def setUp(self):
        from CORE.local_memory import LocalMemoryStore
        from CORE.orkg_integration import ORKGIntegration

        import tempfile
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = LocalMemoryStore(db_path=self.tmp.name)
        self.orkg = ORKGIntegration(db_path=(self.tmp.name if hasattr(self, "tmp") else None))

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_get_knowledge_graph_stats(self):
        """get_knowledge_graph_stats should return stats about knowledge patterns."""
        stats = self.orkg.get_knowledge_graph_stats()
        self.assertIsInstance(stats, dict)
        self.assertIn("total_memories", stats)


class TestORKGMutationKills(unittest.TestCase):
    """Named tests closing scripts/mutate.py survivors (2026-10-17 lane).

    Or@241 (`r["metadata"] or {}`) is NOT covered here: index_paper always
    stores metadata as a JSON string, so the else-branch is defensive dead
    code through the public API — documented equivalent, not a test hole."""

    def setUp(self):
        from CORE.orkg_integration import ORKGIntegration
        self.orkg = ORKGIntegration(db_path=None)

    def test_none_title_guarded_to_empty(self):
        """Kills Or@115/116 (paper.get(..) or "" -> and "").

        Guard lives at the index_paper call site, so the test captures
        the call args there (see arxiv twin for the rationale)."""
        import os
        import tempfile
        from unittest.mock import patch
        from CORE.orkg_integration import ORKGIntegration
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            orkg = ORKGIntegration(db_path=tmp.name)
            with patch.object(
                    ORKGIntegration,
                    "_extract_keywords_from_title_abstract",
                    return_value=[]) as m:
                orkg.index_paper({"title": None,
                                  "abstract": "Learning systems explored."},
                                 source_agent="test_mut")
            self.assertEqual(m.call_args[0][0], "")
            self.assertEqual(m.call_args[0][1],
                             "Learning systems explored.")
        finally:
            os.unlink(tmp.name)


class TestORKGIntegrationDemo(unittest.TestCase):
    """Test demo() function."""

    def test_demo_function(self):
        """demo() should be callable."""
        from CORE.orkg_integration import demo
        self.assertTrue(callable(demo))


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    loader = unittest.TestLoader()
    suite = loader.discover(os.path.dirname(__file__), pattern="test_*.py")
    runner.run(suite)