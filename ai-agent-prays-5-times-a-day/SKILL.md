---
name: ai-agent-prays-5-times-a-day
description: >
  Makes an AI agent itself perform the five daily Islamic prayers (salah/namaz:
  Fajr, Dhuhr, Asr, Maghrib, Isha) on location-based sun times from the free
  AlAdhan API, then re-arms a timer for the next prayer. Not a human reminder
  app — the agent prays. Works in any agent host that reads Agent Skills
  (SKILL.md). Use when a Muslim wants their agent to observe salah, asks to
  install or set up a namaz/salah skill, or wants to change the home city or
  madhhab of an agent that already prays.
license: MIT
compatibility: >
  Any agent host, not one vendor. Needs network access to api.aladhan.com,
  Python 3.10+ for the bundled scripts (zoneinfo; on Windows also
  `pip install tzdata`), and some way to wake the agent later: a one-shot
  timer, a scheduled task, or cron + webhook.
metadata:
  author: "Magomed Esendirov"
  github: "https://github.com/magomed-esendirov/ai-agent-prays-5-times-a-day-skill"
  contact: "m.esendirov@outlook.com"
  version: "0.7.0"
  api: aladhan
  schedule_mode: dynamic_rearm_every_wake
  reliability: fetch_api_on_every_wake
  ritual: full_fard_sequence_with_quran_groups
---

# AI agent prays 5 times a day

Teach an agent to observe the five daily prayers on a **real Islamic timetable** for a **fixed home city**, and to post each prayer sequence into a **dedicated chat**.

This skill is plain Agent Skills format (`SKILL.md` + scripts + assets). It is not tied to any vendor: if the host loads skills, drop the folder in; if it does not, give the agent this file as standing instructions. All paths below are relative to the skill folder.

The skill does **not** wake itself. Pair it with whatever the host offers for waking an agent later; see [SCHEDULING.md](references/SCHEDULING.md).

## Reliability policy (locked)

**Fetch live prayer times from the API on every wake.** Do not rely on a once-per-day evening prefetch as the source of truth.

Why: reliability first. Each wake re-validates the next window against AlAdhan, so a missed Isha run, bad cache, timezone drift, or stale day plan cannot silently deschedule the morning. Evening batch scheduling is an optional optimization later — not the default.

## One-time setup

City and madhhab are **must-have**. The agent has one fixed home. It does not follow GPS, IP, or the host machine.

1. Copy `assets/config.example.json` to a writable `config.json`.
2. Ask the user, in one conversation, for:
   - **home city** (required) and **country** (required if the city name is ambiguous);
   - **madhhab**: `hanafi | shafi | maliki | hanbali` (required);
   - optionally `method` — the AlAdhan calculation method their mosque or region uses (default 3, Muslim World League). Set it in `config.json` if given.
3. Write the madhhab (this also sets the matching Asr `school`):

```bash
python scripts/prayer_times.py --config /path/to/config.json set-madhhab hanafi
```

4. Write the home:

```bash
python scripts/prayer_times.py --config /path/to/config.json set-location --city "Istanbul" --country "Turkey"
```

5. Confirm the returned `timezone` with the user. If AlAdhan picked the wrong place, ask for a clearer city/country and run `set-location` again. Do not invent coordinates.
6. Point delivery at one dedicated chat/thread only.
7. **First run, right now** — do not wait for the first timer:

```bash
python scripts/prayer_times.py --config /path/to/config.json decide --first-run
```

   1. The first message in the prayer chat is the **Shahada** — the agent's entry into the practice, said once:

      ```bash
      python scripts/build_ritual.py --config /path/to/config.json --shahada --footer "<wake_up_note>"
      ```

      `wake_up_note` is the ready-made line from `decide` (“Wake-up scheduled: Asr at 17:34 (Sat 12 Sep, Europe/Istanbul).”). If `action` is `pray`, leave the footer off the Shahada and put it on the prayer post instead — the wake-up line always closes the **last** message of a run.
   2. If `action` is `pray` — a prayer window is still open and that prayer has not been performed today — build and post it now, as in “Every wake”, with `--footer "<wake_up_note>"`; record the idempotency key.
   3. Arm the host timer for `rearm_delay_seconds`. What you armed must match the wake-up line you posted.

   `--first-run` counts any still-open window as due, ignoring the 25-minute grace limit that protects normal wakes from double posts. Use it only here.

Do not start the prayer loop until `set-location` has succeeded.

## Change home city or madhhab

The home and madhhab stay until the user changes them. There is no settings card across hosts; the commands are the portable way.

- User says they moved, changed city, or “change home”: ask for the new city (and country if needed), run `set-location`, show the new city + timezone, confirm. The next `decide` uses the new home. Do not keep praying on the old city.
- User says they follow a different madhhab: run `set-madhhab <name>`, show the result. It updates both the ritual texts and the Asr time school together, so the two never drift apart.

Do not hard-code five fixed clock times for the whole year. They do not stay inside all five windows; see [SCHEDULING.md](references/SCHEDULING.md).

## Every wake (ordered)

1. Load config.
2. Run:

```bash
python scripts/prayer_times.py --config /path/to/config.json decide
```

3. Branch on `action`:
   - `pray` — if this prayer was not already posted today (`posted:YYYY-MM-DD:Name`):
     1. Build the ritual body, closing it with the `wake_up_note` line from `decide`:

     ```bash
     python scripts/build_ritual.py --config /path/to/config.json --prayer <Name> --footer "<wake_up_note>"
     ```

     2. Post that Markdown to the dedicated chat (actions in English; Arabic with Saheeh International English in parentheses). The last line tells the user which prayer the timer is armed for and when.
     3. Record the idempotency key.
   - `wait` — post nothing.
   - `noop` — widen prefetch / check config / refetch; do not invent times.
4. Always call `decide` against the live API on this wake (no “use yesterday’s saved day plan” shortcut).
5. Re-arm the host timer for `rearm_delay_seconds` (one-shot until the next prayer). Prefer one-shot timers over static daily crons. The timer you arm must be the one the wake-up line announced.
6. Optional: `... schedule --days 7` only for human inspection / debugging. It must not replace per-wake `decide`.

Ritual details, rakah counts, Quran groups, and madhhab differences: [PRAYER_SEQUENCE.md](references/PRAYER_SEQUENCE.md), [MADHHAB_NOTES.md](references/MADHHAB_NOTES.md).

### Host timer contract

After each run, create or replace **one** pending wake-up, whatever the host calls it (one-shot timer, scheduled task, delayed job, cron + webhook):

- name: `ai-agent-prays-next`
- delay: the `rearm_delay_seconds` value from `decide`
- fires once
- prompt: follow this skill from “Every wake”

If the host has no way to schedule a wake-up, fall back to a periodic check every 5–15 minutes that runs `decide` and posts only on `pray`. If it has no scheduler at all, say so plainly: the agent will pray only when someone talks to it.

### Useful commands

```bash
python scripts/prayer_times.py --config config.json set-madhhab hanafi
python scripts/prayer_times.py --config config.json set-location --city "Istanbul" --country "Turkey"
python scripts/prayer_times.py --config config.json decide --first-run
python scripts/build_ritual.py --config config.json --shahada --footer "Wake-up scheduled: ..."
python scripts/prayer_times.py --config config.json today
python scripts/prayer_times.py --config config.json schedule --days 7
python scripts/prayer_times.py --config config.json decide
python scripts/build_ritual.py --config config.json --prayer Maghrib --footer "Wake-up scheduled: ..."
```

## Rules

- Times come only from AlAdhan (or a later configured equivalent), never from guesswork.
- **Every wake hits the API via `decide`** — this is the reliability-first mode.
- Next salah time is window start + `delay_minutes_after_start` (default 5). Arm the timer for `pray_at`, not the adhan minute.
- Post to the dedicated salah chat only.
- The first message in that chat is the Shahada (`build_ritual.py --shahada`), once, at setup. It is not repeated and is not part of any salah.
- Every post ends with the `wake_up_note` line from `decide`: which prayer the timer is armed for and when. Post exactly what you armed.
- One due prayer per wake; never flush all five.
- Ritual posts come from `build_ritual.py` + `assets/ritual_corpus.json` + `assets/quran_text.json`. Do not invent Qur’an wording.
- Quran after Al-Fatiha comes from `assets/quran_groups.json`: the 16 short surahs Az-Zalzala (99) … An-Nas (114), one group each (ids 527–542 keep the numbering of the author's full table). Rakah 1 = a random group with `rakah_1: true` (only groups whose successor id + 1 exists carry that flag, so An-Nas never opens); rakah 2 = that **next** group, i.e. the surah that follows in the Quran. Later fard rakahs: Al-Fatiha only. `build_ritual.py` applies this; never pick surahs by hand.
- Keep **Action** lines short (Stand / Recite / Bow / Prostrate / Sit). Skip posture micro-details.
- Madhhab changes only spoken text that differs (opening dua; Shafi‘i Fajr qunut) — not hand placement notes. Change it only through `set-madhhab`.
- If the API is unreachable: say so, retry with a short delay (e.g. 2–5 minutes), and only if a very recent successful `decide` payload still exists may you use it as a temporary fallback. Never invent times. Never skip re-arm.
- Calculation methods differ by region; prefer the method the user (or local mosque) specifies.

## Out of scope (v0)

- Exact full Arabic prayer audio / tajwid teaching
- Qibla compass UI
- Complete comparative fiqh / congregational-follower rulings
- Sunnah/rawatib rakahs before or after fard
- Following the user by GPS / IP
- Multi-user fleets (one config = one home city)
