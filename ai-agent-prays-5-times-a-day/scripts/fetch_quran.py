#!/usr/bin/env python3
"""Rebuild assets/quran_text.json from alquran.cloud for the groups in use.

Sources (same as the original 8-surah corpus):
- Arabic: quran-uthmani
- English: en.sahih (Saheeh International)

Only the ayahs referenced by `assets/quran_groups.json` are written; every surah
entry still records its true `ayah_count` so range checks stay exact.

alquran.cloud prefixes the basmala to ayah 1 of every surah except Al-Fatiha
(where it *is* ayah 1) and At-Tawbah. That prefix is stripped here; the ritual
builder recites the basmala from `ritual_corpus.json` when a surah is begun.

Usage:
    python scripts/fetch_quran.py                # download, write, verify groups
    python scripts/fetch_quran.py --cache-dir X  # reuse raw API downloads in X
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any

from quran_groups import DEFAULT_GROUPS, DEFAULT_QURAN, Group, load_groups, validate_groups

API = "https://api.alquran.cloud/v1/quran/{edition}"
ARABIC_EDITION = "quran-uthmani"
ENGLISH_EDITION = "en.sahih"
SURAH_FATIHA = 1
SURAH_TAWBAH = 9


def fetch_edition(edition: str, cache_dir: Path | None) -> dict[str, Any]:
    cache = cache_dir / f"{edition}.json" if cache_dir else None
    if cache and cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    req = urllib.request.Request(API.format(edition=edition), headers={"User-Agent": "ai-agent-prays-5-times-a-day"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read().decode("utf-8")
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(body, encoding="utf-8")
    return json.loads(body)


def is_mark(char: str) -> bool:
    return unicodedata.category(char) == "Mn"


def skeleton(text: str) -> str:
    """Letters only: drop harakat/tajwid marks so diacritic variants still match."""
    return "".join(c for c in text if not is_mark(c))


def strip_basmala_prefix(text: str, basmala: str) -> str:
    """Remove a leading basmala, tolerating different diacritic marks on it.

    A few surahs (e.g. 95, 97) carry an extra shadda on the first letter in this
    edition, so the prefix is matched on base letters and cut after the last
    basmala letter plus any marks attached to it.
    """
    target = skeleton(basmala)
    consumed = 0
    for idx, char in enumerate(text):
        if is_mark(char):
            continue
        consumed += 1
        if consumed == len(target):
            end = idx + 1
            while end < len(text) and is_mark(text[end]):
                end += 1
            if skeleton(text[:end]) != target:
                break
            return text[end:].strip()
    raise SystemExit(f"expected a leading basmala in {text[:40]!r}")


def clean_arabic(surah_no: int, ayah_no: int, text: str, basmala: str) -> str:
    text = text.lstrip("\ufeff").strip()
    if ayah_no == 1 and surah_no not in (SURAH_FATIHA, SURAH_TAWBAH):
        text = strip_basmala_prefix(text, basmala)
    return text


def merge(arabic: dict[str, Any], english: dict[str, Any]) -> list[dict[str, Any]]:
    ar_surahs = arabic["data"]["surahs"]
    en_surahs = english["data"]["surahs"]
    if len(ar_surahs) != len(en_surahs):
        raise SystemExit("editions disagree on surah count")
    # Al-Fatiha 1:1 *is* the basmala in this edition; use it verbatim so diacritic
    # ordering matches the prefix glued onto ayah 1 of the other surahs.
    basmala = ar_surahs[0]["ayahs"][0]["text"].lstrip("\ufeff").strip()
    out: list[dict[str, Any]] = []
    for ar_s, en_s in zip(ar_surahs, en_surahs):
        number = int(ar_s["number"])
        if number != int(en_s["number"]) or len(ar_s["ayahs"]) != len(en_s["ayahs"]):
            raise SystemExit(f"editions disagree on surah {number}")
        ayahs = []
        for ar_a, en_a in zip(ar_s["ayahs"], en_s["ayahs"]):
            n = int(ar_a["numberInSurah"])
            if n != int(en_a["numberInSurah"]):
                raise SystemExit(f"ayah mismatch in surah {number}")
            ayahs.append({"n": n, "ar": clean_arabic(number, n, ar_a["text"], basmala), "en": en_a["text"].strip()})
        out.append(
            {
                "number": number,
                "name_ar": ar_s["name"],
                "name_en": ar_s["englishName"],
                "name_translation": ar_s["englishNameTranslation"],
                "ayah_count": len(ayahs),
                "ayahs": ayahs,
            }
        )
    return out


def restrict_to_groups(surahs: list[dict[str, Any]], groups: list[Group]) -> list[dict[str, Any]]:
    """Keep only surahs and ayahs that some group recites; `ayah_count` stays the true surah length."""
    wanted: dict[int, set[int]] = {}
    for g in groups:
        wanted.setdefault(g.surah, set()).update(range(g.start, g.end + 1))
    out: list[dict[str, Any]] = []
    for surah in surahs:
        numbers = wanted.get(surah["number"])
        if not numbers:
            continue
        out.append({**surah, "ayahs": [a for a in surah["ayahs"] if a["n"] in numbers]})
    return out


def write_quran(path: Path, surahs: list[dict[str, Any]], english: dict[str, Any]) -> None:
    edition = english["data"]["edition"]
    payload = {
        "meta": {
            "arabic_script": f"Uthmani ({ARABIC_EDITION} via alquran.cloud)",
            "quran_translation": f"{edition['name']} ({ENGLISH_EDITION} via alquran.cloud)",
            "basmala": "Removed from ayah 1 of every surah except Al-Fatiha; recite it from ritual_corpus.json when starting a surah.",
            "scope": "Only the ayahs used by assets/quran_groups.json; ayah_count per surah is the full surah length.",
            "surah_count": len(surahs),
            "ayah_count": sum(len(s["ayahs"]) for s in surahs),
            "generator": "scripts/fetch_quran.py",
        },
        "surahs": surahs,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the whole-Quran text asset and verify groups")
    parser.add_argument("--out", type=Path, default=DEFAULT_QURAN)
    parser.add_argument("--groups", type=Path, default=DEFAULT_GROUPS)
    parser.add_argument("--cache-dir", type=Path, default=None, help="reuse/keep raw API downloads here")
    args = parser.parse_args(argv)

    arabic = fetch_edition(ARABIC_EDITION, args.cache_dir)
    english = fetch_edition(ENGLISH_EDITION, args.cache_dir)
    groups = load_groups(args.groups)
    surahs = restrict_to_groups(merge(arabic, english), groups)

    quran = {s["number"]: s for s in surahs}
    validate_groups(groups, quran)
    write_quran(args.out, surahs, english)

    kept_ayahs = sum(len(s["ayahs"]) for s in surahs)
    eligible = sum(1 for g in groups if g.rakah_1)
    sys.stdout.write(
        f"wrote {args.out} ({len(surahs)} surahs, {kept_ayahs} ayahs); "
        f"{len(groups)} groups ({eligible} allowed for rakah 1), surahs {groups[0].surah}-{groups[-1].surah}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
