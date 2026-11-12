# coding: utf-8
"""league.py — arena self-play league: round-robin fixtures + Elo.

Players are named policies (worms/arms/genomes). match_fn(a, b) -> 1.0/0.5/0.0
from A's view. Ratings persist to JSON. Stdlib-only.
"""
import json
import itertools
from typing import List
import itertools as _it

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


K_DEFAULT = 24.0


class League:
    VERSION = "1.1.0"

    def __init__(self, players=None, k: float = K_DEFAULT, path: str = ""):
        self.k = float(k)
        self.path = path
        self.ratings: dict[str, float] = {}
        self.games: dict[str, int] = {}
        for p in players or []:
            self.ratings.setdefault(p, 1500.0)
            self.games.setdefault(p, 0)
        if path:
            self.load(path)

    def expect(self, a: str, b: str) -> float:
        return 1.0 / (1.0 + 10.0 ** ((self.ratings.get(b, 1500.0)
                                      - self.ratings.get(a, 1500.0)) / 400.0))

    def record(self, a: str, b: str, score_a: float) -> dict:
        """Record one game. score_a in {1.0 win, 0.5 draw, 0.0 loss}."""
        for p in (a, b):
            self.ratings.setdefault(p, 1500.0)
            self.games.setdefault(p, 0)
        ea, eb = self.expect(a, b), self.expect(b, a)
        da = self.k * (score_a - ea)
        db = self.k * ((1.0 - score_a) - eb)
        self.ratings[a] += da
        self.ratings[b] += db
        self.games[a] += 1
        self.games[b] += 1
        if not hasattr(self, "_vel"):
            self._vel = {}
        self._vel.setdefault(a, []).append(da)
        self._vel.setdefault(b, []).append(db)
        if not hasattr(self, "_pairs"):
            self._pairs = {}
        key = (a, b) if a < b else (b, a)
        s = score_a if a < b else 1.0 - score_a
        prev = self._pairs.get(key, [0.0, 0])
        self._pairs[key] = [prev[0] + s, prev[1] + 1]
        if self.path:
            self.save(self.path)
        return {"a": round(self.ratings[a], 1), "b": round(self.ratings[b], 1)}

    def round_robin(self, players, match_fn) -> list[dict]:
        """Every pair meets once. match_fn(a, b) -> score for a."""
        results = []
        for a, b in itertools.combinations(players, 2):
            try:
                s = float(match_fn(a, b))
            except Exception as exc:
                note_failure("league:L64", exc)
                s = 0.5
            s = min(1.0, max(0.0, s))
            r = self.record(a, b, s)
            results.append({"a": a, "b": b, "score_a": s, **r})
        return results

    def standings(self) -> list[tuple]:
        return sorted(self.ratings.items(), key=lambda t: -t[1])

    def velocity(self, name: str, window: int = 10) -> float:
        """Elo slope over recent games — the endogenous RL reward."""
        buf = getattr(self, "_vel", {}).get(name, [])
        if not buf:
            return 0.0
        return sum(buf[-window:]) / min(window, len(buf))

    def transitivity(self, players) -> float:
        """RPS-cycle guard: fraction of triples that are transitive.

        1.0 = total order (safe for Elo-as-reward). Near 0.5 = soup:
        Elo deltas are noise, fall back to hand rewards.
        """
        players = list(players)
        if len(players) < 3:
            return 1.0
        beats = {p: set() for p in players}
        for (a, b), (s, n) in getattr(self, "_pairs", {}).items():
            if a in beats and b in beats and n > 0:
                avg = s / n
                if avg > 0.55:
                    beats[a].add(b)
                elif avg < 0.45:
                    beats[b].add(a)
        tot, trans = 0, 0
        for a, b, c in _it.combinations(players, 3):
            tot += 1
            wins = {p: 0 for p in (a, b, c)}
            for x, y in ((a, b), (b, c), (a, c)):
                if y in beats.get(x, ()):
                    wins[x] += 1
                elif x in beats.get(y, ()):
                    wins[y] += 1
            if sorted(wins.values()) == [0, 1, 2]:
                trans += 1
        return trans / max(1, tot)

    def to_rewards(self, group: list[str]) -> list[float] | None:
        """Elo-velocity rewards for a GRPO group, None if cyclic (<0.75)."""
        if self.transitivity(group) < 0.75:
            return None
        return [self.velocity(p) for p in group]

    def save(self, path: str = ""):
        p = path or self.path
        if not p:
            return
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"ratings": self.ratings, "games": self.games}, fh, indent=1)

    def load(self, path: str):
        try:
            d = json.load(open(path, encoding="utf-8"))
            self.ratings.update({k: float(v) for k, v in d.get("ratings", {}).items()})
            self.games.update({k: int(v) for k, v in d.get("games", {}).items()})
        except Exception as exc:
            note_failure("league:L130", exc)
