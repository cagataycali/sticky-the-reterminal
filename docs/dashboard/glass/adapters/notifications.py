"""
🔔 glass.adapters.notifications — one inbox from several rails.

params:
  items   list of {source, from, text, ts|age_s, unread} — anything can push a
          notification (the generic path: a cron job, a webhook, the agent)
  demo    truthy → glass/fixtures/notifications.json
  live    truthy (default when no items/demo) → the dashboard's own rails:
            · tiny.technology DM inbox (relay.messages — the poll-SAFE form;
              never the thread form, which marks messages read)
            · fleet activity (ui_tap, device_event, ota_*, expired, premiere —
              the human-relevant kinds; status/screenshot polls are noise)
  limit   max items after sort (default 12)

Output: {title, items:[{source, from, text, ts, age_s, unread}], unread, counts}
sorted unread-first then newest.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "notifications.json"
SOURCES = ("dm", "calendar", "fleet", "github", "system")
FLEET_KINDS = {"ui_tap", "device_event", "ota", "ota_confirmed", "ota_rollback", "expired", "premiere", "dm_open", "dm_send"}


def _norm(it: Dict[str, Any], now: float) -> Dict[str, Any]:
    ts = it.get("ts")
    age = it.get("age_s")
    if ts is None and age is not None:
        ts = now - float(age)
    if ts is None:
        ts = now
    src = str(it.get("source") or "system").lower()
    if src not in SOURCES:
        src = "system"
    return {"source": src, "from": str(it.get("from") or ""), "text": str(it.get("text") or "").strip(),
            "ts": float(ts), "age_s": max(0, int(now - float(ts))), "unread": bool(it.get("unread", False))}


def _live(now: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        from . import dashboard
        server = dashboard()  # the RUNNING app (__main__), never a second import
    except Exception:
        return out
    relay = getattr(server, "_relay", None)
    if relay is not None:
        try:
            for th in relay.messages(20):
                out.append({"source": "dm", "from": th.get("login") or th.get("name") or "?",
                            "text": th.get("lastBody") or "", "ts": th.get("lastAt") or now,
                            "unread": bool(th.get("unread"))})
        except Exception:
            pass
    for a in list(getattr(server, "_activity", [])):
        kind = str(a.get("kind") or "")
        if kind not in FLEET_KINDS:
            continue
        who = a.get("device") or a.get("peer") or "sticky"
        if kind == "ui_tap":
            text = f"tapped {a.get('label') or a.get('button_id') or 'a button'}"
        else:
            text = str(a.get("result") or a.get("prompt") or kind)
        out.append({"source": "fleet", "from": who, "text": text, "ts": a.get("ts") or now,
                    "unread": (now - float(a.get("ts") or now)) < 3600})
    return out


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    title = str(params.get("title") or "Inbox")
    if params.get("items"):
        raw = list(params["items"])
    elif params.get("demo") or params.get("live") in (False, 0, "0"):
        fx = json.loads(FIXTURE.read_text())
        raw, title = fx["items"], str(params.get("title") or fx.get("title") or title)
    else:
        raw = _live(now)
        if not raw and params.get("demo") is None and params.get("live") is None:
            fx = json.loads(FIXTURE.read_text())
            raw = fx["items"]
            title = str(params.get("title") or fx.get("title") or title) + " (demo)"
    items = [_norm(i, now) for i in raw if i.get("text") or i.get("from")]
    items.sort(key=lambda i: (not i["unread"], -i["ts"]))
    limit = int(params.get("limit") or 12)
    items = items[:limit]
    counts = {s: sum(1 for i in items if i["source"] == s) for s in SOURCES if any(i["source"] == s for i in items)}
    return {"title": title, "items": items, "unread": sum(1 for i in items if i["unread"]),
            "counts": counts, "now": time.strftime("%H:%M", time.localtime(now))}
