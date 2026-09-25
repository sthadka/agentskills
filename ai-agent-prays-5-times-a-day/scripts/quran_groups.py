"""Quran recitation groups for rakah 1 and rakah 2.

`assets/quran_groups.json` lists the Quran passages recited after Al-Fatiha.
Currently: the 16 short surahs Az-Zalzala (99) to An-Nas (114), one whole surah
per group. A group may also be an ayah range of a longer surah. Ids keep the
author's original full-table numbering, so gaps are expected.
`assets/quran_text.json` holds the Uthmani Arabic and Saheeh International
English for every ayah those groups use.

Rule:
- rakah 1 recites a random group flagged `rakah_1: true`; a group carries that
  flag only when the group with id + 1 also exists in the table
- rakah 2 recites the group with id + 1, even if that starts a new surah
- later fard rakahs recite Al-Fatiha only
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GROUPS = ROOT / "assets" / "quran_groups.json"
DEFAULT_QURAN = ROOT / "assets" / "quran_text.json"

SURAH_WITHOUT_BASMALA = 9  # At-Tawbah has no basmala in the mushaf


@dataclass(frozen=True)
class Group:
    id: int
    surah: int
    start: int
    end: int
    rakah_1: bool

    @property
    def ayah_count(self) -> int:
        return self.end - self.start + 1


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_groups(path: Path = DEFAULT_GROUPS) -> list[Group]:
    raw = load_json(path)["groups"]
    groups = [
        Group(int(g["id"]), int(g["surah"]), int(g["from"]), int(g["to"]), bool(g.get("rakah_1", False)))
        for g in raw
    ]
    if not groups:
        raise ValueError(f"{path}: no groups")
    return groups


def load_quran(path: Path = DEFAULT_QURAN) -> dict[int, dict[str, Any]]:
    surahs = load_json(path)["surahs"]
    return {int(s["number"]): s for s in surahs}


def surah_length(surah: dict[str, Any]) -> int:
    """Total ayahs in the surah (the text asset may hold only a subset of them)."""
    return int(surah.get("ayah_count", len(surah["ayahs"])))


def validate_groups(groups: list[Group], quran: dict[int, dict[str, Any]]) -> None:
    """Ids increase, ranges fit their surahs and do not overlap, and the rakah-1 flag
    is set exactly on groups whose successor (id + 1) exists."""
    ids = {g.id for g in groups}
    if len(ids) != len(groups):
        raise ValueError("duplicate group ids")
    for prev, cur in zip(groups, groups[1:]):
        if cur.id <= prev.id:
            raise ValueError(f"group ids must increase: {prev.id} -> {cur.id}")
        if cur.surah < prev.surah:
            raise ValueError(f"group {cur.id} breaks surah order")
        if cur.surah == prev.surah and cur.start <= prev.end:
            raise ValueError(f"groups {prev.id} and {cur.id} overlap")
    for group in groups:
        if group.surah not in quran:
            raise ValueError(f"group {group.id}: surah {group.surah} missing from the text asset")
        total = surah_length(quran[group.surah])
        if group.start < 1 or group.end < group.start or group.end > total:
            raise ValueError(f"group {group.id}: range {group.start}–{group.end} does not fit surah {group.surah} ({total} ayahs)")
        if len(group_ayahs(group, quran)) != group.ayah_count:
            raise ValueError(f"group {group.id}: text asset lacks some ayahs of {group.surah}:{group.start}–{group.end}")
        has_next = (group.id + 1) in ids
        if group.rakah_1 and not has_next:
            raise ValueError(f"group {group.id} is allowed for rakah 1 but group {group.id + 1} does not exist")
    if not any(g.rakah_1 for g in groups):
        raise ValueError("no group is allowed for rakah 1")


def group_ayahs(group: Group, quran: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    ayahs = quran[group.surah]["ayahs"]
    return [a for a in ayahs if group.start <= int(a["n"]) <= group.end]


def is_whole_surah(group: Group, quran: dict[int, dict[str, Any]]) -> bool:
    return group.start == 1 and group.end == surah_length(quran[group.surah])


def starts_with_basmala(group: Group) -> bool:
    """Recite the basmala only when a surah is begun from ayah 1 (never for At-Tawbah)."""
    return group.start == 1 and group.surah != SURAH_WITHOUT_BASMALA


def group_label(group: Group, quran: dict[int, dict[str, Any]]) -> str:
    surah = quran[group.surah]
    if is_whole_surah(group, quran):
        return f"{surah['name_en']} (whole surah)"
    return f"{surah['name_en']} {group.start}–{group.end}"


def pick_group_pair(groups: list[Group], rng: random.Random, first_id: int | None = None) -> tuple[Group, Group]:
    """Rakah 1: random group flagged for rakah 1. Rakah 2: the group with id + 1."""
    by_id = {g.id: g for g in groups}
    eligible = [g for g in groups if g.rakah_1 and (g.id + 1) in by_id]
    if not eligible:
        raise ValueError("no group is allowed for rakah 1")
    if first_id is None:
        first = rng.choice(eligible)
    else:
        first = by_id.get(first_id)
        if first is None:
            raise ValueError(f"group {first_id} is not in the table")
        if first not in eligible:
            raise ValueError(f"group {first_id} is not allowed for rakah 1 (no group {first_id + 1} to recite in rakah 2)")
    return first, by_id[first.id + 1]
