# coding: utf-8
"""Dreaming: offline consolidation of session traces into durable patterns.

Reads mindwalk opencode snapshot files (``.jsonl``: ``session``/``message``/
``part`` lines) plus optional judge reports, and writes a
``CONSOLIDATED_PATTERNS.md`` digest: per-session signatures (tool mix, error
rate, compactions, user turns) split into failure/success piles, tool-level
error rates across sessions, and recurring failure signatures. Pure
deterministic aggregation — no LLM calls — so a nightly job can run it
unattended; an LLM summary pass can layer on top later.

Not CORE's DreamingModule (scenario replay/synthesis engine); this writes
digest markdown. Renamed from ML.dream 2026-10-10.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class SessionSignature:
    session_id: str = ""
    title: str = ""
    events: int = 0
    tools: Counter = field(default_factory=Counter)
    errors: int = 0
    compactions: int = 0
    user_turns: int = 0
    verdicts: dict[str, str] = field(default_factory=dict)


def summarize_snapshot(path: Path) -> SessionSignature:
    """Reduce one snapshot file to a signature (never raises on bad lines)."""
    sig = SessionSignature(session_id=path.stem)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        note_failure("dream:L39", exc)
        return sig
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            line = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            note_failure("dream:L47", exc)
            continue
        kind = line.get("type")
        if kind == "session":
            ses = line.get("session", {})
            sig.title = str(ses.get("title", ""))
        elif kind == "message":
            if line.get("role") == "user":
                sig.user_turns += 1
        elif kind == "part":
            part_kind = line.get("kind", "")
            if part_kind == "compaction":
                sig.compactions += 1
            elif part_kind == "tool":
                status = str(line.get("status", ""))
                if status not in ("completed", "error"):
                    continue
                sig.events += 1
                sig.tools[str(line.get("tool", "?"))] += 1
                if status == "error":
                    sig.errors += 1
    return sig


def load_report_verdicts(report_path: Path) -> dict[str, str]:
    """Extract dimension->verdict from a judge report file ({} when absent)."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        note_failure("dream:L75", exc)
        return {}
    return {
        str(d.get("name", "?")): str(d.get("verdict", "?"))
        for d in report.get("dimensions", [])
        if isinstance(d, dict)
    }


def consolidate(
    snapshot_dir: Path | str,
    reports_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Aggregate all snapshots (+ optional reports keyed by session id)."""
    snapshot_dir = Path(snapshot_dir)
    by_id_reports: dict[str, dict[str, str]] = {}
    if reports_dir is not None:
        reports_dir = Path(reports_dir)
        if reports_dir.is_dir():
            for fp in reports_dir.glob("*.json"):
                by_id_reports[fp.stem] = load_report_verdicts(fp)
    sessions: list[SessionSignature] = []
    if snapshot_dir.is_dir():
        for fp in sorted(snapshot_dir.glob("*.jsonl")):
            sig = summarize_snapshot(fp)
            if fp.stem in by_id_reports:
                sig.verdicts = by_id_reports[fp.stem]
            sessions.append(sig)
    tool_total: Counter = Counter()
    for sig in sessions:
        for tool, count in sig.tools.items():
            tool_total[tool] += count
    # NOTE: error counts stay session-level. Attributing them to tools
    # proportionally would be dishonest without per-tool error parts.
    return {
        "sessions": sessions,
        "n_sessions": len(sessions),
        "tool_total": dict(tool_total),
    }


def _is_failure(sig: SessionSignature) -> bool:
    return any(v == "problem" for v in sig.verdicts.values()) or (
        not sig.verdicts and sig.errors > 0 and sig.events > 0
    )


def render_markdown(data: dict[str, Any]) -> str:
    """Render the consolidation digest."""
    sessions: list[SessionSignature] = data["sessions"]
    failed = [s for s in sessions if _is_failure(s)]
    ok = [s for s in sessions if s not in failed]
    lines = [
        "# Consolidated session patterns",
        "",
        f"Sessions: {len(sessions)} ({len(failed)} failure, {len(ok)} clean)",
        "",
        "## Failure signatures",
        "",
    ]
    if not failed:
        lines.append("(none)")
    for sig in failed:
        top_tools = ", ".join(f"{t}x{c}" for t, c in sig.tools.most_common(3)) or "no tools"
        lines.append(
            f"- **{sig.title or sig.session_id}**: {sig.events} events, "
            f"{sig.errors} errors, {sig.compactions} compactions, "
            f"{sig.user_turns} turns; tools: {top_tools}"
        )
        for dim, verdict in sorted(sig.verdicts.items()):
            if verdict == "problem":
                lines.append(f"  - problem dimension: {dim}")
    lines += ["", "## Tool usage across sessions", ""]
    totals: Counter = Counter()
    for sig in sessions:
        totals.update(sig.tools)
    if not totals:
        lines.append("(no tool events)")
    for tool, count in totals.most_common(15):
        lines.append(f"- {tool}: {count}")
    lines += ["", "## Success signatures", ""]
    if not ok:
        lines.append("(none)")
    for sig in ok[:10]:
        lines.append(
            f"- **{sig.title or sig.session_id}**: {sig.events} events, "
            f"{sig.errors} errors, {sig.user_turns} turns"
        )
    lines.append("")
    return "\n".join(lines)


def dream(
    snapshot_dir: Path | str,
    reports_dir: Path | str | None,
    out_path: Path | str,
) -> Path:
    """Run one consolidation pass; returns the written digest path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(consolidate(snapshot_dir, reports_dir)), encoding="utf-8")
    return out_path
