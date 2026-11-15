"""Give the demo's fictional orgs real Northeastern club names (public list
from engage.northeastern.edu, saved in demo/neu_orgs.json). People stay fake.

Usage: COLLECTIVIZE_DB_PATH=demo/collectivize-demo.db uv run python scripts/neu_org_names.py
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from backend.config import load_config  # noqa: E402
from backend.database import open_graph  # noqa: E402
from backend.objects import Organization, OrgKind  # noqa: E402

NAMES = ROOT / "demo" / "neu_orgs.json"
STOP = {"club", "the", "of", "and", "at", "nu", "society", "demo", "northeastern", "university",
        "association", "student", "students", "team", "group", "a"}


class NeuNamer:
    """Matches each fictional org to the real club whose name shares the most words."""

    def __init__(self, real: list[list[str]]) -> None:
        self.clubs = [n for n, cat in real if cat != "Greek"]
        self.greek = [n for n, cat in real if cat == "Greek" and "Council" not in n]
        self.used: set[str] = set()

    @staticmethod
    def words(name: str) -> set[str]:
        return {w for w in re.findall(r"[a-z]+", name.lower()) if w not in STOP and len(w) > 2}

    def pick(self, org: Organization) -> str | None:
        pool = self.greek if org.kind is OrgKind.CHAPTER else self.clubs
        free = [n for n in pool if n not in self.used]
        if not free:
            return None
        mine = self.words(org.name)
        best = max(free, key=lambda n: (len(mine & self.words(n)), -pool.index(n) * 0))
        if not mine & self.words(best):
            best = free[hash(org.id) % len(free)]
        self.used.add(best)
        return best

    def apply(self, graph) -> int:
        count = 0
        with graph.store.transaction():
            for org in sorted(graph.store.nodes(Organization), key=lambda o: o.id):
                if "[Demo] Northbridge" in org.name:  # the campus and its councils
                    new = org.name.replace("[Demo] Northbridge University", "Northeastern University")
                    new = new.replace("[Demo] Northbridge", "Northeastern")
                    graph.store.save_node(replace(org, name=new))
                    continue
                if org.kind not in (OrgKind.CLUB, OrgKind.CHAPTER) or "[Demo]" not in org.name and org.name in self.used:
                    continue
                name = self.pick(org)
                if name:
                    graph.store.save_node(replace(org, name=name))
                    count += 1
        return count


if __name__ == "__main__":
    with open_graph(load_config()) as g:
        print("renamed", NeuNamer(json.loads(NAMES.read_text(encoding="utf-8"))).apply(g))
