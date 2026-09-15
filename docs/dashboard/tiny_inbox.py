#!/usr/bin/env python3
"""
tiny_inbox — cagatay's tiny messages, on the Sticky's glass.

Purpose: the device shows the notifications
and the conversations of its owner, and can answer them. This is the OWNER side
of it. It exists as its own module, not as a patch to server.py, for two
reasons: server.py's verb tables are the relay-side live working set, and a
poller wants a process, which server.py does not have (every periodic thing in
it is driven by a browser tab being open).

Why owner-side at all: every message route on tiny.technology is owner-Bearer.
A device token can read the relay and post events, but it cannot list messages
and cannot DM a person. So the device is a FACE for the inbox, not a client of
it — and it must never pretend otherwise. Outbound sends here are marked
viaTiny, because the agent sent them, not cagatay's thumb.

Two facts from the platform that shape the whole design:

  * GET /api/messages (inbox form) is read-only and cheap → this is what the
    poller polls.
  * GET /api/messages?with=<peer> MARKS THE THREAD READ as a side effect. That
    is cagatay's phone badge. It is only called when a human has actually
    opened that thread on the device.

Two facts from the firmware:

  * The panel is ASCII-only — the renderer folds every other byte to '?'. Half
    of these people have Turkish names, so we transliterate rather than mangle:
    "Mehmet Mert Kırgıl" must not arrive as "Mehmet Mert K?rg?l".
  * tiny_touch's action_for() does a case-insensitive SUBSTRING match over
    button ids AND labels, and hijacks any hit into a local shell action. A
    person called "Noble" or a message saying "sorun var" would silently press
    "rescan ble" / "sor". Every human-authored string that becomes a label goes
    through _defuse() first.

Usage (owner credential from ~/.tiny/credentials.json):
    python tiny_inbox.py inbox                 # push the inbox card
    python tiny_inbox.py notify [n]            # push newest thread as a notice
    python tiny_inbox.py thread <login>        # push one conversation (MARKS READ)
    python tiny_inbox.py reply <login> <text>  # send, viaTiny
    python tiny_inbox.py watch [seconds]       # poll messages, push on change
    python tiny_inbox.py serve [seconds]       # the whole loop: messages AND taps
    python tiny_inbox.py peek                  # print state, push nothing
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from tiny_relay import RelayError, TinyRelay

DEVICE = os.getenv("STICKY_DEVICE", "b20893b4-ccc5-4f40-8cda-443c9d9dbdaa")
STATE_PATH = Path(os.getenv("STICKY_INBOX_STATE", "~/.tiny/sticky_inbox.json")).expanduser()
POLL_S = float(os.getenv("STICKY_INBOX_POLL", "45"))

# ── the panel is ASCII-only ───────────────────────────────────────────────
# tiny_display.cpp's ascii_fold() turns every byte >127 into '?', so a name
# must be transliterated on this side or it arrives unreadable. Turkish first
# (that is who writes to cagatay), then the common European letters.
_TRANSLIT = {
    "ı": "i", "İ": "I", "ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G",
    "ç": "c", "Ç": "C", "ö": "o", "Ö": "O", "ü": "u", "Ü": "U",
    "â": "a", "î": "i", "û": "u", "é": "e", "è": "e", "ê": "e", "á": "a",
    "à": "a", "ä": "a", "å": "a", "í": "i", "ó": "o", "ô": "o", "õ": "o",
    "ú": "u", "ñ": "n", "ß": "ss", "ø": "o", "æ": "ae", "å": "a",
    "“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "…": "...",
    "•": "-", "→": "->", "€": "EUR", "₺": "TL", " ": " ",
}


def _ascii(s: Any) -> str:
    """Best-effort ASCII. Anything still unmappable becomes '' rather than '?',
    because an emoji-shaped '?' reads as a rendering bug to the owner."""
    if not isinstance(s, str):
        return ""
    out = []
    for ch in s:
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif 32 <= ord(ch) < 127:
            out.append(ch)
        elif ch in "\r\n\t":
            out.append(" ")
        # else: dropped on purpose (emoji, CJK, symbols)
    return re.sub(r"\s+", " ", "".join(out)).strip()


# ── the label minefield ───────────────────────────────────────────────────
# Every token tiny_touch.cpp action_for() sniffs for. A label or id containing
# one of these gets hijacked into a local shell action AND still reports the
# tap, so the user sees two things happen. Longest first so the replacement of
# "rescan ble" doesn't get re-hit by "ble".
_SNIFFED = ["rescan ble", "ana ekran", "bluetooth", "settings", "ayarlar",
            "rescan", "sensor", "status", "durum", "wi-fi", "wifi", "sens",
            "home", "back", "geri", "ask", "sor", "ble"]


# FIXED IN FIRMWARE 0.14.6-m12: action_for() now sniffs the label only when the
# card supplied no id, and every card here supplies one. Proven on glass —
# buttons labelled "sorun var" / "Status" / "Noble Home" with ids held the card
# and only reported ui_tap, while a bare-string "Status" button still acts.
# So the mangling is OFF. It stays in the file, one env var away, because a
# rollback to <=0.14.5 would otherwise make a contact's name press buttons.
DEFUSE_LABELS = os.getenv("STICKY_DEFUSE_LABELS", "0") not in ("0", "", "no", "false")


def _defuse(s: str) -> str:
    """Break every firmware-sniffed substring by inserting a dot inside it.

    "sorun var" -> "so.run var", "Noble" -> "No.ble". Only needed against
    firmware <= 0.14.5, where a label was sniffed even when the card named the
    button explicitly.
    """
    if not DEFUSE_LABELS:
        return s
    out = s
    for tok in _SNIFFED:
        i = 0
        while True:
            j = out.lower().find(tok, i)
            if j < 0:
                break
            cut = j + max(1, len(tok) // 2)
            out = out[:cut] + "." + out[cut:]
            i = cut + 1 + (len(tok) - (cut - j))
    return out


def _clip(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: max(1, n - 1)].rstrip() + "-"


def _ago(ts: Any, now: Optional[float] = None) -> str:
    """Relative age, ASCII, at most 4 chars for a menu note."""
    if not isinstance(ts, (int, float)) or ts <= 0:
        return "?"
    d = max(0, int((now if now is not None else time.time()) - ts))
    if d < 90:
        return "now"
    if d < 5400:
        return f"{d // 60}m"
    if d < 172800:
        return f"{d // 3600}h"
    return f"{d // 86400}d"


def _first_name(name: str) -> str:
    return (_ascii(name).split(" ") or [""])[0] or "them"


# ── state (survives a restart, which the tap map must) ────────────────────
# A ui_tap carries only card_id/button_id/index/label — never the peer. The map
# from a row to a login lives HERE, so a pusher restart between the push and
# the finger does not turn a reply into a wrong-recipient message.
def _load_state() -> Dict[str, Any]:
    try:
        s = json.loads(STATE_PATH.read_text())
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(s: Dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=1))
        tmp.replace(STATE_PATH)
        os.chmod(STATE_PATH, 0o600)  # message previews are private
    except OSError as e:
        print(f"  ! state not saved: {e}", file=sys.stderr)


# ── cards ─────────────────────────────────────────────────────────────────
# Geometry that these respect (tiny_display.cpp): body box 258 px with buttons
# +footer, 752 px wide, body glyph 16x32, menu row 56+8 px, card_id 23 chars,
# menu label 63 / note 39, ui_tap label report 31, max 4 buttons, max 24 rows.
MAX_ROWS = 8


def inbox_card(threads: List[Dict[str, Any]], now: Optional[float] = None) -> Dict[str, Any]:
    rows, peers = [], []
    for i, t in enumerate(threads[:MAX_ROWS]):
        name = _first_name(t.get("name") or t.get("login") or "unknown")
        prev = _ascii(t.get("lastBody") or "")
        if not prev and (t.get("lastAttachments") or []):
            prev = "[attachment]"
        unread = int(t.get("unread") or 0)
        rows.append({
            "id": f"dm_t{i}",
            # 31 chars so the ui_tap report is not truncated either
            "label": _defuse(_clip(f"{name} - {prev}" if prev else name, 31)),
            "note": _ascii(_ago(t.get("lastAt"), now)) + (f" +{unread}" if unread else ""),
        })
        peers.append({"login": t.get("login"), "userId": t.get("userId"),
                      "name": _ascii(t.get("name") or t.get("login") or "")})
    total_unread = sum(int(t.get("unread") or 0) for t in threads)
    card = {
        "type": "menu",
        "card_id": "dm-inbox",
        "title": "Messages",
        "items": rows or [{"id": "dm_none", "label": "no conversations yet", "note": ""}],
        "footer": _clip(f"{len(threads)} conversations - {total_unread} unread - "
                        f"synced {time.strftime('%H:%M', time.localtime(now))} - tap a row", 94),
        "buttons": [{"id": "dm_reload", "label": "Reload"},
                    {"id": "dm_top", "label": "Newest"}],
    }
    return {"card": card, "peers": peers}


def notify_card(t: Dict[str, Any], total_unread: int = 0,
                now: Optional[float] = None) -> Dict[str, Any]:
    """One arriving message, big enough to read across the room."""
    name = _ascii(t.get("name") or t.get("login") or "unknown")
    body = _ascii(t.get("lastBody") or "")
    if not body:
        body = "[attachment]" if (t.get("lastAttachments") or []) else "(empty message)"
    login = t.get("login") or "?"
    mid = re.sub(r"[^A-Za-z0-9]", "", str(login))[:12] or "peer"
    card = {
        "type": "text",
        # 23 chars max, and distinct per message so the scroll offset resets
        "card_id": _clip(f"dm-{mid}-{int(t.get('lastAt') or 0) % 100000}", 23).rstrip("-"),
        "title": _clip(name, 31),
        "body": _clip(body, 1000),
        "footer": _clip(f"@{_ascii(login)} - {_ago(t.get('lastAt'), now)} ago"
                        + (f" - {total_unread} unread" if total_unread else "")
                        + " - swipe for more", 94),
        "buttons": [{"id": "dm_open", "label": "Thread"},
                    {"id": "dm_inbox", "label": "Inbox"},
                    {"id": "dm_ok", "label": "OK"},
                    {"id": "dm_omw", "label": "On my way"}],
    }
    return {"card": card, "peer": {"login": login, "userId": t.get("userId"), "name": name}}


def thread_card(peer: Dict[str, Any], messages: List[Dict[str, Any]],
                shown: int = 6) -> Dict[str, Any]:
    """A conversation as a `list` — chosen over `kv` (whose columns are only
    21/26 chars) and over `composite` (which does not scroll). The 1024-byte
    fold buffer is per ITEM in a list, so no message gets swallowed."""
    them = _first_name(peer.get("name") or peer.get("login") or "them")
    tail = messages[-shown:] if shown > 0 else messages
    items = []
    for m in tail:  # already oldest-first from the platform
        who = "me" if m.get("direction") == "sent" else them
        clock = time.strftime("%H:%M", time.localtime(m.get("created") or 0))
        body = _ascii(m.get("body") or "")
        if not body:
            body = "[attachment]" if (m.get("attachments") or []) else "(empty)"
        items.append(_clip(f"{clock} {who}: {body}", 150))
    login = peer.get("login") or "?"
    mid = re.sub(r"[^A-Za-z0-9]", "", str(login))[:14] or "peer"
    return {
        "type": "list",
        "card_id": _clip(f"dm-th-{mid}", 23),
        "title": _clip(_ascii(peer.get("name") or login), 31),
        "items": items or ["(no messages)"],
        "footer": _clip(f"@{_ascii(login)} - {len(messages)} in thread, last "
                        f"{len(items)} shown - read on open", 94),
        # Canned, because the device CANNOT compose free text for a DM: the
        # keyboard card's k: taps are routed to the shell and deliberately never
        # leave the device (Wi-Fi password privacy). Voice `ask` is the other way.
        "buttons": [{"id": "dm_yes", "label": "Yes"},
                    {"id": "dm_no", "label": "No"},
                    {"id": "dm_omw", "label": "On my way"},
                    {"id": "dm_inbox", "label": "Inbox"}],
    }


# ── actions ───────────────────────────────────────────────────────────────
CANNED = {
    "dm_yes": "Yes.",
    "dm_no": "No.",
    "dm_omw": "On my way.",
    "dm_ok": "Got it.",
}


def push_inbox(r: TinyRelay, threads: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    now = time.time()
    threads = r.messages() if threads is None else threads
    built = inbox_card(threads, now)
    st = _load_state()
    st["inbox"] = {"card_id": built["card"]["card_id"], "peers": built["peers"], "at": now}
    _save_state(st)
    out = r.push_card(DEVICE, built["card"])
    print(f"  inbox -> {len(built['peers'])} rows, card_id={built['card']['card_id']}, "
          f"device says: {str(out.get('result'))[:120]}")
    return out


def push_notify(r: TinyRelay, index: int = 0) -> Dict[str, Any]:
    now = time.time()
    threads = r.messages()
    if not threads:
        raise RelayError("no conversations to notify about")
    t = threads[max(0, min(index, len(threads) - 1))]
    built = notify_card(t, sum(int(x.get("unread") or 0) for x in threads), now)
    st = _load_state()
    st["notify"] = {"card_id": built["card"]["card_id"], "peer": built["peer"], "at": now}
    _save_state(st)
    out = r.push_card(DEVICE, built["card"])
    print(f"  notify -> {built['peer']['login']}, card_id={built['card']['card_id']}, "
          f"device says: {str(out.get('result'))[:120]}")
    return out


def push_thread(r: TinyRelay, peer: str) -> Dict[str, Any]:
    # This is the read-marking call. Only ever from an explicit human action.
    th = r.thread(peer)
    card = thread_card(th["peer"], th["messages"])
    st = _load_state()
    st["thread"] = {"card_id": card["card_id"], "peer": th["peer"], "at": time.time()}
    _save_state(st)
    out = r.push_card(DEVICE, card)
    print(f"  thread -> @{th['peer'].get('login')}, {len(th['messages'])} msgs, "
          f"card_id={card['card_id']}, device says: {str(out.get('result'))[:120]}")
    return out


def fingerprint(threads: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What "something changed" means: a newer message in any thread, or a
    higher unread count. Deliberately not the thread ORDER — the platform
    reorders on read, and a badge clearing is not a notification."""
    return {str(t.get("login") or t.get("userId")): [int(t.get("lastAt") or 0),
                                                    int(t.get("unread") or 0)]
            for t in threads}


def watch(r: TinyRelay, interval: float = POLL_S) -> None:
    st = _load_state()
    seen: Dict[str, Any] = st.get("seen") or {}
    primed = bool(seen)
    print(f"watching /api/messages every {interval:.0f}s "
          f"({'primed' if primed else 'first run: priming, no push'})", flush=True)
    while True:
        try:
            threads = r.messages()
            fp = fingerprint(threads)
            fresh = [t for t in threads
                     if fp.get(str(t.get("login") or t.get("userId")))
                     != seen.get(str(t.get("login") or t.get("userId")))]
            if primed and fresh:
                # newest first; one push, because the relay hands the device one
                # envelope at a time and a burst would just overwrite the panel
                newest = max(fresh, key=lambda t: int(t.get("lastAt") or 0))
                idx = threads.index(newest)
                print(f"[{time.strftime('%H:%M:%S')}] new from "
                      f"@{newest.get('login')} — pushing", flush=True)
                try:
                    push_notify(r, idx)
                except RelayError as e:
                    print(f"  ! push failed: {e}", flush=True)
            elif not primed:
                print(f"[{time.strftime('%H:%M:%S')}] primed on "
                      f"{len(threads)} threads", flush=True)
            seen = fp
            primed = True
            st = _load_state()
            st["seen"] = seen
            st["last_poll"] = time.time()
            _save_state(st)
        except RelayError as e:
            print(f"[{time.strftime('%H:%M:%S')}] ! {e}", flush=True)
        except KeyboardInterrupt:
            print("stopped")
            return
        time.sleep(max(10.0, interval))


# ── the finger comes back through the owner's event ring ──────────────────
# A tap does NOT reply on the relay: tiny_touch posts device_note "ui_tap
# card_id=… button_id=… index=… label=…", the worker prefixes the device name,
# and it surfaces on GET /api/events. That is the only channel a physical
# finger has to this process.
_TAP = re.compile(r"ui_tap\s+card_id=(\S*)\s+button_id=(\S*)\s+index=(-?\d+)\s+label=(.*)$")
_TAP_OLD = re.compile(r"ui_tap\s+button=(-?\d+)(?:\s+label=(.*))?$")


def parse_tap(detail: str) -> Optional[Dict[str, Any]]:
    m = _TAP.search(detail or "")
    if m:
        return {"card_id": m.group(1), "button_id": m.group(2),
                "index": int(m.group(3)), "label": m.group(4)}
    m = _TAP_OLD.search(detail or "")  # pre-0.9.1 firmware, kept for old feeds
    if m:
        return {"card_id": "", "button_id": "", "index": int(m.group(1)),
                "label": m.group(2) or ""}
    return None


def act_on_tap(r: TinyRelay, tap: Dict[str, Any]) -> Optional[str]:
    """Turn one tap into one action. Returns a human line, or None if the tap
    was not ours (another client's card owns the panel half the time)."""
    bid, card = tap.get("button_id") or "", tap.get("card_id") or ""
    st = _load_state()
    if bid.startswith("dm_t") and bid[4:].isdigit():
        peers = (st.get("inbox") or {}).get("peers") or []
        i = int(bid[4:])
        if (st.get("inbox") or {}).get("card_id") != card:
            return f"stale inbox tap ({card} is not the card I pushed) — ignored"
        if i >= len(peers):
            return f"row {i} has no peer in state — ignored"
        peer = peers[i]
        push_thread(r, str(peer.get("login") or peer.get("userId")))
        return f"opened thread with @{peer.get('login')}"
    if bid in ("dm_inbox", "dm_reload"):
        push_inbox(r)
        return "inbox refreshed"
    if bid == "dm_top":
        push_notify(r, 0)
        return "newest message shown"
    if bid == "dm_open":
        peer = (st.get("notify") or {}).get("peer") or {}
        if not peer.get("login"):
            return "no notified peer in state — ignored"
        push_thread(r, str(peer["login"]))
        return f"opened thread with @{peer['login']}"
    if bid in CANNED:
        # Whose conversation? The thread card that is on the glass, else the
        # peer we last notified about. If neither, we do NOT guess a recipient.
        peer = ((st.get("thread") or {}).get("peer")
                if (st.get("thread") or {}).get("card_id") == card
                else (st.get("notify") or {}).get("peer")) or {}
        login = peer.get("login")
        if not login:
            return f"canned reply {bid} with no known recipient — refused"
        out = r.send_dm(str(login), CANNED[bid])
        # Say on the glass what was actually sent, to whom, and that the agent
        # sent it. A device that silently sends messages is not trustworthy.
        r.push_card(DEVICE, {
            "type": "text", "card_id": "dm-sent",
            "title": "Sent",
            "body": f"To {_ascii(peer.get('name') or login)} (@{_ascii(str(login))}):\n"
                    f"\"{CANNED[bid]}\"  -- sent by sticky, via tiny.",
            "footer": _clip(f"delivered: {json.dumps(out.get('delivered') or {})}"[:90], 94),
            "buttons": [{"id": "dm_open", "label": "Thread"},
                        {"id": "dm_inbox", "label": "Inbox"}]})
        return f"sent \"{CANNED[bid]}\" to @{login} (id {out.get('id')})"
    return None


def serve(r: TinyRelay, interval: float = 5.0, poll_msgs_every: float = POLL_S) -> None:
    """One loop: watch for new messages AND for taps on the cards we pushed.

    The event cursor is this module's own — /api/events is a 200-entry ring
    shared with the dashboard's poller, and two consumers advancing one cursor
    would steal each other's events.
    """
    st = _load_state()
    since = int((st.get("events") or {}).get("since") or 0)
    if not since:  # start at the tail; old taps are not commands
        evs = r.events(0)
        while len(evs) == 50:
            nxt = r.events(evs[-1]["id"])
            if not nxt:
                break
            evs = nxt
        since = evs[-1]["id"] if evs else 0
    seen = (st.get("seen") or {})
    primed = bool(seen)
    last_msgs = 0.0
    print(f"serving: taps every {interval:.0f}s from event id {since}, "
          f"messages every {poll_msgs_every:.0f}s", flush=True)
    while True:
        try:
            for e in r.events(since):
                since = max(since, int(e.get("id") or 0))
                tap = parse_tap(str(e.get("detail") or "")) if e.get("kind") == "device_note" else None
                if tap:
                    line = act_on_tap(r, tap)
                    print(f"[{time.strftime('%H:%M:%S')}] tap {tap['button_id']!r} -> "
                          f"{line or 'not one of my cards'}", flush=True)
            if time.time() - last_msgs >= poll_msgs_every:
                last_msgs = time.time()
                threads = r.messages()
                fp = fingerprint(threads)
                fresh = [t for t in threads
                         if fp.get(str(t.get("login") or t.get("userId")))
                         != seen.get(str(t.get("login") or t.get("userId")))]
                if primed and fresh:
                    newest = max(fresh, key=lambda t: int(t.get("lastAt") or 0))
                    print(f"[{time.strftime('%H:%M:%S')}] new from "
                          f"@{newest.get('login')} — pushing", flush=True)
                    push_notify(r, threads.index(newest))
                seen, primed = fp, True
            st = _load_state()
            st["events"] = {"since": since}
            st["seen"] = seen
            _save_state(st)
        except RelayError as e:
            print(f"[{time.strftime('%H:%M:%S')}] ! {e}", flush=True)
        except KeyboardInterrupt:
            print("stopped")
            return
        time.sleep(max(3.0, interval))


def main(argv: List[str]) -> int:
    cmd = (argv[1] if len(argv) > 1 else "peek").lower()
    r = TinyRelay()
    if cmd == "inbox":
        push_inbox(r)
    elif cmd == "notify":
        push_notify(r, int(argv[2]) if len(argv) > 2 else 0)
    elif cmd == "thread":
        if len(argv) < 3:
            print("usage: tiny_inbox.py thread <login>", file=sys.stderr)
            return 2
        push_thread(r, argv[2])
    elif cmd == "reply":
        if len(argv) < 4:
            print("usage: tiny_inbox.py reply <login> <text…>", file=sys.stderr)
            return 2
        out = r.send_dm(argv[2], " ".join(argv[3:]))
        print(f"  sent id={out.get('id')} to={out.get('to')} "
              f"delivered={out.get('delivered')}")
    elif cmd == "watch":
        watch(r, float(argv[2]) if len(argv) > 2 else POLL_S)
    elif cmd == "serve":
        serve(r, float(argv[2]) if len(argv) > 2 else 5.0)
    elif cmd == "peek":
        threads = r.messages()
        now = time.time()
        for i, t in enumerate(threads[:MAX_ROWS]):
            row = inbox_card([t], now)["card"]["items"][0]
            print(f"  {i} @{t.get('login'):<16} {row['note']:>6}  {row['label']}")
        print(f"  state: {STATE_PATH} "
              f"{'(exists)' if STATE_PATH.exists() else '(none yet)'}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main(sys.argv))
