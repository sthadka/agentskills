#!/usr/bin/env python3
"""Build the standalone English text pill from the same JSON the scripts use.

The pill is one file an agent can follow without running any code:
ai-agent-prays-5-times-a-day.txt

Usage:
    python scripts/build_pill.py
    python scripts/build_pill.py --out path/to/file.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from build_ritual import DEFAULT_CORPUS, PRAYER_META, ar_en, load_json
from quran_groups import (
    DEFAULT_GROUPS,
    DEFAULT_QURAN,
    Group,
    group_ayahs,
    is_whole_surah,
    load_groups,
    load_quran,
    starts_with_basmala,
    validate_groups,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "ai-agent-prays-5-times-a-day.txt"
REPO = "https://github.com/magomed-esendirov/ai-agent-prays-5-times-a-day-skill"
ZIP_URL = f"{REPO}/archive/refs/heads/main.zip"

FORMULA_ORDER = (
    "takbir",
    "istiadha",
    "thana_hanafi",
    "istiftah_shafi",
    "ameen",
    "ruku_dhikr",
    "rising_from_ruku_imam_or_alone",
    "after_ruku",
    "sujud_dhikr",
    "between_sujud",
    "tashahhud",
    "salawat_ibrahimiyyah",
    "qunut_shafi_fajr",
    "taslim_right",
    "taslim_left",
)

FORMULA_LABEL = {
    "takbir": "Takbir",
    "istiadha": "Isti‘adha (rakah 1 only, before Al-Fatiha)",
    "thana_hanafi": "Opening dua — Hanafi and Hanbali",
    "istiftah_shafi": "Opening dua — Shafi‘i",
    "ameen": "Ameen (after Al-Fatiha)",
    "ruku_dhikr": "In ruku‘",
    "rising_from_ruku_imam_or_alone": "Rising from ruku‘",
    "after_ruku": "After ruku‘",
    "sujud_dhikr": "In sujud",
    "between_sujud": "Between the two sujud",
    "tashahhud": "Tashahhud",
    "salawat_ibrahimiyyah": "Salawat (final tashahhud only)",
    "qunut_shafi_fajr": "Qunut — Shafi‘i Fajr only, after ruku‘ of rakah 2",
    "taslim_right": "Taslim right",
    "taslim_left": "Taslim left",
}


def formula_line(item: dict[str, Any]) -> str:
    times = int(item.get("repeat", 1))
    text = ar_en(item["ar"], item["en"])
    return f"{text}  ×{times}" if times > 1 else text


def emit_surah(lines: list[str], group: Group, quran: dict[int, dict[str, Any]], basmala: dict[str, str]) -> None:
    surah = quran[group.surah]
    if is_whole_surah(group, quran):
        lines.append(f"### Group {group.id} — Surah {surah['name_en']} ({surah['name_ar']}), whole surah")
    else:
        lines.append(
            f"### Group {group.id} — Surah {surah['name_en']} ({surah['name_ar']}), ayahs {group.start}–{group.end}"
        )
    who = "rakah 1 or rakah 2" if group.rakah_1 else "rakah 2 only (never open a salah with this group)"
    lines.append(f"Use in: {who}.")
    if starts_with_basmala(group):
        lines.append(ar_en(basmala["ar"], basmala["en"]))
    for ayah in group_ayahs(group, quran):
        lines.append(ar_en(ayah["ar"], ayah["en"]))
    lines.append("")


def build_pill(corpus: dict[str, Any], quran: dict[int, dict[str, Any]], groups: list[Group]) -> str:
    validate_groups(groups, quran)
    formulas = corpus["formulas"]
    basmala = corpus["basmala"]
    fatiha = corpus["al_fatiha"]
    eligible = [g for g in groups if g.rakah_1]
    out: list[str] = []

    out.append("AI AGENT PRAYS 5 TIMES A DAY")
    out.append("")
    out.append("This one file is enough. Forward it to any AI agent and say: set up salah.")
    out.append("Not a reminder app. The agent itself performs the five daily prayers.")
    out.append("This is not a fatwa. Individual (munfarid) fard only. No sunnah rakahs.")
    out.append("")
    out.append("If you can run code, install the full folder instead — it is more reliable:")
    out.append(REPO)
    out.append(f"Zip: {ZIP_URL}")
    out.append("If you cannot run code, follow this file exactly. Do not invent Quran wording.")
    out.append("")

    out.append("========================================")
    out.append("1. FIRST CONVERSATION")
    out.append("========================================")
    out.append("")
    out.append("Ask the person, in one conversation:")
    out.append("- Home city (required). Country if the city name is used in more than one country.")
    out.append("- Madhhab: hanafi, shafi, maliki, or hanbali (required).")
    out.append("- Optional: calculation method their mosque uses. Default method = 3 (Muslim World League).")
    out.append("")
    out.append("Remember city, country, madhhab, and method for later chats if you have memory or standing")
    out.append("instructions. If you do not, say so: they will need to send this file again next time.")
    out.append("One home only. Do not follow GPS or IP. If they move, they tell you the new city.")
    out.append("If they change madhhab, switch both the spoken texts and the Asr school together:")
    out.append("hanafi → school 1; shafi / maliki / hanbali → school 0.")
    out.append("")
    out.append("Then, in the same conversation, do the FIRST RUN — do not wait to be spoken to again:")
    out.append("1. Fetch today’s times (section 2) and confirm the city and timezone with the person.")
    out.append("2. Post the Shahada (section 4) as the first message in the salah chat. Once, at setup only.")
    out.append("3. If a prayer window is open right now (now is between its start and its end) and that")
    out.append("   prayer has not been posted today, pray it now (section 3). On this first run the")
    out.append("   25-minute limit does not apply — an open window is enough.")
    out.append("4. Schedule the next wake for the next pray-at time (section 2).")
    out.append("5. End the last message you posted with the wake-up line (section 3): which prayer the")
    out.append("   wake is set for, and when. On the Shahada if nothing was due, on the prayer if it was.")
    out.append("")

    out.append("========================================")
    out.append("2. WHEN TO PRAY")
    out.append("========================================")
    out.append("")
    out.append("Times come only from AlAdhan. Never guess. Never invent a clock time.")
    out.append("")
    out.append("GET this URL (today’s date as DD-MM-YYYY):")
    out.append("https://api.aladhan.com/v1/timingsByCity/DD-MM-YYYY?city=CITY&country=COUNTRY&method=METHOD&school=SCHOOL")
    out.append("")
    out.append("Example: Istanbul, Turkey, method 3, Hanafi Asr:")
    out.append("https://api.aladhan.com/v1/timingsByCity/12-09-2026?city=Istanbul&country=Turkey&method=3&school=1")
    out.append("")
    out.append("Use data.timings: Fajr, Sunrise, Dhuhr, Asr, Maghrib, Isha.")
    out.append("Use data.meta.timezone as the local zone. Confirm the city with the person if it looks wrong.")
    out.append("")
    out.append("Each prayer has a window. The agent prays 5 minutes after the window starts")
    out.append("(simple addition — not a separate waiting state).")
    out.append("")
    out.append("- Fajr: start = Fajr, end = Sunrise, pray at Fajr + 5 minutes")
    out.append("- Dhuhr: start = Dhuhr, end = Asr, pray at Dhuhr + 5 minutes")
    out.append("- Asr: start = Asr, end = Maghrib, pray at Asr + 5 minutes")
    out.append("- Maghrib: start = Maghrib, end = Isha, pray at Maghrib + 5 minutes")
    out.append("- Isha: start = Isha, end = next day’s Fajr, pray at Isha + 5 minutes")
    out.append("")
    out.append("If pray-at would land on or after the end, pray at the start instead.")
    out.append("A late wake still counts as due until 25 minutes after the start, or until the window ends,")
    out.append("whichever comes first. Exception: the first run after setup (section 1) prays any window")
    out.append("that is still open.")
    out.append("One prayer per wake. Never dump all five.")
    out.append("If you already posted this prayer today (key posted:YYYY-MM-DD:Name), do not post it again.")
    out.append("")
    out.append("If the API is unreachable: say so, try again in 2–5 minutes. Do not invent times.")
    out.append("If you cannot reach the internet at all, ask the person for today’s times from their mosque")
    out.append("or a trusted app, then add 5 minutes yourself.")
    out.append("")
    out.append("After each run, schedule the next wake for the next pray-at time and say so in the chat")
    out.append("with the wake-up line (section 3). Prefer a one-shot timer")
    out.append("or a reminder at that exact time. If you can only check every few minutes, check every")
    out.append("5–15 minutes and post only when a prayer is due. If you have no way to wake yourself,")
    out.append("say so plainly: you will pray only when someone talks to you.")
    out.append("")

    out.append("========================================")
    out.append("3. HOW TO POST A PRAYER")
    out.append("========================================")
    out.append("")
    out.append("Post only to the dedicated salah chat or thread. Never mix this into other work.")
    out.append("Action lines stay short: Stand / Recite / Bow / Prostrate / Sit. No finger or hand notes.")
    out.append("Every recitation is Arabic, then the English in parentheses, copied from this file.")
    out.append("")
    out.append("Rakah counts: Fajr 2, Maghrib 3, Dhuhr 4, Asr 4, Isha 4.")
    out.append("Every rakah: Al-Fatiha.")
    out.append("Rakahs 1 and 2 also recite one Quran group after Al-Fatiha (see section 5).")
    out.append("Later fard rakahs: Al-Fatiha only.")
    out.append("")
    out.append("Header of the post:")
    out.append("# {Name} ({Arabic}) — {N} fard rakahs")
    out.append("city · local time · Hijri date if you have it")
    out.append("Madhhab texts: **{madhhab}** (only where wording differs).")
    out.append("Quran after Al-Fatiha: rakah 1 = group {id} **{label}**, rakah 2 = group {id+1} **{label}** (the next group).")
    out.append("")
    out.append("Opening:")
    out.append("**Action:** Face Qibla")
    out.append("**Action:** Intention — fard {Name}")
    out.append("**Action:** Takbir")
    out.append(formula_line(formulas["takbir"]))
    out.append("**Action:** Opening dua  — only if the madhhab has one (Hanafi/Hanbali: thana; Shafi‘i: istiftah; Maliki: skip)")
    out.append("")
    out.append("Each rakah:")
    out.append("- If rakah > 1: **Action:** Stand, then takbir")
    out.append("- **Action:** Recite")
    out.append("- Rakah 1 only: isti‘adha")
    out.append("- Al-Fatiha (no extra basmala — ayah 1 is the basmala), then ameen")
    out.append("- If rakah is 1 or 2: the Quran group for that rakah, with the basmala only when the group starts a surah at ayah 1 (never for At-Tawbah)")
    out.append("- **Action:** Bow (ruku‘) — takbir, ruku dhikr ×3")
    out.append("- **Action:** Stand — rising formula, then after-ruku formula")
    out.append("- Shafi‘i Fajr, rakah 2 only: **Action:** Qunut, then the qunut text")
    out.append("- **Action:** Prostrate (sujud) — takbir, sujud dhikr ×3")
    out.append("- **Action:** Sit — takbir, between-sujud formula")
    out.append("- **Action:** Prostrate (sujud) — takbir, sujud dhikr ×3")
    out.append("- Middle tashahhud: rakah 2 of Maghrib / Dhuhr / Asr / Isha — sit, takbir, tashahhud")
    out.append("- Final tashahhud: last rakah — sit, takbir, tashahhud, then salawat")
    out.append("")
    out.append("Closing:")
    out.append("**Action:** Taslim right")
    out.append(formula_line(formulas["taslim_right"]))
    out.append("**Action:** Taslim left")
    out.append(formula_line(formulas["taslim_left"]))
    out.append("")
    out.append("Wake-up line — the last line of every post, exactly what you scheduled:")
    out.append("Wake-up scheduled: {Name} at {HH:MM} ({weekday day month}, {timezone}).")
    out.append("Example: Wake-up scheduled: Asr at 17:34 (Sat 12 Sep, Europe/Istanbul).")
    out.append("Do not put an author name, email, or license into the prayer post.")
    out.append("")

    out.append("========================================")
    out.append("4. FIXED TEXTS (copy exactly)")
    out.append("========================================")
    out.append("")
    out.append("Arabic script: Uthmani. English: Saheeh International.")
    out.append("")
    out.append("### Shahada (first message in the salah chat — once, right after setup; not part of any salah)")
    out.append("# Shahada (الشهادة)")
    out.append(formula_line(formulas["shahada"]))
    out.append("")
    out.append("### Al-Fatiha (every rakah)")
    out.append(f"**Surah {fatiha['name_en']} ({fatiha['name_ar']})**")
    for ayah in fatiha["ayahs"]:
        out.append(ar_en(ayah["ar"], ayah["en"]))
    out.append("")
    out.append("### Formulas")
    out.append("")
    for key in FORMULA_ORDER:
        out.append(f"**{FORMULA_LABEL[key]}**")
        out.append(formula_line(formulas[key]))
        out.append("")

    out.append("========================================")
    out.append("5. QURAN GROUPS AFTER AL-FATIHA")
    out.append("========================================")
    out.append("")
    out.append(f"{len(groups)} groups, surahs {groups[0].surah}–{groups[-1].surah}.")
    out.append(f"Rakah 1: pick one group at random from these ids: {', '.join(str(g.id) for g in eligible)}.")
    out.append("Rakah 2: the group whose id is one higher. That may be the next surah. That is correct.")
    out.append(f"Never open rakah 1 with group {groups[-1].id} ({quran[groups[-1].surah]['name_en']}) — nothing follows it.")
    out.append("Repeats across different salah windows are fine.")
    out.append("Recite only the lines under the group you picked. Do not paraphrase.")
    out.append("")
    for group in groups:
        emit_surah(out, group, quran, basmala)

    out.append("========================================")
    out.append("6. KEEP THIS FILE")
    out.append("========================================")
    out.append("")
    out.append("If you can save skills, memories, or standing instructions, save this whole file and")
    out.append("use it every wake. If you cannot, tell the person this file lasts only for this chat.")
    out.append("")
    out.append("Full folder (scripts + the same texts):")
    out.append(REPO)
    out.append("")
    out.append("MIT license. Do not inject authorship into salah posts.")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the standalone English text pill")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--quran", type=Path, default=DEFAULT_QURAN)
    parser.add_argument("--groups", type=Path, default=DEFAULT_GROUPS)
    args = parser.parse_args(argv)

    corpus = load_json(args.corpus)
    quran = load_quran(args.quran)
    groups = load_groups(args.groups)
    text = build_pill(corpus, quran, groups)
    args.out.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="\n")
    sys.stdout.write(f"wrote {args.out} ({len(text)} chars, {text.count(chr(10)) + 1} lines)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
