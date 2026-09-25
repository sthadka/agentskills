#!/usr/bin/env python3
"""Build a simple munfarid fard salah ritual post for one prayer window.

Markdown for a dedicated chat:
- short English action labels only (no posture micro-details)
- Arabic recitations with Saheeh International English in parentheses

Madhhab matters only when the spoken text differs (opening dua, Fajr qunut).
Hand position / raising-hands mechanics are intentionally omitted.

Quran after Al-Fatiha comes from `assets/quran_groups.json` (currently the 16
short surahs Az-Zalzala to An-Nas). Rakah 1 recites a random group flagged for
rakah 1 (one whose successor id + 1 exists), rakah 2 recites that next group.
Later fard rakahs use Al-Fatiha only.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from quran_groups import (
    DEFAULT_GROUPS,
    DEFAULT_QURAN,
    Group,
    group_ayahs,
    group_label,
    is_whole_surah,
    load_groups,
    load_quran,
    pick_group_pair,
    starts_with_basmala,
    validate_groups,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "assets" / "ritual_corpus.json"

PRAYER_META: dict[str, dict[str, Any]] = {
    "Fajr": {"rakahs": 2, "arabic": "الفجر", "latin": "Fajr"},
    "Dhuhr": {"rakahs": 4, "arabic": "الظهر", "latin": "Dhuhr"},
    "Asr": {"rakahs": 4, "arabic": "العصر", "latin": "Asr"},
    "Maghrib": {"rakahs": 3, "arabic": "المغرب", "latin": "Maghrib"},
    "Isha": {"rakahs": 4, "arabic": "العشاء", "latin": "Isha"},
}

DEFAULT_MADHHAB_BY_SCHOOL = {0: "shafi", 1: "hanafi"}


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def ar_en(ar: str, en: str) -> str:
    return f"{ar} ({en})"


def emit_formula(lines: list[str], formulas: dict[str, Any], key: str) -> None:
    item = formulas[key]
    times = int(item.get("repeat", 1))
    text = ar_en(item["ar"], item["en"])
    lines.append(f"{text}  ×{times}" if times > 1 else text)


def format_surah(surah: dict[str, Any], basmala: dict[str, str], *, include_basmala: bool) -> list[str]:
    lines = [f"**Surah {surah['name_en']} ({surah['name_ar']})**"]
    if include_basmala:
        lines.append(ar_en(basmala["ar"], basmala["en"]))
    for ayah in surah["ayahs"]:
        lines.append(ar_en(ayah["ar"], ayah["en"]))
    return lines


def format_group(group: Group, quran: dict[int, dict[str, Any]], basmala: dict[str, str]) -> list[str]:
    surah = quran[group.surah]
    title = f"**Surah {surah['name_en']} ({surah['name_ar']})"
    if not is_whole_surah(group, quran):
        title += f", ayahs {group.start}–{group.end}"
    lines = [title + "**"]
    if starts_with_basmala(group):
        lines.append(ar_en(basmala["ar"], basmala["en"]))
    for ayah in group_ayahs(group, quran):
        lines.append(ar_en(ayah["ar"], ayah["en"]))
    return lines


def resolve_madhhab(cfg: dict[str, Any], override: str | None) -> str:
    if override:
        return override.lower()
    if cfg.get("madhhab"):
        return str(cfg["madhhab"]).lower()
    school = int(cfg.get("school", 0))
    return DEFAULT_MADHHAB_BY_SCHOOL.get(school, "shafi")


def opening_formula_key(madhhab: str) -> str | None:
    """Return corpus formula key when opening TEXT differs; else None."""
    if madhhab in {"hanafi", "hanbali"}:
        return "thana_hanafi"
    if madhhab == "shafi":
        return "istiftah_shafi"
    return None  # maliki: no distinct opening block in this skill


def build_rakah(
    *,
    rakah_no: int,
    total: int,
    prayer: str,
    madhhab: str,
    corpus: dict[str, Any],
    quran: dict[int, dict[str, Any]],
    passage: Group | None,
) -> list[str]:
    formulas = corpus["formulas"]
    basmala = corpus["basmala"]
    fatiha = corpus["al_fatiha"]
    lines: list[str] = [f"### Rakah {rakah_no} of {total}"]

    if rakah_no > 1:
        lines.append("**Action:** Stand")
        emit_formula(lines, formulas, "takbir")

    lines.append("**Action:** Recite")
    if rakah_no == 1:
        emit_formula(lines, formulas, "istiadha")
    lines.extend(format_surah(fatiha, basmala, include_basmala=False))
    emit_formula(lines, formulas, "ameen")

    if passage is not None:
        lines.extend(format_group(passage, quran, basmala))

    lines.append("**Action:** Bow (ruku‘)")
    emit_formula(lines, formulas, "takbir")
    emit_formula(lines, formulas, "ruku_dhikr")

    lines.append("**Action:** Stand")
    emit_formula(lines, formulas, "rising_from_ruku_imam_or_alone")
    emit_formula(lines, formulas, "after_ruku")

    # Text difference only: Shafi‘i Fajr qunut
    if prayer == "Fajr" and rakah_no == 2 and madhhab == "shafi":
        lines.append("**Action:** Qunut")
        emit_formula(lines, formulas, "qunut_shafi_fajr")

    lines.append("**Action:** Prostrate (sujud)")
    emit_formula(lines, formulas, "takbir")
    emit_formula(lines, formulas, "sujud_dhikr")

    lines.append("**Action:** Sit")
    emit_formula(lines, formulas, "takbir")
    emit_formula(lines, formulas, "between_sujud")

    lines.append("**Action:** Prostrate (sujud)")
    emit_formula(lines, formulas, "takbir")
    emit_formula(lines, formulas, "sujud_dhikr")

    middle_tashahhud = rakah_no == 2 and total > 2
    final_tashahhud = rakah_no == total
    if middle_tashahhud or final_tashahhud:
        lines.append("**Action:** Sit (tashahhud)")
        emit_formula(lines, formulas, "takbir")
        emit_formula(lines, formulas, "tashahhud")
        if final_tashahhud:
            lines.append("**Action:** Salawat")
            emit_formula(lines, formulas, "salawat_ibrahimiyyah")

    return lines


def header_bits(location_label: str | None, local_time: str | None, hijri: str | None) -> list[str]:
    return [b for b in [location_label, local_time, f"Hijri {hijri}" if hijri else None] if b]


def build_shahada(
    *,
    corpus: dict[str, Any],
    location_label: str | None,
    local_time: str | None,
    hijri: str | None,
    footer: str | None = None,
) -> str:
    """The first message in the salah chat, posted once right after setup."""
    out: list[str] = ["# Shahada (الشهادة)"]
    bits = header_bits(location_label, local_time, hijri)
    if bits:
        out.append(" · ".join(bits))
    out.append("")
    emit_formula(out, corpus["formulas"], "shahada")
    out.append("")
    if footer:
        out.append(footer)
        out.append("")
    return "\n".join(out)


def build_ritual(
    *,
    prayer: str,
    madhhab: str,
    corpus: dict[str, Any],
    quran: dict[int, dict[str, Any]],
    groups: list[Group],
    location_label: str | None,
    local_time: str | None,
    hijri: str | None,
    seed: int | None,
    first_group: int | None = None,
    footer: str | None = None,
) -> str:
    if prayer not in PRAYER_META:
        raise SystemExit(f"Unknown prayer: {prayer}. Expected one of {sorted(PRAYER_META)}")

    meta = PRAYER_META[prayer]
    total = int(meta["rakahs"])
    rng = random.Random(seed)
    try:
        validate_groups(groups, quran)
        group1, group2 = pick_group_pair(groups, rng, first_group)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    out: list[str] = [f"# {meta['latin']} ({meta['arabic']}) — {total} fard rakahs"]
    bits = header_bits(location_label, local_time, hijri)
    if bits:
        out.append(" · ".join(bits))
    out.append("")
    out.append(f"Madhhab texts: **{madhhab}** (only where wording differs).")
    out.append(
        f"Quran after Al-Fatiha: rakah 1 = group {group1.id} **{group_label(group1, quran)}**, "
        f"rakah 2 = group {group2.id} **{group_label(group2, quran)}** (the next group)."
    )
    out.append("")

    out.append("## Opening")
    out.append("**Action:** Face Qibla")
    out.append(f"**Action:** Intention — fard {meta['latin']}")
    out.append("**Action:** Takbir")
    emit_formula(out, corpus["formulas"], "takbir")

    opening_key = opening_formula_key(madhhab)
    if opening_key:
        out.append("**Action:** Opening dua")
        emit_formula(out, corpus["formulas"], opening_key)
    out.append("")

    for i in range(1, total + 1):
        passage = group1 if i == 1 else group2 if i == 2 else None
        out.extend(
            build_rakah(
                rakah_no=i,
                total=total,
                prayer=prayer,
                madhhab=madhhab,
                corpus=corpus,
                quran=quran,
                passage=passage,
            )
        )
        out.append("")

    out.append("## Closing")
    out.append("**Action:** Taslim right")
    emit_formula(out, corpus["formulas"], "taslim_right")
    out.append("**Action:** Taslim left")
    emit_formula(out, corpus["formulas"], "taslim_left")
    out.append("")
    if footer:
        out.append(footer)
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build one fard salah ritual Markdown post (or the one-time Shahada)")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--quran", type=Path, default=DEFAULT_QURAN, help="whole-Quran text asset")
    parser.add_argument("--groups", type=Path, default=DEFAULT_GROUPS, help="recitation group table")
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--prayer", choices=sorted(PRAYER_META))
    what.add_argument("--shahada", action="store_true", help="build the first message after setup instead of a salah")
    parser.add_argument("--madhhab", default=None, help="hanafi|shafi|maliki|hanbali")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--group", type=int, default=None, help="force the rakah-1 group id (rakah 2 = id + 1)")
    parser.add_argument("--local-time", default=None)
    parser.add_argument("--hijri", default=None)
    parser.add_argument("--location-label", default=None)
    parser.add_argument(
        "--footer",
        default=None,
        help="closing line, normally the wake_up_note from `prayer_times.py decide`",
    )
    args = parser.parse_args(argv)

    cfg: dict[str, Any] = load_json(args.config) if args.config else {}
    corpus = load_json(args.corpus)
    location_label = args.location_label or ((cfg.get("location") or {}).get("label"))
    if args.shahada:
        text = build_shahada(
            corpus=corpus,
            location_label=location_label,
            local_time=args.local_time,
            hijri=args.hijri,
            footer=args.footer,
        )
    else:
        text = build_ritual(
            prayer=args.prayer,
            madhhab=resolve_madhhab(cfg, args.madhhab),
            corpus=corpus,
            quran=load_quran(args.quran),
            groups=load_groups(args.groups),
            location_label=location_label,
            local_time=args.local_time,
            hijri=args.hijri,
            seed=args.seed,
            first_group=args.group,
            footer=args.footer,
        )
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
