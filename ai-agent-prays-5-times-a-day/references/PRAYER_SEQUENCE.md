# Prayer ritual sequence

Educational scaffold for an **individual (munfarid) fard** salah post.
Not a fatwa.

```bash
python scripts/build_ritual.py \
  --config /path/to/config.json \
  --prayer Dhuhr
```

Optional: `--madhhab hanafi|shafi|maliki|hanbali`, `--seed N`, `--group N` (force the rakah-1 group), `--local-time`, `--hijri`, `--footer "<wake_up_note>"` (closing line from `decide`).

`--shahada` instead of `--prayer` builds the one-time first message: the Shahada, said once right after setup. It is not a salah and has no rakahs.

## Message format

1. Header: prayer name + fard rakah count (+ location/time if known)
2. Which Quran group was drawn for rakah 1, and the next group for rakah 2
3. Ordered steps:
   - **Action:** short English label only (`Stand`, `Recite`, `Bow`, `Prostrate`, `Sit`…)
   - Arabic line **(Saheeh International English)**
4. Last line: the `wake_up_note` from `decide` — which prayer the timer is armed for and when

Do **not** narrate posture micro-details (hand height, finger position, etc.).

## Fard rakah counts

| Prayer | Fard rakahs | Quran group after Al-Fatiha |
|--------|-------------|-----------------------------|
| Fajr | 2 | rakah 1 and 2 |
| Dhuhr | 4 | rakah 1 and 2 only |
| Asr | 4 | rakah 1 and 2 only |
| Maghrib | 3 | rakah 1 and 2 only |
| Isha | 4 | rakah 1 and 2 only |

Later fard rakahs: **Al-Fatiha only**.

## Quran groups

`assets/quran_groups.json` holds **16 groups = 16 whole short surahs**, Az-Zalzala (99) through An-Nas (114). Ids 527–542 keep the numbering of the author's full Quran table (surahs 2–98 were left out as too long for a chat post), so the table can grow again later without renumbering. Groups may also be ayah ranges of a longer surah; the builder handles both. Arabic (Uthmani) and Saheeh International English for the ayahs in use live in `assets/quran_text.json` (regenerate with `scripts/fetch_quran.py`).

Rules:

- **Rakah 1:** a random group with `rakah_1: true`. A group carries that flag only when group **id + 1** also exists, so An-Nas (542) never opens a salah.
- **Rakah 2:** the **next group by id** — here simply the next surah: 527 Az-Zalzala → 528 Al-Adiyat, 540 Al-Ikhlas → 541 Al-Falaq, 541 → 542 An-Nas.
- Basmala is recited only when a group starts a surah at ayah 1 (never for At-Tawbah). Mid-surah groups, if ever added, start straight at their first ayah.
- Across different salah windows, repeats are fine.

Example header line: `Quran after Al-Fatiha: rakah 1 = group 540 **Al-Ikhlaas (whole surah)**, rakah 2 = group 541 **Al-Falaq (whole surah)** (the next group).`

## Madhhab

Only where texts differ: opening dua, and Shafi‘i Fajr qunut.  
See [MADHHAB_NOTES.md](MADHHAB_NOTES.md).

## Idempotency

`posted:YYYY-MM-DD:PrayerName`
