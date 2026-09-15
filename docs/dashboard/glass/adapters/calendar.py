"""
📅 glass.adapters.calendar — ICS → the day's / month's events.

params:
  ics_url   http(s) URL or local path of an .ics feed (Google "secret address
            in iCal format", Apple public calendar, Outlook published…)
  date      YYYY-MM-DD (default today, in `tz`)
  tz        IANA zone (default: this machine's)
  demo      truthy → glass/fixtures/demo.ics (no network)

Output events are plain dicts: {title, location, start, end, all_day, dt_start,
dt_end} with times already in `tz`. Recurring events are expanded with
dateutil.rrule over the window (RRULE only — EXDATE honoured, RDATE ignored;
documented limitation, not a silent one).
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import requests
from dateutil.rrule import rrulestr
from icalendar import Calendar

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "demo.ics"
UA = {"User-Agent": "sticky-glass/1.0 (+https://sticky.cagatay.my)"}

_cache: Dict[str, Any] = {}


def local_tz() -> ZoneInfo:
    tz = os.getenv("TZ") or ""
    if not tz:
        try:
            tz = os.readlink("/etc/localtime").split("zoneinfo/")[-1]
        except OSError:
            tz = "UTC"
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("UTC")


def load_ics(src: str) -> Calendar:
    if src.startswith("http://") or src.startswith("https://"):
        hit = _cache.get(src)
        if hit and dt.datetime.now().timestamp() - hit["ts"] < 300:
            return hit["cal"]
        r = requests.get(src, headers=UA, timeout=12)
        r.raise_for_status()
        cal = Calendar.from_ical(r.content)
        _cache[src] = {"cal": cal, "ts": dt.datetime.now().timestamp()}
        return cal
    return Calendar.from_ical(Path(src).read_bytes())


def _as_dt(v, tz: ZoneInfo, end: bool = False) -> tuple[dt.datetime, bool]:
    """Return (aware datetime in tz, all_day)."""
    if isinstance(v, dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=tz)
        return v.astimezone(tz), False
    # date → all-day, midnight local
    d = dt.datetime.combine(v, dt.time.min, tzinfo=tz)
    return d, True


def events_between(cal: Calendar, start: dt.datetime, end: dt.datetime, tz: ZoneInfo) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for comp in cal.walk("VEVENT"):
        if comp.get("STATUS") and str(comp.get("STATUS")).upper() == "CANCELLED":
            continue
        ds = comp.get("DTSTART")
        if ds is None:
            continue
        s, all_day = _as_dt(ds.dt, tz)
        de = comp.get("DTEND")
        if de is not None:
            e, _ = _as_dt(de.dt, tz)
        elif comp.get("DURATION") is not None:
            e = s + comp.get("DURATION").dt
        else:
            e = s + (dt.timedelta(days=1) if all_day else dt.timedelta(hours=1))
        dur = e - s
        title = str(comp.get("SUMMARY", "") or "").strip() or "(untitled)"
        loc = str(comp.get("LOCATION", "") or "").strip()
        starts: List[dt.datetime]
        rrule = comp.get("RRULE")
        if rrule is not None:
            rule_txt = rrule.to_ical().decode()
            base = s if not all_day else s.replace(tzinfo=None)
            try:
                rule = rrulestr("RRULE:" + rule_txt, dtstart=base)
                lo = (start - dur) if not all_day else (start - dur).replace(tzinfo=None)
                hi = end if not all_day else end.replace(tzinfo=None)
                starts = list(rule.between(lo, hi, inc=True))
                if all_day:
                    starts = [x.replace(tzinfo=tz) for x in starts]
            except (ValueError, TypeError):
                starts = [s]
            ex = comp.get("EXDATE")
            if ex is not None:
                exs = ex if isinstance(ex, list) else [ex]
                skip = {_as_dt(d.dt, tz)[0].date() for x in exs for d in x.dts}
                starts = [x for x in starts if x.date() not in skip]
        else:
            starts = [s]
        for st in starts:
            en = st + dur
            if en <= start or st >= end:
                continue
            out.append({"title": title, "location": loc, "all_day": all_day,
                        "dt_start": st, "dt_end": en,
                        "start": st.strftime("%H:%M"), "end": en.strftime("%H:%M"),
                        "date": st.date().isoformat()})
    out.sort(key=lambda e: (not e["all_day"], e["dt_start"]))
    return out


def resolve(params: Dict[str, Any]) -> tuple[Calendar, ZoneInfo, dt.date, str]:
    tz = ZoneInfo(str(params["tz"])) if params.get("tz") else local_tz()
    day = dt.date.fromisoformat(str(params["date"])) if params.get("date") else dt.datetime.now(tz).date()
    if params.get("demo") or not params.get("ics_url"):
        src, name = str(FIXTURE), "Demo calendar"
    else:
        src, name = str(params["ics_url"]), "Calendar"
    cal = load_ics(src)
    cname = str(cal.get("X-WR-CALNAME", "") or "").strip() or name
    return cal, tz, day, cname


def fetch_day(params: Dict[str, Any]) -> Dict[str, Any]:
    cal, tz, day, name = resolve(params)
    start = dt.datetime.combine(day, dt.time.min, tzinfo=tz)
    end = start + dt.timedelta(days=1)
    evs = events_between(cal, start, end, tz)
    now = dt.datetime.now(tz)
    if params.get("demo") and not params.get("date"):
        # the fixture is anchored on a fixed day; show it "live" by pretending it is today
        pass
    return {"date": day.isoformat(), "weekday": day.strftime("%A"), "day_label": day.strftime("%A, %-d %B"),
            "calendar": name, "tz": str(tz), "now": now.strftime("%H:%M") if now.date() == day else None,
            "now_minutes": now.hour * 60 + now.minute if now.date() == day else None,
            "all_day": [e for e in evs if e["all_day"]],
            "events": [e for e in evs if not e["all_day"]]}


def fetch_month(params: Dict[str, Any]) -> Dict[str, Any]:
    cal, tz, day, name = resolve(params)
    first = day.replace(day=1)
    nxt = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    start = dt.datetime.combine(first, dt.time.min, tzinfo=tz) - dt.timedelta(days=7)
    end = dt.datetime.combine(nxt, dt.time.min, tzinfo=tz) + dt.timedelta(days=7)
    evs = events_between(cal, start, end, tz)
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for e in evs:
        d = e["dt_start"].date()
        while d < e["dt_end"].date() + (dt.timedelta(0) if e["all_day"] else dt.timedelta(days=1)):
            if e["all_day"] and d >= e["dt_end"].date():
                break
            by_day.setdefault(d.isoformat(), []).append(e)
            d += dt.timedelta(days=1)
            if not e["all_day"]:
                break
    return {"year": day.year, "month": day.month, "month_label": first.strftime("%B %Y"),
            "today": dt.datetime.now(tz).date().isoformat(), "date": day.isoformat(),
            "calendar": name, "tz": str(tz), "by_day": by_day}
