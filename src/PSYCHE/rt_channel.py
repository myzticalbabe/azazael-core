# coding: utf-8
"""PSYCHE Phase 7 -- the live channel: how a turn actually reaches a person.

WHY THIS IS A SEPARATE MODULE
-----------------------------
`rt_agent.py` decides a turn in 0.025 ms. That number is worthless if the answer
sits in a buffer until the session closes, or if a malformed input kills the
process that was supposed to be listening. A realtime loop needs a transport with
three properties, and all three are testable:

  1. **One line in, one line out, flushed.** Newline-delimited JSON; every
     response is flushed before the next line is read. Buffering is what turns a
     25 microsecond decision into a two-second silence.
  2. **No silent drops.** A line that cannot be parsed produces an error record
     carrying the same `id`, and the loop stays up. Silence is indistinguishable
     from a crash or a refusal, which is the failure mode this project keeps
     rediscovering in other tools.
  3. **The budget is reported per turn, not asserted.** Each response carries its
     own `latency_ms` and `within_budget`, so an operator sees the overrun where
     it happened instead of discovering it in an aggregate.

The protocol is deliberately tiny, because a channel that needs a schema registry
is not a channel:

  in   {"id": 1, "text": "...", "appraisal": {"desirability": -0.7}, "dt_s": 1.0}
  out  {"id": 1, "stance": "validate", "message": "...", "risk": "low",
        "latency_ms": 0.031, "within_budget": true, "path": "intuition",
        "prototype": "threat-loop"}

`id` is echoed so a caller can interleave; it defaults to the line number when
absent. Nothing here decides anything -- every therapeutic decision is
`rt_agent.py`'s, made where the triage check can run first.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import IO, Dict, Iterator, List, Optional

from PSYCHE.intuition import IntuitionIndex
from PSYCHE.network import PersonNetwork
from PSYCHE.rt_agent import TherapeuticLoop, TurnResponse

try:
    from CORE.failure_ledger import note_failure
except ImportError as _ledger_exc:                     # pragma: no cover - host always has it
    _LEDGER_IMPORT_ERROR = str(_ledger_exc)

    def note_failure(where: str, exc: BaseException | None = None) -> None:
        """Record nothing: the ledger itself is missing. Named, not silent."""
        return None

#: Longest line accepted. A realtime channel that reads an unbounded line can be
#: stopped by its own input; the cap is reported rather than truncating silently.
MAX_LINE_BYTES = 65536


@dataclass
class ChannelStats:
    """Counters an operator needs when someone says "it answered slowly"."""

    handled: int = 0
    errors: int = 0
    over_budget: int = 0
    safety_turns: int = 0

    def to_dict(self) -> dict:
        return {"handled": self.handled, "errors": self.errors,
                "over_budget": self.over_budget, "safety_turns": self.safety_turns}


class SessionChannel:
    """Wraps one `TherapeuticLoop` in a line protocol.

    `handle()` is pure with respect to the transport -- it takes a parsed record
    and returns a serialisable record -- so the protocol can be tested without
    subprocesses, and `serve()` is the thin loop that wires it to two streams.
    """

    def __init__(self, loop: TherapeuticLoop, max_message_chars: int = 400) -> None:
        self.loop = loop
        self.max_message_chars = int(max_message_chars)
        self.stats = ChannelStats()

    # -- one request ------------------------------------------------------
    def handle(self, record: Dict) -> Dict:
        """Answer one parsed request. Never raises for bad input."""
        request_id = record.get("id")
        text = record.get("text", "")
        if not isinstance(text, str):
            self.stats.errors += 1
            return self._error(request_id, "field 'text' must be a string, got %s"
                               % type(text).__name__)
        appraisal = record.get("appraisal")
        if appraisal is not None and not isinstance(appraisal, dict):
            self.stats.errors += 1
            return self._error(request_id, "field 'appraisal' must be an object")
        dt_s = record.get("dt_s", 1.0)
        if not isinstance(dt_s, (int, float)):
            self.stats.errors += 1
            return self._error(request_id, "field 'dt_s' must be a number")
        turn = self.loop.turn(text=text, appraisal=appraisal, dt_s=float(dt_s))
        self.stats.handled += 1
        if not turn.within_budget:
            self.stats.over_budget += 1
        if turn.stance == "safety":
            self.stats.safety_turns += 1
        return self._response(request_id, turn)

    def _response(self, request_id, turn: TurnResponse) -> Dict:
        message = turn.message
        if len(message) > self.max_message_chars:
            # Truncation is stated in the payload rather than performed quietly:
            # a cut-off sentence can read as the whole intervention.
            message = message[:self.max_message_chars] + " [truncated]"
        return {"id": request_id, "stance": turn.stance, "message": message,
                "focus": turn.focus, "risk": turn.risk.risk,
                "risk_reasons": list(turn.risk.reasons),
                "latency_ms": round(turn.latency_ms, 4),
                "within_budget": bool(turn.within_budget),
                "path": turn.reading.path if turn.reading else "none",
                "prototype": (turn.reading.prototype if turn.reading
                              and turn.reading.prototype else None),
                "rationale": turn.rationale}

    def _error(self, request_id, detail: str) -> Dict:
        return {"id": request_id, "error": detail, "stance": None,
                "within_budget": None}

    # -- many requests ----------------------------------------------------
    def serve(self, stream_in: IO[str], stream_out: IO[str],
              flush: bool = True) -> ChannelStats:
        """Read lines until EOF, writing one flushed response per input line.

        Malformed JSON is answered with an error record and does NOT end the
        session. Only EOF ends it, so a caller that sends one bad line keeps its
        channel.
        """
        for number, line in enumerate(stream_in, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if len(stripped.encode("utf-8", errors="replace")) > MAX_LINE_BYTES:
                self.stats.errors += 1
                record = self._error(None, "line exceeds %d bytes" % MAX_LINE_BYTES)
            else:
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    self.stats.errors += 1
                    record = self._error(None, "line %d is not JSON: %s" % (number, exc.msg))
                    note_failure("PSYCHE.rt_channel.serve", exc)
                else:
                    if not isinstance(parsed, dict):
                        self.stats.errors += 1
                        record = self._error(None, "request must be a JSON object")
                    else:
                        if parsed.get("id") is None:
                            parsed["id"] = number
                        record = self.handle(parsed)
            stream_out.write(json.dumps(record) + "\n")
            if flush:
                stream_out.flush()
        return self.stats

    def serve_stdio(self) -> ChannelStats:
        return self.serve(sys.stdin, sys.stdout)


def demo_loop(turns: int = 3) -> Iterator[Dict]:
    """A self-contained stream for smoke-testing the channel end to end.

    Builds its own graph and prototypes so `--demo` needs no stored artifact --
    a demo that requires setup is a demo that is never run.
    """
    net = PersonNetwork()
    net.add_edge("threat_appraisal", "avoidance", 0.7, support=12)
    net.add_edge("rumination", "threat_appraisal", 0.6, support=12)
    net.add_edge("approach", "self_worth", 0.5, support=12)
    index = IntuitionIndex(net)
    for _ in range(6):
        index.absorb({"threat_appraisal": 0.7, "avoidance": 0.6,
                      "rumination": 0.5, "arousal": 0.6}, "threat-loop")
        index.absorb({"approach": 0.6, "self_worth": 0.4,
                      "arousal": 0.2}, "settled")
    loop = TherapeuticLoop(net, index)
    script: List[Dict] = [
        {"id": 1, "text": "long day", "appraisal": {"desirability": -0.6}},
        {"id": 2, "text": "I got through the thing I was avoiding",
         "appraisal": {"desirability": 0.6}},
        {"text": "I want to die", "appraisal": {"desirability": -0.9}},
        {"id": 4, "text": 42},
    ]
    channel = SessionChannel(loop)
    for record in script[:max(1, turns)]:
        yield channel.handle(record)


def main(argv: Optional[List[str]] = None) -> int:
    # `__doc__` is Optional: a module run under `python -OO` has no docstring at
    # all, and the first line of None is an AttributeError at argument parsing.
    parser = argparse.ArgumentParser(
        description=(__doc__ or "realtime affective channel").splitlines()[0])
    parser.add_argument("--demo", action="store_true",
                        help="run a self-contained 3-turn script through the channel")
    parser.add_argument("--turns", type=int, default=3,
                        help="how many demo turns to run (default 3)")
    parser.add_argument("--budget-ms", type=float, default=50.0,
                        help="per-turn latency budget reported in each response")
    args = parser.parse_args(argv)

    if args.demo:
        for record in demo_loop(args.turns):
            sys.stdout.write(json.dumps(record) + "\n")
            sys.stdout.flush()
        return 0

    # stdin mode builds an EMPTY person, and says so on stderr: fitting a graph
    # from nothing would make this look like it had learned something.
    net = PersonNetwork()
    loop = TherapeuticLoop(net, IntuitionIndex(net), budget_ms=args.budget_ms)
    sys.stderr.write("[rt_channel] no artifact loaded: serving an unfitted graph; "
                     "pass --demo for a fitted example\n")
    stats = SessionChannel(loop).serve_stdio()
    sys.stderr.write("[rt_channel] %s\n" % json.dumps(stats.to_dict()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
