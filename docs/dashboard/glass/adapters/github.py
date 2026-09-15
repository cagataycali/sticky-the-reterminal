"""
🐙 glass.adapters.github — the owner's GitHub year on the glass.

GraphQL viewer{contributionsCollection{contributionCalendar}} (one call: every
day's count for the year, open PR/issue totals) + REST /users/<login>/events
(latest activity, private included when the token is the owner's). Token:
GITHUB_TOKEN / GH_TOKEN env, else `gh auth token` (absolute paths — launchd has
no brew PATH). demo → glass/fixtures/github.json.

Output: login, total_year, today, this_week, streak (consecutive days ending
today, today may still be 0 → counted from yesterday), best (count, date),
weeks (list of 7-day lists, oldest first, `weeks` param, default 20), month
labels per week column, levels (4-gray quantiles of the visible window),
events [{t, ago, type, repo, what}], open_prs, open_issues.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "github.json"
UA = {"User-Agent": "sticky-glass/1.0", "Accept": "application/vnd.github+json"}
GQL = """{ viewer { login
  contributionsCollection { contributionCalendar { totalContributions
    weeks { contributionDays { date contributionCount } } } }
  pullRequests(states: OPEN) { totalCount } issues(states: OPEN) { totalCount } } }"""


def token() -> Optional[str]:
    for k in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.getenv(k):
            return os.getenv(k)
    for gh in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", "gh"):
        try:
            out = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=5)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            continue
    return None


def _ago(iso: str, now: float) -> str:
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    s = max(0, int(now - t))
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        return f"{s // 3600} h"
    return f"{s // 86400} d"


def _summarize(e: Dict[str, Any]) -> tuple[str, str]:
    p = e.get("payload") or {}
    t = e.get("type", "")
    if t == "PushEvent":
        cs = p.get("commits") or []
        msg = (cs[-1].get("message", "").splitlines()[0] if cs else "").strip()
        n = p.get("size") or len(cs)
        if not n:
            return "push", "force-pushed"
        return "push", f"pushed {n} · {msg}" if msg else f"pushed {n}"
    if t == "PullRequestEvent":
        pr = p.get("pull_request") or {}
        return "pr", f"{p.get('action', 'touched')} PR #{p.get('number')} · {pr.get('title', '')}"
    if t in ("PullRequestReviewEvent", "PullRequestReviewCommentEvent"):
        pr = p.get("pull_request") or {}
        state = ((p.get("review") or {}).get("state") or "commented").lower()
        return "review", f"reviewed PR #{pr.get('number')} · {state}"
    if t == "IssuesEvent":
        i = p.get("issue") or {}
        return "issue", f"{p.get('action', '')} #{i.get('number')} · {i.get('title', '')}"
    if t == "IssueCommentEvent":
        i = p.get("issue") or {}
        return "comment", f"commented on #{i.get('number')} · {i.get('title', '')}"
    if t == "CreateEvent":
        return "create", f"created {p.get('ref_type', 'ref')} {p.get('ref') or ''}".strip()
    if t == "DeleteEvent":
        return "delete", f"deleted {p.get('ref_type', 'ref')} {p.get('ref') or ''}".strip()
    if t == "WatchEvent":
        return "star", "starred"
    if t == "ForkEvent":
        return "fork", "forked"
    if t == "ReleaseEvent":
        return "release", f"released {(p.get('release') or {}).get('tag_name', '')}"
    return "event", t.replace("Event", "").lower()


def _live(params: Dict[str, Any]) -> Dict[str, Any]:
    tok = token()
    if not tok:
        raise RuntimeError("no GitHub token")
    h = {**UA, "Authorization": f"bearer {tok}"}
    r = requests.post("https://api.github.com/graphql", json={"query": GQL}, headers=h, timeout=12)
    r.raise_for_status()
    v = r.json()["data"]["viewer"]
    cal = v["contributionsCollection"]["contributionCalendar"]
    days = [{"date": d["date"], "count": d["contributionCount"]} for w in cal["weeks"] for d in w["contributionDays"]]
    login = v["login"]
    ev = requests.get(f"https://api.github.com/users/{login}/events", params={"per_page": 30}, headers=h, timeout=12)
    ev.raise_for_status()
    now = time.time()
    events = []
    for e in sorted(ev.json(), key=lambda e: e["created_at"], reverse=True):
        kind, what = _summarize(e)
        events.append({"t": e["created_at"], "ago": _ago(e["created_at"], now), "type": kind,
                       "repo": (e.get("repo") or {}).get("name", ""), "what": what})
    return {"login": login, "total_year": cal["totalContributions"], "days": days, "events": events,
            "open_prs": v["pullRequests"]["totalCount"], "open_issues": v["issues"]["totalCount"]}


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    n_weeks = int(params.get("weeks") or 20)
    if params.get("demo"):
        raw = json.loads(FIXTURE.read_text())
        today = dt.date.fromisoformat(raw["days"][-1]["date"])
        now_s = dt.datetime.combine(today, dt.time(10, 0), tzinfo=dt.timezone.utc).timestamp()
        for e in raw["events"]:
            e["ago"] = _ago(e["t"], now_s)
    else:
        try:
            raw = _live(params)
            today = dt.date.today()
        except Exception:
            raw = json.loads(FIXTURE.read_text())
            raw["login"] = raw["login"] + " (offline)"
            today = dt.date.fromisoformat(raw["days"][-1]["date"])
            now_s = dt.datetime.combine(today, dt.time(10, 0), tzinfo=dt.timezone.utc).timestamp()
            for e in raw["events"]:
                e["ago"] = _ago(e["t"], now_s)
    days: List[Dict[str, Any]] = raw["days"]
    by = {d["date"]: int(d["count"]) for d in days}
    # a window of n_weeks columns ending in today's week (weeks start Sunday, like GitHub)
    week_start = today - dt.timedelta(days=(today.weekday() + 1) % 7)
    first = week_start - dt.timedelta(weeks=n_weeks - 1)
    weeks, months = [], []
    for w in range(n_weeks):
        ws = first + dt.timedelta(weeks=w)
        col = []
        for i in range(7):
            d = ws + dt.timedelta(days=i)
            col.append(None if d > today else by.get(d.isoformat(), 0))
        weeks.append(col)
        months.append(ws.strftime("%b") if ws.day <= 7 or w == 0 else "")
    last = -9
    for i in range(len(months)):   # no repeats, and never two labels within 3 columns (they collide)
        if months[i]:
            prev = next((m for m in reversed(months[:i]) if m), "")
            if months[i] == prev or i - last < 3:
                months[i] = ""
            else:
                last = i
    visible = sorted(v for col in weeks for v in col if v)
    if visible:
        q = lambda p: visible[min(len(visible) - 1, int(p * len(visible)))]
        levels = (q(0.25), q(0.5), q(0.75))
    else:
        levels = (1, 2, 3)
    today_n = by.get(today.isoformat(), 0)
    streak, d = 0, today if today_n else today - dt.timedelta(days=1)
    while by.get(d.isoformat(), 0) > 0:
        streak += 1
        d -= dt.timedelta(days=1)
    best = max(days, key=lambda x: x["count"]) if days else {"count": 0, "date": ""}
    this_week = sum(v for v in weeks[-1] if v)
    return {"login": raw["login"], "total_year": raw["total_year"], "today": today_n, "this_week": this_week,
            "streak": streak, "best": {"count": best["count"], "date": best["date"]},
            "weeks": weeks, "months": months, "levels": levels,
            "events": raw.get("events", [])[:12], "open_prs": raw.get("open_prs"), "open_issues": raw.get("open_issues"),
            "now": dt.datetime.now().strftime("%H:%M"), "today_label": today.strftime("%a %-d %b")}
