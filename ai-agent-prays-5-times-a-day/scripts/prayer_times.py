#!/usr/bin/env python3
"""Fetch Islamic prayer times via the free AlAdhan API and decide the next action.

No API key required. Network access to api.aladhan.com is required.

Home city is fixed: set once at install, change later with `set-location`.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

API_BASE = "https://api.aladhan.com/v1"
DEFAULT_PRAYERS = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")
BOUNDARY_KEYS = ("Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha")
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "config.example.json"


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise SystemExit(
            f"Unknown timezone {name!r}. On Windows: pip install tzdata"
        ) from exc


@dataclass(frozen=True)
class Config:
    city: str
    country: str
    latitude: float | None
    longitude: float | None
    timezone: str
    method: int
    school: int
    prayers: tuple[str, ...]
    grace_minutes_after_start: int
    delay_minutes_after_start: int
    min_rearm_delay_seconds: int
    prefetch_days: int
    label: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        location = raw.get("location") or {}
        schedule = raw.get("schedule") or {}
        prayers = tuple(raw.get("prayers") or DEFAULT_PRAYERS)
        city = str(location.get("city") or "").strip()
        country = str(location.get("country") or "").strip()
        lat_raw = location.get("latitude")
        lon_raw = location.get("longitude")
        latitude = float(lat_raw) if lat_raw is not None and lat_raw != "" else None
        longitude = float(lon_raw) if lon_raw is not None and lon_raw != "" else None
        if not city and (latitude is None or longitude is None):
            raise SystemExit("config location.city is required (or latitude + longitude)")
        label = str(location.get("label") or city or "custom")
        timezone = str(location.get("timezone") or "UTC").strip() or "UTC"
        return cls(
            city=city,
            country=country,
            latitude=latitude,
            longitude=longitude,
            timezone=timezone,
            method=int(raw.get("method", 3)),
            school=int(raw.get("school", 0)),
            prayers=prayers,
            grace_minutes_after_start=int(schedule.get("grace_minutes_after_start", 25)),
            delay_minutes_after_start=int(schedule.get("delay_minutes_after_start", 5)),
            min_rearm_delay_seconds=int(schedule.get("min_rearm_delay_seconds", 60)),
            prefetch_days=int(schedule.get("prefetch_days", 7)),
            label=label,
        )


def default_config_path() -> Path:
    return DEFAULT_CONFIG


def load_raw_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_raw_config(path: Path, raw: dict[str, Any]) -> None:
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _http_get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "ai-agent-prays-5-times-a-day/0.6"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"AlAdhan HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"AlAdhan network error: {exc}") from exc
    if payload.get("code") != 200:
        raise SystemExit(f"AlAdhan error payload: {payload}")
    return payload["data"]


def resolve_city(city: str, country: str, method: int, school: int) -> dict[str, str]:
    city = city.strip()
    country = country.strip()
    if not city:
        raise SystemExit("city is required")
    query: dict[str, Any] = {"city": city, "method": method, "school": school}
    if country:
        query["country"] = country
    url = f"{API_BASE}/timingsByCity?{urllib.parse.urlencode(query)}"
    data = _http_get_json(url)
    timings = data.get("timings") or {}
    if "Fajr" not in timings:
        raise SystemExit(f"AlAdhan did not return prayer times for {city}")
    timezone = str((data.get("meta") or {}).get("timezone") or "").strip()
    if not timezone:
        raise SystemExit(f"AlAdhan did not return a timezone for {city}")
    return {
        "city": city,
        "country": country,
        "timezone": timezone,
        "label": city,
        "mode": "fixed",
    }


def apply_home(raw: dict[str, Any], home: dict[str, str]) -> dict[str, Any]:
    location = dict(raw.get("location") or {})
    location["mode"] = "fixed"
    location["city"] = home["city"]
    location["country"] = home["country"]
    location["timezone"] = home["timezone"]
    location["label"] = home["label"]
    location.pop("latitude", None)
    location.pop("longitude", None)
    raw["location"] = location
    return raw


def load_config(path: Path | None) -> Config:
    path = path or default_config_path()
    raw = load_raw_config(path)
    loc = raw.get("location") or {}
    city = str(loc.get("city") or "").strip()
    if city and not str(loc.get("timezone") or "").strip():
        home = resolve_city(
            city,
            str(loc.get("country") or ""),
            int(raw.get("method", 3)),
            int(raw.get("school", 0)),
        )
        apply_home(raw, home)
        if path != default_config_path():
            save_raw_config(path, raw)
    return Config.from_dict(raw)


def fetch_day_timings(cfg: Config, day: date) -> dict[str, Any]:
    date_path = day.strftime("%d-%m-%Y")
    if cfg.city:
        query: dict[str, Any] = {
            "city": cfg.city,
            "method": cfg.method,
            "school": cfg.school,
        }
        if cfg.country:
            query["country"] = cfg.country
        url = f"{API_BASE}/timingsByCity/{date_path}?{urllib.parse.urlencode(query)}"
        return _http_get_json(url)
    if cfg.latitude is None or cfg.longitude is None:
        raise SystemExit("config needs location.city or latitude+longitude")
    query = {
        "latitude": cfg.latitude,
        "longitude": cfg.longitude,
        "method": cfg.method,
        "school": cfg.school,
        "timezonestring": cfg.timezone,
    }
    url = f"{API_BASE}/timings/{date_path}?{urllib.parse.urlencode(query)}"
    return _http_get_json(url)


def parse_local_time(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    clock = hhmm.split()[0]
    hour_s, minute_s = clock.split(":")
    return datetime(
        day.year,
        day.month,
        day.day,
        int(hour_s),
        int(minute_s),
        tzinfo=tz,
    )


def build_day_schedule(cfg: Config, day: date, data: dict[str, Any]) -> list[dict[str, Any]]:
    tz = zone(cfg.timezone)
    timings = data["timings"]
    markers = {key: parse_local_time(day, timings[key], tz) for key in BOUNDARY_KEYS}
    window_end = {
        "Fajr": markers["Sunrise"],
        "Dhuhr": markers["Asr"],
        "Asr": markers["Maghrib"],
        "Maghrib": markers["Isha"],
        # Placeholder until fetch_schedule patches Isha end with next Fajr.
        "Isha": markers["Isha"] + timedelta(hours=6),
    }

    hijri = ((data.get("date") or {}).get("hijri") or {})
    out: list[dict[str, Any]] = []
    for name in cfg.prayers:
        start = markers[name]
        end = window_end[name]
        ready = start + timedelta(minutes=cfg.delay_minutes_after_start)
        if ready >= end:
            ready = start
        out.append(
            {
                "name": name,
                "start_iso": start.isoformat(),
                "end_iso": end.isoformat(),
                "start_local": start.strftime("%H:%M"),
                "end_local": end.strftime("%H:%M"),
                "pray_at_iso": ready.isoformat(),
                "pray_at_local": ready.strftime("%H:%M"),
                "gregorian": day.isoformat(),
                "hijri": hijri.get("date"),
            }
        )
    return out


def _patch_isha_ends(events: list[dict[str, Any]]) -> None:
    by_day: dict[str, dict[str, dict[str, Any]]] = {}
    for event in events:
        by_day.setdefault(event["gregorian"], {})[event["name"]] = event
    for event in events:
        if event["name"] != "Isha":
            continue
        day = date.fromisoformat(event["gregorian"])
        fajr = by_day.get((day + timedelta(days=1)).isoformat(), {}).get("Fajr")
        if fajr:
            event["end_iso"] = fajr["start_iso"]
            event["end_local"] = fajr["start_local"]


def fetch_schedule(cfg: Config, start: date | None = None, days: int | None = None) -> list[dict[str, Any]]:
    start = start or datetime.now(zone(cfg.timezone)).date()
    days = days if days is not None else cfg.prefetch_days
    events: list[dict[str, Any]] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        data = fetch_day_timings(cfg, day)
        events.extend(build_day_schedule(cfg, day, data))
    _patch_isha_ends(events)
    return events


def wake_up_note(target: dict[str, Any] | None, timezone: str) -> str | None:
    """One ready-to-post line stating which prayer the timer is armed for and when."""
    if target is None:
        return None
    at = datetime.fromisoformat(target["pray_at_iso"])
    return f"Wake-up scheduled: {target['name']} at {at.strftime('%H:%M')} ({at.strftime('%a %d %b')}, {timezone})."


def decide(cfg: Config, now: datetime | None = None, *, first_run: bool = False) -> dict[str, Any]:
    """Pick pray / wait / noop for this wake.

    Normal wakes count a prayer as due only within `grace_minutes_after_start`
    of its window start, so a late or duplicate wake never re-posts. On the very
    first run after setup (`first_run=True`) that prayer has not been performed
    today, so any window that is still open counts as due.
    """
    tz = zone(cfg.timezone)
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    events = fetch_schedule(cfg, start=now.date(), days=max(2, min(cfg.prefetch_days, 3)))
    grace = timedelta(minutes=cfg.grace_minutes_after_start)

    due: dict[str, Any] | None = None
    for event in events:
        ready = datetime.fromisoformat(event["pray_at_iso"])
        start = datetime.fromisoformat(event["start_iso"])
        end = datetime.fromisoformat(event["end_iso"])
        latest = end if first_run else min(start + grace, end)
        if ready <= now <= latest:
            due = event
            break

    upcoming: dict[str, Any] | None = None
    for event in events:
        if datetime.fromisoformat(event["pray_at_iso"]) > now:
            upcoming = event
            break

    if due is not None:
        next_after_due = None
        due_start = datetime.fromisoformat(due["start_iso"])
        for event in events:
            if datetime.fromisoformat(event["start_iso"]) > due_start:
                next_after_due = event
                break
        rearm_target = next_after_due or upcoming
        delay = cfg.min_rearm_delay_seconds
        if rearm_target is not None:
            delay = max(
                cfg.min_rearm_delay_seconds,
                int((datetime.fromisoformat(rearm_target["pray_at_iso"]) - now).total_seconds()),
            )
        return {
            "action": "pray",
            "prayer": due,
            "next_prayer": rearm_target,
            "rearm_delay_seconds": delay,
            "wake_up_note": wake_up_note(rearm_target, cfg.timezone),
            "first_run": first_run,
            "now_iso": now.isoformat(),
            "location_label": cfg.label,
            "city": cfg.city,
            "country": cfg.country,
            "timezone": cfg.timezone,
            "method": cfg.method,
            "school": cfg.school,
        }

    if upcoming is None:
        return {
            "action": "noop",
            "reason": "no_upcoming_prayer_in_prefetch_window",
            "now_iso": now.isoformat(),
            "location_label": cfg.label,
            "city": cfg.city,
            "country": cfg.country,
            "timezone": cfg.timezone,
        }

    delay = max(
        cfg.min_rearm_delay_seconds,
        int((datetime.fromisoformat(upcoming["pray_at_iso"]) - now).total_seconds()),
    )
    return {
        "action": "wait",
        "prayer": None,
        "next_prayer": upcoming,
        "rearm_delay_seconds": delay,
        "wake_up_note": wake_up_note(upcoming, cfg.timezone),
        "now_iso": now.isoformat(),
        "location_label": cfg.label,
        "city": cfg.city,
        "country": cfg.country,
        "timezone": cfg.timezone,
        "method": cfg.method,
        "school": cfg.school,
    }


def set_location(path: Path, city: str, country: str) -> dict[str, str]:
    raw = load_raw_config(path)
    home = resolve_city(city, country, int(raw.get("method", 3)), int(raw.get("school", 0)))
    apply_home(raw, home)
    save_raw_config(path, raw)
    return home


MADHHABS = ("hanafi", "shafi", "maliki", "hanbali")
ASR_SCHOOL_BY_MADHHAB = {"hanafi": 1, "shafi": 0, "maliki": 0, "hanbali": 0}


def set_madhhab(path: Path, madhhab: str) -> dict[str, Any]:
    """Write the ritual madhhab and the matching AlAdhan Asr school in one step."""
    raw = load_raw_config(path)
    raw["madhhab"] = madhhab
    raw["school"] = ASR_SCHOOL_BY_MADHHAB[madhhab]
    save_raw_config(path, raw)
    return {"madhhab": madhhab, "school": raw["school"], "method": int(raw.get("method", 3))}


def writable_config(path: Path | None, cmd: str) -> Path:
    if path is None:
        raise SystemExit(f"{cmd} needs --config pointing at a writable config.json")
    if path.resolve() == default_config_path().resolve():
        raise SystemExit("refusing to overwrite assets/config.example.json; copy it to config.json first")
    if not path.exists():
        raise SystemExit(f"config not found: {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Salah prayer schedule helper (AlAdhan)")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config JSON (defaults to assets/config.example.json)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_today = sub.add_parser("today", help="Print today's five prayer times")
    p_today.add_argument("--date", type=str, default=None, help="YYYY-MM-DD (local to config timezone)")

    p_week = sub.add_parser("schedule", help="Print a multi-day schedule")
    p_week.add_argument("--days", type=int, default=None)

    p_next = sub.add_parser("decide", help="Decide pray/wait/noop and re-arm delay")
    p_next.add_argument(
        "--now",
        type=str,
        default=None,
        help="ISO datetime override for tests, e.g. 2026-09-11T12:30:00+03:00",
    )
    p_next.add_argument(
        "--first-run",
        action="store_true",
        help="right after setup: a prayer whose window is still open counts as due, ignoring the grace limit",
    )

    p_home = sub.add_parser("set-location", help="Set or change the agent's fixed home city")
    p_home.add_argument("--city", required=True)
    p_home.add_argument("--country", default="")

    p_madhhab = sub.add_parser("set-madhhab", help="Set or change the madhhab (also sets the matching Asr school)")
    p_madhhab.add_argument("madhhab", choices=MADHHABS)

    args = parser.parse_args(argv)

    if args.cmd == "set-location":
        path = writable_config(args.config, args.cmd)
        home = set_location(path, args.city, args.country)
        json.dump({"ok": True, "home": home}, sys.stdout, indent=2, ensure_ascii=False)
        print()
        return 0

    if args.cmd == "set-madhhab":
        path = writable_config(args.config, args.cmd)
        result = set_madhhab(path, args.madhhab)
        json.dump({"ok": True, **result}, sys.stdout, indent=2, ensure_ascii=False)
        print()
        return 0

    cfg = load_config(args.config)

    if args.cmd == "today":
        day = date.fromisoformat(args.date) if args.date else datetime.now(zone(cfg.timezone)).date()
        events = fetch_schedule(cfg, start=day, days=2)
        todays = [event for event in events if event["gregorian"] == day.isoformat()]
        json.dump(
            {"date": day.isoformat(), "location": cfg.label, "city": cfg.city, "prayers": todays},
            sys.stdout,
            indent=2,
            ensure_ascii=False,
        )
        print()
        return 0

    if args.cmd == "schedule":
        events = fetch_schedule(cfg, days=args.days)
        json.dump({"location": cfg.label, "city": cfg.city, "events": events}, sys.stdout, indent=2, ensure_ascii=False)
        print()
        return 0

    if args.cmd == "decide":
        now = datetime.fromisoformat(args.now) if args.now else None
        result = decide(cfg, now=now, first_run=args.first_run)
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        print()
        return 0

    raise AssertionError(f"unhandled command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
