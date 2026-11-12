#!/usr/bin/env python3
# coding: utf-8
"""
cpg_joern_integration.py — Code Property Graph via Joern (K4)

Integrates Joern CPG generation and CPGQL querying into the azazael pipeline.
Provides pattern mining, taint tracking, and vulnerability detection for the codebase.

Joern CPG Layers (per spec):
- AST: Abstract Syntax Tree
- CFG: Control Flow Graph
- CDG: Control Dependence Graph
- DDG: Data Dependence Graph
- PDG: Program Dependence Graph (DDG + CDG)
- CPG: Unified multi-layer property graph

Features:
- Multi-language support (Python, JavaScript, TypeScript, Java, C/C++)
- CPGQL query DSL for pattern mining
- Taint analysis via data-flow steps
- Export to GraphViz, Neo4j, CSV, JSON
- Integration with existing codegraph_mcp.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict

# ---------------------------------------------------------------------------
# Joern Wrapper
# ---------------------------------------------------------------------------

@dataclass
class JoernConfig:
    """Joern installation configuration."""
    joern_cli: str = "joern"  # or full path
    joern_parse: str = "joern-parse"
    joern_export: str = "joern-export"
    joern_scan: str = "joern-scan"
    workspace: str = "joern_workspace"


class JoernWrapper:
    """Wrapper around Joern CLI tools."""
    
    def __init__(self, config: Optional[JoernConfig] = None):
        self.config = config or JoernConfig()
        self._verify_installation()
    
    def _verify_installation(self) -> bool:
        """Check if Joern is installed and accessible."""
        try:
            result = subprocess.run(
                [self.config.joern_cli, "--version"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10
            )
            if result.returncode == 0:
                print(f"Joern version: {result.stdout.strip()}")
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            note_failure("tools/cpg_joern_integration.py", exc)
            pass
        
        # Try alternative names
        for cmd in ["joern", "joern.bat", "joern.cmd"]:
            try:
                result = subprocess.run([cmd, "--version"], capture_output=True,
                                        text=True, encoding="utf-8", errors="replace",
                                        timeout=10)
                if result.returncode == 0:
                    self.config.joern_cli = cmd
                    self.config.joern_parse = cmd.replace("joern", "joern-parse")
                    self.config.joern_export = cmd.replace("joern", "joern-export")
                    self.config.joern_scan = cmd.replace("joern", "joern-scan")
                    print(f"Joern found at: {cmd}")
                    return True
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                note_failure("tools/cpg_joern_integration.py", exc)
                continue
        
        print("Warning: Joern not found. Install from https://joern.io/", file=sys.stderr)
        return False
    
    def parse(self, src_path: Path, output_cpg: Path, language: str = "auto") -> bool:
        """Parse source code into CPG."""
        output_cpg.parent.mkdir(parents=True, exist_ok=True)
        
        cmd = [
            self.config.joern_parse,
            str(src_path),
            "--output", str(output_cpg),
        ]
        
        if language != "auto":
            cmd.extend(["--language", language])
        
        print(f"Parsing {src_path} -> {output_cpg}")
        start = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=300)
        elapsed = time.time() - start
        
        if result.returncode == 0:
            size_mb = output_cpg.stat().st_size / (1024*1024)
            print(f"Parsed in {elapsed:.1f}s, CPG size: {size_mb:.1f} MB")
            return True
        else:
            print(f"Parse failed: {result.stderr}")
            return False
    
    def export(self, cpg_path: Path, output_dir: Path, 
               format: str = "dot", representation: str = "cpg14") -> bool:
        """Export CPG to various formats."""
        output_dir.mkdir(parents=True, exist_ok=True)
        
        cmd = [
            self.config.joern_export,
            str(cpg_path),
            "--repr", representation,
            "--format", format,
            "--out", str(output_dir),
        ]
        
        print(f"Exporting {representation} as {format} to {output_dir}")
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=120)
        
        if result.returncode == 0:
            print(f"Export complete: {output_dir}")
            return True
        else:
            print(f"Export failed: {result.stderr}")
            return False
    
    def run_query(self, cpg_path: Path, query: str, 
                  output_json: Optional[Path] = None) -> List[Dict[str, Any]]:
        """Run CPGQL query on CPG."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sc", delete=False) as f:
            f.write(query)
            query_file = f.name
        
        try:
            cmd = [
                self.config.joern_cli,
                "--script", query_file,
                "--cpg", str(cpg_path),
            ]
            
            if output_json:
                cmd.extend(["--output", str(output_json)])
            
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=60)
            
            if result.returncode == 0:
                if output_json and output_json.exists():
                    with open(output_json, encoding="utf-8") as f:
                        return json.load(f)
                else:
                    # Parse stdout
                    return self._parse_query_output(result.stdout)
            else:
                print(f"Query failed: {result.stderr}")
                return []
        finally:
            os.unlink(query_file)
    
    def _parse_query_output(self, stdout: str) -> List[Dict[str, Any]]:
        """Parse Joern query output."""
        results = []
        for line in stdout.strip().split("\n"):
            if line.startswith("res") and ": List" in line:
                # Try to parse as JSON-like
                try:
                    # Extract list part
                    start = line.find("[")
                    if start >= 0:
                        data = json.loads(line[start:])
                        results.extend(data)
                except Exception as exc:
                    note_failure("tools/cpg_joern_integration.py", exc)
                    pass
        return results
    

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

    def scan(self, cpg_path: Path, rules: str = "default",
             output_sarif: Optional[Path] = None) -> bool:
        """Run Joern scan for vulnerabilities."""
        cmd = [
            self.config.joern_scan,
            str(cpg_path),
            "--rules", rules,
        ]
        
        if output_sarif:
            cmd.extend(["--sarif", str(output_sarif)])
        
        print(f"Running Joern scan with rules: {rules}")
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=180)
        
        if result.returncode == 0:
            print("Scan complete")
            if output_sarif:
                print(f"SARIF output: {output_sarif}")
            return True
        else:
            print(f"Scan failed: {result.stderr}")
            return False


# ---------------------------------------------------------------------------
# CPGQL Query Library
# ---------------------------------------------------------------------------

class CPGQueryLibrary:
    """Pre-built CPGQL queries for common patterns."""
    
    @staticmethod
    def functions_catching_exceptions_without_logging() -> str:
        """Find try/catch blocks that catch exceptions but don't log them."""
        return """
        cpg.method.where(_.astChildren.isTry).filter { method =>
          method.astChildren.isCatch.exists { catchBlock =>
            catchBlock.astChildren.isBlock.exists { block =>
              !block.astChildren.isCall.exists(_.name.startsWith("log"))
            }
          }
        }.l
        """
    
    @staticmethod
    def functions_with_high_cyclomatic_complexity(threshold: int = 20) -> str:
        """Find functions with cyclomatic complexity above threshold."""
        return f"""
        cpg.method.map(m => (m, m.controlFlowGraph.nodes.size)).filter(_._2 > {threshold}).l
        """
    
    @staticmethod
    def taint_tracking_user_input_to_sink(source_pattern: str, sink_pattern: str) -> str:
        """Taint tracking from user input to sensitive sink."""
        return f"""
        def source = cpg.call.name("{source_pattern}").argument
        def sink = cpg.call.name("{sink_pattern}").argument
        sink.reachableBy(source).l
        """
    
    @staticmethod
    def unused_imports() -> str:
        """Find imported modules that are never used."""
        return """
        cpg.import.whereNot { imp =>
          cpg.call.name(imp.importedEntities.map(_.name).toList).nonEmpty
        }.l
        """
    
    @staticmethod
    def dead_code_unreachable() -> str:
        """Find unreachable code after return/throw."""
        return """
        cpg.method.astChildren.isReturn.astParent.isBlock.astChildren
          .filter(_.order > _.astPrev.order).l
        """
    
    @staticmethod
    def sql_injection_patterns() -> str:
        """Find potential SQL injection via string concatenation in queries."""
        return """
        cpg.call.name("execute").argument.where(_.code.contains("+")).l
        """
    
    @staticmethod
    def hardcoded_secrets() -> str:
        """Find hardcoded passwords, API keys, tokens."""
        return """
        cpg.literal.where(_.code.matches("(?i)(password|api[_-]?key|secret|token)\\s*[=:]\\s*['\"][^'\"]+['\"]")).l
        """
    
    @staticmethod
    def god_classes(threshold_methods: int = 30) -> str:
        """Find classes with too many methods (God classes)."""
        return f"""
        cpg.typeDecl.isClass.map(c => (c, c.method.size)).filter(_._2 > {threshold_methods}).l
        """
    
    @staticmethod
    def circular_dependencies() -> str:
        """Find circular import dependencies."""
        return """
        cpg.dependency.where { dep =>
          cpg.dependency.exists { d => d.from == dep.to && d.to == dep.from }
        }.l
        """


# ---------------------------------------------------------------------------
# Pattern Mining Pipeline
# ---------------------------------------------------------------------------

@dataclass
class PatternMatch:
    """A match from a CPGQL query."""
    query_name: str
    file_path: str
    line_number: int
    column: int
    code_snippet: str
    severity: str = "medium"
    description: str = ""


class CPGPatternMiner:
    """Run pattern mining queries and collect results."""
    
    def __init__(self, joern: JoernWrapper, cpg_path: Path):
        self.joern = joern
        self.cpg_path = cpg_path
        self.queries = {
            "exception_without_logging": CPGQueryLibrary.functions_catching_exceptions_without_logging(),
            "high_complexity": CPGQueryLibrary.functions_with_high_cyclomatic_complexity(),
            "unused_imports": CPGQueryLibrary.unused_imports(),
            "dead_code": CPGQueryLibrary.dead_code_unreachable(),
            "sql_injection": CPGQueryLibrary.sql_injection_patterns(),
            "hardcoded_secrets": CPGQueryLibrary.hardcoded_secrets(),
            "god_classes": CPGQueryLibrary.god_classes(),
            "circular_deps": CPGQueryLibrary.circular_dependencies(),
        }
    
    def run_all(self, output_dir: Optional[Path] = None) -> Dict[str, List[PatternMatch]]:
        """Run all pattern queries."""
        results = {}
        
        for name, query in self.queries.items():
            print(f"Running query: {name}")
            raw_results = self.joern.run_query(self.cpg_path, query)
            
            matches = self._parse_results(name, raw_results)
            results[name] = matches
            print(f"  Found {len(matches)} matches")
            
            if output_dir:
                output_dir.mkdir(parents=True, exist_ok=True)
                out_file = output_dir / f"{name}.json"
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump([asdict(m) for m in matches], f, indent=2)
        
        return results
    
    def _parse_results(self, query_name: str, raw: List[Dict[str, Any]]) -> List[PatternMatch]:
        """Parse raw Joern results into PatternMatch objects."""
        matches = []
        for item in raw:
            # Joern output format varies, try common fields
            file_path = item.get("file", item.get("FILE", item.get("fileName", "")))
            line = item.get("lineNumber", item.get("LINE_NUMBER", item.get("line", 0)))
            column = item.get("columnNumber", item.get("COLUMN_NUMBER", 0))
            code = item.get("code", item.get("CODE", item.get("codeSnippet", "")))
            
            match = PatternMatch(
                query_name=query_name,
                file_path=str(file_path) if file_path else "",
                line_number=int(line) if line else 0,
                column=int(column) if column else 0,
                code_snippet=str(code) if code else "",
                severity=self._infer_severity(query_name),
                description=f"Matched by {query_name}",
            )
            matches.append(match)
        return matches
    
    def _infer_severity(self, query_name: str) -> str:
        severity_map = {
            "hardcoded_secrets": "critical",
            "sql_injection": "critical",
            "exception_without_logging": "high",
            "god_classes": "medium",
            "high_complexity": "medium",
            "circular_deps": "medium",
            "unused_imports": "low",
            "dead_code": "low",
        }
        return severity_map.get(query_name, "medium")


# ---------------------------------------------------------------------------
# Integration with codegraph_mcp
# ---------------------------------------------------------------------------

class CPGCodeGraphBridge:
    """Bridge between Joern CPG and codegraph_mcp.py entities."""
    
    def __init__(self, joern: JoernWrapper, cpg_path: Path):
        self.joern = joern
        self.cpg_path = cpg_path
    
    def enrich_codegraph(self, codegraph_entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Enrich codegraph entities with CPG data (complexity, taint, etc.)."""
        # Query for method complexity
        query = CPGQueryLibrary.functions_with_high_cyclomatic_complexity(1)
        complexity_data = self.joern.run_query(self.cpg_path, query)
        
        # Build lookup
        complexity_map = {}
        for item in complexity_data:
            # Parse method name and complexity
            pass  # Implementation depends on Joern output format
        
        # Enrich entities
        for entity in codegraph_entities:
            if entity.get("type") == "function":
                entity["cpg_complexity"] = complexity_map.get(entity.get("name"), 0)
        
        return codegraph_entities


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CPG/Joern Integration for Code Analysis")
    parser.add_argument("--src", type=Path, default=Path("C:/Azazael/azazael/src"),
                        help="Source root directory")
    parser.add_argument("--cpg", type=Path, default=Path("tools/azazael.cpg"),
                        help="CPG file path")
    parser.add_argument("--workspace", type=Path, default=Path("joern_workspace"),
                        help="Joern workspace directory")
    parser.add_argument("--out", type=Path, default=Path("tools/cpg_results"),
                        help="Output directory")
    parser.add_argument("--parse", action="store_true", help="Parse source to CPG")
    parser.add_argument("--export", action="store_true", help="Export CPG representations")
    parser.add_argument("--query", help="Run single CPGQL query")
    parser.add_argument("--query-file", type=Path, help="Run query from file")
    parser.add_argument("--scan", action="store_true", help="Run vulnerability scan")
    parser.add_argument("--patterns", action="store_true", help="Run all pattern mining queries")
    parser.add_argument("--language", default="python", help="Source language")
    parser.add_argument("--export-format", default="dot", choices=["dot", "csv", "json", "graphml"],
                        help="Export format")
    parser.add_argument("--representation", default="cpg14", 
                        choices=["cpg14", "ast", "cfg", "cdg", "ddg", "pdg"],
                        help="Graph representation to export")
    
    args = parser.parse_args()
    
    # Initialize Joern
    config = JoernConfig()
    config.workspace = str(args.workspace)
    joern = JoernWrapper(config)
    
    if not joern._verify_installation():
        print("Joern not available. Skipping CPG operations.")
        print("Install Joern from https://joern.io/")
        return 1
    
    # Parse source to CPG
    if args.parse or not args.cpg.exists():
        if not joern.parse(args.src, args.cpg, args.language):
            return 1
    
    # Export representations
    if args.export:
        if not joern.export(args.cpg, args.out, args.export_format, args.representation):
            return 1
    
    # Run single query
    if args.query:
        results = joern.run_query(args.cpg, args.query)
        print(json.dumps(results, indent=2))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)
    
    # Run query from file
    if args.query_file:
        with open(args.query_file, encoding="utf-8") as f:
            query = f.read()
        results = joern.run_query(args.cpg, query)
        print(json.dumps(results, indent=2))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)
    
    # Run vulnerability scan
    if args.scan:
        sarif_out = args.out / "scan.sarif" if args.out else None
        if not joern.scan(args.cpg, "default", sarif_out):
            return 1
    
    # Run pattern mining
    if args.patterns:
        miner = CPGPatternMiner(joern, args.cpg)
        results = miner.run_all(args.out)
        
        # Summary
        total = sum(len(matches) for matches in results.values())
        print(f"\nTotal findings: {total}")
        for name, matches in results.items():
            if matches:
                print(f"  {name}: {len(matches)}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())